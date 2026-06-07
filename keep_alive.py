import os
import threading
from flask import Flask

app = Flask(__name__)

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
