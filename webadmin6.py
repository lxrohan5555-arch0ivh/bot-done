"""Deposit / ডিপোজিট — অ্যাডমিন ওয়েব প্যানেল।

- /admin/deposits                  : মেথড + পেন্ডিং ডিপোজিট
- /admin/deposit-method/new        : নতুন মেথড (অটো BEP-20 / ম্যানুয়াল)
- /admin/deposit-method/<id>       : মেথড এডিট / টগল / ডিলিট
- /admin/deposit/<id>/approve|reject : ✅ ব্যালেন্স যোগ / ❌ বাতিল
"""
import base64
import time

from flask import request, redirect

from webadmin import (
    wadm, page, guard, table, badge, esc, num, one, many, _x, log_admin,
    queue_user_msg,
    tg_admin_notify,
    mask_uname,
    ch_http_send,
)
from webadmin2 import settings_all, settings_set

_DDL = (
    """CREATE TABLE IF NOT EXISTS deposit_methods (
        id           BIGSERIAL PRIMARY KEY,
        name         TEXT DEFAULT '',
        kind         TEXT DEFAULT 'manual',
        address      TEXT DEFAULT '',
        instructions TEXT DEFAULT '',
        min_amount   REAL DEFAULT 0,
        is_active    INTEGER DEFAULT 1,
        sort_order   INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS deposits (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT DEFAULT 0,
        method_id   BIGINT DEFAULT 0,
        method_name TEXT DEFAULT '',
        kind        TEXT DEFAULT 'manual',
        amount      REAL DEFAULT 0,
        status      TEXT DEFAULT 'pending',
        txid        TEXT DEFAULT '',
        note        TEXT DEFAULT '',
        created_at  TEXT DEFAULT '',
        reviewed_by BIGINT DEFAULT 0,
        reviewed_at TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS deposits_user ON deposits (user_id, id)",
    "ALTER TABLE deposit_methods ADD COLUMN IF NOT EXISTS qr_photo TEXT DEFAULT ''",
    "ALTER TABLE deposit_methods ADD COLUMN IF NOT EXISTS provider TEXT DEFAULT 'binance'",
    "ALTER TABLE deposits ADD COLUMN IF NOT EXISTS proof TEXT DEFAULT ''",
    "ALTER TABLE deposits ADD COLUMN IF NOT EXISTS kind TEXT DEFAULT 'manual'",
    "ALTER TABLE deposits ADD COLUMN IF NOT EXISTS uniq_code TEXT DEFAULT ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS deposits_uniq_code "
    "ON deposits (uniq_code) WHERE COALESCE(uniq_code,'') <> ''",
)

_inited = False


def _init():
    global _inited
    if _inited:
        return
    for q in _DDL:
        try:
            _x(q)
        except Exception:
            pass
    _inited = True


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _fmt(v):
    try:
        n = float(v or 0)
    except Exception:
        n = 0.0
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return f"{s or '0'}$"


def _kpi(label, value, tone=""):
    return (f"<div class='card kpi'><div class='t'>{esc(label)}</div>"
            f"<div class='v {tone}'>{value}</div></div>")


_KIND_BN = {"auto": "⚡ অটো (BEP-20)", "uid": "🆔 অটো (Binance UID)",
            "manual": "✍️ ম্যানুয়াল", "qr": "🇧🇩 Bangla QR পেমেন্ট"}
_ST_BN = {"pending": ("⏳ অপেক্ষমাণ", "warn"), "approved": ("✅ অনুমোদিত", "ok"),
          "rejected": ("❌ বাতিল", "err")}


# ───────────────────────── ওভারভিউ ─────────────────────────
@wadm.route("/admin/deposits", methods=["GET"])
def deposits_home():
    g = guard()
    if g:
        return g
    _init()
    st = (request.args.get("st") or "").strip()
    methods = many("SELECT * FROM deposit_methods ORDER BY sort_order, id")
    # 🔄 "হিসাব ০ থেকে শুরু" — এর আগের ডিপোজিট KPI-তে গোনা হয় না
    try:
        _since = str((settings_all(["stats_since"]) or {}).get("stats_since") or "").strip()
    except Exception:
        _since = ""
    pend = many("SELECT * FROM deposits WHERE status='pending' ORDER BY id LIMIT 100")
    _sw = (" AND created_at>=%s" if _since else "")
    _sa = ((_since,) if _since else ())
    agg = one("SELECT COALESCE(SUM(amount),0) AS s, COUNT(*) AS n FROM deposits "
              "WHERE status='approved'" + _sw, _sa) or {}
    today = one("SELECT COALESCE(SUM(amount),0) AS s FROM deposits "
                "WHERE status='approved' AND created_at LIKE %s" + _sw,
                (time.strftime("%Y-%m-%d") + "%",) + _sa) or {}
    sql = "SELECT * FROM deposits WHERE COALESCE(admin_hidden,0)=0"
    args = []
    if st:
        sql += " AND status=%s"
        args.append(st)
    sql += " ORDER BY id DESC LIMIT 200"
    try:
        rows_db = many(sql, tuple(args))
    except Exception:
        try:
            _x("ALTER TABLE deposits ADD COLUMN IF NOT EXISTS "
               "admin_hidden INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        rows_db = many(sql, tuple(args))

    head = ("<div class='grid g-kpi'>"
            + _kpi("পেন্ডিং", num(len(pend)), "warn" if pend else "")
            + _kpi("আজকের ডিপোজিট", esc(_fmt((today or {}).get("s"))))
            + _kpi("মোট অনুমোদিত", esc(_fmt((agg or {}).get("s"))))
            + _kpi("মোট ডিপোজিট", num((agg or {}).get("n")))
            + _kpi("মেথড", num(len(methods)))
            + "</div>"
            + ("<div class='card'><div class='row' style='justify-content:space-between;"
               "align-items:center;gap:8px'><div><b>🔄 হিসাব ০ থেকে শুরু</b>"
               "<div class='mut' style='font-size:12px'>উপরের হিসাব ও ইউজারের নম্বর "
               "(#1, #2 …) এখন থেকে নতুন করে গোনা হবে — পুরোনো ডেটা মুছবে না।</div></div>"
               "<form method='post' action='/admin/deposits/stats-reset' "
               "onsubmit=\"return confirm('হিসাব এখন থেকে ০ থেকে শুরু হবে — ঠিক আছে?')\">"
               f"<button class='gray sm' type='submit'>🔄 রিসেট"
               + (f" (শুরু: {esc(_since[:16])})" if _since else "")
               + "</button></form></div></div>")
            )

    mrows = []
    for m in methods:
        mrows.append([
            f"<a href='/admin/deposit-method/{int(m['id'])}'><b>{esc(m['name'] or '')}</b></a>",
            _KIND_BN.get(str(m["kind"]), str(m["kind"])),
            esc(_fmt(m["min_amount"])),
            f"<span class='mono' style='font-size:11px;word-break:break-all'>"
            f"{esc((m['address'] or m['instructions'] or '—')[:60])}</span>",
            badge("চালু", "ok") if _onoff(m["is_active"]) else badge("বন্ধ", "mut"),
            f"<a class='btn gray sm' href='/admin/deposit-method/{int(m['id'])}'>✏️</a>",
        ])

    prow = []
    for r in pend:
        _code = str(r.get("uniq_code") or "") if hasattr(r, "get") else ""
        if not _code:
            _nt = str(r["note"] or "")
            if "Note:" in _nt:
                _code = _nt.split("Note:", 1)[1].split("·")[0].strip()[:12]
        prow.append([
            f"<span class='mono'>#{int(r['id'])}</span>",
            f"<a class='mono' href='/admin/user/{int(r['user_id'] or 0)}'>{int(r['user_id'] or 0)}</a>",
            esc(_fmt(r["amount"])),
            f"<b class='mono' style='letter-spacing:1px'>{esc(_code or '—')}</b>",
            esc(str(r["method_name"] or "")[:30]),
            f"<span class='mono' style='font-size:11px;word-break:break-all'>"
            f"{esc(str(r['note'] or '')[:60])}</span>",
            esc(str(r["created_at"] or "")[:16]),
            f"<form method='post' action='/admin/deposit/{int(r['id'])}/approve' style='display:inline'>"
            f"<button class='ok sm' type='submit'>✅</button></form> "
            f"<form method='post' action='/admin/deposit/{int(r['id'])}/reject' style='display:inline'>"
            f"<button class='gray sm' type='submit'>❌</button></form>",
        ])

    drows = []
    for r in rows_db:
        stt = str(r["status"] or "")
        lbl, kind = _ST_BN.get(stt, (stt, "mut"))
        drows.append([
            f"<span class='mono'>#{int(r['id'])}</span>",
            f"<a class='mono' href='/admin/user/{int(r['user_id'] or 0)}'>{int(r['user_id'] or 0)}</a>",
            esc(_fmt(r["amount"])),
            f"<span class='mono'>{esc(str((r.get('uniq_code') if hasattr(r, 'get') else '') or '')[:12] or '—')}</span>",
            esc(str(r["method_name"] or "")[:26]),
            badge(lbl, kind),
            f"<span class='mono' style='font-size:11px'>"
            f"{esc(str(r['txid'] or '')[:22])}</span>",
            esc(str(r["created_at"] or "")[:16]),
        ])

    def _tab(lbl, q, active):
        return (f"<a class='btn {'ok' if active else 'gray'} sm' "
                f"href='/admin/deposits{q}'>{lbl}</a>")

    _q = settings_all(["qr_image", "qr_image_b64", "qr_image_mime",
                       "qr_min_bdt", "qr_fee_bdt", "qr_rate", "qr_note"])
    _qimg = (_q.get("qr_image") or "").strip()
    _qb64 = (_q.get("qr_image_b64") or "").strip()
    _qmime = (_q.get("qr_image_mime") or "image/png").strip() or "image/png"
    qr_card = (
        "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
        "<b>🇧🇩 Bangla QR পেমেন্ট — সেটিংস</b>"
        + (badge("✅ সেট আছে", "ok") if _qimg else badge("❌ QR নেই", "err"))
        + "</div>"
        "<form method='post' action='/admin/deposits/qr' enctype='multipart/form-data' "
        "style='margin-top:10px'>"
        + (f"<img src='data:{esc(_qmime)};base64,{_qb64}' alt='QR' "
           "style='max-width:180px;border-radius:10px;border:1px solid #2a3550;margin:6px 0'>"
           if _qb64 else "")
        + "<div class='field'><label>QR কোডের ছবি আপলোড করুন (PNG / JPG)</label>"
        "<input type='file' name='qr_file' accept='image/png,image/jpeg,image/webp'></div>"
        "<div class='field'><label>অথবা ছবির লিংক / Telegram file_id</label>"
        f"<input name='qr_image' value='{esc(_qimg)}' "
        "placeholder='https://… অথবা file_id (আপলোড করলে এটার দরকার নেই)'></div>"
        "<div class='grid g2'>"
        "<div class='field'><label>সর্বনিম্ন ডিপোজিট (৳)</label>"
        f"<input name='qr_min_bdt' value='{esc(_q.get('qr_min_bdt') or '100')}'></div>"
        "<div class='field'><label>ফি (৳) — ইউজার ফি সহ দেবে</label>"
        f"<input name='qr_fee_bdt' value='{esc(_q.get('qr_fee_bdt') or '0')}'></div>"
        "<div class='field'><label>রেট — ১ ডলার = কত ৳</label>"
        f"<input name='qr_rate' value='{esc(_q.get('qr_rate') or '125')}'></div>"
        "</div>"
        "<div class='field'><label>নোট (ইউজার QR পেজে দেখবে)</label>"
        f"<input name='qr_note' value='{esc(_q.get('qr_note') or '')}' "
        "placeholder='বিকাশ/নগদ/রকেট — Payment অপশন থেকে পাঠান'></div>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>ফি বাদ দিয়ে শুধু আসল টাকার "
        "ডলার যোগ হবে। উদাহরণ: ৫০০ ৳ + ১০ ৳ ফি, রেট ১২৫ → ইউজার দেবে ৫১০ ৳, পাবে ৪$। "
        "ইউজার Proof-এ নাম্বারের শেষ ৪ ডিজিট দেবে (TrxID নয়) — রিকোয়েস্ট এখানে "
        "পেন্ডিং হিসেবে আসবে, আপনি ✅ করবেন।</p>"
        "<button class='ok' type='submit' style='margin-top:10px'>💾 সেভ করুন</button>"
        "</form></div>"
    )

    body = (
        head
        + qr_card
        + "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
          "<b>📋 ডিপোজিট মেথড</b>"
          "<a class='btn ok sm' href='/admin/deposit-method/new'>➕ নতুন মেথড</a></div>"
        + table(["নাম", "ধরন", "সর্বনিম্ন", "অ্যাড্রেস / নির্দেশনা", "স্ট্যাটাস", ""],
                mrows, "কোনো মেথড নেই — উপরে যোগ করুন")
        + "<div class='mut' style='font-size:12px;margin-top:8px'>"
          "⚡ অটো = ইউজার TxID দিলে বট BSC-তে USDT BEP-20 ট্রান্সফার যাচাই করে সাথে সাথে ব্যালেন্স যোগ করে। "
          "✍️ ম্যানুয়াল = আপনি এখান থেকে ✅ করবেন।</div></div>"
        + "<div class='card'><b>⏳ পেন্ডিং ডিপোজিট (" + num(len(pend)) + ")</b>"
        + table(["#", "ইউজার", "পরিমাণ", "কোড (Note to Payee)", "মেথড", "নোট / TxID", "সময়", ""], prow,
                "কোনো পেন্ডিং ডিপোজিট নেই ✅")
        + "</div>"
        + "<div class='card'><div class='row' style='gap:8px;margin-bottom:8px'>"
          + _tab("সব", "", not st) + _tab("⏳ পেন্ডিং", "?st=pending", st == "pending")
          + _tab("✅ অনুমোদিত", "?st=approved", st == "approved")
          + _tab("❌ বাতিল", "?st=rejected", st == "rejected")
        + "</div>"
        + table(["#", "ইউজার", "পরিমাণ", "কোড", "মেথড", "স্ট্যাটাস", "TxID", "সময়"], drows,
                "কোনো ডিপোজিট নেই")
        + "</div>"
    )
    return page("Deposits / ডিপোজিট", body, "deposits",
                toast=request.args.get("ok") or None, err=request.args.get("err") or None)


@wadm.route("/admin/deposits/stats-reset", methods=["POST"])
def deposits_stats_reset():
    g = guard()
    if g:
        return g
    _init()
    try:
        # ⚠️ ডিপোজিট/অর্ডারের created_at বাংলাদেশ সময়ে সেভ হয় — সেই সময়েই রিসেট
        import datetime as _dt
        import pytz as _pytz
        settings_set("stats_since",
                     _dt.datetime.now(_pytz.timezone("Asia/Dhaka"))
                     .strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    return redirect("/admin/deposits?ok=হিসাব ০ থেকে শুরু হয়েছে")


# ───────────────────────── মেথড ফর্ম ─────────────────────────
def _form(m=None):
    m = m or {}
    kind = str(m.get("kind") or "manual")
    act = int(m.get("is_active") or (1 if not m else 0))
    return (
        "<div class='card'><form method='post'>"
        "<div class='field'><label>মেথডের নাম</label>"
        f"<input name='name' value='{esc(m.get('name') or '')}' "
        "placeholder='Binance USDT (BEP-20)' required></div>"
        "<div class='field'><label>ধরন</label>"
        + "<select name='kind'>"
        + (f"<option value='uid' selected>🆔 অটো — Binance Pay (পুরোনো মেথড)</option>"
           if kind == "uid" else "")
        + (f"<option value='auto' selected>⚡ অটো — BEP-20 (পুরোনো মেথড)</option>"
           if kind == "auto" else "")
        + f"<option value='manual'{' selected' if kind == 'manual' else ''}>✍️ ম্যানুয়াল — আমি ✅ করব</option>"
        + f"<option value='qr'{' selected' if kind == 'qr' else ''}>🇧🇩 Bangla QR পেমেন্ট (QR স্ক্যান)</option>"
        + "</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>এখন QR আর ম্যানুয়াল — "
        "এই দুইটাই সবচেয়ে নিরাপদ। Binance-ভিত্তিক পুরোনো মেথড আর নতুন করে বানানো যাবে না।"
        "</p></div>"
        "<div class='field'><label>USDT BEP-20 অ্যাড্রেস / Binance Pay ID</label>"
        f"<input name='address' value='{esc(m.get('address') or '')}' placeholder='0x… অথবা Binance UID'></div>"
        "<div class='field'><label>নির্দেশনা (ম্যানুয়াল হলে ইউজার যা দেখবে)</label>"
        f"<textarea name='instructions' rows='4' "
        f"placeholder='Binance UID: 123456789&#10;পেমেন্ট করে TxID পাঠান'>{esc(m.get('instructions') or '')}</textarea></div>"
        "<div class='field'><label>সর্বনিম্ন ডিপোজিট</label>"
        f"<input name='min_amount' value='{esc(str(m.get('min_amount')) if m.get('min_amount') not in (None, '') else '1')}' placeholder='1'></div>"
        f"<label class='chk'><input type='checkbox' name='is_active' value='1'"
        f"{' checked' if act else ''}> বটে দেখাবে (চালু)</label>"
        "<div class='row' style='gap:8px;margin-top:12px'>"
        "<button class='ok' type='submit'>💾 সেভ করুন</button>"
        "<a class='btn gray' href='/admin/deposits'>বাতিল</a></div>"
        "</form></div>"
    )


def _save(mid=0):
    name = (request.form.get("name") or "").strip()[:80]
    if not name:
        return 0
    kind = (request.form.get("kind") or "manual").strip().lower()
    if kind not in ("auto", "uid", "manual", "qr"):
        kind = "manual"
    addr = (request.form.get("address") or "").strip()[:120]
    ins = (request.form.get("instructions") or "").strip()[:1200]
    try:
        mn = float((request.form.get("min_amount") or "0").replace(",", "."))
    except Exception:
        mn = 0.0
    act = 1 if request.form.get("is_active") else 0
    if mid:
        _x("UPDATE deposit_methods SET name=%s, kind=%s, address=%s, instructions=%s, "
           "min_amount=%s, is_active=%s WHERE id=%s",
           (name, kind, addr, ins, mn, act, mid))
        return mid
    nxt = (one("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM deposit_methods") or {}).get("n") or 1
    r = one("INSERT INTO deposit_methods (name, kind, address, instructions, min_amount, "
            "is_active, sort_order, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (name, kind, addr, ins, mn, act, int(nxt), _now()))
    return int((r or {}).get("id") or 0)

def _onoff(v):
    """is_active — 0 মানে বন্ধ (`int(v or 1)` লিখলে 0-ও 1 হয়ে যেত)।"""
    if v is None or v == "":
        return 1
    try:
        return 0 if int(v) == 0 else 1
    except Exception:
        return 1



@wadm.route("/admin/deposit-method/new", methods=["GET", "POST"])
def deposit_method_new():
    g = guard()
    if g:
        return g
    _init()
    if request.method == "POST":
        mid = _save()
        if not mid:
            return page("নতুন ডিপোজিট মেথড", _form(), "deposits", err="নাম দিন")
        log_admin("deposit_method_add", mid, request.form.get("name") or "")
        return redirect("/admin/deposits?ok=মেথড যোগ হয়েছে")
    return page("নতুন ডিপোজিট মেথড", _form(), "deposits")


@wadm.route("/admin/deposit-method/<int:mid>", methods=["GET", "POST"])
def deposit_method_edit(mid):
    g = guard()
    if g:
        return g
    _init()
    m = one("SELECT * FROM deposit_methods WHERE id=%s", (mid,))
    if not m:
        return page("পাওয়া যায়নি", "<div class='card'>মেথড নেই</div>", "deposits", status=404)
    if request.method == "POST":
        _save(mid)
        log_admin("deposit_method_edit", mid, request.form.get("name") or "")
        return redirect(f"/admin/deposit-method/{mid}?ok=সেভ হয়েছে")
    used = one("SELECT COUNT(*) AS n FROM deposits WHERE method_id=%s", (mid,)) or {}
    return page(
        "ডিপোজিট মেথড",
        "<div class='grid g-kpi'>"
        + _kpi("এই মেথডে ডিপোজিট", num((used or {}).get("n")))
        + _kpi("ধরন", _KIND_BN.get(str(m["kind"]), ""))
        + _kpi("সর্বনিম্ন", esc(_fmt(m["min_amount"])))
        + "</div>"
        + _form(m)
        + "<div class='card'><b>⚙️ অ্যাকশন</b><div class='row' style='gap:8px;margin-top:8px'>"
          f"<form method='post' action='/admin/deposit-method/{mid}/toggle'>"
          f"<button class='btn gray sm'>🔁 চালু/বন্ধ</button></form>"
          f"<form method='post' action='/admin/deposit-method/{mid}/delete' "
          f"onsubmit=\"return confirm('মেথড ডিলিট হবে?')\">"
          f"<button class='gray sm' type='submit'>🗑 ডিলিট</button></form>"
        "</div></div>",
        "deposits", toast=request.args.get("ok") or None)


@wadm.route("/admin/deposit-method/<int:mid>/toggle", methods=["POST"])
def deposit_method_toggle(mid):
    g = guard()
    if g:
        return g
    _init()
    r = one("SELECT is_active FROM deposit_methods WHERE id=%s", (mid,))
    if r:
        _x("UPDATE deposit_methods SET is_active=%s WHERE id=%s",
           (0 if int(r["is_active"] or 0) else 1, mid))
    return redirect(f"/admin/deposit-method/{mid}?ok=স্ট্যাটাস বদলেছে")


@wadm.route("/admin/deposit-method/<int:mid>/delete", methods=["POST"])
def deposit_method_del(mid):
    g = guard()
    if g:
        return g
    _init()
    _x("DELETE FROM deposit_methods WHERE id=%s", (mid,))
    log_admin("deposit_method_del", mid, "deposit method removed")
    return redirect("/admin/deposits?ok=মেথড ডিলিট হয়েছে")


# ───────────────────────── অনুমোদন / বাতিল ─────────────────────────
@wadm.route("/admin/deposit/<int:did>/approve", methods=["POST"])
def deposit_approve(did):
    g = guard()
    if g:
        return g
    _init()
    d = one("SELECT * FROM deposits WHERE id=%s", (did,))
    if not d:
        return redirect("/admin/deposits?err=ডিপোজিট নেই")
    try:
        import bot as _bchk
        if _bchk.dep_tx_used(str(d["txid"] or ""), did):
            return redirect("/admin/deposits?err="
                            "এই TxID আগে ব্যবহার হয়ে গেছে — একই TxID দিয়ে দ্বিতীয়বার হবে না")
    except Exception:
        pass
    # ⚠️ `credited` ফ্ল্যাগ আগে সেট হতো না → বটের অটো-ক্রেডিট/রিট্রাই পরে
    #    একই ডিপোজিটে আবার টাকা যোগ করতে পারত (ডাবল ক্রেডিট)।
    try:
        import bot as _bclaim
        if not _bclaim.dep_credit_claim(did):
            return redirect("/admin/deposits?err=এই ডিপোজিটের টাকা আগেই যোগ হয়েছে")
    except Exception:
        pass
    n = _x("UPDATE deposits SET status='approved', credited=1, reviewed_by=%s, "
           "reviewed_at=%s WHERE id=%s AND status='pending'", (0, _now(), did))
    if not n:
        try:
            import bot as _buc
            _buc.dep_credit_unclaim(did)
        except Exception:
            pass
        return redirect("/admin/deposits?err=এটি আগেই প্রসেস হয়েছে")
    amt = float(d["amount"] or 0)
    # 💳 ডিপোজিটের টাকা ডিপোজিট ওয়ালেটে (৳) — earn ব্যালেন্স আলাদা থাকে
    _x("UPDATE users SET dep_balance=COALESCE(dep_balance,0)+%s, "
       "dep_total=COALESCE(dep_total,0)+%s WHERE user_id=%s",
       (amt, amt, int(d["user_id"] or 0)))
    try:
        import bot as _bcache
        _bcache._invalidate_user_cache(int(d["user_id"] or 0))   # বটের ক্যাশে পুরোনো ব্যালেন্স না থাকে
    except Exception:
        pass
    try:
        import bot as _bdep
        _bdep.dep_tx_mark(str(d["txid"] or ""), int(d["user_id"] or 0), amt)
    except Exception:
        pass
    # চ্যানেলে একটাই নোটিশ (বটের অটো-ক্রেডিটও একই ফাংশন ব্যবহার করে →
    # ডিপোজিট আইডি দিয়ে ডুপ্লিকেট আটকে যায়, Deposit ID উপরে + বাটন থাকে)
    try:
        import bot as _bch2
        _bch2.dep_ch_notify(int(did), int(d["user_id"] or 0), amt,
                            str(d["method_name"] or ""), str(d["txid"] or ""))
    except Exception:
        pass
    queue_user_msg(int(d["user_id"]),
                   f"✅ ডিপোজিট অনুমোদিত!\n\n💵 {_fmt(amt)} যোগ হয়েছে\n🧾 #{did}")
    log_admin("deposit_approve", did, f"uid={d['user_id']} amount={amt}")
    try:
        tg_admin_notify(f"💳 <b>Deposit approved (web)</b>\n\n🧾 #{did}"
                        f"\n👤 <code>{int(d['user_id'] or 0)}</code>\n💵 {_fmt(amt)}")
    except Exception:
        pass
    return redirect("/admin/deposits?ok=ব্যালেন্স যোগ হয়েছে")


@wadm.route("/admin/deposit/<int:did>/reject", methods=["POST"])
def deposit_reject(did):
    g = guard()
    if g:
        return g
    _init()
    d = one("SELECT * FROM deposits WHERE id=%s", (did,))
    if not d:
        return redirect("/admin/deposits?err=ডিপোজিট নেই")
    n = _x("UPDATE deposits SET status='rejected', reviewed_by=%s, reviewed_at=%s "
           "WHERE id=%s AND status='pending'", (0, _now(), did))
    if not n:
        return redirect("/admin/deposits?err=এটি আগেই প্রসেস হয়েছে")
    queue_user_msg(int(d["user_id"]),
                   f"❌ ডিপোজিট বাতিল হয়েছে\n\n💵 {_fmt(d['amount'])}\n🧾 #{did}\n\n"
                   "সমস্যা হলে সাপোর্টে মেসেজ দিন।")
    log_admin("deposit_reject", did, f"uid={d['user_id']}")
    try:
        tg_admin_notify(f"❌ <b>Deposit rejected (web)</b>\n\n🧾 #{did}"
                        f"\n👤 <code>{int(d['user_id'] or 0)}</code>")
    except Exception:
        pass
    return redirect("/admin/deposits?ok=বাতিল করা হয়েছে")


# ───────────────────────── QR সেটিংস ─────────────────────────
@wadm.route("/admin/deposits/qr", methods=["POST"])
def deposits_qr_save():
    g = guard()
    if g:
        return g
    _init()
    img = (request.form.get("qr_image") or "").strip()[:400]
    note = (request.form.get("qr_note") or "").strip()[:300]
    # ── QR ছবি আপলোড → base64 করে DB-তে (রিস্টার্টেও থাকে) ──
    _up = request.files.get("qr_file")
    if _up and getattr(_up, "filename", ""):
        try:
            _raw = _up.read()
            if _raw and len(_raw) <= 2 * 1024 * 1024:
                _magic = _raw[:12]
                if _magic.startswith(b"\x89PNG"):
                    _mime = "image/png"
                elif _magic.startswith(b"\xff\xd8"):
                    _mime = "image/jpeg"
                elif _magic[:4] == b"RIFF" and _magic[8:12] == b"WEBP":
                    _mime = "image/webp"
                else:
                    _mime = ""
                if _mime:
                    settings_set("qr_image_b64", base64.b64encode(_raw).decode("ascii"))
                    settings_set("qr_image_mime", _mime)
                    settings_set("qr_image", "")     # আপলোড থাকলে লিংক আর দরকার নেই
                else:
                    return redirect("/admin/deposits?err=QR+ছবি+PNG/JPG+হতে+হবে")
            elif _raw:
                return redirect("/admin/deposits?err=QR+ছবি+২MB+এর+কম+হতে+হবে")
        except Exception:
            return redirect("/admin/deposits?err=QR+ছবি+সেভ+হয়নি")
    elif img:
        settings_set("qr_image_b64", "")
        settings_set("qr_image_mime", "")
    def _num(k, dflt):
        try:
            return f"{float((request.form.get(k) or '').replace(',', '.').strip()):g}"
        except Exception:
            return dflt
    settings_set("qr_image", img)
    settings_set("qr_note", note)
    settings_set("qr_min_bdt", _num("qr_min_bdt", "100"))
    settings_set("qr_fee_bdt", _num("qr_fee_bdt", "0"))
    _rate = _num("qr_rate", "125")
    try:
        if float(_rate) <= 0:
            _rate = "125"
    except Exception:
        _rate = "125"
    settings_set("qr_rate", _rate)
    try:
        import bot as _bq
        _bq.dep_qr_method()
        _bq.qr_activate()
    except Exception:
        pass
    log_admin("deposits_qr_save", 0, "qr settings")
    return redirect("/admin/deposits?ok=QR+সেটিংস+সেভ+হয়েছে")
