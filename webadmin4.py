"""Instagram Task — অ্যাডমিন ওয়েব প্যানেল (webadmin blueprint-এ যোগ হয়)।

পেজ: /admin/igtasks (তালিকা), /admin/igtask/new (তৈরি), /admin/igtask/<id> (এডিট +
জমা তথ্য), CSV export, সাবমিশন মুছে ফেলা। ফেসবুক প্যানেলের মতোই, শুধু UID-র বদলে
Instagram Username আর আলাদা টেবিল (ig_tasks / ig_submissions)।
"""
import json

from urllib.parse import quote as _q

from flask import request, redirect, Response

from webadmin import (
    wadm, page, guard, table, badge, esc, icon, num, money, one, many, scal, _x,
    log_admin, pager, st_badge, review_card, review_tokens, review_apply,
    review_counts, review_kpi, _fire_settings_changed, _fire_review_changed,
    xlsx_bytes, queue_task_notify, export_table, queue_user_msg, queue_broadcast,
    vid_value,
    review_one, review_apply_lines,
)

def _ts_now():
    """created_at — বটের now() ফরম্যাটে সময়।"""
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M")


_DDL = (
    """CREATE TABLE IF NOT EXISTS ig_tasks (
        id                    BIGSERIAL PRIMARY KEY,
        name                  TEXT DEFAULT '',
        rules_bn              TEXT DEFAULT '',
        rules_en              TEXT DEFAULT '',
        price                 REAL DEFAULT 0,
        username_mode         TEXT DEFAULT 'fixed',
        username              TEXT DEFAULT '',
        username_list         TEXT DEFAULT '',
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
    """CREATE TABLE IF NOT EXISTS ig_submissions (
        id              BIGSERIAL PRIMARY KEY,
        task_id         BIGINT NOT NULL,
        user_id         BIGINT NOT NULL,
        account_no      INTEGER DEFAULT 1,
        ig_username     TEXT DEFAULT '',
        ig_password     TEXT DEFAULT '',
        login_username  TEXT DEFAULT '',
        login_password  TEXT DEFAULT '',
        data            TEXT DEFAULT '{}',
        status          TEXT DEFAULT 'pending',
        synced          INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_igsub_task ON ig_submissions (task_id)",
    "DROP INDEX IF EXISTS uq_igsub_name",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_igsub_name_pw ON ig_submissions "
    "(task_id, ig_username, COALESCE(ig_password,''))",
    "ALTER TABLE ig_submissions ADD COLUMN IF NOT EXISTS paid INTEGER DEFAULT 1",
    "ALTER TABLE ig_submissions ADD COLUMN IF NOT EXISTS notified INTEGER DEFAULT 1",
    "ALTER TABLE ig_submissions ADD COLUMN IF NOT EXISTS reviewed_at TEXT DEFAULT ''",
    "ALTER TABLE ig_tasks ADD COLUMN IF NOT EXISTS video_url TEXT DEFAULT ''",
    """CREATE TABLE IF NOT EXISTS ig_pw_groups (
        id          BIGSERIAL PRIMARY KEY,
        task_id     BIGINT NOT NULL,
        password    TEXT DEFAULT '',
        seats_total INTEGER DEFAULT 0,
        per_user    INTEGER DEFAULT 0,
        is_active   INTEGER DEFAULT 1,
        note        TEXT DEFAULT '',
        created_at  TEXT DEFAULT ''
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ig_pw_groups_u ON ig_pw_groups (task_id, password)",
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
    {"key": "2fa", "label_bn": "🔑 2FA Key দিন", "label_en": "🔑 Enter the 2FA key",
     "required": 1, "type": "2fa"},
]

# কলামের ছোট নাম — Sheet-এ হেডারও এটাই হবে
COLUMN_CHOICES = [
    ("username", "Username"), ("pass", "pass"), ("2fa", "2fa"), ("coki", "coki"),
    ("sl", "SL"), ("date", "Date"), ("user_id", "Telegram ID"),
    ("tg_username", "TG Username"), ("login_username", "Login Username"),
    ("task", "Task"), ("account_no", "Account No"), ("status", "Status"),
]

FIELD_TYPES = ("iguser", "password", "cookies", "2fa", "email", "number", "text")

# ইউজারনেম-টাইপ টোকেন — ফিল্ড-লিস্টে এগুলো আলাদা করে দেখানো লাগে না
_USER_TOKENS = ("username", "user", "ig", "iguser", "handle")


def guess_type(key):
    k = str(key or "").lower()
    if k in _USER_TOKENS or k in ("instagram", "user_name"):
        return "iguser"
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
        typ = f.get("type") or guess_type(f.get("key"))
        if str(typ).lower() == "username":
            typ = "iguser"
        lines.append(f"{f.get('key','')}|{f.get('label_bn','')}|{f.get('label_en','')}|{typ}")
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
        if typ in _USER_TOKENS:
            typ = "iguser"
        out.append({"key": key, "label_bn": lb, "label_en": le, "required": 1, "type": typ})
    return out or DEFAULT_FIELDS


_NAME_MODES = ("auto", "fixed", "upload", "random")


def _mode(v):
    v = (v or "fixed").strip().lower()
    return v if v in _NAME_MODES else "fixed"


def _clean_user(txt):
    """@ বাদ, স্পেস বাদ, ছোট হাতের অক্ষরে।"""
    out = "".join(str(txt or "").split()).lstrip("@").lower()
    return out


def _read_names(field):
    """আপলোড করা .txt/.csv → ইউজারনেমের লিস্ট (প্রতি লাইনে একটা, CSV হলে ১ম কলাম)।"""
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
            name = _clean_user(line.split(",")[0])
            if name and name not in out:
                out.append(name)
        return "\n".join(out[:5000])
    except Exception:
        return ""


def _form(task=None):
    t = task or {}
    def v(k, d=""):
        return esc(t.get(k) if t.get(k) is not None else d)
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
<div class='card'><h3>{icon('task')} Instagram Task — {'এডিট' if task else 'নতুন'}</h3>
<form method='post' enctype='multipart/form-data'>
<div class='grid g2'>
  <div class='form'><label>Task নাম</label>
    <input name='name' value='{v('name')}' placeholder='ইনস্টাগ্রাম 2fa' required></div>
  <div class='form'><label>প্রতি অ্যাকাউন্টে পেমেন্ট ($)</label>
    <input name='price' type='number' step='0.0001' min='0.0001' value='{v('price', '0')}'></div>
</div>
<div class='form'><label>Rules (বাংলা)</label>
  <textarea name='rules_bn' rows='6' placeholder='• নিজের Instagram অ্যাকাউন্ট দিতে হবে&#10;• একই Username একবারই'>{
    esc(t.get('rules_bn') or '')}</textarea></div>
<div class='form'><label>Rules (English) — খালি রাখলে বাংলাটাই দেখাবে</label>
  <textarea name='rules_en' rows='4'>{esc(t.get('rules_en') or '')}</textarea></div>
<div class='form'><label>📽 Video instruction — লিংক (যেকোনো লিংক)</label>
  <input name='video_url' value='{v('video_url') if not str(t.get('video_url') or '').startswith('tg:') else ''}'
         placeholder='https://youtu.be/... / https://drive.google.com/... / যেকোনো MP4 লিংক'>
  <p class='mut' style='font-size:11px'>ইউজার Rules স্ক্রিনে “📽 Video দেখুন” বাটন দেখবে — চাপলে এই লিংক খুলবে।</p></div>
<div class='form'><label>🎬 অথবা ভিডিও ফাইল আপলোড (সর্বোচ্চ ~৪৮MB)</label>
  <input type='file' name='video_file' accept='video/*'>
  <p class='mut' style='font-size:11px'>আপলোড করলে বটই ভিডিওটা ইউজারকে পাঠাবে (Telegram file_id হিসেবে সেভ হয়)।
    {('<b>এখন সেট করা আছে:</b> ✅ আপলোড করা ভিডিও' if str(t.get('video_url') or '').startswith('tg:') else '')}
    লিংক আর ফাইল — দুটোই দিলে ফাইলটাই আগে নেওয়া হবে।</p></div>

<h3 style='margin-top:16px'>👤 ইউজারকে দেওয়া Username (বট দেয়)</h3>
<p class='mut' style='font-size:11px'>বট এই লিস্ট থেকে Username বেছে ইউজারকে দেয় — দেওয়ার <b>আগেই</b> চেক করে ওই নামে Instagram-এ অ্যাকাউন্ট আছে কি না (থাকলে পরের নাম নেয়া হয়) এবং এই টাস্কে আগে জমা হয়েছে কি না। ইউজার চাইলে নিচের 🔄 বাটনে <b>১ বার</b> বদলাতে পারে।</p>
<div class='grid g2'>
  <div class='form'><label>Username — মোড</label><select name='username_mode'>
    <option value='auto' {'selected' if (t.get('username_mode') or 'auto') == 'auto' else ''}>🤖 Auto — বট নিজেই ইউজারনেম বানাবে (কিছু লিখতে হবে না)</option>
    <option value='fixed' {'selected' if (t.get('username_mode') or 'auto') == 'fixed' else ''}>Fixed (সবাই একই)</option>
    <option value='upload' {'selected' if (t.get('username_mode') or 'auto') == 'upload' else ''}>আপলোড (লিস্ট ধরে ধরে, ঘুরে ঘুরে)</option>
    <option value='random' {'selected' if (t.get('username_mode') or 'auto') == 'random' else ''}>Random (লিস্ট থেকে এলোমেলো)</option>
  </select></div>
  <div class='form'><label>Username (fixed হলে)</label>
    <input name='username' value='{v('username')}' placeholder='johhrpv67'></div>
</div>
<div class='form'><label>Username list (upload/random) — প্রতি লাইনে একটি</label>
  <textarea name='username_list' rows='5' placeholder='johhrpv67&#10;marcus_ig&#10;daniel.d'>{
    esc(t.get('username_list') or '')}</textarea>
  <input name='username_file' type='file' accept='.txt,.csv,.text' style='margin-top:8px'>
  <p class='mut' style='font-size:11px'>ফাইল আপলোড করলে টেক্সটের বদলে ফাইলের নামগুলোই ব্যবহার হবে
    (এক লাইনে একটা, বা CSV-র প্রথম কলাম)। ১০০ নাম দিলে ১০০টি কাজ আলাদা ইউজারনেম পাবে,
    পরেরগুলো আবার শুরু থেকে।</p></div>

<h3 style='margin-top:16px'>🔑 Password</h3>
<div class='grid g2'>
  <div class='form'><label>Password (ইউজারকে এটাই দেখানো হবে)</label>
    <input name='password' value='{v('password')}' placeholder='gmail@03'></div>
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
  <textarea name='fields' rows='5' placeholder='username|🔑 আপনার Instagram Username দিন|🔑 Enter username|iguser&#10;2fa|🔑 2FA key দিন|2FA key|2fa'>{
    esc(_fields_to_text(t.get('fields')))}</textarea></div>
{_order_html(_fields_to_text(t.get('fields')))}
<p class='mut' style='font-size:11px'>এই লাইনের ক্রমটাই প্রশ্নের ক্রম, আর <b>বাংলা কলামটাই প্রশ্নের লেখা</b> —
  বট নিজে থেকে কোনো প্রশ্ন যোগ করে না। type:
  <code>2fa</code> (key দিলে বট ৬ ডিজিটের কোড দেবে), <code>cookies</code>,
  <code>email</code>, <code>number</code>, <code>text</code>। type খালি রাখলে key দেখে অনুমান হবে।<br>
  ⚠️ <code>username</code>/<code>password</code> আর জিজ্ঞেস করা হয় না — বট নিজেই Username+Password দেয়
  (এই লিস্টে ওগুলো থাকলে কেটে দেওয়া দেখাবে)। কোনো প্রশ্ন না রাখলে কার্ডেই
  <b>✅ অ্যাকাউন্ট খোলা শেষ</b> বাটন আসবে।</p>

<h3 style='margin-top:16px'>📊 লিমিট</h3>
<div class='grid g2'>
  <div class='form'><label>সর্বোচ্চ মোট অ্যাকাউন্ট (0 = আনলিমিটেড)</label>
    <input name='max_accounts' type='number' min='0' value='{v('max_accounts', '0')}'></div>
  <div class='form'><label>প্রতি ইউজার সর্বোচ্চ অ্যাকাউন্ট (0 = আনলিমিটেড)</label>
    <input name='per_user_limit' type='number' min='0' value='{v('per_user_limit', '1')}'></div>
</div>

<h3 style='margin-top:16px'>🧾 Google Sheet</h3>
<div class='form'><label>Sheet-এ কোনটা আগে, কোনটা পরে — নাম্বার দিন (১ = প্রথম কলাম, ২ = দ্বিতীয়…)</label>
  {col_grid}
  <p class='mut' style='font-size:11px'>যে কলাম চান না সেটার ঘর <b>ফাঁকা</b> রাখুন। একই নাম্বার দুইবার দিলে
    তালিকার ক্রম অনুযায়ী সাজবে। Sheet/CSV-র হেডার হবে ঠিক এই ক্রমেই।</p></div>
<div class='form'><label>Google Sheet Web App URL (Apps Script — খালি রাখলে শুধু CSV/ফাইল)</label>
  <input name='sheet_url' value='{v('sheet_url')}' placeholder='https://script.google.com/macros/s/.../exec'>
  <p class='mut' style='font-size:11px'>লিংক বানানোর রেডি কোড + ধাপ: <a href='/admin/sheethelp'
    target='_blank'><b>📄 Apps Script কোড দেখুন / কপি করুন</b></a></p></div>
<div class='form'><label>প্রতিদিন অটো ফাইল পাঠানোর সময় (খালি = বন্ধ)</label>
  <input name='export_time' type='time' value='{esc(t.get('export_time') or '')}'></div>

<div class='form'><label>অবস্থা</label><select name='is_active'>
  <option value='1' {'selected' if int(t.get('is_active', 1) or 0) else ''}>চালু</option>
  <option value='0' {'selected' if not int(t.get('is_active', 1) or 0) else ''}>বন্ধ</option>
</select></div>

<div class='form'><label>📢 ইউজারদের নোটিফাই</label>
  <label style='display:flex;gap:8px;align-items:center;font-weight:400'>
    <input type='checkbox' name='notify' value='1' {'checked' if not task else ''}>
    সেভ করার পর সব verified ইউজারকে এই টাস্কের আপডেট পাঠাও
  </label>
  <p class='mut' style='font-size:11px'>দাম বাড়ানো, নাম/নিয়ম বদলানো বা নতুন টাস্ক — নিজের ইচ্ছেমতো টিক দিন।</p></div>

<div class='row' style='margin-top:14px'>
  <button type='submit'>💾 Save</button>
  <a class='btn gray' href='/admin/igtasks'>Cancel</a>
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
            if k and k not in _USER_TOKENS and k not in (
                    "pass", "password", "2fa", "coki", "cookies", "cookie"):
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
    _ask = 0
    for f in fs:
        typ = str(f.get("type") or guess_type(f.get("key"))).lower()
        key = str(f.get("key") or "").lower()
        if typ in ("iguser", "username", "password") or key in _USER_TOKENS or \
                key in ("pass", "password"):
            chips.append(
                f"<s><b>{esc(f.get('key'))}</b></s> "
                f"<span class='mut'>(বট নিজেই দেয় — জিজ্ঞেস করা হবে না)</span>")
            continue
        _ask += 1
        chips.append(
            f"<span class='badge'>{_bn(_ask)}</span> <b>{esc(f.get('key'))}</b> "
            f"<span class='mut'>({esc(typ)})</span>")
    return ("<p class='mut' style='font-size:11px;margin:6px 0 2px'>ইউজারকে এই ক্রমেই জিজ্ঞেস করা হবে "
            "(বট নিজে ক্রম ঠিক করে না, প্রশ্নও আপনার লেখা):</p>"
            "<div class='row' style='gap:6px;flex-wrap:wrap'>"
            + " <span class='mut'>→</span> ".join(chips) + "</div>")


def _cols_from_orders(form):
    """ord_<key>=নাম্বার → ক্রম অনুযায়ী কলামের লিস্ট (১ = প্রথম)।"""
    items = []
    for k in list(form.keys()):
        if not str(k).startswith("ord_"):
            continue
        tok = str(k)[4:].strip().lower()
        try:
            n = int(str(form.get(k) or "").strip())
        except Exception:
            continue
        if tok and n > 0:
            items.append((n, tok))
    items.sort(key=lambda x: x[0])
    out = []
    for _n, tok in items:
        if tok not in out:
            out.append(tok)
    return out[:40]


def _vid_url(v):
    """📽 Video — লিংক (যেকোনো) অথবা আপলোড করা ভিডিওর `tg:<file_id>`।"""
    u = (v or "").strip()
    if u.startswith("tg:") and len(u) > 5:
        return u
    if u and not u.lower().startswith(("http://", "https://")):
        if "." in u and " " not in u:
            u = "https://" + u.lstrip("/")
    return u if u.lower().startswith(("http://", "https://")) else ""


def _save(tid=None):
    f = request.form
    data = {
        "name": (f.get("name") or "").strip(),
        "rules_bn": (f.get("rules_bn") or "").strip(),
        "rules_en": (f.get("rules_en") or "").strip(),
        "video_url": _vid_url(vid_value(f.get("video_url"))),
        "price": float(f.get("price") or 0),
        "username_mode": _mode(f.get("username_mode")),
        "username": _clean_user(f.get("username")),
        "username_list": (_read_names("username_file")
                          or (f.get("username_list") or "").strip()),
        "password": (f.get("password") or "").strip(),
        "password_auto": 1 if f.get("password_auto") == "on" else 0,
        "password_change_time": (f.get("password_change_time") or "00:00").strip(),
        "fields": json.dumps(_text_to_fields(f.get("fields")), ensure_ascii=False),
        "max_accounts": int(f.get("max_accounts") or 0),
        # ⚠️ `or 1` হলে 0 (আনলিমিটেড) সেভই হতো না — হার্ডকোড ১ হয়ে যেত
        "per_user_limit": (int(str(f.get("per_user_limit")).strip())
                           if str(f.get("per_user_limit") or "").strip() else 1),
        "is_active": 1 if (f.get("is_active") or "1") == "1" else 0,
        "sheet_url": (f.get("sheet_url") or "").strip(),
        "sheet_columns": json.dumps(_cols_from_orders(f), ensure_ascii=False),
        "export_time": (f.get("export_time") or "").strip(),
    }
    if not data["name"]:
        return None
    if tid:
        _old = one("SELECT password FROM ig_tasks WHERE id=%s", (tid,)) or {}
        _old_pw = str(_old.get("password") or "")
        sets = ", ".join(f"{k}=%s" for k in data)
        _x(f"UPDATE ig_tasks SET {sets} WHERE id=%s", tuple(data.values()) + (tid,))
        # পাসওয়ার্ড বদলালে pending জমাগুলোও নতুন পাসওয়ার্ড পাবে → শিটে নতুন সারি যাবে
        if _old_pw != str(data.get("password") or ""):
            try:
                _x("UPDATE ig_submissions SET ig_password=%s, synced=0 "
                   "WHERE task_id=%s AND status='pending'", (data.get("password"), tid))
            except Exception:
                pass
        return tid
    cols = ", ".join(data.keys())
    ph = ", ".join(["%s"] * len(data))
    r = one(f"INSERT INTO ig_tasks ({cols}, created_at) VALUES ({ph}, to_char(now(),'YYYY-MM-DD HH24:MI')) RETURNING id",
            tuple(data.values()))
    return int(r["id"]) if r else None


@wadm.route("/admin/igtasks", methods=["GET"])
def igtasks():
    g = guard()
    if g:
        return g
    _init()
    rows = many("SELECT * FROM ig_tasks ORDER BY id DESC")
    _cnt = {int(r["task_id"]): int(r.get("n") or 0) for r in many(
        "SELECT task_id, COUNT(*) AS n FROM ig_submissions GROUP BY task_id")}
    trs = []
    for t in rows:
        tid = int(t["id"])
        total = _cnt.get(tid, 0)
        trs.append([
            f"<a class='mono' href='/admin/igtask/{tid}'>#{tid}</a>",
            f"<a href='/admin/igtask/{tid}'>{esc(t['name'])}</a>",
            money(t.get("price") or 0),
            f"{num(total)} / {num(t.get('max_accounts') or '∞')}",
            num(t.get("per_user_limit") or 0),
            badge("চালু", "ok") if int(t.get("is_active") or 0) else badge("বন্ধ", "err"),
            ("🔑 auto " + esc(t.get("password_change_time") or "")) if int(t.get("password_auto") or 0) else "—",
            badge("Sheet ✓", "ok") if (t.get("sheet_url") or "").strip() else "—",
            f"<a class='btn gray sm' href='/admin/igtask/{tid}'>Edit</a> "
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/subs'>Submissions</a> "
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/passwords'>🔑 Passwords</a> "
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/passwords/new'>➕ পাসওয়ার্ড</a> "
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/export.xlsx'>Excel</a> "
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/export.csv'>CSV</a> "
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/notify'>📢</a>",
        ])
    body = (
        "<div class='card'><div class='row' style='justify-content:space-between'>"
        f"<h3>{icon('task')} Instagram Tasks</h3>"
        "<a class='btn' href='/admin/igtask/new'>➕ নতুন Instagram Task</a></div>"
        + table(["ID", "Name", "Price", "Accounts", "Per user", "Status", "Password",
                 "Sheet", ""], trs, "এখনো কোনো Instagram Task নেই")
        + "<p class='mut' style='font-size:12px;margin-top:10px'>"
          "ইউজার বটে <b>📸 Instagram Tasks</b> বাটন দেখবে। প্রতিটি টাস্কের জমা তথ্য আলাদা — "
          "আলাদা CSV/Sheet।</p></div>"
    )
    return page("Instagram Tasks", body, "igtasks", toast=request.args.get("ok"))


@wadm.route("/admin/igtask/new", methods=["GET", "POST"])
def igtask_new():
    g = guard()
    if g:
        return g
    _init()
    if request.method == "POST":
        tid = _save()
        if not tid:
            return page("New Instagram Task", _form(), "igtasks", err="নাম দিন")
        log_admin("igtask_add", tid, "instagram task created")
        _fire_settings_changed(["ig_task"])
        if (request.form.get("notify") or "").strip() == "1":
            queue_task_notify("ig", tid)      # সব ইউজারকে নতুন টাস্কের নোটিফিকেশন
        return redirect(f"/admin/igtask/{tid}?ok=সেভ হয়েছে")
    return page("New Instagram Task", _form(), "igtasks")


@wadm.route("/admin/igtask/<int:tid>", methods=["GET", "POST"])
def igtask_edit(tid):
    g = guard()
    if g:
        return g
    _init()
    if request.method == "POST":
        if _save(tid):
            log_admin("igtask_edit", tid, "instagram task updated")
            _fire_settings_changed(["ig_task"])
            if (request.form.get("notify") or "").strip() == "1":
                queue_task_notify("ig", tid)          # ইউজারদের জানাও (ঐচ্ছিক)
            return redirect(f"/admin/igtask/{tid}?ok=সেভ হয়েছে")
        return page("Edit Instagram Task", _form(one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))),
                    "igtasks", err="নাম দিন")
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "igtasks", status=404)
    total = scal("SELECT COUNT(*) FROM ig_submissions WHERE task_id=%s", (tid,))
    head = (
        "<div class='grid g-kpi'>"
        + f"<div class='card kpi'><div class='t'>মোট জমা</div><div class='v'>{num(total)}</div></div>"
        + f"<div class='card kpi'><div class='t'>স্লট বাকি</div><div class='v'>"
          f"{num(max(0, int(t.get('max_accounts') or 0) - total)) if int(t.get('max_accounts') or 0) else '∞'}</div></div>"
        + f"<div class='card kpi'><div class='t'>পেমেন্ট/অ্যাকাউন্ট</div><div class='v'>{money(t.get('price') or 0)}</div></div>"
        + "</div>"
    )
    links = (
        f"<div class='card'><div class='row'><a class='btn gray' href='/admin/igtask/{tid}/subs'>"
        "📋 জমা তথ্য দেখুন</a>"
        f"<a class='btn gray' href='/admin/igtask/{tid}/export.xlsx'>⬇️ Excel Download</a>"
        f"<a class='btn gray' href='/admin/igtask/{tid}/export.csv'>CSV</a>"
        f"<a class='btn gray' href='/admin/igtask/{tid}/notify'>📢 নোটিফাই</a>"
        "<form method='post' action='/admin/do/igtask/%d/toggle' style='display:inline'>"
        "<button class='btn gray'>%s</button></form></div></div>"
        % (tid, "⏸ বন্ধ করুন" if int(t.get("is_active") or 0) else "▶️ চালু করুন")
    )
    return page(f"IG Task #{tid}", head + links + _form(t), "igtasks",
                toast=request.args.get("ok"))


@wadm.route("/admin/do/igtask/<int:tid>/toggle", methods=["POST"])
def igtask_toggle(tid):
    g = guard()
    if g:
        return g
    t = one("SELECT is_active FROM ig_tasks WHERE id=%s", (tid,))
    if t:
        _x("UPDATE ig_tasks SET is_active=%s WHERE id=%s",
           (0 if int(t["is_active"] or 0) else 1, tid))
        log_admin("igtask_toggle", tid, "instagram task toggled")
        _fire_settings_changed(["ig_task"])
    return redirect(f"/admin/igtask/{tid}?ok=আপডেট হয়েছে")


@wadm.route("/admin/do/igtask/<int:tid>/delete", methods=["POST"])
def igtask_delete(tid):
    g = guard()
    if g:
        return g
    _x("DELETE FROM ig_submissions WHERE task_id=%s", (tid,))
    _x("DELETE FROM ig_tasks WHERE id=%s", (tid,))
    log_admin("igtask_delete", tid, "instagram task deleted")
    _fire_settings_changed(["ig_task"])
    return redirect("/admin/igtasks?ok=মুছে ফেলা হয়েছে")


# ───────────────── Instagram পাসওয়ার্ড-গ্রুপ (সিট) ─────────────────
def _int_form(name, dflt=0):
    try:
        return int(float((request.form.get(name) or "").replace(",", ".").strip()))
    except Exception:
        return dflt


def _ig_pw_form(t, g=None, tid=0):
    g = g or {}
    _pw = str(g.get("password") or request.args.get("pw") or "")
    _seats = g.get("seats_total") if g.get("seats_total") not in (None, "") else 0
    _per = g.get("per_user") if g.get("per_user") not in (None, "") else 0
    return (
        "<div class='card'><h3>🔑 পাসওয়ার্ড + সিট (Instagram)</h3>"
        "<form method='post'>"
        "<div class='field'><label>পাসওয়ার্ড</label>"
        f"<input name='password' value='{esc(_pw)}' placeholder='Instagram অ্যাকাউন্টের পাসওয়ার্ড' required></div>"
        "<div class='field'><label>এই পাসওয়ার্ডে মোট কত জন / সিট নেবে</label>"
        f"<input name='seats_total' value='{esc(str(_seats))}' placeholder='যেমন 20'>"
        "<p class='mut' style='font-size:11px;margin:4px 0 0'>০ = সীমা নেই।</p></div>"
        "<div class='field'><label>একজন ইউজার সর্বোচ্চ কয়টা দিতে পারবে</label>"
        f"<input name='per_user' value='{esc(str(_per))}' placeholder='যেমন 2'>"
        "<p class='mut' style='font-size:11px;margin:4px 0 0'>০ = সীমা নেই।</p></div>"
        "<div class='field'><label>নোট (ঐচ্ছিক)</label>"
        f"<input name='note' value='{esc(str(g.get('note') or ''))}'></div>"
        "<div class='row' style='gap:8px;margin-top:12px'>"
        "<button class='ok' type='submit'>💾 সেভ করুন</button>"
        f"<a class='btn gray' href='/admin/igtask/{tid}/passwords'>বাতিল</a></div>"
        "</form></div>"
    )


@wadm.route("/admin/igtask/<int:tid>/passwords", methods=["GET"])
def igtask_passwords(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "igtasks", status=404)
    groups = many("SELECT * FROM ig_pw_groups WHERE task_id=%s ORDER BY id", (tid,))
    agg = {}
    for r in many(
            "SELECT COALESCE(ig_password,'') AS pw, COUNT(*) AS n, "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pend, "
            "SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS appr, "
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rej "
            "FROM ig_submissions WHERE task_id=%s GROUP BY COALESCE(ig_password,'')", (tid,)):
        agg[str(r.get("pw") or "")] = r
    trs, seen = [], set()
    for g2 in groups:
        _pw = str(g2.get("password") or "")
        seen.add(_pw)
        r = agg.get(_pw) or {}
        used = int(r.get("n") or 0)
        seats = int(g2.get("seats_total") or 0)
        per = int(g2.get("per_user") or 0)
        trs.append([
            f"<span class='mono'>{esc(_pw) or '-'}</span>"
            + (f"<div class='mut' style='font-size:11px'>{esc(str(g2.get('note') or ''))}</div>"
               if str(g2.get("note") or "").strip() else ""),
            (f"<b>{num(used)}</b> / {num(seats)}" if seats else f"{num(used)} / ∞")
            + (badge("ভরা", "err") if (seats and used >= seats) else ""),
            num(per) if per else "∞",
            badge(str(int(r.get("pend") or 0)), "mut"),
            badge(str(int(r.get("appr") or 0)), "ok"),
            badge(str(int(r.get("rej") or 0)), "err"),
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/subs?pw={_q(_pw)}'>👁 সাবমিশন</a>",
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/passwords/{int(g2['id'])}/edit'>✏️ Edit</a>",
            "<form method='post' action='/admin/do/igtask/%d/pwgroup/%d/delete' "
            "style='display:inline' onsubmit=\"return confirm('গ্রুপটা মুছে যাবে (জমাগুলো থাকবে)?')\">"
            "<button class='btn gray sm' type='submit'>🗑</button></form>" % (tid, int(g2["id"])),
        ])
    for pw, r in agg.items():
        if not pw or pw in seen:
            continue
        trs.append([
            f"<span class='mono'>{esc(pw)}</span>"
            "<div class='mut' style='font-size:11px'>গ্রুপ নেই (পুরোনো)</div>",
            f"{num(r.get('n') or 0)} / —", "—",
            badge(str(int(r.get("pend") or 0)), "mut"),
            badge(str(int(r.get("appr") or 0)), "ok"),
            badge(str(int(r.get("rej") or 0)), "err"),
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/subs?pw={_q(pw)}'>👁 সাবমিশন</a>",
            f"<a class='btn gray sm' href='/admin/igtask/{tid}/passwords/new?pw={_q(pw)}'>➕ গ্রুপ বানান</a>",
            "",
        ])
    body = (
        "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
        f"<h3>🔑 পাসওয়ার্ড ও সিট — {esc(t.get('name') or '')}</h3>"
        "<div class='row' style='gap:6px'>"
        f"<a class='btn ok sm' href='/admin/igtask/{tid}/passwords/new'>➕ নতুন পাসওয়ার্ড</a>"
        f"<a class='btn gray sm' href='/admin/igtask/{tid}/subs'>⟨ সব সাবমিশন</a></div></div>"
        "<p class='mut' style='font-size:12px'>প্রতিটি পাসওয়ার্ড আলাদা গ্রুপ — নিজের সিট আর "
        "একজনের লিমিট। সিট ভরে গেলে নতুন ইউজার এই পাসওয়ার্ড পাবে না। 👁 চাপলে শুধু ওই "
        "পাসওয়ার্ডের জমা — সেখানেই ✅/❌।</p>"
        + table(["পাসওয়ার্ড", "সিট", "একজন", "Pending", "Approved", "Rejected", "", "", ""],
                trs, "কোনো পাসওয়ার্ড নেই — ➕ নতুন পাসওয়ার্ড চাপুন")
        + "</div>"
    )
    return page(f"Passwords — {t.get('name') or 'IG'}", body, "igtasks",
                toast=request.args.get("ok"), err=request.args.get("err"))


@wadm.route("/admin/igtask/<int:tid>/passwords/new", methods=["GET", "POST"])
def igtask_pw_new(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "igtasks", status=404)
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        if not pw:
            return page("নতুন পাসওয়ার্ড", _ig_pw_form(t, None, tid), "igtasks", err="পাসওয়ার্ড দিন")
        _x("INSERT INTO ig_pw_groups (task_id, password, seats_total, per_user, is_active, "
           "note, created_at) VALUES (%s,%s,%s,%s,1,%s,%s)",
           (tid, pw[:80], _int_form("seats_total", 0), _int_form("per_user", 0),
            (request.form.get("note") or "").strip()[:120], _ts_now()))
        log_admin("igtask_pw_new", tid, f"pw={pw[:20]}")
        return redirect(f"/admin/igtask/{tid}/passwords?ok=" + _q("✅ পাসওয়ার্ড যোগ হয়েছে"))
    return page("নতুন পাসওয়ার্ড", _ig_pw_form(t, None, tid), "igtasks")


@wadm.route("/admin/igtask/<int:tid>/passwords/<int:gid>/edit", methods=["GET", "POST"])
def igtask_pw_edit(tid, gid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    row = one("SELECT * FROM ig_pw_groups WHERE id=%s AND task_id=%s", (gid, tid))
    if not t or not row:
        return page("Not found", "<div class='card'>পাসওয়ার্ড পাওয়া যায়নি</div>",
                    "igtasks", status=404)
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip() or str(row.get("password") or "")
        _x("UPDATE ig_pw_groups SET password=%s, seats_total=%s, per_user=%s, note=%s "
           "WHERE id=%s AND task_id=%s",
           (pw[:80], _int_form("seats_total", 0), _int_form("per_user", 0),
            (request.form.get("note") or "").strip()[:120], gid, tid))
        log_admin("igtask_pw_edit", tid, f"gid={gid}")
        return redirect(f"/admin/igtask/{tid}/passwords?ok=" + _q("✅ সেভ হয়েছে"))
    return page("পাসওয়ার্ড এডিট", _ig_pw_form(t, row, tid), "igtasks")


@wadm.route("/admin/do/igtask/<int:tid>/pwgroup/<int:gid>/delete", methods=["POST"])
def igtask_pw_group_del(tid, gid):
    g = guard()
    if g:
        return g
    _init()
    _x("DELETE FROM ig_pw_groups WHERE id=%s AND task_id=%s", (gid, tid))
    log_admin("igtask_pw_group_del", tid, f"gid={gid}")
    return redirect(f"/admin/igtask/{tid}/passwords?ok=" + _q("গ্রুপ ডিলিট হয়েছে (জমাগুলো আছে)"))


@wadm.route("/admin/igtask/<int:tid>/notify", methods=["GET", "POST"])
def igtask_notify(tid):
    """এই টাস্কের নোটিফিকেশন — নিজের লেখা মেসেজ সব verified ইউজারকে (বট পাঠাবে)।"""
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "igtasks", status=404)
    if request.method == "POST":
        txt = (request.form.get("text") or "").strip()
        if not txt:
            return page(f"Notify · IG #{tid}",
                        _ig_notify_form(t, tid, ""), "igtasks", err="মেসেজ লেখুন")
        queue_broadcast(txt)
        log_admin("ig_task_notify", tid, txt[:120])
        return redirect(f"/admin/igtasks?ok=" + _q("📢 নোটিফিকেশন পাঠানো হয়েছে"))
    return page(f"Notify · IG #{tid}", _ig_notify_form(t, tid, None), "igtasks",
                toast=request.args.get("ok"))


def _ig_notify_form(t, tid, default_text=None):
    if default_text is None:
        total = scal("SELECT COUNT(*) FROM ig_submissions WHERE task_id=%s", (tid,))
        mx = int(t.get("max_accounts") or 0)
        left = (max(0, mx - total) if mx else "∞")
        default_text = (
            "🔥 নতুন টাস্ক এসেছে!\n\n"
            f"📸 {t.get('name') or 'Instagram Task'}\n"
            f"💰 রিওয়ার্ড: {float(t.get('price') or 0):g}$\n"
            f"👥 স্লট বাকি: {left}\n\n"
            "📌 বটে 📸 Instagram Tasks চাপুন → রুলস পড়ে শুরু করুন।"
        )
    return (
        "<div class='card'><h3>" + icon("bell") + " 📢 নোটিফিকেশন পাঠান</h3>"
        f"<p class='mut' style='font-size:12px'>এই মেসেজটি সব verified ইউজারের কাছে "
        f"বটে যাবে (Task: <b>{esc(t.get('name') or '')}</b>)। লেখা বদলাতে পারেন।</p>"
        "<form method='post'>"
        f"<textarea name='text' rows='8' class='f1' style='width:100%'>"
        f"{esc(default_text)}</textarea>"
        "<div class='row' style='margin-top:10px'>"
        "<button class='ok' type='submit'>📢 সবাইকে পাঠান</button>"
        f"<a class='btn gray' href='/admin/igtask/{tid}'>বাতিল</a></div></form></div>"
    )


@wadm.route("/admin/igtask/<int:tid>/subs", methods=["GET"])
def igtask_subs(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "igtasks", status=404)
    try:
        p = max(1, int(request.args.get("page") or 1))
    except Exception:
        p = 1
    _pw_filter = (request.args.get("pw") or "").strip()
    if _pw_filter:
        rows = many(
            "SELECT s.*, COALESCE(u.username,'') AS username FROM ig_submissions s "
            "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s "
            "AND COALESCE(s.ig_password,'')=%s "
            "ORDER BY s.id DESC LIMIT 100 OFFSET %s",
            (tid, _pw_filter, (p - 1) * 100))
    else:
        rows = many(
            "SELECT s.*, COALESCE(u.username,'') AS username FROM ig_submissions s "
            "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s "
            "ORDER BY s.id DESC LIMIT 100 OFFSET %s", (tid, (p - 1) * 100))
    trs = []
    for s in rows:
        trs.append([
            f"<span class='mono'>{esc(str(s.get('created_at') or ''))}</span>",
            f"<a class='mono' href='/admin/user/{int(s['user_id'])}'>{int(s['user_id'])}</a>"
            f"<div class='mut' style='font-size:11px'>@{esc(s.get('username') or '-')}</div>",
            num(s.get("account_no") or 0),
            f"<span class='mono'>{esc(s.get('ig_username') or '-')}</span>",
            f"<span class='mono'>{esc(s.get('login_username') or '-')}</span>",
            f"<span class='mono'>{esc(s.get('ig_password') or '-')}</span>",
            f"<span class='mono'>{esc(s.get('login_password') or '-')}</span>",
            st_badge(s.get("status")),
            badge("Sheet ✓", "ok") if int(s.get("synced") or 0) else badge("Sheet -", "mut"),
            "<form method='post' action='/admin/do/igsub/%d/approve' style='display:inline'>"
            "<button class='btn green sm'>✅</button></form>" % int(s["id"]),
            "<form method='post' action='/admin/do/igsub/%d/reject' style='display:inline;margin-left:4px'>"
            "<button class='btn gray sm'>❌</button></form>" % int(s["id"]),
            "<form method='post' action='/admin/do/igsub/%d/delete' style='display:inline'>"
            "<button class='btn gray sm'>🗑</button></form>" % int(s["id"]),
        ])
    body = (
        review_kpi(review_counts("ig_submissions", tid))
        + review_card(tid, "ig")
        + f"<div class='card'><div class='row' style='justify-content:space-between'>"
        f"<h3>{esc(t['name'])} — জমা তথ্য"
        + (f" · পাসওয়ার্ড: <span class='mono'>{esc(_pw_filter)}</span> "
           f"<a class='btn gray sm' href='/admin/igtask/{tid}/subs'>সব দেখুন</a>"
           if _pw_filter else "") + "</h3>"
        f"<a class='btn gray sm' href='/admin/igtask/{tid}/export.xlsx'>⬇️ Excel</a>"
        f"<a class='btn gray sm' href='/admin/igtask/{tid}/export.csv'>CSV</a></div>"
        + table(["Date", "User", "Acc#", "Instagram Username", "Login Username",
                 "Login Pass", "IG Password", "Status", "Sheet", ""], trs,
                "এখনো কোনো জমা নেই")
        + pager(f"/admin/igtask/{tid}/subs", p, len(rows) == 100)
        + "</div>"
    )
    return page(f"{t['name']} — Submissions", body, "igtasks",
                toast=request.args.get("ok"))


@wadm.route("/admin/do/igtask/<int:tid>/review", methods=["POST"])
def igtask_review(tid):
    """Instagram Username লিস্ট → approve বা reject।"""
    g = guard()
    if g:
        return g
    _init()
    action = (request.form.get("action") or "").strip().lower()
    _raw_ids = request.form.get("ids") or ""
    tokens = [ln.strip() for ln in _raw_ids.splitlines() if ln.strip()]
    if not tokens:
        return redirect(f"/admin/igtask/{tid}/subs?ok=⚠️ কোনো Username দেওয়া হয়নি")
    if action not in ("approve", "reject"):
        return redirect(f"/admin/igtask/{tid}/subs?ok=⚠️ কাজ বোঝা যায়নি")
    done, miss = review_apply_lines(tid, request.form.get("ids") or "",
                                    action, "ig_submissions", "ig_username")
    log_admin("igtask_review", tid, f"{action} {done}/{len(tokens)}")
    msg = f"{'✅ approve' if action == 'approve' else '❌ reject'}: {done} টি"
    if miss:
        msg += f" · পাওয়া যায়নি {miss} টি"
    if action == "reject" and done:
        msg += " · টাকা যোগ হবে না"
    return redirect(f"/admin/igtask/{tid}/subs?ok={msg}")


@wadm.route("/admin/do/igsub/<int:sid>/approve", methods=["POST"])
def igsub_approve(sid):
    g = guard()
    if g:
        return g
    s = one("SELECT task_id FROM ig_submissions WHERE id=%s", (sid,))
    n = review_one(int(s["task_id"]), sid, "approve", "ig_submissions", "ig_username") if s else 0
    return redirect(f"/admin/igtask/{int(s['task_id'])}/subs?ok=" +
                    ("✅ approve হয়েছে" if n else "⚠️ আগেই প্রসেস হয়েছে") if s else "/admin/igtasks")


@wadm.route("/admin/do/igsub/<int:sid>/reject", methods=["POST"])
def igsub_reject(sid):
    g = guard()
    if g:
        return g
    s = one("SELECT task_id FROM ig_submissions WHERE id=%s", (sid,))
    n = review_one(int(s["task_id"]), sid, "reject", "ig_submissions", "ig_username") if s else 0
    return redirect(f"/admin/igtask/{int(s['task_id'])}/subs?ok=" +
                    ("❌ reject হয়েছে" if n else "⚠️ আগেই প্রসেস হয়েছে") if s else "/admin/igtasks")


@wadm.route("/admin/do/igsub/<int:sid>/delete", methods=["POST"])
def igsub_delete(sid):
    g = guard()
    if g:
        return g
    s = one("SELECT task_id, user_id, COALESCE(status,'pending') AS st FROM ig_submissions WHERE id=%s", (sid,))
    if s:
        try:
            from webadmin import hist_add
            hist_add(s.get("user_id") or 0, "ig", s.get("st") or "pending", 1)
        except Exception:
            pass
        _x("DELETE FROM ig_submissions WHERE id=%s", (sid,))
        _x("UPDATE ig_tasks SET total_done=GREATEST(0, COALESCE(total_done,0)-1) WHERE id=%s",
           (int(s["task_id"]),))
        log_admin("igsub_delete", sid, "instagram submission deleted")
        return redirect(f"/admin/igtask/{int(s['task_id'])}/subs?ok=মুছে ফেলা হয়েছে")
    return redirect("/admin/igtasks")


def _ig_export_table(t):
    """CSV আর Excel — দুটোর জন্যই একই ডেটা, কলাম-ক্রম অনুযায়ী।

    মান বের করা webadmin.export_table-এ — টাস্কের ফিল্ডের আসল key আর
    টোকেন (uid/username/2fa/coki) দুটোই ধরে, তাই কোনো কলাম ফাঁকা থাকে না।
    """
    try:
        cols = json.loads(t.get("sheet_columns") or "[]")
    except Exception:
        cols = []
    cols = [str(c) for c in cols if str(c).strip()] or [
        "sl", "date", "username", "password", "2fa", "user_id", "tg_username"]
    subs = many(
        "SELECT s.*, COALESCE(u.username,'') AS username FROM ig_submissions s "
        "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s ORDER BY s.id ASC",
        (int(t["id"]),))
    return export_table(cols, subs, t, "ig")


def _ig_file_base(t, tid):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in str(t.get("name") or tid))[:40]


@wadm.route("/admin/igtask/<int:tid>/export.xlsx", methods=["GET"])
def igtask_export_xlsx(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "igtasks", status=404)
    header, rows = _ig_export_table(t)
    data = xlsx_bytes(header, rows, str(t.get("name") or ("IG Task %s" % tid)))
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=ig_{_ig_file_base(t, tid)}.xlsx"})


@wadm.route("/admin/igtask/<int:tid>/export.csv", methods=["GET"])
def igtask_export(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM ig_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "igtasks", status=404)
    import csv, io
    header, rows = _ig_export_table(t)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=ig_{_ig_file_base(t, tid)}.csv"})


# ── মেনুতে দেখাও (CONTENT সেকশন, Facebook Tasks-এর ঠিক পরে) ──
try:
    from webadmin import MENU as _MENU
    if not any(isinstance(r, tuple) and len(r) == 5 and r[0] == "igtasks" for r in _MENU):
        _idx = next((i for i, r in enumerate(_MENU) if isinstance(r, tuple) and r[0] == "fbtasks"),
                    len(_MENU))
        _MENU.insert(_idx + 1, ("igtasks", "/admin/igtasks", "Instagram Tasks", "task", ""))
except Exception:
    pass
