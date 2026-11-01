"""Verification web page — Flask blueprint।

কোনো নতুন dependency নেই। একই CockroachDB (DATABASE_URL) ব্যবহার করে।
keep_alive.py এর Flask app এ register হয়:
    https://<host>/v/<token>        → ইউজারের verification পাতা (শুধু ৫ সেকেন্ড)
    POST /v/<token>/done            → verification সম্পন্ন + IP/ডিভাইস সিগন্যাল সেভ

এই পাতায় admin এর কোনো অংশ, লিংক বা ডেটা নেই।
"""

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager

import urllib.parse
import urllib.request

from flask import Blueprint, Response, request, jsonify

try:
    import vtoken
except Exception:      # vtoken.py না থাকলেও পাতা চলবে — একই অ্যালগরিদমে inline
    import base64 as _b64
    import hmac as _hm

    class vtoken:  # type: ignore
        DEFAULT_TTL = 24 * 3600

        @staticmethod
        def _secret():
            s = (os.environ.get("VERIFY_SECRET") or "").strip()
            if not s:
                s = (os.environ.get("BOT_TOKEN") or "").strip()
            if not s:
                s = "task-earn-bot-verify-fallback"
            return s.encode("utf-8")

        @classmethod
        def _sign(cls, body):
            return _hm.new(cls._secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

        @classmethod
        def make_token(cls, uid, ttl=DEFAULT_TTL):
            exp = int(time.time()) + int(ttl)
            body = f"{int(uid)}.{exp}"
            raw = f"{body}.{cls._sign(body)}"
            return _b64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

        @classmethod
        def parse_token(cls, token):
            if not token:
                return None
            try:
                pad = "=" * (-len(token) % 4)
                raw = _b64.urlsafe_b64decode((token + pad).encode("ascii")).decode("utf-8")
                uid_s, exp_s, sig = raw.rsplit(".", 2)
                if not _hm.compare_digest(sig, cls._sign(f"{uid_s}.{exp_s}")):
                    return None
                if int(exp_s) < int(time.time()):
                    return None
                return int(uid_s)
            except Exception:
                return None

bp = Blueprint("verify", __name__)

_pool = None
_pool_lock = threading.Lock()

DEFAULT_COUNTDOWN = 5
_PAGE_CACHE_SECONDS = 60
_settings_cache = {}          # key -> (value, ts)
_rate = {}                    # ip -> [ts, ...]
_rate_lock = threading.Lock()


# ──────────────────────────── DB ────────────────────────────
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
                    minconn=1, maxconn=6, dsn=dsn, connect_timeout=10,
                )
    return _pool


@contextmanager
def _db():
    import psycopg2.extras
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
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


def _setting(cur, key, default=None):
    now_ts = time.time()
    hit = _settings_cache.get(key)
    if hit and now_ts - hit[1] < _PAGE_CACHE_SECONDS:
        return hit[0]
    try:
        row = cur.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
        val = row["value"] if row else None
    except Exception:
        val = None
    _settings_cache[key] = (val, now_ts)
    return val if val is not None else default


def _countdown_seconds(cur) -> int:
    try:
        val = int(str(_setting(cur, "verify_countdown", DEFAULT_COUNTDOWN)).strip())
    except Exception:
        val = DEFAULT_COUNTDOWN
    return max(2, min(60, val))


# ──────────────────────────── helpers ────────────────────────────
def _client_ip() -> str:
    for h in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
        raw = (request.headers.get(h) or "").strip()
        if raw:
            return raw.split(",")[0].strip()
    return (request.remote_addr or "").strip()


def _is_private_ip(ip: str) -> bool:
    if not ip:
        return True
    if ip.startswith(("10.", "127.", "192.168.", "172.16.", "172.17.", "172.18.",
                      "172.19.", "172.2", "172.30.", "172.31.", "::1")):
        return True
    return False


