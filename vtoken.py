"""Verification token helper — কোনো external dependency নেই।

token = base64url("<uid>.<exp>.<hmac>")
secret = VERIFY_SECRET env, না থাকলে BOT_TOKEN থেকে derived।
একই secret bot.py আর panel.py দুটোই ব্যবহার করে, তাই আলাদা কিছু সেটআপ লাগে না।
"""

import base64
import hashlib
import hmac
import os
import time

DEFAULT_TTL = 24 * 3600  # ২৪ ঘণ্টা


def _secret() -> bytes:
    s = (os.environ.get("VERIFY_SECRET") or "").strip()
    if not s:
        s = (os.environ.get("BOT_TOKEN") or "").strip()
    if not s:
        s = "task-earn-bot-verify-fallback"
    return s.encode("utf-8")


def _sign(body: str) -> str:
    return hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def make_token(uid: int, ttl: int = DEFAULT_TTL) -> str:
    """নির্দিষ্ট ইউজারের জন্য একবার-ব্যবহার্য (সময়সীমা সহ) token।"""
    exp = int(time.time()) + int(ttl)
    body = f"{int(uid)}.{exp}"
    raw = f"{body}.{_sign(body)}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def parse_token(token: str):
    """token ঠিক থাকলে uid (int), নইলে None। মেয়াদ শেষ/ভুল সিগনেচার → None।"""
    if not token:
        return None
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode((token + pad).encode("ascii")).decode("utf-8")
        uid_s, exp_s, sig = raw.rsplit(".", 2)
        body = f"{uid_s}.{exp_s}"
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return int(uid_s)
    except Exception:
        return None
