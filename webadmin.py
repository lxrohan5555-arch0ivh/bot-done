"""Task Earn Bot — Premium Admin Panel (core)।

Flask blueprint `wadm` — সাইটেই পুরো কন্ট্রোল রুম:
  /admin                     লগইন
  /admin/                    ড্যাশবোর্ড (KPI + চার্ট + recent users)
  /admin/users               সব ইউজার (search/sort/filter/pagination)
  /admin/verified|unverified|blocked
  /admin/user/<uid>          পুরো প্রোফাইল + টাকা/পয়েন্ট/ব্যান কন্ট্রোল
আরও পাতা webadmin2.py-তে (devices, ips, countries, alerts, tasks, earnings,
withdrawals, referrals, logs, settings)।
কোনো নতুন dependency নেই — শুধু Flask + psycopg2 (আগেই আছে)।
"""
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import time
import threading

from flask import Blueprint, Response, request, redirect, make_response

wadm = Blueprint("wadm", __name__)

SESSION_HOURS = 12
COOKIE = "teb_admin"

# ── পুরোনো/বাজে ডেটা ডিলিট — টাকা, ইউজার, রেফারেল কিছুই ছোঁবে না ────────────
# (টেবিল, টাইম-কলাম, কত দিনের পুরোনো)
JUNK_TABLES = (
    ("pending_notifications", "send_at", 3, ""),
    ("tg_task_pre_members", "seen_at", 7, ""),
    ("vrf_signals", "created_at", 3, "processed = 1"),
    ("security_alerts", "created_at", 30, ""),
    ("admin_actions", "created_at", 90, ""),
)

# ⚠️ এই টেবিলগুলো কখনো, কোনোকিছুতেই ডিলিট হয় না (মালিকের নির্দেশ)
PROTECTED_TABLES = ("devices", "user_ips", "ip_history", "ip_geo_cache")


def _junk_cut(days):
    """(epoch cut, 'YYYY-MM-DD HH:MM' cut) — দুই ফরম্যাটের টাইমকলামের জন্য।"""
    from datetime import datetime, timedelta, timezone
    days = max(1, int(days))
    cut_dt = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    return str(int(time.time()) - days * 86400), cut_dt


def _junk_where(col):
    """কলামটা epoch সংখ্যা হোক বা 'YYYY-MM-DD HH:MM' — দুটোই ধরে পুরোনো বের করে।"""
    return ("((" + col + " ~ '^[0-9]+$') AND " + col + " < %s) OR "
            "((" + col + " ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}') AND " + col + " < %s)")


def junk_preview():
    """কোন টেবিলে কত row ডিলিট হবে — ডিলিটের আগেই প্যানেলে দেখানোর জন্য।"""
    out = {}
    for row in JUNK_TABLES:
        tbl, col, days = row[0], row[1], row[2]
        extra = row[3] if len(row) > 3 else ""
        ep, dt = _junk_cut(days)
        where = _junk_where(col) + ((" AND " + extra) if extra else "")
        try:
            r = one("SELECT COUNT(*) AS n FROM " + tbl + " WHERE " + where, (ep, dt))
            out[tbl] = int((r or {}).get("n") or 0)
        except Exception:
            out[tbl] = 0
    return out


def cleanup_junk():
    """পুরোনো বেকার রেকর্ড ডিলিট — devices/IP/টাকা/রেফারেল কিছুই ছোঁবে না।

    রিটার্ন: {table: কত row মুছল}।
    """
    out = {}
    for row in JUNK_TABLES:
        tbl, col, days = row[0], row[1], row[2]
        extra = row[3] if len(row) > 3 else ""
        ep, dt = _junk_cut(days)
        where = _junk_where(col) + ((" AND " + extra) if extra else "")
        try:
            n = _x("DELETE FROM " + tbl + " WHERE " + where, (ep, dt))
            out[tbl] = int(n or 0)
        except Exception as e:
            print("[cleanup_junk] " + tbl + ": " + str(e)[:120])
            out[tbl] = 0
    return out


# ── ডেটাবেস সাইজ + সত্যিকারের space reclaim (VACUUM) ────────────────────────
def human_size(n):
    """বাইট → "12.4 MB" / "1.8 GB" ইত্যাদি।"""
    try:
        n = float(n)
    except Exception:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "—"


def db_size_bytes():
    """ডেটাবেসের মোট সাইজ (bytes)। Postgres আর CockroachDB দুটোই চেষ্টা করে।"""
    for sql in ("SELECT pg_database_size(current_database()) AS n",
                "SELECT sum(range_size) AS n FROM crdb_internal.ranges_no_leases",
                "SELECT sum(range_size) AS n FROM crdb_internal.ranges"):
        try:
            r = one(sql)
            if r and r.get("n") is not None:
                return int(r["n"])
        except Exception:
            continue
    return None


