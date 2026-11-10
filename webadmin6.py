"""Deposit / ডিপোজিট — অ্যাডমিন ওয়েব প্যানেল।

- /admin/deposits                  : মেথড + পেন্ডিং ডিপোজিট
- /admin/deposit-method/new        : নতুন মেথড (অটো BEP-20 / ম্যানুয়াল)
- /admin/deposit-method/<id>       : মেথড এডিট / টগল / ডিলিট
- /admin/deposit/<id>/approve|reject : ✅ ব্যালেন্স যোগ / ❌ বাতিল
"""
import time

from flask import request, redirect

from webadmin import (
    wadm, page, guard, table, badge, esc, num, one, many, _x, log_admin,
    queue_user_msg,
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
            "manual": "✍️ ম্যানুয়াল"}
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
    pend = many("SELECT * FROM deposits WHERE status='pending' ORDER BY id LIMIT 100")
    agg = one("SELECT COALESCE(SUM(amount),0) AS s, COUNT(*) AS n FROM deposits "
              "WHERE status='approved'") or {}
    today = one("SELECT COALESCE(SUM(amount),0) AS s FROM deposits "
                "WHERE status='approved' AND created_at LIKE %s",
                (time.strftime("%Y-%m-%d") + "%",)) or {}
    sql = "SELECT * FROM deposits WHERE 1=1"
    args = []
    if st:
        sql += " AND status=%s"
        args.append(st)
    sql += " ORDER BY id DESC LIMIT 200"
    rows_db = many(sql, tuple(args))

    head = ("<div class='grid g-kpi'>"
            + _kpi("পেন্ডিং", num(len(pend)), "warn" if pend else "")
            + _kpi("আজকের ডিপোজিট", esc(_fmt((today or {}).get("s"))))
            + _kpi("মোট অনুমোদিত", esc(_fmt((agg or {}).get("s"))))
            + _kpi("মোট ডিপোজিট", num((agg or {}).get("n")))
            + _kpi("মেথড", num(len(methods)))
            + "</div>")

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
        prow.append([
            f"<span class='mono'>#{int(r['id'])}</span>",
            f"<a class='mono' href='/admin/user/{int(r['user_id'] or 0)}'>{int(r['user_id'] or 0)}</a>",
            esc(_fmt(r["amount"])),
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
            esc(str(r["method_name"] or "")[:26]),
            badge(lbl, kind),
            f"<span class='mono' style='font-size:11px'>"
            f"{esc(str(r['txid'] or '')[:22])}</span>",
            esc(str(r["created_at"] or "")[:16]),
        ])

    def _tab(lbl, q, active):
        return (f"<a class='btn {'ok' if active else 'gray'} sm' "
                f"href='/admin/deposits{q}'>{lbl}</a>")

    _api = settings_all(["binance_api_key", "binance_api_secret", "binance_uid_window"])
    _key_set = bool((_api.get("binance_api_key") or "").strip())
    _sec_set = bool((_api.get("binance_api_secret") or "").strip())
    _api_ready = _key_set and _sec_set
    api_card = (
        "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
        "<b>🆔 Binance UID অটো — API সেটিং</b>"
        + (badge("✅ চালু", "ok") if _api_ready else badge("❌ সেট নেই", "err"))
        + "</div>"
        "<form method='post' action='/admin/deposits/api' style='margin-top:10px'>"
        "<div class='field'><label>Binance API Key</label>"
        f"<input name='binance_api_key' value='{esc(_api.get('binance_api_key') or '')}' "
        "placeholder='Binance → API Management → Create API (Enable Reading)'></div>"
        "<div class='field'><label>Binance API Secret</label>"
        f"<input name='binance_api_secret' type='password' "
        f"value='{esc(_api.get('binance_api_secret') or '')}' placeholder='secret key'></div>"
        "<div class='field'><label>কত মিনিট আগের পেমেন্ট খুঁজবে</label>"
        f"<input name='binance_uid_window' value='{esc(_api.get('binance_uid_window') or 45)}'></div>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>API Key-তে শুধু "
        "<b>Enable Reading</b> + Binance Pay permission থাকলেই যথেষ্ট। সেট না থাকলে "
        "UID মেথড অটো যাচাই না করে পেন্ডিং হিসেবে আপনার কাছে আসবে (ম্যানুয়াল ✅)।</p>"
        "<button class='ok' type='submit' style='margin-top:10px'>💾 সেভ করুন</button>"
        "</form></div>"
    )

    body = (
        head
        + api_card
        + "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
          "<b>📋 ডিপোজিট মেথড</b>"
          "<a class='btn ok sm' href='/admin/deposit-method/new'>➕ নতুন মেথড</a></div>"
        + table(["নাম", "ধরন", "সর্বনিম্ন", "অ্যাড্রেস / নির্দেশনা", "স্ট্যাটাস", ""],
                mrows, "কোনো মেথড নেই — উপরে যোগ করুন")
        + "<div class='mut' style='font-size:12px;margin-top:8px'>"
          "⚡ অটো = ইউজার TxID দিলে বট BSC-তে USDT BEP-20 ট্রান্সফার যাচাই করে সাথে সাথে ব্যালেন্স যোগ করে। "
          "✍️ ম্যানুয়াল = আপনি এখান থেকে ✅ করবেন।</div></div>"
        + "<div class='card'><b>⏳ পেন্ডিং ডিপোজিট (" + num(len(pend)) + ")</b>"
        + table(["#", "ইউজার", "পরিমাণ", "মেথড", "নোট / TxID", "সময়", ""], prow,
                "কোনো পেন্ডিং ডিপোজিট নেই ✅")
        + "</div>"
        + "<div class='card'><div class='row' style='gap:8px;margin-bottom:8px'>"
          + _tab("সব", "", not st) + _tab("⏳ পেন্ডিং", "?st=pending", st == "pending")
          + _tab("✅ অনুমোদিত", "?st=approved", st == "approved")
          + _tab("❌ বাতিল", "?st=rejected", st == "rejected")
        + "</div>"
        + table(["#", "ইউজার", "পরিমাণ", "মেথড", "স্ট্যাটাস", "TxID", "সময়"], drows,
                "কোনো ডিপোজিট নেই")
        + "</div>"
    )
    return page("Deposits / ডিপোজিট", body, "deposits",
                toast=request.args.get("ok") or None, err=request.args.get("err") or None)


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
        f"<select name='kind'>"
        f"<option value='uid'{' selected' if kind == 'uid' else ''}>🆔 অটো — Binance Pay (পরিমাণ + অর্ডার আইডি)</option>"
        f"<option value='auto'{' selected' if kind == 'auto' else ''}>⚡ অটো — BEP-20 TxID অন-চেইনে যাচাই</option>"
        f"<option value='manual'{' selected' if kind == 'manual' else ''}>✍️ ম্যানুয়াল — আমি ✅ করব</option>"
        f"</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>🆔 UID: ইউজার কত ডলার পাঠিয়েছে + "
        "অর্ডার আইডি/TxID দিলে বট Binance Pay হিস্ট্রি থেকে পেমেন্ট খুঁজে সাথে সাথে ব্যালেন্স যোগ করে; "
        "না পেলে পেন্ডিং আসে — আপনি ✅/❌ করবেন।</p></div>"
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
    if kind not in ("auto", "uid", "manual"):
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
    n = _x("UPDATE deposits SET status='approved', reviewed_by=%s, reviewed_at=%s "
           "WHERE id=%s AND status='pending'", (0, _now(), did))
    if not n:
        return redirect("/admin/deposits?err=এটি আগেই প্রসেস হয়েছে")
    amt = float(d["amount"] or 0)
    _x("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amt, int(d["user_id"] or 0)))
    queue_user_msg(int(d["user_id"]),
                   f"✅ ডিপোজিট অনুমোদিত!\n\n💵 {_fmt(amt)} যোগ হয়েছে\n🧾 #{did}")
    log_admin("deposit_approve", did, f"uid={d['user_id']} amount={amt}")
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
    return redirect("/admin/deposits?ok=বাতিল করা হয়েছে")


# ───────────────────────── Binance API সেটিং ─────────────────────────
@wadm.route("/admin/deposits/api", methods=["POST"])
def deposits_api_save():
    g = guard()
    if g:
        return g
    _init()
    key = (request.form.get("binance_api_key") or "").strip()
    sec = (request.form.get("binance_api_secret") or "").strip()
    win = (request.form.get("binance_uid_window") or "").strip()
    if key:
        settings_set("binance_api_key", key)
    if sec:
        settings_set("binance_api_secret", sec)
    if win.isdigit() and 5 <= int(win) <= 1440:
        settings_set("binance_uid_window", win)
    log_admin("binance_api_save", 0, "key_set" if key else "key_keep")
    return redirect("/admin/deposits?ok=Binance+API+সেভ+হয়েছে")
