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
                    minconn=4, maxconn=10, dsn=dsn, connect_timeout=10,
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
        cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
        row = cur.fetchone()
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
def _norm_ip(raw: str) -> str:
    """হেডার/সকেট থেকে আসা IP ঠিক করো: পোর্ট/ব্র্যাকেট বাদ, IPv4-mapped IPv6 → IPv4,
    ভুল/ফাঁকা হলে '' (তাহলে কখনো ভুল করে কাউকে আটকাবে না)।"""
    ip = (raw or "").strip()
    if not ip:
        return ""
    if "," in ip:                               # XFF লিস্ট হলে প্রথমটা
        ip = ip.split(",")[0].strip()
    if ip.startswith("["):                      # [2001:db8::1]:443
        ip = ip[1:ip.find("]")] if "]" in ip else ip[1:]
    elif ip.count(":") == 1 and "." in ip:      # 1.2.3.4:5678
        ip = ip.split(":")[0]
    try:
        import ipaddress
        a = ipaddress.ip_address(ip)
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        return str(a)
    except Exception:
        return ""


def _client_ip() -> str:
    """Render/Vercel এর প্রক্সি হেডার থেকে আসল ক্লায়েন্ট IP।"""
    for h in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
        raw = (request.headers.get(h) or "").strip()
        if raw:
            for part in raw.split(","):        # XFF → প্রথমটা ক্লায়েন্ট
                ip = _norm_ip(part)
                if ip:
                    return ip
    return _norm_ip(request.remote_addr or "")


def _shared_ip_net():
    """CGNAT (100.64.0.0/10) — মোবাইল অপারেটরে হাজারো ইউজার একই IP-তে।"""
    import ipaddress
    return ipaddress.ip_network("100.64.0.0/10")


def _is_private_ip(ip: str) -> bool:
    """প্রাইভেট/লুপব্যাক/লিংক-লোকাল/CGNAT/reserved — geo আর ইউনিক-IP গণনায় বাদ।"""
    if not ip:
        return True
    try:
        import ipaddress
        a = ipaddress.ip_address(ip)
    except Exception:
        return True
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved \
            or a.is_multicast or a.is_unspecified:
        return True
    try:
        return a.version == 4 and a in _shared_ip_net()
    except Exception:
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