def db_table_names():
    """public schema-র সব টেবিলের নাম।"""
    try:
        rows = many("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "ORDER BY tablename")
        return [str(r["tablename"]) for r in rows if r.get("tablename")]
    except Exception:
        return []


def db_reclaim(tables=None, full=False):
    """DELETE-এর পর সত্যিই ডিস্ক থেকে জায়গা ফেরত আনে।

    Postgres-এ `DELETE` শুধু row-কে dead মার্ক করে — ফাইলের সাইজ কমে না,
    `VACUUM` না চালালে হোস্টিং প্যানেলে (Render/Neon) সাইজ একই দেখায়।
      • VACUUM (ANALYZE)            → dead space reuse-এর জন্য ছাড়ে (দ্রুত, lock নেই)
      • VACUUM (FULL, ANALYZE)      → ফাইল সত্যিই ছোট হয় (ধীর, টেবিল lock হয়)
    CockroachDB-তে VACUUM নেই (GC নিজেই করে) — ওখানে চুপচাপ skip হয়।
    """
    import re as _re
    safe = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    out = {"ok": [], "skip": [], "err": [], "full": bool(full), "supported": False}
    tbls = list(tables) if tables else db_table_names()
    if not tbls:
        return out
    pool = _get_pool()
    for t in tbls:
        if not safe.match(str(t)):
            out["skip"].append(str(t))
            continue
        conn = None
        try:
            conn = pool.getconn()
            conn.autocommit = True          # VACUUM transaction-এর ভেতরে চলে না
            cur = conn.cursor()
            cur.execute(('VACUUM (FULL, ANALYZE) "%s"' if full
                         else 'VACUUM (ANALYZE) "%s"') % t)
            cur.close()
            out["ok"].append(str(t))
            out["supported"] = True
        except Exception as e:
            msg = str(e).lower()
            if "syntax" in msg or "not supported" in msg or "unsupported" in msg:
                out["skip"].append(str(t))
            else:
                out["err"].append(str(t))
            print("[db_reclaim] " + str(t) + ": " + str(e)[:120])
        finally:
            if conn is not None:
                try:
                    conn.autocommit = False
                except Exception:
                    pass
                try:
                    pool.putconn(conn)
                except Exception:
                    pass
    return out



# ═══════════════ Excel / CSV — ঘরের মান বের করার এক জায়গা ═══════════════
# আগে টোকেন (uid/pass/coki) আর ফিল্ডের আসল key মিলত না → Excel-এ শুধু UID
# বসত, বাকি কলাম ফাঁকা থাকত। এখন টোকেন → alias → ফিল্ড-টাইপ → ফাজি ম্যাচ।
_EXPORT_ALIAS = {
    "pass": "password", "pw": "password", "pwd": "password", "fb_pass": "password",
    "ig_pass": "password", "ck": "cookies", "coki": "cookies", "cookie": "cookies",
    "totp": "2fa", "2step": "2fa", "auth2": "2fa",
    "first": "login_first", "fname": "login_first", "firstname": "login_first",
    "last": "login_last", "lname": "login_last", "lastname": "login_last",
    "id": "uid", "profile": "uid", "fb_uid": "uid", "fb_id": "uid",
    "acc_no": "account_no", "tg_id": "user_id", "telegram_id": "user_id",
    "created_at": "date", "uname": "username",
}

_EXPORT_LABELS = {
    "sl": "SL", "date": "Date", "user_id": "Telegram ID", "username": "Username",
    "tg_username": "TG Username", "task": "Task", "account_no": "Account No",
    "login_first": "First Name", "login_last": "Last Name",
    "login_password": "Login Password", "uid": "UID", "password": "Password",
    "2fa": "2FA", "cookies": "Cookies", "status": "Status",
}


def _xtask_fields(task):
    try:
        arr = json.loads((task or {}).get("fields") or "[]")
        return [f for f in arr if isinstance(f, dict)]
    except Exception:
        return []


def _xfield_type(f):
    t = str((f or {}).get("type") or "").strip().lower()
    if t:
        return t
    k = str((f or {}).get("key") or "").lower()
    if "cookie" in k or k == "ck":
        return "cookies"
    if "2fa" in k or "totp" in k or "2step" in k:
        return "2fa"
    if "pass" in k or "pwd" in k:
        return "password"
    if k in ("uid", "fb_uid", "profile"):
        return "uid"
    if "mail" in k:
        return "email"
    return "text"


def _xtype_keys(task, typ):
    """এই টাইপের ফিল্ডগুলোর আসল key (একাধিক হলে সব) — আগে একটা হলেই হতো না।"""
    return [str(f.get("key") or "") for f in _xtask_fields(task)
            if _xfield_type(f) == typ and str(f.get("key") or "").strip()]


def _xdget(data, *names):
    """data dict থেকে নাম ধরে মান — case/space উপেক্ষা করে।"""
    if not isinstance(data, dict) or not data:
        return ""
    low = {}
    for k, v in data.items():
        low[str(k).strip().lower()] = v
    for n in names:
        if not str(n or "").strip():
            continue
        nk = str(n).strip().lower()
        v = data.get(n, low.get(nk))
        if v is None:
            v = low.get(nk)
        if str(v if v is not None else "").strip():
            return v
    return ""


def _xfuzzy(data, words):
    """নাম না মিললেও key-র ভেতরে শব্দ থাকলে ধরে নাও (password/pass/pwd ইত্যাদি)।"""
    if not isinstance(data, dict):
        return ""
    for k, v in data.items():
        kk = str(k).lower()
        if any(w in kk for w in words) and str(v if v is not None else "").strip():
            return v
    return ""


def export_cell(tok, sub, data, task=None, idx=1, kind="fb"):
    """একটা ঘরের মান — টোকেন, ফিল্ড-টাইপ, ফাজি ম্যাচ, DB কলাম সব ক্রমে চেষ্টা।"""
    sub = sub or {}
    data = data if isinstance(data, dict) else {}
    tok = str(tok or "").strip()
    if not tok:
        return ""
    low = tok.lower()
    if low.startswith("f:"):                 # f:<field_key> — সরাসরি ফিল্ড
        key = tok[2:]
        v = _xdget(data, key)
        if str(v or "").strip():
            return v
        v = sub.get(key, "")
        if str(v or "").strip():
            return v
        return _xfuzzy(data, [key.lower()])
    low = _EXPORT_ALIAS.get(low, low)
    if low == "sl":
        return idx
    if low == "date":
        return sub.get("created_at", "")
    if low == "user_id":
        return sub.get("user_id", "")
    if low == "task":
        return (task or {}).get("name", "")
    if low == "account_no":
        return sub.get("account_no", "")
    if low == "status":
        return sub.get("status", "")
    if low == "tg_username":
        return sub.get("username", "")
    if low == "username":
        if kind == "ig":                      # IG-তে username = জমা দেওয়া ইউজারনেম
            v = sub.get("ig_username", "") or _xdget(data, *_xtype_keys(task, "uid"), "username")
            if str(v or "").strip():
                return v
        v = sub.get("username", "")
        if str(v or "").strip():
            return v
        return _xfuzzy(data, ["username", "uname"])
    if low == "uid":
        if kind == "ig":
            v = sub.get("ig_username", "")
            if str(v or "").strip():
                return v
        v = sub.get("fb_uid", "")
        if str(v or "").strip():
            return v
        v = _xdget(data, *_xtype_keys(task, "uid"), "uid", "fb_uid", "profile", "id")
        if str(v or "").strip():
            return v
        return _xfuzzy(data, ["uid", "profile", "_id"])
    if low == "password":
        v = sub.get("fb_password") or sub.get("ig_password") or ""
        if str(v or "").strip():
            return v
        v = _xdget(data, *_xtype_keys(task, "password"), "password", "pass", "pwd")
        if str(v or "").strip():
            return v
        v = _xfuzzy(data, ["pass", "pwd"])
        if str(v or "").strip():
            return v
        return sub.get("login_password", "")
    if low == "2fa":
        v = _xdget(data, *_xtype_keys(task, "2fa"), "2fa", "totp", "2step")
        if str(v or "").strip():
            return v
        return _xfuzzy(data, ["2fa", "totp", "2step", "auth"])
    if low == "cookies":
        v = _xdget(data, *_xtype_keys(task, "cookies"), "cookies", "coki", "ck")
        if str(v or "").strip():
            return v
        return _xfuzzy(data, ["cookie", "coki"])
    if low in ("login_first", "login_last"):
        w = "first" if low.endswith("first") else "last"
        v = sub.get(low, "")
        if str(v or "").strip():
            return v
        v = _xdget(data, low, w)
        if str(v or "").strip():
            return v
        return _xfuzzy(data, [w])
    # শেষ চেষ্টা: DB কলাম → data → ফাজি
    v = sub.get(tok, sub.get(low, ""))
    if v is not None and str(v).strip():
        return v
    v = _xdget(data, tok, low)
    if str(v or "").strip():
        return v
    return _xfuzzy(data, [low])


def export_label(tok, task=None):
    """কলামের হেডার — চেনা টোকেন হলে সুন্দর নাম, নাহলে ফিল্ডের লেবেল।"""
    tok = str(tok or "").strip()
    if tok.lower().startswith("f:"):
        key = tok[2:]
        for f in _xtask_fields(task):
            if str(f.get("key") or "") == key:
                return str(f.get("label_en") or f.get("label_bn")
                           or key.replace("_", " ").title())
        return key.replace("_", " ").title()
    low = _EXPORT_ALIAS.get(tok.lower(), tok.lower())
    if low in _EXPORT_LABELS:
        return _EXPORT_LABELS[low]
    for f in _xtask_fields(task):
        if str(f.get("key") or "").lower() == low:
            return str(f.get("label_en") or f.get("label_bn") or tok)
    return tok.replace("_", " ").title()


def _col_covers(key, cols, task=None):
    """এই data key কি কোনো কলামে ইতিমধ্যে যাচ্ছে?"""
    kl = str(key or "").strip().lower()
    if not kl:
        return True
    # এই key টাস্কের কোন ফিল্ড — তার টাইপ (cookies/2fa/password/uid)
    _ktype = ""
    for f in _xtask_fields(task):
        if str(f.get("key") or "").strip().lower() == kl:
            _ktype = _xfield_type(f)
            break
    for c in cols or []:
        cl = str(c).strip().lower()
        if cl.startswith("f:") and cl[2:] == kl:
            return True
        if _ktype and _EXPORT_ALIAS.get(cl, cl) == _ktype:
            return True
        cl = _EXPORT_ALIAS.get(cl, cl)
        if cl == kl:
            return True
        if cl == "uid" and kl in ("uid", "fb_uid", "ig_username", "profile", "id"):
            return True
        if cl == "username" and kl in ("username", "ig_username", "tg_username", "uname"):
            return True
        if cl == "password" and ("pass" in kl or "pwd" in kl):
            return True
        if cl == "2fa" and ("2fa" in kl or "totp" in kl or "2step" in kl):
            return True
        if cl == "cookies" and ("cookie" in kl or "coki" in kl or kl == "ck"):
            return True
        if cl == "login_first" and "first" in kl:
            return True
        if cl == "login_last" and "last" in kl:
            return True
    return False


def export_table(cols, subs, task=None, kind="fb"):
    """(header, rows) — কলাম অনুযায়ী সাজানো, আর কলামে না-ঢোকা ডেটাও শেষে যোগ হয়।"""
    cols = [str(c) for c in (cols or []) if str(c).strip()]
    datas = []
    for s in subs or []:
        try:
            d = json.loads(s.get("data") or "{}")
        except Exception:
            d = {}
        datas.append(d if isinstance(d, dict) else {})
    extra = []
    for d in datas:
        for k in d.keys():
            if k in extra:
                continue
            if not _col_covers(k, cols, task):
                extra.append(k)
    header = [export_label(c, task) for c in cols] + [export_label("f:" + str(k), task) for k in extra]
    rows = []
    for i, s in enumerate(subs or [], 1):
        d = datas[i - 1]
        row = [export_cell(c, s, d, task, i, kind) for c in cols]
        row += [d.get(k, "") for k in extra]
        rows.append(row)
    return header, rows


# ── Excel (.xlsx) এক্সপোর্ট — CSV-র separator/encoding সমস্যা এড়াতে ──────────
def xlsx_bytes(header, rows, sheet="Sheet1"):
    """সাধারণ .xlsx বানায় (stdlib দিয়েই, নতুন dependency নেই)।

    CSV-তে কমা/ইনকোডিং ভুল পড়ে সব এক কলামে যাওয়ার সমস্যা থাকে; .xlsx-এ
    প্রতিটা ভ্যালু আলাদা সেলে বাঁধা থাকে — Excel/Google Sheets/WPS সবখানে ঠিক খোলে।
    """
    import io as _io
    import re as _re
    import zipfile as _zf
    from xml.sax.saxutils import escape as _esc

    def _letters(n):
        out = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            out = chr(65 + r) + out
        return out

    def _cell(ref, v):
        if isinstance(v, bool):
            v = "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return '<c r="%s"><v>%s</v></c>' % (ref, v)
        txt = "" if v is None else str(v)
        txt = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", txt)[:32767]
        if not txt:
            return ""
        return ('<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                % (ref, _esc(txt)))

    all_rows = [list(header or [])] + [list(r) for r in (rows or [])]
    body = []
    for ri, r in enumerate(all_rows, 1):
        cells = "".join(_cell("%s%d" % (_letters(ci), ri), v) for ci, v in enumerate(r, 1))
        body.append('<row r="%d">%s</row>' % (ri, cells))

    name = _re.sub(r"[\[\]:*?/\\]", " ", str(sheet or "Sheet1")).strip()[:31] or "Sheet1"
    name = _esc(name, {'"': "&quot;"})

    sheet_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 '<sheetData>' + "".join(body) + '</sheetData></worksheet>')
    wb_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
              '<sheets><sheet name="' + name + '" sheetId="1" r:id="rId1"/></sheets></workbook>')
    ct_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/xl/workbook.xml" ContentType="application/'
              'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
              '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
              'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rels_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    wbrels_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                  '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                  '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')

    buf = _io.BytesIO()
    with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct_xml)
        z.writestr("_rels/.rels", rels_xml)
        z.writestr("xl/workbook.xml", wb_xml)
        z.writestr("xl/_rels/workbook.xml.rels", wbrels_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


# ───────────────────────────── CSS ─────────────────────────────
CSS = """
*{box-sizing:border-box}
:root{
 --bg:#06060c;--bg2:#0a0a13;--card:#0f0f1b;--card2:#12121f;--line:#1d1d2e;
 --tx:#eef0f8;--mut:#8b8ba3;--acc:#7c5cff;--acc2:#4f7cff;--ok:#22c55e;--warn:#f59e0b;--err:#ef4444;
 --shadow:0 18px 40px -18px rgba(0,0,0,.9);--r:16px;
}
*{scrollbar-width:thin;scrollbar-color:#2a2a40 transparent}
body{margin:0;background:radial-gradient(1200px 600px at 15% -10%,#1a1440 0%,transparent 60%),
 radial-gradient(900px 500px at 95% 0%,#102a4d 0%,transparent 55%),var(--bg);
 color:var(--tx);font:14px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Noto Sans Bengali',sans-serif;
 -webkit-font-smoothing:antialiased;min-height:100vh}
a{color:var(--acc2);text-decoration:none;transition:.15s}
a:hover{color:#8fa9ff}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.mut{color:var(--mut)}
h1,h2,h3,h4{margin:0;font-weight:650;letter-spacing:-.01em}
h1{font-size:19px}h3{font-size:15px}

/* shell */
.shell{display:flex;min-height:100vh}
.side{width:252px;flex:0 0 252px;background:linear-gradient(180deg,#0b0b16,#08080f);
 border-right:1px solid var(--line);padding:18px 14px;position:sticky;top:0;height:100vh;overflow:auto;z-index:40}
.brand{display:flex;align-items:center;gap:10px;padding:6px 8px 16px}
.logo{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;
 background:linear-gradient(135deg,var(--acc),var(--acc2));box-shadow:0 8px 22px -8px var(--acc);font-size:18px}
.brand b{display:block;font-size:14px}.brand span{font-size:11px;color:var(--mut)}
.navgrp{font-size:10.5px;letter-spacing:.12em;color:#5c5c78;text-transform:uppercase;margin:14px 10px 6px}
.nav a{display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:11px;color:#c9c9dd;
 font-size:13.5px;margin-bottom:2px;border:1px solid transparent;transition:.15s}
.nav a:hover{background:#151527;color:#fff}
.nav a.on{background:linear-gradient(90deg,rgba(124,92,255,.22),rgba(79,124,255,.10));
 color:#fff;border-color:#2c2a55;box-shadow:inset 0 0 0 1px rgba(124,92,255,.15)}
.nav a svg{width:17px;height:17px;opacity:.9;flex:0 0 17px}
.nav .pill{margin-left:auto;background:var(--err);color:#fff;font-size:10px;font-weight:700;
 padding:1px 6px;border-radius:99px}

.main{flex:1;min-width:0;display:flex;flex-direction:column}
.top{display:flex;align-items:center;gap:10px;padding:12px 20px;border-bottom:1px solid var(--line);
 background:rgba(8,8,15,.75);backdrop-filter:blur(10px);position:sticky;top:0;z-index:30}
.top h1{font-size:16px}
.search{flex:1;max-width:420px;position:relative}
.search input{width:100%;background:#0c0c17;border:1px solid var(--line);color:var(--tx);
 border-radius:11px;padding:9px 12px 9px 34px;font:inherit}
.search svg{position:absolute;left:11px;top:10px;width:15px;height:15px;color:var(--mut)}
.iconbtn{width:36px;height:36px;border-radius:11px;border:1px solid var(--line);background:#0c0c17;
 display:grid;place-items:center;color:#cfcfe4;position:relative;transition:.15s}
.iconbtn:hover{background:#151528;color:#fff}
.iconbtn svg{width:17px;height:17px}
.dot{position:absolute;top:-4px;right:-4px;background:var(--err);color:#fff;font-size:9.5px;
 font-weight:800;min-width:16px;height:16px;border-radius:99px;display:grid;place-items:center;padding:0 4px}
.who{display:flex;align-items:center;gap:8px;padding-left:6px}
.avatar{width:34px;height:34px;border-radius:50%;background:linear-gradient(135deg,var(--acc),var(--acc2));
 display:grid;place-items:center;font-weight:800;font-size:13px;color:#fff}
.burger{display:none}

.content{padding:20px;max-width:1300px;width:100%;margin:0 auto}
.grid{display:grid;gap:14px}
.g-kpi{grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.g2{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.card{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--line);
 border-radius:var(--r);padding:16px;box-shadow:var(--shadow);transition:.18s}
.card:hover{border-color:#2a2a44;transform:translateY(-1px)}
.card h3{display:flex;align-items:center;gap:8px;margin-bottom:12px}
.card h3 svg{width:16px;height:16px;color:var(--acc2)}

.kpi{position:relative;overflow:hidden}
.kpi .ic{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;margin-bottom:10px}
.kpi .ic svg{width:18px;height:18px}
.kpi .t{font-size:11.5px;color:var(--mut);letter-spacing:.02em}
.kpi .v{font-size:23px;font-weight:750;letter-spacing:-.02em;margin-top:2px}
.kpi .d{font-size:11px;color:#6f6f8c;margin-top:3px}
.kpi:after{content:"";position:absolute;right:-30px;top:-30px;width:110px;height:110px;border-radius:50%;
 background:radial-gradient(circle,rgba(124,92,255,.18),transparent 70%)}

table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#75758f;font-weight:650}
tbody tr:hover{background:#12121f}
td.num{font-variant-numeric:tabular-nums}
.tbl-wrap{overflow:auto;border-radius:12px}

.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:99px;font-size:11px;
 font-weight:600;border:1px solid transparent;white-space:nowrap}
.b-ok{color:#4ade80;background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.35)}
.b-warn{color:#fbbf24;background:rgba(245,158,11,.12);border-color:rgba(245,158,11,.35)}
.b-err{color:#fca5a5;background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.35)}
.b-blue{color:#93b4ff;background:rgba(79,124,255,.13);border-color:rgba(79,124,255,.35)}
.b-mut{color:#a3a3bd;background:#151527;border-color:var(--line)}

input,select,textarea,button{font:inherit}
input,select,textarea{background:#0b0b16;border:1px solid var(--line);color:var(--tx);
 border-radius:10px;padding:8px 10px;width:100%;transition:.15s}
input:focus,select:focus{outline:none;border-color:#3d3a6e;box-shadow:0 0 0 3px rgba(124,92,255,.16)}
button,.btn{cursor:pointer;border:none;border-radius:10px;padding:9px 13px;font-weight:650;font-size:13px;
 background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;transition:.16s;display:inline-flex;
 align-items:center;gap:6px}
button:hover{filter:brightness(1.1);transform:translateY(-1px)}
button.gray{background:#191927;color:#d3d3e6;border:1px solid var(--line)}
button.ok{background:linear-gradient(135deg,#16a34a,#22c55e)}
button.red{background:linear-gradient(135deg,#dc2626,#ef4444)}
button.sm{padding:6px 9px;font-size:12px;border-radius:8px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.f1{flex:1;min-width:150px}
.form label{display:block;font-size:11.5px;color:var(--mut);margin:0 0 5px 2px}

.seg{display:inline-flex;background:#0b0b16;border:1px solid var(--line);border-radius:10px;padding:3px;gap:3px}
.seg a{padding:6px 11px;border-radius:8px;font-size:12.5px;color:#b9b9d0}
.seg a.on{background:#1b1b2e;color:#fff}

.chart{width:100%;height:150px;display:block}
.bars{display:flex;flex-direction:column;gap:8px;margin-top:6px}
.bar{display:grid;grid-template-columns:1fr 90px;gap:10px;align-items:center;font-size:12.5px}
.bar .track{height:8px;border-radius:99px;background:#15152a;overflow:hidden}
.bar .fill{height:100%;border-radius:99px;background:linear-gradient(90deg,var(--acc),var(--acc2))}
.donut{width:132px;height:132px;border-radius:50%;position:relative;flex:0 0 132px}
.donut:after{content:"";position:absolute;inset:20px;border-radius:50%;background:#0d0d18;border:1px solid var(--line)}
.donut b{position:absolute;inset:0;display:grid;place-items:center;font-size:19px;z-index:2}

.stats-mini{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
.stat{background:#0c0c17;border:1px solid var(--line);border-radius:12px;padding:10px}
.stat span{font-size:11px;color:var(--mut)}.stat b{display:block;font-size:17px;margin-top:3px}

.empty{text-align:center;padding:34px 12px;color:var(--mut)}
.empty svg{width:34px;height:34px;opacity:.5;margin-bottom:8px}
.pager{display:flex;gap:8px;align-items:center;margin-top:12px}
.toast{position:fixed;right:18px;bottom:18px;background:#10251a;border:1px solid #22c55e66;color:#baf7cf;
 padding:11px 15px;border-radius:12px;box-shadow:var(--shadow);z-index:99;font-size:13px}
.toast.err{background:#251012;border-color:#ef444466;color:#ffc9c9}

/* login */
.login-wrap{min-height:100vh;display:grid;place-items:center;padding:20px}
.login{width:100%;max-width:400px;background:linear-gradient(180deg,#10101c,#0b0b14);
 border:1px solid var(--line);border-radius:22px;padding:28px;box-shadow:0 30px 80px -30px #000}
.login .logo{width:54px;height:54px;border-radius:16px;font-size:24px;margin:0 auto 14px}
.login h2{text-align:center;font-size:19px}
.login p.sub{text-align:center;color:var(--mut);font-size:12.5px;margin:6px 0 20px}
.field{position:relative;margin-bottom:12px}
.field svg{position:absolute;left:12px;top:11px;width:16px;height:16px;color:#6f6f8c}
.field input{padding-left:36px;height:42px}
.pw-toggle{position:absolute;right:10px;top:9px;background:none;border:none;color:#8b8ba3;padding:2px}
.pw-toggle svg{position:static;width:17px;height:17px}
.err{background:#251012;border:1px solid #ef444455;color:#ffb4b4;padding:9px 12px;border-radius:10px;
 font-size:12.5px;margin-bottom:12px}
.chk{display:flex;align-items:center;gap:8px;font-size:12.5px;color:#b9b9d0;margin:4px 0 16px}
.chk input{width:16px;height:16px;accent-color:var(--acc)}
.spin{width:15px;height:15px;border:2px solid #ffffff55;border-top-color:#fff;border-radius:50%;
 animation:sp .7s linear infinite;display:none}
@keyframes sp{to{transform:rotate(360deg)}}
.login button{width:100%;height:42px;justify-content:center;font-size:14px}
.foot{text-align:center;font-size:11px;color:#5c5c78;margin-top:16px}

.skel{height:14px;border-radius:8px;background:linear-gradient(90deg,#15152a,#1e1e35,#15152a);
 background-size:200% 100%;animation:sk 1.2s infinite}
@keyframes sk{to{background-position:-200% 0}}

@media(max-width:900px){
 .side{position:fixed;left:0;top:0;transform:translateX(-100%);transition:.22s;box-shadow:0 0 60px #000}
 .side.open{transform:none}
 .burger{display:grid}
 .top{padding:10px 12px}.content{padding:14px}
 .kpi .v{font-size:20px}
 .hide-sm{display:none}
}
"""

# ───────────────────────────── icons ─────────────────────────────
ICONS = {
    "dash": "<path d='M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z'/>",
    "users": "<path d='M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2'/><circle cx='9' cy='7' r='4'/><path d='M22 21v-2a4 4 0 0 0-3-3.87'/>",
    "check": "<path d='M20 6 9 17l-5-5'/>",
    "x": "<path d='M18 6 6 18M6 6l12 12'/>",
    "shield": "<path d='M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'/>",
    "device": "<rect x='5' y='2' width='14' height='20' rx='3'/><path d='M12 18h.01'/>",
    "globe": "<circle cx='12' cy='12' r='9'/><path d='M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18'/>",
    "alert": "<path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z'/><path d='M12 9v4M12 17h.01'/>",
    "task": "<path d='M9 11l3 3L22 4'/><path d='M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11'/>",
    "wallet": "<path d='M20 12V8H6a2 2 0 0 1 0-4h12v4'/><path d='M4 6v12a2 2 0 0 0 2 2h14v-4'/><circle cx='16' cy='14' r='1.5'/>",
    "refer": "<circle cx='18' cy='5' r='3'/><circle cx='6' cy='12' r='3'/><circle cx='18' cy='19' r='3'/><path d='M8.6 10.6 15.4 6.4M8.6 13.4l6.8 4.2'/>",
    "ban": "<circle cx='12' cy='12' r='9'/><path d='M5.6 5.6l12.8 12.8'/>",
    "logs": "<path d='M4 4h16v16H4z'/><path d='M8 8h8M8 12h8M8 16h5'/>",
    "settings": "<circle cx='12' cy='12' r='3'/><path d='M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'/>",
    "bell": "<path d='M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9'/><path d='M13.7 21a2 2 0 0 1-3.4 0'/>",
    "search": "<circle cx='11' cy='11' r='7'/><path d='m20 20-3.5-3.5'/>",
    "logout": "<path d='M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'/><path d='m16 17 5-5-5-5M21 12H9'/>",
    "menu": "<path d='M3 6h18M3 12h18M3 18h18'/>",
    "eye": "<path d='M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z'/><circle cx='12' cy='12' r='3'/>",
    "lock": "<rect x='4' y='11' width='16' height='10' rx='2'/><path d='M8 11V7a4 4 0 0 1 8 0v4'/>",
    "user": "<circle cx='12' cy='8' r='4'/><path d='M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1'/>",
    "money": "<circle cx='12' cy='12' r='9'/><path d='M15 9.5c-.6-.7-1.7-1-3-1-1.7 0-3 .8-3 2s1.3 1.7 3 2 3 .9 3 2-1.3 2-3 2c-1.3 0-2.4-.3-3-1'/><path d='M12 6v12'/>",
    "trend": "<path d='m3 17 6-6 4 4 8-8'/><path d='M17 7h4v4'/>",
    "clock": "<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>",
}


_ICON_CACHE = {}


def icon(name, cls=""):
    key = (name, cls)
    hit = _ICON_CACHE.get(key)
    if hit is not None:
        return hit
    p = ICONS.get(name, ICONS["dash"])
    out = (f"<svg class='{cls}' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
           f"stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>{p}</svg>")
    _ICON_CACHE[key] = out
    return out


def esc(v):
    return html.escape("" if v is None else str(v))


def flag(code):
    c = (code or "").strip().upper()
    if len(c) != 2 or not c.isalpha():
        return "🌐"
    try:
        return chr(0x1F1E6 + ord(c[0]) - 65) + chr(0x1F1E6 + ord(c[1]) - 65)
    except Exception:
        return "🌐"


def money(v):
    try:
        return f"{round(float(v or 0), 4):g}$"
    except Exception:
        return "0$"


def num(v):
    try:
        return f"{int(v or 0):,}"
    except Exception:
        return "0"


# ───────────────────────────── DB ─────────────────────────────
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import psycopg2.pool
                dsn = (os.environ.get("DATABASE_URL") or "").strip()
                if not dsn:
                    raise RuntimeError("DATABASE_URL সেট নেই")
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=4, maxconn=14, dsn=dsn, connect_timeout=10)
    return _pool


def _q(sql, args=(), one=False):
    import psycopg2.extras
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, args)
        rows = cur.fetchall()
        # SELECT-এর পরই transaction বন্ধ করো — idle-in-transaction CockroachDB-তে
        # পরের query গুলোকে অনেক ধীর করে দেয়
        try:
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return (rows[0] if rows else None) if one else rows
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            pool.putconn(conn, close=True)   # নষ্ট connection pool-এ ফেরত নয়
        except Exception:
            pass
        conn = None
        raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn)
            except Exception:
                pass


def _x(sql, args=()):
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        n = cur.rowcount
        conn.commit()
        return n
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


def one(sql, args=()):
    try:
        return _q(sql, args, one=True)
    except Exception:
        return None


def many(sql, args=()):
    try:
        return _q(sql, args)
    except Exception:
        return []


def scal(sql, args=(), default=0):
    key = ("i", sql, repr(args))
    hit, val = _agg_get(key)
    if hit:
        return val
    r = one(sql, args)
    res = default
    if r:
        try:
            res = int(list(r.values())[0] or 0)
        except Exception:
            res = default
    _agg_put(key, res)
    return res


def sfloat(sql, args=(), default=0.0):
    key = ("f", sql, repr(args))
    hit, val = _agg_get(key)
    if hit:
        return val
    r = one(sql, args)
    res = default
    if r:
        try:
            res = float(list(r.values())[0] or 0)
        except Exception:
            res = default
    _agg_put(key, res)
    return res


