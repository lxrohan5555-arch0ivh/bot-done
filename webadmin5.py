"""Shop / দোকান — অ্যাডমিন ওয়েব প্যানেল।

- /admin/shop                : ক্যাটাগরি + পণ্য + স্টক ওভারভিউ
- /admin/shop/new            : নতুন পণ্য
- /admin/shop/<id>           : পণ্য এডিট / ডিলিট
- /admin/shop/<id>/stock     : .txt আপলোড / লাইন যোগ / স্টক তালিকা
- /admin/shop/orders         : অর্ডার তালিকা (ফিল্টার)
- /admin/shop/order/<id>     : ম্যানুয়াল ডেলিভারি / রিফান্ড
"""
import time

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
        "ALTER TABLE shop_orders ADD COLUMN IF NOT EXISTS delivered_at TEXT DEFAULT ''",
    ):
        try:
            _x(m)
        except Exception:
            pass
    _inited = True


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _cur():
    r = one("SELECT value FROM settings WHERE key=%s", ("shop_currency",))
    v = ((r or {}).get("value") or "").strip()
    return v or "$"


def _fmt(v):
    """ছোট দামও পুরো দেখাবে (0.0001 → `0.0001`, 0 নয়)।"""
    try:
        n = float(v or 0)
    except Exception:
        n = 0.0
    if n == int(n):
        s = str(int(n))
    else:
        s = f"{n:.8f}".rstrip("0").rstrip(".")
    return f"{s or '0'} {_cur()}"


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
    pend = one("SELECT COUNT(*) AS n FROM shop_orders WHERE status='pending'") or {}
    sold = one("SELECT COUNT(*) AS n FROM shop_orders WHERE status='delivered'") or {}
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

    # ক্যাটাগরি তৈরি + ক্রেন্সি
    cur = esc(_cur())
    tools = (
        "<div class='card'><b>➕ নতুন ক্যাটাগরি</b>"
        "<form method='post' action='/admin/shop/cat/add' class='row' style='gap:8px;margin-top:8px'>"
        "<input name='emoji' placeholder='🛍' style='max-width:70px'>"
        "<input name='name' placeholder='ক্যাটাগরির নাম (যেমন VPN)' required>"
        "<button class='ok sm' type='submit'>যোগ করুন</button></form>"
        "<div class='mut' style='font-size:12px;margin-top:6px'>ইউজার বটে এই ক্যাটাগরিগুলো বাটন হিসেবে দেখবে।</div>"
        "</div>"
        "<div class='card'><b>💱 মুদ্রা</b>"
        "<form method='post' action='/admin/shop/currency' class='row' style='gap:8px;margin-top:8px'>"
        f"<input name='currency' value='{cur}' placeholder='$' style='max-width:120px'>"
        "<button class='btn sm' type='submit'>সেভ</button>"
        "<span class='mut' style='font-size:12px'>বটে দাম এভাবেই দেখাবে (যেমন 0.78 $)</span>"
        "</form></div>"
    )

    cat_rows = []
    for c in cats:
        n = sum(1 for p in prods if int(p["category_id"] or 0) == int(c["id"]))
        cat_rows.append([
            f"{esc(c['emoji'] or '🛍')} <b>{esc(c['name'] or '')}</b>",
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

    body = (
        head + tools
        + "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
          "<b>📁 ক্যাটাগরি</b>" + _btn("🛒 অর্ডার দেখুন", "/admin/shop/orders", "btn gray sm")
        + "</div>" + table(["ক্যাটাগরি", "পণ্য", "স্ট্যাটাস", "অন/অফ", ""], cat_rows,
                           "কোনো ক্যাটাগরি নেই — উপরে যোগ করুন")
        + "</div>"
        + "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
          "<b>📦 পণ্য</b>" + _btn("➕ নতুন পণ্য", "/admin/shop/new", "btn ok sm")
        + "</div>" + table(["পণ্য", "ক্যাটাগরি", "দাম", "ডেলিভারি", "স্টক", "স্ট্যাটাস", ""],
                           prod_rows, "কোনো পণ্য নেই")
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
    nxt = (one("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM shop_categories") or {}).get("n") or 1
    _x("INSERT INTO shop_categories (name, emoji, sort_order, is_active, created_at) "
       "VALUES (%s,%s,%s,1,%s)", (name, emoji, int(nxt), _now()))
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


# ───────────────────────── পণ্য ফর্ম ─────────────────────────
def _form(p=None):
    p = p or {}
    name = esc(p.get("name") or "")
    desc = esc(p.get("description") or "")
    price = esc(str(p.get("price") or ""))
    typ = p.get("deliver_type") or "auto"
    act = int(p.get("is_active") or (1 if not p else 0))
    pid = int(p.get("id") or 0)
    stock = _stock_n(pid) if pid else 0
    return (
        "<div class='card'><form method='post'>"
        "<div class='field'><label>পণ্যের নাম</label>"
        f"<input name='name' value='{name}' placeholder='Outlook Trusted' required></div>"
        "<div class='field'><label>ক্যাটাগরি</label>"
        f"<select name='category_id'>{_cat_options(p.get('category_id') or 0)}</select></div>"
        "<div class='field'><label>দাম (ব্যালেন্স থেকে কাটা হবে)</label>"
        f"<input name='price' value='{price}' placeholder='0.78' required></div>"
        "<div class='field'><label>ডেলিভারি ধরন</label>"
        f"<select name='deliver_type'>"
        f"<option value='auto'{' selected' if typ == 'auto' else ''}>🤖 অটো — txt/লাইন/Sheet স্টক থেকে বট দেবে</option>"
        f"<option value='hybrid'{' selected' if typ == 'hybrid' else ''}>⚡ হাইব্রিড — স্টক থাকলে সাথে সাথে, না থাকলে ম্যানুয়াল</option>"
        f"<option value='manual'{' selected' if typ == 'manual' else ''}>✍️ ম্যানুয়াল — আমি নিজে দেব</option>"
        f"</select></div>"
        "<div class='field'><label>💸 রেফার কমিশন (%)</label>"
        f"<input name='ref_pct' value='{esc(str(p.get('ref_pct')) if p.get('ref_pct') not in (None, '') else '')}' "
        "placeholder='খালি = ডিফল্ট, 0 = বন্ধ, 10 = ১০%'></div>"
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
    try:
        price = float((request.form.get("price") or "0").replace(",", "."))
    except Exception:
        price = 0.0
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
    _rp_raw = (request.form.get("ref_pct") or "").strip().replace("%", "")
    try:
        ref_pct = float(_rp_raw.replace(",", ".")) if _rp_raw else -1.0
    except Exception:
        ref_pct = -1.0
    ref_pct = -1.0 if ref_pct < 0 else min(90.0, ref_pct)
    if pid:
        _x("UPDATE shop_products SET category_id=%s, name=%s, description=%s, price=%s, "
           "deliver_type=%s, ref_pct=%s, is_active=%s WHERE id=%s",
           (cid, name, desc, price, typ, ref_pct, active, pid))
        return pid
    nxt = (one("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM shop_products") or {}).get("n") or 1
    r = one("INSERT INTO shop_products (category_id, name, description, price, deliver_type, "
            "ref_pct, is_active, sort_order, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid, name, desc, price, typ, ref_pct, active, int(nxt), _now()))
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
            queue_user_msg(int(o["user_id"]),
                           f"✅ আপনার অর্ডার #{oid} ডেলিভারি হয়েছে\n\n"
                           f"🛍 {o['product_name']}\n\n{content}\n\n"
                           "⚠️ এই তথ্য কারও সাথে শেয়ার করবেন না।")
            log_admin("shop_deliver", oid, f"uid={o['user_id']}")
            return redirect(f"/admin/shop/order/{oid}?ok=ডেলিভারি পাঠানো হয়েছে (বট ১ মিনিটে পৌঁছে দেবে)")
        if act == "refund":
            _x("UPDATE users SET balance=balance+%s WHERE user_id=%s",
               (float(o["price"] or 0), int(o["user_id"])))
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