def _ip_flags(ip: str):
    """IP মোবাইল নেটওয়ার্ক কি না / VPN-Proxy-Datacenter কি না।

    ফেরে {"mobile": 0/1, "vpn": 0/1}; ব্যর্থ হলে None (কিছুই আটকাবে না)।
    ip-api.com free — key লাগে না, 45 req/min; ফলাফল DB-তে ক্যাশ হয়।
    """
    if _is_private_ip(ip):
        return None
    try:
        url = ("http://ip-api.com/json/" + urllib.parse.quote(ip)
               + "?fields=status,proxy,hosting,mobile,isp,org,as")
        req = urllib.request.Request(url, headers={"User-Agent": "task-earn-bot/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            d = json.loads(resp.read().decode("utf-8", "ignore"))
        if d.get("status") != "success":
            return None
        _txt = " ".join(str(d.get(k) or "") for k in ("isp", "org", "as")).lower()
        # ip-api-র mobile ফ্ল্যাগ অনেক ক্যারিয়ারে false আসে — ISP নাম দেখেও ধরো
        _mobile_word = any(w in _txt for w in (
            "mobile", "cellular", "wireless", "gsm", "3g", "4g", "5g", "lte",
            "wimax", "grameenphone", "gp-", "robi", "airtel", "banglalink",
            "teletalk", "jio", "vodafone", "idea cellular", "telenor", "dialog",
            "bharati", "vi ", "zong", "ufone", "jazz", "tigo", "claro", "movistar",
            "orange", "mtn", "safaricom", "globe telecom", "smart comm", "telkomsel",
            "viettel", "vinaphone", "mobifone", "true move", "ais ", "dtac"))
        _mobile = bool(d.get("mobile")) or _mobile_word
        return {
            "mobile": 1 if _mobile else 0,
            # proxy/VPN বা datacenter IP = VPN ধরনের ট্রাফিক
            "vpn": 1 if (d.get("proxy") or d.get("hosting")) else 0,
        }
    except Exception:
        return None


def _geo_cached(cur, ip: str):
    if _is_private_ip(ip):
        return None
    try:
        cur.execute(
            "SELECT country_code, country, city, isp, is_mobile, is_vpn "
            "FROM ip_geo_cache WHERE ip=%s", (ip,)
        )
        row = cur.fetchone()
    except Exception:
        row = None
    if row:
        try:
            _m = int(row["is_mobile"]) if row["is_mobile"] is not None else -1
        except Exception:
            _m = -1
        try:
            _v = int(row["is_vpn"]) if row["is_vpn"] is not None else -1
        except Exception:
            _v = -1
        if _m == -1 and _v == -1:      # ধরন এখনো চেক করা হয়নি
            _fl = _ip_flags(ip)
            if _fl:
                _m, _v = _fl["mobile"], _fl["vpn"]
                try:
                    cur.execute("UPDATE ip_geo_cache SET is_mobile=%s, is_vpn=%s WHERE ip=%s",
                                (_m, _v, ip))
                except Exception:
                    pass
        return {"code": row["country_code"] or "", "name": row["country"] or "",
                "city": row["city"] or "", "isp": row["isp"] or "",
                "mobile": _m, "vpn": _v}
    geo = _geo_lookup(ip) or {}
    _fl = _ip_flags(ip) or {}
    _m = int(_fl.get("mobile", -1))
    _v = int(_fl.get("vpn", -1))
    try:
        cur.execute(
            "INSERT INTO ip_geo_cache (ip, country_code, country, city, isp, is_mobile, "
            "is_vpn, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (ip) DO UPDATE SET "
            "country_code=EXCLUDED.country_code, country=EXCLUDED.country, "
            "city=EXCLUDED.city, isp=EXCLUDED.isp, "
            "is_mobile=EXCLUDED.is_mobile, is_vpn=EXCLUDED.is_vpn",
            (ip, geo.get("code", ""), geo.get("name", ""), geo.get("city", ""),
             geo.get("isp", ""), _m, _v, str(int(time.time()))),
        )
    except Exception:
        pass
    return {"code": geo.get("code", ""), "name": geo.get("name", ""),
            "city": geo.get("city", ""), "isp": geo.get("isp", ""),
            "mobile": _m, "vpn": _v}


def _ip_type(ip: str, fetch: bool = False) -> str:
    """'vpn' | 'mobile' | 'wifi' | '' — ip_geo_cache থেকে।

    ⚡ fetch=False (ডিফল্ট) মানে শুধু ক্যাশ — পেজ লোড কখনো নেটওয়ার্কে আটকাবে না।
    fetch=True হলে একবার ip-api-তে চেক করে ক্যাশে লিখে দেয় (ব্যাকগ্রাউন্ড থ্রেডে চলে)।
    """
    if not ip or _is_private_ip(ip):
        return ""
    try:
        _p = ip.split(".")
        if len(_p) == 4 and _p[0] == "100" and 64 <= int(_p[1]) <= 127:
            return "mobile"          # CGNAT — মোবাইল ক্যারিয়ার
    except Exception:
        pass
    _m, _v = -1, -1
    try:
        with _db() as cur:
            cur.execute("SELECT is_mobile, is_vpn FROM ip_geo_cache WHERE ip=%s", (ip,))
            r = cur.fetchone()
        if not r and fetch:
            _fl = _ip_flags(ip)
            if _fl:
                _m, _v = _fl["mobile"], _fl["vpn"]
                with _db() as cur2:
                    cur2.execute(
                        "INSERT INTO ip_geo_cache (ip, is_mobile, is_vpn, created_at) "
                        "VALUES (%s,%s,%s,%s) ON CONFLICT (ip) DO UPDATE SET "
                        "is_mobile=EXCLUDED.is_mobile, is_vpn=EXCLUDED.is_vpn",
                        (ip, _m, _v, str(int(time.time()))))
        if r:
            _m = int(r["is_mobile"]) if r["is_mobile"] is not None else -1
            _v = int(r["is_vpn"]) if r["is_vpn"] is not None else -1
            if _m == -1 and _v == -1 and fetch:
                _fl = _ip_flags(ip)
                if _fl:
                    _m, _v = _fl["mobile"], _fl["vpn"]
                    with _db() as cur2:
                        cur2.execute(
                            "INSERT INTO ip_geo_cache (ip, is_mobile, is_vpn, created_at) "
                            "VALUES (%s,%s,%s,%s) ON CONFLICT (ip) DO UPDATE SET "
                            "is_mobile=EXCLUDED.is_mobile, is_vpn=EXCLUDED.is_vpn",
                            (ip, _m, _v, str(int(time.time()))))
    except Exception:
        pass
    if _v == 1:
        return "vpn"
    if _m == 1:
        return "mobile"
    if _m == 0 and _v == 0:
        return "wifi"
    return ""


def _known_country(code: str) -> str:
    """অজানা ISO কোড হলে 'OT' (Other) — দেশভিত্তিক টাস্ক ফিল্টার ভাঙবে না।"""
    known = {"BD", "IN", "PK", "NP", "LK", "MY", "PH", "ID", "TH", "MM",
             "VN", "KH", "NG", "GH", "KE", "US", "GB", "CA", "AU", "AE", "SA"}
    code = (code or "").upper().strip()
    return code if code in known else ("OT" if code else "")


def _device_hash(payload: dict, with_did: bool = True) -> str:
    """ডিভাইস ফিঙ্গারপ্রিন্ট।
    with_did=True  → নতুন ফরম্যাট (localStorage did + GPU) — অনেক নির্ভুল
    with_did=False → পুরোনো ফরম্যাট — আগের ইউজারদের সাথে মেলাতে লাগে
    """
    # ⚠️ ফিঙ্গারপ্রিন্ট অসম্পূর্ণ (JS বন্ধ/ব্লক) হলে ফাঁকা — নাহলে ফাঁকা payload-এর
    # হ্যাশ সবার জন্য একই হয়ে যেত এবং প্রথমজন verify করার পর বাকি সবাই
    # "একই ডিভাইস" হিসেবে ব্লক খেত।
    if not (payload.get("platform") and payload.get("screen") and payload.get("dpr")):
        return ""
    parts = [
        str(payload.get("ua", ""))[:300], str(payload.get("platform", "")),
        str(payload.get("tz", "")), str(payload.get("lang", "")),
        str(payload.get("screen", "")), str(payload.get("dpr", "")),
        str(payload.get("hw", "")), str(payload.get("touch", "")),
        str(payload.get("mem", "")),
    ]
    if with_did:
        parts.append(str(payload.get("did", ""))[:64])
        parts.append(str(payload.get("gpu", ""))[:90])
        parts.append(str(payload.get("hard", ""))[:40])
    return hashlib.sha256(("|".join(parts) + "|te-v1").encode("utf-8", "ignore")).hexdigest()


def _coarse_hash(payload: dict) -> str:
    """একই ফোন চেনার মোটা ফিঙ্গারপ্রিন্ট — ব্রাউজার/UA/localStorage did বাদ।
    একই ফোনে ভিন্ন ব্রাউজার বা Telegram-এর ইন-অ্যাপ ব্রাউজার দিয়ে verify করলেও
    platform+screen+dpr+cores+mem+gpu একই থাকে → ধরা পড়ে।
    (শুধু same-IP এর সাথে মিলিয়ে ব্যবহার হয়, তাই ভিন্ন লোকের ফোন ভুলে আটকাবে না।)"""
    # ⚠️ অসম্পূর্ণ payload (JS বন্ধ/ব্লক) হলে ফাঁকা — নাহলে সবার হ্যাশ এক হয়ে যেত
    if not (payload.get("platform") and payload.get("screen") and payload.get("dpr")):
        return ""
    parts = [
        str(payload.get("platform", "")),
        str(payload.get("screen", "")),
        str(payload.get("dpr", "")),
        str(payload.get("hw", "")),
        str(payload.get("mem", "")),
        str(payload.get("gpu", ""))[:90],
    ]
    return hashlib.sha256(("|".join(parts) + "|te-coarse-v1").encode("utf-8", "ignore")).hexdigest()


def _limit_check(uid: int, ip: str, hashes, coarse: str = "", ua: str = ""):
    """এক ফোন = এক অ্যাকাউন্ট, এক IP-তে সর্বোচ্চ ২ — ভাঙলে কারণ ফেরত দেয়।
    কোনো query ব্যর্থ হলে None (কাউকে ভুলভাবে আটকাবে না)।"""
    try:
        with _db() as cur:
            # /restart করা ইউজার — একবারের জন্য ডিভাইস/IP চেক বাইপাস
            try:
                cur.execute("SELECT COALESCE(vrf_force,0) AS f FROM users WHERE user_id=%s", (uid,))
                _f = cur.fetchone()
                if _f and int(_f.get("f") or 0) == 1:
                    print(f"[verify] vrf_force bypass uid={uid}")
                    # ⚠️ ফ্ল্যাগটা একবারই — নাহলে এই ইউজার চিরকাল লিমিট বাইপাস করত
                    try:
                        cur.execute("UPDATE users SET vrf_force=0 WHERE user_id=%s", (uid,))
                    except Exception:
                        pass
                    return None
            except Exception:
                pass
            try:
                dlim = int(str(_setting(cur, "sec_device_limit", "1")).strip() or 1)
            except Exception:
                dlim = 1
            try:
                ilim = int(str(_setting(cur, "sec_ip_limit", "2")).strip() or 2)
            except Exception:
                ilim = 2
            for dh in [h for h in (hashes or []) if h]:
                cur.execute(
                    "SELECT COUNT(DISTINCT user_id) AS n FROM devices "
                    "WHERE device_hash=%s AND user_id<>%s", (dh, uid))
                r1 = cur.fetchone()
                n_dev = int((r1 or {}).get("n") or 0)
                if n_dev == 0:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM users WHERE device_hash=%s AND user_id<>%s",
                        (dh, uid))
                    r2 = cur.fetchone()
                    n_dev = int((r2 or {}).get("n") or 0)
                if n_dev >= max(1, dlim):
                    return "device"
            # ── STRICT: একই ফোন প্রোফাইল + একই ব্রাউজার = একই ফোন (IP বদলালেও ধরা পড়ে) ──
            # মোবাইল ডেটা/ওয়াইফাই বদলে IP পাল্টালেও একই ফোনে দ্বিতীয় অ্যাকাউন্ট আটকাবে।
            try:
                strict_on = (str(_setting(cur, "sec_device_strict", "on")).strip() or "on") != "off"
            except Exception:
                strict_on = True
            if strict_on and coarse and ua and ip:
                # ⚠️ আগে IP ছাড়াই ব্লক হতো — একই মডেলের ফোন (একই screen/cores/
                #    ব্রাউজার ভার্সন) হলেই ভিন্ন লোক ভুলভাবে আটকে যেত।
                #    এখন একই IP + একই ফোন-প্রোফাইল + একই ব্রাউজার হলে তবেই ব্লক।
                cur.execute(
                    "SELECT COUNT(DISTINCT user_id) AS n FROM devices "
                    "WHERE device_hash=%s AND ua=%s AND ip=%s AND user_id<>%s",
                    (coarse, ua, ip, uid))
                r6 = cur.fetchone()
                if int((r6 or {}).get("n") or 0) >= max(1, dlim):
                    return "device"
            # ── একই ফোন-প্রোফাইল কিন্তু ভিন্ন ব্রাউজার ও ভিন্ন IP → ব্লক নয়, অ্যালার্ট
            #    (একই মডেলের দুইটা আলাদা ফোনকেও ধরা পড়তে পারে, তাই অ্যাডমিন দেখে ঠিক করবেন)
            if coarse:
                try:
                    cur.execute(
                        "SELECT COUNT(DISTINCT user_id) AS n FROM devices "
                        "WHERE device_hash=%s AND user_id<>%s", (coarse, uid))
                    _rc = cur.fetchone()
                    if int((_rc or {}).get("n") or 0) >= 1:
                        # 🔁 একই ইউজারের জন্য ২৪ ঘণ্টায় একবারই অ্যালার্ট (স্প্যাম নয়)
                        _cut24 = str(int(time.time()) - 86400)
                        try:
                            cur.execute(
                                "SELECT COUNT(*) AS n FROM security_alerts "
                                "WHERE kind='same_phone' AND user_id=%s "
                                "AND created_at ~ '^[0-9]+$' AND created_at > %s",
                                (uid, _cut24))
                            _ex = cur.fetchone()
                            _dup = int((_ex or {}).get("n") or 0)
                        except Exception:
                            _dup = 0
                        if not _dup:
                            cur.execute(
                                "INSERT INTO security_alerts (kind, user_id, related_uid, ip, country, "
                                "device_hash, detail, status, created_at) "
                                "VALUES ('same_phone',%s,0,%s,'',%s,%s,'new',%s)",
                                (uid, ip, coarse,
                                 "একই ফোন-প্রোফাইল (ভিন্ন ব্রাউজার/IP) — ম্যানুয়ালি দেখুন",
                                 str(int(time.time()))))
                except Exception:
                    pass
            # ── ফলব্যাক: একই IP + একই ফোন প্রোফাইল = একই ফোন ──
            # (ভিন্ন ব্রাউজার/ইন-অ্যাপ ব্রাউজারে localStorage did আলাদা হলেও ধরা পড়বে)
            if coarse and ip:
                cur.execute(
                    "SELECT COUNT(DISTINCT user_id) AS n FROM devices "
                    "WHERE device_hash=%s AND ip=%s AND user_id<>%s",
                    (coarse, ip, uid))
                r5 = cur.fetchone()
                if int((r5 or {}).get("n") or 0) >= max(1, dlim):
                    return "device"
            # IP লিমিট শুধু "নিশ্চিত Wi-Fi" IP-তে — মোবাইল ডেটা (CGNAT) বা ধরন
            # অজানা IP-তে কাউকে আটকানো হয় না (আগে মোবাইল ইউজারও আটকে যেত)।
            _kind_ip = ""
            try:
                _kind_ip = _ip_type(ip, False)
            except Exception:
                _kind_ip = ""
            if ip and _kind_ip == "mobile":
                return None
            if ip:
                cur.execute(
                    "SELECT COUNT(DISTINCT user_id) AS n FROM user_ips "
                    "WHERE ip=%s AND user_id<>%s", (ip, uid))
                r3 = cur.fetchone()
                cur.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE (last_ip=%s OR reg_ip=%s) "
                    "AND user_id<>%s", (ip, ip, uid))
                r4 = cur.fetchone()
                n_ip = max(int((r3 or {}).get("n") or 0), int((r4 or {}).get("n") or 0))
                if n_ip >= max(1, ilim):
                    try:
                        ip_block_on = (str(_setting(cur, "sec_ip_block", "off")).strip().lower() == "on")
                    except Exception:
                        ip_block_on = False
                    if ip_block_on:
                        return "ip"
                    # ⚠️ মোবাইল CGNAT-এ এক IP-তে শত শত ইউজার → আটকাবো না, শুধু অ্যালার্ট
                    # (একই ইউজার+IP-তে ৬ ঘণ্টায় একবার — অ্যালার্ট স্প্যাম হবে না)
                    _now = str(int(time.time()))
                    try:
                        cur.execute(
                            "INSERT INTO security_alerts (kind, user_id, related_uid, ip, country, "
                            "device_hash, detail, status, created_at) "
                            "SELECT %s,%s,0,%s,'','',%s,'new',%s WHERE NOT EXISTS ("
                            " SELECT 1 FROM security_alerts WHERE kind=%s AND user_id=%s "
                            "AND ip=%s AND status='new' AND created_at>%s)",
                            ("ip_multi", uid, ip,
                             f"একই IP-তে {n_ip}টি অ্যাকাউন্ট (ব্লক হয়নি)",
                             _now, "ip_multi", uid, ip,
                             str(int(time.time()) - 21600)),
                        )
                    except Exception as _ae:
                        print(f"[verify] ip alert skip uid={uid}: {_ae}")
    except Exception as e:
        try:
            print(f"[verify] limit check skip uid={uid}: {e}")
        except Exception:
            pass
        return None
    return None


