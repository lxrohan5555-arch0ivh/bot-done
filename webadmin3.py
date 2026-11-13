"""Facebook Task — অ্যাডমিন ওয়েব প্যানেল (webadmin blueprint-এ যোগ হয়)।

/páginas: /admin/fbtasks (তালিকা), /admin/fbtask/new (তৈরি),
/admin/fbtask/<id> (এডিট + জমা তথ্য), CSV export, সাবমিশন মুছে ফেলা।
"""
import json
import time

from urllib.parse import quote as _q

from flask import request, redirect, Response

from webadmin import (
    wadm, page, guard, table, badge, esc, icon, num, money, one, many, scal, _x,
    log_admin, pager, st_badge, review_card, review_tokens, review_apply,
    review_counts, review_kpi, _fire_settings_changed, _fire_review_changed, hist_add,
    review_one, review_apply_lines,
    xlsx_bytes, queue_task_notify, export_table, queue_user_msg, queue_broadcast,
    vid_value,
)

def _ts_now():
    """created_at — বটের now() ফরম্যাটে সময়।"""
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M")


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
    "DROP INDEX IF EXISTS uq_fbsub_uid",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_fbsub_uid_pw ON fb_submissions "
    "(task_id, fb_uid, COALESCE(fb_password,''))",
    "ALTER TABLE fb_submissions ADD COLUMN IF NOT EXISTS paid INTEGER DEFAULT 1",
    "ALTER TABLE fb_submissions ADD COLUMN IF NOT EXISTS notified INTEGER DEFAULT 1",
    "ALTER TABLE fb_submissions ADD COLUMN IF NOT EXISTS reviewed_at TEXT DEFAULT ''",
    "ALTER TABLE fb_tasks ADD COLUMN IF NOT EXISTS video_url TEXT DEFAULT ''",
    """CREATE TABLE IF NOT EXISTS fb_pw_groups (
        id          BIGSERIAL PRIMARY KEY,
        task_id     BIGINT NOT NULL,
        password    TEXT DEFAULT '',
        seats_total INTEGER DEFAULT 0,
        per_user    INTEGER DEFAULT 0,
        is_active   INTEGER DEFAULT 1,
        note        TEXT DEFAULT '',
        created_at  TEXT DEFAULT ''
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS fb_pw_groups_u ON fb_pw_groups (task_id, password)",
    "ALTER TABLE fb_pw_groups ADD COLUMN IF NOT EXISTS collect_ids INTEGER DEFAULT 0",
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
    ("sl", "SL"), ("date", "Date"), ("user_id", "Telegram ID"),
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

_DEFAULT_COLS = ["sl", "date", "uid", "pass", "2fa", "coki", "user_id", "login_first",
                 "login_last"]


def _task_fields(task):
    """এই টাস্কের ফিল্ডের লিস্ট (DB-র `fields` JSON থেকে)।"""
    try:
        return _text_to_fields(_fields_to_text((task or {}).get("fields")))
    except Exception:
        return []


def _fld_key(task, typ):
    """এই টাইপের ফিল্ডের key (cookies / 2fa) — না থাকলে টাইপের নামই।"""
    for f in _task_fields(task):
        if str(f.get("type") or guess_type(f.get("key"))) == typ:
            return str(f.get("key") or "")
    return typ


def _fb_norm_cols(task, cols):
    """FB কলাম ঠিক করো — Facebook-এ কোনো username থাকে না, আর টাস্কে 2fa/cookies
    নেওয়া হলে সে দুটো কলাম সবসময় থাকবে (আগে না থাকায় ফাঁকা কলাম হয়ে যেত)।"""
    cols = [str(c) for c in cols if str(c).strip()]
    if not cols:
        return list(_DEFAULT_COLS)
    cols = [c for c in cols if str(c).lower() != "username"]
    types = set()
    for f in _task_fields(task):
        t = str(f.get("type") or "").strip()
        if not t:
            k = str(f.get("key") or "").lower()
            t = "cookies" if "cookie" in k else ("2fa" if ("2fa" in k or "totp" in k) else "")
        types.add(t)
    for tok in ("coki", "2fa"):
        if tok in cols:
            continue
        if (tok == "coki" and "cookies" not in types) or (tok == "2fa" and "2fa" not in types):
            continue
        i = (cols.index("pass") + 1 if "pass" in cols
             else (cols.index("uid") + 1 if "uid" in cols else len(cols)))
        cols.insert(i, tok)
    return cols or list(_DEFAULT_COLS)


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


_NAME_MODES = ("auto", "fixed", "upload", "random")


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
    <input name='price' type='number' step='0.0001' min='0.0001' value='{v('price', '0')}'></div>
</div>
<div class='form'><label>Rules (বাংলা)</label>
  <textarea name='rules_bn' rows='6' placeholder='• নিজের Facebook অ্যাকাউন্ট দিতে হবে&#10;• একই UID একবারই'>{
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

<h3 style='margin-top:16px'>👤 নাম</h3>
<div class='grid g2'>
  <div class='form'><label>First name — মোড</label><select name='first_name_mode'>
    <option value='auto' {'selected' if (t.get('first_name_mode') or 'auto') == 'auto' else ''}>🤖 Auto — বট নিজেই নাম বানাবে (লিস্ট দিলে সেটাই ঘুরে ঘুরে)</option>
    <option value='fixed' {'selected' if (t.get('first_name_mode') or 'auto') == 'fixed' else ''}>Fixed (সবাই একই)</option>
    <option value='upload' {'selected' if (t.get('first_name_mode') or 'auto') == 'upload' else ''}>আপলোড (লিস্ট ধরে ধরে, ঘুরে ঘুরে)</option>
    <option value='random' {'selected' if (t.get('first_name_mode') or 'auto') == 'random' else ''}>Random (লিস্ট থেকে এলোমেলো)</option>
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
    <option value='auto' {'selected' if (t.get('last_name_mode') or 'auto') == 'auto' else ''}>🤖 Auto — বট নিজেই (লিস্ট দিলে সেটাই)</option>
    <option value='fixed' {'selected' if (t.get('last_name_mode') or 'auto') == 'fixed' else ''}>Fixed</option>
    <option value='upload' {'selected' if (t.get('last_name_mode') or 'auto') == 'upload' else ''}>আপলোড (ঘুরে ঘুরে)</option>
    <option value='random' {'selected' if (t.get('last_name_mode') or 'auto') == 'random' else ''}>Random</option>
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
  <div class='form'><label>প্রতি ইউজার সর্বোচ্চ অ্যাকাউন্ট (0 = আনলিমিটেড)</label>
    <input name='per_user_limit' type='number' min='0' value='{v('per_user_limit', '1')}'></div>
</div>

<h3 style='margin-top:16px'>🧾 Google Sheet</h3>
<div class='form'><label>Sheet-এ কোনটা আগে, কোনটা পরে — নাম্বার দিন (১ = প্রথম কলাম, ২ = দ্বিতীয়…)</label>
  {col_grid}
  <p class='mut' style='font-size:11px'>যে কলাম চান না সেটার ঘর <b>ফাঁকা</b> রাখুন। একই নাম্বার দুইবার দিলে
    তালিকার ক্রম অনুযায়ী সাজবে। Sheet/CSV-র হেডার হবে ঠিক এই ক্রমেই।</p></div>
<div class='form'><label>Google Sheet Web App URL — (ঐচ্ছিক, খালি রাখলেও চলবে)</label>
  <input name='sheet_url' value='{v('sheet_url')}' placeholder='https://script.google.com/macros/s/.../exec'>
  <p class='mut' style='font-size:11px'>🛑 লিংক দিতে হবে না — জমা তথ্য <b>বটেই অটো সেভ</b> থাকে।
    দেখতে/নিতে সাবমিশন পেজের <b>⬇️ ডাউনলোড</b> চাপলেই হবে (UID, Password, Cookies, 2FA সহ)।</p>
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
        _raw = [str(c) for c in json.loads(t.get("sheet_columns") or "[]")]
    except Exception:
        _raw = []
    cols = _fb_norm_cols(t, _raw)
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
        _old = one("SELECT password FROM fb_tasks WHERE id=%s", (tid,)) or {}
        _old_pw = str(_old.get("password") or "")
        sets = ", ".join(f"{k}=%s" for k in data)
        _x(f"UPDATE fb_tasks SET {sets} WHERE id=%s", tuple(data.values()) + (tid,))
        # পাসওয়ার্ড বদলালে pending জমাগুলোও নতুন পাসওয়ার্ড পাবে → শিটে নতুন সারি যাবে
        if _old_pw != str(data.get("password") or ""):
            try:
                _x("UPDATE fb_submissions SET fb_password=%s, synced=0 "
                   "WHERE task_id=%s AND status='pending'", (data.get("password"), tid))
            except Exception:
                pass
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
    _cnt = {int(r["task_id"]): int(r.get("n") or 0) for r in many(
        "SELECT task_id, COUNT(*) AS n FROM fb_submissions GROUP BY task_id")}
    trs = []
    for t in rows:
        tid = int(t["id"])
        total = _cnt.get(tid, 0)
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
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/passwords'>🔑 Passwords</a> "
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/passwords/new'>➕ পাসওয়ার্ড</a> "
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/export.xlsx'>Excel</a> "
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
        _fire_settings_changed(["fb_task"])
        if (request.form.get("notify") or "").strip() == "1":
            queue_task_notify("fb", tid)      # সব ইউজারকে নতুন টাস্কের নোটিফিকেশন
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
            _fire_settings_changed(["fb_task"])      # বটে সাথে সাথে কার্যকর
            if (request.form.get("notify") or "").strip() == "1":
                queue_task_notify("fb", tid)          # ইউজারদের জানাও (ঐচ্ছিক)
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
        f"<a class='btn gray' href='/admin/fbtask/{tid}/export.xlsx'>⬇️ ডাউনলোড (Excel)</a>"
        f"<a class='btn gray' href='/admin/fbtask/{tid}/export.csv'>CSV</a>"
        f"<a class='btn gray' href='/admin/fbtask/{tid}/notify'>📢 নোটিফাই</a>"
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
        _fire_settings_changed(["fb_task"])
    return redirect(f"/admin/fbtask/{tid}?ok=আপডেট হয়েছে")


@wadm.route("/admin/do/fbtask/<int:tid>/delete", methods=["POST"])
def fbtask_delete(tid):
    g = guard()
    if g:
        return g
    _x("DELETE FROM fb_submissions WHERE task_id=%s", (tid,))
    _x("DELETE FROM fb_tasks WHERE id=%s", (tid,))
    log_admin("fbtask_delete", tid, "facebook task deleted")
    _fire_settings_changed(["fb_task"])
    return redirect("/admin/fbtasks?ok=মুছে ফেলা হয়েছে")


@wadm.route("/admin/fbtask/<int:tid>/notify", methods=["GET", "POST"])
def fbtask_notify(tid):
    """এই টাস্কের নোটিফিকেশন — নিজের লেখা মেসেজ সব verified ইউজারকে (বট পাঠাবে)।"""
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "fbtasks", status=404)
    total = scal("SELECT COUNT(*) FROM fb_submissions WHERE task_id=%s", (tid,))
    mx = int(t.get("max_accounts") or 0)
    left = (max(0, mx - total) if mx else "∞")
    if request.method == "POST":
        txt = (request.form.get("text") or "").strip()
        if not txt:
            return page(f"Notify · FB #{tid}",
                        _notify_form(t, tid, ""), "fbtasks", err="মেসেজ লেখুন")
        queue_broadcast(txt)
        log_admin("fb_task_notify", tid, txt[:120])
        return redirect(f"/admin/fbtasks?ok=" + _q("📢 নোটিফিকেশন পাঠানো হয়েছে"))
    return page(f"Notify · FB #{tid}", _notify_form(t, tid, None), "fbtasks",
                toast=request.args.get("ok"))


def _notify_form(t, tid, default_text=None):
    if default_text is None:
        total = scal("SELECT COUNT(*) FROM fb_submissions WHERE task_id=%s", (tid,))
        mx = int(t.get("max_accounts") or 0)
        left = (max(0, mx - total) if mx else "∞")
        default_text = (
            "🔥 নতুন টাস্ক এসেছে!\n\n"
            f"📘 {t.get('name') or 'Facebook Task'}\n"
            f"💰 রিওয়ার্ড: {float(t.get('price') or 0):g}$\n"
            f"👥 স্লট বাকি: {left}\n\n"
            "📌 বটে 📘 Facebook Tasks চাপুন → রুলস পড়ে শুরু করুন।"
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
        f"<a class='btn gray' href='/admin/fbtask/{tid}'>বাতিল</a></div></form></div>"
    )


def _int_form(name, dflt=0):
    try:
        return int(float((request.form.get(name) or "").replace(",", ".").strip()))
    except Exception:
        return dflt


def _pw_group_form(t, g=None, tid=0):
    g = g or {}
    _pw = str(g.get("password") or request.args.get("pw") or "")
    _seats = g.get("seats_total") if g.get("seats_total") not in (None, "") else 0
    _per = g.get("per_user") if g.get("per_user") not in (None, "") else 0
    _act = (f"/admin/fbtask/{tid}/passwords/{int(g['id'])}/edit" if g.get("id")
            else f"/admin/fbtask/{tid}/passwords/new")
    return (
        "<div class='card'><h3>🔑 পাসওয়ার্ড + সিট</h3>"
        "<form method='post'>"
        "<div class='field'><label>পাসওয়ার্ড</label>"
        f"<input name='password' value='{esc(_pw)}' placeholder='FB অ্যাকাউন্টের পাসওয়ার্ড' required></div>"
        "<div class='field'><label>এই পাসওয়ার্ডে মোট কত জন / সিট নেবে</label>"
        f"<input name='seats_total' value='{esc(str(_seats))}' placeholder='যেমন 20'>"
        "<p class='mut' style='font-size:11px;margin:4px 0 0'>০ = সীমা নেই। "
        "সিট ভরে গেলে নতুন ইউজার এই পাসওয়ার্ড পাবে না।</p></div>"
        "<div class='field'><label>একজন ইউজার সর্বোচ্চ কয়টা দিতে পারবে</label>"
        f"<input name='per_user' value='{esc(str(_per))}' placeholder='যেমন 2'>"
        "<p class='mut' style='font-size:11px;margin:4px 0 0'>০ = সীমা নেই।</p></div>"
        # ⚠️ এখানে ভুল করে মেইল-কোডের "নাম | লিংক" বক্স বসে ছিল — বাদ দেওয়া হলো।
        # এখন এই পাসওয়ার্ডে আইডি (UID) জমা নেওয়ার অপশন।
        "<div class='field'><label>📝 এই পাসওয়ার্ডে আইডি (UID) জমা নিন</label>"
        "<label style='display:flex;gap:8px;align-items:center;font-size:13px'>"
        "<input type='checkbox' name='collect_ids' value='1'"
        + (" checked" if int(g.get("collect_ids") or 0) else "") +
        "> চালু — ইউজার এই পাসওয়ার্ডের সিটে শুধু নিজের আইডি (UID) জমা দেবে</label>"
        "<p class='mut' style='font-size:11px;margin:4px 0 0'>"
        "বন্ধ থাকলে আগের মতোই পাসওয়ার্ড/কুকিজ নেওয়া হবে। চালু করলে শুধু এই সিটেই "
        "আইডি জমা হবে — অন্য পাসওয়ার্ডের সিটে প্রভাব পড়বে না।</p></div>"
        "<div class='row' style='gap:8px;margin-top:12px'>"
        "<button class='ok' type='submit'>💾 সেভ করুন</button>"
        f"<a class='btn gray' href='/admin/fbtask/{tid}/passwords'>বাতিল</a></div>"
        "</form></div>"
    )


@wadm.route("/admin/fbtask/<int:tid>/passwords", methods=["GET"])
def fbtask_passwords(tid):
    """এই টাস্কের পাসওয়ার্ড-গ্রুপ — প্রতিটি পাসওয়ার্ড = আলাদা সিট গ্রুপ।"""
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "fbtasks", status=404)
    groups = many("SELECT * FROM fb_pw_groups WHERE task_id=%s ORDER BY id", (tid,))
    agg = {}
    for r in many(
            "SELECT COALESCE(fb_password,'') AS pw, COUNT(*) AS n, "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pend, "
            "SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS appr, "
            "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rej "
            "FROM fb_submissions WHERE task_id=%s GROUP BY COALESCE(fb_password,'')", (tid,)):
        agg[str(r.get("pw") or "")] = r
    # বটের ইউজারনেম — প্রতি সারির "এই পাসওয়ার্ড দিয়ে আইডি নিন" লিংকে লাগে
    _bu = ""
    try:
        for _k in ("bot_username", "bot_uname"):
            _rr = one("SELECT value FROM settings WHERE key=%s", (_k,))
            _v = str((_rr["value"] if _rr else "") or "").strip().lstrip("@")
            if _v:
                _bu = _v
                break
    except Exception:
        _bu = ""
    trs, seen = [], set()
    for g2 in groups:
        _pw = str(g2.get("password") or "")
        seen.add(_pw)
        r = agg.get(_pw) or {}
        used = int(r.get("n") or 0) - int(r.get("rej") or 0)   # ❌ সিট খালি রাখে
        seats = int(g2.get("seats_total") or 0)
        per = int(g2.get("per_user") or 0)
        full = bool(seats and used >= seats)
        _cid_on = int(g2.get("collect_ids") or 0)
        trs.append([
            f"<span class='mono'>{esc(_pw) or '-'}</span>"
            + (badge("🆔 আইডি জমা", "ok") if _cid_on else ""),
            (f"<b>{num(used)}</b> / {num(seats)}" if seats else f"{num(used)} / ∞")
            + (badge("ভরা", "err") if full else ""),
            num(per) if per else "∞",
            badge(str(int(r.get("pend") or 0)), "mut"),
            badge(str(int(r.get("appr") or 0)), "ok"),
            badge(str(int(r.get("rej") or 0)), "err"),
            (f"<a class='btn ok sm' target='_blank' "
             f"href='https://t.me/{esc(_bu)}?start=fbpw_{int(g2['id'])}'>"
             "🆔 এই পাস দিয়ে আইডি নিন</a>" if _bu else
             f"<a class='btn ok sm' href='/admin/fbtask/{tid}/pw/{int(g2['id'])}'>"
             "🆔 এই পাস দিয়ে আইডি নিন</a>"),
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/subs?pw={_q(_pw)}'>📄 শীট</a>",
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/passwords/{int(g2['id'])}/edit'>✏️ Edit</a>",
            "<form method='post' action='/admin/do/fbtask/%d/pwgroup/%d/delete' "
            "style='display:inline' onsubmit=\"return confirm('গ্রুপটা মুছে যাবে (জমাগুলো থাকবে)?')\">"
            "<button class='btn gray sm' type='submit'>🗑</button></form>"
            % (tid, int(g2["id"])),
        ])
    # গ্রুপ ছাড়া পুরোনো পাসওয়ার্ড
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
            "",   # "আইডি নিন" — গ্রুপবিহীন পুরোনো পাসওয়ার্ডে লিংক হয় না
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/subs?pw={_q(pw)}'>👁 সাবমিশন</a>",
            f"<a class='btn gray sm' href='/admin/fbtask/{tid}/passwords/new?pw={_q(pw)}'>➕ গ্রুপ বানান</a>",
            "",
        ])
    body = (
        "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
        f"<h3>🔑 পাসওয়ার্ড ও সিট — {esc(t.get('name') or '')}</h3>"
        "<div class='row' style='gap:6px'>"
        f"<a class='btn ok sm' href='/admin/fbtask/{tid}/passwords/new'>➕ নতুন পাসওয়ার্ড</a>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/subs'>⟨ সব সাবমিশন</a></div></div>"
        "<p class='mut' style='font-size:12px'>প্রতিটি পাসওয়ার্ড আলাদা গ্রুপ — নিজের সিট আর "
        "একজনের লিমিট। ইউজার টাস্ক শুরু করলে যে গ্রুপে সিট খালি আছে সেটার পাসওয়ার্ড পাবে; "
        "সিট ভরে গেলে নতুন কেউ ওই পাসওয়ার্ড পাবে না। 👁 চাপলে শুধু ওই পাসওয়ার্ডের জমা — "
        "সেখানেই ✅/❌ করা যায়।</p>"
        + table(["পাসওয়ার্ড", "আইডি / সিট", "একজন", "Pending", "Approved", "Rejected",
                 "আইডি নিন", "শীট", "Edit", "🗑"],
                trs, "কোনো পাসওয়ার্ড নেই — ➕ নতুন পাসওয়ার্ড চাপুন")
        + "</div>"
    )
    return page(f"Passwords — {t.get('name') or 'FB'}", body, "fbtasks",
                toast=request.args.get("ok"), err=request.args.get("err"))


@wadm.route("/admin/fbtask/<int:tid>/passwords/new", methods=["GET", "POST"])
def fbtask_pw_new(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "fbtasks", status=404)
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        if not pw:
            return page("নতুন পাসওয়ার্ড", _pw_group_form(t, None, tid), "fbtasks",
                        err="পাসওয়ার্ড দিন")
        _x("INSERT INTO fb_pw_groups (task_id, password, seats_total, per_user, is_active, "
           "note, collect_ids, created_at) VALUES (%s,%s,%s,%s,1, '', %s, %s)",
           (tid, pw[:80], _int_form("seats_total", 0), _int_form("per_user", 0),
            1 if request.form.get("collect_ids") else 0, _ts_now()))
        log_admin("fbtask_pw_new", tid, f"pw={pw[:20]}")
        return redirect(f"/admin/fbtask/{tid}/passwords?ok=" + _q("✅ পাসওয়ার্ড যোগ হয়েছে"))
    return page("নতুন পাসওয়ার্ড", _pw_group_form(t, None, tid), "fbtasks")


@wadm.route("/admin/fbtask/<int:tid>/passwords/<int:gid>/edit", methods=["GET", "POST"])
def fbtask_pw_edit(tid, gid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    row = one("SELECT * FROM fb_pw_groups WHERE id=%s AND task_id=%s", (gid, tid))
    if not t or not row:
        return page("Not found", "<div class='card'>পাসওয়ার্ড পাওয়া যায়নি</div>",
                    "fbtasks", status=404)
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip() or str(row.get("password") or "")
        _x("UPDATE fb_pw_groups SET password=%s, seats_total=%s, per_user=%s, "
           "collect_ids=%s WHERE id=%s AND task_id=%s",
           (pw[:80], _int_form("seats_total", 0), _int_form("per_user", 0),
            1 if request.form.get("collect_ids") else 0, gid, tid))
        log_admin("fbtask_pw_edit", tid, f"gid={gid}")
        return redirect(f"/admin/fbtask/{tid}/passwords?ok=" + _q("✅ সেভ হয়েছে"))
    return page("পাসওয়ার্ড এডিট", _pw_group_form(t, row, tid), "fbtasks")


@wadm.route("/admin/do/fbtask/<int:tid>/pwgroup/<int:gid>/delete", methods=["POST"])
def fbtask_pw_group_del(tid, gid):
    """পাসওয়ার্ড সম্পূর্ণ ডিলিট — ভেতরের সব জমাও ডেটাবেস থেকে মুছে যায়।

    ⚠️ আগে শুধু গ্রুপ রো মুছত, জমাগুলো (fb_submissions) থেকে যেত — তাই পরে
    হিসাব এলোমেলো হতো। এখন আগে ইউজারের ✅/❌ হিসাব seat_archive-এ রাখা হয়,
    তারপর জমাগুলো ডিলিট, শেষে গ্রুপ।
    """
    g = guard()
    if g:
        return g
    _init()
    _row = one("SELECT * FROM fb_pw_groups WHERE id=%s AND task_id=%s", (gid, tid))
    if not _row:
        return redirect(f"/admin/fbtask/{tid}/passwords?err=" + _q("পাসওয়ার্ড পাওয়া যায়নি"))
    _pw = str(_row.get("password") or "")
    n = 0
    try:
        n = fbtask_pw_delete_do(tid, _pw)
    except Exception:
        n = 0
    _x("DELETE FROM fb_pw_groups WHERE id=%s AND task_id=%s", (gid, tid))
    log_admin("fbtask_pw_group_del", tid, f"gid={gid} pw={_pw[:20]} rows={n}")
    return redirect(f"/admin/fbtask/{tid}/passwords?ok="
                    + _q(f"✅ পাসওয়ার্ড + ভেতরের {n} টি জমা সম্পূর্ণ ডিলিট হয়েছে"))


def fbtask_pw_delete_do(tid, pw):
    """পাসওয়ার্ডের সব সিট ডিলিট — আগে হিসাব seat_archive-এ রাখে।"""
    _pw = (pw or "").strip()
    n = 0
    try:
        _x("CREATE TABLE IF NOT EXISTS seat_archive ("
           "user_id BIGINT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, "
           "n BIGINT NOT NULL DEFAULT 0, updated_at TEXT DEFAULT '', "
           "UNIQUE(user_id, kind, status))")
        for _r in many("SELECT user_id, COALESCE(status,'pending') AS st, COUNT(*) AS n "
                       "FROM fb_submissions WHERE task_id=%s AND COALESCE(fb_password,'')=%s "
                       "GROUP BY user_id, COALESCE(status,'pending')", (int(tid), _pw)):
            hist_add(_r["user_id"] or 0, "fb", str(_r["st"]), int(_r["n"] or 0))
    except Exception:
        pass
    try:
        n = _x("DELETE FROM fb_submissions WHERE task_id=%s AND COALESCE(fb_password,'')=%s",
               (int(tid), _pw))
    except Exception:
        n = 0
    log_admin("fbtask_pw_delete", tid, f"pw={_pw[:20]} rows={n}")
    return n


@wadm.route("/admin/do/fbtask/<int:tid>/password/delete", methods=["POST"])
def fbtask_password_delete(tid):
    g = guard()
    if g:
        return g
    _init()
    _pw = (request.form.get("pw") or "").strip()
    n = fbtask_pw_delete_do(tid, _pw)
    return redirect(f"/admin/fbtask/{tid}/passwords?ok=" + _q(f"{n} টি সিট ডিলিট হয়েছে"))


@wadm.route("/admin/do/fbtask/<int:tid>/pw/<int:gid>/seat", methods=["POST"])
def fbtask_pw_seat(tid, gid):
    """এই পাসওয়ার্ডে আরও সিট যোগ — সিট ভরে গেলে নতুন ইউজার আবার এই পাসওয়ার্ড পাবে।"""
    g = guard()
    if g:
        return g
    _init()
    g2 = one("SELECT * FROM fb_pw_groups WHERE id=%s AND task_id=%s", (gid, tid))
    if not g2:
        return redirect(f"/admin/fbtask/{tid}/passwords?err=" + _q("পাসওয়ার্ড নেই"))
    try:
        n = int(request.form.get("n") or 1)
    except Exception:
        n = 1
    n = max(1, min(n, 500))
    _x("UPDATE fb_pw_groups SET seats_total=COALESCE(seats_total,0)+%s WHERE id=%s", (n, gid))
    log_admin("fbtask_pw_seat", gid, f"+{n} seats")
    _back = (request.form.get("back") or "").strip()
    if _back == "subs":
        return redirect(f"/admin/fbtask/{tid}/subs?pw=" + _q(str(g2.get("password") or ""))
                        + "&ok=" + _q(f"✅ {n} টি নতুন সিট যোগ হয়েছে"))
    return redirect(f"/admin/fbtask/{tid}/pw/{gid}?ok=" + _q(f"✅ {n} টি নতুন সিট যোগ হয়েছে"))


@wadm.route("/admin/fbtask/<int:tid>/pw/<int:gid>", methods=["GET"])
def fbtask_pw_view(tid, gid):
    """একটা পাসওয়ার্ডের সব কিছু — সিট, ⏳✅❌, আইডির তালিকা, নতুন সিট।"""
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    g2 = one("SELECT * FROM fb_pw_groups WHERE id=%s AND task_id=%s", (gid, tid))
    if not t or not g2:
        return page("Not found", "<div class='card'>পাসওয়ার্ড নেই</div>", "fbtasks", status=404)
    _pw = str(g2.get("password") or "")
    # 🔗 শীট-লিংক: t.me/<bot>?start=fbpw_<gid>
    _bot_uname = ""
    try:
        for _k in ("bot_username", "bot_uname"):
            _rr = one("SELECT value FROM settings WHERE key=%s", (_k,))
            _bu = str((_rr["value"] if _rr else "") or "").strip().lstrip("@")
            if _bu:
                _bot_uname = _bu
                break
    except Exception:
        _bot_uname = ""
    _link = (f"https://t.me/{_bot_uname}?start=fbpw_{gid}" if _bot_uname else "")
    if _link:
        _share_card = (
            "<div class='card' style='margin-top:10px'><b>🔗 এই পাসওয়ার্ড দিয়ে আইডি নিন</b>"
            "<div class='mut' style='font-size:12px;margin:6px 0'>"
            "এই লিংকটা ইউজারকে পাঠান — বটে চাপলে ঠিক এই পাসওয়ার্ডটাই পাবে, "
            "আর তার জমা এই শীটেই যাবে।</div>"
            f"<input id='pwl' value='{esc(_link)}' readonly style='width:100%' "
            "onclick='this.select()'>"
            "<div class='row' style='gap:6px;margin-top:6px'>"
            "<button class='ok sm' type='button' onclick=\"var e=document.getElementById('pwl');"
            "e.select();navigator.clipboard.writeText(e.value)\">📋 লিংক কপি</button>"
            "</div></div>"
        )
    else:
        _share_card = (
            "<div class='card' style='margin-top:10px'><b>🔗 এই পাসওয়ার্ড দিয়ে আইডি নিন</b>"
            "<div class='mut' style='font-size:12px;margin:6px 0'>"
            "বটের ইউজারনেম সেট করা নেই — Settings-এ <b>bot_username</b> দিন "
            "(যেমন TaskEarnBot), তাহলে শেয়ার লিংক তৈরি হবে।</div></div>"
        )
    _cnt = one("SELECT COUNT(*) AS n, "
               "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pend, "
               "SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) AS appr, "
               "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rej "
               "FROM fb_submissions WHERE task_id=%s AND COALESCE(fb_password,'')=%s",
               (tid, _pw)) or {}
    used = int(_cnt.get("n") or 0) - int(_cnt.get("rej") or 0)   # ❌ সিট খালি রাখে
    seats = int(g2.get("seats_total") or 0)
    per = int(g2.get("per_user") or 0)
    left = (seats - used) if seats else -1
    rows = many("SELECT id, user_id, account_no, fb_uid, status, created_at "
                "FROM fb_submissions WHERE task_id=%s AND COALESCE(fb_password,'')=%s "
                "ORDER BY id DESC LIMIT 30", (tid, _pw))
    trs = []
    for r in rows:
        trs.append([
            f"<span class='mono'>{esc(str(r.get('created_at') or ''))}</span>",
            f"<a class='mono' href='/admin/user/{int(r['user_id'])}'>{int(r['user_id'])}</a>",
            num(r.get("account_no") or 0),
            f"<span class='mono'>{esc(r.get('fb_uid') or '-')}</span>",
            st_badge(r.get("status")),
            "<a class='btn gray sm' href='/admin/fbsub/%d'>👁</a>" % int(r["id"]),
        ])
    body = (
        "<div class='card'>"
        "<div class='row' style='justify-content:space-between;align-items:center'>"
        f"<h3>🔑 <span class='mono'>{esc(_pw)}</span></h3>"
        f"<div class='row' style='gap:6px'>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/passwords'>‹ সব পাসওয়ার্ড</a>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/subs?pw={_q(_pw)}'>📄 শীট</a>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/passwords/{gid}/edit'>✏️ Edit</a>"
        "</div></div>"
        "</div></div>"
        + _share_card +

        f"<div class='card' style='flex:1;min-width:120px'><div class='mut'>আইডি / সিট</div>"
        f"<b style='font-size:20px'>{num(used)} / {num(seats) if seats else '∞'}</b>"
        + (badge("ভরা", "err") if seats and used >= seats else
           (badge(f"{num(left)} সিট খালি", "ok") if seats else ""))
        + "</div>"
        f"<div class='card' style='flex:1;min-width:120px'><div class='mut'>⏳ Pending</div>"
        f"<b style='font-size:20px'>{num(_cnt.get('pend') or 0)}</b></div>"
        f"<div class='card' style='flex:1;min-width:120px'><div class='mut'>✅ Approved</div>"
        f"<b style='font-size:20px'>{num(_cnt.get('appr') or 0)}</b></div>"
        f"<div class='card' style='flex:1;min-width:120px'><div class='mut'>❌ Rejected</div>"
        f"<b style='font-size:20px'>{num(_cnt.get('rej') or 0)}</b></div>"
        f"<div class='card' style='flex:1;min-width:120px'><div class='mut'>একজন নিতে পারবে</div>"
        f"<b style='font-size:20px'>{num(per) if per else '∞'}</b></div>"
        "</div>"
        "<div class='row' style='gap:8px;margin-top:12px;align-items:center'>"
        f"<form method='post' action='/admin/do/fbtask/{tid}/pw/{gid}/seat' class='row' "
        "style='gap:8px;align-items:center'>"
        "<input name='n' value='1' inputmode='numeric' style='max-width:90px' aria-label='কত সিট'>"
        "<button class='btn ok sm' type='submit'>➕ নতুন সিট</button>"
        "<span class='mut' style='font-size:12px'>সিট বাড়ালে বট আবার এই পাসওয়ার্ড নতুন ইউজারকে দেবে।</span>"
        "</form></div></div>"
        "<div class='card'><b>এই পাসওয়ার্ডে জমা হওয়া আইডি</b>"
        + table(["Date", "User", "Acc#", "Facebook UID", "Status", "জমা তথ্য"], trs,
                "এখনো কেউ এই পাসওয়ার্ডে আইডি জমা দেয়নি")
        + f"<div class='mut' style='font-size:12px;margin-top:6px'>সর্বশেষ ৩০টি — "
          f"<a href='/admin/fbtask/{tid}/subs?pw={_q(_pw)}'>সব দেখুন / ✅ ❌ করুন</a></div></div>")
    return page(f"পাসওয়ার্ড — {t.get('name') or 'FB'}", body, "fbtasks",
                toast=request.args.get("ok"), err=request.args.get("err"))


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
    _pw_filter = (request.args.get("pw") or "").strip()
    if _pw_filter:
        rows = many(
            "SELECT s.*, COALESCE(u.username,'') AS username FROM fb_submissions s "
            "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s "
            "AND COALESCE(s.fb_password,'')=%s "
            "ORDER BY s.id DESC LIMIT 100 OFFSET %s",
            (tid, _pw_filter, (p - 1) * 100))
    else:
        rows = many(
            "SELECT s.*, COALESCE(u.username,'') AS username FROM fb_submissions s "
            "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s "
            "ORDER BY s.id DESC LIMIT 100 OFFSET %s", (tid, (p - 1) * 100))
    trs = []
    for s in rows:
        try:
            _d = json.loads(s.get("data") or "{}")
        except Exception:
            _d = {}
        _ck = str(_d.get(_fld_key(t, "cookies"), "") or _d.get("cookies", "") or "")
        trs.append([
            f"<span class='mono'>{esc(str(s.get('created_at') or ''))}</span>",
            f"<a class='mono' href='/admin/user/{int(s['user_id'])}'>{int(s['user_id'])}</a>"
            f"<div class='mut' style='font-size:11px'>@{esc(s.get('username') or '-')}</div>",
            num(s.get("account_no") or 0),
            f"<span class='mono'>{esc(s.get('fb_uid') or '-')}</span>",
            f"<span class='mono'>{esc(s.get('fb_password') or '-')}</span>",
            (f"<span class='mono' style='font-size:11px' title='{esc(_ck)}'>"
             f"{esc(_ck[:24] + ('…' if len(_ck) > 24 else '')) or '-'}</span>"),
            esc(f"{s.get('login_first') or ''} {s.get('login_last') or ''}".strip() or "-"),
            f"<span class='mono'>{esc(s.get('login_password') or '-')}</span>",
            st_badge(s.get("status")),
            badge("Sheet ✓", "ok") if int(s.get("synced") or 0) else badge("Sheet -", "mut"),
            "<a class='btn gray sm' href='/admin/fbsub/%d' title='জমা তথ্য দেখুন'>👁</a>"
            % int(s["id"]),
            "<form method='post' action='/admin/do/fbsub/%d/approve' style='display:inline'>"
            "<button class='btn green sm'>✅</button></form>" % int(s["id"]),
            "<form method='post' action='/admin/do/fbsub/%d/reject' style='display:inline;margin-left:4px'>"
            "<button class='btn gray sm'>❌</button></form>" % int(s["id"]),
            "<form method='post' action='/admin/do/fbsub/%d/delete' style='display:inline'>"
            "<button class='btn gray sm'>🗑</button></form>" % int(s["id"]),
        ])
    _pwcard2 = ""
    if _pw_filter:
        _g3 = one("SELECT * FROM fb_pw_groups WHERE task_id=%s AND password=%s",
                  (tid, _pw_filter))
        if _g3:
            _s3 = int(_g3.get("seats_total") or 0)
            _u3 = len(rows) if len(rows) < 100 else int(
                (one("SELECT COUNT(*) AS n FROM fb_submissions WHERE task_id=%s "
                     "AND COALESCE(fb_password,'')=%s AND COALESCE(status,'pending')<>'rejected'",
                     (tid, _pw_filter)) or {}).get("n") or 0)
            _pwcard2 = (
                "<div class='card'><b>🔑 <span class='mono'>%s</span></b>"
                " <a class='btn gray sm' href='/admin/fbtask/%d/pw/%d'>পাসওয়ার্ডের নিজের পেজ ›</a>"
                "<div class='mut' style='font-size:12px;margin:6px 0'>আইডি %s / %s</div>"
                "<form method='post' action='/admin/do/fbtask/%d/pw/%d/seat' class='row' "
                "style='gap:8px;align-items:center'>"
                "<input type='hidden' name='back' value='subs'>"
                "<input name='n' value='1' inputmode='numeric' style='max-width:90px'>"
                "<button class='btn ok sm' type='submit'>➕ নতুন সিট</button></form></div>"
                % (esc(_pw_filter), tid, int(_g3["id"]), num(_u3),
                   num(_s3) if _s3 else "∞", tid, int(_g3["id"])))
    body = (
        _pwcard2
        + review_kpi(review_counts("fb_submissions", tid))
        + review_card(tid, "fb")
        + f"<div class='card'><div class='row' style='justify-content:space-between'>"
        f"<h3>{esc(t['name'])} — জমা তথ্য"
        + (f" · পাসওয়ার্ড: <span class='mono'>{esc(_pw_filter)}</span>"
           f" <a class='btn gray sm' href='/admin/fbtask/{tid}/subs'>সব দেখুন</a>"
           if _pw_filter else "") + "</h3>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/export.xlsx'>⬇️ ডাউনলোড (Excel)</a>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/export.csv'>CSV</a></div>"
        + table(["Date", "User", "Acc#", "Facebook UID", "FB Password", "Cookies",
                 "Login Name", "Login Pass", "Status", "Sheet", "জমা তথ্য", "", "", "", ""], trs,
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
    _raw_ids = request.form.get("ids") or ""
    tokens = [ln.strip() for ln in _raw_ids.splitlines() if ln.strip()]
    if not tokens:
        return redirect(f"/admin/fbtask/{tid}/subs?ok=⚠️ কোনো UID দেওয়া হয়নি")
    if action not in ("approve", "reject"):
        return redirect(f"/admin/fbtask/{tid}/subs?ok=⚠️ কাজ বোঝা যায়নি")
    done, miss = review_apply_lines(tid, _raw_ids, action, "fb_submissions", "fb_uid")
    log_admin("fbtask_review", tid, f"{action} {done}/{len(tokens)}")
    _cc = review_counts("fb_submissions", tid)
    msg = (f"{'✅ approve' if action == 'approve' else '❌ reject'}: {done} টি সিট"
           f" · pending {_cc.get('pending',0)} · approved {_cc.get('approved',0)}"
           f" · rejected {_cc.get('rejected',0)}")
    if miss:
        msg += f" · পাওয়া যায়নি {miss} টি"
    if action == "reject" and done:
        msg += " · টাকা যোগ হবে না"
    return redirect(f"/admin/fbtask/{tid}/subs?ok={msg}")


def _fb_seat_fields(t, s):
    """এই সিটের দেখানোর মতো সব তথ্য — [(লেবেল, মান)]।"""
    try:
        _d = json.loads(s.get("data") or "{}")
    except Exception:
        _d = {}
    out = []
    for f in _task_fields(t):
        k = str(f.get("key") or "")
        if not k:
            continue
        v = _d.get(k, "")
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        out.append((str(f.get("label_bn") or f.get("label_en") or k), str(v or "")))
    if not out:
        for k, v in (_d or {}).items():
            out.append((str(k), str(v or "")))
    return out


def _fb_seat_text(t, s):
    lines = [f"Task: {t.get('name') or ''}",
             f"Seat: #{int(s.get('id') or 0)}  (Account #{int(s.get('account_no') or 0)})",
             f"Facebook UID: {s.get('fb_uid') or ''}",
             f"Password: {s.get('fb_password') or ''}",
             f"Login Name: {s.get('login_first') or ''} {s.get('login_last') or ''}".strip(),
             f"Login Password: {s.get('login_password') or ''}",
             f"Status: {s.get('status') or ''}",
             f"User ID: {int(s.get('user_id') or 0)}",
             f"Time: {s.get('created_at') or ''}", ""]
    for lb, v in _fb_seat_fields(t, s):
        lines.append(f"{lb}: {v}")
    return "\n".join(lines)


@wadm.route("/admin/fbsub/<int:sid>", methods=["GET"])
def fbsub_view(sid):
    g = guard()
    if g:
        return g
    _init()
    s = one("SELECT * FROM fb_submissions WHERE id=%s", (sid,))
    if not s:
        return page("Not found", "<div class='card'>এই জমা পাওয়া যায়নি</div>",
                    "fbtasks", status=404)
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (int(s["task_id"]),)) or {}
    tid = int(s["task_id"])
    rows = [[esc(lb), f"<span class='mono'>{esc(v) if v else '-'}</span>"]
            for lb, v in _fb_seat_fields(t, s)]
    body = (
        "<div class='card'><div class='row' style='justify-content:space-between;align-items:center'>"
        f"<h3>👁 সিট #{sid} — শুধু এই পাসওয়ার্ডের জমা</h3>"
        f"<a class='btn gray sm' href='/admin/fbtask/{tid}/subs'>← তালিকায় ফিরুন</a></div>"
        f"<p class='mut' style='font-size:12px;margin:6px 0 10px'>"
        f"Task: <b>{esc(t.get('name') or '')}</b> · "
        f"UID: <span class='mono'>{esc(s.get('fb_uid') or '-')}</span> · "
        f"Password: <span class='mono'>{esc(s.get('fb_password') or '-')}</span> · "
        f"Status: {st_badge(s.get('status'))}</p>"
        + table(["তথ্য", "মান"], rows, "কোনো তথ্য নেই")
        + "<div class='row' style='gap:8px;margin-top:12px;flex-wrap:wrap'>"
        f"<a class='btn gray sm' href='/admin/fbsub/{sid}/download.txt'>⬇️ এই সিট ডাউনলোড (.txt)</a>"
        f"<a class='btn gray sm' href='/admin/fbsub/{sid}/download.csv'>⬇️ CSV</a>"
        f"<form method='post' action='/admin/do/fbsub/{sid}/approve' style='display:inline'>"
        "<button class='btn green sm' type='submit'>✅ Approve</button></form>"
        f"<form method='post' action='/admin/do/fbsub/{sid}/reject' style='display:inline'>"
        "<button class='btn gray sm' type='submit'>❌ Reject</button></form>"
        f"<form method='post' action='/admin/do/fbsub/{sid}/delete' style='display:inline' "
        "onsubmit=\"return confirm('এই সিটটি মুছে ফেলবেন?')\">"
        "<button class='btn gray sm' type='submit'>🗑 ডিলিট</button></form></div></div>"
    )
    return page(f"সিট #{sid} — {t.get('name') or 'FB'}", body, "fbtasks",
                toast=request.args.get("ok"))


@wadm.route("/admin/fbsub/<int:sid>/download.txt", methods=["GET"])
def fbsub_download_txt(sid):
    g = guard()
    if g:
        return g
    _init()
    s = one("SELECT * FROM fb_submissions WHERE id=%s", (sid,))
    if not s:
        return Response("not found", status=404, mimetype="text/plain")
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (int(s["task_id"]),)) or {}
    txt = _fb_seat_text(t, s)
    return Response(txt, mimetype="text/plain",
                    headers={"Content-Disposition":
                             f"attachment; filename=fb_seat_{sid}.txt"})


@wadm.route("/admin/fbsub/<int:sid>/download.csv", methods=["GET"])
def fbsub_download_csv(sid):
    g = guard()
    if g:
        return g
    _init()
    s = one("SELECT * FROM fb_submissions WHERE id=%s", (sid,))
    if not s:
        return Response("not found", status=404, mimetype="text/plain")
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (int(s["task_id"]),)) or {}
    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow([lb for lb, _ in _fb_seat_fields(t, s)])
    w.writerow([v for _, v in _fb_seat_fields(t, s)])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=fb_seat_{sid}.csv"})


@wadm.route("/admin/do/fbsub/<int:sid>/approve", methods=["POST"])
def fbsub_approve(sid):
    g = guard()
    if g:
        return g
    s = one("SELECT task_id FROM fb_submissions WHERE id=%s", (sid,))
    n = review_one(int(s["task_id"]), sid, "approve", "fb_submissions", "fb_uid") if s else 0
    return redirect(f"/admin/fbtask/{int(s['task_id'])}/subs?ok=" +
                    ("✅ approve হয়েছে" if n else "⚠️ আগেই প্রসেস হয়েছে") if s else "/admin/fbtasks")


@wadm.route("/admin/do/fbsub/<int:sid>/reject", methods=["POST"])
def fbsub_reject(sid):
    g = guard()
    if g:
        return g
    s = one("SELECT task_id FROM fb_submissions WHERE id=%s", (sid,))
    n = review_one(int(s["task_id"]), sid, "reject", "fb_submissions", "fb_uid") if s else 0
    return redirect(f"/admin/fbtask/{int(s['task_id'])}/subs?ok=" +
                    ("❌ reject হয়েছে" if n else "⚠️ আগেই প্রসেস হয়েছে") if s else "/admin/fbtasks")


@wadm.route("/admin/do/fbsub/<int:sid>/delete", methods=["POST"])
def fbsub_delete(sid):
    g = guard()
    if g:
        return g
    s = one("SELECT task_id, user_id, COALESCE(status,'pending') AS st FROM fb_submissions WHERE id=%s", (sid,))
    if s:
        hist_add(s.get("user_id") or 0, "fb", s.get("st") or "pending", 1)
        _x("DELETE FROM fb_submissions WHERE id=%s", (sid,))
        _x("UPDATE fb_tasks SET total_done=GREATEST(0, COALESCE(total_done,0)-1) WHERE id=%s",
           (int(s["task_id"]),))
        log_admin("fbsub_delete", sid, "facebook submission deleted")
        return redirect(f"/admin/fbtask/{int(s['task_id'])}/subs?ok=মুছে ফেলা হয়েছে")
    return redirect("/admin/fbtasks")


def _fb_export_table(t):
    """CSV আর Excel — দুটোর জন্যই একই ডেটা, কলাম-ক্রম অনুযায়ী।

    মান বের করা এখন webadmin.export_table-এ (টোকেন/ফিল্ড-টাইপ/ফাজি ম্যাচ) —
    আগে শুধু UID বসত, বাকি কলাম ফাঁকা থাকত।
    """
    try:
        _raw = json.loads(t.get("sheet_columns") or "[]")
    except Exception:
        _raw = []
    cols = _fb_norm_cols(t, _raw)          # username বাদ, 2fa/coki যোগ
    subs = many(
        "SELECT s.*, COALESCE(u.username,'') AS username FROM fb_submissions s "
        "LEFT JOIN users u ON u.user_id=s.user_id WHERE s.task_id=%s ORDER BY s.id ASC",
        (int(t["id"]),))
    return export_table(cols, subs, t, "fb")


def _fb_file_base(t, tid):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in str(t.get("name") or tid))[:40]


@wadm.route("/admin/fbtask/<int:tid>/export.xlsx", methods=["GET"])
def fbtask_export_xlsx(tid):
    g = guard()
    if g:
        return g
    _init()
    t = one("SELECT * FROM fb_tasks WHERE id=%s", (tid,))
    if not t:
        return page("Not found", "<div class='card'>Task নেই</div>", "fbtasks", status=404)
    header, rows = _fb_export_table(t)
    data = xlsx_bytes(header, rows, str(t.get("name") or ("FB Task %s" % tid)))
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=fb_{_fb_file_base(t, tid)}.xlsx"})


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
    header, rows = _fb_export_table(t)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename=fb_{_fb_file_base(t, tid)}.csv"})


# ── মেনুতে দেখাও (CONTENT সেকশন) ──
try:
    from webadmin import MENU as _MENU
    if not any(isinstance(r, tuple) and len(r) == 5 and r[0] == "fbtasks" for r in _MENU):
        _idx = next((i for i, r in enumerate(_MENU) if isinstance(r, tuple) and r[0] == "taskstats"),
                    len(_MENU))
        _MENU.insert(_idx + 1, ("fbtasks", "/admin/fbtasks", "Facebook Tasks", "task", ""))
except Exception:
    pass


# ══════════════════ Google Sheet — রেডি Apps Script (প্যানেলেই কপি) ══════════════════
SHEET_SCRIPT = """function doPost(e) {
  var d = JSON.parse(e.postData.contents);
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var name = (d.sheet || d.task || 'Sheet1').substring(0, 95);
  var sh = ss.getSheetByName(name) || ss.insertSheet(name);
  var cols = d.columns || [], row = d.row || [];
  sh.getRange(1, 1, 1, cols.length).setValues([cols]);
  sh.getRange(sh.getLastRow() + 1, 1, 1, row.length).setValues([row]);
  sh.setFrozenRows(1);
  return ContentService.createTextOutput('{"ok":true}')
    .setMimeType(ContentService.MimeType.JSON);
}
"""


def _sheet_help_body():
    """Apps Script কোড + ধাপে ধাপে নির্দেশ (কপি বাটনসহ)।"""
    steps = (
        "<ol style='line-height:1.9;font-size:13px;padding-left:20px'>"
        "<li>যে Google Sheet-এ ডেটা যাবে সেটা খুলুন।</li>"
        "<li>মেনু থেকে <b>Extensions → Apps Script</b>।</li>"
        "<li>ভেতরের সব মুছে <b>উপরের কোডটা পেস্ট</b> করুন → <b>Save</b>।</li>"
        "<li><b>Deploy → New deployment</b> → gear ⚙️ → <b>Web app</b>।"
        "<br>Execute as: <b>Me</b> · Who has access: <b>Anyone</b> → <b>Deploy</b> → Allow।</li>"
        "<li>যে লিংক (<code>.../exec</code>) আসবে কপি করুন।</li>"
        "<li>এই পেজ থেকে ফিরে টাস্কের <b>Google Sheet Web App URL</b> ঘরে লিংকটা পেস্ট করে Save।</li>"
        "<li>বটে <code>/sheettest fb &lt;id&gt;</code> দিয়ে যাচাই করুন।</li>"
        "</ol>"
        "<p class='mut' style='font-size:12px'>কোড বদলালে: <b>Deploy → Manage deployments → "
        "✏️ Edit → Version: New version → Deploy</b> (লিংক একই থাকে)।</p>"
    )
    return (
        "<div class='card'><h3>" + icon('task') + " Google Sheet — Apps Script কোড</h3>"
        "<p class='mut' style='font-size:12px'>এই কোডটা <b>বটের ভেতরে নয়</b> — শুধু Google "
        "Sheet-এর Apps Script-এ একবার বসাতে হয়। তারপর প্রতিটা টাস্কের Sheet URL ঘরে লিংক দিলেই "
        "কলাম-অর্ডার অনুযায়ী সারি জমা হবে (পাসওয়ার্ড বদলের তারিখে নতুন ট্যাব)।</p>"
        "<button class='btn' onclick=\"navigator.clipboard.writeText("
        "document.getElementById('sheet-script').innerText);"
        "this.innerText='✅ কপি হয়েছে'\">📋 কোড কপি করুন</button>"
        "<pre id='sheet-script' style='white-space:pre-wrap;background:#0f172a;color:#e2e8f0;"
        "padding:14px;border-radius:10px;font-size:12px;line-height:1.6;margin-top:12px;"
        "overflow:auto'>" + esc(SHEET_SCRIPT) + "</pre></div>"
        "<div class='card'><h3>📋 ধাপে ধাপে</h3>" + steps + "</div>"
    )


@wadm.route("/admin/sheethelp", methods=["GET", "POST"])
def sheethelp():
    g = guard()
    if g:
        return g
    ok = request.args.get("ok")
    if request.method == "POST":
        _url = (request.form.get("sheet_default_url") or "").strip()
        try:
            _x("INSERT INTO settings (key, value) VALUES ('sheet_default_url', %s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (_url,))
            log_admin("sheet_default_url", _url[:120])
        except Exception:
            pass
        return redirect("/admin/sheethelp?ok=1")
    try:
        _cur = (one("SELECT value FROM settings WHERE key='sheet_default_url'") or {}).get("value") or ""
    except Exception:
        _cur = ""
    _form = (
        "<div class='card'><h3>🔗 সব টাস্কের জন্য একটাই Sheet URL</h3>"
        "<p class='mut' style='font-size:12px'>এখানে লিংকটা একবার দিলে <b>যে টাস্কে আলাদা "
        "URL দেওয়া নেই</b> সেগুলোর সব ডেটা এই লিংকেই যাবে (প্রতিটা টাস্কের নিজের ট্যাব হবে)। "
        "টাস্কে আলাদা URL দিলে সেটাই আগে ব্যবহার হবে।</p>"
        "<form method='post'><div class='form'><label>Default Sheet URL</label>"
        "<input name='sheet_default_url' value='" + esc(_cur) + "' "
        "placeholder='https://script.google.com/macros/s/.../exec'></div>"
        "<button class='btn' type='submit'>💾 সেভ করুন</button></form></div>"
    )
    return page("Sheet Script", _form + _sheet_help_body(), "fbtasks",
                toast=("✅ সেভ হয়েছে" if ok else None))