def _geo_lookup(ip: str):
    """ফ্রি HTTPS geo API (key লাগে না)। ব্যর্থ হলে None।"""
    if _is_private_ip(ip):
        return None
    try:
        url = f"https://ipwho.is/{urllib.parse.quote(ip)}?fields=success,country,country_code,city,connection"
        req = urllib.request.Request(url, headers={"User-Agent": "task-earn-bot/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
        if not data.get("success"):
            return None
        isp = ""
        try:
            isp = (data.get("connection") or {}).get("isp") or ""
        except Exception:
            isp = ""
        return {
            "code": (data.get("country_code") or "").upper().strip(),
            "name": (data.get("country") or "").strip(),
            "city": (data.get("city") or "").strip(),
            "isp": isp[:120],
        }
    except Exception:
        return None


def _geo_cached(cur, ip: str):
    if _is_private_ip(ip):
        return None
    try:
        row = cur.execute(
            "SELECT country_code, country, city, isp FROM ip_geo_cache WHERE ip=%s", (ip,)
        ).fetchone()
    except Exception:
        row = None
    if row:
        return {"code": row["country_code"] or "", "name": row["country"] or "",
                "city": row["city"] or "", "isp": row["isp"] or ""}
    geo = _geo_lookup(ip)
    try:
        cur.execute(
            "INSERT INTO ip_geo_cache (ip, country_code, country, city, isp, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (ip) DO UPDATE SET "
            "country_code=EXCLUDED.country_code, country=EXCLUDED.country, "
            "city=EXCLUDED.city, isp=EXCLUDED.isp",
            (ip, (geo or {}).get("code", ""), (geo or {}).get("name", ""),
             (geo or {}).get("city", ""), (geo or {}).get("isp", ""), str(int(time.time()))),
        )
    except Exception:
        pass
    return geo


def _known_country(code: str) -> str:
    """অজানা ISO কোড হলে 'OT' (Other) — দেশভিত্তিক টাস্ক ফিল্টার ভাঙবে না।"""
    known = {"BD", "IN", "PK", "NP", "LK", "MY", "PH", "ID", "TH", "MM",
             "VN", "KH", "NG", "GH", "KE", "US", "GB", "CA", "AU", "AE", "SA"}
    code = (code or "").upper().strip()
    return code if code in known else ("OT" if code else "")


def _device_hash(payload: dict) -> str:
    parts = [
        str(payload.get("ua", ""))[:300], str(payload.get("platform", "")),
        str(payload.get("tz", "")), str(payload.get("lang", "")),
        str(payload.get("screen", "")), str(payload.get("dpr", "")),
        str(payload.get("hw", "")), str(payload.get("touch", "")),
        str(payload.get("mem", "")),
    ]
    return hashlib.sha256(("|".join(parts) + "|te-v1").encode("utf-8", "ignore")).hexdigest()


def _rate_ok(ip: str, limit: int = 40, window: int = 60) -> bool:
    now_ts = time.time()
    with _rate_lock:
        hits = [t for t in _rate.get(ip, []) if now_ts - t < window]
        hits.append(now_ts)
        _rate[ip] = hits
    return len(hits) <= limit


# ──────────────────────────── DB writes ────────────────────────────
def _mark_verified(uid: int, ip: str, payload: dict):
    """verification সম্পন্ন — আগে নিশ্চিত ধাপ (verified+vrf_pass), তারপর সিগন্যাল।
    কোনো সিগন্যাল টেবিল/কলাম না থাকলেও verification সফল হবে (ব্যবহারকারী আটকাবে না)।"""
    now_s = str(int(time.time()))
    dev_hash = _device_hash(payload or {}) if "_device_hash" in globals() else ""

    # ১) মূল কাজ — শুধু গ্যারান্টিড কলাম
    with _db() as cur:
        cur.execute("UPDATE users SET verified=1, vrf_pass=1 WHERE user_id=%s", (uid,))

    # ২) বাকি সব সিগন্যাল — প্রতিটা আলাদা, ব্যর্থ হলেও verification সফল
    geo = {}
    try:
        with _db() as cur:
            geo = _geo_cached(cur, ip) or {}
    except Exception as e:
        print(f"[verify] geo skip uid={uid}: {e}")
        geo = {}
    country = _known_country((geo or {}).get("code", ""))
    try:
        with _db() as cur:
            cur.execute(
                "UPDATE users SET verified_at=%s, reg_ip=%s, last_ip=%s, "
                "reg_country=COALESCE(NULLIF(%s,''), reg_country), "
                "country=CASE WHEN COALESCE(country,'') IN ('','BD') AND %s <> '' THEN %s "
                "ELSE country END, device_hash=COALESCE(device_hash, %s), "
                "last_seen=COALESCE(NULLIF(last_seen,''), %s) WHERE user_id=%s",
                (now_s, ip, ip, country, country, country, dev_hash, now_s, uid),
            )
    except Exception as e:
        print(f"[verify] user signal skip uid={uid}: {e}")
    try:
        with _db() as cur:
            cur.execute(
                "INSERT INTO verifications (user_id, status, method, attempts, created_at, "
                "verified_at, ip, country, device_hash) VALUES (%s,'verified','web',1,%s,%s,%s,%s,%s) "
                "ON CONFLICT (user_id) DO UPDATE SET status='verified', method='web', "
                "verified_at=EXCLUDED.verified_at, ip=EXCLUDED.ip, "
                "country=COALESCE(NULLIF(EXCLUDED.country,''), verifications.country), "
                "device_hash=COALESCE(NULLIF(EXCLUDED.device_hash,''), verifications.device_hash)",
                (uid, now_s, now_s, ip, country, dev_hash),
            )
    except Exception as e:
        print(f"[verify] verifications skip uid={uid}: {e}")
    if dev_hash:
        try:
            with _db() as cur:
                cur.execute(
                    "INSERT INTO devices (user_id, device_hash, ua, platform, first_seen, last_seen, ip) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (user_id, device_hash) DO UPDATE SET last_seen=EXCLUDED.last_seen, ip=EXCLUDED.ip",
                    (uid, dev_hash, str((payload or {}).get("ua", ""))[:300],
                     str((payload or {}).get("platform", ""))[:60], now_s, now_s, ip),
                )
        except Exception as e:
            print(f"[verify] devices skip uid={uid}: {e}")
    if ip and not _is_private_ip(ip):
        try:
            with _db() as cur:
                cur.execute(
                    "INSERT INTO user_ips (user_id, ip, country_code, country, city, isp, "
                    "first_seen, last_seen, hits) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1) "
                    "ON CONFLICT (user_id, ip) DO UPDATE SET last_seen=EXCLUDED.last_seen, "
                    "hits=user_ips.hits+1",
                    (uid, ip, (geo or {}).get("code", ""), (geo or {}).get("name", ""),
                     (geo or {}).get("city", ""), (geo or {}).get("isp", ""), now_s, now_s),
                )
        except Exception as e:
            print(f"[verify] user_ips skip uid={uid}: {e}")
        try:
            with _db() as cur:
                cur.execute(
                    "INSERT INTO ip_history (user_id, ip, country_code, seen_at) "
                    "SELECT %s,%s,%s,%s WHERE NOT EXISTS ("
                    " SELECT 1 FROM ip_history WHERE user_id=%s AND ip=%s)",
                    (uid, ip, (geo or {}).get("code", ""), now_s, uid, ip),
                )
        except Exception as e:
            print(f"[verify] ip_history skip uid={uid}: {e}")
    return geo


def _mark_pass(uid: int):
    """বটের নতুন ফ্লোতে 'পার' মার্ক — পুরোনো verified ইউজারও বট চালু করতে পারবে।"""
    try:
        with _db() as cur:
            cur.execute("UPDATE users SET vrf_pass=1 WHERE user_id=%s", (uid,))
    except Exception:
        pass


def _already_verified(uid: int) -> bool:
    try:
        with _db() as cur:
            row = cur.execute("SELECT verified FROM users WHERE user_id=%s", (uid,)).fetchone()
        return bool(row and int(row["verified"] or 0) == 1)
    except Exception:
        return False


# ──────────────────────────── HTML ────────────────────────────
_CSS = """
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{min-height:100vh;background:#0a0a0f;background-image:radial-gradient(circle at 50% 15%,#1a1a2b 0%,#0a0a0f 60%);
display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#e9e9f2}
.card{width:100%;max-width:420px;background:#111118;border:1px solid #23232f;border-radius:26px;
padding:46px 28px 40px;text-align:center;box-shadow:0 18px 60px rgba(0,0,0,.55)}
.ring{width:150px;height:150px;margin:0 auto 28px;border-radius:50%;display:flex;align-items:center;
justify-content:center;background:radial-gradient(circle,#f5a623 0%,#f5a623 62%,rgba(245,166,35,.16) 63%,rgba(245,166,35,0) 100%);
position:relative}
.ring.done{box-shadow:0 0 60px rgba(245,166,35,.45);animation:pop .45s ease-out}
@keyframes pop{0%{transform:scale(.82)}60%{transform:scale(1.05)}100%{transform:scale(1)}}
.circle{width:118px;height:118px;border-radius:50%;background:#f5a623;display:flex;align-items:center;
justify-content:center;color:#fff;font-size:58px;font-weight:700;line-height:1}
.tick{width:60px;height:32px;border-left:9px solid #fff;border-bottom:9px solid #fff;
transform:rotate(-45deg) translate(4px,-8px);border-radius:2px}
h1{font-size:23px;font-weight:700;letter-spacing:.2px;margin-bottom:10px;color:#fff}
h1.warn{color:#f5a623}
p{font-size:14.5px;line-height:1.6;color:#9a9aae}
.bar{height:5px;width:100%;max-width:240px;margin:26px auto 0;background:#1d1d28;border-radius:9px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#7c3aed,#2563eb,#f5a623);border-radius:9px;
transition:width 1s linear}
.foot{margin-top:26px;font-size:11.5px;color:#55556a;letter-spacing:.4px}
.note{margin-top:14px;font-size:12px;color:#6b6b80}
"""


def _shell(inner: str, title: str) -> str:
    return (
        "<!doctype html><html lang=\"bn\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,maximum-scale=1\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        f"<title>{title}</title><style>{_CSS}</style></head>"
        f"<body><div class=\"card\">{inner}</div>"
        "<div class=\"foot\">Secure Verification Service</div></body></html>"
    )


def _page_countdown(token: str, seconds: int) -> str:
    inner = (
        "<div class=\"ring\" id=\"ring\"><div class=\"circle\" id=\"num\">"
        f"{seconds}</div></div>"
        "<h1 id=\"title\">Verifying…</h1>"
        "<p id=\"sub\">Please wait a few seconds.</p>"
        "<div class=\"bar\"><i id=\"prog\"></i></div>"
        "<div class=\"note\">Do not close this page.</div>"
    )
    js = """
<script>
(function(){
  var total=%d, left=total;
  var num=document.getElementById('num'), prog=document.getElementById('prog');
  var title=document.getElementById('title'), sub=document.getElementById('sub');
  var ring=document.getElementById('ring');
  prog.style.width='6%%';
  var t=setInterval(function(){
    left--;
    if(left>0){ num.textContent=left; prog.style.width=Math.round((total-left)/total*100)+'%%'; return; }
    clearInterval(t);
    num.textContent=''; prog.style.width='100%%';
    ring.classList.add('done');
    finish();
  }, 1000);

  function payload(){
    var p={ua:navigator.userAgent,platform:navigator.platform||'',
      lang:navigator.language||'',tz:'',screen:(screen.width+'x'+screen.height),
      dpr:String(window.devicePixelRatio||1),hw:String(navigator.hardwareConcurrency||''),
      touch:String((navigator.maxTouchPoints||0)),mem:String(navigator.deviceMemory||'')};
    try{ p.tz=Intl.DateTimeFormat().resolvedOptions().timeZone||''; }catch(e){}
    return p;
  }
  function showSuccess(){
    ring.classList.remove('done');
    ring.innerHTML='<div class="circle" style="padding-top:6px"><div class="tick"></div></div>';
    ring.classList.add('done');
    title.textContent='Verification Successful';
    title.className='';
    sub.textContent='You can now return to Telegram and continue.';
    document.querySelector('.note').textContent='You may close this page now.';
  }
  function finish(){
    fetch(location.pathname.replace(/\\/$/,'')+'/done',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())})
      .then(function(r){ return r.json(); })
      .then(function(d){ if(d && d.ok){ showSuccess(); } else { fail(d && d.error); } })
      .catch(function(){
        setTimeout(function(){
          fetch(location.pathname.replace(/\\/$/,'')+'/done',{method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())})
            .then(function(r){ return r.json(); })
            .then(function(d){ if(d && d.ok){ showSuccess(); } else { fail(d && d.error); } })
            .catch(function(){ fail('network'); });
        }, 2500);
      });
  }
  function fail(){
    title.textContent='Try Again';
    sub.textContent='Something went wrong. Please reopen the link.';
  }
  if(document.readyState!=='loading'){} 
})();
</script>
""" % (seconds,)
    return _shell(inner, "Verification") .replace("</body>", js + "</body>")


def _page_success() -> str:
    inner = (
        "<div class=\"ring done\"><div class=\"circle\" style=\"padding-top:6px\">"
        "<div class=\"tick\"></div></div></div>"
        "<h1>Verification Successful</h1>"
        "<p>You can now return to Telegram and continue.</p>"
    )
    return _shell(inner, "Verified")


def _page_already() -> str:
    inner = (
        "<div class=\"ring done\"><div class=\"circle\" style=\"padding-top:6px\">"
        "<div class=\"tick\"></div></div></div>"
        "<h1 class=\"warn\">Already Completed</h1>"
        "<p>Verification has already been done</p>"
    )
    return _shell(inner, "Already Completed")


def _page_invalid() -> str:
    inner = (
        "<div class=\"ring\"><div class=\"circle\">!</div></div>"
        "<h1 class=\"warn\">Link Expired</h1>"
        "<p>This verification link is invalid or expired.<br>"
        "Open the bot and press Verify again.</p>"
    )
    return _shell(inner, "Invalid Link")


# ──────────────────────────── routes ────────────────────────────
@bp.route("/v/<token>", methods=["GET"])
def verify_page(token):
    uid = vtoken.parse_token(token)
    if not uid:
        return Response(_page_invalid(), status=400, mimetype="text/html")
    if _already_verified(uid):
        _mark_pass(uid)   # পুরোনো verified ইউজারও যাতে বট চালু করতে পারে
        return Response(_page_already(), status=200, mimetype="text/html")
    try:
        with _db() as cur:
            seconds = _countdown_seconds(cur)
    except Exception:
        seconds = DEFAULT_COUNTDOWN
    resp = Response(_page_countdown(token, seconds), status=200, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@bp.route("/v/<token>/done", methods=["POST"])
def verify_done(token):
    uid = vtoken.parse_token(token)
    if not uid:
        return jsonify({"ok": False, "error": "invalid"}), 400
    ip = _client_ip()
    if not _rate_ok(ip):
        return jsonify({"ok": False, "error": "rate"}), 429
    payload = {}
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if _already_verified(uid):
        _mark_pass(uid)
        return jsonify({"ok": True, "already": True})
    try:
        _mark_verified(uid, ip, payload)
    except Exception as e:
        try:
            print(f"[verify] mark failed uid={uid}: {e}")
        except Exception:
            pass
        # শেষ চেষ্টা: শুধু verified+vrf_pass (সিগন্যাল ছাড়া)
        try:
            with _db() as cur:
                cur.execute("UPDATE users SET verified=1, vrf_pass=1 WHERE user_id=%s", (uid,))
        except Exception as e2:
            try:
                print(f"[verify] fallback failed uid={uid}: {e2}")
            except Exception:
                pass
            return jsonify({"ok": False, "error": "db"}), 500
    return jsonify({"ok": True})
