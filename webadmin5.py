"""Shop / দোকান — অ্যাডমিন ওয়েব প্যানেল।

- /admin/shop                : ক্যাটাগরি + পণ্য + স্টক ওভারভিউ
- /admin/shop/new            : নতুন পণ্য
- /admin/shop/<id>           : পণ্য এডিট / ডিলিট
- /admin/shop/<id>/stock     : .txt আপলোড / লাইন যোগ / স্টক তালিকা
- /admin/shop/orders         : অর্ডার তালিকা (ফিল্টার)
- /admin/shop/order/<id>     : ম্যানুয়াল ডেলিভারি / রিফান্ড
"""
import time

from urllib.parse import quote as _q
from flask import request, redirect

from webadmin import (
    wadm, page, guard, table, badge, esc, num, one, many, _x, log_admin,
    icon, queue_user_msg,
)

_DDL = (
    """CREATE TABLE IF NOT EXISTS shop_categories (
        id         BIGSERIAL PRIMARY KEY,
        name       TEXT DEFAULT '',
        emoji      TEXT DEFAULT '🛍',
        sort_order INTEGER DEFAULT 0,
        is_active  INTEGER DEFAULT 1,
        created_at TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS shop_products (
        id           BIGSERIAL PRIMARY KEY,
        category_id  BIGINT DEFAULT 0,
        name         TEXT DEFAULT '',
        description  TEXT DEFAULT '',
        price        REAL DEFAULT 0,
        deliver_type TEXT DEFAULT 'auto',
        is_active    INTEGER DEFAULT 1,
        sort_order   INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS shop_stock (
        id         BIGSERIAL PRIMARY KEY,
        product_id BIGINT DEFAULT 0,
        content    TEXT DEFAULT '',
        added_at   TEXT DEFAULT '',
        sold       INTEGER DEFAULT 0,
        sold_to    BIGINT DEFAULT 0,
        sold_at    TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS shop_orders (
        id           BIGSERIAL PRIMARY KEY,
        user_id      BIGINT DEFAULT 0,
        product_id   BIGINT DEFAULT 0,
        product_name TEXT DEFAULT '',
        price        REAL DEFAULT 0,
        status       TEXT DEFAULT 'delivered',
        content      TEXT DEFAULT '',
        created_at   TEXT DEFAULT '',
        delivered_at TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS shop_stock_unsold ON shop_stock (product_id, sold)",
    """CREATE TABLE IF NOT EXISTS shop_plans (
        id          BIGSERIAL PRIMARY KEY,
        category_id BIGINT NOT NULL,
        label       TEXT DEFAULT '',
        kind        TEXT DEFAULT 'days',
        value       REAL DEFAULT 0,
        sort_order  INTEGER DEFAULT 0,
        is_active   INTEGER DEFAULT 1,
        created_at  TEXT DEFAULT ''
    )""",
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
    for m in (   # পুরোনো টেবিলে কলাম না থাকলে যোগ করে
        "ALTER TABLE shop_stock ADD COLUMN IF NOT EXISTS added_at TEXT DEFAULT ''",
        "ALTER TABLE shop_stock ADD COLUMN IF NOT EXISTS sold_to BIGINT DEFAULT 0",
        "ALTER TABLE shop_stock ADD COLUMN IF NOT EXISTS sold_at TEXT DEFAULT ''",
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS deliver_type TEXT DEFAULT 'auto'",
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS ref_pct REAL DEFAULT -1",
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS fields TEXT DEFAULT ''",
        "ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS delivered_at TEXT DEFAULT ''",
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS plan_id BIGINT DEFAULT 0",
        # 💰 কেনা দাম — লাভের হিসাব (বিক্রি − কেনা)
        "ALTER TABLE shop_products ADD COLUMN IF NOT EXISTS cost REAL DEFAULT 0",
        "ALTER TABLE shop_categories ADD COLUMN IF NOT EXISTS plan_kind TEXT DEFAULT ''",
    ):
        try:
            _x(m)
        except Exception:
            pass
    try:
        _x("UPDATE shop_categories SET plan_kind='days' "
           "WHERE COALESCE(plan_kind,'')='' AND id IN "
           "(SELECT DISTINCT category_id FROM shop_plans)")
    except Exception:
        pass
    _inited = True


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _cur():
    r = one("SELECT value FROM settings WHERE key=%s", ("shop_currency",))
    v = ((r or {}).get("value") or "").strip()
    return v or "$"


def _shop_rate():
    """১$ = কত ৳ — বটের `dep_rate()` (QR রেট), ক্যাশে করা।"""
    try:
        import bot as _b
        return float(_b.dep_rate() or 125) or 125.0
    except Exception:
        return 125.0


def _usd_str(n, r=None):
    """৳ → $ — বট যেভাবে দেখায় ঠিক সেভাবেই (0.01$, 0.658$)।"""
    try:
        import bot as _b
        return _b._usd_exact(n, r or _shop_rate())
    except Exception:
        _r = float(r or 125.0) or 125.0
        return (f"{float(n or 0) / _r:.6f}".rstrip("0").rstrip(".") or "0")


def _taka_str(n):
    """৳ — ছোট অঙ্কও পুরো (0.0125 → `0.0125`)।"""
    try:
        _n = float(n or 0)
    except Exception:
        _n = 0.0
    if abs(_n) >= 1 or _n == 0:
        _s = f"{_n:,.2f}".rstrip("0").rstrip(".")
    elif abs(_n) >= 0.01:
        _s = f"{_n:.4f}".rstrip("0").rstrip(".")
    else:
        _s = f"{_n:.6f}".rstrip("0").rstrip(".")
    return _s or "0"


def _fmt(v):
    """দাম/টাকা — আগে ডলার, পাশে টাকা: `0.01$ · 1.25 ৳`।"""
    return f"{_usd_str(v)}$ · {_taka_str(v)} ৳"


def _stock_n(pid):
    r = one("SELECT COUNT(*) AS n FROM shop_stock WHERE product_id=%s AND COALESCE(sold,0)=0", (pid,))
    try:
        return int((r or {}).get("n") or 0)
    except Exception:
        return 0


def _stock_key(s):
    import re as _re
    return _re.sub(r"\s+", " ", str(s or "").strip()).lower().strip('"\'')


def _add_stock(pid, text):
    """স্টক যোগ — ডুপ্লিকেট লাইন বাদ। রিটার্ন ``(added, dup)``।"""
    lines = [ln.strip() for ln in str(text or "").replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln][:3000]
    if not lines:
        return 0, 0
    added = dup = 0
    seen = set()
    for r in (many("SELECT content FROM shop_stock WHERE product_id=%s", (int(pid),)) or []):
        seen.add(_stock_key(r["content"]))
    for ln in lines:
        k = _stock_key(ln)
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        _x("INSERT INTO shop_stock (product_id, content, added_at, sold) VALUES (%s,%s,%s,0)",
           (int(pid), ln[:3900], _now()))
        added += 1
    return added, dup


def _import_sheet(pid, url):
    """Google Sheet / CSV / যেকোনো লিংক থেকে স্টক। রিটার্ন ``(added, dup)``।"""
    import bot as _b          # একই প্রসেসে লোড থাকে — একই নিয়ম, একই ডুপ্লিকেট চেক
    return _b.shop_import_sheet(int(pid), url)


def _cats():
    return many("SELECT * FROM shop_categories ORDER BY sort_order, id")


def _cat_options(selected=0):
    out = ["<option value='0'>— ক্যাটাগরি ছাড়া —</option>"]
    for c in _cats():
        sel = " selected" if int(c["id"]) == int(selected or 0) else ""
        out.append(f"<option value='{int(c['id'])}'{sel}>{esc(c['emoji'] or '🛍')} "
                   f"{esc(c['name'] or '')}</option>")
    return "".join(out)


def _kpi(label, value, tone=""):
    return (f"<div class='card kpi'><div class='t'>{esc(label)}</div>"
            f"<div class='v {tone}'>{value}</div></div>")


def _btn(text, url, cls="btn"):
    return f"<a class='{cls}' href='{url}'>{text}</a>"


# ───────────────────────── ওভারভিউ ─────────────────────────
@wadm.route("/admin/shop/onoff", methods=["POST"])
def shop_onoff():
    """দোকান চালু/বন্ধ সুইচ।"""
    g = guard()
    if g:
        return g
    _init()
    val = "off" if (request.form.get("val") or "").strip().lower() == "off" else "on"
    try:
        import bot as _bs
        _bs.set_setting("shop_on", val)
    except Exception as _e:
        _x("INSERT INTO settings (key,value) VALUES ('shop_on',%s) "
           "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (val,))
    log_admin("shop_onoff", 0, val)
    return redirect("/admin/shop?ok=" + __import__("urllib.parse", fromlist=["quote"]).quote(
        "🛍 দোকান " + ("চালু করা হয়েছে" if val == "on" else "বন্ধ করা হয়েছে")))


@wadm.route("/admin/shop/mailcode", methods=["POST"])
def shop_mailcode_save():
    """🎫 মেইল কোড — লিংক, সার্ভিস, প্রম্পট।"""
    g = guard()
    if g:
        return g
    _init()
    form = request.form
    # ── শুধু নাম + লিংক ── (প্রতি লাইনে: নাম | https://…)
    import json as _json
    _rows = []
    _raw = (form.get("mailcode_links_text") or "").replace("\r", "")
    _lines = [_l.strip() for _l in _raw.split("\n") if _l.strip()]
    # "প্রতি লাইনে নাম | লিংক" — আবার কেউ নাম এক লাইনে, লিংক পরের লাইনে
    # লিখলে সেটাও ধরা হয় (নাহলে বাটন কম দেখাত)।
    _merged = []
    _i = 0
    while _i < len(_lines):
        _l = _lines[_i]
        if "|" in _l:
            _nm0, _u0 = _l.split("|", 1)
            if not _u0.strip().lower().startswith(("http://", "https://")):
                _nxt = _lines[_i + 1] if _i + 1 < len(_lines) else ""
                if _nxt.lower().startswith(("http://", "https://")):
                    _merged.append(_nm0.strip() + " | " + _nxt)
                    _i += 2
                    continue
        _merged.append(_l)
        _i += 1
    for _line in _merged:
        if "|" in _line:
            _nm, _u = _line.split("|", 1)
        else:
            _nm, _u = "", _line
        _nm = _nm.strip()[:40]
        _u = _u.strip()[:400]
        if not _u.lower().startswith(("http://", "https://")):
            continue
        _rows.append({"name": _nm or _u.split("//")[-1].split("/")[0][:40], "url": _u})
        if len(_rows) >= 20:
            break
    vals = {
        "mailcode_on": "off" if (form.get("mailcode_on") or "").strip().lower() == "off" else "on",
        "mailcode_links": _json.dumps(_rows, ensure_ascii=False)[:4000],
        "mailcode_link": (_rows[0]["url"] if _rows else ""),
    }
    # 🤖 বট সিস্টেম API — কার্ড থেকে বাদ দেওয়া হয়েছে;
    # পুরোনো api/key সেটিংসে অপরিবর্তিত থাকে (মুছে যায় না)।
    try:
        import bot as _bs
        for k, v in vals.items():
            _bs.set_setting(k, v)
    except Exception:
        for k, v in vals.items():
            _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, v))
    log_admin("shop_mailcode", 0, "saved")
    return redirect("/admin/shop?ok=" + __import__("urllib.parse", fromlist=["quote"]).quote(
        "🎫 মেইল কোড সেটিংস সেভ হয়েছে"))


def _ml_map_norm(txt):
    """আইটেম-ভিত্তিক লাভের লাইন — service | domain | লাভ (আবর্জনা বাদ)।"""
    _out = []
    for _ln in str(txt or "").replace("\r", "").split("\n")[:300]:
        _p = [x.strip() for x in
              _ln.replace("—", "|").replace("\t", "|").split("|")]
        _p = [x for x in _p if x]
        if len(_p) < 2:
            continue
        _out.append(" | ".join(x[:60] for x in _p[:3]))
        if len(_out) >= 200:
            break
    return "\n".join(_out)[:6000]


@wadm.route("/admin/shop/getnum", methods=["POST"])
def shop_getnum_save():
    """📱 নাম্বার নিন (Zenex) — API key, সার্ভিস/দেশ/দাম, OTP সেটিং।"""
    g = guard()
    if g:
        return g
    _init()
    import json as _json
    form = request.form
    groups = []
    _idx = {}
    for _line in (form.get("getnum_services_text") or "").replace("\r", "").split("\n"):
        _line = _line.strip()
        if not _line:
            continue
        _p = [_x.strip() for _x in _line.split("|")]
        if len(_p) >= 4:
            _sv, _cty, _rng, _pr = _p[0], _p[1], _p[2], _p[3]
        elif len(_p) == 3:
            _sv, _cty, _rng, _pr = _p[0], _p[0], _p[1], _p[2]
        elif len(_p) == 2:
            _sv, _cty, _rng, _pr = _p[0], _p[0], _p[1], "0"
        else:
            _sv, _cty, _rng, _pr = _p[0], _p[0], _p[0], "0"
        if not _rng:
            continue
        try:
            _price = max(0.0, round(float(_pr.replace("৳", "").replace(",", ".") or 0), 4))
        except Exception:
            _price = 0.0
        _sv = _sv[:40]
        if _sv not in _idx:
            _idx[_sv] = len(groups)
            groups.append({"name": _sv, "items": []})
        _g = groups[_idx[_sv]]
        # রেঞ্জে অন্তত ৩টি ডিজিট থাকতে হবে — ভুল লাইন (যেমন "খারাপ লাইন") বাদ
        if sum(1 for _ch in _rng if _ch.isdigit()) < 3:
            continue
        # একই সার্ভিসে একই রেঞ্জ দুইবার নয় (আগেরটাই থাকবে, দাম বদলাবে না)
        if any(str(_x.get("range") or "") == _rng[:40] for _x in _g["items"]):
            continue
        if len(_g["items"]) < 60:
            _g["items"].append({"name": (_cty or _rng)[:40], "range": _rng[:40],
                                "price": _price})
        if len(groups) >= 12:
            break
    vals = {
        "getnum_on": "off" if (form.get("getnum_on") or "").strip().lower() == "off" else "on",
        "getnum_api": (form.get("getnum_api") or "").strip()[:300],
        "getnum_sms_api": (form.get("getnum_sms_api") or "").strip()[:400],
        "getnum_header": (form.get("getnum_header") or "").strip()[:40],
        "getnum_ch_id": (form.get("getnum_ch_id") or "").strip()[:24],
        "getnum_rate": (form.get("getnum_rate") or "0.25").replace(",", ".").strip()[:12],
        "getnum_group_url": (form.get("getnum_group_url") or "").strip()[:300],
        "getnum_list_api": (form.get("getnum_list_api") or "").strip()[:400],
        "getnum_feed_api": (form.get("getnum_feed_api") or "").strip()[:400],
    }
    # ✉️ মেইল কিনুন (SmsBower) — পুরোনো/ক্যাশ করা পেজ থেকে সাবমিট হলে ফিল্ডগুলো
    #    থাকে না; তখন আগের মেইল সেটিং অপরিবর্তিত থাকবে (হঠাৎ বন্ধ/৩০% হবে না)।
    if "mail_on" in form:
        vals["mail_on"] = ("off" if (form.get("mail_on") or "off").strip().lower() == "off"
                           else "on")
    if "mail_api" in form:
        vals["mail_api"] = (form.get("mail_api") or "").strip()[:300]
    if "mail_markup" in form:
        vals["mail_markup"] = ((form.get("mail_markup") or "30")
                               .replace(",", ".").strip()[:8])
    # পুরোনো/ক্যাশ-করা পেজ থেকে সাবমিট হলে ফিল্ডটা নেই — তখন আগের লিস্ট মুছে যাবে না
    if "mail_markup_map" in form:
        vals["mail_markup_map"] = _ml_map_norm(form.get("mail_markup_map") or "")
    # 🔢 সার্ভিস-ভিত্তিক কোড-সংখ্যা (ইউজার কেনার আগে দেখে)
    if "mail_limits" in form:
        _liml = []
        for _ln in (form.get("mail_limits") or "").replace("\r", "").split("\n")[:200]:
            _ln = _ln.strip()
            if not _ln or _ln.startswith("#"):
                continue
            _p = [x.strip() for x in _ln.replace("—", "|").split("|") if x.strip()]
            if len(_p) < 2:
                continue
            try:
                _n = max(0, min(50, int(float(_p[-1].replace(",", ".")))))
            except Exception:
                continue
            _liml.append(f"{_p[0][:40]} | {_n}")
        vals["mail_limits"] = "\n".join(_liml)[:6000]
    # হাতে লেখার বক্স আর নেই — ফিল্ড না এলে আগের লিস্টটাই অক্ষত থাকে
    if form.get("getnum_services_text") is not None:
        try:                       # বটের হেল্পার — JSON মাঝখানে কাটা পড়ে না
            import bot as _bsd
            vals["getnum_services"] = _bsd._gn_dump_services(groups)
        except Exception:
            vals["getnum_services"] = _json.dumps(groups, ensure_ascii=False)
    # কী-তে লুকানো স্পেস/নিউলাইন/ZWSP থাকলে API গ্রহণ করে না — পরিষ্কার করি
    _k = "".join((form.get("getnum_key") or "").split())[:300]
    # বক্সে আগের কী-এর মাস্ক করা লেখা (ZNX_G7N…TJWO) থাকলে সেটা সেভ হবে না
    if _k and "…" not in _k and "*" not in _k:
        vals["getnum_key"] = _k
    _mk = "".join((form.get("mail_key") or "").split())[:300]
    if _mk and "…" not in _mk and "*" not in _mk:
        vals["mail_key"] = _mk
    try:
        import bot as _bs
        for k, v in vals.items():
            _bs.set_setting(k, v)
    except Exception:
        for k, v in vals.items():
            _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, v))
    # 🔄 সেভের সাথে সাথেই বটের ক্যাশ ফাঁকা — নতুন রেঞ্জ/দাম ও রেট সাথে সাথে দেখা যাবে
    try:
        import bot as _bs
        _bs._GN_SVC_CACHE["ts"] = 0.0
        _bs._GN_SVC_CACHE["val"] = None
        _bs._MAIL_MK_CACHE["ts"] = 0.0
        _bs._MAIL_MK_CACHE["val"] = None
        _bs._MAIL_CACHE["ts"] = 0.0
        _bs._MAIL_LIM_CACHE["ts"] = 0.0
        _bs._MAIL_LIM_CACHE["val"] = None
    except Exception:
        pass
    log_admin("shop_getnum", 0, str(len(groups)))
    return redirect("/admin/shop?ok=" + _q("📱 নাম্বার সার্ভিস সেভ হয়েছে"))


@wadm.route("/admin/shop/getnum/test", methods=["GET"])
def shop_getnum_test():
    """সেভ করা প্রথম রেঞ্জ দিয়ে একবার নাম্বার আনি — ঠিক আছে কি না দেখা।"""
    g = guard()
    if g:
        return g
    _init()
    try:
        import bot as _bs
        _all = _bs.getnum_all_items()
        if not _all:
            return redirect("/admin/shop?ok=" + _q("আগে একটা সার্ভিস/দেশ সেভ করুন"))
        if not _bs.getnum_key():
            return redirect("/admin/shop?ok=" + _q("আগে API Key সেভ করুন"))
        _ok, _val, _raw = _bs.getnum_fetch(_all[0][3]["range"])
    except Exception as _e:
        return redirect("/admin/shop?ok=" + _q("❌ টেস্ট করা যায়নি: " + str(_e)[:150]))
    _msg = ("✅ নাম্বার পাওয়া গেছে: " + str(_val)) if _ok else ("❌ " + str(_val)[:200])
    return redirect("/admin/shop?ok=" + _q(_msg[:250]))


@wadm.route("/admin/shop/closedmsg", methods=["POST"])
def shop_closed_msg_save():
    """দোকান/ডিপোজিট বন্ধের সময় ইউজারকে যা দেখাবে — নিজের লেখা।"""
    g = guard()
    if g:
        return g
    _init()
    keys = ("shop_closed_bn", "shop_closed_en", "dep_closed_bn", "dep_closed_en",
            "task_closed_bn", "task_closed_en")
    _reset = (request.form.get("reset") or "") == "1"
    try:
        import bot as _bs
        for k in keys:
            _bs.set_setting(k, "" if _reset else (request.form.get(k) or "").strip()[:600])
    except Exception:
        for k in keys:
            _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
               (k, (request.form.get(k) or "").strip()[:600]))
    log_admin("shop_closed_msg", 0, "saved")
    return redirect("/admin/shop?ok=" + __import__("urllib.parse", fromlist=["quote"]).quote(
        "✍️ বন্ধের বার্তা সেভ হয়েছে"))


@wadm.route("/admin/section/onoff", methods=["POST"])
def section_onoff():
    """🛍 শপ / 💳 ডিপোজিট / 📋 টাস্ক — আলাদা আলাদা চালু/বন্ধ সুইচ।"""
    g = guard()
    if g:
        return g
    _init()
    _k = (request.form.get("key") or "").strip()
    _key = {"shop": "shop_on", "dep": "dep_on", "task": "task_on"}.get(_k)
    if not _key:
        return redirect("/admin/shop")
    val = "off" if (request.form.get("val") or "").strip().lower() == "off" else "on"
    try:
        import bot as _bs
        _bs.set_setting(_key, val)
    except Exception:
        _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
           "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (_key, val))
    log_admin("section_" + _k, 0, val)
    return redirect("/admin/shop?ok=" + __import__("urllib.parse", fromlist=["quote"]).quote(
        "🔒 সেকশন সেটিং সেভ হয়েছে"))


@wadm.route("/admin/shop/deponoff", methods=["POST"])
def shop_dep_lock():
    """দোকান বন্ধ থাকলে ডিপোজিটও বন্ধ থাকবে কি না — সুইচ।"""
    g = guard()
    if g:
        return g
    _init()
    val = "off" if (request.form.get("val") or "").strip().lower() == "off" else "on"
    try:
        import bot as _bs
        _bs.set_setting("shop_off_dep", val)
    except Exception as _e:
        _x("INSERT INTO settings (key,value) VALUES ('shop_off_dep',%s) "
           "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (val,))
    log_admin("shop_dep_lock", 0, val)
    return redirect("/admin/shop?ok=" + __import__("urllib.parse", fromlist=["quote"]).quote(
        "💳 ডিপোজিট নিয়ম সেভ হয়েছে"))


def _gn_page_html():
    """📱 নাম্বার নিন (OTP) পেজের ভিতর — API key, সার্ভিস, চ্যানেল, লাইভ ডেটা."""
    # ── 📱 নাম্বার নিন (Zenex Network) ──
    _gn_feed_on = False
    # ✉️ মেইল (SmsBower mails API) — ডিফল্ট
    _ml_on, _ml_api, _ml_markup, _ml_raw = False, "https://smsbower.online", 30.0, ""
    _ml_map_raw = ""
    _ml_lim_raw = ""
    _ml_live = ""
    try:
        import bot as _gnt
        _gn_on = bool(_gnt.getnum_on())
        _gn_svcs = _gnt.getnum_services()
        _gn_key_ok = bool(_gnt.getnum_key())
        _gn_api = _gnt.getnum_api()
        _gn_hdr = _gnt.getnum_header()
        _gn_sms = _gnt.getnum_sms_api()
        _gn_ch = _gnt.getnum_ch_id()
        _gn_rate = _gnt.getnum_rate()
        _gn_group = _gnt.getnum_group_url()
        _gn_list = _gnt.getnum_list_api()
        _gn_feedapi = _gnt.getnum_feed_api()
        _gn_feed_on = bool(getattr(_gnt, "GN_FEED_ON", False))
        _ml_on = bool(getattr(_gnt, "mail_on", lambda: False)())
        _ml_api = str(getattr(_gnt, "mail_base", lambda: "")() or "")
        _ml_markup = float(getattr(_gnt, "mail_markup", lambda: 30)() or 30)
        _ml_raw = str(getattr(_gnt, "mail_key", lambda: "")() or "")
        _ml_map_raw = str(_gnt.get_setting("mail_markup_map") or "")
        _ml_lim_raw = str(_gnt.get_setting("mail_limits") or "")
        # 🔗 ওয়েবে/প্রোভাইডারে এখন যে সার্ভিসগুলো আছে (ক্যাশে থাকলে) + বটের লিমিট
        try:
            _cache = getattr(_gnt, "_MAIL_CACHE", {}) or {}
            _seen = {}
            for _r in (_cache.get("rows") or []):
                _sv = str(_r.get("service") or "").lower()
                if _sv and _sv not in _seen:
                    _seen[_sv] = _gnt.mail_limit(_sv)
            if _seen:
                _parts = []
                for _k, _v in sorted(_seen.items()):
                    if _gnt.mail_service_known(_k):
                        _parts.append(f"<span class='mono'>{esc(_k)}</span>={int(_v)}")
                    else:
                        _parts.append(f"<span class='mono'>{esc(_k)}</span>="
                                      "<b style='color:#c00'>🆕 নতুন (বন্ধ)</b>")
                _ml_live = ("<div class='mut' style='font-size:12px;margin:6px 0 2px'>"
                            "<b>ওয়েবে এখন যে সার্ভিসগুলো আছে</b> "
                            "(কোড = বটের দেখানো কোড-সংখ্যা):<br>"
                            + " · ".join(_parts)
                            + "<br>🆕 নতুন সার্ভিস বটে এখনো বন্ধ — উপরে লাইন "
                              "লিখলেই চালু হবে।</div>")
        except Exception:
            _ml_live = ""
    except Exception:
        _gn_on, _gn_svcs, _gn_key_ok = True, [], False
        _gn_api, _gn_hdr = "https://api.zenexnetwork.com/v1/getnum", "mapikey"
        _gn_sms, _gn_ch = "", -1003728636195
        _gn_rate, _gn_group, _gn_list = 0.25, "", ""
        _gn_feedapi = "https://www.zenexnetwork.com/api/v1/global-broadcast"
    try:
        import bot as _gnt2
        _gk_raw = str(_gnt2.getnum_key() or "")
    except Exception:
        _gk_raw = ""
    _gn_key_mask = ((_gk_raw[:7] + "…" + _gk_raw[-4:])
                    if len(_gk_raw) > 14 else _gk_raw)
    _gn_sel = ("<option value='on' selected>চালু</option><option value='off'>বন্ধ</option>"
               if _gn_on else
               "<option value='on'>চালু</option><option value='off' selected>বন্ধ</option>")
    _ml_sel = ("<option value='on' selected>চালু</option><option value='off'>বন্ধ</option>"
               if _ml_on else
               "<option value='on'>চালু</option><option value='off' selected>বন্ধ</option>")
    _ml_mask = ((_ml_raw[:7] + "…" + _ml_raw[-4:])
                if len(_ml_raw) > 14 else _ml_raw)
    _gn_txt = esc("\n".join(
        f"{sv.get('name') or ''} | {it.get('name') or ''} | {it.get('range') or ''} | "
        f"{(float(it.get('price') or 0)):g}"
        for sv in _gn_svcs for it in (sv.get("items") or [])))
    _gn_live = []
    if _gn_feed_on:        # ⛔ লাইভ ফিড বন্ধ থাকলে কোনো কোয়েরি নয়
        try:
            _gn_live = many("SELECT service, range_name, range_val, COUNT(*) AS n, "
                            "MAX(updated_at) AS last, SUM(price) AS paid "
                            "FROM getnum_orders WHERE status='received' "
                            "GROUP BY service, range_name, range_val ORDER BY n DESC LIMIT 30")
        except Exception:
            _gn_live = []
    if _gn_live:
        _live_html = table(
            ["সার্ভিস", "দেশ", "রেঞ্জ", "কোড এসেছে", "দেওয়া হয়েছে", "শেষ কোড"],
            [[esc(str(r.get("service") or "")), esc(str(r.get("range_name") or "")),
              f"<span class='mono'>{esc(str(r.get('range_val') or ''))}</span>",
              num(int(r.get("n") or 0)),
              ("—" if not float(r.get("paid") or 0)
               else f"{float(r.get('paid') or 0):.4g}৳"),
              f"<span class='mut'>{esc(str(r.get('last') or ''))}</span>"
              ] for r in _gn_live], "এখনো কোনো কোড আসেনি")
    else:
        _live_html = ("<div class='mut' style='font-size:12px;margin-top:8px'>"
                      "এখনো কোনো OTP আসেনি — আসলে এখানে সার্ভিস/দেশ/রেঞ্জসহ দেখা যাবে "
                      "(সব ইউজারের, শুধু আপনার না)।</div>")

    _gn_feed_rows = []
    if _gn_feed_on:
        try:
            _gn_feed_rows = many("SELECT service, range_val, country, msg, created_at "
                                 "FROM getnum_feed ORDER BY id DESC LIMIT 25")
        except Exception:
            _gn_feed_rows = []
    if _gn_feed_rows:
        _feed_html = table(
            ["সময়", "সার্ভিস", "রেঞ্জ", "দেশ", "কোডের মেসেজ"],
            [[f"<span class='mut'>{esc(str(r.get('created_at') or '')[5:16])}</span>",
              esc(str(r.get("service") or "")),
              f"<span class='mono'>{esc(str(r.get('range_val') or ''))}</span>",
              esc(str(r.get("country") or "")),
              f"<span class='mut'>{esc(str(r.get('msg') or '')[:60])}</span>"]
             for r in _gn_feed_rows], "ফিড এখনো খালি")
    else:
        _feed_html = ("<div class='mut' style='font-size:12px;margin-top:6px'>"
                      "জেনেক্সের পাবলিক ফিড এখনো আসেনি — কয়েক সেকেন্ড পরে রিফ্রেশ করুন।</div>")

    if _gn_feed_on:
        _diag_html = ("<div style='margin-top:14px;padding-top:10px;border-top:1px solid #ddd'>"
                      "<b>📊 কোথায় কোড আসছে (সব ইউজার)</b>"
                      "<div class='mut' style='font-size:11.5px;margin:4px 0'>"
                      "যে সার্ভিস/দেশে OTP এসেছে সেগুলো এখানে জমা হয়।</div>"
                      + _live_html + "</div>"
                      "<div style='margin-top:14px;padding-top:10px;border-top:1px solid #ddd'>"
                      "<b>🌍 জেনেক্স নেটওয়ার্কে সবার কোড (লাইভ)</b>"
                      "<div class='mut' style='font-size:11.5px;margin:4px 0'>"
                      "পাবলিক ফিড — সব ইউজারের কোড, নাম্বার মাস্ক করা।</div>"
                      + _feed_html + "</div>")
    else:
        _diag_html = ("<div class='mut' style='font-size:12px;margin-top:12px'>"
                      "⛔ লাইভ OTP ফিড বন্ধ — ফিড API কল হবে না, ডাটাবেজে কিছু "
                      "সেভ হবে না, OTP চ্যানেলেও কিছু পোস্ট হবে না।</div>")
    getnumcard = "".join([
        "<div class='card'><b>📱 নাম্বার নিন (OTP)</b>",
        "<div class='mut' style='font-size:12px;margin:6px 0'>বটের কীবোর্ডে “📱 নাম্বার নিন” → "
        "সার্ভিস → দেশ → নাম্বার। OTP এলে ইউজারের ইনবক্সে আর নিচের চ্যানেলে যাবে। "
        "API Key: ", (("✅ সেভ আছে — <span class='mono'>" + esc(_gn_key_mask) + "</span>")
        if _gn_key_ok else "❌ এখনো দেওয়া হয়নি"),
        " · সার্ভিস: " + num(len(_gn_svcs)) + "টি · নাম্বার: " +
        num(sum(len(sv.get("items") or []) for sv in _gn_svcs)) + "টি</div>",
        "<form method='post' action='/admin/shop/getnum'>",
        "<div class='field'><label>অপশন</label><select name='getnum_on'>" + _gn_sel + "</select></div>",
        "<div class='field'><label>API Key (mapikey) — সেভ আছে; বদলাতে চাইলে পুরোটা মুছে নতুনটা দিন</label>"
        f"<input name='getnum_key' value='{esc(_gn_key_mask)}' autocomplete='off' "
        "placeholder='এখানে API Key পেস্ট করুন'></div>",
        "<div class='field'><label>নাম্বার আনার API URL</label>"
        f"<input name='getnum_api' value='{esc(_gn_api)}'></div>",
        "<div class='field'><label>OTP চেক করার API URL — "
        "{number} / {range} / {order} বসাতে পারেন</label>"
        f"<input name='getnum_sms_api' value='{esc(_gn_sms)}' "
        "placeholder='https://api.zenexnetwork.com/v1/sms?number={number}'></div>",
        "<div class='field'><label>OTP চ্যানেল আইডি</label>"
        f"<input name='getnum_ch_id' value='{_gn_ch}'></div>",
        "<div class='field'><label>OTP রেট (৳) — প্রতি OTP-তে ইউজার যা পাবে "
        "(ইউজার ডলারেও দেখবে)</label>"
        f"<input name='getnum_rate' value='{_gn_rate:g}'></div>",
        "<div class='field'><label>🛡 OTP গ্রুপের লিংক (খালি রাখলে বাটন দেখাবে না)</label>"
        f"<input name='getnum_group_url' value='{esc(_gn_group)}' placeholder='https://t.me/...'></div>",
        "<div class='field'><label>🔄 লাইভ রেঞ্জ API (সার্ভিস/দেশ/রেট নিজেই আপডেট হবে)</label>"
        f"<input name='getnum_list_api' value='{esc(_gn_list)}'></div>",
        "<div class='field'><label>🌍 সবার OTP ফিড API (পাবলিক — নতুন দেশ নিজে থেকে যোগ হয়; "
        "বন্ধ করতে - লিখুন)</label>"
        f"<input name='getnum_feed_api' value='{esc(_gn_feedapi)}'></div>",
        "<div class='field'><label>Key যে হেডারে যাবে</label>"
        f"<input name='getnum_header' value='{esc(_gn_hdr)}'></div>",
        "<div class='mut' style='font-size:12px;margin:4px 0 10px'>✅ সার্ভিস/দেশ/রেঞ্জ "
        f"({num(sum(len(sv.get('items') or []) for sv in _gn_svcs))}টি) বট নিজেই লাইভ ফিড থেকে "
        "যোগ করে — এখানে হাতে কিছু করতে হবে না।</div>",
        "<div style='margin-top:16px;padding-top:12px;border-top:1px solid #ddd'>"
        "<b>✉️ মেইল কিনুন (SmsBower mails API)</b>"
        "<div class='mut' style='font-size:12px;margin:4px 0 8px'>"
        "বটের 🔑 OTP → ✉️ OTP Mail Account — মেইল কেনা, কোড পড়া (প্রোভাইডারে নাম্বার "
        "২৫ মিনিট থাকে, বটও ততক্ষণ কোড দেখে)। ২৫ মিনিটে কোড না এলে অর্ডার "
        "অটো বাতিল হয় — প্রোভাইডারের রিফান্ড নিশ্চিত হওয়ার পরেই ইউজারের "
        "ব্যালেন্সে টাকা ফেরত যায়। চাইলে ২৫ মিনিটের আগেই ইউজার নিজে Cancel "
        "করতে পারে। দাম = প্রোভাইডারের লাইভ $ দাম × ৳ রেট + লাভ।</div>"
        "<div class='field'><label>অপশন</label><select name='mail_on'>" + _ml_sel + "</select></div>"
        "<div class='field'><label>API Key — সেভ আছে; বদলাতে চাইলে পুরোটা মুছে নতুনটা দিন</label>"
        f"<input name='mail_key' value='{esc(_ml_mask)}' autocomplete='off' "
        "placeholder='এখানে mail API Key পেস্ট করুন'></div>"
        "<div class='field'><label>API Base URL</label>"
        f"<input name='mail_api' value='{esc(_ml_api)}'></div>"
        "<div class='field'><label>ডিফল্ট লাভ (%) — ডলার দামের উপর %, যে আইটেমের আলাদা লাইন নেই</label>"
        f"<input name='mail_markup' value='{_ml_markup:g}'></div>"
        "<div class='field'><label>আইটেম-ভিত্তিক লাভ (প্রতি লাইনে: service | domain | লাভ)</label>"
        "<textarea name='mail_markup_map' rows='6' style='width:100%' "
        "placeholder='gmail.com | 40&#10;outlook.com | +20&#10;"
        "whatsapp | gmail.com | 50'>" + esc(_ml_map_raw) + "</textarea>"
        "<div class='mut' style='font-size:12px;margin:4px 0 8px'>"
        "<b>40</b> বা <b>40%</b> = ৪০% লাভ · <b>+20</b> = ২০৳ নির্দিষ্ট লাভ (দামের উপর যোগ) · "
        "প্রথম ঘর খালি রাখলে (gmail.com | 40) সব সার্ভিসের ওই ডোমেইনে কাজ করবে।<br>"
        "💵 প্রোভাইডারের দাম <b>ডলারে</b> — % লাভ বসে ওই ডলার দামের উপরেই, তারপর "
        "ডলার-রেটে ৳ হয় (যেমন $0.1 + ৩০% → ১.৩× → ৳)। তাই ওয়েবে দাম বাড়লে "
        "বটের দামও একই %ে সাথে সাথে বাড়ে; কেনার মুহূর্তে দাম আবার লাইভ চেক হয়।</div></div>"
        "<div class='field' style='margin-top:10px'>"
        "<label>🔢 সার্ভিস-ভিত্তিক কোড সংখ্যা (প্রতি লাইনে: service | কয়টি)</label>"
        "<textarea name='mail_limits' rows='6' style='width:100%' "
        "placeholder='instagram | 6&#10;facebook | 10&#10;telegram | 3'>"
        + esc(_ml_lim_raw) + "</textarea>"
        "<div class='mut' style='font-size:12px;margin:4px 0 8px'>"
        "ইউজার মেইল কেনার আগে/পরে দেখবে এই সার্ভিসে কয়টি রেজিস্ট্রেশন-কোড "
        "পাওয়া যাবে (লগইন অ্যালার্ট বা অন্য মেইল গোনা হয় না)। খালি রাখলে "
        "বটের ডিফল্ট লিস্ট (Instagram 6, Facebook 10, Telegram 3 …) ব্যবহার হবে।<br>"
        "• বাম দিকে <b>প্রোভাইডারের আসল সার্ভিস কোড</b> লিখুন (যেমন <b>ig</b>, <b>fb</b>, "
        "<b>telegram</b>) — বট ছোট কোড/পুরো নাম দুটোই মিলিয়ে নেয়।<br>"
        "• যে সার্ভিসের লাইন এখানে নেই সেটি <b>বটে বন্ধ</b> থাকে — ওয়েবে নতুন সার্ভিস "
        "এলে আপনাকে বটে মেসেজ আসবে, আপনি লাইন লিখলেই ইউজার দেখতে পাবে।</div>"
        + _ml_live +
        "</div></div>"
        "</div>",
        "<button class='ok sm' type='submit'>💾 সেভ করুন</button>",
        " <a class='btn gray sm' href='/admin/shop/getnum/test'>🧪 টেস্ট (প্রথম নাম্বার)</a>",
        "</form>",
        _diag_html,
        "</div>",
    ])
    return getnumcard


@wadm.route("/admin/shop/number", methods=["GET"])
def shop_number():
    """শপের ভিতরের আলাদা পেজ — শুধু নাম্বার/OTP সেটিং।"""
    g = guard()
    if g:
        return g
    _init()
    return page("📱 নাম্বার নিন (OTP)", _gn_page_html(), "shopnum")


def _px_admin_html():
    """🔍 Proxy Checker — আলাদা সেটিং কার্ড (SMM/Number-এর মতোই)।

    এক জায়গায় সব প্রোভাইডারের নিজের API: প্রতি লাইন `provider | key/URL`।
    """
    _on, _map, _names = True, "", ()
    try:
        import bot as _b
        _on = bool(getattr(_b, "px_on", lambda: True)())
        _map = str(_b.get_setting("px_usage_map") or "")
        _names = tuple(getattr(_b, "PX_USAGE_NAMES", ()))
    except Exception:
        pass
    _sel = ("<option value='on' selected>চালু</option><option value='off'>বন্ধ</option>" if _on
            else "<option value='on'>চালু</option><option value='off' selected>বন্ধ</option>")
    # বর্তমান লাইন → প্রতি প্রোভাইডারের নিজের ভ্যালু
    _cur, _dn = {}, ""
    for _ln in _map.replace("\r", "").split("\n"):
        _p = [x.strip() for x in _ln.replace("—", "|").split("|") if x.strip()]
        if len(_p) < 2:
            continue
        if _p[0].lower() == "*":
            _dn = "|".join(_p[1:]).strip()
        else:
            _cur[_p[0].lower()] = "|".join(_p[1:]).strip()
    _rows = []
    for _i, _n in enumerate(_names):
        _v = _cur.get(_n.lower(), "")
        _rows.append(
            "<div class='field'><label>" + esc(_n) + (
                " <span class='mut' style='font-weight:400'>✅ দেওয়া আছে</span>"
                if _v else "") + "</label>"
            "<input name='pxp_" + str(_i) + "' value='" + esc(_v) + "' "
            "placeholder='API key · https://…{user}… · manual:1024/800' "
            "style='width:100%'></div>")
    return (
        "<div class='card'><a class='btn gray sm' href='/admin/shop'>‹ Shop</a>"
        "<h3 style='margin:8px 0 4px'>🔍 Proxy Checker</h3>"
        "<div class='mut' style='font-size:12px;margin:0 0 10px'>"
        "বটের 🔌 Proxy-IP পেজে “CHECK PROXY” বাটন। ইউজার "
        "<b>IP:PORT:USERNAME:PASSWORD</b> পাঠালে Provider · Status (LIVE/DEAD) · IP · "
        "MB/GB দেখায় (শুধু ইউজারনেম গ্রহণ করা হয় না)।</div>"
        "<form method='post' action='/admin/shop/proxy/save'>"
        "<div class='field'><label>অপশন</label><select name='px_on'>" + _sel + "</select></div>"
        "<div class='mut' style='font-size:12px;margin:0 0 8px'>"
        "প্রত্যেক প্রোভাইডারের <b>নিজের আলাদা API</b> — যে লাইনে যেটা দিবেন বট শুধু সেই "
        "প্রোভাইডারেই ব্যবহার করবে (711-এর key 9proxy-তে যাবে না)। ”না দেওয়া“ মানে ওই "
        "প্রোভাইডারে MB/GB দেখাবে না, শুধু LIVE/DEAD।</div>"
        "<div class='mut' style='font-size:12px;margin:0 0 10px'>"
        "• <b>9Proxy</b> → শুধু API key পেস্ট করুন, ঠিকানা বট নিজেই জানে।<br>"
        "• অন্য প্রোভাইডারে URL-এ <b>{user}</b> / <b>{pass}</b> / <b>{host}</b> / "
        "<b>{port}</b> বসাতে পারেন।<br>"
        "• API না থাকলে নিজের কোটা: <span class='mono'>manual:1024/800</span> "
        "(মোট/বাকি, MB) — ইউজার কার্ডে এটাই দেখাবে।</div>"
        + "".join(_rows) +
        "<div class='field'><label>* বাকি সব প্রোভাইডারের ডিফল্ট (ঐচ্ছিক)</label>"
        "<input name='pxp_def' value='" + esc(_dn) + "' placeholder='API key বা URL' "
        "style='width:100%'></div>"
        "<button class='ok sm' type='submit'>💾 সেভ করুন</button>"
        "<a class='btn gray sm' href='/admin/shop/proxy/test' "
        "style='margin-left:6px'>🔎 সব প্রোভাইডারের MB/GB</a>"
        "</form></div>")


@wadm.route("/admin/shop/proxy", methods=["GET"])
def shop_proxy_page():
    """🔍 Proxy Checker — আলাদা পেজ (নাম্বার/মেইল কার্ডের বাইরে)।"""
    g = guard()
    if g:
        return g
    _init()
    return page("🔍 Proxy Checker", _px_admin_html(), "shopproxy")


@wadm.route("/admin/shop/proxy/test", methods=["GET"])
def shop_proxy_test():
    """🔎 অ্যাডমিনের জন্য — প্রতিটি প্রোভাইডারের API থেকে ব্যবহার/মোট MB।"""
    g = guard()
    if g:
        return g
    _init()
    _rows, _ok = [], 0
    try:
        import bot as _bx
        _cfg = dict(_bx._px_usage_cfg())
        for _n in tuple(getattr(_bx, "PX_USAGE_NAMES", ())):
            _key = _n.lower()
            _v = _cfg.get(_key, "")
            if not _v:
                for _k, _vv in _cfg.items():
                    if _k not in ("*", "all", "default") and (
                            _k in _key or _key in _k):
                        _v = _vv
                        break
            if not _v:
                _v = _cfg.get("*", "")
            if not _v:
                _got = ("<span style='color:#c00'>API লাইন নেই</span>")
            else:
                _u = _bx._px_usage({}, _n)
                _tag = (" <span class='mut'>(অ্যাডমিন-সেট)</span>"
                        if _v.lower().startswith("manual:") else "")
                if _u:
                    _ok += 1
                    _got = ("<b>" + _bx._px_mb(_u[0]) + "</b> ব্যবহূত · মোট "
                            + _bx._px_mb(_u[1]) + _tag)
                else:
                    _got = ("<span style='color:#c00'>ডেটা আসেনি</span> "
                            "<span class='mut'>— key/URL ঠিক আছে কি না দেখুন</span>")
            _rows.append("<tr><td style='padding:6px 4px;border-bottom:1px solid #2a2a2a'>"
                         + esc(_n) + "</td><td style='padding:6px 4px;"
                         "border-bottom:1px solid #2a2a2a'>" + _got + "</td></tr>")
    except Exception as e:
        _rows = ["<tr><td colspan='2'>ত্রুটি: " + esc(str(e)[:140]) + "</td></tr>"]
    _body = ("<div class='card'><a class='btn gray sm' href='/admin/shop/proxy'>"
             "‹ Proxy Checker</a>"
             "<h3 style='margin:8px 0 4px'>🔎 সব প্রোভাইডারের MB/GB</h3>"
             "<div class='mut' style='font-size:12px;margin:0 0 8px'>"
             "এই পেজ শুধু আপনিই দেখেন (ইউজার নয়)। সফল: " + str(_ok) + "টি। "
             "প্রতিটা প্রোভাইডারে আলাদা API লাইন লাগবে।"
             "</div><table style='width:100%;font-size:13px;border-collapse:collapse'>"
             "<tr><th align='left'>প্রোভাইডার</th><th align='left'>ব্যবহার / মোট</th></tr>"
             + "".join(_rows) + "</table></div>")
    return page("🔎 Proxy MB/GB", _body, "shopproxy")


@wadm.route("/admin/shop/proxy/save", methods=["POST"])
def shop_proxy_save():
    """Proxy Checker সেটিং — চালু/বন্ধ + প্রোভাইডার-ভিত্তিক API লিস্ট।"""
    g = guard()
    if g:
        return g
    _init()
    form = request.form
    try:
        import bot as _bs0
        _names = tuple(getattr(_bs0, "PX_USAGE_NAMES", ()))
        _old = str(_bs0.get_setting("px_usage_map") or "")
    except Exception:
        _names, _old = (), (form.get("px_usage_map") or "")
    _pxl = []
    for _i, _n in enumerate(_names):
        _v = (form.get("pxp_" + str(_i)) or "").strip().replace("\n", " ")
        if _v:
            _pxl.append((_n[:40] + " | " + _v[:360]).strip())
    _dv = (form.get("pxp_def") or "").strip().replace("\n", " ")
    if _dv:
        _pxl.append("* | " + _dv[:360])
    # চেনা প্রোভাইডারের বাইরে অ্যাডমিনের নিজের লেখা লাইনগুলো রেখে দিই
    _known = {n.lower() for n in _names} | {"*"}
    for _ln in _old.replace("\r", "").split("\n")[:40]:
        _p = [x.strip() for x in _ln.replace("—", "|").split("|") if x.strip()]
        if len(_p) < 2 or _p[0].lower() in _known:
            continue
        _pxl.append((_p[0][:40] + " | " + "|".join(_p[1:])[:360]).strip())
    vals = {
        "px_on": "off" if (form.get("px_on") or "on").strip().lower() == "off" else "on",
        "px_usage_map": "\n".join(_pxl)[:4000],
    }
    try:
        import bot as _bs
        for k, v in vals.items():
            _bs.set_setting(k, v)
    except Exception:
        for k, v in vals.items():
            _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, v))
    log_admin("shop_proxy", 0, "saved")
    return redirect("/admin/shop/proxy?ok=" + _q("🔍 Proxy Checker সেভ হয়েছে"))


def _lamix_admin_html():
    """☎️ Lamix SMS API — টোকেন + প্যানেল ঠিকানা (শুধু API সেটিংস)।"""
    _onoff, _key, _base = "on", "", "https://panel.lamix.org"
    try:
        import bot as _b
        _onoff = str(_b.get_setting("lamix_on") or "on").strip().lower()
        _key = str(getattr(_b, "lamix_key", lambda: "")() or "")
        _base = str(getattr(_b, "lamix_base", lambda: "")() or "")
    except Exception:
        pass
    _sel = ("<option value='on' selected>চালু</option><option value='off'>বন্ধ</option>"
            if _onoff != "off" else
            "<option value='on'>চালু</option><option value='off' selected>বন্ধ</option>")
    _mask = ((_key[:6] + "…" + _key[-4:]) if len(_key) > 12 else _key)
    return (
        "<div class='card'><a class='btn gray sm' href='/admin/'>‹ Dashboard</a>"
        "<h3 style='margin:8px 0 4px'>☎️ Lamix SMS API</h3>"
        "<div class='mut' style='font-size:12px;margin:0 0 10px'>"
        "Lamix panel-এর REST API (v1) — Bearer token। रেট লিমিট ১/সেকেন্ড, "
        "৪০/মিনিট (প্রতি endpoint-এ আলাদা)। এই পেজ শুধু <b>API সংযোগ</b> সেট করে; "
        "ইউজারকে নাম্বার দেখানো/নেওয়ার ফ্লো এখনো যোগ হয়নি।</div>"
        "<form method='post' action='/admin/lamix/save'>"
        "<div class='field'><label>অপশন</label><select name='lamix_on'>" + _sel + "</select></div>"
        "<div class='field'><label>API Token — সেভ আছে; বদলাতে চাইলে পুরোটা মুছে নতুনটা দিন</label>"
        f"<input name='lamix_key' value='{esc(_mask)}' autocomplete='off' "
        "placeholder='প্যানেল → REST API পেজের টোকেন'></div>"
        "<div class='field'><label>Panel Base URL</label>"
        f"<input name='lamix_api' value='{esc(_base)}' "
        "placeholder='https://panel.lamix.org'></div>"
        "<div class='mut' style='font-size:12px;margin:0 0 10px'>"
        "⚠️ টোকেন কোথাও শেয়ার করবেন না — আপনার লগইনের সমান ক্ষমতা দেয়।</div>"
        "<button class='ok sm' type='submit'>💾 সেভ করুন</button>"
        "<a class='btn gray sm' href='/admin/lamix/test' style='margin-left:6px'>🔎 সংযোগ পরীক্ষা</a>"
        "</form></div>")


@wadm.route("/admin/lamix", methods=["GET"])
def shop_lamix_page():
    """☎️ Lamix SMS API — আলাদা (Shop-এর ভিতরে নয়) সেটিংস পেজ।"""
    g = guard()
    if g:
        return g
    _init()
    return page("☎️ Lamix SMS API", _lamix_admin_html(), "shoplamix")


@wadm.route("/admin/lamix/save", methods=["POST"])
def shop_lamix_save():
    """Lamix API সেটিং — চালু/বন্ধ · টোকেন · প্যানেল ঠিকানা।"""
    g = guard()
    if g:
        return g
    _init()
    form = request.form
    vals = {
        "lamix_on": ("off" if (form.get("lamix_on") or "on").strip().lower() == "off"
                    else "on"),
    }
    _b = (form.get("lamix_api") or "").strip().rstrip("/")[:300]
    if _b:
        vals["lamix_api"] = _b
    _k = "".join((form.get("lamix_key") or "").split())[:300]
    if _k and "…" not in _k and "*" not in _k:     # মাস্ক করা পুরোনো ভ্যালু নয়
        vals["lamix_key"] = _k
    try:
        import bot as _bs
        for k, v in vals.items():
            _bs.set_setting(k, v)
        _bs._LAMIX_RANGES.update({"ts": 0.0, "rows": []})
    except Exception:
        for k, v in vals.items():
            _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, v))
    log_admin("shop_lamix", 0, "saved")
    return redirect("/admin/lamix?ok=" + _q("☎️ Lamix API সেভ হয়েছে"))


@wadm.route("/admin/lamix/test", methods=["GET"])
def shop_lamix_test():
    """🔎 Lamix-এ সংযোগ/টোকেন ঠিক কি না + রেঞ্জ/নাম্বার পড়া যায় কি না।"""
    g = guard()
    if g:
        return g
    _init()
    _rows = []
    try:
        import bot as _bx
        _err = str(_bx.lamix_ping() or "")
        _rows.append("<tr><td>সংযোগ</td><td>" + (
            "<b style='color:#0a0'>✅ ঠিক আছে</b>" if not _err else
            "<b style='color:#c00'>❌</b> " + esc(_err[:180])) + "</td></tr>")
        try:
            _rows.insert(0, "<tr><td>API ঠিকানা</td><td><span class='mono'>"
                            + esc(str(_bx.lamix_base()) + "/api/v1/messages")
                            + "</span></td></tr>")
        except Exception:
            pass
        _rg = _bx.lamix_ranges(force=True)
        _rows.append("<tr><td>রেঞ্জ</td><td>" + esc(str(len(_rg))) + "টি</td></tr>")
        _nm = _bx.lamix_numbers(limit=1)
        if _nm.get("error"):
            _rows.append("<tr><td>নাম্বার</td><td><span style='color:#c00'>"
                         + esc(str(_nm.get("message") or _nm.get("error"))[:180])
                         + "</span></td></tr>")
        else:
            _rows.append("<tr><td>নাম্বার</td><td>" + esc(str(_nm.get("count") or 0))
                         + "টি (মোট)</td></tr>")
        # 💬 কোড পড়া যাচ্ছে কি না — নয়া নাম্বার-টেক্সট দেখতে
        try:
            _ms = _bx.lamix_messages(limit=5)
            _rows.append("<tr><td>SMS (messages)</td><td>"
                         + esc(str(len(_ms))) + "টি</td></tr>")
            if _ms:
                _f0 = _bx._lx_rec_fields(_ms[0])
                _rows.append("<tr><td>সর্বশেষ SMS</td><td>নাম্বার <span class='mono'>"
                             + esc(str(_f0.get("number") or "❌"))
                             + "</span> · টেক্সট "
                             + ("✅" if _f0.get("content") else "❌")
                             + "</td></tr>")
                _rows.append("<tr><td>ফিল্ডের নাম</td><td class='mono'>"
                             + esc(str(", ".join(list(_ms[0])[:10]))[:200])
                             + "</td></tr>")
            _cd = _bx.lamix_cdrs(limit=5)
            _rows.append("<tr><td>SMS (cdrs)</td><td>"
                         + esc(str(len(_cd))) + "টি</td></tr>")
        except Exception as _mse:
            _rows.append("<tr><td>SMS</td><td>" + esc(str(_mse)[:160]) + "</td></tr>")
    except Exception as e:
        _rows.append("<tr><td colspan='2'>ত্রুটি: " + esc(str(e)[:160]) + "</td></tr>")
    _body = ("<div class='card'><a class='btn gray sm' href='/admin/lamix'>"
             "‹ Lamix API</a>"
             "<h3 style='margin:8px 0 4px'>🔎 Lamix সংযোগ পরীক্ষা</h3>"
             "<table style='width:100%;font-size:13px;border-collapse:collapse'>"
             "<tr><th align='left'>কী</th><th align='left'>ফল</th></tr>"
             + "".join(_rows) + "</table>"
             "<div class='mut' style='font-size:12px;margin-top:10px'>"
             "রেঞ্জ/নাম্বার পড়তে Extended API লাগে — `403 extended_api_disabled` এলে "
             "আপনার অ্যাকাউন্টে সেটি চালু করাতে হবে।</div></div>")
    return page("🔎 Lamix পরীক্ষা", _body, "shoplamix")


@wadm.route("/admin/shop", methods=["GET"])
def shop_home():
    g = guard()
    if g:
        return g
    _init()
    cats = _cats()
    prods = many(
        "SELECT p.*, (SELECT COUNT(*) FROM shop_stock s WHERE s.product_id=p.id "
        "AND COALESCE(s.sold,0)=0) AS stock FROM shop_products p "
        "ORDER BY p.category_id, p.sort_order, p.id")
    _since = ""
    try:
        _r_since = one("SELECT value FROM settings WHERE key='stats_since'") or {}
        _since = str(_r_since.get("value") or "").strip()
    except Exception:
        _since = ""
    _sw = (" AND created_at>=%s" if _since else "")
    _sa = ((_since,) if _since else ())
    pend = one("SELECT COUNT(*) AS n FROM shop_orders WHERE status='pending'" + _sw, _sa) or {}
    sold = one("SELECT COUNT(*) AS n FROM shop_orders WHERE status='delivered'" + _sw, _sa) or {}
    total_stock = sum(int((p or {}).get("stock") or 0) for p in prods)
    cat_map = {int(c["id"]): c for c in cats}

    head = ("<div class='grid g-kpi'>"
            + _kpi("ক্যাটাগরি", num(len(cats)))
            + _kpi("পণ্য", num(len(prods)))
            + _kpi("স্টক (বিক্রির জন্য)", num(total_stock))
            + _kpi("পেন্ডিং অর্ডার", num((pend or {}).get("n") or 0),
                   "warn" if int((pend or {}).get("n") or 0) else "")
            + _kpi("ডেলিভারি হয়েছে", num((sold or {}).get("n") or 0))
            + "</div>")

    # ── শপ / ডিপোজিট / টাস্ক — তিনটাই আলাদা সুইচ ──
    try:
        import bot as _bs2
        _s_on = bool(_bs2.shop_on())
        _dep_on = bool(_bs2.dep_on())
        _task_on = bool(_bs2.task_on())
        _d_lock = bool(_bs2.shop_off_dep_on())
    except Exception:
        _s_on, _dep_on, _task_on, _d_lock = True, True, True, True

    def _sw_card(label, on, key, hint_on, hint_off):
        _col = "color:#2e7d32" if on else "color:#c62828"
        _st = "চালু" if on else "বন্ধ"
        return ("<div style='margin-top:12px;padding-top:10px;border-top:1px solid #ddd'>"
                f"<b>{label}: <span style='{_col}'>{_st}</span></b>"
                "<div class='mut' style='font-size:12px;margin:6px 0'>"
                f"{hint_on if on else hint_off}</div>"
                "<form method='post' action='/admin/section/onoff' style='display:inline'>"
                f"<input type='hidden' name='key' value='{key}'>"
                f"<input type='hidden' name='val' value='{'off' if on else 'on'}'>"
                f"<button class='{'gray' if on else 'ok'} sm' type='submit'>"
                f"{'⏸ বন্ধ করুন' if on else '▶️ চালু করুন'}</button></form></div>")
    switch_card = (
        "<div class='card'>"
        "<b>🔒 সেকশন চালু / বন্ধ</b>"
        "<div class='mut' style='font-size:12px;margin:4px 0 0'>"
        "যেটা বন্ধ করবেন — ইউজার ওটায় চাপলে শুধু “এখন বন্ধ” মেসেজ দেখবে।"
        "অ্যাডমিন/ওনার সবসময় ঢুকতে পারবেন।</div>"
        + _sw_card("🛍 শপ", _s_on, "shop",
                   "ইউজার এখন কেনাকাটা করতে পারছে।",
                   "ইউজার '🔒 দোকান এখন বন্ধ' মেসেজ দেখবে।")
        + _sw_card("💳 ডিপোজিট", _dep_on, "dep",
                   "ইউজার টাকা যোগ করতে পারছে।",
                   "ইউজার '🔒 ডিপোজিট এখন বন্ধ' মেসেজ পাবে।")
        + _sw_card("📋 টাস্ক", _task_on, "task",
                   "ইউজার টাস্ক করতে পারছে।",
                   "ইউজার '🔒 টাস্ক সেকশন এখন বন্ধ' মেসেজ পাবে।")
        + "<div style='margin-top:12px;padding-top:10px;border-top:1px solid #ddd'>"
        + "<b>💳 দোকানের সাথে ডিপোজিট</b>"
        + "<div class='mut' style='font-size:12px;margin:6px 0'>"
        + ("দোকান বন্ধ থাকলে ডিপোজিটও বন্ধ থাকবে।"
           if _d_lock else
           "দোকান বন্ধ থাকলেও ডিপোজিট চালু থাকবে।")
        + "</div>"
        + "<form method='post' action='/admin/shop/deponoff' style='display:inline'>"
        + "<input type='hidden' name='val' value='%s'>" % ("off" if _d_lock else "on")
        + "<button class='gray sm' type='submit'>%s</button></form>"
        % ("✅ দোকান বন্ধ = ডিপোজিট বন্ধ (বদলাতে চাপুন)"
           if _d_lock else
           "⬜ দোকান বন্ধেও ডিপোজিট চালু (বদলাতে চাপুন)")
        + "</div></div>")

    # ✍️ বন্ধের বার্তা এখন Settings পেজে — এখানে আর রাখা হয় না
    msg_card = ""

    # ── 🎫 মেইল কোড সেটিংস (শুধু নাম + লিংক) ──
    try:
        import bot as _bm
        _m_on = bool(_bm.mailcode_on())
        _m_rows = _bm.mailcode_links()
    except Exception:
        _m_on, _m_rows = True, []
    _sel = ("<option value='on' selected>চালু</option><option value='off'>বন্ধ</option>"
            if _m_on else
            "<option value='on'>চালু</option><option value='off' selected>বন্ধ</option>")
    _txt_val = esc("\n".join(
        (str(r.get("name") or "") + " | " + str(r.get("url") or "")).strip()
        for r in _m_rows))
    mailcard = "".join([
        "<div class='card'><b>🎫 মেইল কোড নিন</b>",
        "<div class='mut' style='font-size:12px;margin:6px 0'>",
        "নিচে যত নাম + লিংক দেবেন, বটে ঠিক ততগুলো বাটন দেখাবে।</div>",
        "<form method='post' action='/admin/shop/mailcode'>",
        "<div class='field'><label>অপশন</label>"
        "<select name='mailcode_on'>" + _sel + "</select></div>",
        "<div class='field'><label>🔗 নাম | লিংক (প্রতি লাইনে একটা) — "
        f"এখন সেভ আছে {num(len(_m_rows))}টি</label>",
        "<textarea name='mailcode_links_text' rows='6' style='width:100%' "
        "placeholder='dongvanfb OTP | https://example.com/otp&#10;"
        "CODE YAMIN | https://example.com/yamin&#10;"
        "MailGen | https://mailgen.shop/'>" + _txt_val + "</textarea>",
        "<div class='mut' style='font-size:11px;margin-top:4px'>",
        "⬆️ উপরের ধূসর লেখা শুধু <b>উদাহরণ</b> — টাইপ না করলে সেভ হবে না। ",
        "নিজের লাইন লিখুন, যেমন: <b>MailGen | https://mailgen.shop/</b> — বটে বাটনের নাম হবে "
        "“MailGen”। নাম না দিলে লিংকের সাইটের নাম বসবে।</div></div>",
        "<button class='ok sm' type='submit'>💾 সেভ করুন</button>",
        "</form></div>",
    ])

    # ক্যাটাগরি তৈরি + ক্রেন্সি
    cur = esc(_cur())
    # 💵 ডলার↔টাকা রেট — এই রেটেই বটের সব দাম/ডিপোজিট হিসাব হয়
    try:
        _rate_now = float(str((one("SELECT value FROM settings WHERE key='qr_rate'") or {})
                             .get("value") or "125").replace(",", "."))
    except Exception:
        _rate_now = 125.0
    if _rate_now <= 0:
        _rate_now = 125.0
    # 📦 প্যাকের তালিকা — এখান থেকেই নাম বদলানো / ডিলিট করা যাবে
    plan_rows = []
    for pl in _plans():
        _cc = cat_map.get(int(pl["category_id"] or 0)) or {}
        plan_rows.append([
            esc(str(_cc.get("emoji") or "🛍") + " " + str(_cc.get("name") or "—")),
            f"<b>{esc(_plan_txt(pl))}</b>",
            _btn("✏️ এডিট", f"/admin/shop/plan/{int(pl['id'])}/edit", "btn gray sm")
            + "<form method='post' action='/admin/shop/plan/%d/delete' "
              "style='display:inline;margin-left:6px' "
              "onsubmit=\"return confirm('প্যাকটা ডিলিট হবে? পণ্যগুলো প্যাক ছাড়া হয়ে থাকবে।')\">"
              "<button class='gray sm' type='submit'>🗑 ডিলিট</button></form>" % int(pl["id"]),
        ])

    tools = (
        "<div class='card'><b>➕ নতুন ক্যাটাগরি</b>"
        "<form method='post' action='/admin/shop/cat/add' class='row' style='gap:8px;margin-top:8px'>"
        "<input name='emoji' placeholder='🛍' style='max-width:70px'>"
        "<input name='name' placeholder='ক্যাটাগরির নাম (যেমন VPN)' required>"
        "<button class='ok sm' type='submit'>যোগ করুন</button></form>"
        "<div class='mut' style='font-size:12px;margin-top:6px'>ইউজার বটে এই ক্যাটাগরিগুলো বাটন হিসেবে দেখবে।"
        "</div></div>"
        "<div class='card'><b>➕ নতুন প্যাক (ক্যাটাগরির ভিতরে)</b>"
        "<form method='post' action='/admin/shop/plan/add' class='row' "
        "style='gap:8px;margin-top:8px;flex-wrap:wrap'>"
        "<select name='category_id' required style='min-width:160px'>"
        + "".join(f"<option value='{int(c['id'])}'>"
                  f"{esc(c['emoji'] or '🛍')} {esc(c['name'] or '')}</option>"
                  for c in cats)
        + "</select>"
        "<input name='label' placeholder='প্যাকের নাম (যেমন ১ মাস / ৭ দিন / India Proxy)' "
        "required style='min-width:220px'>"
        "<button class='ok sm' type='submit'>যোগ করুন</button></form>"
        "<div class='mut' style='font-size:12px;margin-top:6px'>ইউজার ক্যাটাগরিতে চাপলে "
        "আগে এই প্যাকগুলো দেখবে (কোন প্যাক নিবেন?) — প্যাক বেছে নিলে তার ভিতরের পণ্য।</div>"
        "</div>"
        + "<div class='card'><b>📦 প্যাক (এডিট / ডিলিট)</b>"
        + _group_details(plan_rows, 0, ["ক্যাটাগরি", "প্যাক", ""],
                         "কোনো প্যাক নেই — উপরে যোগ করুন")
        + "<div class='mut' style='font-size:12px;margin-top:6px'>"
          "প্যাকের নাম বদলাতে ✏️, বাদ দিতে 🗑 চাপুন।</div></div>"
        "<div class='card'>" + _user_preview(cats, prods) + "</div>"
        "<div class='card'><b>💱 মুদ্রা ও রেট</b>"
        "<div class='mut' style='font-size:12px;margin:6px 0'>"
        "পণ্যের দাম ডলারে লিখুন — বটে দেখাবে ডলার + পাশে টাকা "
        "(যেমন 0.78$ · 97.5 ৳)। "
        f"এখন <b>১$ = {_rate_now:g} ৳</b> — এই রেট দিয়েই সব দাম, ডিপোজিট ও ব্যালেন্স "
        "হিসাব হয়। রেট বদলালে সাথে সাথে বটের সব দাম বদলে যাবে।</div>"
        "<a class='btn gray sm' href='/admin/deposits'>🇧🇩 রেট বদলান (QR সেটিং)</a>"
        "</div>"
    )

    cat_rows = []
    for c in cats:
        n = sum(1 for p in prods if int(p["category_id"] or 0) == int(c["id"]))
        cat_rows.append([
            f"<a href='/admin/shop/cat/{int(c['id'])}' style='text-decoration:none'>"
            f"{esc(c['emoji'] or '🛍')} <b>{esc(c['name'] or '')}</b></a>"
            f"<div class='mut' style='font-size:11px'>ভিতরে দেখুন ›</div>",
            num(n),
            badge("চালু", "ok") if _onoff(c["is_active"]) else badge("বন্ধ", "mut"),
            "<form method='post' action='/admin/shop/cat/%d/toggle' style='display:inline'>"
            "<button class='btn gray sm' type='submit'>%s</button></form> "
            % (int(c["id"]), "🔴 বন্ধ" if _onoff(c["is_active"]) else "🟢 চালু"),
            "<form method='post' action='/admin/shop/cat/%d/delete' style='display:inline' "
            "onsubmit=\"return confirm('ক্যাটাগরি + এর পণ্যগুলো ডিলিট হবে?')\">"
            "<button class='gray sm' type='submit'>🗑</button></form>" % int(c["id"]),
        ])

    prod_rows = []
    for p in prods:
        c = cat_map.get(int(p["category_id"] or 0))
        stock = int(p["stock"] or 0)
        _t = str(p["deliver_type"] or "auto").lower()
        typ = {"auto": "🤖 অটো", "manual": "✍️ ম্যানুয়াল",
               "hybrid": "⚡ হাইব্রিড"}.get(_t, "🤖 অটো")
        prod_rows.append([
            f"<a href='/admin/shop/{int(p['id'])}'><b>{esc(p['name'] or '')}</b></a>",
            esc(((c or {}).get("emoji") or "") + " " + ((c or {}).get("name") or "—")),
            _fmt(p["price"]),
            typ,
            badge(f"স্টক {stock}", "ok" if stock > 0 else "err") if typ == "অটো" else badge("—", "mut"),
            badge("চালু", "ok") if _onoff(p["is_active"]) else badge("বন্ধ", "mut"),
            _btn("📥 স্টক", f"/admin/shop/{int(p['id'])}/stock", "btn gray sm")
            + _btn("✏️", f"/admin/shop/{int(p['id'])}", "btn gray sm"),
        ])

    # ── 📱 নাম্বার নিন (OTP) — আলাদা পেজে গুছানো (শপের ভিতরেই) ──
    getnum_link = (
        "<div class='card'><div class='row' style='justify-content:space-between;"
        "align-items:center'><b>📱 নাম্বার নিন (OTP)</b>"
        + _btn("⚙️ সেটিং খুলুন", "/admin/shop/number", "btn ok sm")
        + "</div><div class='mut' style='font-size:12px;margin-top:6px'>"
        "API key · সার্ভিস/দেশ/রেট · OTP চ্যানেল/ফিড — সব আলাদা পেজে।</div></div>")
    # 🔍 Proxy Checker — আলাদা কার্ড/পেজ (নাম্বার কার্ডের ভিতরে নয়)
    proxy_link = (
        "<div class='card'><div class='row' style='justify-content:space-between;"
        "align-items:center'><b>🔍 Proxy Checker (LIVE/DEAD + MB/GB)</b>"
        + _btn("⚙️ সেটিং খুলুন", "/admin/shop/proxy", "btn ok sm")
        + "</div><div class='mut' style='font-size:12px;margin-top:6px'>"
        "প্রতি প্রোভাইডারের নিজের API এক জায়গায় · চালু/বন্ধ।</div></div>")


    body = (
        head + switch_card + msg_card + mailcard + getnum_link + proxy_link + tools
        + "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
          "<b>📁 ক্যাটাগরি</b>" + _btn("🛒 অর্ডার দেখুন", "/admin/shop/orders", "btn gray sm")
        + "</div>" + table(["ক্যাটাগরি", "পণ্য", "স্ট্যাটাস", "অন/অফ", ""], cat_rows,
                           "কোনো ক্যাটাগরি নেই — উপরে যোগ করুন")
        + "</div>"
        + "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
          "<b>📦 পণ্য</b>" + _btn("➕ নতুন পণ্য", "/admin/shop/new", "btn ok sm")
        + "</div>" + _group_details(
            prod_rows, 1, ["পণ্য", "ক্যাটাগরি", "দাম", "ডেলিভারি", "স্টক", "স্ট্যাটাস", ""],
            "কোনো পণ্য নেই")
        + "</div>"
    )
    return page("Shop / দোকান", body, "shop")

def _onoff(v):
    """is_active — 0 মানে বন্ধ (`int(v or 1)` লিখলে 0-ও 1 হয়ে যেত)।"""
    if v is None or v == "":
        return 1
    try:
        return 0 if int(v) == 0 else 1
    except Exception:
        return 1



@wadm.route("/admin/shop/cat/<int:cid>", methods=["GET"])
def shop_cat_view(cid):
    """ক্যাটাগরির ভিতরে — কোন কোন মেয়াদ/প্যাক আছে, কত পণ্য, কত স্টক।"""
    g = guard()
    if g:
        return g
    _init()
    c = one("SELECT * FROM shop_categories WHERE id=%s", (cid,))
    if not c:
        return page("পাওয়া যায়নি", "<div class='card'>ক্যাটাগরি নেই</div>", "shop", status=404)
    prods = many(
        "SELECT p.*, (SELECT COUNT(*) FROM shop_stock s WHERE s.product_id=p.id "
        "AND COALESCE(s.sold,0)=0) AS stock FROM shop_products p "
        "WHERE p.category_id=%s ORDER BY p.sort_order, p.id", (cid,))
    total_stock = sum(int(p["stock"] or 0) for p in prods)

    plans = _plans(cid)
    head = (
        "<div class='card'>"
        f"<a class='btn gray sm' href='/admin/shop'>‹ Shop</a>"
        f"<h3 style='margin:8px 0 4px'>{esc(c['emoji'] or '🛍')} {esc(c['name'] or '')}</h3>"
        f"<div class='mut' style='font-size:12px'>প্যাক {num(len(plans))} · "
        f"পণ্য {num(len(prods))} · স্টক {num(total_stock)}</div></div>")

    # ➕ এই ক্যাটাগরিতে নতুন প্যাক — শুধু নাম
    add_form = (
        "<div class='card'><b>➕ নতুন প্যাক</b>"
        "<form method='post' action='/admin/shop/plan/add' class='row' "
        "style='gap:8px;margin-top:8px'>"
        f"<input type='hidden' name='category_id' value='{int(cid)}'>"
        "<input name='label' required placeholder='প্যাকের নাম (যেমন ১ মাস / ৭ দিন / ৫ GB)' "
        "style='min-width:240px'>"
        "<button class='ok sm' type='submit'>যোগ করুন</button></form>"
        "<div class='mut' style='font-size:12px;margin-top:6px'>"
        "ইউজার এই ক্যাটাগরিতে চাপলে আগে এই প্যাকগুলো দেখবে — প্যাক বেছে নিলে তার ভিতরের পণ্য।"
        "</div></div>")

    # প্রতিটি প্যাকের ভিতরের পণ্য
    blocks = []
    for pl in plans:
        rows = [_prod_row(p) for p in prods
                if int(p.get("plan_id") or 0) == int(pl["id"])]
        n = len(rows)
        st = sum(int(p.get("stock") or 0) for p in prods
                 if int(p.get("plan_id") or 0) == int(pl["id"]))
        blocks.append(
            "<div class='card'>"
            "<div class='row' style='justify-content:space-between;align-items:center'>"
            f"<b>📦 {esc(_plan_txt(pl))}</b>"
            f"<span class='mut' style='font-size:12px'>পণ্য {num(n)} · স্টক {num(st)}</span>"
            f"<a class='btn gray sm' href='/admin/shop/plan/{int(pl['id'])}/edit'>✏️ নাম বদলান</a>"
            f"<form method='post' action='/admin/shop/plan/{int(pl['id'])}/delete' "
            "style='display:inline' onsubmit=\"return confirm('প্যাকটা ডিলিট হবে? পণ্যগুলো "
            "প্যাক ছাড়া হয়ে থাকবে।')\">"
            "<button class='gray sm' type='submit'>🗑</button></form></div>"
            + table(["পণ্য", "দাম", "ডেলিভারি", "স্টক", "স্ট্যাটাস", ""], rows,
                    "এই প্যাকে এখনো কোনো পণ্য নেই")
            + "</div>")
    _loose = [p for p in prods if not int(p.get("plan_id") or 0)]
    if _loose or not plans:
        blocks.append(
            "<div class='card'><b>📦 প্যাক ছাড়া পণ্য</b>"
            + table(["পণ্য", "দাম", "ডেলিভারি", "স্টক", "স্ট্যাটাস", ""],
                    [_prod_row(p) for p in _loose], "কোনো পণ্য নেই")
            + "</div>")

    body = (head + add_form + "".join(blocks)
            + "<div class='card'><div class='row' style='gap:8px'>"
            + _btn("➕ নতুন পণ্য", "/admin/shop/new", "btn ok sm")
            + _btn("🛒 অর্ডার", f"/admin/shop/orders?c={int(cid)}", "btn gray sm")
            + "</div></div>")
    return page(f"Shop — {c['name'] or ''}", body, "shop")


def _prod_row(p):
    _t = str(p.get("deliver_type") or "auto").lower()
    typ = {"auto": "🤖 অটো", "manual": "✍️ ম্যানুয়াল",
           "hybrid": "⚡ হাইব্রিড"}.get(_t, "🤖 অটো")
    stock = int(p.get("stock") or 0)
    return [
        f"<a href='/admin/shop/{int(p['id'])}'><b>{esc(p.get('name') or '')}</b></a>",
        _fmt(p.get("price")),
        typ,
        badge(f"স্টক {stock}", "ok" if stock > 0 else "err"),
        badge("চালু", "ok") if _onoff(p.get("is_active")) else badge("বন্ধ", "mut"),
        _btn("📥 স্টক", f"/admin/shop/{int(p['id'])}/stock", "btn gray sm")
        + _btn("✏️", f"/admin/shop/{int(p['id'])}", "btn gray sm"),
    ]


@wadm.route("/admin/shop/currency", methods=["POST"])
def shop_currency():
    g = guard()
    if g:
        return g
    _init()
    cur = (request.form.get("currency") or "$").strip()[:8] or "$"
    _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
       "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", ("shop_currency", cur))
    log_admin("shop_currency", 0, f"currency={cur}")
    return redirect("/admin/shop?ok=মুদ্রা সেভ হয়েছে")


@wadm.route("/admin/shop/cat/<int:cid>/kind", methods=["POST"])
def shop_cat_kind(cid):
    g = guard()
    if g:
        return g
    _init()
    _pk = (request.form.get("plan_kind") or "").strip().lower()
    if _pk not in ("days", "mb"):
        _pk = ""
    _x("UPDATE shop_categories SET plan_kind=%s WHERE id=%s", (_pk, cid))
    log_admin("shop_cat_kind", cid, _pk or "none")
    return redirect("/admin/shop?ok=ক্যাটাগরির ধরন সেভ হয়েছে")


@wadm.route("/admin/shop/plan/add", methods=["POST"])
def shop_plan_add():
    """ক্যাটাগরির ভিতরে নতুন প্যাক — শুধু নাম দিলেই হবে (যেমন ১ মাস)।"""
    g = guard()
    if g:
        return g
    _init()
    try:
        cid = int(request.form.get("category_id") or 0)
    except Exception:
        cid = 0
    if not cid:
        return redirect("/admin/shop?err=" + _q("ক্যাটাগরি বেছে নিন"))
    if not one("SELECT id FROM shop_categories WHERE id=%s", (cid,)):
        return redirect("/admin/shop?err=" + _q("ক্যাটাগরি নেই"))
    label = (request.form.get("label") or "").strip()[:60]
    if not label:
        return redirect("/admin/shop?err=" + _q("প্যাকের নাম লিখুন (যেমন ১ মাস)"))
    nxt = (one("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM shop_plans "
               "WHERE category_id=%s", (cid,)) or {}).get("n") or 1
    _x("INSERT INTO shop_plans (category_id,label,kind,value,sort_order,is_active,created_at) "
       "VALUES (%s,%s,'days',0,%s,1,%s)", (cid, label, int(nxt), _now()))
    log_admin("shop_plan_add", cid, label)
    return redirect("/admin/shop?ok=" + _q(f"প্যাক «{label}» যোগ হয়েছে"))


@wadm.route("/admin/shop/plan/<int:plid>/edit", methods=["GET", "POST"])
def shop_plan_edit(plid):
    """প্যাকের নিজের নাম / মান / ধরন বদল — পুরোনো প্যাক ঠিক করার জন্যও।"""
    g = guard()
    if g:
        return g
    _init()
    pl = one("SELECT * FROM shop_plans WHERE id=%s", (plid,))
    if not pl:
        return page("পাওয়া যায়নি", "<div class='card'>প্যাক নেই</div>", "shop", status=404)
    cid = int(pl["category_id"] or 0)
    if request.method == "POST":
        lb = (request.form.get("label") or "").strip()[:60]
        kind = "mb" if (request.form.get("kind") or "").lower() == "mb" else "days"
        try:
            val = float((request.form.get("value") or "0").replace(",", "."))
        except Exception:
            val = 0.0
        _x("UPDATE shop_plans SET label=%s, kind=%s, value=%s WHERE id=%s",
           (lb, kind, val, plid))
        log_admin("shop_plan_edit", plid, f"{kind} {val} {lb}")
        return redirect(f"/admin/shop/cat/{cid}?ok=" + __import__("urllib.parse", fromlist=["quote"]).quote("✅ প্যাক সেভ হয়েছে"))
    _name = esc(str(pl.get("label") or ""))
    _val = esc(str(int(pl["value"]) if float(pl["value"] or 0) == int(float(pl["value"] or 0))
                   else pl["value"] or 0))
    _kind = str(pl.get("kind") or "days")
    body = (
        "<div class='card'><a class='btn gray sm' href='/admin/shop/cat/%d'>‹ ক্যাটাগরি</a>" % cid
        + "<h3 style='margin:8px 0'>✏️ প্যাক এডিট</h3>"
        "<form method='post'>"
        "<div class='field'><label>নিজের নাম (বটে ও প্যানেলে এটাই দেখাবে)</label>"
        f"<input name='label' value='{_name}' placeholder='যেমন ১ মাস / India Proxy 512MB'></div>"
        "<div class='field'><label>ধরন</label><select name='kind'>"
        f"<option value='mb'{' selected' if _kind == 'mb' else ''}>MB/GB (Proxy)</option>"
        f"<option value='days'{' selected' if _kind == 'days' else ''}>দিন (মেয়াদ)</option>"
        "</select></div>"
        "<div class='field'><label>মান (হিসাবের জন্য — 512 / 1024 / 3 / 30)</label>"
        f"<input name='value' value='{_val}' inputmode='decimal'></div>"
        "<div class='row' style='gap:8px;margin-top:12px'>"
        "<button class='ok' type='submit'>💾 সেভ করুন</button>"
        f"<a class='btn gray' href='/admin/shop/cat/{cid}'>বাতিল</a></div></form></div>")
    return page("প্যাক এডিট", body, "shop")


@wadm.route("/admin/shop/plan/<int:plid>/delete", methods=["POST"])
def shop_plan_del(plid):
    g = guard()
    if g:
        return g
    _init()
    try:
        _x("UPDATE shop_products SET plan_id=0 WHERE plan_id=%s", (plid,))
        _x("DELETE FROM shop_plans WHERE id=%s", (plid,))
    except Exception:
        pass
    log_admin("shop_plan_del", plid, "plan removed")
    return redirect("/admin/shop?ok=প্ল্যান ডিলিট হয়েছে")


@wadm.route("/admin/shop/cat/add", methods=["POST"])
def shop_cat_add():
    g = guard()
    if g:
        return g
    _init()
    name = (request.form.get("name") or "").strip()[:80]
    emoji = (request.form.get("emoji") or "🛍").strip()[:8] or "🛍"
    if not name:
        return redirect("/admin/shop?err=নাম দিন")
    # টেবিল লেজি-ক্রিয়েটেড — প্রথমবার যোগ করার সময় না থাকলে INSERT ফেল করত
    try:
        import bot as _b
        _b._shop_ddl()
    except Exception:
        pass
    _pk = (request.form.get("plan_kind") or "").strip().lower()
    if _pk not in ("days", "mb"):
        _pk = ""
    nxt = (one("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM shop_categories") or {}).get("n") or 1
    try:
        _x("INSERT INTO shop_categories (name, emoji, plan_kind, sort_order, is_active, created_at) "
           "VALUES (%s,%s,%s,%s,1,%s)", (name, emoji, _pk, int(nxt), _now()))
    except Exception as _ce:
        return redirect("/admin/shop?err=ক্যাটাগরি যোগ হয়নি: " + str(_ce)[:60])
    log_admin("shop_cat_add", 0, name)
    return redirect("/admin/shop?ok=ক্যাটাগরি যোগ হয়েছে")


@wadm.route("/admin/shop/cat/<int:cid>/toggle", methods=["POST"])
def shop_cat_toggle(cid):
    """ক্যাটাগরি চালু/বন্ধ — বন্ধ হলে বটের দোকানে দেখাবে না।"""
    g = guard()
    if g:
        return g
    c = one("SELECT is_active FROM shop_categories WHERE id=%s", (cid,))
    if not c:
        return redirect("/admin/shop?err=ক্যাটাগরি নেই")
    new = 0 if int(c["is_active"] or 0) else 1
    _x("UPDATE shop_categories SET is_active=%s WHERE id=%s", (new, cid))
    log_admin("shop_cat_toggle", cid, "on" if new else "off")
    return redirect("/admin/shop?ok=" + ("ক্যাটাগরি চালু হয়েছে" if new else "ক্যাটাগরি বন্ধ হয়েছে"))


@wadm.route("/admin/shop/cat/<int:cid>/delete", methods=["POST"])
def shop_cat_del(cid):
    g = guard()
    if g:
        return g
    _init()
    for p in many("SELECT id FROM shop_products WHERE COALESCE(category_id,0)=%s", (cid,)):
        _x("DELETE FROM shop_stock WHERE product_id=%s AND COALESCE(sold,0)=0", (int(p["id"]),))
        _x("DELETE FROM shop_products WHERE id=%s", (int(p["id"]),))
    _x("DELETE FROM shop_categories WHERE id=%s", (cid,))
    log_admin("shop_cat_del", cid, "category removed")
    return redirect("/admin/shop?ok=ক্যাটাগরি ডিলিট হয়েছে")


def _plans(cid=None):
    try:
        if cid is None:
            return many("SELECT * FROM shop_plans ORDER BY category_id, sort_order, id")
        return many("SELECT * FROM shop_plans WHERE category_id=%s ORDER BY sort_order, id", (int(cid),))
    except Exception:
        return []


def _plan_counts(pl, prods):
    """এই প্ল্যানে কত পণ্য ও কত স্টক আছে — (products, stock)।"""
    n = st = 0
    for p in prods or []:
        try:
            if int(p.get("plan_id") or 0) != int(pl["id"]):
                continue
        except Exception:
            continue
        n += 1
        st += int(p.get("stock") or 0)
    return n, st


def _group_details(rows, cat_idx, head_cols, empty_txt):
    """একই ক্যাটাগরির রো-গুলো আলাদা ভাঁজ করা (details) টেবিলে — লম্বা লিস্ট ছোট হয়।"""
    if not rows:
        return f"<div class='mut' style='font-size:12px'>{empty_txt}</div>"
    groups = {}
    for r in rows:
        groups.setdefault(str(r[cat_idx] or "—"), []).append(r)
    out = []
    for name, items in groups.items():
        out.append(
            "<details style='margin:6px 0;border:1px solid #2a3550;"
            "border-radius:10px;padding:8px 10px'>"
            f"<summary style='cursor:pointer;font-weight:600'>{name} "
            f"<span class='mut' style='font-size:12px'>· {len(items)}টি</span></summary>"
            + table(head_cols, items, "")
            + "</details>")
    return "".join(out)


def _user_preview(cats, prods):
    """ইউজার বটে কোনো ক্যাটাগরিতে চাপলে ঠিক কী দেখবে — অ্যাডমিন প্যানেলে প্রিভিউ।"""
    plans = _plans()
    out = ["<div style='margin-top:10px'>"]
    if not cats:
        out.append("<div class='mut' style='font-size:12px'>কোনো ক্যাটাগরি নেই।</div></div>")
        return "".join(out)
    for c in cats:
        cid = int(c["id"])
        _pk = str(c.get("plan_kind") or "").strip().lower()
        mine = [pl for pl in plans if int(pl["category_id"] or 0) == cid]
        prods_n = sum(1 for p in prods if int(p["category_id"] or 0) == cid)
        chips = " · ".join(f"[{esc(_plan_txt(pl, _pk))}]" for pl in mine) or "—"
        if mine:
            line = f"{num(prods_n)}টি পণ্য · {num(len(mine))}টি প্যাক"
        elif _pk in ("days", "mb"):
            line = f"{num(prods_n)}টি পণ্য · প্যাক যোগ করুন"
        else:
            line = f"{num(prods_n)}টি পণ্য"
        out.append(
            f"<div class='row' style='gap:6px;margin-top:6px;align-items:center'>"
            f"<a href='/admin/shop/cat/{cid}' style='text-decoration:none'>"
            f"{esc(c.get('emoji') or '🛍')} <b>{esc(c.get('name') or '')}</b></a>"
            f"<span class='mut' style='font-size:12px'>· {line}</span></div>")
    out.append("</div>")
    return "".join(out)


def _plans_grouped(prods):
    """ক্যাটাগরি → তার ভিতরের মেয়াদ/প্যাক — কত পণ্য, কত স্টক।"""
    cats = {int(c["id"]): c for c in _cats()}
    plans = _plans()
    if not plans:
        return "<div class='mut' style='font-size:12px;margin-top:8px'>এখনো কোনো মেয়াদ/প্যাক নেই।</div>"
    out = []
    for cid in dict.fromkeys([int(pl["category_id"]) for pl in plans]):
        c = cats.get(cid) or {}
        out.append(
            f"<div style='margin-top:10px'><a href='/admin/shop/cat/{cid}'>"
            f"<b>{esc(c.get('emoji') or '🛍')} {esc(c.get('name') or ('cat#' + str(cid)))}</b></a>"
            f" <span class='mut' style='font-size:12px'>— ভিতরে দেখুন</span></div>")
        for pl in [x for x in plans if int(x["category_id"]) == cid]:
            n, st = _plan_counts(pl, prods)
            out.append(
                "<div class='row' style='gap:6px;margin-top:6px;align-items:center'>"
                f"<a href='/admin/shop/cat/{cid}' style='text-decoration:none'>📦 {esc(_plan_txt(pl, c.get('plan_kind')))}</a>"
                f"<span class='mut' style='font-size:12px'>· পণ্য {num(n)} · স্টক {num(st)}</span>"
                f"<a class='btn gray sm' href='/admin/shop/plan/{int(pl['id'])}/edit'>✏️</a>"
                f"<form method='post' action='/admin/shop/plan/{int(pl['id'])}/delete' style='display:inline'>"
                f"<button class='gray sm' type='submit'>🗑</button></form></div>")
    return "".join(out)


def _plan_txt(pl, cat_kind=""):
    """প্ল্যানের নাম — নিজের দেওয়া নাম থাকলে সেটাই, নাহলে '৩ দিন' / '৫ GB'।"""
    try:
        v = float(pl["value"] or 0)
    except Exception:
        v = 0.0
    vs = int(v) if v == int(v) else round(v, 2)
    lb = str(pl["label"] or "").strip()
    if lb:
        return lb[:40]
    _k = str(cat_kind or "").strip().lower() or str(pl["kind"] or "days")
    if _k == "mb":
        return f"{vs}"          # Proxy-তে অটো MB/GB নয় — নাম দিতেই হবে
    return f"{vs} দিন"


def _plan_options(sel=0):
    """প্যাকের তালিকা — সাথে ক্যাটাগরির নাম, যাতে ভুল প্যাক না বাছেন।"""
    out = "<option value='0'>— প্যাক ছাড়া (ক্যাটাগরিতে সরাসরি দেখাবে) —</option>"
    _cm = {}
    try:
        for c in many("SELECT id, name, emoji FROM shop_categories"):
            _cm[int(c["id"])] = (str(c["emoji"] or "🛍") + " " + str(c["name"] or ""))
    except Exception:
        _cm = {}
    for pl in _plans():
        _cn = _cm.get(int(pl["category_id"] or 0), "")
        out += (f"<option value='{int(pl['id'])}'"
                f"{' selected' if int(sel or 0) == int(pl['id']) else ''}>"
                f"{esc(_plan_txt(pl))}" + (f" — {esc(_cn)}" if _cn else "") + "</option>")
    return out


# ───────────────────────── পণ্য ফর্ম ─────────────────────────
def _form(p=None):
    p = p or {}
    # 💵 ডলার↔টাকা রেট (Settings → QR রেট)
    try:
        _rs0 = one("SELECT value FROM settings WHERE key='qr_rate'")
        _rate_now = float(str((_rs0 or {}).get("value") or "125").replace(",", "."))
    except Exception:
        _rate_now = 125.0
    if _rate_now <= 0:
        _rate_now = 125.0
    name = esc(p.get("name") or "")
    desc = esc(p.get("description") or "")
    try:
        _pv = float(p.get("price") or 0)
    except Exception:
        _pv = 0.0
    price = esc(_usd_str(_pv, _rate_now) if _pv else "")
    # 💰 কেনা দাম (আমার খরচ) — লাভের হিসাবের জন্য; ইউজার দেখে না
    try:
        _cv = float(p.get("cost") or 0)
    except Exception:
        _cv = 0.0
    cost = esc(_usd_str(_cv, _rate_now) if _cv else "")
    typ = p.get("deliver_type") or "auto"
    act = int(p.get("is_active") or (1 if not p else 0))
    pid = int(p.get("id") or 0)
    stock = _stock_n(pid) if pid else 0
    # 🛍 পণ্যভিত্তিক রেফার কমিশন — খালি/০ মানে Settings-এর মান
    try:
        _rpv = float(p.get("ref_pct"))
        _rp = "" if _rpv <= 0 else f"{_rpv:g}"
    except Exception:
        _rp = ""
    try:
        _minq = max(1, int(float(p.get("min_qty") or 1)))
    except Exception:
        _minq = 1
    _ask = esc(str(p.get("ask_fields") or "").replace("|", chr(10)))
    return (
        "<div class='card'><form method='post'>"
        "<div class='field'><label>পণ্যের নাম</label>"
        f"<input name='name' value='{name}' placeholder='Outlook Trusted' required></div>"
        "<div class='field'><label>ক্যাটাগরি</label>"
        f"<select name='category_id'>{_cat_options(p.get('category_id') or 0)}</select></div>"
        "<div class='field'><label>📦 প্যাক (কোন প্যাকের ভিতরে থাকবে)</label>"
        f"<select name='plan_id'>{_plan_options(p.get('plan_id') or 0)}</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>প্যাক বানাবেন উপরে "
        "“➕ নতুন প্যাক” থেকে। প্যাক না বাছলে পণ্যটা ক্যাটাগরিতে সবার আগে দেখাবে।</p></div>"
        "<div class='field'><label>💵 এক পিসের দাম ($ ডলারে)</label>"
        f"<input name='price' value='{price}' placeholder='0.01' required inputmode='decimal'>"
        f"<p class='mut' style='font-size:11px;margin:6px 0 0'>ডলারে লিখুন (যেমন <b>0.01</b>)। "
        f"এখন 1$ = {_rate_now:g} ৳ — তাই বটে দেখাবে "
        f"<b>0.01$ · {_taka_str(0.01 * _rate_now)} ৳</b>। "
        "কেনার সময় এই টাকাই ইউজারের ডিপোজিট/Earn ব্যালেন্স থেকে কাটা হবে।</p></div>"
        "<div class='field'><label>💰 কেনা দাম ($ ডলারে) — আপনার খরচ</label>"
        f"<input name='cost' value='{cost}' placeholder='0.005' inputmode='decimal'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>এই পিসটা আপনার কত পড়ে "
        "(প্রোভাইডার/সাপ্লায়ারকে যা দেন)। <b>বিক্রি দাম − কেনা দাম = লাভ</b>, "
        "যেটা রাতের দৈনিক রিপোর্টে যোগ হবে। ইউজার কোথাও এটা দেখে না।</p></div>"
        "<div class='field'><label>🛍 রেফার কমিশন (%) — না দিলে Settings-এর %</label>"
        f"<input name='ref_pct' value='{_rp}' placeholder='খালি = Settings-এর %, 0 = কমিশন নেই'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>এই পণ্যের জন্য আলাদা % চাইলে "
        "লিখুন (যেমন 25)। খালি রাখলে Settings → শপ রেফার কমিশন % চলবে। "
        "<b>0</b> লিখলে এই পণ্যে রেফারার কিছুই পাবে না।</p></div>"

        "<div class='field'><label>ডেলিভারি ধরন</label>"
        f"<select name='deliver_type'>"
        f"<option value='auto'{' selected' if typ == 'auto' else ''}>🤖 অটো — txt/লাইন/Sheet স্টক থেকে বট দেবে</option>"
        f"<option value='hybrid'{' selected' if typ == 'hybrid' else ''}>⚡ হাইব্রিড — স্টক থাকলে সাথে সাথে, না থাকলে ম্যানুয়াল</option>"
        f"<option value='manual'{' selected' if typ == 'manual' else ''}>✍️ ম্যানুয়াল — আমি নিজে দেব</option>"
        f"</select></div>"
        "<div class='field'><label>🏷 ফিল্ড-নাম (প্রতি লাইনে একটি) — ইউজার বুঝবে কোনটা কী</label>"
        f"<textarea name='fields' rows='4' placeholder='Number\nUsername\nPassword'>{esc(str(p.get('fields') or '').replace('|', chr(10)))}</textarea>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>স্টকের প্রতিটি লাইনে যতগুলো অংশ "
        "(ট্যাব / | / কমা / স্পেস দিয়ে আলাদা), ঠিক ততগুলো নাম দিন। খালি রাখলে কপি করা লাইনই যাবে।</p></div>"
        "<div class='field'><label>🔢 মিনিমাম কত পিস কিনতে হবে</label>"
        f"<input name='min_qty' type='number' min='1' step='1' value='{_minq}' placeholder='1'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>ইউজার এর কম পিস কিনতে পারবে না। খালি/১ = কোনো বাধা নেই।</p></div>"
        "<div class='field'><label>❓ কেনার সময় ইউজারকে যা জিজ্ঞেস করবেন (প্রতি লাইনে একটি, সর্বোচ্চ ২টা)</label>"
        f"<textarea name='ask_fields' rows='2' placeholder='তোমার Gmail ঠিকানা দাও'>{_ask}</textarea>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>টাস্কের মতো — ইউজার উত্তর না দিলে কেনা সম্পন্ন হবে না। "
        "খালি রাখলে কিছু জিজ্ঞেস করা হবে না।</p></div>"
        "<div class='field'><label>বর্ণনা (ঐচ্ছিক)</label>"
        f"<textarea name='description' rows='3' placeholder='যেমন: ১০০% Fresh, Instant Delivery'>{desc}</textarea></div>"
        f"<label class='chk'><input type='checkbox' name='is_active' value='1'"
        f"{' checked' if act else ''}> বটে দেখাবে (চালু)</label>"
        "<div class='row' style='gap:8px;margin-top:12px'>"
        "<button class='ok' type='submit'>💾 সেভ করুন</button>"
        + (f"<a class='btn gray' href='/admin/shop/{pid}/stock'>📥 স্টক ({stock})</a>" if pid else "")
        + "<a class='btn gray' href='/admin/shop'>বাতিল</a></div>"
        "</form></div>"
    )


def _save(pid=0):
    name = (request.form.get("name") or "").strip()[:120]
    if not name:
        return 0
    # 💵 অ্যাডমিন ডলারে লেখেন — ভিতরে সব হিসাব ৳-তে (পেমেন্ট/হোল্ড/রিফান্ড)
    try:
        _price_usd = float((request.form.get("price") or "0").replace(",", ".").strip() or 0)
    except Exception:
        _price_usd = 0.0
    _rate = _shop_rate()
    price = round(_price_usd * _rate, 4)
    # 💰 কেনা দাম (USD ইনপুট → ৳) — লাভের হিসাব
    try:
        _cost_usd = float((request.form.get("cost") or "0").replace(",", ".").strip() or 0)
    except Exception:
        _cost_usd = 0.0
    cost = round(max(_cost_usd, 0.0) * _rate, 4)
    cid = request.form.get("category_id") or 0
    try:
        cid = int(cid)
    except Exception:
        cid = 0
    typ = str(request.form.get("deliver_type") or "auto").strip().lower()
    if typ not in ("auto", "manual", "hybrid"):
        typ = "auto"
    desc = (request.form.get("description") or "").strip()[:2000]
    active = 1 if request.form.get("is_active") else 0
    # 🛍 প্রতি পণ্যের কমিশন: খালি = Settings-এর %, 0 = এই পণ্যে কমিশন নেই
    try:
        _rp_v = (request.form.get("ref_pct") or "").strip().replace("%", "")
        ref_pct = float(_rp_v) if _rp_v else -1.0
        if ref_pct < 0:
            ref_pct = -1.0
        ref_pct = min(90.0, ref_pct)
    except Exception:
        ref_pct = -1.0
    _flds = [x.strip()[:24] for x in str(request.form.get("fields") or "").replace(",", "\n")
             .replace("|", "\n").replace(";", "\n").splitlines() if x.strip()][:8]
    fields = "|".join(_flds)
    # 🔢 মিনিমাম পিস + ❓ কেনার সময়ের প্রশ্ন (সর্বোচ্চ ২টা)
    try:
        min_qty = max(1, int(float(str(request.form.get("min_qty") or "1").strip() or 1)))
    except Exception:
        min_qty = 1
    _asks = [x.strip()[:90] for x in str(request.form.get("ask_fields") or "")
             .replace("|", "\n").replace(";", "\n").splitlines() if x.strip()][:2]
    ask_fields = "|".join(_asks)
    try:
        plan_id = int(request.form.get("plan_id") or 0)
    except Exception:
        plan_id = 0
    if pid:
        _x("UPDATE shop_products SET category_id=%s, name=%s, description=%s, price=%s, "
           "cost=%s, deliver_type=%s, ref_pct=%s, fields=%s, plan_id=%s, is_active=%s, "
           "min_qty=%s, ask_fields=%s WHERE id=%s",
           (cid, name, desc, price, cost, typ, ref_pct, fields, plan_id, active,
            min_qty, ask_fields, pid))
        return pid
    nxt = (one("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM shop_products") or {}).get("n") or 1
    r = one("INSERT INTO shop_products (category_id, name, description, price, cost, "
            "deliver_type, ref_pct, fields, plan_id, is_active, min_qty, ask_fields, "
            "sort_order, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid, name, desc, price, cost, typ, ref_pct, fields, plan_id, active,
             min_qty, ask_fields, int(nxt), _now()))
    return int((r or {}).get("id") or 0)


@wadm.route("/admin/shop/new", methods=["GET", "POST"])
def shop_new():
    g = guard()
    if g:
        return g
    _init()
    if request.method == "POST":
        pid = _save()
        if not pid:
            return page("নতুন পণ্য", _form(), "shop", err="পণ্যের নাম দিন")
        log_admin("shop_product_add", pid, request.form.get("name") or "")
        return redirect(f"/admin/shop/{pid}?ok=পণ্য তৈরি হয়েছে")
    return page("নতুন পণ্য", _form(), "shop")


@wadm.route("/admin/shop/<int:pid>", methods=["GET", "POST"])
def shop_edit(pid):
    g = guard()
    if g:
        return g
    _init()
    p = one("SELECT * FROM shop_products WHERE id=%s", (pid,))
    if not p:
        return page("পাওয়া যায়নি", "<div class='card'>পণ্য নেই</div>", "shop", status=404)
    if request.method == "POST":
        _save(pid)
        log_admin("shop_product_edit", pid, request.form.get("name") or "")
        return redirect(f"/admin/shop/{pid}?ok=সেভ হয়েছে")
    stock = _stock_n(pid)
    sold = one("SELECT COUNT(*) AS n FROM shop_stock WHERE product_id=%s AND COALESCE(sold,0)=1",
               (pid,)) or {}
    orders = one("SELECT COUNT(*) AS n FROM shop_orders WHERE product_id=%s", (pid,)) or {}
    err = request.args.get("err") or ""
    return page(
        "পণ্য এডিট",
        "<div class='grid g-kpi'>"
        + _kpi("স্টক বাকি", num(stock))
        + _kpi("বিক্রি হয়েছে", num((sold or {}).get("n") or 0))
        + _kpi("মোট অর্ডার", num((orders or {}).get("n") or 0))
        + "</div>"
        + _form(p)
        + "<div class='card'><b>⚙️ অ্যাকশন</b><div class='row' style='gap:8px;margin-top:8px'>"
          f"<form method='post' action='/admin/shop/{pid}/toggle'><button class='btn gray sm'>🔁 চালু/বন্ধ</button></form>"
          f"<form method='post' action='/admin/shop/{pid}/delete' "
          f"onsubmit=\"return confirm('পণ্য ও এর স্টক ডিলিট হবে?')\">"
          f"<button class='gray sm' type='submit'>🗑 পণ্য ডিলিট</button></form>"
          f"<a class='btn gray sm' href='/admin/shop/{pid}/stock'>📥 স্টক ম্যানেজ</a>"
          f"<a class='btn gray sm' href='/admin/shop/orders?p={pid}'>🧾 এই পণ্যের অর্ডার</a>"
        "</div></div>",
        "shop", err=err or None)


@wadm.route("/admin/shop/<int:pid>/toggle", methods=["POST"])
def shop_toggle(pid):
    g = guard()
    if g:
        return g
    _init()
    r = one("SELECT is_active FROM shop_products WHERE id=%s", (pid,))
    if r:
        _x("UPDATE shop_products SET is_active=%s WHERE id=%s",
           (0 if int(r["is_active"] or 0) else 1, pid))
    return redirect(f"/admin/shop/{pid}?ok=স্ট্যাটাস বদলেছে")


@wadm.route("/admin/shop/<int:pid>/delete", methods=["POST"])
def shop_delete(pid):
    g = guard()
    if g:
        return g
    _init()
    _x("DELETE FROM shop_stock WHERE product_id=%s AND COALESCE(sold,0)=0", (pid,))
    _x("DELETE FROM shop_products WHERE id=%s", (pid,))
    log_admin("shop_product_del", pid, "product removed")
    return redirect("/admin/shop?ok=পণ্য ডিলিট হয়েছে")


# ───────────────────────── স্টক ─────────────────────────
@wadm.route("/admin/shop/<int:pid>/stock", methods=["GET", "POST"])
def shop_stock(pid):
    g = guard()
    if g:
        return g
    _init()
    p = one("SELECT * FROM shop_products WHERE id=%s", (pid,))
    if not p:
        return page("পাওয়া যায়নি", "<div class='card'>পণ্য নেই</div>", "shop", status=404)
    msg = None
    if request.method == "POST":
        text = request.form.get("items") or ""
        sheet = (request.form.get("sheet") or "").strip()
        f = request.files.get("txtfile")
        if f and f.filename:
            try:
                raw = f.read()
                text = (text or "") + "\n" + raw.decode("utf-8", "ignore")
            except Exception as e:
                msg = f"ফাইল পড়া যায়নি: {e}"
        added = dup = 0
        if sheet:
            try:
                added, dup = _import_sheet(pid, sheet)
            except Exception as e:
                msg = (f"লিংক পড়া যায়নি: {e} — Google Sheet হলে "
                       "Share → Anyone with the link → Viewer করুন")
        elif text.strip():
            added, dup = _add_stock(pid, text)
        if added or dup:
            log_admin("shop_stock_add", pid, f"{added} items ({dup} dup skipped)")
            tail = f" · {dup} ডুপ্লিকেট বাদ" if dup else ""
            if not added:
                return redirect(f"/admin/shop/{pid}/stock?err=সব লাইনই আগে থেকে আছে — নতুন কিছু যোগ হয়নি")
            return redirect(f"/admin/shop/{pid}/stock?ok={added} টা স্টক যোগ হয়েছে{tail}")
        msg = msg or "কিছু পাওয়া যায়নি — লাইন লিখুন, .txt দিন বা Sheet লিংক দিন"
    unsold = many("SELECT * FROM shop_stock WHERE product_id=%s AND COALESCE(sold,0)=0 "
                  "ORDER BY id LIMIT 300", (pid,))
    sold = many("SELECT * FROM shop_stock WHERE product_id=%s AND COALESCE(sold,0)=1 "
                "ORDER BY id DESC LIMIT 60", (pid,))
    stock = _stock_n(pid)
    rows = []
    for s in unsold:
        rows.append([
            f"<span class='mono'>#{int(s['id'])}</span>",
            f"<span class='mono' style='word-break:break-all'>{esc(str(s['content'] or '')[:160])}</span>",
            esc(str(s["added_at"] or "")[:16]),
            f"<form method='post' action='/admin/shop/{pid}/stock/{int(s['id'])}/delete' "
            f"style='display:inline'><button class='gray sm' type='submit'>🗑</button></form>",
        ])
    sold_rows = []
    for s in sold:
        sold_rows.append([
            f"<span class='mono'>#{int(s['id'])}</span>",
            f"<span class='mono' style='word-break:break-all'>{esc(str(s['content'] or '')[:120])}</span>",
            f"<a class='mono' href='/admin/user/{int(s['sold_to'] or 0)}'>{int(s['sold_to'] or 0)}</a>",
            esc(str(s["sold_at"] or "")[:16]),
        ])
    body = (
        "<div class='grid g-kpi'>"
        + _kpi("পণ্য", esc(p["name"] or ""))
        + _kpi("স্টক বাকি", num(stock), "" if stock else "warn")
        + _kpi("দাম", esc(_fmt(p["price"])))
        + "</div>"
        + "<div class='card'><b>📥 স্টক যোগ করুন</b>"
          "<form method='post' enctype='multipart/form-data'>"
          "<div class='field'><label>প্রতি লাইনে ১টা আইটেম (একাউন্ট:পাসওয়ার্ড / কী ইত্যাদি)</label>"
          "<textarea name='items' rows='7' placeholder='user1@outlook.com:pass1&#10;user2@outlook.com:pass2'></textarea></div>"
          "<div class='field'><label>অথবা .txt ফাইল</label>"
          "<input type='file' name='txtfile' accept='.txt,text/plain'></div>"
          "<div class='field'><label>অথবা Google Sheet / CSV লিংক</label>"
          "<input name='sheet' placeholder='https://docs.google.com/spreadsheets/d/.../edit#gid=0'>"
          "<div class='mut' style='font-size:12px'>Sheet: Share → Anyone with the link → Viewer। "
          "প্রতি সারির প্রথম ঘর থেকেই আইটেম নেওয়া হবে।</div></div>"
          "<button class='ok' type='submit'>➕ যোগ করুন</button>"
          "<span class='mut' style='font-size:12px;margin-left:10px'>"
          "এক ইউজার একটা পাবে, ওই লাইন আর কারও কাছে যাবে না। "
          "ডুপ্লিকেট লাইন অটো বাদ যায়।</span>"
          "</form></div>"
        + "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
          "<b>📦 স্টক বাকি (" + num(stock) + ")</b>"
          f"<form method='post' action='/admin/shop/{pid}/stock/clear' "
          f"onsubmit=\"return confirm('সব অবিক্রীত স্টক মুছবে?')\">"
          f"<button class='gray sm' type='submit'>🧹 সব খালি করুন</button></form></div>"
        + table(["#", "আইটেম", "যোগ হয়েছে", ""], rows, "স্টক নেই — উপরে যোগ করুন")
        + "</div>"
        + "<div class='card'><b>✅ বিক্রি হয়েছে (সর্বশেষ ৬০)</b>"
        + table(["#", "আইটেম", "ইউজার", "সময়"], sold_rows, "এখনো কিছু বিক্রি হয়নি")
        + "</div>"
        + "<div class='row' style='gap:8px;margin-top:10px'>"
        f"<a class='btn gray' href='/admin/shop/{pid}'>🔙 পণ্যে ফিরুন</a>"
        "<a class='btn gray' href='/admin/shop'>🛒 Shop</a></div>"
    )
    return page("স্টক — " + (p["name"] or ""), body, "shop",
                toast=request.args.get("ok") or None, err=msg)


@wadm.route("/admin/shop/<int:pid>/stock/<int:sid>/delete", methods=["POST"])
def shop_stock_del(pid, sid):
    g = guard()
    if g:
        return g
    _init()
    _x("DELETE FROM shop_stock WHERE id=%s AND product_id=%s AND COALESCE(sold,0)=0", (sid, pid))
    return redirect(f"/admin/shop/{pid}/stock?ok=আইটেম মুছে ফেলা হয়েছে")


@wadm.route("/admin/shop/<int:pid>/stock/clear", methods=["POST"])
def shop_stock_clear(pid):
    g = guard()
    if g:
        return g
    _init()
    _x("DELETE FROM shop_stock WHERE product_id=%s AND COALESCE(sold,0)=0", (pid,))
    log_admin("shop_stock_clear", pid, "unsold stock cleared")
    return redirect(f"/admin/shop/{pid}/stock?ok=স্টক খালি করা হয়েছে")


# ───────────────────────── অর্ডার ─────────────────────────
@wadm.route("/admin/shop/orders", methods=["GET"])
def shop_orders():
    g = guard()
    if g:
        return g
    _init()
    st = (request.args.get("st") or "").strip()
    pid = request.args.get("p") or ""
    sql = "SELECT * FROM shop_orders WHERE 1=1"
    args = []
    if st:
        sql += " AND status=%s"
        args.append(st)
    if pid.isdigit():
        sql += " AND product_id=%s"
        args.append(int(pid))
    sql += " ORDER BY id DESC LIMIT 300"
    rows_db = many(sql, tuple(args))
    rows = []
    for r in rows_db:
        stat = str(r["status"] or "")
        kind = "ok" if stat == "delivered" else ("warn" if stat == "pending" else "mut")
        rows.append([
            f"<span class='mono'>#{int(r['id'])}</span>",
            esc(str(r["product_name"] or "")[:44]),
            f"<a class='mono' href='/admin/user/{int(r['user_id'] or 0)}'>{int(r['user_id'] or 0)}</a>",
            esc(_fmt(r["price"])),
            badge(stat, kind),
            esc(str(r["created_at"] or "")[:16]),
            _btn("খুলুন", f"/admin/shop/order/{int(r['id'])}", "btn gray sm"),
        ])
    def _tab(lbl, q, active):
        return _btn(lbl, "/admin/shop/orders" + q,
                    "btn " + ("ok sm" if active else "gray sm"))

    tabs = ("<div class='row' style='gap:8px;margin-bottom:10px'>"
            + _tab("সব", "", not st)
            + _tab("⏳ পেন্ডিং", "?st=pending", st == "pending")
            + _tab("✅ ডেলিভারি", "?st=delivered", st == "delivered")
            + _tab("💸 রিফান্ড", "?st=refunded", st == "refunded")
            + "</div>")
    body = (tabs + "<div class='card'>"
            + table(["#", "পণ্য", "ইউজার", "দাম", "স্ট্যাটাস", "সময়", ""], rows,
                    "কোনো অর্ডার নেই")
            + "</div><a class='btn gray' href='/admin/shop'>🔙 Shop</a>")
    return page("Shop অর্ডার", body, "shop")


@wadm.route("/admin/shop/order/<int:oid>", methods=["GET", "POST"])
def shop_order_view(oid):
    g = guard()
    if g:
        return g
    _init()
    o = one("SELECT * FROM shop_orders WHERE id=%s", (oid,))
    if not o:
        return page("পাওয়া যায়নি", "<div class='card'>অর্ডার নেই</div>", "shop", status=404)
    if request.method == "POST":
        act = (request.form.get("act") or "").strip()
        if str(o["status"]) != "pending":
            return redirect(f"/admin/shop/order/{oid}?err=এই অর্ডার আগেই প্রসেস হয়েছে")
        if act == "deliver":
            content = (request.form.get("content") or "").strip()
            if not content:
                return redirect(f"/admin/shop/order/{oid}?err=কনটেন্ট লিখুন")
            _x("UPDATE shop_orders SET status='delivered', content=%s, delivered_at=%s WHERE id=%s",
               (content[:3900], _now(), oid))
            # 🔒 Hold থাকলে এখনই স্থায়ীভাবে কাটা (একবারই — লেজার protected)
            try:
                import bot as _bc
                _ha = float(o["hold_amt"] or 0) if "hold_amt" in o.keys() else 0.0
                if _ha > 0:
                    _bc.shop_hold_consume(int(o["user_id"]), _ha, int(oid))
            except Exception:
                pass
            queue_user_msg(int(o["user_id"]),
                           f"✅ আপনার অর্ডার #{oid} ডেলিভারি হয়েছে\n\n"
                           f"🛍 {o['product_name']}\n\n{content}\n\n"
                           "⚠️ এই তথ্য কারও সাথে শেয়ার করবেন না।")
            log_admin("shop_deliver", oid, f"uid={o['user_id']}")
            return redirect(f"/admin/shop/order/{oid}?ok=ডেলিভারি পাঠানো হয়েছে (বট ১ মিনিটে পৌঁছে দেবে)")
        if act == "refund":
            # 🔒 Hold থাকলে সেটাই ৳ ওয়ালেটে ফেরত; পুরোনো (Hold-বিহীন) অর্ডার হলে আগের নিয়মে Earn-এ
            _ha2 = float(o["hold_amt"] or 0) if "hold_amt" in o.keys() else 0.0
            _done = False
            if _ha2 > 0:
                try:
                    import bot as _bc2
                    _done = bool(_bc2.shop_hold_release(int(o["user_id"]), _ha2, int(oid)))
                except Exception:
                    _done = False
            if not _done:
                _x("UPDATE users SET balance=balance+%s WHERE user_id=%s",
                   (float(o["price"] or 0), int(o["user_id"])))
            try:
                import bot as _bcache
                _bcache._invalidate_user_cache(int(o["user_id"]))   # ক্যাশে রিফ্রেশ
            except Exception:
                pass
            _x("UPDATE shop_orders SET status='refunded' WHERE id=%s", (oid,))
            queue_user_msg(int(o["user_id"]),
                           f"💸 আপনার অর্ডার #{oid} বাতিল — {_fmt(o['price'])} ফেরত দেওয়া হয়েছে।")
            log_admin("shop_refund", oid, f"uid={o['user_id']}")
            return redirect(f"/admin/shop/order/{oid}?ok=রিফান্ড হয়েছে")
    stat = str(o["status"] or "")
    kind = "ok" if stat == "delivered" else ("warn" if stat == "pending" else "mut")
    body = (
        "<div class='card'>"
        f"<div class='row' style='justify-content:space-between'><b>🧾 অর্ডার #{int(o['id'])}</b>"
        f"{badge(stat, kind)}</div>"
        "<div class='tbl-wrap' style='margin-top:8px'><table><tbody>"
        f"<tr><th>পণ্য</th><td>{esc(o['product_name'] or '')}</td></tr>"
        f"<tr><th>দাম</th><td>{esc(_fmt(o['price']))}</td></tr>"
        f"<tr><th>ইউজার</th><td><a class='mono' href='/admin/user/{int(o['user_id'] or 0)}'>"
        f"{int(o['user_id'] or 0)}</a></td></tr>"
        f"<tr><th>সময়</th><td>{esc(str(o['created_at'] or ''))}</td></tr>"
        f"<tr><th>ডেলিভারি</th><td>{esc(str(o['delivered_at'] or '—'))}</td></tr>"
        "</tbody></table></div></div>"
        + (f"<div class='card'><b>📦 ডেলিভারি কনটেন্ট</b>"
           f"<pre class='mono' style='white-space:pre-wrap;word-break:break-all'>"
           f"{esc(o['content'] or '')}</pre></div>" if (o["content"] or "") else "")
        + ("<div class='card'><b>✍️ ম্যানুয়াল ডেলিভারি</b>"
           "<form method='post'><input type='hidden' name='act' value='deliver'>"
           "<div class='field'><label>ইউজার এই টেক্সটটা পাবে</label>"
           "<textarea name='content' rows='5' placeholder='user@outlook.com:password'></textarea></div>"
           "<button class='ok' type='submit'>✅ ডেলিভারি পাঠান</button></form>"
           "<form method='post' style='margin-top:8px'>"
           "<input type='hidden' name='act' value='refund'>"
           "<button class='gray' type='submit' "
           "onsubmit=\"return confirm('টাকা ফেরত দেবেন?')\">💸 রিফান্ড করুন</button></form></div>"
           if stat == "pending" else "")
        + "<a class='btn gray' href='/admin/shop/orders'>🔙 অর্ডার তালিকা</a>"
    )
    return page(f"অর্ডার #{oid}", body, "shop",
                toast=request.args.get("ok") or None, err=request.args.get("err") or None)


# ═════════════════ 🌐 SMM প্যানেল (bdsmmpanel API v2) ═════════════════
@wadm.route("/admin/smm", methods=["GET", "POST"])
def admin_smm():
    """SMM প্যানেল: API Key, USD→৳ রেট, লাভের %, চালু/বন্ধ + প্যানেল ব্যালেন্স।"""
    g = guard()
    if g:
        return g
    _init()
    if request.method == "POST":
        form = request.form
        vals = {
            "smm_url": (form.get("smm_url") or "https://bdsmmpanel.com/api/v2").strip()[:300],
            "smm_key": (form.get("smm_key") or "").strip()[:200],
            "smm_usd_bdt": (form.get("smm_usd_bdt") or "130").strip()[:12],
            "smm_tiers": (form.get("smm_tiers") or "").strip()[:4000],
            "smm_cur": ("usd" if (form.get("smm_cur") or "auto").strip().lower() == "usd"
                        else ("bdt" if (form.get("smm_cur") or "").strip().lower() == "bdt"
                              else "auto")),
            "smm_min_bal": (form.get("smm_min_bal") or "5").strip()[:12],
            "smm_on": "on" if (form.get("smm_on") or "") in ("on", "1", "yes") else "off",
        }
        try:
            float(vals["smm_usd_bdt"])
        except Exception:
            vals["smm_usd_bdt"] = "130"
        try:                       # ধাপ-তালিকা পরিষ্কার করে সেভ (ভুল লাইন বাদ পড়ে)
            import bot as _bt
            _tiers = _bt.smm_tiers_parse(vals["smm_tiers"])
            vals["smm_tiers"] = _bt.smm_tiers_str(_tiers) if _tiers else _bt.SMM_TIERS_DEF
        except Exception:
            vals["smm_tiers"] = ""
        try:
            import bot as _bs
            for k, v in vals.items():
                _bs.set_setting(k, v)
        except Exception:
            for k, v in vals.items():
                _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
                   "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, v))
        log_admin("smm_save", 0, "saved")
        return redirect("/admin/smm?ok=" + _q("🌐 SMM সেটিংস সেভ হয়েছে"))

    try:
        import bot as _bs2
        cfg = _bs2.smm_cfg()
    except Exception:
        cfg = {"url": "https://bdsmmpanel.com/api/v2", "key": "", "usd": 130,
               "markup": 20, "cur": "bdt", "on": False}
    bal_txt, bal_err, n_svc = "—", "", 0
    _bal_cur = ""
    _last_bal, _last_at = "—", "—"
    try:
        _r_lb = one("SELECT value FROM settings WHERE key='smm_bal_last'") or {}
        _r_la = one("SELECT value FROM settings WHERE key='smm_bal_at'") or {}
        _r_lc = one("SELECT value FROM settings WHERE key='smm_bal_cur'") or {}
        if str(_r_lb.get("value") or "").strip():
            _last_bal = str(_r_lb.get("value")) + " " + str(_r_lc.get("value") or "")
        _last_at = str(_r_la.get("value") or "—")
    except Exception:
        pass
    _minb = 5.0
    try:
        import bot as _bs5
        _minb = _bs5._smm_min_bal()
    except Exception:
        pass
    try:
        if request.args.get("chk") and cfg.get("key"):
            import bot as _bs6
            _res = _bs6.smm_check_balance(None)
            if _res:
                bal_txt = _bs6.smm_bal_text(_res.get("balance"), _res.get("currency"))
                _last_bal, _last_at = bal_txt, __import__("time").strftime("%Y-%m-%d %H:%M")
    except Exception as _ce:
        bal_err = str(_ce)[:150]
    if cfg.get("key"):
        try:
            import bot as _bs3
            _b, _cur = _bs3.smm_panel_balance()
            _bal_cur = str(_cur or "")
            try:
                bal_txt = _bs3.smm_bal_text(_b, _cur)
            except Exception:
                bal_txt = f"{_b} {_cur}"
        except Exception as e:
            bal_err = str(e)[:160]
        try:
            import bot as _bs4
            n_svc = len(_bs4.smm_services())
        except Exception:
            n_svc = 0
    _on = bool(cfg.get("on"))
    _tiers_txt = ""
    try:
        import bot as _bt2
        _tiers_txt = "\n".join(f"{lo:g}-{hi:g} = {pct:g}"
                               for lo, hi, pct in _bt2.smm_tiers())
    except Exception:
        _tiers_txt = "1-3 = 100\n4-6 = 50\n7-10 = 40\n10-50 = 30\n50-80 = 40\n80-100 = 30\n100-120 = 25\n120-150 = 20"
    body = (
        "<div class='card'><h3>" + icon("globe") + " 🌐 SMM প্যানেল</h3>"
        "<div class='mut' style='font-size:12px;margin-bottom:10px'>"
        "bdsmmpanel-এর API v2 দিয়ে ইউজার বট থেকেই সার্ভিস কিনবে। "
        "দাম = প্যানেলের rate × পরিমাণ, তারপর অর্ডারের অঙ্ক অনুযায়ী নিচের তালিকা থেকে "
        "লাভের % যোগ হয় (যেমন ২৳ অর্ডারে ১০০% → দ্বিগুণ দাম)।</div>"
        + ("<div class='note ok'>✅ SMM চালু — ইউজার দোকানে '🌐 SMM প্যানেল' বাটন দেখছে।</div>"
           if _on else
           "<div class='note warn'>⏸ SMM বন্ধ — বাটন ইউজার দেখছে না।</div>")
        + "<div class='tbl-wrap' style='margin:10px 0'><table><tbody>"
        + f"<tr><th>প্যানেলে আপনার ব্যালেন্স</th><td><b>{esc(_last_bal)}</b></td></tr>"
        + f"<tr><th>সর্বশেষ চেক</th><td>{esc(_last_at)}</td></tr>"
        + f"<tr><th>কম-ব্যালেন্স সীমা</th><td>{esc(str(_minb))} (নিচে নামলে অ্যাডমিনকে অ্যালার্ট)</td></tr>"
        + f"<tr><th>সার্ভিস সংখ্যা</th><td>{num(n_svc)}</td></tr>"
        + "</tbody></table></div>"
        + "<a class='btn gray sm' href='/admin/smm?chk=1'>🔄 এখনই ব্যালেন্স চেক করুন</a>"
        + (("<div class='note warn'>ℹ️ প্যানেল ব্যালেন্স ডলারে দেখাচ্ছে। "
            "আপনার ওয়েবসাইটে ৳ দেখায় — তাই নিচের <b>প্যানেলের দাম কোন মুদ্রায়</b> "
            "অংশে <b>ডলার ($)</b> বেছে নিন, আর USD→৳ রেট (এখন "
            f"{esc(str(cfg.get('usd')))}৳) মিলিয়ে নিন; নাহলে দাম অনেক কম দেখাবে।</div>")
           if _bal_cur.upper() == "USD" and (cfg.get("cur_eff") or "") != "usd" else "")
        + (f"<div class='note warn'>⚠️ {esc(bal_err)}</div>" if bal_err else "")
        + "</div>"
        "<div class='card'><h3>⚙️ সেটিংস</h3>"
        "<form method='post'>"
        "<div class='field'><label>API URL</label>"
        f"<input name='smm_url' value='{esc(cfg.get('url') or '')}'></div>"
        "<div class='field'><label>API Key <span class='mut'>(প্যানেলের Account পেজ থেকে)</span></label>"
        f"<input name='smm_key' value='{esc(cfg.get('key') or '')}' placeholder='আপনার API key'></div>"
        "<div class='field'><label>প্যানেলের দাম কোন মুদ্রায়?</label>"
        "<select name='smm_cur'>"
        + ("<option value='auto'" + (" selected" if (cfg.get("cur") or "auto") not in ("usd", "bdt") else "")
           + ">🔄 অটো — প্যানেল যা বলে (সবচেয়ে নিরাপদ)</option>")
        + ("<option value='bdt'" + (" selected" if (cfg.get("cur") or "auto") == "bdt" else "")
           + ">৳ টাকা (রেট আগেই ৳-তে)</option>")
        + ("<option value='usd'" + (" selected" if (cfg.get("cur") or "auto") == "usd" else "")
           + ">ডলার ($) — USD→৳ রেট দিয়ে গুণ হবে</option>")
        + "</select></div>"
        "<div class='field'><label>USD → ৳ রেট <span class='mut'>— শুধু প্যানেলের দাম "
        "ডলারে হলে: এই রেট দিয়ে টাকায় গুণ হবে। প্যানেলের দাম আগেই টাকায় হলে "
        "(🔄 অটো/৳) এই ঘর কাজে লাগে না। ডিপোজিটের রেট আলাদা "
        "(Settings → ডিপোজিট)।</span></label>"
        f"<input name='smm_usd_bdt' value='{esc(str(cfg.get('usd')))}' placeholder='130'></div>"
        "<div class='field'><label>ধাপ অনুযায়ী লাভের % "
        "<span class='mut'>(প্রতি ১০০০-এর দাম কোন ধাপে পড়লে কত % লাভ — "
        "প্রতি লাইনে একটা, যেমন <code>1-3 = 100</code>)। একই সার্ভিসে ১০০-এর "
        "দাম ১০০০-এর ঠিক ১/১০ হবে।</span></label>"
        f"<textarea name='smm_tiers' rows='9' style='width:100%' "
        f"placeholder='1-3 = 100&#10;4-6 = 50'>{esc(_tiers_txt)}</textarea></div>"
        + "<div class='field'><label>কম-ব্যালেন্স সীমা <span class='mut'>(এর নিচে নামলে টেলিগ্রামে অ্যালার্ট)</span></label>"
        + f"<input name='smm_min_bal' value='{esc(str(_minb))}' placeholder='5'></div>"
        "<div class='field'><label><input type='checkbox' name='smm_on' value='on'"
        + (" checked" if _on else "") + "> SMM প্যানেল চালু</label></div>"
        "<button class='ok' type='submit'>💾 সেভ করুন</button></form></div>"
    )
    return page("SMM প্যানেল", body, "smm",
                toast=request.args.get("ok") or None, err=request.args.get("err") or None)


# ═══════ 🎁 OTP Earn — নাম্বার স্টক (.txt) · সার্ভিস · রেট · withdraw ═══════
def _otp_s(k, d=""):
    try:
        import bot as _b
        return str(_b.get_setting(k) or d)
    except Exception:
        return d


@wadm.route("/admin/otp", methods=["GET"])
def otp_earn_page():
    """নাম্বার আপলোড + সার্ভিস/রেট + OTP withdraw কন্ট্রোল — এক পেজ।"""
    g = guard()
    if g:
        return g
    try:
        import bot as _b
    except Exception as e:
        return page("OTP Earn", f"<div class='card'>bot লোড হয়নি: {esc(str(e)[:180])}</div>", "otp")
    _earn_on = _otp_s("otp_earn_on", "on") != "off"
    _wd_on = _otp_s("otp_wd_on", "off") == "on"
    _cap = _otp_s("otp_wd_cap", "0")
    _pool = _otp_s("otp_wd_pool", "0")
    _mn = _otp_s("otp_wd_min", "0.05")
    _total, _stats = _b.otp_stock_stats()
    _tot = _b.otp_admin_totals()
    # 💵 আমার লাভ — প্রতিটা OTP-তে (আমার payout − ইউজারকে দেওয়া); শুধু অ্যাডমিন
    try:
        _profit_tot, _profit_rows = _b.otp_profit_stats(30)
    except Exception:
        _profit_tot, _profit_rows = 0.0, []
    _prof_rows = []
    for _d0, _u0, _m0, _p0 in _profit_rows:
        _prof_rows.append([
            esc(_d0 or "—"), esc(f"${_u0:.4f}"), esc(f"${_m0:.4f}"),
            badge(("+" if _p0 >= 0 else "") + f"${_p0:.4f}",
                  "ok" if _p0 >= 0 else "err"),
        ])
    _pend = _b.otp_wd_list("pending", 100)
    _done = _b.otp_wd_list("all", 15)
    _svcs = _b.otp_services()

    _svc_opts = "".join(f"<option value='{esc(s)}'>" for s in _svcs)
    _svc_opts += "".join(f"<option value='{esc(s)}'>" for s in
                         ("facebook", "whatsapp", "telegram", "instagram",
                          "google", "tiktok", "twitter", "discord"))

    # 🌍 স্টক — দেশ + সার্ভিস ধরে; প্রতিটা গ্রুপের পাশে 🗑 (ডাটাবেজ থেকেও মুছে যায়)
    _stat_rows = []
    for s in _b.otp_stock_groups(80):
        _iso_g = str(s.get("iso") or "")
        _cty_g = str(s.get("country_raw", s.get("country")) or "")
        _svc_g = str(s.get("service") or "")
        _stat_rows.append([
            f"{s['flag']} {esc(str(s.get('country') or 'অজানা'))}"
            f" <span class='mono mut'>({num(s['n'])})</span>",
            esc(_svc_g or "—"),
            esc(f"${s['rate']:.4f}"),
            "<form method='post' action='/admin/otp/stock/del' style='display:inline' "
            "onsubmit=\"return confirm('এই গ্রুপের সব ফ্রি নাম্বার স্টক ও ডাটাবেজ থেকে ডিলিট হবে — ঠিক আছে?')\">"
            f"<input type='hidden' name='iso' value='{esc(_iso_g)}'>"
            f"<input type='hidden' name='country' value='{esc(_cty_g)}'>"
            f"<input type='hidden' name='service' value='{esc(_svc_g)}'>"
            "<button class='err sm' type='submit'>🗑 ডিলিট</button></form>",
        ])
    _pend_rows = []
    for w in _pend:
        _pend_rows.append([
            f"#{int(w['id'])}",
            f"<a class='mono' href='/admin/user/{int(w['user_id'] or 0)}'>"
            f"{int(w['user_id'] or 0)}</a>",
            f"<b>${float(w['amount'] or 0):.4f}</b>",
            f"<span class='mono' style='word-break:break-all'>"
            f"{esc(str(w['address'] or '')[:60])}</span>",
            badge("⏳ পেমেন্ট চলছে" if str(w["status"]) == "paying" else "পেন্ডিং",
                  "warn"),
            esc(str(w["requested_at"] or "")[:16]),
            (f"<form method='post' action='/admin/otp/wd/{int(w['id'])}/approve' "
             f"style='display:inline'><button class='ok sm' type='submit'>✅ Approve</button></form> "
             f"<form method='post' action='/admin/otp/wd/{int(w['id'])}/reject' "
             f"style='display:inline'><button class='err sm' type='submit'>❌ Reject</button></form>"),
        ])
    _done_rows = []
    for w in _done:
        _st = str(w["status"] or "")
        _done_rows.append([
            f"#{int(w['id'])}",
            f"<a class='mono' href='/admin/user/{int(w['user_id'] or 0)}'>"
            f"{int(w['user_id'] or 0)}</a>",
            f"${float(w['amount'] or 0):.4f}",
            badge(_st, "ok" if _st == "approved" else "err"),
            esc(str(w["decided_at"] or "—")[:16]),
        ])

    body = (
        "<div class='grid g-kpi'>"
        + _kpi("ফ্রি নাম্বার", num(_total), "" if _total else "warn")
        + _kpi("দেশ", num(len(_stats)))
        + _kpi("OTP আয় (মোট)", esc(f"${_tot['earned']:.4f}"))
        + _kpi("পেন্ডিং withdraw", esc(f"${_tot['pending_usd']:.4f}")
               + f" <span class='mut'>({num(_tot['pending_n'])})</span>",
               "warn" if _tot["pending_n"] else "")
        + _kpi("আমার লাভ", esc(f"${_profit_tot:.4f}"),
               "" if _profit_tot >= 0 else "warn")
        + "</div>"

        # ── 💵 আমার লাভ (শুধু অ্যাডমিন) ──
        + "<div class='card'><b>💵 আমার লাভ</b>"
          "<div class='mut' style='font-size:12px;margin:4px 0 10px'>"
          "প্রতিটা OTP-তে <b>আমার payout − ইউজারকে দেওয়া</b> = লাভ। "
          "এই রেট, payout আর লাভের হিসাব <b>কোনো ইউজার দেখতে পায় না</b> — "
          "শুধু এই প্যানেলে আপনি দেখেন।</div>"
          f"<div class='note {'ok' if _profit_tot >= 0 else 'warn'}'>"
          f"মোট লাভ: <b>${_profit_tot:.4f}</b></div>"
        + table(["তারিখ", "ইউজার পেয়েছে", "আমি পেয়েছি", "লাভ"], _prof_rows,
                "এখনো কোনো OTP আসেনি")
        + "</div>"

        # ── সেটিংস ──
        + "<div class='card'><b>⚙️ OTP Earn সেটিংস</b>"
          "<div class='mut' style='font-size:12px;margin:4px 0 10px'>"
          "ইউজারের OTP আয় আলাদা ওয়ালেটে জমা হয় (মেইন balance-এ যায় না)। "
          "Withdraw বন্ধ থাকলে টাকা লক — চালু করলেই ইউজার তুলতে পারবে।</div>"
          "<form method='post' action='/admin/otp/save'>"
          "<div class='field'><label>OTP ইনকাম</label><select name='otp_earn_on'>"
          + ("<option value='on' selected>চালু</option><option value='off'>বন্ধ</option>"
             if _earn_on else
             "<option value='on'>চালু</option><option value='off' selected>বন্ধ</option>") +
          "</select></div>"
          "<div class='field'><label>OTP Withdraw <span class='mut'>— চালু করলে বট নিজেই "
          "অটো USDT-BEP20 পেমেন্ট করবে; বন্ধ = টাকা লক</span></label>"
          "<select name='otp_wd_on'>"
          + ("<option value='on' selected>চালু (টাকা তোলা যাবে)</option>"
             "<option value='off'>বন্ধ (লকড)</option>"
             if _wd_on else
             "<option value='on'>চালু (টাকা তোলা যাবে)</option>"
             "<option value='off' selected>বন্ধ (লকড)</option>") +
          "</select></div>"
          "<div class='field'><label>প্রতি ইউজার সর্বোচ্চ OTP withdraw (USD)"
          " <span class='mut'>— 0 = লিমিট নেই। যেমন 1 দিলে একজন ইউজার মোট "
          "সর্বোচ্চ $1 তুলতে পারবে</span></label>"
          f"<input name='otp_wd_cap' value='{esc(str(_cap))}' placeholder='0'></div>"
          "<div class='field'><label>সব ইউজার মিলিয়ে সর্বোচ্চ OTP withdraw (USD)"
          " <span class='mut'>— 0 = লিমিট নেই। যেমন 1 দিলে ১ জন হোক বা ১০ জন, "
          "মোট $1-এর বেশি কেউ তুলতে পারবে না</span></label>"
          f"<input name='otp_wd_pool' value='{esc(str(_pool))}' placeholder='0'></div>"
          "<div class='field'><label>মিনি withdraw — সর্বনিম্ন OTP withdraw (USD)</label>"
          f"<input name='otp_wd_min' value='{esc(str(_mn))}' placeholder='0.05'></div>"
          "<div class='field'><label>নরমাল (Zenex) নাম্বারে প্রতি OTP-তে আমি পাই (৳)"
          " <span class='mut'>— লাভের হিসাবে এটাই আমার আয় ধরা হবে</span></label>"
          f"<input name='getnum_earn' value='{esc(_otp_s('getnum_earn', '0.65'))}' "
          "placeholder='0.65'></div>"
          "<button class='ok' type='submit'>💾 সেভ করুন</button></form></div>"

        # ── আপলোড ──
        + "<div class='card'><b>📥 নাম্বার আপলোড (.txt)</b>"
          "<div class='mut' style='font-size:12px;margin:4px 0 10px'>"
          "ফাইলের প্রতি লাইনে: <span class='mono'>দেশকোড:নাম্বার:দেশ</span> — "
          "যেমন <span class='mono'>93:703231343:AF</span>। "
          "<b>দেশ ফাইল থেকেই বসে যায়</b> — আলাদা করে লিখতে হয় না, "
          "ইউজার নাম্বার + দেশ কোড দুটোই পাবে। "
          "<b>যে ডলার লিখবেন ইউজার প্রতি OTP-তে ঠিক ততই পাবে</b>; "
          "আলাদা নাম্বারে আলাদা দিতে চাইলে ওই লাইনে "
          "<span class='mono'>নাম্বার|ডলার|সার্ভিস</span> ফরম্যাটও চলে।</div>"
          "<form method='post' action='/admin/otp/upload' enctype='multipart/form-data'>"
          "<div class='field'><label>সার্ভিস (কিসের নাম্বার) "
          "<span class='mut'>— নিজের মতো যেকোনো নাম লিখতে পারবেন "
          "(facebook / whatsapp / telegram / অন্য কিছু)</span></label>"
          "<input name='service' value='' placeholder='facebook' autocomplete='off'>"
          "</div>"
          "<div class='field'><label>প্রতি OTP-তে ইউজার পাবে (USD $)</label>"
          "<input name='rate' type='number' step='0.0001' min='0' value='0.01'></div>"
          "<div class='field'><label>প্রতি OTP-তে আমি পাব (USD $)"
          " <span class='mut'>— শুধু আপনার লাভের হিসাব, ইউজার দেখে না</span></label>"
          "<input name='my_cost' type='number' step='0.0001' min='0' value='0' "
          "placeholder='0'></div>"
          "<div class='field'><label>.txt ফাইল বেছে নিন "
          "<span class='mut'>— প্রতি লাইনে দেশকোড:নাম্বার:দেশ</span></label>"
          "<input type='file' name='txtfile' accept='.txt,text/plain' required></div>"
          "<button class='ok' type='submit'>➕ আপলোড করুন</button>"
          "<span class='mut' style='font-size:12px;margin-left:10px'>"
          "একই নাম্বার দুইবার যোগ হবে না।</span></form></div>"

        # ── স্টক (দেশ + সার্ভিস, প্রতিটার পাশে ডিলিট) ──
        + "<div class='card'><b>🌍 স্টক — দেশ অনুযায়ী</b>"
          "<div class='mut' style='font-size:12px;margin:4px 0 10px'>"
          "ফ্রি নাম্বার এই টেবিলেই দেখা যায়। 🗑 ডিলিট চাপলে ওই দেশ+সার্ভিসের সব ফ্রি "
          "নাম্বার স্টক ও ডাটাবেজ — দুটো থেকেই মুছে যাবে।</div>"
        + "<div style='display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start'>"
          "<div style='flex:1 1 460px;min-width:280px'>"
        + table(["দেশ", "সার্ভিস", "গড় রেট", ""], _stat_rows,
                "এখনো কোনো নাম্বার আপলোড হয়নি")
        + "</div>"
        # ── ➕ সরাসরি স্টক এড — টেবিলের পাশেই (ডিলিট বাটনের লাইনে) ──
        + "<div style='flex:1 1 300px;min-width:260px;padding-left:12px;"
          "border-left:1px solid #ddd'>"
          "<b>➕ স্টক এড করুন</b>"
          "<div class='mut' style='font-size:12px;margin:4px 0 8px'>"
          "দেশ + সার্ভিস + রেট দিয়ে নিচে নাম্বার বসান (প্রতি লাইনে একটা) — "
          "সাথে সাথে স্টকে যোগ হবে, ফাইল আপলোড লাগবে না।</div>"
          "<form method='post' action='/admin/otp/add'>"
          "<div class='field'><label>দেশ (নাম বা ISO — যেমন Sri Lanka / LK)</label>"
          "<input name='country' placeholder='Sri Lanka' required></div>"
          "<div class='field'><label>সার্ভিস</label>"
          "<input name='service' placeholder='facebook'></div>"
          "<div class='field'><label>প্রতি OTP-তে ইউজার পাবে (USD $)</label>"
          "<input name='rate' type='number' step='0.0001' min='0' value='0.01'></div>"
          "<div class='field'><label>প্রতি OTP-তে আমি পাব (USD $)"
          " <span class='mut'>— শুধু লাভের হিসাব</span></label>"
          "<input name='my_cost' type='number' step='0.0001' min='0' value='0'></div>"
          "<div class='field'><label>নাম্বার (প্রতি লাইনে একটা)</label>"
          "<textarea name='numbers' rows='5' placeholder='94771234567' required "
          "oninput=\"document.getElementById('otp_cnt').textContent = 'কয়টা নাম্বার: ' "
          "+ this.value.split(String.fromCharCode(10))"
          ".filter(function(x){return x.trim();}).length\">"
          "</textarea>"
          "<div class='mut mono' style='font-size:12px' id='otp_cnt'>"
          "কয়টা নাম্বার: 0</div></div>"
          "<button class='ok' type='submit'>➕ স্টক এড করুন</button></form></div>"
        + "</div>"
        + "</div>"
        # ── withdraw ──
        + "<div class='card'><b>💸 OTP Withdraw রিকোয়েস্ট (পেন্ডিং)</b>"
        + table(["ID", "ইউজার", "পরিমাণ", "Wallet", "স্টেটাস", "সময়", "অ্যাকশন"],
                _pend_rows, "কোনো পেন্ডিং রিকোয়েস্ট নেই")
        + "</div>"
        + "<div class='card'><b>📜 সর্বশেষ সিদ্ধান্ত</b>"
        + table(["ID", "ইউজার", "পরিমাণ", "স্টেটাস", "সময়"],
                _done_rows, "এখনো কিছু নেই")
        + "</div>"
    )
    return page("🎁 OTP Earn", body, "otp",
                toast=request.args.get("ok") or None,
                err=request.args.get("err") or None)


@wadm.route("/admin/otp/save", methods=["POST"])
def otp_earn_save():
    g = guard()
    if g:
        return g
    form = request.form
    def _fnum(k, d, lo=0.0):
        try:
            return str(max(float(str(form.get(k) or d).replace(",", ".")), lo))
        except Exception:
            return str(d)
    vals = {
        "otp_earn_on": "off" if (form.get("otp_earn_on") or "on") == "off" else "on",
        "otp_wd_on": "on" if (form.get("otp_wd_on") or "") == "on" else "off",
        "otp_wd_cap": _fnum("otp_wd_cap", 0),
        "otp_wd_pool": _fnum("otp_wd_pool", 0),
        "otp_wd_min": _fnum("otp_wd_min", 0.05),
        # 💵 নরমাল (Zenex) নাম্বারে প্রতি OTP-তে আমার আয় — লাভের হিসাব
        "getnum_earn": _fnum("getnum_earn", 0.65),
    }
    try:
        import bot as _b
        for k, v in vals.items():
            _b.set_setting(k, v)
    except Exception:
        for k, v in vals.items():
            _x("INSERT INTO settings (key,value) VALUES (%s,%s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k, v))
    log_admin("otp_settings", 0, "saved")
    return redirect("/admin/otp?ok=" + _q("🎁 OTP Earn সেটিংস সেভ হয়েছে"))


@wadm.route("/admin/otp/upload", methods=["POST"])
def otp_earn_upload():
    g = guard()
    if g:
        return g
    try:
        import bot as _b
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(f"bot লোড হয়নি: {e}"))
    form = request.form
    text = form.get("numbers") or ""
    fname = ""
    f = request.files.get("txtfile")
    if f and f.filename:
        fname = str(f.filename)[:80]
        try:
            text = (text or "") + "\n" + f.read().decode("utf-8", "ignore")
        except Exception as e:
            return redirect("/admin/otp?err=" + _q(f"ফাইল পড়া যায়নি: {e}"))
    service = (form.get("service") or "").strip()[:40]
    try:
        rate = float(str(form.get("rate") or 0).replace(",", "."))
    except Exception:
        rate = 0.0
    if not (text or "").strip():
        return redirect("/admin/otp?err=" + _q("আগে .txt ফাইল বেছে নিন (দেশকোড:নাম্বার:দেশ)"))
    # 💵 আমার payout — লাভের হিসাব (ইউজার দেখে না); লাইনে আলাদা রেট দিলে সেটাই প্রাধান্য পায়
    try:
        _myc = max(float(str(form.get("my_cost") or 0).replace(",", ".")), 0.0)
    except Exception:
        _myc = 0.0
    try:
        added, dup, bad = _b.otp_numbers_add(text, service, rate, fname or "upload",
                                             flat_cost=_myc)
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(f"যোগ হয়নি: {e}"))
    if not added:
        return redirect("/admin/otp?err=" + _q(
            "সব লাইনই আগে থেকে আছে বা ভুল ফরম্যাট"))
    log_admin("otp_upload", 0, f"{added} numbers ({dup} dup, {bad} bad) service={service or '-'} rate={rate}")
    _tail = f" · {dup} ডুপ্লিকেট বাদ" if dup else ""
    if bad:
        _tail += f" · {bad} লাইন বাদ"
    return redirect("/admin/otp?ok=" + _q(f"✅ {added} টা নাম্বার যোগ হয়েছে{_tail}"))


@wadm.route("/admin/otp/add", methods=["POST"])
def otp_earn_stock_add():
    """➕ স্টক টেবিল থেকে সরাসরি নাম্বার এড (দেশ + সার্ভিস + রেট)।"""
    g = guard()
    if g:
        return g
    try:
        import bot as _b
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(f"bot লোড হয়নি: {e}"))
    form = request.form
    _cty = (form.get("country") or "").strip()[:80]
    _svc = (form.get("service") or "").strip()[:40]
    try:
        _rate = round(max(float(str(form.get("rate") or 0).replace(",", ".")), 0.0), 4)
    except Exception:
        _rate = 0.0
    try:
        _myc = max(float(str(form.get("my_cost") or 0).replace(",", ".")), 0.0)
    except Exception:
        _myc = 0.0
    _text = (form.get("numbers") or "").strip()
    if not _text:
        return redirect("/admin/otp?err=" + _q("নাম্বার লিখুন (প্রতি লাইনে একটা)"))
    if not _cty:
        return redirect("/admin/otp?err=" + _q("দেশ লিখুন"))
    # দেশের নাম/ISO → ওই দেশের নাম+ISO সব নাম্বারে বসবে
    _iso, _nm = "", ""
    _up = _cty.upper()
    if 2 <= len(_up) <= 3 and _up.isalpha():
        try:
            _nm0, _cc0 = _b.otp_iso_name(_up)
        except Exception:
            _nm0, _cc0 = "", ""
        if _nm0:
            _iso, _nm = _up[:3], _nm0
        else:
            _nm = _cty
    else:
        _nm = _cty
    _meta = {}
    for _ln in _text.replace("\r", "").split("\n"):
        try:
            _n = _b.otp_norm_number(_ln.strip())
        except Exception:
            _n = ""
        if _n:
            _meta[_n] = (_svc, _nm, _iso)
    try:
        _a, _d, _bad = _b.otp_numbers_add(_text, _svc, _rate, "manual",
                                          meta=_meta, flat_cost=_myc)
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(f"যোগ হয়নি: {str(e)[:140]}"))
    if not _a:
        return redirect("/admin/otp?err=" + _q("সব নাম্বারই আগে থেকে আছে বা ভুল"))
    log_admin("otp_stock_add", 0, f"+{_a} numbers {_cty} svc={_svc or '-'} rate={_rate}")
    _tail = f" · {_d} ডুপ্লিকেট" if _d else ""
    if _bad:
        _tail += f" · {_bad} বাদ"
    return redirect("/admin/otp?ok=" + _q(f"✅ {_a}টি নাম্বার স্টকে যোগ হয়েছে{_tail}"))


@wadm.route("/admin/otp/sync", methods=["POST"])
def otp_earn_sync_lamix():
    """☎️ Lamix প্যানেল থেকে নাম্বার টেনে স্টকে যোগ (হাতে আপলোড লাগে না)।"""
    g = guard()
    if g:
        return g
    try:
        import bot as _b
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(f"bot লোড হয়নি: {e}"))
    try:
        _def_rate = float(str(request.form.get("rate") or "0.01").replace(",", "."))
    except Exception:
        _def_rate = 0.01
    _def_rate = round(max(0.0, _def_rate), 4)
    # 💵 রেঞ্জভেদে আলাদা রেট ($) — "নাম | ডলার" লাইন থেকে (কোনো % নেই)
    # `রেঞ্জ | আমি পাব | ইউজার পাবে` (দুই ঘর হলে শুধু ইউজার রেট)
    _rmap, _cmap = {}, {}
    for _ln in (request.form.get("rate_lines") or "").replace("\r", "").split("\n"):
        _ln = _ln.strip()
        if not _ln or "|" not in _ln:
            continue
        _fld = [x.strip() for x in _ln.split("|")]
        if len(_fld) >= 3:
            _nm, _my, _pc = _fld[0], _fld[1], _fld[2]
        else:
            _nm, _my, _pc = _fld[0], "", _fld[1]
        _nm = _nm.strip().lower()
        if not _nm:
            continue
        for _val, _mp in ((_pc, _rmap), (_my, _cmap)):
            try:
                _pv = float(str(_val).strip().replace(",", "."))
            except Exception:
                continue
            _mp[_nm] = round(max(0.0, _pv), 4)
    try:
        import json as _json2
        _b.set_setting("otp_lamix_rate", str(_def_rate))
        _b.set_setting("otp_lamix_rates", _json2.dumps(_rmap, ensure_ascii=False))
        _b.set_setting("otp_lamix_costs", _json2.dumps(_cmap, ensure_ascii=False))
    except Exception:
        pass
    try:
        _a, _d, _bad, _err = _b.otp_sync_lamix(rate=_def_rate, rates=_rmap,
                                              costs=_cmap)
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(f"সিঙ্ক ব্যর্থ: {str(e)[:140]}"))
    if _err:
        return redirect("/admin/otp?err=" + _q(f"Lamix: {_err}"))
    log_admin("otp_sync_lamix", 0, f"+{_a} numbers rate=${_def_rate}")
    return redirect("/admin/otp?ok=" + _q(
        f"☎️ Lamix থেকে {_a}টি নাম্বার যোগ · {_d} ডুপ্লিকেট · {_bad} বাদ"))


@wadm.route("/admin/otp/stock/del", methods=["POST"])
def otp_earn_stock_delete():
    """🌍 স্টক টেবিলের 🗑 — ওই দেশ+সার্ভিসের সব ফ্রি নাম্বার ডাটাবেজ থেকেই মুছে যায়।"""
    g = guard()
    if g:
        return g
    form = request.form
    _iso = (form.get("iso") or "").strip()[:3]
    _cty = (form.get("country") or "").strip()[:80]
    _svc = (form.get("service") or "").strip()[:40]
    if not (_iso or _cty):
        return redirect("/admin/otp?err=" + _q("দেশ/সার্ভিস পাওয়া যায়নি"))
    try:
        import bot as _b
        _n = _b.otp_stock_purge(iso=_iso, country=_cty, service=_svc)
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(str(e)[:150]))
    log_admin("otp_stock_del", 0, f"iso={_iso} country={_cty} svc={_svc} removed={_n}")
    return redirect("/admin/otp?ok=" + _q(f"🗑 {_n} টা ফ্রি নাম্বার ডিলিট হয়েছে"))


@wadm.route("/admin/otp/num/<int:nid>/delete", methods=["POST"])
def otp_earn_num_delete(nid):
    g = guard()
    if g:
        return g
    try:
        import bot as _b
        _b.otp_number_delete(nid)
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(str(e)[:150]))
    log_admin("otp_num_del", nid, "number removed")
    return redirect("/admin/otp?ok=" + _q("নাম্বার ডিলিট হয়েছে"))


@wadm.route("/admin/otp/wd/<int:wid>/approve", methods=["POST"])
def otp_earn_wd_approve(wid):
    return _otp_wd_decide_web(wid, True)


@wadm.route("/admin/otp/wd/<int:wid>/reject", methods=["POST"])
def otp_earn_wd_reject(wid):
    return _otp_wd_decide_web(wid, False)


def _otp_wd_decide_web(wid, ok):
    g = guard()
    if g:
        return g
    try:
        import bot as _b
        row = _b.otp_wd_decide(wid, ok, "web admin")
    except Exception as e:
        return redirect("/admin/otp?err=" + _q(str(e)[:150]))
    if not row:
        return redirect("/admin/otp?err=" + _q("রিকোয়েস্ট পাওয়া যায়নি / আগেই সিদ্ধান্ত হয়েছে"))
    _amt = float(row.get("amount") or 0)
    _u = int(row.get("user_id") or 0)
    try:
        queue_user_msg(
            _u,
            (f"✅ আপনার OTP withdraw `${_amt:.4f}` অ্যাপ্রুভ হয়েছে। পেমেন্ট শিগগিরই পাবেন।"
             if ok else
             f"❌ আপনার OTP withdraw `${_amt:.4f}` রিজেক্ট হয়েছে — টাকা OTP ব্যালেন্সে ফেরত দেওয়া হয়েছে।"))
    except Exception:
        pass
    log_admin("otp_wd_%s" % ("approve" if ok else "reject"), wid, f"uid={_u} ${_amt:.4f}")
    return redirect("/admin/otp?ok=" + _q(
        ("✅ Approved" if ok else "❌ Rejected ও টাকা ফেরত") + f" — #{wid}"))
