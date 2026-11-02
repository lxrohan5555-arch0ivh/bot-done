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

# ── Admin web panel (webadmin.py + webadmin2.py) — /admin ─────────
try:
    import webadmin as _wadm
    import webadmin2 as _wadm2  # noqa: F401  (বাকি সব route যোগ করে)
    import webadmin3 as _wadm3  # noqa: F401  (Facebook Task pages)
    import webadmin4 as _wadm4  # noqa: F401  (Instagram Task pages)
    app.register_blueprint(_wadm.wadm)
except Exception as _ae:
    print(f"[keep_alive] admin panel load ব্যর্থ: {_ae}")

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