def _rate_ok(ip: str, limit: int = 120, window: int = 60) -> bool:
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

    # ১) মূল কাজ — শুধু গ্যারান্টিড কলাম (bypass ফ্ল্যাগও পরিষ্কার)
    with _db() as cur:
        cur.execute("UPDATE users SET verified=1, vrf_pass=1 WHERE user_id=%s", (uid,))
        try:
            cur.execute("UPDATE users SET vrf_force=0 WHERE user_id=%s", (uid,))
        except Exception:
            pass

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
                "ELSE country END, device_hash=CASE WHEN COALESCE(device_hash,'')='' "
                "THEN %s ELSE device_hash END, "
                "last_ip_mobile=%s, last_ip_vpn=%s, "
                "last_seen=COALESCE(NULLIF(last_seen,''), %s) WHERE user_id=%s",
                (now_s, ip, ip, country, country, country, dev_hash,
                 int(geo.get("mobile", -1) or -1) if geo else -1,
                 int(geo.get("vpn", -1) or -1) if geo else -1,
                 now_s, uid),
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
    try:
        _coarse_save = _coarse_hash(payload or {})
    except Exception:
        _coarse_save = ""
    for _dh in [h for h in (dev_hash, _coarse_save) if h]:
        # ⚠️ ON CONFLICT ব্যবহার করা যাবে না — devices(user_id, device_hash)-এ
        # unique constraint নেই, তাই INSERT প্রতি বার ব্যর্থ হয়ে চুপচাপ skip হতো
        # এবং এক-ফোন নিয়ম কখনো কাজ করত না। এখন UPDATE → না মিললে INSERT।
        try:
            _ua_s = str((payload or {}).get("ua", ""))[:300]
            _plat_s = str((payload or {}).get("platform", ""))[:60]
            with _db() as cur:
                cur.execute(
                    "UPDATE devices SET last_seen=%s, ip=%s, "
                    "ua=CASE WHEN COALESCE(ua,'')='' THEN %s ELSE ua END, "
                    "platform=CASE WHEN COALESCE(platform,'')='' THEN %s ELSE platform END "
                    "WHERE user_id=%s AND device_hash=%s",
                    (now_s, ip, _ua_s, _plat_s, uid, _dh),
                )
                _upd = cur
                if int(getattr(_upd, "rowcount", 0) or 0) == 0:
                    cur.execute(
                        "INSERT INTO devices (user_id, device_hash, ua, platform, "
                        "first_seen, last_seen, ip) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (uid, _dh, _ua_s, _plat_s, now_s, now_s, ip),
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
    try:
        with _db() as cur:
            cur.execute("UPDATE users SET vrf_force=0 WHERE user_id=%s", (uid,))
    except Exception:
        pass


def _already_verified(uid: int) -> bool:
    try:
        with _db() as cur:
            cur.execute("SELECT verified FROM users WHERE user_id=%s", (uid,))
            row = cur.fetchone()
        return bool(row and int(row["verified"] or 0) == 1)
    except Exception:
        return False



def _ip_banned(ip: str) -> bool:
    """admin panel থেকে ব্যান করা IP কি না (টেবিল না থাকলে False)।"""
    if not ip:
        return False
    try:
        with _db() as cur:
            cur.execute("SELECT 1 AS x FROM ip_bans WHERE ip=%s", (ip,))
            row = cur.fetchone()
        return bool(row)
    except Exception:
        return False


def _page_blocked() -> str:
    inner = (
        "<div class=\"ring\"><div class=\"circle\">!</div></div>"
        "<h1 class=\"warn\">Access Blocked</h1>"
        "<p>This network has been blocked by the admin.<br>"
        "Contact support if you think this is a mistake.</p>"
    )
    return _shell(inner, "Blocked")


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
    # ⚠️ Telegram-এ লিংক প্রিভিউতে লম্বা URL দেখাত। og:title/description দিলে
    #    প্রিভিউতে শুধু "Task Earn Bot" লেখা দেখাবে।
    return (
        "<!doctype html><html lang=\"bn\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,maximum-scale=1\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        "<meta property=\"og:site_name\" content=\"Task Earn Bot\">"
        "<meta property=\"og:type\" content=\"website\">"
        "<meta property=\"og:title\" content=\"Task Earn Bot\">"
        "<meta property=\"og:description\" content=\"Task Earn Bot — অ্যাকাউন্ট ভেরিফিকেশন\">"
        "<meta name=\"twitter:card\" content=\"summary\">"
        "<meta name=\"twitter:title\" content=\"Task Earn Bot\">"
        "<meta name=\"theme-color\" content=\"#0b0b12\">"
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
    var did='';
    try{
      did=localStorage.getItem('teb_did')||'';
      if(!did){
        did=(window.crypto&&crypto.randomUUID)?crypto.randomUUID():
            ('d'+Date.now()+Math.random().toString(36).slice(2));
        localStorage.setItem('teb_did',did);
      }
    }catch(e){}
    p.did=did;
    p.hard=String(navigator.hardwareConcurrency||'')+'x'+String(navigator.deviceMemory||'');
    try{
      var cv=document.createElement('canvas');
      var gl=cv.getContext('webgl')||cv.getContext('experimental-webgl');
      var dbg=gl&&gl.getExtension('WEBGL_debug_renderer_info');
      p.gpu=dbg?String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)).slice(0,80):'';
    }catch(e){ p.gpu=''; }
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
  function fail(err){
    var m={device:'এই ফোনে ইতিমধ্যে একটি অ্যাকাউন্ট আছে। এক ফোনে একটির বেশি অ্যাকাউন্ট চলবে না।',
           ip:'এই ইন্টারনেট (IP) দিয়ে ইতিমধ্যে ২টি অ্যাকাউন্ট খোলা হয়েছে। Wi-Fi বন্ধ করে মোবাইল ডেটা দিয়ে চেষ্টা করুন।',
           rate:'অনেকবার চেষ্টা হয়েছে। কিছুক্ষণ পরে আবার চেষ্টা করুন।',
           vpn:'VPN/Proxy ব্যবহার করা যাচ্ছে না। VPN বন্ধ করে আবার verify করুন।',
           fp:'ব্রাউজার ঠিকভাবে শনাক্ত করা যায়নি। অন্য ব্রাউজার (Chrome) দিয়ে লিংকটা খুলে আবার চেষ্টা করুন।'};
    title.textContent='Verification Blocked';
    title.className='warn';
    sub.textContent=m[err]||'Something went wrong. Please reopen the link.';
    try{ document.querySelector('.note').textContent='Contact admin if you think this is a mistake.'; }catch(e){}
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


# ────────────── web → bot bridge (verify শেষে বট নিজেই মেনু পাঠায়) ──────────────
_VERIFY_HOOK = None
_VERIFY_LOOP = None
_SIG_READY = False


def set_verify_hook(fn, loop=None):
    """bot.py post_init থেকে কল হয় — (coroutine_fn(uid), running_loop)।"""
    global _VERIFY_HOOK, _VERIFY_LOOP
    _VERIFY_HOOK, _VERIFY_LOOP = fn, loop
    try:
        print("[panel] verify hook রেজিস্টার্ড")
    except Exception:
        pass


def _sig_table():
    global _SIG_READY
    if _SIG_READY:
        return
    try:
        with _db() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS vrf_signals (id BIGSERIAL PRIMARY KEY, "
                        "user_id BIGINT, created_at TEXT DEFAULT '', processed INTEGER DEFAULT 0)")
        _SIG_READY = True
    except Exception as e:
        try:
            print(f"[panel] vrf_signals তৈরি ব্যর্থ: {e}")
        except Exception:
            pass


def _fire_verify(uid: int):
    """⟵ এখানেই আসল ফিক্স: verify সেভ হওয়ার সাথে সাথে বটকে মেনু পাঠাতে বলা হয়।"""
    if not uid or uid <= 0:
        return
    # ১) সিগন্যাল (bot restart হলেও কাজ করবে)
    try:
        _sig_table()
        with _db() as cur:
            cur.execute("INSERT INTO vrf_signals (user_id, created_at, processed) "
                        "VALUES (%s,%s,0)", (uid, str(int(time.time()))))
    except Exception as e:
        try:
            print(f"[panel] signal insert ব্যর্থ uid={uid}: {e}")
        except Exception:
            pass
    # ২) সাথে সাথে (thread-safe) — বট একই প্রসেসে চলছে
    fn, loop = _VERIFY_HOOK, _VERIFY_LOOP
    if fn and loop is not None:
        try:
            import asyncio as _a
            _a.run_coroutine_threadsafe(fn(uid), loop)
        except Exception as e:
            try:
                print(f"[panel] hook call ব্যর্থ uid={uid}: {e}")
            except Exception:
                pass


# ──────────────────────────── routes ────────────────────────────
@bp.route("/v/<token>", methods=["GET"])
def verify_page(token):
    if _ip_banned(_client_ip()):
        return Response(_page_blocked(), status=403, mimetype="text/html")
    uid = vtoken.parse_token(token)
    if uid == 0:
        # 🧪 টেস্ট মোড — কাউন্টডাউন আসল, কিন্তু DB-তে কিছুই লেখা হবে না
        try:
            with _db() as cur:
                seconds = _countdown_seconds(cur)
        except Exception:
            seconds = DEFAULT_COUNTDOWN
        resp = Response(_page_countdown(token, seconds), status=200, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-store"
        return resp
    if not uid:
        return Response(_page_invalid(), status=400, mimetype="text/html")
    try:
        _pg_ip = _client_ip()
        # ধরন (মোবাইল/VPN) ক্যাশে না থাকলে ব্যাকগ্রাউন্ডে চেক — পেজ আটকাবে না
        if not _ip_type(_pg_ip):
            threading.Thread(target=lambda: _ip_type(_pg_ip, True), daemon=True).start()
    except Exception:
        pass
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
    if _ip_banned(_client_ip()):
        return jsonify({"ok": False, "error": "blocked"}), 403
    uid = vtoken.parse_token(token)
    if uid == 0:
        return jsonify({"ok": True, "test": True})   # 🧪 টেস্ট — কিছুই সেভ হয় না
    if not uid:
        return jsonify({"ok": False, "error": "invalid"}), 400
    ip = _client_ip()
    if not _rate_ok(ip):
        return jsonify({"ok": False, "error": "rate"}), 429
    _kind = _ip_type(ip, True)         # ক্যাশ না থাকলে একবার চেক (ip_geo_cache-এ জমা)
    if _kind == "vpn":
        # 🛡️ VPN/Proxy দিয়ে verify নয় — বন্ধ করে আবার চেষ্টা করতে বলি
        return jsonify({"ok": False, "error": "vpn", "ip": ip}), 403
    payload = {}
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if _already_verified(uid):
        _mark_pass(uid)
        _fire_verify(uid)
        return jsonify({"ok": True, "already": True})
    # ── ১ ফোন = ১ অ্যাকাউন্ট, এক IP-তে ২ — ভাঙলে verify হবে না ──
    # 🖐 ফিঙ্গারপ্রিন্ট ছাড়া verify হবে না — নাহলে ব্রাউজারে JS বন্ধ করে
    #    এক ফোন থেকেই যত ইচ্ছা অ্যাকাউন্ট খোলা যেত (এক-ফোন নিয়ম ফাঁকা)।
    try:
        _fp_ok = bool(_device_hash(payload))
    except Exception:
        _fp_ok = False
    if not _fp_ok:
        return jsonify({"ok": False, "error": "fp"}), 403
    _hashes = []
    _coarse = ""
    try:
        _hashes = [_device_hash(payload), _device_hash(payload, with_did=False)]
        _coarse = _coarse_hash(payload)
    except Exception:
        _hashes = []
    _ua = ""
    try:
        _ua = str((payload or {}).get("ua", ""))[:300]
    except Exception:
        _ua = ""
    _block = _limit_check(uid, ip, _hashes, _coarse, _ua)
    if _block:
        try:
            _related = 0
            try:
                with _db() as cur2:
                    if _coarse:
                        _rr = cur2.execute(
                            "SELECT user_id FROM devices WHERE device_hash=%s AND user_id<>%s LIMIT 1",
                            (_coarse, uid)).fetchone()
                        _related = int((_rr or {}).get("user_id") or 0)
                    elif _ua:
                        _rr = cur2.execute(
                            "SELECT user_id FROM devices WHERE ua=%s AND user_id<>%s LIMIT 1",
                            (_ua, uid)).fetchone()
                        _related = int((_rr or {}).get("user_id") or 0)
            except Exception:
                _related = 0
            with _db() as cur:
                cur.execute(
                    "INSERT INTO security_alerts (kind, user_id, related_uid, ip, country, "
                    "device_hash, detail, status, created_at) "
                    "VALUES (%s,%s,%s,%s,'',%s,%s,'new',%s)",
                    ("verify_blocked", uid, _related, ip, (_hashes[0] if _hashes else ""),
                     ("একই ফোনে অন্য অ্যাকাউন্ট" if _block == "device"
                      else "একই IP-তে ২টির বেশি অ্যাকাউন্ট"), str(int(time.time()))),
                )
        except Exception as _ae:
            try:
                print(f"[verify] block alert skip uid={uid}: {_ae}")
            except Exception:
                pass
        return jsonify({"ok": False, "error": _block}), 403
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
    _fire_verify(uid)
    return jsonify({"ok": True})
