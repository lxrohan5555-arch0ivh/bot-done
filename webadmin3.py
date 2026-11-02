"""Facebook Task — অ্যাডমিন ওয়েব প্যানেল (webadmin blueprint-এ যোগ হয়)।

/páginas: /admin/fbtasks (তালিকা), /admin/fbtask/new (তৈরি),
/admin/fbtask/<id> (এডিট + জমা তথ্য), CSV export, সাবমিশন মুছে ফেলা।
"""
import json

from flask import request, redirect, Response

from webadmin import (
    wadm, page, guard, table, badge, esc, icon, num, money, one, many, scal, _x,
    log_admin, pager, st_badge, review_card, review_tokens, review_apply,
    review_counts, review_kpi,
)

_DDL = (
    """CREATE TABLE IF NOT EXISTS fb_tasks (
        id                    BIGSERIAL PRIMARY KEY,
        name                  TEXT DEFAULT '',
        rules_bn              TEXT DEFAULT '',
        rules_en              TEXT DEFAULT '',
        price                 REAL DEFAULT 0,
        first_name_mode       TEXT DEFAULT 'fixed',
        first_name            TEXT DEFAULT '',
        first_name_list       TEXT DEFAULT '',
        last_name_mode        TEXT DEFAULT 'fixed',
        last_name             TEXT DEFAULT '',
        last_name_list        TEXT DEFAULT '',
        password              TEXT DEFAULT '',
        password_auto         INTEGER DEFAULT 0,
        password_change_time  TEXT DEFAULT '00:00',
        password_last_change  TEXT DEFAULT '',
        fields                TEXT DEFAULT '[]',
        max_accounts          INTEGER DEFAULT 0,
        per_user_limit        INTEGER DEFAULT 1,
        total_done            INTEGER DEFAULT 0,
        is_active             INTEGER DEFAULT 1,
        sheet_url             TEXT DEFAULT '',
        sheet_columns         TEXT DEFAULT '[]',
        export_time           TEXT DEFAULT '',
        last_export_at        TEXT DEFAULT '',
        created_at            TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS fb_submissions (
        id              BIGSERIAL PRIMARY KEY,
        task_id         BIGINT NOT NULL,
        user_id         BIGINT NOT NULL,
        account_no      INTEGER DEFAULT 1,
        fb_uid          TEXT DEFAULT '',
        fb_password     TEXT DEFAULT '',
        login_first     TEXT DEFAULT '',
        login_last      TEXT DEFAULT '',
        login_password  TEXT DEFAULT '',
        data            TEXT DEFAULT '{}',
        status          TEXT DEFAULT 'pending',
        synced          INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_fbsub_task ON fb_submissions (task_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_fbsub_uid ON fb_submissions (task_id, fb_uid)",
    "ALTER TABLE fb_submissions ADD COLUMN IF NOT EXISTS paid INTEGER DEFAULT 1",
    "ALTER TABLE fb_submissions ADD COLUMN IF NOT EXISTS notified INTEGER DEFAULT 1",
    "ALTER TABLE fb_submissions ADD COLUMN IF NOT EXISTS reviewed_at TEXT DEFAULT ''",
)
_READY = [False]


def _init():
    if _READY[0]:
        return
    for q in _DDL:
        try:
            _x(q)
        except Exception:
            pass
    _READY[0] = True


DEFAULT_FIELDS = [
    {"key": "uid", "label_bn": "🔑 আপনার Facebook UID দিন",
     "label_en": "🔑 Enter your Facebook UID", "required": 1, "type": "uid"},
]

# কলামের ছোট নাম — Sheet-এ হেডারও এটাই হবে
COLUMN_CHOICES = [
    ("uid", "uid"), ("pass", "pass"), ("2fa", "2fa"), ("coki", "coki"),
    ("sl", "SL"), ("date", "Date"), ("user_id", "Telegram ID"), ("username", "Username"),
    ("first", "First Name"), ("last", "Last Name"), ("task", "Task"),
    ("account_no", "Account No"), ("status", "Status"),
]

FIELD_TYPES = ("uid", "password", "cookies", "2fa", "email", "number", "text")


def guess_type(key):
    k = str(key or "").lower()
    if k in ("uid", "fb_uid", "user_id", "profile", "id"):
        return "uid"
    if "cookie" in k:
        return "cookies"
    if "2fa" in k or "totp" in k or "2step" in k:
        return "2fa"
    if "pass" in k:
        return "password"
    if "mail" in k:
        return "email"
    return "text"

def _fields_to_text(raw):
    try:
        arr = json.loads(raw or "[]")
    except Exception:
        arr = []
    if not isinstance(arr, list) or not arr:
        arr = DEFAULT_FIELDS
    lines = []
    for f in arr:
        if not isinstance(f, dict):
            continue
        lines.append(f"{f.get('key','')}|{f.get('label_bn','')}|{f.get('label_en','')}"
                     f"|{f.get('type') or guess_type(f.get('key'))}")
    return "\n".join(lines)


def _text_to_fields(txt):
    out = []
    for line in (txt or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        key = parts[0] if parts else ""
        if not key:
            continue
        lb = parts[1] if len(parts) > 1 and parts[1] else key
        le = parts[2] if len(parts) > 2 and parts[2] else lb
        typ = parts[3].lower() if len(parts) > 3 and parts[3] else guess_type(key)
        if typ not in FIELD_TYPES:
            typ = guess_type(key)
        out.append({"key": key, "label_bn": lb, "label_en": le, "required": 1, "type": typ})
    return out or DEFAULT_FIELDS


def _tok(col):
    c = str(col or "").strip()
    return c


_NAME_MODES = ("fixed", "upload", "random")


def _mode(v):
    v = (v or "fixed").strip().lower()
    return v if v in _NAME_MODES else "fixed"


def _read_names(field):
    """আপলোড করা .txt/.csv → নামের লিস্ট (প্রতি লাইনে একটা, CSV হলে ১ম কলাম)।"""
    try:
        up = request.files.get(field)
        if not up or not getattr(up, "filename", ""):
            return ""
        raw = up.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", "ignore")
        out = []
        for line in str(raw).replace("\r", "\n").splitlines():
            line = line.strip().strip('"')
            if not line:
                continue
            name = line.split(",")[0].strip().strip('"')
            if name and name not in out:
                out.append(name)
        return "\n".join(out[:5000])
    except Exception:
        return ""


def _form(task=None):
    t = task or {}
    def v(k, d=""):
        return esc(t.get(k) if t.get(k) is not None else d)
    try:
        cols = json.loads(t.get("sheet_columns") or "[]")
    except Exception:
        cols = []
    grid = []
    for tok, lbl, hint, pos in _col_rows(t):
        grid.append(
            f"<tr><td><b>{esc(lbl)}</b>"
            + (f" <span class='mut' style='font-size:11px'>{esc(hint)}</span>" if hint else "")
            + f"</td><td class='mut mono' style='font-size:11px'>{esc(tok)}</td>"
            f"<td><input name='ord_{esc(tok)}' type='number' min='0' max='40' value='{pos}'"
            f" style='width:72px' placeholder='—'></td></tr>")
    col_grid = ("<table class='tbl' style='width:100%'><tr><th>কলাম</th><th>key</th>"
                "<th style='width:90px'>ক্রম</th></tr>" + "".join(grid) + "</table>")
    body = f"""
<div class='card'><h3>{icon('task')} Facebook Task — {'এডিট' if task else 'নতুন'}</h3>
<form method='post' enctype='multipart/form-data'>
<div class='grid g2'>
  <div class='form'><label>Task নাম</label>
    <input name='name' value='{v('name')}' placeholder='Facebook Task 01' required></div>
  <div class='form'><label>প্রতি অ্যাকাউন্টে পেমেন্ট ($)</label>
    <input name='price' type='number' step='0.01' min='0' value='{v('price', '0')}'></div>
</div>
<div class='form'><label>Rules (বাংলা)</label>
  <textarea name='rules_bn' rows='6' placeholder='• নিজের Facebook অ্যাকাউন্ট দিতে হবে&#10;• একই UID একবারই'>{
    esc(t.get('rules_bn') or '')}</textarea></div>
<div class='form'><label>Rules (English) — খালি রাখলে বাংলাটাই দেখাবে</label>
  <textarea name='rules_en' rows='4'>{esc(t.get('rules_en') or '')}</textarea></div>

<h3 style='margin-top:16px'>👤 নাম</h3>
<div class='grid g2'>
  <div class='form'><label>First name — মোড</label><select name='first_name_mode'>
    <option value='fixed' {'selected' if (t.get('first_name_mode') or 'fixed') == 'fixed' else ''}>Fixed (সবাই একই)</option>
    <option value='upload' {'selected' if (t.get('first_name_mode') or '') == 'upload' else ''}>আপলোড (লিস্ট ধরে ধরে, ঘুরে ঘুরে)</option>
    <option value='random' {'selected' if (t.get('first_name_mode') or '') == 'random' else ''}>Random (লিস্ট থেকে এলোমেলো)</option>
  </select></div>
  <div class='form'><label>First name (fixed হলে)</label>
    <input name='first_name' value='{v('first_name')}' placeholder='Clayton'></div>
</div>
<div class='form'><label>First name list (upload/random) — প্রতি লাইনে একটি</label>
  <textarea name='first_name_list' rows='5' placeholder='Clayton&#10;Marcus&#10;Daniel'>{
    esc(t.get('first_name_list') or '')}</textarea>
  <input name='first_name_file' type='file' accept='.txt,.csv,.text' style='margin-top:8px'>
  <p class='mut' style='font-size:11px'>ফাইল আপলোড করলে টেক্সটের বদলে ফাইলের নামগুলোই ব্যবহার হবে
    (এক লাইনে একটা, বা CSV-র প্রথম কলাম)। ১০০ নাম দিলে ১০০টি কাজ আলাদা নাম পাবে, পরেরগুলো আবার শুরু থেকে।</p></div>
<div class='grid g2'>
  <div class='form'><label>Last name — মোড</label><select name='last_name_mode'>
    <option value='fixed' {'selected' if (t.get('last_name_mode') or 'fixed') == 'fixed' else ''}>Fixed</option>
    <option value='upload' {'selected' if (t.get('last_name_mode') or '') == 'upload' else ''}>আপলোড (ঘুরে ঘুরে)</option>
    <option value='random' {'selected' if (t.get('last_name_mode') or '') == 'random' else ''}>Random</option>
  </select></div>
  <div class='form'><label>Last name (fixed হলে)</label>
    <input name='last_name' value='{v('last_name')}' placeholder='Chambers'></div>
</div>
<div class='form'><label>Last name list (upload/random) — প্রতি লাইনে একটি</label>
  <textarea name='last_name_list' rows='5'>{esc(t.get('last_name_list') or '')}</textarea>
  <input name='last_name_file' type='file' accept='.txt,.csv,.text' style='margin-top:8px'></div>

<h3 style='margin-top:16px'>🔑 Password</h3>
<div class='grid g2'>
  <div class='form'><label>Password (ইউজারকে এটাই দেখানো হবে)</label>
    <input name='password' value='{v('password')}' placeholder='ecrfcTb9GfQT'></div>
  <div class='form'><label>অটো পাসওয়ার্ড বদল</label><select name='password_auto'>
    <option value='off' {'selected' if not int(t.get('password_auto') or 0) else ''}>বন্ধ</option>
    <option value='on' {'selected' if int(t.get('password_auto') or 0) else ''}>চালু (প্রতিদিন)</option>
  </select>
  <input name='password_change_time' type='time' value='{esc(t.get('password_change_time') or '00:00')}'
         style='margin-top:8px'></div>
</div>
<p class='mut' style='font-size:11px'>অটো চালু থাকলে প্রতিদিন এই সময়ে নতুন পাসওয়ার্ড তৈরি হবে — বট আপনাকে DM-এ জানাবে
   (শেষ বদল: {esc(t.get('password_last_change') or '—')})</p>

<h3 style='margin-top:16px'>📝 কী কী তথ্য নেবেন (ক্রম অনুযায়ী)</h3>
<div class='form'><label>প্রতি লাইনে: key | বাংলা প্রশ্ন | English প্রশ্ন | type</label>
  <textarea name='fields' rows='5' placeholder='uid|🔑 আপনার Facebook UID দিন|🔑 Enter UID|uid&#10;cookies|🍪 Cookies দিন|cookies|&#10;2fa|🔑 2FA key দিন|2FA key|2fa'>{
    esc(_fields_to_text(t.get('fields')))}</textarea></div>
{_order_html(_fields_to_text(t.get('fields')))}
<p class='mut' style='font-size:11px'>এই লাইনের ক্রমটাই প্রশ্নের ক্রম — উপরে যেটা, সেটা আগে চাওয়া হবে। তাছাড়া
  বট নিজে থেকে কিছু ঠিক করবে না। type: <code>uid</code> (১৪–১৬ সংখ্যা),
  <code>cookies</code> (c_user= / sessionid=), <code>2fa</code> (key দিলে বট কোড দেবে),
  <code>password</code>, <code>email</code>, <code>number</code>, <code>text</code>।
  type খালি রাখলে key দেখে অনুমান হবে।</p>

<h3 style='margin-top:16px'>📊 লিমিট</h3>
<div class='grid g2'>
  <div class='form'><label>সর্বোচ্চ মোট অ্যাকাউন্ট (0 = আনলিমিটেড)</label>
    <input name='max_accounts' type='number' min='0' value='{v('max_accounts', '0')}'></div>
  <div class='form'><label>প্রতি ইউজার সর্বোচ্চ অ্যাকাউন্ট</label>
    <input name='per_user_limit' type='number' min='0' value='{v('per_user_limit', '1')}'></div>
</div>

<h3 style='margin-top:16px'>🧾 Google Sheet</h3>
<div class='form'><label>Sheet-এ কোনটা আগে, কোনটা পরে — নাম্বার দিন (১ = প্রথম কলাম, ২ = দ্বিতীয়…)</label>
  {col_grid}
  <p class='mut' style='font-size:11px'>যে কলাম চান না সেটার ঘর <b>ফাঁকা</b> রাখুন। একই নাম্বার দুইবার দিলে
    তালিকার ক্রম অনুযায়ী সাজবে। Sheet/CSV-র হেডার হবে ঠিক এই ক্রমেই।</p></div>
<div class='form'><label>Google Sheet Web App URL (Apps Script — খালি রাখলে শুধু CSV/ফাইল)</label>
  <input name='sheet_url' value='{v('sheet_url')}' placeholder='https://script.google.com/macros/s/.../exec'></div>
<div class='form'><label>প্রতিদিন অটো ফাইল পাঠানোর সময় (খালি = বন্ধ)</label>
  <input name='export_time' type='time' value='{esc(t.get('export_time') or '')}'></div>

<div class='form'><label>অবস্থা</label><select name='is_active'>
  <option value='1' {'selected' if int(t.get('is_active', 1) or 0) else ''}>চালু</option>
  <option value='0' {'selected' if not int(t.get('is_active', 1) or 0) else ''}>বন্ধ</option>
</select></div>

<div class='row' style='margin-top:14px'>
  <button type='submit'>💾 Save</button>
  <a class='btn gray' href='/admin/fbtasks'>Cancel</a>
</div>
</form></div>
"""
    return body


_BN_DIGITS = "০১২৩৪৫৬৭৮৯"


def _bn(i):
    return "".join(_BN_DIGITS[int(d)] for d in str(i))


def _col_rows(task):
    """Sheet-এর কলামের তালিকা — প্রতিটির সাথে "ক্রম" নাম্বার (১ = প্রথম কলাম)।"""
    t = task or {}
    try:
        cols = [str(c) for c in json.loads(t.get("sheet_columns") or "[]")]
    except Exception:
        cols = []
    rows, seen = [], set()

    def add(tok, label, hint=""):
        tok = str(tok or "").strip()
        if not tok or tok in seen:
            return
        seen.add(tok)
        pos = cols.index(tok) + 1 if tok in cols else ""
        rows.append((tok, label, hint, pos))

    for tok, lbl in COLUMN_CHOICES:
        add(tok, lbl, "")
    try:
        for f in _text_to_fields(_fields_to_text(t.get("fields"))):
            k = str(f.get("key") or "").strip().lower()
            if k and k not in ("uid", "pass", "password", "2fa", "coki", "cookies", "cookie"):
                add(k, k, f"ফিল্ড ({f.get('type') or guess_type(k)})")
    except Exception:
        pass
    for c in cols:                                  # পুরোনো/কাস্টম কলামও দেখাবে
        add(c, c, "সেভ করা")
    return rows


def _order_html(raw):
    """ফিল্ড-ক্রমের প্রিভিউ — কোন প্রশ্ন আগে, কোনটা পরে (পুরোটাই অ্যাডমিন-সেট)।"""
    try:
        fs = _text_to_fields(raw)
    except Exception:
        fs = []
    if not fs:
        return "<p class='mut' style='font-size:11px'>কোনো ফিল্ড নেই।</p>"
    chips = []
    for i, f in enumerate(fs, 1):
        chips.append(
            f"<span class='badge'>{_bn(i)}</span> <b>{esc(f.get('key'))}</b> "
            f"<span class='mut'>({esc(f.get('type') or guess_type(f.get('key')))})</span>")
    return ("<p class='mut' style='font-size:11px;margin:6px 0 2px'>ইউজারকে এই ক্রমেই জিজ্ঞেস করা হবে "
            "(বট নিজে ক্রম ঠিক করে না):</p><div class='row' style='gap:6px;flex-wrap:wrap'>"
            + " <span class='mut'>→</span> ".join(chips) + "</div>")


def _cols_from_orders(form):
    """ord_<key>=নাম্বার → ক্রম অনুযায়ী কলামের লিস্ট (১ = প্রথম)।"""
    items = []
    for k in list(form.keys()):
        if not str(k).startswith("ord_"):
            continue
        tok = str(k)[4:].strip().lower()
        try:
            num = int(str(form.get(k) or "").strip())
        except Exception:
            continue
        if tok and num > 0:
            items.append((num, tok))
    items.sort(key=lambda x: x[0])
    out = []
    for _n, tok in items:
        if tok not in out:
            out.append(tok)
    return out[:40]


def _save(tid=None):
    f = request.form
    data = {
        "name": (f.get("name") or "").strip(),
        "rules_bn": (f.get("rules_bn") or "").strip(),
        "rules_en": (f.get("rules_en") or "").strip(),
        "price": float(f.get("price") or 0),
        "first_name_mode": _mode(f.get("first_name_mode")),
        "first_name": (f.get("first_name") or "").strip(),
        "first_name_list": (_read_names("first_name_file")
                            or (f.get("first_name_list") or "").strip()),
        "last_name_mode": _mode(f.get("last_name_mode")),
        "last_name": (f.get("last_name") or "").strip(),
        "last_name_list": (_read_names("last_name_file")
                           or (f.get("last_name_list") or "").strip()),
        "password": (f.get("password") or "").strip(),
        "password_auto": 1 if f.get("password_auto") == "on" else 0,
        "password_change_time": (f.get("password_change_time") or "00:00").strip(),
        "fields": json.dumps(_text_to_fields(f.get("fields")), ensure_ascii=False),
        "max_accounts": int(f.get("max_accounts") or 0),
        "per_user_limit": int(f.get("per_user_limit") or 1),
        "is_active": 1 if (f.get("is_active") or "1") == "1" else 0,
        "sheet_url": (f.get("sheet_url") or "").strip(),
        "sheet_columns": json.dumps(_cols_from_orders(f), ensure_ascii=False),
        "export_time": (f.get("export_time") or "").strip(),
    }
    if not data["name"]:
        return None
    if tid:
        sets = ", ".join(f"{k}=%s" for k in data)
        _x(f"UPDATE fb_tasks SET {sets} WHERE id=%s", tuple(data.values()) + (tid,))
        return tid
    cols = ", ".join(data.keys())
    ph = ", ".join(["%s"] * len(data))
    r = one(f"INSERT INTO fb_tasks ({cols}, created_at) VALUES ({ph}, to_char(now(),'YYYY-MM-DD HH24:MI')) RETURNING id",
            tuple(data.values()))
    return int(r["id"]) if r else None


@wadm.route("/admin/fbtasks", methods=["GET"])
def fbtasks():
    g = guard()
    if g:
        return g
    _init()
    rows = many("SELECT * FROM fb_tasks ORDER BY id DESC")
    trs = []
    for t in rows:
        tid = int(t["id"])
        total = scal("SELECT COUNT(*) FROM fb_submissions WHERE task_id=%s", (tid,))
        trs.append([
            f"<a class='mono' href='/admin/fbtask/{tid}'>#{tid}</a>",
            f"<a href='/admin/fbtask/{tid}'>{esc(t['name'])}</a>",
            money(t.get("price") or 0),
            f"{num(total)} / {num(t.get('max_accounts') or '∞')}",
            num(t.get("per_user_limit") or 0),
            badge("চালু", "ok") if int(t.get("is_active") or 0) else badge("বন্ধ", "err"),
            ("🔑 auto " + esc(t.get("password_change_time") or "")) if int(t.get("password_auto") or 0) else "—",
            badge("Sheet ✓", "ok") if (t.get("sheet_url") or "").strip() else "—",
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}'>Edit</a> "
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/subs'>Submissions</a> "
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/export.csv'>CSV</a>",
        ])
    body = (
        "<div class='card'><div class='row' style='justify-content:space-between'>"
        f"<h3>{icon('task')} Facebook Tasks</h3>"
        "<a class='btn' href='/admin/fbtask/new'>➕ নতুন Facebook Task</a></div>"
        + table(["ID", "Name", "Price", "Accounts", "Per user", "Status", "Password",
                 "Sheet", ""], trs, "এখনো কোনো Facebook Task নেই")
        + "<p class='mut' style='font-size:12px;margin-top:10px'>"
          "ইউজার বটে <b>📘 Facebook Tasks</b> বাটন দেখবে। প্রতিটি টাস্কের জমা তথ্য আলাদা — "
          "আলাদা CSV/Sheet।</p></div>"
    )
    return page("Facebook Tasks", body, "fbtasks", toast=request.args.get("ok"))


@wadm.route("/admin/fbtask/new", methods=["GET", "POST"])
def fbtask_new():
    g = guard()
    if g:
        return g
    _init()
    if request.method == "POST":
        tid = _save()
        if not tid:
            return page("New Facebook Task", _form(), "fbtasks", err="নাম দিন")
        log_admin("fbtask_add", tid, "facebook task created")
        return redirect(f"/admin/fbtask/{tid}?ok=সেভ হয়েছে")
    return page("New Facebook Task", _form(), "fbtasks")


@wadm.route("/admin/fbtask/<int:tid>", methods=["GET", "POST"])
def fbtask_edit(tid):
    g = guard()
    if g:
        return g
    _init()
    if request.method == "POST":
        if _save(tid):
            log_admin("fbtask_edit", tid, "facebook task updated")
            return redirect(f"/admin/fbtask/{tid}?ok=সেভ হয়েছে")
        return page("Edit Facebook Task", _form(one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))),
                    "fbtasks", err="নাম দিন")
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "fbtasks", status=404)
    total = scal("SELECT COUNT(*) FROM fb_submissions WHERE task_id=%s", (tid,))
    head = (
        "<div class='grid g-kpi'>"
        + f"<div class='card kpi'><div class='t'>মোট জমা</div><div class='v'>{num(total)}</div></div>"
        + f"<div class='card kpi'><div class='t'>স্লট বাকি</div><div class='v'>"
          f"{num(max(0, int(t.get('max_accounts') or 0) - total)) if int(t.get('max_accounts') or 0) else '∞'}</div></div>"
        + f"<div class='card kpi'><div class='t'>পেমেন্ট/অ্যাকাউন্ট</div><div class='v'>{money(t.get('price') or 0)}</div></div>"
        + "</div>"
    )
    links = (
        f"<div class='card'><div class='row'><a class='btn gray' href='/admin/fbtask/{tid}/subs'>"
        "📋 জমা তথ্য দেখুন</a>"
        f"<a class='btn gray' href='/admin/fbtask/{tid}/export.csv'>⬇️ CSV Download</a>"
        "<form method='post' action='/admin/do/fbtask/%d/toggle' style='display:inline'>"
        "<button class='btn gray'>%s</button></form></div></div>"
        % (tid, "⏸ বন্ধ করুন" if int(t.get("is_active") or 0) else "▶️ চালু করুন")
    )
    return page(f"FB Task #{tid}", head + links + _form(t), "fbtasks", toast=request.args.get("ok"))


@wadm.route("/admin/do/fbtask/<int:tid>/toggle", methods=["POST"])
def fbtask_toggle(tid):
    g = guard()
    if g:
        return g
    t = one("SELECT is_active FROM fb_tasks WHERE id=%s", (tid,))
    if t:
        _x("UPDATE fb_tasks SET is_active=%s WHERE id=%s",
           (0 if int(t["is_active"] or 0) else 1, tid))
        log_admin("fbtask_toggle", tid, "facebook task toggled")
    return redirect(f"/admin/fbtask/{tid}?ok=আপডেট হয়েছে")


@wadm.route("/admin/do/fbtask/<int:tid>/delete", methods=["POST"])
def fbtask_delete(tid):
    g = guard()
    if g:
        return g
    _x("DELETE FROM fb_submissions WHERE task_id=%s", (tid,))
    _x("DELETE FROM fb_tasks WHERE id=%s", (tid,))
    log_admin("fbtask_delete", tid, "facebook task deleted")
    return redirect("/admin/fbtasks?ok=মুছে ফেলা হয়েছে")


@wadm.route("/admin/fbtask/<int:tid>/subs", methods=["GET"])
def fbtask_subs(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "fbtasks", status=404)
    try:
        p = max(1, int(request.args.get("page") or 1))
    except Exception:
        p = 1
    rows = many(
        "SELECT s.*, COALESCE(u.username,'') AS username FROM fb_submissions s "
        "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s "
        "ORDER BY s.id DESC LIMIT 100 OFFSET %s", (tid, (p - 1) * 100))
    trs = []
    for s in rows:
        trs.append([
            f"<span class='mono'>{esc(str(s.get('created_at') or ''))}</span>",
            f"<a class='mono' href='/admin/user/{int(s['user_id'])}'>{int(s['user_id'])}</a>"
            f"<div class='mut' style='font-size:11px'>@{esc(s.get('username') or '-')}</div>",
            num(s.get("account_no") or 0),
            f"<span class='mono'>{esc(s.get('fb_uid') or '-')}</span>",
            f"<span class='mono'>{esc(s.get('fb_password') or '-')}</span>",
            esc(f"{s.get('login_first') or ''} {s.get('login_last') or ''}".strip() or "-"),
            f"<span class='mono'>{esc(s.get('login_password') or '-')}</span>",
            st_badge(s.get("status")),
            badge("Sheet ✓", "ok") if int(s.get("synced") or 0) else badge("Sheet -", "mut"),
            "<form method='post' action='/admin/do/fbsub/%d/delete' style='display:inline'>"
            "<button class='btn gray sm'>🗑</button></form>" % int(s["id"]),
        ])
    body = (
        review_kpi(review_counts("fb_submissions", tid))
        + review_card(tid, "fb")
        + f"<div class='card'><div class='row' style='justify-content:space-between'>"
        f"<h3>{esc(t['name'])} — জমা তথ্য</h3>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/export.csv'>⬇️ CSV</a></div>"
        + table(["Date", "User", "Acc#", "Facebook UID", "FB Password",
                 "Login Name", "Login Pass", "Status", "Sheet", ""], trs,
                "এখনো কোনো জমা নেই")
        + pager(f"/admin/fbtask/{tid}/subs", p, len(rows) == 100)
        + "</div>"
    )
    return page(f"{t['name']} — Submissions", body, "fbtasks",
                toast=request.args.get("ok"))


@wadm.route("/admin/do/fbtask/<int:tid>/review", methods=["POST"])
def fbtask_review(tid):
    """UID লিস্ট → approve বা reject। টাকা/নোটিফিকেশন বটের job পাঠায়।"""
    g = guard()
    if g:
        return g
    _init()
    action = (request.form.get("action") or "").strip().lower()
    tokens = review_tokens(request.form.get("ids") or "")
    if not tokens:
        return redirect(f"/admin/fbtask/{tid}/subs?ok=⚠️ কোনো UID দেওয়া হয়নি")
    if action not in ("approve", "reject"):
        return redirect(f"/admin/fbtask/{tid}/subs?ok=⚠️ কাজ বোঝা যায়নি")
    done, miss = review_apply(tid, tokens, action, "fb_submissions", "fb_uid")
    log_admin("fbtask_review", tid, f"{action} {done}/{len(tokens)}")
    msg = f"{'✅ approve' if action == 'approve' else '❌ reject'}: {done} টি"
    if miss:
        msg += f" · পাওয়া যায়নি {miss} টি"
    if action == "reject" and done:
        msg += " · টাকা যোগ হবে না"
    return redirect(f"/admin/fbtask/{tid}/subs?ok={msg}")


@wadm.route("/admin/do/fbsub/<int:sid>/delete", methods=["POST"])
def fbsub_delete(sid):
    g = guard()
    if g:
        return g
    s = one("SELECT task_id FROM fb_submissions WHERE id=%s", (sid,))
    if s:
        _x("DELETE FROM fb_submissions WHERE id=%s", (sid,))
        _x("UPDATE fb_tasks SET total_done=GREATEST(0, COALESCE(total_done,0)-1) WHERE id=%s",
           (int(s["task_id"]),))
        log_admin("fbsub_delete", sid, "facebook submission deleted")
        return redirect(f"/admin/fbtask/{int(s['task_id'])}/subs?ok=মুছে ফেলা হয়েছে")
    return redirect("/admin/fbtasks")


@wadm.route("/admin/fbtask/<int:tid>/export.csv", methods=["GET"])
def fbtask_export(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "fbtasks", status=404)
    import csv, io
    try:
        cols = json.loads(t.get("sheet_columns") or "[]")
    except Exception:
        cols = []
    cols = [str(c) for c in cols if str(c).strip()] or [
        "sl", "date", "uid", "password", "user_id", "username",
        "login_first", "login_last", "login_password"]
    labels = {"sl": "SL", "date": "Date", "user_id": "Telegram ID", "username": "Username",
              "task": "Task", "account_no": "Account No", "login_first": "First Name",
              "login_last": "Last Name", "login_password": "Login Password",
              "uid": "Facebook UID", "password": "Facebook Password", "status": "Status"}
    subs = many(
        "SELECT s.*, COALESCE(u.username,'') AS username FROM fb_submissions s "
        "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s ORDER BY s.id ASC", (tid,))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([labels.get(c, c[2:].replace("_", " ").title() if c.startswith("f:") else c)
                for c in cols])
    for i, s in enumerate(subs, 1):
        try:
            data = json.loads(s.get("data") or "{}")
        except Exception:
            data = {}
        out = []
        for c in cols:
            if c.startswith("f:"):
                out.append(data.get(c[2:], ""))
            elif c == "sl":
                out.append(i)
            elif c == "task":
                out.append(t.get("name", ""))
            elif c == "uid":
                out.append(s.get("fb_uid", ""))
            elif c == "password":
                out.append(s.get("fb_password", ""))
            elif c == "username":
                out.append(s.get("username", ""))
            else:
                out.append(s.get(c, ""))
        w.writerow(out)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(t.get("name") or tid))[:40]
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=fb_{safe}.csv"})


# ── মেনুতে দেখাও (CONTENT সেকশন) ──
try:
    from webadmin import MENU as _MENU
    if not any(isinstance(r, tuple) and len(r) == 5 and r[0] == "fbtasks" for r in _MENU):
        _idx = next((i for i, r in enumerate(_MENU) if isinstance(r, tuple) and r[0] == "taskstats"),
                    len(_MENU))
        _MENU.insert(_idx + 1, ("fbtasks", "/admin/fbtasks", "Facebook Tasks", "task", ""))
except Exception:
    pass
