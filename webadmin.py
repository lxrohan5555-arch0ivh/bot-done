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
import os
import time
import threading

from flask import Blueprint, Response, request, redirect, make_response

wadm = Blueprint("wadm", __name__)

SESSION_HOURS = 12
COOKIE = "teb_admin"

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
    """অ্যালার্ট স্টেটাস বদলালে কাউন্ট ক্যাশে পরিষ্কার করো।"""
    _CHROME.pop("alerts", None)


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


@wadm.before_request
def _panel_security_gate():
    """১) গোপন লিংক (?k=) ২) CSRF (cross-site POST ব্লক)।"""
    if not request.path.startswith("/admin"):
        return None
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
        if not _gate_ok():
            # ভুল/গায়েব লিংক → 404 (প্যানেল আছে বোঝাও যাবে না)
            return Response("Not Found", status=404, mimetype="text/plain")
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
    ("earnings", "/admin/earnings", "Earnings", "money", ""),
    ("withdrawals", "/admin/withdrawals", "Withdrawals", "wallet", ""),
    ("referrals", "/admin/referrals", "Referrals", "refer", ""),
    ("blocked", "/admin/blocked", "Blocked Users", "ban", ""),
    ("SYSTEM", []),
    ("logs", "/admin/logs", "Admin Activity Logs", "logs", ""),
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
    ph = ("61595645847047&#10;61595657606938" if is_fb
          else "johhrpv67&#10;tania_9")
    return (
        "<div class='card'>"
        "<h3>✅ Approve / ❌ Reject</h3>"
        f"<p class='mut' style='font-size:12px;margin:6px 0 10px'>প্রতি লাইনে একটা {idname} দিন। "
        "যেগুলো অ্যাকাউন্ট ঠিক আছে সেগুলো একবার ✅ Approve চাপুন; "
        "ভুল/ব্যান করা আইডি আলাদা লিস্টে দিয়ে ❌ Reject চাপুন। "
        "টাকা যোগ আর ইউজারকে নোটিফিকেশন — বট নিজেই পাঠাবে।</p>"
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
    for part in _re.split(r"[\s,;|]+", str(raw or "")):
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


def review_counts(table, tid):
    """pending / approved / rejected কাউন্ট।"""
    cnt = {"pending": 0, "approved": 0, "rejected": 0}
    if table not in ("fb_submissions", "ig_submissions"):
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
    return many(
        "SELECT user_id, username, full_name, balance, points, is_banned, verified, vrf_pass, "
        "COALESCE(NULLIF(country,''),'??') AS country, COALESCE(last_ip,'') AS last_ip, "
        "joined_at, last_seen, COALESCE(is_suspicious,0) AS is_suspicious, "
        "COALESCE(device_hash,'') AS device_hash "
        "FROM users " + where + " ORDER BY user_id DESC LIMIT %s OFFSET %s",
        tuple(args) + (limit, offset))


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
        "COALESCE(device_hash,'') AS device_hash FROM users "
        "ORDER BY user_id DESC LIMIT 10 OFFSET 0")
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
            f"<span class='mono'>{esc(r['last_ip'] or '-')}</span>",
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
            f"<span class='mono'>{esc(r['last_ip'] or '-')}</span>",
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
        "সব পরিবর্তন <b>admin_actions</b> লগে সেভ হয়; কখনো ব্যালেন্স নেগেটিভ হবে না।</p></div>"
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
    if op == "add":
        _x(f"UPDATE users SET {field}=COALESCE({field},0)+%s WHERE user_id=%s", (amt, uid))
    elif op == "sub":
        _x(f"UPDATE users SET {field}=GREATEST(0,COALESCE({field},0)-%s) WHERE user_id=%s", (amt, uid))
    else:
        _x(f"UPDATE users SET {field}=%s WHERE user_id=%s", (max(0, amt), uid))
    log_admin(f"{field}_{op}", uid, f"{amt} {request.form.get('note') or ''}")
    _fire_user_changed(uid)      # বটের ক্যাশে সাথে সাথে নতুন ব্যালেন্স
    return back(uid)


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
            _x("UPDATE users SET vrf_pass=0, verified=0, verified_at='', "
               "vrf_notice_build='' WHERE user_id=%s", (uid,))
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