# ── aggregate ক্যাশ (ড্যাশবোর্ডের SUM/COUNT একই সংখ্যা বারবার গণে না) ──
_AGG_CACHE = {}
_AGG_TTL = 15.0
_AGG_LOCK = threading.Lock()


def _agg_get(key):
    with _AGG_LOCK:
        hit = _AGG_CACHE.get(key)
        if hit and (time.time() - hit[1]) < _AGG_TTL:
            return True, hit[0]
    return False, None


def _agg_put(key, val):
    with _AGG_LOCK:
        if len(_AGG_CACHE) > 500:
            _AGG_CACHE.clear()
        _AGG_CACHE[key] = (val, time.time())


def agg_clear():
    with _AGG_LOCK:
        _AGG_CACHE.clear()


def many_cached(key, sql, args=()):
    """১৫s ক্যাশসহ LIST query — ড্যাশবোর্ডের গ্রাফ/লিস্ট বারবার DB-তে যায় না।
    অ্যাডমিন কিছু বদলালে (hook) ক্যাশ এমনিতেই পরিষ্কার হয়।"""
    k = ("m", key, repr(args))
    hit, val = _agg_get(k)
    if hit:
        return val
    rows = many(sql, args)
    _agg_put(k, rows)
    return rows


# ── প্রতি-রিকোয়েস্ট chrome (সাইডবার alert কাউন্ট + admin key) DB-তে একবারই যাবে ──
_CHROME = {}
_CHROME_ALERTS_TTL = 8.0
_CHROME_KEY_TTL = 60.0


def alerts_changed():
    """অ্যালার্ট স্টেটাস বদলালে কাউন্ট ক্যাশে পরিষ্কার করো।

    ⚠️ আগে শুধু সাইডবারের কাউন্ট ক্যাশে পরিষ্কার হতো; KPI সংখ্যাগুলো
    (_AGG_CACHE) পুরোনোই থেকে যেত — তাই Review/Ignore চাপলে "কিছুই হয়নি"
    মনে হতো। এখন দুটোই পরিষ্কার হয়।
    """
    _CHROME.pop("alerts", None)
    try:
        agg_clear()
    except Exception:
        pass


# ── bot ↔ web ব্রিজ: প্যানেল থেকে বদলালে সাথে সাথে বটে প্রয়োগ ──
_SETTINGS_HOOK = None
_USER_HOOK = None
_REVIEW_HOOK = None


def set_settings_hook(fn):
    global _SETTINGS_HOOK
    _SETTINGS_HOOK = fn


def set_user_hook(fn):
    global _USER_HOOK
    _USER_HOOK = fn


def set_review_hook(fn):
    """বট এই hook দিয়ে FB/IG রিভিউ job সাথে সাথে চালায়।"""
    global _REVIEW_HOOK
    _REVIEW_HOOK = fn


def _bump_sync(kind, val=""):
    """বটের কাছে সিঙ্ক-সিগন্যাল (bot_meta-তে version stamp)।

    hook কাজ না করলেও (আলাদা প্রসেস/রিস্টার্ট-মাঝে) বট এই stamp দেখে ৫ সেকেন্ডে
    নিজের cache পরিষ্কার করে — তাই ওয়েবে বদলানো সব কিছু বটে ১০০% কাজ করে।
    """
    try:
        _x("INSERT INTO bot_meta (key,value) VALUES (%s,%s) "
           "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
           (kind, str(val or int(time.time() * 1000))))
    except Exception:
        pass


def _fire_review_changed():
    _bump_sync("review_ver")
    if _REVIEW_HOOK:
        try:
            _REVIEW_HOOK()
        except Exception:
            pass


def _fire_settings_changed(keys=None):
    _bump_sync("cfg_ver")
    agg_clear()
    _CHROME.pop("key", None)     # admin key বদলালে ক্যাশেও সাথে সাথে
    try:
        import panel as _p
        try:
            _p._settings_cache.clear()
        except Exception:
            pass
    except Exception:
        pass
    if _SETTINGS_HOOK:
        try:
            _SETTINGS_HOOK(list(keys or []))
        except Exception:
            pass


def _fire_user_changed(uid):
    _bump_sync("user_ver", f"{int(uid)}:{int(time.time() * 1000)}")
    agg_clear()
    if _USER_HOOK:
        try:
            _USER_HOOK(int(uid))
        except Exception:
            pass


@wadm.after_request
def _gzip_resp(resp):
    """HTML gzip — মোবাইল/স্লো নেটওয়ার্কে অনেক দ্রুত লোড।"""
    try:
        if resp.direct_passthrough or resp.status_code != 200:
            return resp
        if not (resp.mimetype or "").startswith("text/"):
            return resp
        if "gzip" not in (request.headers.get("Accept-Encoding") or ""):
            return resp
        body = resp.get_data()
        if len(body) < 1200:
            return resp
        import gzip
        out = gzip.compress(body, 6)
        if len(out) >= len(body):
            return resp
        resp.set_data(out)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(out))
        resp.headers.add("Vary", "Accept-Encoding")
    except Exception:
        pass
    return resp


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _norm_ip(raw):
    """IP নরমালাইজ: পোর্ট/ব্র্যাকেট বাদ, IPv4-mapped IPv6 → IPv4, ভুল হলে ''।"""
    ip = (raw or "").strip()
    if not ip:
        return ""
    if "," in ip:                               # XFF লিস্ট হলে প্রথমটা
        ip = ip.split(",")[0].strip()
    if ip.startswith("["):
        ip = ip[1:ip.find("]")] if "]" in ip else ip[1:]
    elif ip.count(":") == 1 and "." in ip:
        ip = ip.split(":")[0]
    try:
        import ipaddress
        a = ipaddress.ip_address(ip)
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        return str(a)
    except Exception:
        return ""


def client_ip():
    """আসল ক্লায়েন্ট IP — প্রক্সি হেডার আগে, তারপর remote_addr।"""
    try:
        for h in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
            raw = (request.headers.get(h) or "").strip()
            if not raw:
                continue
            for part in raw.split(","):
                ip = _norm_ip(part)
                if ip:
                    return ip
        return _norm_ip(request.remote_addr or "")
    except Exception:
        return ""


def log_admin(action, target=0, detail=""):
    try:
        _x("INSERT INTO admin_actions (admin_id, action, target_uid, detail, ip, created_at) "
           "VALUES (%s,%s,%s,%s,%s,%s)",
           (0, action, int(target or 0), str(detail)[:300], client_ip(), now()))
    except Exception:
        pass


