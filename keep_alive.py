import os
import threading
from flask import Flask

app = Flask(__name__)

# ── Verification web page (panel.py) — /v/<token> ──────────────
try:
    from panel import bp as _verify_bp
    app.register_blueprint(_verify_bp)
except Exception as _pe:
    print(f"[keep_alive] verification page load ব্যর্থ: {_pe}")

# ── Admin web panel — /admin ──────────────────────────────────────
# ⚠️ webadmin.py আগে আলাদাভাবে লোড হয়, তারপর বাকি page-মডিউল।
# আগে সব একসাথে try-তে ছিল → একটা মডিউল (যেমন webadmin6.py) মিসিং হলেই
# পুরো /admin panel 404 হয়ে যেত। এখন শুধু ওই মডিউলটাই বাদ পড়ে, প্যানেল চলে।
try:
    import webadmin as _wadm
except Exception as _ae:
    _wadm = None
    print(f"[keep_alive] admin panel load ব্যর্থ: {_ae}")

# ⚠️ Flask-এ blueprint register করার পরে আর route যোগ করা যায় না —
# তাই সব page-মডিউল আগে import, register সবার শেষে।
for _mod in ("webadmin2", "webadmin3", "webadmin4", "webadmin5", "webadmin6"):
    try:
        __import__(_mod)
    except Exception as _me:
        print(f"[keep_alive] {_mod} লোড হয়নি (প্যানেল চলবে): {_me}")

if _wadm is not None:
    try:
        app.register_blueprint(_wadm.wadm)
    except Exception as _re:
        print(f"[keep_alive] admin blueprint register ব্যর্থ: {_re}")

@app.route("/")
def home():
    return "✅ Task Earn Bot is running!", 200

@app.route("/health")
def health():
    return "OK", 200

@app.route("/api/bot-ping")
def bot_ping():
    return "pong", 200

@app.route("/api/ping")
def api_ping():
    return "pong", 200

def keep_alive():
    port = int(os.environ.get("PORT", 8080))
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True
    )
    t.start()
    print(f"[keep_alive] Flask server started on port {port}")