# ───────────────────────────── auth ─────────────────────────────
def admin_key():
    k = (os.environ.get("ADMIN_WEB_KEY") or "").strip()
    if k:
        return k
    _ck, _ts = _CHROME.get("key", ("", 0.0))
    if _ck and (time.time() - _ts) < _CHROME_KEY_TTL:
        return _ck
    r = one("SELECT value FROM settings WHERE key=%s", ("admin_web_key",))
    k = (r["value"] if r else "") or ""
    if not k:
        k = base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip("=")
        try:
            _x("INSERT INTO settings (key, value) VALUES ('admin_web_key', %s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (k,))
        except Exception:
            pass
    try:
        _CHROME["key"] = (k, time.time())
    except Exception:
        pass
    return k


def admin_user():
    return (os.environ.get("ADMIN_WEB_USER") or "admin").strip()


def _sign(exp):
    return hmac.new(admin_key().encode(), str(exp).encode(), hashlib.sha256).hexdigest()[:32]


def _cookie_ok(val):
    try:
        exp_s, sig = (val or "").split(".", 1)
        if int(exp_s) < int(time.time()):
            return False
        return hmac.compare_digest(sig, _sign(exp_s))
    except Exception:
        return False


def authed():
    return _cookie_ok(request.cookies.get(COOKIE, ""))


def guard():
    return None if authed() else login_page()


# ───────────────── panel security (gate · throttle · headers) ─────────────────
COOKIE_GATE     = "teb_gate"
_GATE_LABEL     = "teb-panel-gate-v1"
_LOGIN_FAILS    = {}          # ip → {"n": ভুল সংখ্যা, "until": lock শেষ সময়}
_LOGIN_LOCK     = threading.Lock()
LOGIN_MAX_FAILS = 5           # ৫ বার ভুল → ১৫ মিনিট লক
LOGIN_LOCK_MIN  = 15


def _is_https():
    try:
        if request.is_secure:
            return True
        return (request.headers.get("X-Forwarded-Proto") or "").lower() == "https"
    except Exception:
        return False


def panel_secret():
    """প্যানেলের গোপন এন্ট্রি-কী — `/admin?k=<secret>` ছাড়া প্যানেল খোলে না।"""
    s = (os.environ.get("PANEL_URL_SECRET") or "").strip()
    if s:
        return s
    _ck, _ts = _CHROME.get("psecret", ("", 0.0))
    if _ck and (time.time() - _ts) < _CHROME_KEY_TTL:
        return _ck
    try:
        r = one("SELECT value FROM settings WHERE key=%s", ("panel_url_secret",))
        s = ((r["value"] if r else "") or "").strip()
        if not s:
            s = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
            _x("INSERT INTO settings (key,value) VALUES ('panel_url_secret',%s) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (s,))
        _CHROME["psecret"] = (s, time.time())
        return s
    except Exception:
        return ""


def _gate_token(secret):
    return hmac.new(secret.encode(), _GATE_LABEL.encode(), hashlib.sha256).hexdigest()


def _gate_ok():
    try:
        s = panel_secret()
        if not s:
            return True
        c = request.cookies.get(COOKIE_GATE, "")
        return bool(c) and hmac.compare_digest(c, _gate_token(s))
    except Exception:
        return True          # DB সমস্যায় অ্যাডমিন যেন লকআউট না হয়


def _login_blocked(ip):
    """লক থাকলে কত মিনিট বাকি — 0 মানে ব্লক নেই।"""
    with _LOGIN_LOCK:
        e = _LOGIN_FAILS.get(ip)
        if not e:
            return 0
        left = int(e.get("until", 0) - time.time())
    return (left // 60) + 1 if left > 0 else 0


def _login_fail(ip):
    with _LOGIN_LOCK:
        if len(_LOGIN_FAILS) > 2000:
            _LOGIN_FAILS.clear()
        e = _LOGIN_FAILS.setdefault(ip, {"n": 0, "until": 0.0})
        e["n"] = int(e.get("n", 0)) + 1
        if e["n"] >= LOGIN_MAX_FAILS:
            e["until"] = time.time() + LOGIN_LOCK_MIN * 60
        return e["n"], e["n"] >= LOGIN_MAX_FAILS


def _login_ok(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(ip, None)


log = logging.getLogger("webadmin")


@wadm.before_request
def _panel_security_gate():
    """১) গোপন লিংক (?k=) ২) CSRF (cross-site POST ব্লক)।"""
    if not request.path.startswith("/admin"):
        return None
    try:
        request.environ["_teb_t0"] = time.time()     # ⏱️ স্লো পেজ খুঁজতে
    except Exception:
        pass
    secret = ""
    try:
        secret = panel_secret()
    except Exception:
        secret = ""
    if secret:
        got = (request.args.get("k") or "").strip()
        if got and hmac.compare_digest(got, secret):
            args = request.args.to_dict(flat=True)
            args.pop("k", None)
            from urllib.parse import urlencode
            qs = urlencode(args)
            resp = redirect(request.path + (("?" + qs) if qs else ""))
            resp.set_cookie(COOKIE_GATE, _gate_token(secret), max_age=365 * 86400,
                            httponly=True, samesite="Strict",
                            secure=_is_https(), path="/")
            try:
                log_admin("panel_gate_open", 0, client_ip())
            except Exception:
                pass
            return resp
        # ⚠️ আগে গেট মিললেই 404 দেওয়া হতো — মালিকের পুরোনো/বুকমার্ক করা
        # `/admin` লিংক (বা PANEL_URL_SECRET বদলালে) পুরো প্যানেল লক করে দিত।
        # এখন `/admin` সবসময় লগইন পেজ দেখায়; আসল তালা = পাসওয়ার্ড +
        # ব্রুট-ফোর্স লক + CSRF + সিকিউরিটি হেডার। `?k=` লিংক আগের মতোই
        # গেট-কুকি বসিয়ে দেয় (এক ক্লিকে ঢোকার সুবিধা)।
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        org = (request.headers.get("Origin") or request.headers.get("Referer") or "")
        if org:
            try:
                from urllib.parse import urlparse
                netloc = (urlparse(org).netloc or "").lower()
            except Exception:
                netloc = ""
            if netloc and netloc != (request.host or "").lower():
                return Response("Forbidden — cross-site request blocked",
                                status=403, mimetype="text/plain")
    return None


@wadm.after_request
def _panel_security_headers(resp):
    if not request.path.startswith("/admin"):
        return resp
    try:
        _t0p = request.environ.get("_teb_t0")
        if _t0p:
            _dtp = time.time() - _t0p
            resp.headers["X-Resp-Time"] = f"{_dtp:.2f}s"
            if _dtp > 1.5:
                log.warning(f"[WEB-SLOW] {request.method} {request.path} {_dtp:.2f}s")
    except Exception:
        pass
    try:
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy",
                                "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers.setdefault("Cache-Control", "no-store, max-age=0, must-revalidate")
        if _is_https():
            resp.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000; includeSubDomains")
    except Exception:
        pass
    return resp


# ───────────────────────────── layout ─────────────────────────────
MENU = [
    ("MAIN", []),
    ("dash", "/admin/", "Dashboard", "dash", ""),
    ("users", "/admin/users", "Users", "users", ""),
    ("verified", "/admin/verified", "Verified Users", "check", ""),
    ("unverified", "/admin/unverified", "Unverified Users", "x", ""),
    ("SECURITY", []),
    ("devices", "/admin/devices", "Device Management", "device", ""),
    ("ips", "/admin/ips", "IP Tracking", "globe", ""),
    ("countries", "/admin/countries", "Country Statistics", "globe", ""),
    ("alerts", "/admin/alerts", "Security Alerts", "alert", "alerts"),
    ("CONTENT", []),
    ("tasks", "/admin/tasks", "Task Management", "task", ""),
    ("taskstats", "/admin/task-stats", "Task Statistics", "trend", ""),
    ("history", "/admin/history", "Task History", "task", ""),
    ("shop", "/admin/shop", "Shop / দোকান", "money", ""),
    ("shopnum", "/admin/shop/number", "Number / OTP", "device", ""),
    ("shopproxy", "/admin/shop/proxy", "Proxy Checker", "globe", ""),
    ("smm", "/admin/smm", "SMM Panel", "globe", ""),
    ("deposits", "/admin/deposits", "Deposits", "wallet", ""),
    ("earnings", "/admin/earnings", "Earnings", "money", ""),
    ("withdrawals", "/admin/withdrawals", "Withdrawals", "wallet", ""),
    ("referrals", "/admin/referrals", "Referrals", "refer", ""),
    ("blocked", "/admin/blocked", "Blocked Users", "ban", ""),
    ("OTP / APIS", []),
    ("otp", "/admin/otp", "OTP Earn", "money", ""),
    ("shoplamix", "/admin/lamix", "Lamix SMS API", "globe", ""),
    ("SYSTEM", []),
    ("logs", "/admin/logs", "Admin Activity Logs", "logs", ""),
    ("storage", "/admin/storage", "Data & Storage", "logs", ""),
    ("settings", "/admin/settings", "Settings", "settings", ""),
]


def unread_alerts():
    """সাইডবার/বেলের কাউন্ট — ৮ সেকেন্ড ক্যাশ (প্রতি পেজে একটা extra DB হিট বাঁচে)।"""
    n, ts = _CHROME.get("alerts", (None, 0.0))
    if n is not None and (time.time() - ts) < _CHROME_ALERTS_TTL:
        return n
    n = scal("SELECT COUNT(*) FROM security_alerts WHERE status='new'")
    _CHROME["alerts"] = (n, time.time())
    return n


def layout(title, body, active="", toast=None, err=None):
    alerts_n = unread_alerts()
    items = []
    for row in MENU:
        if len(row) == 2:
            items.append(f"<div class='navgrp'>{esc(row[0])}</div>")
            continue
        key, href, label, ic, badge = row
        pill = ""
        if badge == "alerts" and alerts_n:
            pill = f"<span class='pill'>{alerts_n}</span>"
        items.append(
            f"<a class='{'on' if active == key else ''}' href='{href}'>"
            f"{icon(ic)}<span>{esc(label)}</span>{pill}</a>"
        )
    side = ("<aside class='side' id='side'>"
            "<div class='brand'><div class='logo'>⚡</div><div><b>Task Earn</b>"
            "<span>Admin Console</span></div></div>"
            f"<nav class='nav'>{''.join(items)}</nav></aside>")
    bell = ("<a class='iconbtn' href='/admin/alerts' title='Notifications'>"
            f"{icon('bell')}" + (f"<span class='dot'>{alerts_n}</span>" if alerts_n else "") + "</a>")
    top = ("<header class='top'>"
           "<button class='iconbtn burger' onclick=\"document.getElementById('side').classList.toggle('open')\" "
           f"aria-label='menu'>{icon('menu')}</button>"
           f"<h1 class='hide-sm'>{esc(title)}</h1>"
           "<form class='search' method='get' action='/admin/users'>"
           f"{icon('search')}<input name='q' placeholder='Search user id / name / IP…'>"
           "</form>"
           f"{bell}"
           "<a class='iconbtn' href='/admin/mail' title='Mail auto-deposit'>✉️</a>"
           "<a class='iconbtn' href='/admin/settings' title='Settings'>" + icon("settings") + "</a>"
           "<div class='who'><div class='avatar'>A</div>"
           "<div class='hide-sm'><b style='font-size:12.5px'>Admin</b>"
           "<div class='mut' style='font-size:10.5px'>" + esc(admin_user()) + "</div></div></div>"
           "<a class='iconbtn' href='/admin/logout' title='Logout'>" + icon("logout") + "</a>"
           "</header>")
    toast_html = ""
    if toast:
        toast_html = f"<div class='toast' id='toast'>✅ {esc(toast)}</div>"
    if err:
        toast_html += f"<div class='toast err' id='toast2'>⚠️ {esc(err)}</div>"
    js = ("<script>setTimeout(function(){var t=document.getElementById('toast');"
          "if(t)t.remove();var e=document.getElementById('toast2');if(e)e.remove();},3200);"
          "document.querySelectorAll('form[data-confirm]').forEach(function(f){"
          "f.addEventListener('submit',function(e){"
          "if(!confirm(f.getAttribute('data-confirm')))e.preventDefault();});});</script>")
    return ("<!doctype html><html lang='bn'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='robots' content='noindex,nofollow'>"
            f"<title>{esc(title)} — Task Earn Admin</title><style>{CSS}</style></head><body>"
            f"<div class='shell'>{side}<div class='main'>{top}"
            f"<div class='content'>{body}</div></div></div>{toast_html}{js}</body></html>")


def page(title, body, active="", toast=None, err=None, status=200):
    return Response(layout(title, body, active, toast, err), status=status, mimetype="text/html")


def login_page(err="", status=None):
    body = (
        "<div class='login-wrap'><form class='login' method='post' action='/admin/login'>"
        "<div class='logo'>⚡</div>"
        "<h2>Task Earn Bot</h2>"
        "<p class='sub'>Admin Panel — authorized access only</p>"
        + (f"<div class='err'>{esc(err)}</div>" if err else "")
        + "<div class='field'>" + icon("user") +
        "<input name='username' placeholder='Username' value='admin' autocomplete='username' required></div>"
        "<div class='field'>" + icon("lock") +
        "<input id='pw' name='password' type='password' placeholder='Password' "
        "autocomplete='current-password' required>"
        "<button type='button' class='pw-toggle' onclick=\"var p=document.getElementById('pw');"
        "p.type=p.type==='password'?'text':'password'\">" + icon("eye") + "</button></div>"
        "<label class='chk'><input type='checkbox' name='remember' value='1'> Remember me for 7 days</label>"
        "<button type='submit' id='lgbtn'><span class='spin' id='sp'></span> Secure Login</button>"
        "<p class='foot'>🔒 Key না জানলে বটে <b>/webadmin</b> পাঠান</p>"
        "</form></div>"
        "<script>document.querySelector('form.login').addEventListener('submit',function(){"
        "document.getElementById('sp').style.display='inline-block';"
        "document.getElementById('lgbtn').style.opacity='.8';});</script>"
    )
    return Response(
        "<!doctype html><html lang='bn'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'><title>Admin Login</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        status=status or (401 if err else 200), mimetype="text/html")


@wadm.route("/admin", methods=["GET"])
def root():
    return redirect("/admin/") if authed() else login_page()


@wadm.route("/admin/login", methods=["POST"])
def login():
    ip = client_ip()
    wait = _login_blocked(ip)
    if wait:
        return login_page(f"অনেকবার ভুল হয়েছে — {wait} মিনিট পর আবার চেষ্টা করুন।", status=429)
    u = (request.form.get("username") or "").strip()
    p = (request.form.get("password") or "").strip()
    ok_user = u.lower() in (admin_user().lower(), "admin")
    ok_pass = False
    try:
        ok_pass = hmac.compare_digest(p, admin_key())
    except Exception:
        ok_pass = False
    if not (ok_user and ok_pass):
        time.sleep(0.5)                      # brute-force ধীর করো
        n, locked = _login_fail(ip)
        left = max(0, LOGIN_MAX_FAILS - n)
        if locked:
            msg = f"অনেকবার ভুল — {LOGIN_LOCK_MIN} মিনিটের জন্য ব্লক করা হলো।"
        else:
            msg = f"ভুল username বা password। আর {left} বার ভুল হলে {LOGIN_LOCK_MIN} মিনিট ব্লক হবে।"
        try:
            log_admin("web_login_fail", 0, ip)
        except Exception:
            pass
        return login_page(msg, status=429 if locked else 401)
    _login_ok(ip)
    days = 7 if request.form.get("remember") else SESSION_HOURS / 24
    exp = int(time.time()) + int(days * 86400)
    r = make_response(redirect("/admin/"))
    r.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", max_age=int(days * 86400),
                 httponly=True, samesite="Strict", secure=_is_https(), path="/")
    log_admin("web_login", 0, ip)
    try:
        tg_admin_notify(f"🔐 <b>Web panel login</b>\n\n👤 <code>{esc(u)}</code>"
                        f"\n🌐 IP: <code>{esc(ip)}</code>\n\n✅ <b>অ্যাপ্রুভ পেলেই লগইন হবে</b> — বট admin approve করলেই সেশন ওপেন হবে।")
    except Exception:
        pass
    return r


@wadm.route("/admin/logout", methods=["GET"])
def logout():
    r = make_response(redirect("/admin"))
    r.delete_cookie(COOKIE)
    return r


# ───────────────────────────── UI blocks ─────────────────────────────
def kpi(title, value, desc="", ic="dash", color="#7c5cff"):
    return (f"<div class='card kpi'><div class='ic' style='background:{color}22;color:{color}'>"
            f"{icon(ic)}</div><div class='t'>{esc(title)}</div>"
            f"<div class='v'>{esc(value)}</div>"
            + (f"<div class='d'>{esc(desc)}</div>" if desc else "") + "</div>")


def table(headers, rows, empty="কোনো ডেটা নেই"):
    if not rows:
        return (f"<div class='empty'>{icon('logs')}<div>{esc(empty)}</div></div>")
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='tbl-wrap'><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>"


# ───────────────── FB / IG জমা রিভিউ (approve · reject) ─────────────────
def st_badge(st):
    """জমার স্টেটাস ব্যাজ।"""
    st = str(st or "pending").strip().lower()
    if st == "approved":
        return badge("✅ approved", "ok")
    if st == "rejected":
        return badge("❌ rejected", "err")
    return badge("⏳ pending", "warn")


def review_card(tid, kind="fb"):
    """UID/Username লিস্ট দিয়ে approve বা reject — এক ফর্মে দুই বাটন।"""
    is_fb = (kind == "fb")
    idname = "Facebook UID" if is_fb else "Instagram Username"
    act = (f"/admin/do/fbtask/{tid}/review" if is_fb
           else f"/admin/do/igtask/{tid}/review")
    ph = ("61595645847047|Pass123&#10;61595645847047|Other456"
          if is_fb else "johhrpv67|Pass123&#10;tania_9|Pass456")
    try:
        _c = review_counts("fb_submissions" if is_fb else "ig_submissions", tid)
        _tot = int(_c.get("pending", 0) + _c.get("approved", 0) + _c.get("rejected", 0))
    except Exception:
        _tot = 0
    return (
        "<div class='card'>"
        "<h3>✅ Approve / ❌ Reject</h3>"
        f"<p class='mut' style='font-size:12px;margin:6px 0 10px'>"
        f"<b>সিট = {idname} + পাসওয়ার্ড</b> — একই UID-র পাসওয়ার্ড আলাদা হলে সেটা "
        f"<b>আলাদা সিট</b>, আলাদা approve/reject, আলাদা হিসাব। "
        f"মোট সিট এখন: <b>{_tot}</b><br>"
        f"লিখুন প্রতি লাইনে <code>UID|পাসওয়ার্ড</code> — শুধু ওই পাসওয়ার্ডের সিটটাই "
        f"approve/reject হবে। শুধু <code>UID</code> দিলে ওই UID-র প্রথম pending সিট, "
        f"আর <code>#12</code> দিলে ওই সারির সিট। "
        f"প্রতিটি সারির পাশেও আলাদা ✅ / ❌ / 🗑 বাটন আছে।</p>"
        f"<form method='post' action='{act}'>"
        f"<textarea name='ids' rows='5' style='width:100%;padding:10px;border-radius:11px' "
        f"placeholder='{ph}'></textarea>"
        "<div class='row' style='margin-top:10px;gap:8px'>"
        "<button class='btn' name='action' value='approve'>✅ Approve</button>"
        "<button class='btn gray' name='action' value='reject'>❌ Reject</button></div>"
        "</form></div>"
    )


def review_tokens(raw):
    """লাইনে/কমায়/স্পেসে লেখা UID বা Username — ডুপ্লিকেট বাদ।"""
    import re as _re
    out, seen = [], set()
    for part in _re.split(r"[\s,;]+", str(raw or "")):
        t = part.strip().lstrip("@").strip()
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def review_apply(tid, tokens, action, table, idcol):
    """token → pending জমা approve/reject। (done, not_found) ফেরত দেয়।"""
    if table not in ("fb_submissions", "ig_submissions"):
        return 0, len(tokens)
    if idcol not in ("fb_uid", "ig_username"):
        return 0, len(tokens)
    status = "approved" if action == "approve" else "rejected"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    done = 0
    for tk in tokens:
        r = one(f"SELECT id FROM {table} WHERE task_id=%s AND lower(trim({idcol}))=lower(%s) "
                "AND status='pending' ORDER BY id LIMIT 1", (tid, tk))
        if not r:
            continue
        _x(f"UPDATE {table} SET status=%s, notified=0, reviewed_at=%s "
           "WHERE id=%s AND status='pending'", (status, stamp, int(r["id"])))
        done += 1
    if done:
        _fire_review_changed()      # বট সাথে সাথে টাকা + মেসেজ পাঠাবে
    return done, len(tokens) - done


def uid_lock_reason(table, tid, sid, idcol):
    """এই সিটের UID আগেই approve/rejected হলে কারণ ফেরত দাও (নাহলে '')।"""
    if table not in ("fb_submissions", "ig_submissions") or idcol not in ("fb_uid", "ig_username"):
        return ""
    try:
        r = one(f"SELECT a.status FROM {table} a WHERE a.task_id=%s AND a.id<>%s "
                f"AND a.status IN ('approved','rejected') AND lower(trim(a.{idcol})) = "
                f"(SELECT lower(trim(b.{idcol})) FROM {table} b WHERE b.id=%s) LIMIT 1",
                (int(tid), int(sid), int(sid)))
        return str((r or {}).get("status") or "")
    except Exception:
        return ""


def review_one(tid, sid, action, table, idcol=""):
    """একটি নির্দিষ্ট সিট (id) approve/reject — একই UID-তে ভিন্ন পাসওয়ার্ড হলেও আলাদা।"""
    if table not in ("fb_submissions", "ig_submissions"):
        return 0
    if action == "approve" and uid_lock_reason(table, tid, sid, idcol):
        return 0
    status = "approved" if action == "approve" else "rejected"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    n = _x(f"UPDATE {table} SET status=%s, notified=0, reviewed_at=%s "
           "WHERE id=%s AND task_id=%s AND status='pending'", (status, stamp, int(sid), int(tid)))
    if n:
        _fire_review_changed()
    return int(n or 0)


def review_apply_lines(tid, raw, action, table, idcol):
    """লাইন-ভিত্তিক review:
       • `#123`        → শুধু ওই সিট (id)
       • `uid|password`→ ওই UID-র ঠিক ওই পাসওয়ার্ডের সিট
       • `uid`         → পুরোনো আচরণ (প্রথম pending)
    """
    if table not in ("fb_submissions", "ig_submissions") or idcol not in ("fb_uid", "ig_username"):
        return 0, 0
    pwcol = "fb_password" if idcol == "fb_uid" else "ig_password"
    status = "approved" if action == "approve" else "rejected"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    done = miss = 0
    for line in str(raw or "").splitlines():
        tk = line.strip().strip(",").strip()
        if not tk:
            continue
        row = None
        if tk.startswith("#") and tk[1:].strip().isdigit():
            row = one(f"SELECT id FROM {table} WHERE id=%s AND task_id=%s "
                      "AND status='pending'", (int(tk[1:].strip()), int(tid)))
        elif "|" in tk:
            _u, _p = tk.split("|", 1)
            row = one(f"SELECT id FROM {table} WHERE task_id=%s "
                      f"AND lower(trim({idcol}))=lower(%s) "
                      f"AND COALESCE({pwcol},'')=%s AND status='pending' "
                      "ORDER BY id LIMIT 1",
                      (int(tid), _u.strip(), _p.strip()))
        elif tk.isdigit():
            row = one(f"SELECT id FROM {table} WHERE task_id=%s AND lower(trim({idcol}))=lower(%s) "
                      "AND status='pending' ORDER BY id LIMIT 1", (int(tid), tk))
        if not row and "," in tk:
            for part in [x.strip() for x in tk.split(",") if x.strip()]:
                if part.isdigit():
                    row = one(f"SELECT id FROM {table} WHERE task_id=%s "
                              f"AND lower(trim({idcol}))=lower(%s) AND status='pending' "
                              "ORDER BY id LIMIT 1", (int(tid), part))
                    if row:
                        break
        if not row:
            miss += 1
            continue
        if action == "approve" and uid_lock_reason(table, tid, int(row["id"]), idcol):
            miss += 1
            continue
        _x(f"UPDATE {table} SET status=%s, notified=0, reviewed_at=%s "
           "WHERE id=%s AND status='pending'", (status, stamp, int(row["id"])))
        done += 1
    if done:
        _fire_review_changed()
    return done, miss


def hist_add(user_id, kind, status, n=1):
    """ডিলিট হওয়া সারির হিসাব — বটের প্রোফাইলে চিরকাল দেখাবে।"""
    try:
        _x("CREATE TABLE IF NOT EXISTS user_hist (user_id BIGINT NOT NULL, kind TEXT NOT NULL, "
           "status TEXT NOT NULL, n BIGINT NOT NULL DEFAULT 0, updated_at TEXT DEFAULT '', "
           "UNIQUE(user_id, kind, status))")
        _x("INSERT INTO user_hist (user_id,kind,status,n,updated_at) VALUES (%s,%s,%s,%s,%s) "
           "ON CONFLICT (user_id,kind,status) DO UPDATE SET "
           "n = user_hist.n + EXCLUDED.n, updated_at = EXCLUDED.updated_at",
           (int(user_id or 0), str(kind), str(status or "pending"), int(n or 0),
            time.strftime("%Y-%m-%d %H:%M")))
        return True
    except Exception:
        return False


def review_counts(table, tid, pw=None):
    """pending / approved / rejected কাউন্ট।

    `pw` দিলে শুধু ওই পাসওয়ার্ডের (শীটের) জমাগুলোর হিসাব — আগে সব পাসওয়ার্ডের
    জমা মিলিয়ে দেখানো হতো, তাই একটার শীটে ঢুকলেও সবার Approved দেখাত।
    """
    cnt = {"pending": 0, "approved": 0, "rejected": 0}
    if table not in ("fb_submissions", "ig_submissions"):
        return cnt
    _pw = str(pw or "").strip()
    if _pw:
        for r in many(f"SELECT status, COUNT(*) AS n FROM {table} WHERE task_id=%s "
                      "AND COALESCE(fb_password,'')=%s GROUP BY status", (tid, _pw)):
            cnt[str(r.get("status") or "pending").strip().lower()] = int(r.get("n") or 0)
        return cnt
    for r in many(f"SELECT status, COUNT(*) AS n FROM {table} WHERE task_id=%s "
                  "GROUP BY status", (tid,)):
        cnt[str(r.get("status") or "pending").strip().lower()] = int(r.get("n") or 0)
    return cnt


def review_kpi(cnt):
    return (
        "<div class='grid g-kpi'>"
        f"<div class='card kpi'><div class='t'>⏳ Pending</div><div class='v'>{num(cnt['pending'])}</div></div>"
        f"<div class='card kpi'><div class='t'>✅ Approved</div><div class='v'>{num(cnt['approved'])}</div></div>"
        f"<div class='card kpi'><div class='t'>❌ Rejected</div><div class='v'>{num(cnt['rejected'])}</div></div>"
        "</div>"
    )


def badge(text, kind="mut"):
    return f"<span class='badge b-{kind}'>{esc(text)}</span>"


def _notify_queue_init():
    """ওয়েব → বট নোটিফিকেশন কিউ (পুরোনো টেবিলেও নতুন কলাম যোগ হয়)।"""
    try:
        _x("CREATE TABLE IF NOT EXISTS task_notify_queue (id BIGSERIAL PRIMARY KEY, "
           "kind TEXT DEFAULT '', task_id BIGINT DEFAULT 0, user_id BIGINT DEFAULT 0, "
           "msg TEXT DEFAULT '', created_at TEXT DEFAULT '', processed INTEGER DEFAULT 0)")
    except Exception:
        pass
    for col in ("user_id BIGINT DEFAULT 0", "msg TEXT DEFAULT ''"):
        try:
            _x("ALTER TABLE task_notify_queue ADD COLUMN IF NOT EXISTS " + col)
        except Exception:
            pass


def _queue_push(kind, task_id=0, user_id=0, msg=""):
    _notify_queue_init()
    try:
        _x("INSERT INTO task_notify_queue (kind, task_id, user_id, msg, created_at, processed) "
           "VALUES (%s,%s,%s,%s,%s,0)",
           (str(kind), int(task_id or 0), int(user_id or 0), str(msg or ""),
            time.strftime("%Y-%m-%d %H:%M:%S")))
        return True
    except Exception:
        return False


def queue_broadcast(text):
    """প্যানেল থেকে লেখা নোটিশ সব verified ইউজারকে (বট পাঠাবে)।"""
    return _queue_push("broadcast", msg=text)


def mask_uname(name):
    """চ্যানেলে নাম আধা-মাস্ক: `Zia uddin` → `Zia***n`."""
    x = str(name or "").strip()
    if not x:
        return "***"
    if x.isdigit():
        return x[:2] + "***" + x[-1:]
    core = x[1:] if x.startswith("@") else x
    if len(core) <= 3:
        return core[:1] + "***"
    return core[:3] + "***" + core[-1:]


def ch_http_send(text):
    """চ্যানেলে পোস্ট — Flask থেকে সরাসরি Bot API (bot import লাগে না)।"""
    import json as _json
    import urllib.request as _ur
    tok = (os.environ.get("BOT_TOKEN") or "").strip()
    if not tok:
        return False
    cid = 0
    for _k in ("deposit_ch_id", "withdraw_ch_id"):
        try:
            _r = one("SELECT value FROM settings WHERE key=%s", (_k,))
            _v = str((_r or {}).get("value") or "").strip()
            if _v.lstrip("-").isdigit():
                cid = int(_v)
                break
        except Exception:
            continue
    if not cid:
        try:
            cid = int(os.environ.get("DEPOSIT_CH_ID") or 0)
        except Exception:
            cid = 0
    if not cid:
        return False
    try:
        body = _json.dumps({"chat_id": cid, "text": str(text)[:3900],
                            "disable_web_page_preview": True}).encode("utf-8")
        req = _ur.Request("https://api.telegram.org/bot" + tok + "/sendMessage",
                          data=body, headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=8).read()
        return True
    except Exception as e:
        try:
            import logging as _lg
            _lg.warning(f"[CH] চ্যানেল পোস্ট ব্যর্থ: {str(e)[:120]}")
        except Exception:
            pass
        return False


def tg_admin_notify(text):
    """অ্যাডমিনদের Telegram DM — ওয়েব প্যানেলের ঘটনা জানাতে (dependency ছাড়া)।"""
    import json as _json
    import urllib.request as _ur
    tok = (os.environ.get("BOT_TOKEN") or "").strip()
    if not tok:
        return 0
    ids = set()
    try:
        for _r in many("SELECT user_id FROM admins"):
            try:
                ids.add(int(_r["user_id"]))
            except Exception:
                continue
    except Exception:
        pass
    try:
        import bot as _b
        ids.add(int(_b.ADMIN_ID))
    except Exception:
        pass
    sent = 0
    for uid in ids:
        try:
            body = _json.dumps({"chat_id": uid, "text": text,
                                "parse_mode": "HTML"}).encode("utf-8")
            req = _ur.Request("https://api.telegram.org/bot" + tok + "/sendMessage",
                              data=body, headers={"Content-Type": "application/json"})
            _ur.urlopen(req, timeout=8).read()
            sent += 1
        except Exception:
            continue
    return sent


def queue_user_msg(user_id, text):
    # গ্রাহক ইংরেজি ভাষা选 করলে ওয়েব-মেসেজও ইংরেজিতে (Google ক্যাশ)
    try:
        import bot as _bi18n
        text = _bi18n.tr_for_user(user_id, text)
    except Exception:
        pass
    """একজন ইউজারকে বটে মেসেজ (যেমন অ্যাডমিন ব্যালেন্স যোগ করলে)।"""
    return _queue_push("dm", user_id=user_id, msg=text)


def queue_task_notify(kind, task_id):
    """বটকে বলি — এই টাস্কের আপডেট সব verified ইউজারকে জানাও (queue টেবিলে row)।"""
    try:
        _x("CREATE TABLE IF NOT EXISTS task_notify_queue (id BIGSERIAL PRIMARY KEY, "
           "kind TEXT DEFAULT '', task_id BIGINT DEFAULT 0, created_at TEXT DEFAULT '', "
           "processed INTEGER DEFAULT 0)")
    except Exception:
        pass
    try:
        _x("INSERT INTO task_notify_queue (kind, task_id, created_at, processed) "
           "VALUES (%s,%s,%s,0)",
           (str(kind), int(task_id), time.strftime("%Y-%m-%d %H:%M:%S")))
        return True
    except Exception:
        return False


# ─────────────────── টাকা/পয়েন্ট পরিবর্তনের অনুমোদন ───────────────────
_MONEY_FIELD_BN = {"balance": "টাকা", "points": "পয়েন্ট"}
_MONEY_OP_BN = {"add": "যোগ", "sub": "বাদ", "set": "সেট"}


_MONEY_REQ_READY = False


def _money_req_init():
    """ওয়েব থেকে টাকা/পয়েন্ট বদলের অনুমোদন-রিকোয়েস্ট টেবিল (প্রসেসে একবারই)।"""
    global _MONEY_REQ_READY
    if _MONEY_REQ_READY:
        return
    try:
        _x("CREATE TABLE IF NOT EXISTS money_requests (id BIGSERIAL PRIMARY KEY, "
           "user_id BIGINT DEFAULT 0, field TEXT DEFAULT 'balance', op TEXT DEFAULT 'add', "
           "amount DOUBLE PRECISION DEFAULT 0, note TEXT DEFAULT '', status TEXT DEFAULT 'pending', "
           "admin TEXT DEFAULT '', created_at TEXT DEFAULT '', decided_at TEXT DEFAULT '', "
           "decided_by TEXT DEFAULT '')")
        _MONEY_REQ_READY = True     # সফল হলেই ফ্ল্যাগ — DB ডাউন থাকলে পরের বার আবার চেষ্টা
    except Exception:
        pass


def money_req_new(uid, field, op, amt, note="", admin=""):
    """রিকোয়েস্ট সেভ করে অ্যাডমিনকে বটে অনুমোদনের মেসেজ পাঠানোর কিউ-তে দেয়।"""
    _money_req_init()
    rid = 0
    try:
        r = one("INSERT INTO money_requests (user_id, field, op, amount, note, status, admin, "
                "created_at) VALUES (%s,%s,%s,%s,%s,'pending',%s,%s) RETURNING id",
                (int(uid), str(field), str(op), float(amt or 0), str(note or "")[:200],
                 str(admin or ""), time.strftime("%Y-%m-%d %H:%M:%S")))
        rid = int((r or {}).get("id") or 0)
    except Exception as e:
        print(f"[moneyreq] সেভ ব্যর্থ: {e}")
        return 0
    if rid:
        _queue_push("adminreq", task_id=rid)     # বট অ্যাডমিনকে ✅/❌ বাটনসহ পাঠাবে
    return rid


def money_req_pending(uid=0, limit=10):
    _money_req_init()
    try:
        q = ("SELECT id, user_id, field, op, amount, note, status, created_at "
             "FROM money_requests WHERE status='pending'")
        args = ()
        if uid:
            q += " AND user_id=%s"
            args = (int(uid),)
        q += " ORDER BY id DESC LIMIT %s"
        return many(q, args + (int(limit),))
    except Exception:
        return []


def money_req_apply(rid, ok, by=""):
    """অনুমোদন/বাতিল — সত্যি হলে ব্যালেন্স বদলায় ও ইউজারকে বটে জানায়।"""
    _money_req_init()
    r = one("SELECT * FROM money_requests WHERE id=%s", (int(rid),))
    if not r:
        return None, "notfound"
    if str(r.get("status") or "") != "pending":
        return r, "done"
    uid = int(r.get("user_id") or 0)
    field = r.get("field") if r.get("field") in ("balance", "points") else "balance"
    op = r.get("op") if r.get("op") in ("add", "sub", "set") else "add"
    try:
        amt = float(r.get("amount") or 0)
    except Exception:
        amt = 0.0
    if field == "points":
        amt = int(round(amt))
    now_s = time.strftime("%Y-%m-%d %H:%M:%S")
    if ok:
        if op == "add":
            _x(f"UPDATE users SET {field}=COALESCE({field},0)+%s WHERE user_id=%s", (amt, uid))
        elif op == "sub":
            _x(f"UPDATE users SET {field}=GREATEST(0,COALESCE({field},0)-%s) "
               f"WHERE user_id=%s", (amt, uid))
        else:
            _x(f"UPDATE users SET {field}=%s WHERE user_id=%s", (max(0, amt), uid))
        _fire_user_changed(uid)
    else:
        _fire_user_changed(uid)
    try:
        _x("UPDATE money_requests SET status=%s, decided_at=%s, decided_by=%s WHERE id=%s",
           ("approved" if ok else "rejected", now_s, str(by or "")[:60], int(rid)))
    except Exception:
        pass
    log_admin(("money_approved" if ok else "money_rejected"), uid,
              f"#{rid} {field}_{op} {amt} {r.get('note') or ''}")
    # ইউজারকে বটে জানাও (অনুমোদন হলে নতুন ব্যালেন্সসহ)
    try:
        _bal = one("SELECT COALESCE(balance,0) AS b, COALESCE(points,0) AS p FROM users "
                   "WHERE user_id=%s", (uid,)) or {}
        _sym = "৳" if field == "balance" else "পয়েন্ট"
        if ok:
            if op == "add":
                _msg = (f"✅ আপনার {_sym} {amt:g} যোগ হয়েছে।\n"
                        f"💳 এখন: {float(_bal.get('b') or 0):g} ৳ · "
                        f"{int(_bal.get('p') or 0)} point")
            elif op == "sub":
                _msg = (f"➖ আপনার {_sym} {amt:g} কমানো হয়েছে।\n"
                        f"💳 এখন: {float(_bal.get('b') or 0):g} ৳ · "
                        f"{int(_bal.get('p') or 0)} point")
            else:
                _msg = (f"🔄 আপনার {_sym} অ্যাডমিন সেট করেছেন: {amt:g}\n"
                        f"💳 এখন: {float(_bal.get('b') or 0):g} ৳ · "
                        f"{int(_bal.get('p') or 0)} point")
            if str(r.get("note") or "").strip():
                _msg += f"\n📝 {str(r.get('note')).strip()[:120]}"
        else:
            _msg = (f"❌ আপনার {_sym} {amt:g} পরিবর্তনের অনুরোধ বাতিল হয়েছে।\n"
                    f"💳 এখন: {float(_bal.get('b') or 0):g} ৳ · "
                    f"{int(_bal.get('p') or 0)} point")
        queue_user_msg(uid, _msg)
    except Exception:
        pass
    return r, "ok"


def _rget(row, key, default=None):
    """row থেকে নিরাপদে মান — কলাম না থাকলেও ক্র্যাশ নয়।"""
    try:
        v = row[key]
        return default if v is None else v
    except Exception:
        return default


def ip_tag(mobile=-1, vpn=-1):
    """IP-এর পাশে ধরন — 🛡️ VPN / 📶 মোবাইল / 🏠 Wi-Fi (অজানা হলে কিছুই না)।"""
    try:
        mobile, vpn = int(mobile), int(vpn)
    except Exception:
        return ""
    if vpn == 1:
        return " " + badge("VPN ব্যবহার করছে", "err")
    if mobile == 1:
        return " " + badge("মোবাইল", "mut")
    if mobile == 0 and vpn == 0:
        return " " + badge("Wi-Fi", "ok")
    return ""


def spark(values, color="#7c5cff"):
    vals = [float(v or 0) for v in values] or [0]
    n = len(vals)
    mx = max(vals) or 1
    w, h = 300, 120
    if n == 1:
        pts = f"0,{h - (vals[0] / mx) * (h - 8) - 4} {w},{h - (vals[0] / mx) * (h - 8) - 4}"
    else:
        pts = " ".join(
            f"{i * (w / (n - 1)):.1f},{h - (v / mx) * (h - 10) - 5:.1f}"
            for i, v in enumerate(vals))
    area = f"0,{h} " + pts + f" {w},{h}"
    return (f"<svg class='chart' viewBox='0 0 {w} {h}' preserveAspectRatio='none'>"
            f"<defs><linearGradient id='g{abs(hash(tuple(vals))) % 99999}' x1='0' y1='0' x2='0' y2='1'>"
            f"<stop offset='0%' stop-color='{color}' stop-opacity='.42'/>"
            "<stop offset='100%' stop-color='#000' stop-opacity='0'/></linearGradient></defs>"
            f"<polygon points='{area}' fill='url(#g{abs(hash(tuple(vals))) % 99999})'/>"
            f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2.2' "
            "stroke-linecap='round' stroke-linejoin='round'/></svg>")


def bars(items, color=None):
    if not items:
        return "<div class='empty'>কোনো ডেটা নেই</div>"
    mx = max([float(v or 0) for _, v in items] or [1]) or 1
    out = []
    for label, v in items:
        pct = max(2, int(float(v or 0) / mx * 100))
        out.append(
            f"<div class='bar'><span>{esc(label)}</span>"
            f"<div class='row' style='gap:8px'><div class='track' style='flex:1;width:auto'>"
            f"<div class='fill' style='width:{pct}%'></div></div>"
            f"<b class='mono' style='width:52px;text-align:right'>{esc(num(v))}</b></div></div>")
    return "<div class='bars'>" + "".join(out) + "</div>"


def donut(parts, center=""):
    parts = [(l, float(v or 0)) for l, v in parts if float(v or 0) > 0][:6]
    total = sum(v for _, v in parts) or 1
    colors = ["#7c5cff", "#4f7cff", "#22c55e", "#f59e0b", "#ef4444", "#22d3ee"]
    segs, acc = [], 0.0
    for i, (_, v) in enumerate(parts):
        a = v / total * 100
        segs.append(f"{colors[i % len(colors)]} {acc:.2f}% {acc + a:.2f}%")
        acc += a
    style = ";".join(segs) or "#1a1a2c 0 100%"
    legend = "".join(
        f"<div class='row' style='gap:6px;font-size:12px'>"
        f"<span style='width:9px;height:9px;border-radius:3px;background:{colors[i % len(colors)]};"
        f"display:inline-block'></span><span>{esc(l)}</span>"
        f"<b style='margin-left:auto' class='mono'>{esc(num(v))}</b></div>"
        for i, (l, v) in enumerate(parts))
    return (f"<div class='row' style='gap:16px;align-items:center'>"
            f"<div class='donut' style='background:conic-gradient({style})'><b>{esc(center)}</b></div>"
            f"<div style='flex:1;min-width:120px;display:flex;flex-direction:column;gap:5px'>{legend}</div></div>")


def pager(base, page_no, has_next, q="", extra=""):
    out = ["<div class='pager'>"]
    sep = "&" if "?" in base else "?"
    if page_no > 1:
        out.append(f"<a class='btn gray sm' href='{base}{sep}{extra}{('q=' + q + '&') if q else ''}"
                   f"page={page_no - 1}'>◀ Prev</a>")
    out.append(f"<span class='mut'>Page {page_no}</span>")
    if has_next:
        out.append(f"<a class='btn gray sm' href='{base}{sep}{extra}{('q=' + q + '&') if q else ''}"
                   f"page={page_no + 1}'>Next ▶</a>")
    out.append("</div>")
    return "".join(out)


# ───────────────────────────── dashboard ─────────────────────────────
def user_rows(where="", args=(), limit=50, offset=0):
    base = ("SELECT user_id, username, full_name, balance, points, is_banned, verified, vrf_pass, "
            "COALESCE(NULLIF(country,''),'??') AS country, COALESCE(last_ip,'') AS last_ip, ")
    tail = ("joined_at, last_seen, COALESCE(is_suspicious,0) AS is_suspicious, "
            "COALESCE(device_hash,'') AS device_hash ")
    a = tuple(args) + (limit, offset)
    try:
        return many(base + tail + ", COALESCE(last_ip_mobile,-1) AS last_ip_mobile, "
                    "COALESCE(last_ip_vpn,-1) AS last_ip_vpn FROM users " + where +
                    " ORDER BY user_id DESC LIMIT %s OFFSET %s", a)
    except Exception:
        # পুরোনো DB (কলাম এখনো তৈরি হয়নি) — আগের কোয়েরিতেই চলবে
        return many(base + tail + "FROM users " + where +
                    " ORDER BY user_id DESC LIMIT %s OFFSET %s", a)


def task_count(uid_map=True):
    c = scal("SELECT COUNT(*) FROM category_task_completions")
    t = scal("SELECT COUNT(*) FROM tg_task_completions")
    b = scal("SELECT COUNT(*) FROM bio_task_completions")
    return c, t, b


def dash_counts():
    """ড্যাশবোর্ডের সব সংখ্যা *এক* query-তে — CockroachDB-তে ১৩টি আলাদা query মানে
    ১৩ বার নেটওয়েট (~১০০ms করে) — এতে পেজ অনেক দ্রুত হয়।"""
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    r = one(
        "SELECT (SELECT COUNT(*) FROM users) AS total,"
        " (SELECT COUNT(*) FROM users WHERE last_seen > %s) AS active,"
        " (SELECT COUNT(*) FROM users WHERE COALESCE(vrf_pass,0)=1) AS verify,"
        " (SELECT COUNT(*) FROM users WHERE COALESCE(is_banned,0)=1) AS ban,"
        " (SELECT COUNT(*) FROM users WHERE COALESCE(is_suspicious,0)=1) AS sus,"
        " (SELECT COUNT(*) FROM security_alerts WHERE kind='multi_account') AS dup,"
        " (SELECT COALESCE(SUM(points),0) FROM users) AS points_total,"
        " (SELECT COALESCE(SUM(balance),0) FROM users) AS balance,"
        " (SELECT COALESCE(SUM(amount),0) FROM withdrawals) AS withdrawals,"
        " (SELECT COUNT(*) FROM category_task_completions) AS cat,"
        " (SELECT COUNT(*) FROM tg_task_completions) AS tg,"
        " (SELECT COUNT(*) FROM bio_task_completions) AS bio,"
        " (SELECT COUNT(*) FROM withdrawals WHERE status='pending') AS pending_wd",
        (day,))
    if r:
        out = {}
        for k, v in dict(r).items():
            try:
                out[k] = float(v or 0) if k in ("balance", "withdrawals") else int(v or 0)
            except Exception:
                out[k] = 0.0 if k in ("balance", "withdrawals") else 0
        return out
    # ব্যাকআপ — এক query ফেল করলে পুরোনো আলাদা পথেই
    tc, tt, tb = task_count()
    return {"total": scal("SELECT COUNT(*) FROM users"),
            "active": scal("SELECT COUNT(*) FROM users WHERE last_seen > %s", (day,)),
            "verify": scal("SELECT COUNT(*) FROM users WHERE COALESCE(vrf_pass,0)=1"),
            "ban": scal("SELECT COUNT(*) FROM users WHERE COALESCE(is_banned,0)=1"),
            "sus": scal("SELECT COUNT(*) FROM users WHERE COALESCE(is_suspicious,0)=1"),
            "dup": scal("SELECT COUNT(*) FROM security_alerts WHERE kind='multi_account'"),
            "points_total": scal("SELECT COALESCE(SUM(points),0) FROM users"),
            "balance": sfloat("SELECT COALESCE(SUM(balance),0) FROM users"),
            "withdrawals": sfloat("SELECT COALESCE(SUM(amount),0) FROM withdrawals"),
            "cat": tc, "tg": tt, "bio": tb,
            "pending_wd": scal("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")}


@wadm.route("/admin/", methods=["GET"])
def dashboard():
    g = guard()
    if g:
        return g
    _c = dash_counts()
    total, active, ver = _c["total"], _c["active"], _c["verify"]
    ban, sus, dup = _c["ban"], _c["sus"], _c["dup"]
    points_total, bal, wd = _c["points_total"], _c["balance"], _c["withdrawals"]
    tc, tt, tb = _c["cat"], _c["tg"], _c["bio"]
    done_tasks = tc + tt + tb
    pend = _c["pending_wd"]

    growth = many_cached("dash_growth",
                  "SELECT SUBSTRING(joined_at,1,10) AS d, COUNT(*) AS n FROM users "
                  "WHERE LENGTH(COALESCE(joined_at,''))>=10 GROUP BY d ORDER BY d DESC LIMIT 14")
    growth = list(reversed(growth))
    gvals = [r["n"] for r in growth]

    earn = many_cached("dash_earn", "SELECT d, SUM(p) AS s FROM ("
                " SELECT SUBSTRING(c.done_at,1,10) AS d, ct.price AS p FROM category_task_completions c"
                "  LEFT JOIN category_tasks ct ON ct.id=c.task_id"
                " UNION ALL SELECT SUBSTRING(c.done_at,1,10), tt.price FROM tg_task_completions c"
                "  LEFT JOIN tg_tasks tt ON tt.id=c.task_id"
                " UNION ALL SELECT SUBSTRING(b.paid_at,1,10), b.amount FROM bio_task_completions b"
                ") x WHERE d IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 14")
    earn = list(reversed(earn))
    evals = [r["s"] or 0 for r in earn]

    countries = many_cached("dash_countries",
                     "SELECT COALESCE(NULLIF(country,''),'??') AS c, COUNT(*) AS n FROM users "
                     "GROUP BY c ORDER BY n DESC LIMIT 8")
    task_parts = [("Category", tc), ("Telegram", tt), ("Bio", tb)]

    kpis = "".join([
        kpi("Total Users", num(total), "সব অ্যাকাউন্ট", "users", "#7c5cff"),
        kpi("Active (7d)", num(active), "গত ৭ দিনে সক্রিয়", "trend", "#22d3ee"),
        kpi("Verified", num(ver), "verify সম্পূর্ণ", "check", "#22c55e"),
        kpi("Unverified", num(max(0, total - ver)), "বাকি আছে", "x", "#f59e0b"),
        kpi("Suspicious", num(sus), "ফ্ল্যাগড", "alert", "#f97316"),
        kpi("Blocked", num(ban), "ব্যান করা", "ban", "#ef4444"),
        kpi("Duplicate Device", num(dup), "মাল্টি-অ্যাকাউন্ট অ্যালার্ট", "device", "#a855f7"),
        kpi("Tasks Completed", num(done_tasks), f"cat {tc} · tg {tt} · bio {tb}", "task", "#4f7cff"),
        kpi("Total Points", num(points_total), "ইউজারদের মোট পয়েন্ট", "trend", "#a855f7"),
        kpi("Total Earnings", money(bal), "ইউজারদের মোট ব্যালেন্স", "money", "#22c55e"),
        kpi("Total Withdrawals", money(wd), f"pending {pend}", "wallet", "#ef4444"),
    ])

    recent = many_cached("dash_recent",
        "SELECT user_id, username, full_name, balance, points, is_banned, verified, vrf_pass, "
        "COALESCE(NULLIF(country,''),'??') AS country, COALESCE(last_ip,'') AS last_ip, "
        "joined_at, last_seen, COALESCE(is_suspicious,0) AS is_suspicious, "
        "COALESCE(device_hash,'') AS device_hash, "
        "COALESCE(last_ip_mobile,-1) AS last_ip_mobile, COALESCE(last_ip_vpn,-1) AS last_ip_vpn "
        "FROM users ORDER BY user_id DESC LIMIT 10 OFFSET 0")
    rows = []
    for r in recent:
        uid = int(r["user_id"])
        st = (badge("verified", "ok") if int(r["vrf_pass"] or 0) == 1 else badge("pending", "warn"))
        if int(r["is_banned"] or 0):
            st = badge("banned", "err")
        dev = badge("shared", "err") if int(r["is_suspicious"] or 0) else badge("ok", "mut")
        rows.append([
            f"<a class='mono' href='/admin/user/{uid}'>{uid}</a>",
            f"<a href='/admin/user/{uid}'>{esc(r['full_name'] or 'User')}</a>"
            f"<div class='mut' style='font-size:11px'>@{esc(r['username'] or '-')}</div>",
            f"{flag(r['country'])} <span class='mono'>{esc(r['country'])}</span>",
            f"<span class='mono'>{esc(r['last_ip'] or '-')}</span>"
            + ip_tag(_rget(r, "last_ip_mobile", -1), _rget(r, "last_ip_vpn", -1)),
            st, dev, money(r["balance"]),
            num(r["points"]),
            f"<a class='btn gray sm' href='/admin/user/{uid}'>Open</a>",
        ])

    body = (
        f"<div class='grid g-kpi'>{kpis}</div>"
        "<div class='grid g2' style='margin-top:14px'>"
        f"<div class='card'><h3>{icon('trend')} User Growth (14 days)</h3>{spark(gvals, '#7c5cff')}"
        "<div class='row' style='justify-content:space-between' class='mut'>"
        f"<span class='mut'>মোট নতুন: {num(sum(gvals))}</span>"
        f"<span class='mut'>আজ: {num(gvals[-1] if gvals else 0)}</span></div></div>"
        f"<div class='card'><h3>{icon('money')} Earnings Trend (14 days)</h3>{spark(evals, '#22c55e')}"
        f"<div class='mut'>মোট: {money(sum(evals))}</div></div>"
        "</div>"
        "<div class='grid g2' style='margin-top:14px'>"
        f"<div class='card'><h3>{icon('globe')} Country Analytics</h3>"
        + donut([(f"{flag(r['c'])} {r['c']}", r["n"]) for r in countries], num(total))
        + "</div>"
        f"<div class='card'><h3>{icon('task')} Task Analytics</h3>"
        + donut(task_parts, num(done_tasks))
        + "</div>"
        "</div>"
        f"<div class='card' style='margin-top:14px'><h3>{icon('users')} Recent Users</h3>"
        + table(["User ID", "Name", "Country", "IP", "Verification", "Device", "Balance", "Points", ""], rows)
        + "<div class='row' style='margin-top:12px'><a class='btn gray sm' href='/admin/users'>"
        "সব ইউজার দেখুন →</a></div></div>"
    )
    return page("Dashboard", body, "dash")


# ───────────────────────────── user lists ─────────────────────────────
def users_page(title, active, where="", args=(), q=""):
    try:
        p = max(1, int(request.args.get("page") or 1))
    except Exception:
        p = 1
    rows = user_rows(where, args, 50, (p - 1) * 50)
    trs = []
    for r in rows:
        uid = int(r["user_id"])
        st = badge("verified", "ok") if int(r["vrf_pass"] or 0) == 1 else badge("pending", "warn")
        if int(r["is_banned"] or 0):
            st += " " + badge("banned", "err")
        trs.append([
            f"<a class='mono' href='/admin/user/{uid}'>{uid}</a>",
            f"<a href='/admin/user/{uid}'>{esc(r['full_name'] or 'User')}</a>"
            f"<div class='mut' style='font-size:11px'>@{esc(r['username'] or '-')}</div>",
            f"{flag(r['country'])} {esc(r['country'])}",
            f"<span class='mono'>{esc(r['last_ip'] or '-')}</span>"
            + ip_tag(_rget(r, "last_ip_mobile", -1), _rget(r, "last_ip_vpn", -1)),
            st, money(r["balance"]), num(r["points"]),
            f"<span class='mut'>{esc(str(r['joined_at'] or '')[:10])}</span>",
            f"<a class='btn gray sm' href='/admin/user/{uid}'>Open</a>",
        ])
    body = (
        "<div class='card'><form class='row' method='get'>"
        f"<input class='f1' name='q' value='{esc(q)}' placeholder='ID / নাম / ইউজারনেম / IP দিয়ে খুঁজুন'>"
        "<button type='submit'>Search</button>"
        "<a class='btn gray' href='/admin/users'>Reset</a></form></div>"
        f"<div class='card' style='margin-top:14px'><h3>{icon('users')} {esc(title)} "
        f"<span class='mut' style='font-weight:400'>({len(rows)} দেখানো হচ্ছে)</span></h3>"
        + table(["User ID", "Name", "Country", "Last IP", "Status", "Balance", "Points", "Joined", ""], trs)
        + pager("/admin/users" if active == "users" else f"/admin/{active}", p, len(rows) == 50, q)
        + "</div>"
    )
    return page(title, body, active)


@wadm.route("/admin/users", methods=["GET"])
def users():
    g = guard()
    if g:
        return g
    q = (request.args.get("q") or "").strip()
    if q:
        # 🔎 শুধু সংখ্যা (Telegram ID) দিলে সোজা ওই ইউজারের প্রোফাইল — ১০০%
        if q.isdigit():
            try:
                _u = user_row(int(q))
            except Exception:
                _u = None
            if _u:
                return redirect(f"/admin/user/{int(q)}")
        like = f"%{q.lower()}%"
        return users_page("Users", "users",
                          "WHERE CAST(user_id AS TEXT) LIKE %s OR LOWER(COALESCE(username,'')) LIKE %s "
                          "OR LOWER(COALESCE(full_name,'')) LIKE %s OR COALESCE(last_ip,'') LIKE %s",
                          (like, like, like, f"%{q}%"), q)
    return users_page("Users", "users")


@wadm.route("/admin/verified", methods=["GET"])
def verified_users():
    g = guard()
    if g:
        return g
    return users_page("Verified Users", "verified", "WHERE COALESCE(vrf_pass,0)=1")


@wadm.route("/admin/unverified", methods=["GET"])
def unverified_users():
    g = guard()
    if g:
        return g
    return users_page("Unverified Users", "unverified", "WHERE COALESCE(vrf_pass,0)=0")


@wadm.route("/admin/blocked", methods=["GET"])
def blocked_users():
    g = guard()
    if g:
        return g
    return users_page("Blocked Users", "blocked", "WHERE COALESCE(is_banned,0)=1")


# ───────────────────────────── user detail ─────────────────────────────
def user_row(uid):
    return one(
        "SELECT user_id, username, full_name, balance, points, is_banned, verified, vrf_pass, "
        "COALESCE(NULLIF(country,''),'??') AS country, COALESCE(lang,'') AS lang, referred_by, "
        "joined_at, last_seen, COALESCE(saved_wallet,'') AS saved_wallet, "
        "COALESCE(last_ip,'') AS last_ip, COALESCE(reg_ip,'') AS reg_ip, "
        "COALESCE(device_hash,'') AS device_hash, COALESCE(ban_reason,'') AS ban_reason, "
        "COALESCE(is_suspicious,0) AS is_suspicious, COALESCE(risk_score,0) AS risk_score "
        "FROM users WHERE user_id=%s", (uid,))


def _pending_html(uid):
    """এই ইউজারের অপেক্ষমাণ টাকা/পয়েন্ট রিকোয়েস্ট — বটে না পেলে ওয়েব থেকেই ✅ করা যায়।"""
    rows = money_req_pending(uid, 6)
    if not rows:
        return ""
    out = ["<div style='margin-top:10px'><b style='font-size:12.5px'>⏳ অপেক্ষমাণ অনুমোদন</b>"]
    for r in rows:
        _f = _MONEY_FIELD_BN.get(r.get("field"), r.get("field"))
        _o = _MONEY_OP_BN.get(r.get("op"), r.get("op"))
        _a = float(r.get("amount") or 0)
        out.append(
            "<div class='row' style='margin-top:6px;gap:8px;align-items:center'>"
            f"<span class='mono mut'>#{int(r.get('id') or 0)}</span>"
            f"<span>{esc(str(_f))} {esc(str(_o))} <b>{num(_a)}</b></span>"
            f"<span class='mut' style='font-size:11px'>{esc(str(r.get('created_at') or '')[:16])}</span>"
            f"<form method='post' action='/admin/do/moneyreq/{int(r.get('id') or 0)}/ok' "
            "style='display:inline'><button class='ok sm' type='submit'>✅ অনুমোদন</button></form>"
            f"<form method='post' action='/admin/do/moneyreq/{int(r.get('id') or 0)}/no' "
            "style='display:inline'><button class='gray sm' type='submit'>❌ বাতিল</button></form>"
            "</div>"
        )
    out.append("</div>")
    return "".join(out)


@wadm.route("/admin/user/<int:uid>", methods=["GET"])
def user_detail(uid):
    g = guard()
    if g:
        return g
    u = user_row(uid)
    if not u:
        return page("User", "<div class='card empty'>এই ইউজার পাওয়া যায়নি।</div>", "users", status=404)
    cat = many("SELECT ct.name, ct.price, c.done_at FROM category_task_completions c "
               "LEFT JOIN category_tasks ct ON ct.id=c.task_id WHERE c.user_id=%s "
               "ORDER BY c.id DESC LIMIT 40", (uid,))
    tg = many("SELECT tt.name, tt.price, c.done_at FROM tg_task_completions c "
              "LEFT JOIN tg_tasks tt ON tt.id=c.task_id WHERE c.user_id=%s "
              "ORDER BY c.id DESC LIMIT 40", (uid,))
    bio = many("SELECT task_date, amount, paid_at FROM bio_task_completions WHERE user_id=%s "
               "ORDER BY id DESC LIMIT 40", (uid,))
    wds = many("SELECT id, amount, status, requested_at, address, tx_hash FROM withdrawals "
               "WHERE user_id=%s ORDER BY id DESC LIMIT 25", (uid,))
    ips = many("SELECT ip, country, city, hits, first_seen, last_seen FROM user_ips "
               "WHERE user_id=%s ORDER BY hits DESC LIMIT 25", (uid,))
    devs = many("SELECT device_hash, platform, ua, first_seen, last_seen FROM devices "
                "WHERE user_id=%s ORDER BY id DESC LIMIT 12", (uid,))
    alerts = many("SELECT kind, detail, status, created_at FROM security_alerts WHERE user_id=%s "
                  "ORDER BY id DESC LIMIT 12", (uid,))
    verif = many("SELECT status, method, attempts, created_at, verified_at, ip, country "
                 "FROM verifications WHERE user_id=%s", (uid,))
    try:
        deps = many("SELECT id, amount, method_name, kind, status, created_at FROM deposits "
                    "WHERE user_id=%s ORDER BY id DESC LIMIT 30", (uid,))
    except Exception:
        deps = []
    try:
        orders = many("SELECT id, product_name, price, status, created_at FROM shop_orders "
                      "WHERE user_id=%s ORDER BY id DESC LIMIT 30", (uid,))
    except Exception:
        orders = []
    dep_total = sum(float(r["amount"] or 0) for r in deps if str(r["status"]) == "approved")
    shop_total = sum(float(r["price"] or 0) for r in orders
                     if str(r["status"]) != "refunded")
    refs = scal("SELECT COUNT(*) FROM users WHERE referred_by=%s", (uid,))
    earned = sum(float(r["price"] or 0) for r in cat) + sum(float(r["price"] or 0) for r in tg) \
        + sum(float(r["amount"] or 0) for r in bio)
    tasks_rows = ([[f"Category · {esc(r['name'] or '-')}", money(r["price"]),
                    esc(str(r["done_at"] or "")[:19])] for r in cat]
                  + [[f"Telegram · {esc(r['name'] or '-')}", money(r["price"]),
                      esc(str(r["done_at"] or "")[:19])] for r in tg]
                  + [[f"Bio · {esc(r['task_date'])}", money(r["amount"]),
                      esc(str(r["paid_at"] or "")[:19])] for r in bio])
    tasks_rows.sort(key=lambda x: x[2], reverse=True)

    status_badges = []
    status_badges.append(badge("verified", "ok") if int(u["vrf_pass"] or 0) == 1 else badge("unverified", "warn"))
    if int(u["is_banned"] or 0):
        status_badges.append(badge("banned", "err"))
    if int(u["is_suspicious"] or 0):
        status_badges.append(badge(f"risk {int(u['risk_score'] or 0)}", "warn"))

    header = (
        "<div class='card'><div class='row' style='gap:16px'>"
        "<div class='avatar' style='width:56px;height:56px;font-size:22px'>"
        f"{esc((u['full_name'] or 'U')[:1].upper())}</div>"
        "<div style='flex:1;min-width:180px'>"
        f"<h3 style='font-size:18px'>{esc(u['full_name'] or 'User')} "
        f"<span class='mono mut'>#{uid}</span></h3>"
        f"<div class='mut'>@{esc(u['username'] or '-')} · {flag(u['country'])} {esc(u['country'])}"
        f" · joined {esc(str(u['joined_at'] or '')[:10])}</div>"
        f"<div class='row' style='margin-top:6px'>{' '.join(status_badges)}</div></div>"
        "<div style='text-align:right'>"
        f"<div class='mut' style='font-size:11px'>Balance</div><b style='font-size:20px'>{money(u['balance'])}</b>"
        f"<div class='mut' style='font-size:11px'>Points</div><b>{num(u['points'])}</b></div>"
        "</div></div>"
    )
    money_card = (
        "<div class='card'><h3>" + icon("money") + " টাকা ও পয়েন্ট কন্ট্রোল</h3>"
        f"<form class='row' method='post' action='/admin/do/money/{uid}'>"
        "<select name='field' style='max-width:170px'><option value='balance'>Balance ($)</option>"
        "<option value='points'>Points</option></select>"
        "<select name='op' style='max-width:130px'><option value='add'>Add</option>"
        "<option value='sub'>Subtract</option><option value='set'>Set</option></select>"
        "<input name='amount' type='number' step='0.0001' placeholder='Amount' required "
        "style='max-width:150px'>"
        "<input name='note' class='f1' placeholder='Note (optional)'>"
        "<button type='submit'>Apply</button></form>"
        "<p class='mut' style='font-size:11.5px;margin:8px 0 0'>"
        "সব পরিবর্তন <b>admin_actions</b> লগে সেভ হয়; কখনো ব্যালেন্স নেগেটিভ হবে না। "
        "<b>এখন Apply চাপলে টাকা সাথে সাথে যোগ হয় না</b> — বটে আপনার কাছে ✅/❌ বাটনসহ "
        "অনুমোদনের মেসেজ যায়, আপনি ✅ চাপলেই ইউজারের ব্যালেন্সে যোগ হবে।</p>"
        + _pending_html(uid) + "</div>"
    )
    action_card = (
        "<div class='card'><h3>" + icon("shield") + " অ্যাকশন</h3>"
        f"<form class='row' method='post' action='/admin/do/ban/{uid}' data-confirm='এই ইউজারকে ব্যান করবেন?'>"
        "<input name='reason' class='f1' placeholder='ব্যানের কারণ'>"
        "<button class='red' type='submit'>Ban</button></form>"
        f"<form class='row' method='post' action='/admin/do/unban/{uid}'>"
        "<button class='ok' type='submit'>Unban / Approve</button></form>"
        f"<form class='row' method='post' action='/admin/do/restart/{uid}' "
        "data-confirm='এই ইউজারকে শুরু থেকে চালু করবেন? আবার verify করতে হবে — "
        "টাকা/পয়েন্ট/রেফারেল/ব্যান অক্ষত থাকবে।'>"
        "<button class='gray' type='submit'>🔄 শুরু থেকে চালু</button>"
        "<span class='mut' style='font-size:11.5px'>এখন /start দিলে নতুন ইউজারের মতো</span></form>"
        f"<form class='row' method='post' action='/admin/do/review/{uid}'>"
        "<button class='gray' type='submit'>Mark Reviewed (clear suspicious)</button></form>"
        + (f"<form class='row' method='post' action='/admin/do/ipban/{uid}'>"
           "<button class='gray' type='submit'>Ban this IP</button>"
           f"<span class='mono mut'>{esc(u['last_ip'] or u['reg_ip'] or '-')}</span></form>"
           if (u["last_ip"] or u["reg_ip"]) else "")
        + "</div>"
    )
    info_rows = [
        ["Telegram ID", f"<span class='mono'>{uid}</span>"],
        ["Country", f"{flag(u['country'])} {esc(u['country'])}"],
        ["Language", esc(u["lang"] or "-")],
        ["Referred by", f"<a class='mono' href='/admin/user/{int(u['referred_by'])}'>{int(u['referred_by'])}</a>"
         if u["referred_by"] else "-"],
        ["Referrals", num(refs)],
        ["Last seen", esc(str(u["last_seen"] or "-")[:19])],
        ["Last IP / Reg IP", f"<span class='mono'>{esc(u['last_ip'] or '-')} / {esc(u['reg_ip'] or '-')}</span>"],
        ["Wallet", f"<span class='mono'>{esc(u['saved_wallet'] or '-')}</span>"],
        ["Ban reason", esc(u["ban_reason"] or "-")],
        ["Total earned (tasks)", money(earned)],
    ]
    body = (
        header
        + "<div class='grid g2' style='margin-top:14px'>"
        + money_card + action_card + "</div>"
        + "<div class='grid g2' style='margin-top:14px'>"
        + "<div class='card'><h3>" + icon("user") + " প্রোফাইল</h3>"
        + table(["Field", "Value"], [[esc(a), b] for a, b in info_rows]) + "</div>"
        + "<div class='card'><h3>" + icon("shield") + " Verification History</h3>"
        + table(["Status", "Method", "Attempts", "Verified at", "IP"],
                [[esc(r["status"]), esc(r["method"]), num(r["attempts"]),
                  esc(str(r["verified_at"] or "")[:19]), f"<span class='mono'>{esc(r['ip'] or '-')}</span>"]
                 for r in verif]) + "</div>"
        + "</div>"
        + "<div class='card' style='margin-top:14px'><h3>" + icon("task")
        + f" টাস্ক হিস্ট্রি <span class='mut' style='font-weight:400'>({len(tasks_rows)})</span></h3>"
        + table(["Task", "Reward", "Time"], tasks_rows) + "</div>"
        # 💰 ডিপোজিট + 🛍 কেনাকাটা — এক নজরে
        + "<div class='grid g2' style='margin-top:14px'>"
        + "<div class='card'><h3>" + icon("money")
        + f" ডিপোজিট <span class='mut' style='font-weight:400'>({len(deps)})</span></h3>"
        + f"<p class='mut' style='margin:0 0 8px;font-size:12px'>মোট অনুমোদিত: <b>{money(dep_total)}</b></p>"
        + table(["ID", "Amount", "Method", "Status", "Date"],
                [[f"#{int(r['id'])}", money(r["amount"]),
                  esc(r["method_name"] or r.get("kind") or "-"),
                  badge(str(r["status"]), "ok" if str(r["status"]) == "approved"
                        else "err" if str(r["status"]) == "rejected" else "warn"),
                  esc(str(r["created_at"] or "")[:19])] for r in deps]) + "</div>"
        + "<div class='card'><h3>" + icon("shop")
        + f" কেনাকাটা <span class='mut' style='font-weight:400'>({len(orders)})</span></h3>"
        + f"<p class='mut' style='margin:0 0 8px;font-size:12px'>মোট খরচ: <b>{money(shop_total)}</b></p>"
        + table(["ID", "Product", "Price", "Status", "Date"],
                [[f"#{int(r['id'])}", esc(r["product_name"] or "-"), money(r["price"]),
                  badge(str(r["status"]), "ok" if str(r["status"]) in ("delivered",)
                        else "err" if str(r["status"]) == "refunded" else "warn"),
                  esc(str(r["created_at"] or "")[:19])] for r in orders]) + "</div>"
        + "</div>"
        + "<div class='grid g2' style='margin-top:14px'>"
        + "<div class='card'><h3>" + icon("wallet") + " Withdrawals</h3>"
        + table(["ID", "Amount", "Status", "Date", "Wallet"],
                [[f"#{int(r['id'])}", money(r["amount"]),
                  badge(str(r["status"]), "ok" if str(r["status"]) == "approved"
                        else "err" if str(r["status"]) == "rejected" else "warn"),
                  esc(str(r["requested_at"] or "")[:19]),
                  f"<span class='mono'>{esc(str(r['address'] or '-')[:16])}</span>"] for r in wds])
        + "</div>"
        + "<div class='card'><h3>" + icon("globe") + " IP Addresses</h3>"
        + table(["IP", "Country", "Hits", "Last seen"],
                [[f"<span class='mono'>{esc(r['ip'])}</span>",
                  f"{flag(r['country'])} {esc(r['country'] or '-')}",
                  num(r["hits"]), esc(str(r["last_seen"] or "")[:19])] for r in ips])
        + "</div></div>"
        + "<div class='card' style='margin-top:14px'><h3>" + icon("device") + " Devices</h3>"
        + table(["Device (hash)", "Platform", "First seen", "Last seen"],
                [[f"<span class='mono'>{esc(str(r['device_hash'] or '')[:20])}</span>",
                  esc(r["platform"] or "-"), esc(str(r["first_seen"] or "")[:19]),
                  esc(str(r["last_seen"] or "")[:19])] for r in devs])
        + "</div>"
        + "<div class='card' style='margin-top:14px'><h3>" + icon("alert") + " Security Alerts</h3>"
        + table(["Kind", "Detail", "Status", "Time"],
                [[esc(r["kind"]), esc(str(r["detail"] or "")[:90]), badge(str(r["status"]),
                  "ok" if str(r["status"]) == "ignored" else "warn"),
                  esc(str(r["created_at"] or "")[:19])] for r in alerts])
        + "</div>"
    )
    return page(f"User {uid}", body, "users", toast=request.args.get("ok"))


# ───────────────────────────── actions ─────────────────────────────
def back(uid=None, to="/admin/users", msg=""):
    url = f"/admin/user/{uid}" if uid else to
    if msg:
        url += ("&" if "?" in url else "?") + "ok=" + msg
    return redirect(url)


@wadm.route("/admin/do/moneyreq/<int:rid>/<act>", methods=["POST"])
def act_moneyreq(rid, act):
    """ওয়েব থেকেই অনুমোদন (বটে মেসেজ না পেলে/বন্ধ থাকলে)।"""
    g = guard()
    if g:
        return g
    r, st = money_req_apply(rid, act == "ok", admin_user())
    if not r:
        return back(None, to="/admin/users", msg="রিকোয়েস্ট পাওয়া যায়নি")
    return back(int(r.get("user_id") or 0),
                msg=("অনুমোদিত হয়েছে ✅ টাকা/পয়েন্ট যোগ হয়েছে" if act == "ok"
                     else "বাতিল করা হয়েছে ❌"))


@wadm.route("/admin/do/money/<int:uid>", methods=["POST"])
def act_money(uid):
    g = guard()
    if g:
        return g
    field = request.form.get("field") or "balance"
    op = request.form.get("op") or "add"
    if field not in ("balance", "points"):
        return back(uid, msg="badfield")
    try:
        amt = float(request.form.get("amount") or 0)
    except Exception:
        amt = 0.0
    if field == "points":
        amt = int(round(amt))
    if amt <= 0:
        return back(uid, msg="পরিমাণ ০-এর বেশি হতে হবে")
    # ── সরাসরি প্রয়োগ নয় — অ্যাডমিনের অনুমোদনের জন্য রিকোয়েস্ট ──
    rid = money_req_new(uid, field, op, amt, request.form.get("note") or "", admin_user())
    if not rid:
        return back(uid, msg="রিকোয়েস্ট সেভ করা যায়নি")
    log_admin(f"money_request_{field}_{op}", uid,
              f"#{rid} {amt} {request.form.get('note') or ''}")
    _fbn = _MONEY_FIELD_BN.get(field, field)
    _obn = _MONEY_OP_BN.get(op, op)
    return back(uid, msg=f"{_fbn} {_obn} {amt:g} — অনুমোদনের জন্য পাঠানো হয়েছে (#{rid})")


@wadm.route("/admin/do/ban/<int:uid>", methods=["POST"])
def act_ban(uid):
    g = guard()
    if g:
        return g
    why = (request.form.get("reason") or "admin panel")[:200]
    _x("UPDATE users SET is_banned=1, ban_reason=%s WHERE user_id=%s", (why, uid))
    log_admin("ban", uid, why)
    _fire_user_changed(uid)
    return back(uid)


@wadm.route("/admin/do/unban/<int:uid>", methods=["POST"])
def act_unban(uid):
    g = guard()
    if g:
        return g
    _x("UPDATE users SET is_banned=0, ban_reason='' WHERE user_id=%s", (uid,))
    log_admin("unban", uid)
    _fire_user_changed(uid)
    return back(uid)


@wadm.route("/admin/do/restart/<int:uid>", methods=["POST"])
def act_restart(uid):
    """একজন ইউজারকে শুরু থেকে চালু — শুধু verification/onboarding state।
    ⚠️ balance, points, referred_by/রেফারেল, task/withdraw ইতিহাস, ban, ডিভাইস/IP — কিছুই মোছা হয় না।"""
    g = guard()
    if g:
        return g
    try:
        try:
            # vrf_force=1 → এরপর verify-এ ডিভাইস/IP লিমিট আটকাবে না (রিস্টার্টের নিয়ম)
            _x("UPDATE users SET vrf_pass=0, verified=0, verified_at='', "
               "vrf_notice_build='', vrf_force=1 WHERE user_id=%s", (uid,))
        except Exception as e:
            if "does not exist" not in str(e).lower():
                raise
            # পুরোনো DB-তে কলাম নেই → বাকি ফিল্ড দিয়েই রিসেট (ফিচার আটকাবে না)
            _x("UPDATE users SET vrf_pass=0, verified=0, verified_at='' WHERE user_id=%s", (uid,))
    except Exception as e:
        return back(uid, msg="restartfail")
    log_admin("restart_flow", uid, "start from scratch (money/points/referral kept)")
    _fire_user_changed(uid)      # বটের ক্যাশে সাথে সাথে — পরের /start-এ নতুন ফ্লো
    return back(uid, msg="restarted")


@wadm.route("/admin/do/review/<int:uid>", methods=["POST"])
def act_review(uid):
    g = guard()
    if g:
        return g
    _x("UPDATE users SET is_suspicious=0 WHERE user_id=%s", (uid,))
    try:
        _x("UPDATE security_alerts SET status='reviewed', handled_at=%s "
           "WHERE user_id=%s AND status='new'", (now(), uid))
    except Exception:
        pass
    alerts_changed()          # সাইডবারের কাউন্ট সাথে সাথে আপডেট
    log_admin("review", uid)
    _fire_user_changed(uid)
    return back(uid)


# ─────────────────── 🎬 ভিডিও আপলোড (web → Telegram file_id) ───────────────────
def _tg_send_video(up, max_mb=48):
    """ওয়েব থেকে ভিডিও ফাইল নিয়ে Telegram-এ পাঠায় → file_id ফেরত দেয়।

    file_id পরে `video_url`-এ `tg:<id>` হিসেবে থাকে, বট নিজেই ভিডিওটা
    ইউজারকে পাঠায়। Render-এ BOT_TOKEN আর ADMIN_ID env লাগবে।
    """
    import json as _json
    import os as _os
    import urllib.request as _ur
    import uuid as _uuid
    tok = (_os.environ.get("BOT_TOKEN") or "").strip()
    chat = ((_os.environ.get("ADMIN_ID") or _os.environ.get("ADMIN_CHAT_ID") or "").strip())
    if not tok or not chat:
        return ""
    try:
        raw = up.read()
        if not raw or len(raw) > max_mb * 1024 * 1024:
            return ""
        fname = (getattr(up, "filename", "") or "video.mp4").replace('"', "")
        boundary = _uuid.uuid4().hex
        def _field(name, val):
            return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                    f"{val}\r\n").encode()
        body = _field("chat_id", chat) + _field("supports_streaming", "true")
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; "
                 f"filename=\"{fname}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
        body += raw + b"\r\n" + f"--{boundary}--\r\n".encode()
        req = _ur.Request(
            f"https://api.telegram.org/bot{tok}/sendVideo", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with _ur.urlopen(req, timeout=180) as resp:
            j = _json.loads(resp.read().decode("utf-8", "ignore"))
        if not j.get("ok"):
            return ""
        res = j.get("result") or {}
        return ((res.get("video") or {}).get("file_id")
                or (res.get("document") or {}).get("file_id") or "")
    except Exception as e:
        print(f"[VIDEO-UPLOAD] ব্যর্থ: {e}")
        return ""


def vid_value(link_val, file_field="video_file"):
    """ফর্মের লিংক + আপলোড দুটোই সামলায় → video_url এর ফাইনাল মান।"""
    up = None
    try:
        up = request.files.get(file_field)
    except Exception:
        up = None
    if up is not None and getattr(up, "filename", ""):
        fid = _tg_send_video(up)
        if fid:
            return "tg:" + fid
    return link_val or ""


# ─────────────────── 🗄️ Data & Storage (বাকার ডাটা মুছুন) ───────────────────
_TABLE_LABELS = {
    "users": "👥 ইউজার", "fb_tasks": "📘 FB টাস্ক", "fb_submissions": "📘 FB জমা",
    "ig_tasks": "📸 IG টাস্ক", "ig_submissions": "📸 IG জমা", "gmails": "📧 Gmail জমা",
    "tg_tasks": "✈️ TG টাস্ক", "tg_task_completions": "✈️ TG সম্পন্ন",
    "bio_task_completions": "📝 Bio সম্পন্ন", "withdrawals": "💸 উইথড্র",
    "commissions": "🎁 কমিশন", "referrals": "🔗 রেফারেল", "devices": "📱 ডিভাইস",
    "user_ips": "🌐 IP তালিকা", "ip_history": "🧭 IP হিস্ট্রি",
    "verifications": "✅ ভেরিফিকেশন", "security_alerts": "🛡️ অ্যালার্ট",
    "task_notify_queue": "📨 নোটিফাই কিউ", "pending_notifications": "🔔 পেন্ডিং নোটিফিকেশন",
    "admin_actions": "📋 অ্যাডমিন লগ", "money_requests": "💸 টাকা রিকোয়েস্ট",
    "task_done_history": "📜 টাস্ক হিস্ট্রি", "settings": "⚙️ সেটিংস",
    "bot_meta": "🔧 bot_meta", "user_activity": "📈 অ্যাক্টিভিটি",
}


_STORAGE_NOTE = ""


def _table_rows():
    """(নাম, রো) — CockroachDB-তে estimated_row_count, না পেলে COUNT(*)।

    যেকোনো query ব্যর্থ হলেও খালি তালিকা ফেরত দেয় — পেজ কখনো 500 হবে না।
    """
    global _STORAGE_NOTE
    _STORAGE_NOTE = ""
    try:
        rows = many("SELECT table_name AS t, estimated_row_count AS n FROM crdb_internal.tables "
                    "WHERE database_name = current_database() AND schema_name = 'public' "
                    "ORDER BY n DESC")
    except Exception as e:
        print(f"[storage] estimated_row_count ব্যর্থ: {e}")
        _STORAGE_NOTE = ("টেবিলের সঠিক গণনা আনা যায়নি — COUNT(*) দিয়ে দেখানো হচ্ছে।")
        rows = []
    out = []
    if rows:
        for r in rows:
            try:
                out.append((str(r.get("t")), int(r.get("n") or 0)))
            except Exception:
                continue
        if out:
            out.sort(key=lambda x: -x[1])
            return out
    for t in _TABLE_LABELS:
        try:
            r = one(f"SELECT COUNT(*) AS n FROM {t}")
            out.append((t, int((r or {}).get("n") or 0)))
        except Exception:
            continue
    out.sort(key=lambda x: -x[1])
    return out


# কোন ক্যাটাগরির জন্য কোন টেবিল+কলাম লাগবে (না থাকলে স্কিপ)
_JUNK_NEEDS = {
    "queue":   ("task_notify_queue", "processed"),
    "notif":   ("pending_notifications", "send_at"),
    "alerts":  ("security_alerts", "status"),
    "devices": ("devices", "last_seen"),
    "ips":     ("user_ips", "last_seen"),
    "iph":     ("ip_history", "seen_at"),
    "actions": ("admin_actions", "created_at"),
    "mreq":    ("money_requests", "status"),
    "premem":  ("tg_task_pre_members", "seen_at"),
}


def _col_ok(tbl, col):
    """টেবিল+কলাম দুটোই আছে কি না (একবার ক্যাশ হয়ে যায়)।"""
    key = ("col", tbl, col)
    hit, val = _agg_get(key)
    if hit:
        return val
    ok = False
    try:
        ok = bool(one("SELECT 1 AS x FROM information_schema.columns "
                      "WHERE table_name=%s AND column_name=%s LIMIT 1", (tbl, col)))
    except Exception as e:
        print(f"[storage] কলাম চেক ব্যর্থ {tbl}.{col}: {e}")
        return False          # ব্যর্থতা ক্যাশে রাখি না — পরে আবার চেষ্টা হবে
    _agg_put(key, ok)
    return ok


# ⚠️ ফাইলের নিজের নীতি: এই টেবিলগুলো "সব মুছুন"-এ ডিফল্টে বাদ
# (লাগলে আলাদা টিক দিয়ে মোছা যাবে — সেটা ইউজারের ইচ্ছা)
_JUNK_PROTECTED = ("devices", "ips", "iph")


def _junk_defs():
    """বাকার (মুছলে কোনো হিসাব/টাকা নষ্ট হয় না) ডাটার তালিকা।"""
    _now = time.time()
    d3 = time.strftime("%Y-%m-%d %H:%M", time.localtime(_now - 3 * 86400))
    d7 = time.strftime("%Y-%m-%d %H:%M", time.localtime(_now - 7 * 86400))
    d60 = time.strftime("%Y-%m-%d %H:%M", time.localtime(_now - 60 * 86400))
    e90 = str(int(_now - 90 * 86400))
    e120 = str(int(_now - 120 * 86400))
    return [
        ("queue", "📨 পাঠানো হয়ে যাওয়া নোটিফিকেশন কিউ (processed)",
         "SELECT COUNT(*) AS n FROM task_notify_queue WHERE processed=1",
         "DELETE FROM task_notify_queue WHERE processed=1"),
        ("notif", "🔔 পুরোনো পেন্ডিং নোটিফিকেশন (৩ দিন+)",
         "SELECT COUNT(*) AS n FROM pending_notifications WHERE send_at < %s",
         "DELETE FROM pending_notifications WHERE send_at < %s", (d3,)),
        ("alerts", "🛡️ সমাধান/ইগনোর হওয়া সিকিউরিটি অ্যালার্ট",
         "SELECT COUNT(*) AS n FROM security_alerts WHERE status IN ('resolved','ignored')",
         "DELETE FROM security_alerts WHERE status IN ('resolved','ignored')"),
        ("devices", "📱 ১২০ দিন দেখা হয়নি এমন ডিভাইস",
         "SELECT COUNT(*) AS n FROM devices WHERE last_seen ~ '^[0-9]+$' AND last_seen < %s",
         "DELETE FROM devices WHERE last_seen ~ '^[0-9]+$' AND last_seen < %s", (e120,)),
        ("ips", "🌐 ৯০ দিন পুরোনো IP তালিকা (user_ips)",
         "SELECT COUNT(*) AS n FROM user_ips WHERE last_seen ~ '^[0-9]+$' AND last_seen < %s",
         "DELETE FROM user_ips WHERE last_seen ~ '^[0-9]+$' AND last_seen < %s", (e90,)),
        ("iph", "🧭 ৯০ দিন পুরোনো IP হিস্ট্রি (ip_history)",
         "SELECT COUNT(*) AS n FROM ip_history WHERE seen_at ~ '^[0-9]+$' AND seen_at < %s",
         "DELETE FROM ip_history WHERE seen_at ~ '^[0-9]+$' AND seen_at < %s", (e90,)),
        ("actions", "📋 ৬০ দিন পুরোনো অ্যাডমিন লগ",
         "SELECT COUNT(*) AS n FROM admin_actions WHERE created_at < %s",
         "DELETE FROM admin_actions WHERE created_at < %s", (d60,)),
        ("mreq", "💸 ৬০ দিন পুরোনো হয়ে যাওয়া টাকা-রিকোয়েস্ট",
         "SELECT COUNT(*) AS n FROM money_requests WHERE status <> 'pending' AND created_at < %s",
         "DELETE FROM money_requests WHERE status <> 'pending' AND created_at < %s", (d60,)),
        ("premem", "👥 ৭ দিন পুরোনো TG pre-members",
         "SELECT COUNT(*) AS n FROM tg_task_pre_members WHERE seen_at < %s",
         "DELETE FROM tg_task_pre_members WHERE seen_at < %s", (d7,)),
    ]


def _junk_counts():
    out = []
    for d in _junk_defs():
        key, label, cnt_sql = d[0], d[1], d[2]
        args = d[4] if len(d) > 4 else ()
        _need = _JUNK_NEEDS.get(key)
        if _need and not _col_ok(*_need):
            out.append({"key": key, "label": label, "n": -2, "err": "টেবিল/কলাম নেই"})
            continue
        n = 0
        err = ""
        try:
            r = one(cnt_sql, args)
            n = int((r or {}).get("n") or 0)
        except Exception as e:
            n = -1                     # query ব্যর্থ
            err = str(e)[:120]
        out.append({"key": key, "label": label, "n": n, "err": err})
    return out


def _junk_delete(kinds):
    """নির্বাচিত বাকার ডাটা ডিলিট করে — {key: কত রো}; ব্যর্থ হলে {key: -1}।"""
    done = {}
    for d in _junk_defs():
        key = d[0]
        if key not in kinds:
            continue
        _need = _JUNK_NEEDS.get(key)
        if _need and not _col_ok(*_need):
            done[key] = -2             # স্কিপ — টেবিল/কলাম নেই
            continue
        del_sql = d[3]
        args = d[4] if len(d) > 4 else ()
        try:
            done[key] = int(_x(del_sql, args) or 0)
        except Exception as e:
            print(f"[cleanup] {key} ব্যর্থ: {e}")
            done[key] = -1             # ব্যর্থ — পেজে কারণ দেখাবে
    return done


@wadm.route("/admin/storage", methods=["GET"])
def storage_page():
    g = guard()
    if g:
        return g
    _warn = ""
    try:
        rows = _table_rows()
    except Exception as e:
        print(f"[storage] টেবিল তালিকা ব্যর্থ: {e}")
        rows, _warn = [], "টেবিলের গণনা আনা যায়নি — নিচের ডিলিট অপশন তবুও কাজ করবে।"
    try:
        junk = _junk_counts()
    except Exception as e:
        print(f"[storage] বাকার ডাটার গণনা ব্যর্থ: {e}")
        junk, _warn = [], "বাকার ডাটার হিসাব আনা যায়নি — কিছুক্ষণ পরে আবার চেষ্টা করুন।"
    if not _warn and _STORAGE_NOTE:
        _warn = _STORAGE_NOTE
    total_rows = sum(n for _, n in rows)
    junk_rows = sum(j["n"] for j in junk if j["n"] > 0)
    trs = [[f"<span class='mono'>{esc(t)}</span>",
            esc(_TABLE_LABELS.get(t, t)), num(n),
            ("<b style='color:#f59e0b'>বেশি</b>" if n >= 20000 else "")] for t, n in rows]
    jtrs = []
    for j in junk:
        if j["n"] == -2:
            jtrs.append([f"<code>{esc(j['key'])}</code>", esc(j["label"]),
                         "<span class='mut'>স্কিপ</span>",
                         "<span class='mut' style='font-size:11px'>টেবিল/কলাম নেই</span>"])
            continue
        if j["n"] < 0:
            jtrs.append([f"<code>{esc(j['key'])}</code>", esc(j["label"]),
                         "<span class='mut'>?</span>",
                         f"<span class='mut' style='font-size:11px'>{esc(j.get('err') or 'query ব্যর্থ')}</span>"])
            continue
        jtrs.append([
            f"<input type='checkbox' name='k' value='{esc(j['key'])}'>",
            esc(j["label"]), num(j["n"]),
            f"<span class='mono mut'>~{num(int(j['n'] * 0.6))} KB</span>",
        ])
    if _warn:
        body_warn = ("<div class='card' style='border-color:#f59e0b'>"
                     f"<p style='margin:0;color:#f59e0b'>⚠️ {esc(_warn)}</p></div>")
    else:
        body_warn = ""
    body = (
        body_warn +
        "<div class='grid g-kpi'>"
        f"<div class='card kpi'><div class='t'>মোট রো (সব টেবিল)</div>"
        f"<div class='v'>{num(total_rows)}</div></div>"
        f"<div class='card kpi'><div class='t'>বাকার ডাটা (মুছতে পারেন)</div>"
        f"<div class='v'>{num(junk_rows)}</div></div>"
        f"<div class='card kpi'><div class='t'>সবচেয়ে বড় টেবিল</div>"
        f"<div class='v' style='font-size:15px'>{esc(rows[0][0]) if rows else '-'}</div></div>"
        "</div>"
        "<div class='card'><h3>" + icon("logs") + " 🧹 বাকার ডাটা ডিলিট (সরাসরি সাইট থেকে)</h3>"
        "<form method='post' action='/admin/do/cleanup'>"
        "<p class='mut' style='font-size:12px;margin:0 0 8px'>নিচের যেগুলো টিক দেবেন সেগুলোই "
        "সাথে সাথে ডিলিট হবে। <b>টাকা, ব্যালেন্স, উইথড্র, কমিশন, রেফারেল, টাস্ক হিস্ট্রি — "
        "কোনোদিন মুছবে না।</b> IP/ডিভাইস হিস্ট্রি ডিফল্টে সুরক্ষিত — দরকার হলে আলাদা টিক দিন।</p>"
        + table(["", "কী ডিলিট হবে", "রো সংখ্যা", "আকার (আন্দাজ)"], jtrs, "বাকার ডাটা নেই")
        + "<div class='row' style='margin-top:12px'>"
        "<button class='red' type='submit' "
        "data-confirm='নির্বাচিত বাকার ডাটা স্থায়ীভাবে ডিলিট হবে। চালিয়ে যাবেন?'>"
        "🗑 নির্বাচিত ডিলিট করুন</button>"
        "<button class='gray' type='submit' name='all' value='1' "
        "data-confirm='সব বাকার ডাটা ডিলিট হবে (IP ও ডিভাইস হিস্ট্রি বাদে)। চালিয়ে যাবেন?'>"
        "🧹 সব বাকার ডাটা মুছুন</button>"
        "</div></form>"
        "<p class='mut' style='font-size:11.5px;margin:10px 0 0'>"
        "ℹ️ CockroachDB-তে ডিলিট করা ডাটা সাথে সাথে ডিস্ক থেকে কমে না — ব্যাকগ্রাউন্ড GC "
        "প্রায় ২৫ ঘণ্টায় একবার চলে, তারপর Storage used কমতে দেখা যায় (কখনো ১-২ দিন)। "
        "ডিলিটের সাথে সাথেই কিন্তু RU/query খরচ কমে যায়।</p></div>"
        "<div class='card'><h3>" + icon("chart") + " কোন টেবিলে কী আছে</h3>"
        + table(["টেবিল", "কী", "রো", ""], trs, "টেবিল পাওয়া যায়নি")
        + "<p class='mut' style='font-size:11.5px;margin-top:10px'>"
        "৪০০ ইউজারে ৪৮০MB হওয়ার সাধারণ কারণ: IP তালিকা/হিস্ট্রি, ডিভাইস, অ্যালার্ট, "
        "নোটিফাই কিউ আর অ্যাডমিন লগ জমে থাকা — উপরের ডিলিট বাটনে এসব কমে যাবে।</p></div>"
    )
    return page("Data & Storage", body, "storage", toast=request.args.get("ok"))


@wadm.route("/admin/do/cleanup", methods=["POST"])
def storage_cleanup():
    g = guard()
    if g:
        return g
    if (request.form.get("all") or "") == "1":
        # IP/ডিভাইস হিস্ট্রি ডিফল্টে বাদ — আলাদা টিক দিলেই মুছবে
        kinds = [d[0] for d in _junk_defs() if d[0] not in _JUNK_PROTECTED]
    else:
        kinds = request.form.getlist("k")
    if not kinds:
        return redirect("/admin/storage?ok=" + _q_("কিছু নির্বাচন করা হয়নি"))
    try:
        done = _junk_delete(kinds)
    except Exception as e:
        print(f"[storage] cleanup ব্যর্থ: {e}")
        done = {k: -1 for k in kinds}
    total = sum(v for v in done.values() if v > 0)
    _bad = [k for k, v in done.items() if v == -1]
    _skip = [k for k, v in done.items() if v == -2]
    log_admin("junk_cleanup", 0, ",".join(f"{k}={v}" for k, v in done.items())[:280])
    _msg = f"{total} রো ডিলিট হয়েছে"
    if _bad:
        _msg += f" | ❌ ব্যর্থ: {', '.join(_bad)}"
    if _skip:
        _msg += f" | ⏭ বাদ: {', '.join(_skip)}"
    return redirect("/admin/storage?ok=" + _q_(_msg))


# ─────────────────── 📜 টাস্ক ইতিহাস (ডিলিট হলেও থাকে) ───────────────────
@wadm.route("/admin/history", methods=["GET"])
def task_history_page():
    g = guard()
    if g:
        return g
    q = (request.args.get("q") or "").strip()
    kind = (request.args.get("kind") or "").strip().lower()
    where, args = [], []
    if q:
        if q.isdigit():
            where.append("user_id=%s")
            args.append(int(q))
        else:
            where.append("(task_name ILIKE %s OR kind ILIKE %s)")
            args += [f"%{q}%", f"%{q}%"]
    if kind in ("fb", "ig", "tg", "bio", "g"):
        where.append("kind=%s")
        args.append(kind)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    rows = many("SELECT * FROM task_done_history" + w + " ORDER BY id DESC LIMIT 300", tuple(args))
    trs = [[f"<a class='mono' href='/admin/user/{int(r.get('user_id') or 0)}'>"
            f"{int(r.get('user_id') or 0)}</a>",
            esc(str(r.get("kind") or "")), esc(str(r.get("task_name") or "-")),
            money(r.get("amount") or 0), esc(str(r.get("done_at") or "")[:19])] for r in rows]
    body = (
        "<div class='card'><h3>" + icon("task") + " 📜 টাস্ক ইতিহাস (স্থায়ী)</h3>"
        "<p class='mut' style='font-size:12px;margin:0 0 8px'>কে, কখন, কোন টাস্ক করেছে আর কত "
        "পেয়েছে — এই রেকর্ড <b>কখনো মুছবে না</b>, জমা তথ্য (submission) ডিলিট করলেও থাকে।</p>"
        "<form class='row' method='get'>"
        f"<input name='q' class='f1' value='{esc(q)}' placeholder='User ID বা টাস্কের নাম'>"
        "<select name='kind' style='max-width:150px'>"
        f"<option value=''>সব</option>"
        + "".join(f"<option value='{k}' {'selected' if kind == k else ''}>{v}</option>"
                  for k, v in (("fb", "Facebook"), ("ig", "Instagram"), ("tg", "Telegram"),
                               ("bio", "Bio"), ("g", "Gmail")))
        + "</select><button type='submit'>খুঁজুন</button></form>"
        + table(["ইউজার", "ধরন", "টাস্ক", "টাকা", "কখন"], trs, "এখনো কোনো রেকর্ড নেই")
        + "</div>"
    )
    return page("Task History", body, "history", toast=request.args.get("ok"))


def _q_(txt):
    from urllib.parse import quote
    return quote(str(txt or ""))
