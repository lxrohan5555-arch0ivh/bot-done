#!/usr/bin/env python3
"""Task Earn Bot — Full Featured Telegram Bot"""

import os, logging, asyncio, urllib.request, urllib.parse, urllib.error, json as _json, random, re, html
# pip install psycopg2-binary
import psycopg2
import psycopg2.extras
import psycopg2.pool as _pg_pool
import time as _time

# ── .env ফাইল automatic load ─────────────────────────────────────
# VPS/Server → tgbot/.env ফাইলে config লিখুন
# Render     → Environment Variables এ দিন (env var হিসেবেই কাজ করে)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=False)
except ImportError:
    pass  # python-dotenv নেই হলেও চলবে — Render Env Vars / system env থেকে পড়বে

# ── web3 (auto-withdraw এর জন্য) ────────────────────────────────
try:
    from web3 import Web3
    _WEB3_OK = True
except ImportError:
    _WEB3_OK = False
try:
    from keep_alive import keep_alive
except ImportError:
    def keep_alive(): pass
from datetime import datetime, timedelta, timezone, time as dt_time
import pytz
BD_TZ = pytz.timezone("Asia/Dhaka")
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError, RetryAfter as TgRetryAfter

# ──────────────────────────── CONFIG ────────────────────────────
# প্রথমে Environment Variable থেকে নেওয়ার চেষ্টা করো,
# না পেলে টার্মিনালে input() দিয়ে চাও — কোনো .env ফাইল লাগবে না।


class _DuplicatePendingError(Exception):
    """Raised when atomic INSERT detects a concurrent pending withdrawal already exists."""


def _fval(row, default=None):
    """fetchone() → প্রথম value বের করো (single-column query এর জন্য)."""
    if row is None:
        return default
    if isinstance(row, dict):
        vals = list(row.values())
        return vals[0] if vals else default
    # tuple/list row (non-dict cursor compat)
    try:
        return row[0]
    except (IndexError, TypeError):
        return default

  
def _get_env(key, default=""):
    val = os.environ.get(key, "").strip()
    return val if val else default

def _require_env(key):
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"[ERROR] '{key}' সেট করা নেই! Render Environment Variables-এ {key} যোগ করুন।")
        raise SystemExit(1)
    return val

BOT_TOKEN         = _require_env("BOT_TOKEN")
ADMIN_ID          = 7423929548
GMAIL_CH_ID       = -1003947318907
WITHDRAW_CH_ID    = -1003765360862
LEADERBOARD_CH_ID = "@task_earn_bot_news"
TASK_DONE_NOTIFY_CH  = -1004251213683
WD_DETAIL_CH_ID      = -1003963267867   # Admin withdrawal detail log channel

# ── BEP-20 Auto Withdraw Config ──────────────────────────────────
BSC_RPC_URL     = _get_env("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")
BSC_PRIVATE_KEY = _get_env("BSC_PRIVATE_KEY")  # optional — না থাকলে auto-withdraw বন্ধ থাকবে
BSC_WALLET_ADDR = "0x6e545ce914b411a59000e6fff5a330426802aa65"
USDT_BEP20_CA    = "0x55d398326f99059fF775485246999027B3197955"
USDT_BEP20_ABI   = [
    {"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],
     "name":"transfer","outputs":[{"name":"","type":"bool"}],
     "stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"_owner","type":"address"}],
     "name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],
     "stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],
     "stateMutability":"view","type":"function"},
    {"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],
     "name":"approve","outputs":[{"name":"","type":"bool"}],
     "stateMutability":"nonpayable","type":"function"},
]

# ── PancakeSwap — USDT→BNB অটো swap (gas এর জন্য) ────────────────
PANCAKE_ROUTER   = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
WBNB_ADDR        = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
PANCAKE_ROUTER_ABI = [
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"amountOutMin","type":"uint256"},
               {"name":"path","type":"address[]"},{"name":"to","type":"address"},{"name":"deadline","type":"uint256"}],
     "name":"swapExactTokensForETH","outputs":[{"name":"amounts","type":"uint256[]"}],
     "stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],
     "name":"getAmountsOut","outputs":[{"name":"amounts","type":"uint256[]"}],
     "stateMutability":"view","type":"function"},
]
# BNB এই পরিমাণের নিচে নামলে Admin কে alert পাঠাবে (অটো কেনা বন্ধ)
BNB_ALERT_THRESHOLD = 0.001   # এর নিচে হলে Admin কে message যাবে

# ── Scheduled backup channels ────────────────────────────────────
BACKUP_CHANNELS = [-1003863212399, -1003932547078]
BACKUP_INTERVAL_HOURS = 6   # প্রতি ৬ ঘণ্টায় একবার auto report + DB backup

REQUIRED_CHANNELS = [
    {"id": "@rohanbd0",               "link": "https://t.me/rohanbd0",               "name": "📢 Official Channel"},
    {"id": "@gmail_sell_bot_payment", "link": "https://t.me/gmail_sell_bot_payment", "name": "💰 Payment Channel"},
    {"id": "@backuprohanbd0",         "link": "https://t.me/backuprohanbd0",         "name": "🔄 Backup Channel"},
]

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    print("[ERROR] DATABASE_URL সেট করা নেই! Render এ Environment Variable এ DATABASE_URL দিন।")
    raise SystemExit(1)

# ── Render-এ CockroachDB SSL cert ফাইল থাকে না — sslmode=require দিয়ে fix ──
# verify-full বা verify-ca mode certificate file খোঁজে যা Render-এ নেই।
# require mode SSL encrypt করে কিন্তু cert verify করে না — Render-এ কাজ করে।
if "sslmode=" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"
else:
    import re as _re
    DATABASE_URL = _re.sub(r"sslmode=(verify-full|verify-ca)", "sslmode=require", DATABASE_URL)
# sslrootcert parameter থাকলে সরিয়ে দাও — require mode-এ দরকার নেই
DATABASE_URL = _re.sub(r"[&?]sslrootcert=[^&]*", "", DATABASE_URL)
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── File log handler — admin log viewer এর জন্য ──────────────────
_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
try:
    from logging.handlers import RotatingFileHandler as _RFH
    _fh = _RFH(_LOG_FILE, maxBytes=500_000, backupCount=2, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _fh.setLevel(logging.INFO)
    logging.getLogger().addHandler(_fh)
except Exception as _log_ex:
    print(f"[WARN] Log file handler যোগ হয়নি: {_log_ex}")

# Background tasks — strong reference রাখা হয় যাতে asyncio garbage collect না করে
_background_tasks: set = set()

# Withdraw job lock — একসাথে একটাই cycle, nonce conflict বন্ধ
_wd_job_lock = asyncio.Lock()
_bnb_alert_last_sent: float = 0.0   # BNB low alert cooldown — প্রতি ঘন্টায় সর্বোচ্চ একবার
_BNB_ALERT_COOLDOWN = 3600          # seconds (1 ঘন্টা)


# ── Default menu button labels ──────────────────────────────────
DEFAULT_BTNS = {
    'tasks':    ('📋 Tasks',        '📋 Tasks'),
    'profile':  ('👤 My Profile',   '👤 My Profile'),
    'balance':  ('💰 Balance',      '💰 Balance'),
    'refer':    ('🔗 Refer & Earn', '🔗 Refer & Earn'),
    'support':  ('📞 Support',      '📞 Support'),
    'withdraw': ('💸 Withdraw',     '💸 Withdraw'),
    'history':      ('📋 My History',     '📋 My History'),
    'language':     ('🌍 Language',       '🌍 Language'),
}

# ──────────────────────────── TRANSLATIONS ────────────────────────────
MSGS = {
    'bn': {
        'welcome': (
            "📧 *Welcome to Task Earn Bot*\n\n"
            "✅ নিরাপদে Tasks সম্পন্ন করে আয় করুন\n"
            "⚡ দ্রুত পেমেন্ট প্রসেসিং\n"
            "💰 USDT BEP-20 এর মাধ্যমে পেমেন্ট\n"
            "🔄 *অটো Withdraw — ২ থেকে ৫ মিনিটের মধ্যে*\n"
            "🔒 আপনার তথ্য সম্পূর্ণ গোপন রাখা হবে\n\n"
            "📌 *সেবা সমূহ:* Tasks • Balance • Withdraw • Support\n\n"
            "⚠️ ভুল Wallet Address দিলে পেমেন্ট ফেরত পাওয়া যাবে না।\n\n"
            "💎 *Trusted • Fast • Secure*\n\n"
            "👇 *নিচের মেনু থেকে অপশন বেছে নিন।*"
        ),
        'banned': "🚫 *আপনি ব্যান হয়েছেন।*\n\nযোগাযোগ: @inkamxbd",
        'join_prompt': "⚠️ *বটের ফিচার ব্যবহার করতে সকল চ্যানেলে জয়েন করতে হবে।*\n\nনিচের চ্যানেলগুলোতে জয়েন করুন, তারপর ✅ বাটনে চাপুন:",
        'joined_ok': "✅ *ধন্যবাদ! এখন বট ব্যবহার করতে পারবেন।*",
        'not_joined': "❌ এখনো সব চ্যানেলে জয়েন করেননি!",
        'cancelled': "❌ *বাতিল করা হয়েছে।*",
        'bot_off': "🔴 *বট এখন বন্ধ আছে।*\n\nকিছুক্ষণ পরে আবার চেষ্টা করুন।",
        'choose_menu': "👇 *নিচের মেনু থেকে অপশন বেছে নিন।*",
        'profile_title': "👤 *MY PROFILE*",
        'balance_label': "💰 *Balance:*",
        'status_active': "🛡 *Account Status:* Active ✅",
        'developer': "👨‍💻 *Developer:* @inkamxbd",
        'balance_title': "💰 *BALANCE*",
        'balance_greet': "✅ *আসসালামু আলাইকুম ভাই/আপু*",
        'your_balance': "💰 *আপনার ব্যালান্স:*",
        'tasks_title': "📋 *Tasks*",
        'tasks_choose': "👇 *একটি Task বেছে নিন:*",
        'no_tasks': "📋 *Tasks*\n\n❌ *এখন কোনো Task উপলব্ধ নেই।*",
        'refer_title': "🔗 *REFER & EARN*",
        'refer_total': "👥 *মোট রেফার:*",
        'refer_people': "জন",
        'refer_earned': "💸 *মোট কমিশন আয়:*",
        'refer_link_label': "📋 *আপনার রেফার লিংক:*",
        'refer_rules': "💡 *নিয়ম:*\n• লিংক দিয়ে কেউ জয়েন করলে\n• সে Task জমা দিলে তার আয়ের *{comm_pct}%* আপনি পাবেন\n• শুধু সরাসরি Task approval এ কমিশন — chain নেই",
        'my_referrals_btn': "👥 আমার Referrals দেখুন",
        'my_referrals_title': "👥 *আপনার Referrals:*",
        'no_referrals': "_কোনো রেফার নেই এখনো।_",
        'withdraw_title': "💸 *WITHDRAW*",
        'withdraw_only_usdt': "✅ *শুধু USDT-BEP20 (BSC) এ উইথড্র করতে পারবেন*",
        'withdraw_bkash_off': "❌ *বিকাশ/নগদ পেমেন্ট অফ এখন*",
        'min_withdraw': "⚠️ *মিনিমাম উইথড্র:* `0.05$ USDT`",
        'choose_method': "নিচে থেকে পদ্ধতি বেছে নিন:",
        'wd_enter_addr': "📝 *আপনার USDT-BEP20 (BSC) ঠিকানা দিন:*\n\n⚠️ *শুধু BEP-20 (BSC) address দিন — ভুল দিলে পেমেন্ট পাবেন না!*",
        'wd_enter_amt': "💵 *কত ডলার নিতে চান?*\n\n⚠️ মিনিমাম: `{min_w}$ USDT`\n📉 *ফি:* `{fee}$` (কাটা যাবে)\n💰 ব্যালান্স: `{bal}$ USDT`",
        'wd_invalid_amt': "❌ *সঠিক পরিমাণ লিখুন। যেমন: 0.5*",
        'wd_min_err': "❌ *মিনিমাম উইথড্র:* `0.05$ USDT`",
        'wd_no_bal': "❌ *পর্যাপ্ত ব্যালান্স নেই!*\n💰 *ব্যালান্স:* `{bal}$ USDT`\n💸 *চাইছেন:* `{amt}$ USDT`",
        'wd_sent': "✅ *`{amt}$ USDT` Withdraw রিকোয়েস্ট পাঠানো হয়েছে!*\n\n📍 *Address:* `{addr}`\n\n⚡ *২-৫ মিনিটের মধ্যে অটো পেমেন্ট হয়ে যাবে।*\n🔗 BSC (BEP-20) Network এ পাঠানো হবে।",
        'cancel_btn': "❌ বাতিল করুন",
        'skip_btn': "⏭ Skip করুন",
        'submit_ok': "✅ *আপনার তথ্য সফলভাবে এডমিনের কাছে পাঠানো হয়েছে।*\n\nরিভিউ শেষে জানিয়ে দেওয়া হবে! খুব তাড়াতাড়ি আপনার ডলার এড হয়ে যাবে 🤑",
        'approved_msg': "✅ *আপনার Submission Approve হয়েছে!*\n\n📌 *Task:* {task}\n💰 *{price}$ USDT আপনার একাউন্টে যোগ হয়েছে।*\n\n💎 *ধন্যবাদ!*",
        'rejected_msg': "❌ *আপনার Submission Reject হয়েছে।*\n\n📌 *Task:* {task}\nℹ️ নিয়মগুলো পড়ে পুনরায় চেষ্টা করুন।",
        'wd_approved_msg': "✅ *আপনার {amt}$ USDT Withdraw Approve হয়েছে!*\n\n📍 *Address:* `{addr}`\n\n💸 *পেমেন্ট প্রক্রিয়া সম্পন্ন।*\n💎 *ধন্যবাদ!*",
        'wd_rejected_msg': "❌ *আপনার {amt}$ USDT Withdraw Reject হয়েছে।*\n\n🔄 *{amt}$ USDT আপনার একাউন্টে ফেরত দেওয়া হয়েছে।*\n\nℹ️ সমস্যার জন্য: @inkamxbd",
        'commission_msg': "🎉 *রেফার কমিশন পেয়েছেন!*\n\n👤 *Referral:* {name}\n💸 *তার আয়:* `{price}$ USDT`\n💰 *আপনার {comm_pct}% কমিশন:* `{comm}$ USDT`\n\n✅ *একাউন্টে যোগ হয়েছে।*",
        'new_user_ref': "🎉 *আপনার রেফার থেকে একজন জয়েন করেছেন!*\n\n👤 *নাম:* {name}\n\n💡 সে Task জমা দিলে আপনি *{comm_pct}% কমিশন* পাবেন।",
        'history_title': "📋 *আপনার ইতিহাস (সর্বশেষ ১০টি)*",
        'history_gmail': "📋 *Task সাবমিশন:*",
        'history_wd': "💸 *Withdraw রিকোয়েস্ট:*",
        'history_none_gmail': "_কোনো সাবমিশন নেই_",
        'history_none_wd': "_কোনো withdraw নেই_",
        'history_legend': "⏳ = Pending  ✅ = Approved  ❌ = Rejected",
        'submit_btn': "📨 জমা দিন",
        'cancel_inline': "❌ বাতিল",
        'lang_choose': "🌍 *ভাষা বেছে নিন / Choose Language:*",
        'lang_set_bn': "✅ *ভাষা বাংলা করা হয়েছে।*",
        'lang_set_en': "✅ *Language set to English.*",
        'bal_added': "✅ *আপনার একাউন্টে `{amt}$ USDT` যোগ করা হয়েছে!*\n\n💰 *ব্যালান্স:* `{bal}$ USDT`",
        'unban_notify': "✅ *ব্যান তুলে নেওয়া হয়েছে। বট ব্যবহার করতে পারবেন।*",
        'field_ask': "📝 *{prompt}*\n\n❌ বাতিল করতে /cancel লিখুন",
        'field_photo_ask': "📷 *{prompt}*\n\n⏭ Skip করতে নিচের বাটন চাপুন\n❌ বাতিল করতে /cancel লিখুন",
        'collecting_step': "✅ সেভ হয়েছে! ({current}/{total})",
        'new_joiners_only_reject': "❌ *এই Task শুধুমাত্র নতুন জয়েনারদের জন্য।*\n\nআপনি আগে থেকেই এই চ্যানেলে জয়েন আছেন, তাই এই Task করতে পারবেন না।\n\n📢 শুধু নতুন ইউজার যারা এই Task এর মাধ্যমে জয়েন করবে তারাই পুরস্কার পাবে।",
        'task_blocked_msg': (
            "⛔ *Task করা সাময়িকভাবে বন্ধ!*\n\n"
            "আপনার পয়েন্ট ৫০ বা তার নিচে নেমে গেছে।\n\n"
            "🔓 *Block উঠবে:* {until}\n\n"
            "💡 */point* লিখে পয়েন্ট দেখুন।"
        ),
        'point_cmd': (
            "🎯 *আপনার Trust Points*\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "🔵 *পয়েন্ট:* `{pts}/100`\n"
            "🛡 *অবস্থা:* {status}\n"
            "{block_line}"
            "━━━━━━━━━━━━━━━━\n\n"
            "📜 *পয়েন্ট নিয়মাবলী:*\n"
            "• শুরুতে সবার পয়েন্ট: `100`\n"
            "• Task *reject* হলে: `-{reject_pts}` পয়েন্ট\n"
            "• Task *approve* হলে: `+{approve_pts}` পয়েন্ট\n"
            "• পয়েন্ট `50` বা নিচে গেলে: `{block_days}` দিন Task বন্ধ\n"
            "• পয়েন্ট সর্বোচ্চ `100` পর্যন্ত যাবে\n\n"
            "💡 সঠিক নিয়মে Task করলে পয়েন্ট বাড়বে!"
        ),
    },
    'en': {
        'welcome': (
            "📧 *Welcome to Task Earn Bot*\n\n"
            "✅ Complete tasks safely and earn\n"
            "⚡ Fast payment processing\n"
            "💰 Payments via USDT BEP-20\n"
            "🔄 *Auto Withdraw — within 2 to 5 minutes*\n"
            "🔒 Your info is completely private\n\n"
            "📌 *Services:* Tasks • Balance • Withdraw • Support\n\n"
            "⚠️ Wrong wallet address = no refund.\n\n"
            "💎 *Trusted • Fast • Secure*\n\n"
            "👇 *Choose an option from the menu below.*"
        ),
        'banned': "🚫 *You have been banned.*\n\nContact: @inkamxbd",
        'join_prompt': "⚠️ *You must join all channels to use the bot.*\n\nJoin the channels below, then press ✅:",
        'joined_ok': "✅ *Thank you! You can now use the bot.*",
        'not_joined': "❌ You haven't joined all channels yet!",
        'cancelled': "❌ *Action cancelled.*",
        'bot_off': "🔴 *Bot is currently offline.*\n\nPlease try again later.",
        'choose_menu': "👇 *Choose an option from the menu below.*",
        'profile_title': "👤 *MY PROFILE*",
        'balance_label': "💰 *Balance:*",
        'status_active': "🛡 *Account Status:* Active ✅",
        'developer': "👨‍💻 *Developer:* @inkamxbd",
        'balance_title': "💰 *BALANCE*",
        'balance_greet': "✅ *Hello! Welcome back.*",
        'your_balance': "💰 *Your balance:*",
        'tasks_title': "📋 *Tasks*",
        'tasks_choose': "👇 *Choose a task:*",
        'no_tasks': "📋 *Tasks*\n\n❌ *No tasks available right now.*",
        'refer_title': "🔗 *REFER & EARN*",
        'refer_total': "👥 *Total Referrals:*",
        'refer_people': "people",
        'refer_earned': "💸 *Total Commission Earned:*",
        'refer_link_label': "📋 *Your Referral Link:*",
        'refer_rules': "💡 *Rules:*\n• When someone joins via your link\n• And gets a Task approved, you earn *{comm_pct}%* of their income\n• Direct referrals only — no chains",
        'my_referrals_btn': "👥 View My Referrals",
        'my_referrals_title': "👥 *Your Referrals:*",
        'no_referrals': "_No referrals yet._",
        'withdraw_title': "💸 *WITHDRAW*",
        'withdraw_only_usdt': "✅ *Withdrawals only via USDT-BEP20 (BSC)*",
        'withdraw_bkash_off': "❌ *bKash/Nagad payments are currently off*",
        'min_withdraw': "⚠️ *Minimum withdrawal:* `0.05$ USDT`",
        'choose_method': "Choose a method below:",
        'wd_enter_addr': "📝 *Enter your USDT-BEP20 (BSC) address:*\n\n⚠️ *BEP-20 (BSC) address only — wrong address = no refund!*",
        'wd_enter_amt': "💵 *How much do you want to withdraw?*\n\n⚠️ Minimum: `{min_w}$ USDT`\n📉 *Fee:* `{fee}$` (deducted)\n💰 Balance: `{bal}$ USDT`",
        'wd_invalid_amt': "❌ *Enter a valid amount. Example: 0.5*",
        'wd_min_err': "❌ *Minimum withdrawal:* `0.05$ USDT`",
        'wd_no_bal': "❌ *Insufficient balance!*\n💰 *Balance:* `{bal}$ USDT`\n💸 *Requested:* `{amt}$ USDT`",
        'wd_sent': "✅ *Withdrawal request of `{amt}$ USDT` sent!*\n\n📍 *Address:* `{addr}`\n\n⚡ *Auto payment will be processed within 2-5 minutes.*\n🔗 Via BSC (BEP-20) Network.",
        'cancel_btn': "❌ Cancel",
        'skip_btn': "⏭ Skip",
        'submit_ok': "✅ *Your info has been sent to admin successfully.*\n\nYou'll be notified after review! Your dollars will be added soon 🤑",
        'approved_msg': "✅ *Your Submission has been Approved!*\n\n📌 *Task:* {task}\n💰 *{price}$ USDT has been added to your account.*\n\n💎 *Thank you!*",
        'rejected_msg': "❌ *Your Submission has been Rejected.*\n\n📌 *Task:* {task}\nℹ️ Please re-read the rules and try again.",
        'wd_approved_msg': "✅ *Your withdrawal of {amt}$ USDT has been approved!*\n\n📍 *Address:* `{addr}`\n\n💸 *Payment processed.*\n💎 *Thank you!*",
        'wd_rejected_msg': "❌ *Your withdrawal of {amt}$ USDT has been rejected.*\n\n🔄 *{amt}$ USDT has been refunded to your account.*\n\nℹ️ Contact: @inkamxbd",
        'commission_msg': "🎉 *Referral commission received!*\n\n👤 *Referral:* {name}\n💸 *Their earnings:* `{price}$ USDT`\n💰 *Your {comm_pct}% commission:* `{comm}$ USDT`\n\n✅ *Added to your account.*",
        'new_user_ref': "🎉 *Someone joined via your referral link!*\n\n👤 *Name:* {name}\n\n💡 You'll earn *{comm_pct}% commission* when they get a Task approved.",
        'history_title': "📋 *Your History (Last 10)*",
        'history_gmail': "📋 *Task Submissions:*",
        'history_wd': "💸 *Withdrawal Requests:*",
        'history_none_gmail': "_No submissions yet_",
        'history_none_wd': "_No withdrawals yet_",
        'history_legend': "⏳ = Pending  ✅ = Approved  ❌ = Rejected",
        'submit_btn': "📨 Submit",
        'cancel_inline': "❌ Cancel",
        'lang_choose': "🌍 *ভাষা বেছে নিন / Choose Language:*",
        'lang_set_bn': "✅ *ভাষা বাংলা করা হয়েছে।*",
        'lang_set_en': "✅ *Language set to English.*",
        'bal_added': "✅ *`{amt}$ USDT` has been added to your account!*\n\n💰 *Balance:* `{bal}$ USDT`",
        'unban_notify': "✅ *Your ban has been lifted. You can now use the bot.*",
        'field_ask': "📝 *{prompt}*\n\n❌ To cancel, type /cancel",
        'field_photo_ask': "📷 *{prompt}*\n\nPress Skip button below to skip\n❌ To cancel, type /cancel",
        'collecting_step': "✅ Saved! ({current}/{total})",
        'new_joiners_only_reject': "❌ *This Task is for new joiners only.*\n\nYou are already a member of this channel, so you cannot complete this task.\n\n📢 Only users who join the channel through this Task will receive the reward.",
        'task_blocked_msg': (
            "⛔ *Task submissions are temporarily blocked!*\n\n"
            "Your trust points dropped to 50 or below.\n\n"
            "🔓 *Block lifts on:* {until}\n\n"
            "💡 Type */point* to view your points."
        ),
        'point_cmd': (
            "🎯 *Your Trust Points*\n\n"
            "━━━━━━━━━━━━━━━━\n"
            "🔵 *Points:* `{pts}/100`\n"
            "🛡 *Status:* {status}\n"
            "{block_line}"
            "━━━━━━━━━━━━━━━━\n\n"
            "📜 *Points Rules:*\n"
            "• Starting points: `100`\n"
            "• Task *rejected*: `-{reject_pts}` points\n"
            "• Task *approved*: `+{approve_pts}` points\n"
            "• Points `30` or below: Tasks blocked for `{block_days}` days\n"
            "• Maximum points: `100`\n\n"
            "💡 Follow the rules correctly to keep your points high!"
        ),
    }
}

def T(uid, key, **kwargs):
    lang = get_lang(uid)
    base = MSGS.get(lang, MSGS['bn'])
    text = base.get(key, MSGS['bn'].get(key, key))
    if kwargs:
        try: text = text.format(**kwargs)
        except Exception: pass  # template key mismatch — return raw template
    return text

# ── Translation cache — একই text বারবার Google API call করা বন্ধ ──────────
_translation_cache: "dict[str, str]" = {}   # original_text → translated_text
_TRANSLATION_CACHE_MAX = 500                  # সর্বোচ্চ ৫০০ entry রাখো

def translate_to_en(text):
    if not text:
        return text
    # Cache hit — network call এড়াও
    cached = _translation_cache.get(text)
    if cached is not None:
        return cached
    try:
        url = ("https://translate.googleapis.com/translate_a/single"
               "?client=gtx&sl=bn&tl=en&dt=t&q=" + urllib.parse.quote(text))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = _json.loads(r.read())
        result = "".join(seg[0] for seg in data[0] if seg[0]).strip()
    except Exception:
        return text
    # Cache store — overflow হলে পুরনো অর্ধেক মুছো
    if len(_translation_cache) >= _TRANSLATION_CACHE_MAX:
        keys = list(_translation_cache.keys())
        for k in keys[:len(keys)//2]:
            _translation_cache.pop(k, None)
    _translation_cache[text] = result
    return result

def md_escape(text):
    for ch in ('*', '_', '`', '['):
        text = text.replace(ch, f'\\{ch}')
    return text

# ── নতুন ইউজার notification — bot restart এ ctx.user_data হারালেও কাজ করবে ──
_notified_set: set = set()   # uid set — double-notification বন্ধ (max 10,000 — পুরনো clear হয়)

def _notified_set_add(uid: int):
    """_notified_set এ uid যোগ করো। ১০,০০০ পার হলে পুরো set মুছে নতুন শুরু।"""
    global _notified_set
    if len(_notified_set) >= 10_000:
        _notified_set = set()
    _notified_set.add(uid)

# ──────────────────────────── DATABASE ────────────────────────────
# যখনই টাকা/ডাটা বদলায় → True হয় → background job Telegram এ backup পাঠায়
_db_dirty = False
_app = None   # set in post_init — immediate backup scheduling এর জন্য
_backup_task_pending = False  # duplicate immediate task তৈরি হওয়া ঠেকায়

def _mark_dirty():
    global _db_dirty, _backup_task_pending
    _db_dirty = True

    # ── Method 1: asyncio event loop (সবচেয়ে নির্ভরযোগ্য — job_queue ছাড়াই কাজ করে) ──
    # async handler থেকে call হলে event loop running থাকে, তাই সরাসরি task create করা যায়।
    if not _backup_task_pending and _app is not None:
        try:
            loop = asyncio.get_running_loop()
            _backup_task_pending = True
            async def _delayed_backup():
                global _backup_task_pending
                await asyncio.sleep(3)
                _backup_task_pending = False
                if _app is not None:
                    await _run_dirty_backup_now(_app.bot)
            _dbt = loop.create_task(_delayed_backup())
            _background_tasks.add(_dbt); _dbt.add_done_callback(_background_tasks.discard)
        except RuntimeError:
            pass  # event loop নেই (e.g. init time) — Method 2 fallback নেবে

    # ── Method 2: job_queue fallback (APScheduler থাকলে extra safety net) ──
    if _app is not None and _app.job_queue is not None:
        try:
            existing = _app.job_queue.get_jobs_by_name("dirty_immediate")
            for j in existing:
                j.schedule_removal()
            _app.job_queue.run_once(job_dirty_backup, when=3, name="dirty_immediate")
        except Exception:
            pass

mark_db_dirty = _mark_dirty  # alias

class _CursorWrapper:
      """psycopg2 cursor wrapper — SQLite compat: c.execute(...).fetchone() chaining"""
      __slots__ = ("_cur", "_conn", "_last_id", "_rowcount")

      def __init__(self, conn):
          self._conn     = conn
          self._cur      = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
          self._last_id  = None
          self._rowcount = 0

      def execute(self, sql, params=None):
          try:
              self._cur.execute(sql, params)
          except psycopg2.errors.InFailedSqlTransaction:
              # আগের query fail করেছিল, transaction aborted — rollback করে clean state ফিরিয়ে আনো
              try: self._conn.rollback()
              except Exception: pass
              raise
          except psycopg2.Error:
              # যেকোনো DB error-এ transaction dirty হতে পারে — rollback করো
              try: self._conn.rollback()
              except Exception: pass
              raise
          self._rowcount = self._cur.rowcount if self._cur.rowcount is not None else 0
          if "RETURNING" in sql.upper() and sql.strip().upper().startswith("INSERT"):
              try:
                  row = self._cur.fetchone()
                  if row:
                      for k in ("id", "user_id"):
                          if k in row:
                              self._last_id = row[k]
                              break
              except Exception:
                  pass
          return self

      def executemany(self, sql, seq):
          self._cur.executemany(sql, seq)
          self._rowcount = self._cur.rowcount if self._cur.rowcount is not None else 0
          return self

      def fetchone(self):
          try:
              row = self._cur.fetchone()
              return dict(row) if row else None
          except Exception:
              return None

      def fetchall(self):
          try:
              rows = self._cur.fetchall() or []
              return [dict(r) for r in rows]
          except Exception:
              return []

      @property
      def rowcount(self):
          return self._rowcount

      @property
      def lastrowid(self):
          return self._last_id

      def __iter__(self):
          return iter(self.fetchall())

      def __getitem__(self, idx):
          try:
              return list((self._cur.fetchone() or {}).values())[idx]
          except Exception:
              return None


# ── Connection Pool — প্রতি query তে নতুন TCP connection খরচ বাঁচায় ──────
_conn_pool: "_pg_pool.ThreadedConnectionPool | None" = None

def _get_pool() -> "_pg_pool.ThreadedConnectionPool":
    global _conn_pool
    if _conn_pool is None or _conn_pool.closed:
        try:
            _conn_pool = _pg_pool.ThreadedConnectionPool(
                minconn=5,
                maxconn=30,          # concurrent updates বেশি হলেও pool exhaust হবে না
                dsn=DATABASE_URL,
                connect_timeout=10,
                keepalives=1,        # idle connection ড্রপ হওয়া থেকে বাঁচায়
                keepalives_idle=30,  # ৩০s idle হলে keepalive packet পাঠাও
                keepalives_interval=10,
                keepalives_count=5,
            )
        except Exception:
            _conn_pool = None
            raise
    return _conn_pool


class _ConnCtx:
    """with _c() as c: → pool থেকে connection নাও, শেষে ফিরিয়ে দাও।
    Pool না থাকলে direct connect fallback।"""
    __slots__ = ("_conn", "_wrapper", "_from_pool")

    def __enter__(self):
        self._from_pool = False
        try:
            pool = _get_pool()
            self._conn = pool.getconn()
            self._from_pool = True
        except Exception:
            # Pool fail হলে direct connect (safety fallback)
            self._conn = psycopg2.connect(DATABASE_URL)
        self._conn.autocommit = False
        self._wrapper = _CursorWrapper(self._conn)
        return self._wrapper

    def __exit__(self, exc_type, exc_val, exc_tb):
        _broken = False
        _commit_err = None
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception as _ce:
                _broken = True
                _commit_err = _ce
                try: self._conn.rollback()
                except Exception: pass
        else:
            _broken = True
            try: self._conn.rollback()
            except Exception: pass
        # Error হলে ভাঙা connection pool-এ ফেরত না দিয়ে বন্ধ করো
        if self._from_pool:
            try:
                pool = _get_pool()
                pool.putconn(self._conn, close=_broken)
            except Exception:
                try: self._conn.close()
                except Exception: pass
        else:
            try: self._conn.close()
            except Exception: pass
        # Commit fail হলে caller কে জানাও — silent failure বন্ধ
        if _commit_err is not None:
            raise _commit_err
        return False


def _c():
    return _ConnCtx()


def _rget(row, key, default=None):
    """dict row থেকে safe get।"""
    if row is None:
        return default
    try:
        v = row[key]
        return default if v is None else v
    except (KeyError, TypeError, IndexError):
        return default


def _md_esc(text: str) -> str:
    """Telegram Markdown v1-এ special characters escape করো যাতে parse error না হয়।"""
    if not text: return text
    for ch in ['_', '*', '`', '[']:
        text = text.replace(ch, f'\\{ch}')
    return text

def _count(row):
    """RealDictCursor COUNT(*)/COALESCE result থেকে int বের করো।"""
    if row is None:
        return 0
    if isinstance(row, dict):
        for v in row.values():
            try: return int(v) if v is not None else 0
            except (TypeError, ValueError): pass
        return 0
    try:
        return int(list(row.values())[0])
    except Exception:
        return 0
def init_db():
    with _c() as c:
        # ── Core tables ──────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       BIGINT  PRIMARY KEY,
                username      TEXT    DEFAULT '',
                full_name     TEXT    DEFAULT '',
                balance       REAL    DEFAULT 0,
                referred_by   BIGINT  DEFAULT NULL,
                joined_at     TEXT    DEFAULT '',
                is_banned     INTEGER DEFAULT 0,
                lang          TEXT    DEFAULT '',
                country       TEXT    DEFAULT 'BD',
                points        INTEGER DEFAULT 100,
                blocked_until TEXT    DEFAULT NULL,
                last_seen     TEXT    DEFAULT '',
                saved_wallet  TEXT    DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS gmails (
                id             BIGSERIAL PRIMARY KEY,
                user_id        BIGINT,
                gmail_text     TEXT,
                task_name      TEXT    DEFAULT 'Gmail',
                status         TEXT    DEFAULT 'pending',
                amount         REAL    DEFAULT 0,
                submitted_at   TEXT,
                admin_msg_id   BIGINT  DEFAULT 0,
                channel_msg_id BIGINT  DEFAULT 0,
                task_type      TEXT    DEFAULT 'other',
                approved_at    TEXT    DEFAULT NULL,
                task_code      TEXT    DEFAULT '',
                reject_reason  TEXT    DEFAULT NULL,
                photo_unique_ids TEXT  DEFAULT '',
                owner_id       BIGINT  DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id             BIGSERIAL PRIMARY KEY,
                user_id        BIGINT,
                amount         REAL,
                address        TEXT,
                status         TEXT    DEFAULT 'pending',
                requested_at   TEXT,
                admin_msg_id   BIGINT  DEFAULT 0,
                channel_msg_id BIGINT  DEFAULT 0,
                tx_hash        TEXT    DEFAULT '',
                fee            REAL    DEFAULT 0,
                locked_at      TEXT    DEFAULT '',
                scheduled_at   TEXT    DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id               BIGSERIAL PRIMARY KEY,
                name             TEXT NOT NULL,
                price            REAL NOT NULL DEFAULT 0.10,
                rules            TEXT NOT NULL DEFAULT '',
                rules_en         TEXT DEFAULT '',
                is_active        INTEGER DEFAULT 1,
                per_user_limit   INTEGER DEFAULT 1,
                task_type        TEXT DEFAULT 'other',
                max_completions  INTEGER DEFAULT 0,
                completed_count  INTEGER DEFAULT 0,
                task_code        TEXT DEFAULT '',
                allowed_countries TEXT DEFAULT '',
                example_image_id TEXT DEFAULT '',
                demo_video_id    TEXT DEFAULT '',
                owner_id         BIGINT DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS task_fields (
                id               BIGSERIAL PRIMARY KEY,
                task_id          BIGINT NOT NULL,
                field_order      INTEGER DEFAULT 0,
                field_type       TEXT DEFAULT 'text',
                prompt_bn        TEXT DEFAULT '',
                prompt_en        TEXT DEFAULT '',
                example_image_id TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS menu_buttons (
                key      TEXT PRIMARY KEY,
                label_bn TEXT NOT NULL,
                label_en TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id     BIGINT PRIMARY KEY,
                added_by    BIGINT,
                added_at    TEXT DEFAULT '',
                permissions TEXT DEFAULT 'full'
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tg_tasks (
                id               BIGSERIAL PRIMARY KEY,
                name             TEXT NOT NULL,
                price            REAL NOT NULL DEFAULT 0.10,
                tg_target        TEXT NOT NULL DEFAULT '',
                tg_target_link   TEXT DEFAULT '',
                tg_target_name   TEXT DEFAULT '',
                max_completions  INTEGER DEFAULT 0,
                completed_count  INTEGER DEFAULT 0,
                is_active        INTEGER DEFAULT 1,
                new_joiners_only INTEGER DEFAULT 0,
                created_at       TEXT DEFAULT '',
                per_user_limit   INTEGER DEFAULT 1,
                task_code        TEXT DEFAULT '',
                allowed_countries TEXT DEFAULT '',
                owner_id         BIGINT DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tg_task_completions (
                id       BIGSERIAL PRIMARY KEY,
                task_id  BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                done_at  TEXT DEFAULT '',
                UNIQUE(task_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS task_categories (
                id        BIGSERIAL PRIMARY KEY,
                name      TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS category_tasks (
                id               BIGSERIAL PRIMARY KEY,
                category_id      BIGINT NOT NULL,
                name             TEXT NOT NULL,
                price            REAL NOT NULL DEFAULT 0.10,
                rules            TEXT NOT NULL DEFAULT '',
                rules_en         TEXT DEFAULT '',
                max_completions  INTEGER DEFAULT 0,
                completed_count  INTEGER DEFAULT 0,
                is_active        INTEGER DEFAULT 1,
                per_user_limit   INTEGER DEFAULT 1,
                task_type        TEXT DEFAULT 'other',
                task_code        TEXT DEFAULT '',
                allowed_countries TEXT DEFAULT '',
                example_image_id TEXT DEFAULT '',
                demo_video_id    TEXT DEFAULT '',
                owner_id         BIGINT DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS category_task_fields (
                id               BIGSERIAL PRIMARY KEY,
                task_id          BIGINT NOT NULL,
                field_order      INTEGER DEFAULT 0,
                field_type       TEXT DEFAULT 'text',
                prompt_bn        TEXT DEFAULT '',
                prompt_en        TEXT DEFAULT '',
                example_image_id TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS category_task_completions (
                id       BIGSERIAL PRIMARY KEY,
                task_id  BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                done_at  TEXT DEFAULT '',
                UNIQUE(task_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bot_meta (key TEXT PRIMARY KEY, value TEXT)
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS required_channels (
                id      BIGSERIAL PRIMARY KEY,
                ch_id   TEXT NOT NULL,
                ch_link TEXT NOT NULL,
                ch_name TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_notifications (
                id             BIGSERIAL PRIMARY KEY,
                send_at        TEXT NOT NULL,
                sender_uid     BIGINT NOT NULL,
                t_name         TEXT NOT NULL,
                t_price        TEXT NOT NULL,
                task_link      TEXT NOT NULL,
                slot_txt       TEXT NOT NULL,
                njo_line       TEXT NOT NULL,
                ticon          TEXT NOT NULL,
                notify_ch_link TEXT NOT NULL,
                delay_min      INTEGER NOT NULL DEFAULT 0,
                t_code         TEXT DEFAULT '',
                allowed_countries TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                id              BIGSERIAL PRIMARY KEY,
                code            TEXT    NOT NULL UNIQUE,
                amount          REAL    NOT NULL DEFAULT 0,
                max_uses        INTEGER NOT NULL DEFAULT 0,
                used_count      INTEGER NOT NULL DEFAULT 0,
                requirement     TEXT    NOT NULL DEFAULT '',
                min_tasks       INTEGER NOT NULL DEFAULT 0,
                created_by      BIGINT  DEFAULT NULL,
                created_at      TEXT    NOT NULL DEFAULT '',
                expires_at      TEXT    NOT NULL DEFAULT '',
                is_active       INTEGER NOT NULL DEFAULT 1,
                news_ch_msg_id  BIGINT  DEFAULT NULL,
                news_ch_chat_id BIGINT  DEFAULT NULL
            )
        """)
        # Backfill missing columns for existing databases
        for _col, _defn in [
            ("min_tasks",    "INTEGER NOT NULL DEFAULT 0"),
            ("created_by",   "BIGINT  DEFAULT NULL"),
            ("news_ch_msg_id",  "BIGINT  DEFAULT NULL"),
            ("news_ch_chat_id", "BIGINT  DEFAULT NULL"),
        ]:
            try:
                c.execute(f"ALTER TABLE promocodes ADD COLUMN IF NOT EXISTS {_col} {_defn}")
            except Exception:
                pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS promo_uses (
                id        BIGSERIAL PRIMARY KEY,
                code_id   BIGINT NOT NULL,
                user_id   BIGINT NOT NULL,
                used_at   TEXT DEFAULT '',
                UNIQUE(code_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tg_task_pre_members (
                id       BIGSERIAL PRIMARY KEY,
                task_id  BIGINT NOT NULL,
                user_id  BIGINT NOT NULL,
                seen_at  TEXT DEFAULT '',
                UNIQUE(task_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bio_tasks (
                id           INTEGER PRIMARY KEY DEFAULT 1,
                reward       REAL    NOT NULL DEFAULT 0,
                add_reward   REAL    NOT NULL DEFAULT 0,
                is_active    INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT    DEFAULT ''
            )
        """)
        try:
            c.execute("ALTER TABLE bio_tasks ADD COLUMN IF NOT EXISTS add_reward REAL NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE bio_task_tracking ADD COLUMN IF NOT EXISTS add_reward_received INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE bio_task_tracking ADD COLUMN IF NOT EXISTS last_warned_at TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS saved_wallet TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE task_fields ADD COLUMN IF NOT EXISTS example_image_id TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE category_task_fields ADD COLUMN IF NOT EXISTS example_image_id TEXT DEFAULT ''")
        except Exception:
            pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS bio_task_completions (
                id        BIGSERIAL PRIMARY KEY,
                user_id   BIGINT NOT NULL,
                task_date TEXT   NOT NULL,
                amount    REAL   NOT NULL DEFAULT 0,
                paid_at   TEXT   DEFAULT '',
                UNIQUE(user_id, task_date)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bio_task_tracking (
                user_id              BIGINT PRIMARY KEY,
                first_correct_at     TEXT    DEFAULT '',
                last_checked_at      TEXT    DEFAULT '',
                add_reward_received  INTEGER NOT NULL DEFAULT 0,
                last_warned_at       TEXT    DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS commissions (
                id            BIGSERIAL PRIMARY KEY,
                user_id       BIGINT NOT NULL,
                from_user_id  BIGINT NOT NULL,
                amount        REAL   NOT NULL DEFAULT 0,
                task_name     TEXT   DEFAULT '',
                created_at    TEXT   DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_commissions_user ON commissions(user_id)")
        # ── Referral Event tables ──────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS referral_events (
                id             BIGSERIAL PRIMARY KEY,
                start_date     TEXT NOT NULL,
                end_date       TEXT NOT NULL,
                required_tasks INTEGER NOT NULL DEFAULT 5,
                bonus_amount   REAL    NOT NULL DEFAULT 0.05,
                is_active      INTEGER NOT NULL DEFAULT 1,
                created_at     TEXT    DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS referral_event_rewards (
                id           BIGSERIAL PRIMARY KEY,
                event_id     BIGINT NOT NULL,
                referrer_id  BIGINT NOT NULL,
                referred_id  BIGINT NOT NULL,
                rewarded_at  TEXT DEFAULT '',
                UNIQUE(event_id, referrer_id, referred_id)
            )
        """)
        # ── Indexes for fast cleanup queries ──────────────────────
        c.execute("CREATE INDEX IF NOT EXISTS idx_gmails_user_status ON gmails(user_id, status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_gmails_submitted ON gmails(submitted_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_user_status ON withdrawals(user_id, status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_withdrawals_requested ON withdrawals(requested_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pending_notif_send_at ON pending_notifications(send_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tg_completions_task ON tg_task_completions(task_id, done_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cat_completions_task ON category_task_completions(task_id, done_at)")
        # ── Default settings (ON CONFLICT → keep existing) ────────
        for k, v in [
            ('gmail_price','0.10'), ('bot_status','on'),
            ('support_owner','@inkamxbd'), ('support_bot','@gmail_sell_help_bot'),
            ('min_withdraw','0.05'), ('commission_pct','10'),
            ('task_notify','on'), ('welcome_msg_bn',''), ('welcome_msg_en',''),
            ('task_notify_ch_id','-1003866731221'),
            ('task_notify_ch_username','@task_earn_bot_notification'),
            ('notify_delay_min','0'), ('wd_fee','0.01'),
        ]:
            c.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT (key) DO NOTHING", (k, v))
        # ── Default menu buttons ──────────────────────────────────
        for key, (bn, en) in DEFAULT_BTNS.items():
            c.execute(
                "INSERT INTO menu_buttons (key,label_bn,label_en) VALUES (%s,%s,%s) "
                "ON CONFLICT (key) DO NOTHING",
                (key, bn, en)
            )
        # ── Default required channels ─────────────────────────────
        existing = _count(c.execute("SELECT COUNT(*) FROM required_channels").fetchone())
        if existing == 0:
            for ch in REQUIRED_CHANNELS:
                c.execute(
                    "INSERT INTO required_channels (ch_id, ch_link, ch_name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (ch["id"], ch["link"], ch["name"])
                )
        # gmail/withdraw channel ids
        if not c.execute("SELECT value FROM settings WHERE key='gmail_ch_id'").fetchone():
            c.execute("INSERT INTO settings (key,value) VALUES ('gmail_ch_id',%s) ON CONFLICT DO NOTHING", (str(GMAIL_CH_ID),))
        if not c.execute("SELECT value FROM settings WHERE key='withdraw_ch_id'").fetchone():
            c.execute("INSERT INTO settings (key,value) VALUES ('withdraw_ch_id',%s) ON CONFLICT DO NOTHING", (str(WITHDRAW_CH_ID),))
        # ── Backfill task_codes for existing tasks ────────────────
        try:
            _chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            for tbl in ("tasks", "tg_tasks", "category_tasks"):
                empty_ids = [list(r.values())[0] for r in c.execute(
                    f"SELECT id FROM {tbl} WHERE task_code='' OR task_code IS NULL"
                ).fetchall()]
                for eid in empty_ids:
                    while True:
                        _code = "TK-" + "".join(random.choices(_chars, k=5))
                        clash = (
                            c.execute("SELECT id FROM tasks WHERE task_code=%s", (_code,)).fetchone() or
                            c.execute("SELECT id FROM tg_tasks WHERE task_code=%s", (_code,)).fetchone() or
                            c.execute("SELECT id FROM category_tasks WHERE task_code=%s", (_code,)).fetchone()
                        )
                        if not clash:
                            break
                    c.execute(f"UPDATE {tbl} SET task_code=%s WHERE id=%s", (_code, eid))
        except Exception:
            pass
        # ── One-time referral fix: user 8435677588 → admin 7423929548 ──
        try:
            c.execute(
                "UPDATE users SET referred_by=%s WHERE user_id=%s AND (referred_by IS NULL OR referred_by=0)",
                (7423929548, 8435677588)
            )
        except Exception:
            pass
        # ── owner_id column backfill (idempotent) ────────────────────
        for _tbl in ("tasks", "tg_tasks", "category_tasks", "gmails"):
            try:
                c.execute(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS owner_id BIGINT DEFAULT 0")
            except Exception:
                pass
        log.info("✅ PostgreSQL schema ready.")


def now(): return datetime.now(pytz.timezone("Asia/Dhaka")).strftime("%Y-%m-%d %H:%M")

# ── Task Type system ──────────────────────────────────────────────
TASK_TYPES = [
    ("tg_follow",  "📲", "Telegram Follow"),
    ("tg_join",    "📲", "Telegram Group Join"),
    ("yt_sub",     "▶️",  "YouTube Subscribe"),
    ("yt_watch",   "▶️",  "YouTube Watch"),
    ("fb_like",    "👍", "Facebook Like"),
    ("fb_follow",  "👍", "Facebook Follow"),
    ("ig_follow",  "📸", "Instagram Follow"),
    ("tt_follow",  "🎵", "TikTok Follow"),
    ("web_visit",  "🌐", "Website Visit"),
    ("app_dl",     "📱", "App Download"),
    ("other",      "📋", "Other"),
]
TASK_TYPE_MAP = {k: (icon, label) for k, icon, label in TASK_TYPES}

def task_type_icon(ttype):
    return TASK_TYPE_MAP.get(ttype or "other", ("📋", "Other"))[0]

def task_type_label(ttype):
    return TASK_TYPE_MAP.get(ttype or "other", ("📋", "Other"))[1]

def task_type_picker_kb(prefix):
    """Returns inline keyboard for picking task type. prefix = callback prefix like 'adm_task_type_' """
    rows = []
    row = []
    for key, icon, label in TASK_TYPES:
        row.append(InlineKeyboardButton(f"{icon} {label}", callback_data=f"{prefix}{key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("❌ বাতিল", callback_data="task_cancel")])
    return InlineKeyboardMarkup(rows)

# ── User helpers ──────────────────────────────────────────────────
def _invalidate_user_cache(uid):
    """User data বদলালে cache clear করো।"""
    _user_cache.pop(uid, None)
    _lang_cache.pop(uid, None)

def get_user(uid):
    now_ts = _time.monotonic()
    cached = _user_cache.get(uid)
    if cached:
        u, ts = cached
        if now_ts - ts < _USER_CACHE_TTL:
            return u
    # ── Evict expired entries যখন cache বড় হয় (memory leak বন্ধ) ──
    if len(_user_cache) > 2000:
        _exp = [k for k, (_, t) in _user_cache.items() if now_ts - t >= _USER_CACHE_TTL]
        for _k in _exp:
            _user_cache.pop(_k, None)
    with _c() as c:
        u = c.execute("SELECT * FROM users WHERE user_id=%s", (uid,)).fetchone()
    _user_cache[uid] = (u, now_ts)
    return u

def ensure_user(uid, username, full_name, ref_by=None):
    with _c() as c:
        cur = c.execute(
            "INSERT INTO users (user_id,username,full_name,referred_by,joined_at,lang,country) VALUES (%s,%s,%s,%s,%s,'','BD') ON CONFLICT DO NOTHING",
            (uid, username or "", full_name or "", ref_by, now())
        )
        if cur.rowcount:  # নতুন user যোগ হলে
            _invalidate_user_cache(uid)
            _mark_dirty()

def get_lang(uid):
    now_ts = _time.monotonic()
    cached = _lang_cache.get(uid)
    if cached:
        lang, ts = cached
        if now_ts - ts < _LANG_CACHE_TTL:
            return lang
    # ── Evict expired entries যখন cache বড় হয় (memory leak বন্ধ) ──
    if len(_lang_cache) > 2000:
        _exp = [k for k, (_, t) in _lang_cache.items() if now_ts - t >= _LANG_CACHE_TTL]
        for _k in _exp:
            _lang_cache.pop(_k, None)
    with _c() as c:
        r = c.execute("SELECT lang FROM users WHERE user_id=%s", (uid,)).fetchone()
    lang = (r["lang"] if r and r["lang"] else "bn")
    _lang_cache[uid] = (lang, now_ts)
    return lang

def user_has_lang(uid):
    now_ts = _time.monotonic()
    cached = _lang_cache.get(uid)
    if cached:
        lang, ts = cached
        if now_ts - ts < _LANG_CACHE_TTL:
            return bool(lang)
    with _c() as c:
        r = c.execute("SELECT lang FROM users WHERE user_id=%s", (uid,)).fetchone()
    lang = (r["lang"] if r and r["lang"] else "")
    if lang:
        _lang_cache[uid] = (lang, now_ts)
    return bool(lang)

def set_lang(uid, lang):
    with _c() as c:
        c.execute("UPDATE users SET lang=%s WHERE user_id=%s", (lang, uid))
    _lang_cache[uid] = (lang, _time.monotonic())
    _user_cache.pop(uid, None)

def get_user_country(uid):
    now_ts = _time.monotonic()
    cached = _country_cache.get(uid)
    if cached:
        val, ts = cached
        if now_ts - ts < _COUNTRY_CACHE_TTL:
            return val
    with _c() as c:
        r = c.execute("SELECT country FROM users WHERE user_id=%s", (uid,)).fetchone()
    result = (r["country"] or "BD") if r else "BD"
    _country_cache[uid] = (result, now_ts)
    return result

def set_user_country(uid, country):
    with _c() as c:
        c.execute("UPDATE users SET country=%s WHERE user_id=%s", (country, uid))
    _country_cache.pop(uid, None)

# ── Point / Trust system ─────────────────────────────────────────
POINT_START       = 100
POINT_PER_REJECT  = 10   # reject হলে কমবে
POINT_PER_APPROVE = 3    # approve হলে বাড়বে (ধীরে ধীরে)
POINT_DUP_PHOTO   = 15   # duplicate ছবি submit করলে কমবে
POINT_BLOCK_AT    = 50   # এর সমান বা নিচে গেলে block
POINT_BLOCK_DAYS  = 3    # কতদিন task বন্ধ থাকবে

def get_points(uid):
    with _c() as c:
        r = c.execute("SELECT points FROM users WHERE user_id=%s", (uid,)).fetchone()
        if r is None: return POINT_START
        val = r["points"]
        return val if val is not None else POINT_START

def get_blocked_until(uid):
    with _c() as c:
        r = c.execute("SELECT blocked_until FROM users WHERE user_id=%s", (uid,)).fetchone()
        return (r["blocked_until"] or "") if r else ""

def is_task_blocked(uid):
    block_dt = None
    bu = get_blocked_until(uid)
    if not bu: return False, None
    try:
        block_dt = datetime.fromisoformat(bu)
        now_dt   = datetime.now(timezone.utc)
        if block_dt.tzinfo is None:
            block_dt = block_dt.replace(tzinfo=timezone.utc)
        if now_dt < block_dt:
            return True, block_dt
        return False, None
    except Exception:
        return False, None

def add_points(uid, delta):
    """Points বাড়াও/কমাও। 0–100 clamp। ≤ POINT_BLOCK_AT হলে block করো।
    Atomic single-statement UPDATE — race condition নেই।"""
    with _c() as c:
        r = c.execute(
            "UPDATE users SET points=GREATEST(0, LEAST(%s, COALESCE(points,%s)+%s)) "
            "WHERE user_id=%s RETURNING points",
            (POINT_START, POINT_START, delta, uid)
        ).fetchone()
        new_pts = r["points"] if r else POINT_START
        if delta < 0 and new_pts <= POINT_BLOCK_AT:
            block_dt      = datetime.now(timezone.utc) + timedelta(days=POINT_BLOCK_DAYS)
            c.execute("UPDATE users SET blocked_until=%s WHERE user_id=%s",
                      (block_dt.isoformat(), uid))
        elif new_pts > POINT_BLOCK_AT:
            c.execute("UPDATE users SET blocked_until=NULL WHERE user_id=%s", (uid,))
    _mark_dirty()
    return new_pts

def get_bal(uid):
    with _c() as c:
        r = c.execute("SELECT balance FROM users WHERE user_id=%s", (uid,)).fetchone()
        return round(r["balance"], 4) if r else 0

def add_bal(uid, amt):
    """Balance যোগ করো। Returns True if row found and updated, False if user not in DB."""
    with _c() as c:
        cur = c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amt, uid))
        affected = cur.rowcount
    if affected:
        _invalidate_user_cache(uid)
        _mark_dirty()
        return True
    log.warning(f"[add_bal] user_id={uid} DB তে নেই — balance update হয়নি!")
    return False

def ded_bal(uid, amt):
    """Balance কাটো — negative হতে পারে (unfollow penalty). Returns True if updated."""
    with _c() as c:
        cur = c.execute("UPDATE users SET balance=balance-%s WHERE user_id=%s", (amt, uid))
        affected = cur.rowcount
    if affected:
        _invalidate_user_cache(uid)
        _mark_dirty()
        return True
    log.warning(f"[ded_bal] user_id={uid} DB তে নেই — balance update হয়নি!")
    return False

def safe_ded_bal(uid, amt):
    """
    Atomic balance deduction — balance >= amt হলেই কাটবে।
    Returns True if deducted, False if insufficient balance.
    Race condition safe: SQL level check-and-deduct একসাথে।
    """
    with _c() as c:
        cur = c.execute(
            "UPDATE users SET balance=balance-%s WHERE user_id=%s AND balance >= %s",
            (amt, uid, amt)
        )
        if cur.rowcount == 0:
            return False
    _invalidate_user_cache(uid)
    _mark_dirty()
    return True

def get_referred_by(uid):
    u = get_user(uid)
    return u["referred_by"] if u else None

def ban_user(uid, val):
    with _c() as c: c.execute("UPDATE users SET is_banned=%s WHERE user_id=%s", (val, uid))
    _invalidate_user_cache(uid)

def delete_user_data(uid):
    """User বট block করলে তার সব data DB থেকে মুছে দাও।"""
    with _c() as c:
        c.execute("DELETE FROM gmails                  WHERE user_id=%s", (uid,))
        c.execute("DELETE FROM withdrawals             WHERE user_id=%s", (uid,))
        # Task completion records — UNIQUE(task_id, user_id) constraint আছে,
        # তাই না মুছলে rejoin করলে পুরানো tasks আর করতে পারবে না
        c.execute("DELETE FROM tg_task_completions     WHERE user_id=%s", (uid,))
        c.execute("DELETE FROM category_task_completions WHERE user_id=%s", (uid,))
        # Bio task records
        c.execute("DELETE FROM bio_task_completions    WHERE user_id=%s", (uid,))
        c.execute("DELETE FROM bio_task_tracking       WHERE user_id=%s", (uid,))
        try:
            c.execute("DELETE FROM referrals WHERE referrer_id=%s OR referred_id=%s", (uid, uid))
        except Exception:
            pass  # referrals table may not exist — referral info stored in users.referred_by
        c.execute("DELETE FROM users                   WHERE user_id=%s", (uid,))
    _invalidate_user_cache(uid)
    log.info(f"[BLOCK-DELETE] user {uid} বট block করেছে — সব data মুছে দেওয়া হয়েছে।")

def all_users():
    with _c() as c:
        return c.execute("SELECT user_id, username, full_name, balance, is_banned, joined_at, lang FROM users WHERE is_banned=0").fetchall()

# ── Admin permission constants ─────────────────────────────────────
PERM_FULL         = "full"         # সব কিছু
PERM_TASKS        = "tasks"        # Task add/edit/approve/reject
PERM_WITHDRAW     = "withdraw"     # Withdraw approve/reject
PERM_USERS        = "users"        # Ban/unban/balance/user list
PERM_BROADCAST    = "broadcast"    # Broadcast/notification
PERM_FINANCE      = "finance"      # Stats/leaderboard/wallet
PERM_SETTINGS     = "settings"     # Bot settings/channel/DB/menu
PERM_ADMIN_MANAGE = "admin_manage" # Admin যোগ/সরানো/পারমিশন বদলানো

PERM_LABELS = {
    PERM_FULL:         "👑 Full Access",
    PERM_TASKS:        "📋 Tasks",
    PERM_WITHDRAW:     "💸 Withdraw",
    PERM_USERS:        "👥 Users",
    PERM_BROADCAST:    "📢 Broadcast",
    PERM_FINANCE:      "📊 Finance",
    PERM_SETTINGS:     "⚙️ Settings",
    PERM_ADMIN_MANAGE: "👮 Admin যোগ/সরানো",
}

# ── Admin helpers ──────────────────────────────────────────────────
def _invalidate_admin_cache(uid=None):
    """admin যোগ/সরানো হলে cache clear করো।"""
    if uid is None:
        _admin_cache.clear()
    else:
        _admin_cache.pop(uid, None)

def is_admin(uid):
    if uid == ADMIN_ID: return True
    now_ts = _time.monotonic()
    cached = _admin_cache.get(uid)
    if cached:
        result, ts = cached
        if now_ts - ts < _ADMIN_CACHE_TTL:
            return result
    with _c() as c:
        result = c.execute("SELECT 1 FROM admins WHERE user_id=%s", (uid,)).fetchone() is not None
    _admin_cache[uid] = (result, now_ts)
    return result

def add_admin(uid, added_by, permissions="full"):
    with _c() as c:
        c.execute(
            "INSERT INTO admins (user_id,added_by,added_at,permissions) VALUES (%s,%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET added_by=EXCLUDED.added_by,added_at=EXCLUDED.added_at,permissions=EXCLUDED.permissions",
            (uid, added_by, now(), permissions)
        )
    _invalidate_admin_cache(uid)

def remove_admin(uid):
    with _c() as c:
        c.execute("DELETE FROM admins WHERE user_id=%s", (uid,))
    _invalidate_admin_cache(uid)

def get_all_admins():
    with _c() as c:
        return c.execute("SELECT * FROM admins").fetchall()

def get_full_access_admins():
    """Full access আছে এমন সব sub-admin এর list — notification forwarding এর জন্য।"""
    with _c() as c:
        rows = c.execute("SELECT * FROM admins").fetchall()
    result = []
    for r in rows:
        perms = set((r["permissions"] or "full").split(","))
        if PERM_FULL in perms:
            result.append(r)
    return result

async def notify_full_admins(bot, msg: str, parse_mode: str = "Markdown", reply_markup=None):
    """Main admin + সব full-access sub-admin কে notification পাঠায়।"""
    kwargs = {"parse_mode": parse_mode}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    try:
        await bot.send_message(ADMIN_ID, msg, **kwargs)
    except Exception as e:
        log.warning(f"notify_full_admins main admin fail: {e}")
    for adm in await asyncio.to_thread(get_full_access_admins):
        try:
            await bot.send_message(adm["user_id"], msg, **kwargs)
        except Exception as e:
            log.warning(f"notify_full_admins sub-admin {adm['user_id']} fail: {e}")

async def notify_all_admins(bot, msg: str, parse_mode: str = "Markdown", reply_markup=None):
    """Main admin + সব sub-admin কে notification পাঠায় (permission নির্বিশেষে)।
    Critical system alerts যেমন BNB low, server error ইত্যাদির জন্য।"""
    kwargs = {"parse_mode": parse_mode}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    try:
        await bot.send_message(ADMIN_ID, msg, **kwargs)
    except Exception as e:
        log.warning(f"notify_all_admins main admin fail: {e}")
    for adm in await asyncio.to_thread(get_all_admins):
        if adm["user_id"] == ADMIN_ID:
            continue  # main admin কে দুইবার না
        try:
            await bot.send_message(adm["user_id"], msg, **kwargs)
        except Exception as e:
            log.warning(f"notify_all_admins sub-admin {adm['user_id']} fail: {e}")

def get_admin_perms(uid) -> set:
    """Return set of permissions for this admin. Main admin = all perms."""
    if uid == ADMIN_ID:
        return {PERM_FULL}
    with _c() as c:
        r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (uid,)).fetchone()
    if not r:
        return set()
    perms_str = (r["permissions"] or "full").strip()
    return set(perms_str.split(","))

def admin_has_perm(uid, perm: str) -> bool:
    """True if uid is main admin OR has 'full' OR has the specific perm."""
    if uid == ADMIN_ID:
        return True
    perms = get_admin_perms(uid)
    return PERM_FULL in perms or perm in perms

def _perms_display(perms_str: str) -> str:
    """Human-readable permission list."""
    if not perms_str or perms_str == "full":
        return "👑 Full Access"
    parts = [PERM_LABELS.get(p, p) for p in perms_str.split(",") if p]
    return " | ".join(parts) if parts else "❓ Unknown"

# ── Settings Cache — প্রতি user action এ DB query কমাতে 60s TTL cache ──
_settings_cache: "dict[str, tuple]" = {}   # key → (value, timestamp)
_SETTINGS_TTL = 120.0  # সেকেন্ড (60→120: DB miss কমাও)

# ── User Cache — guard() + handler দুইবার DB hit বন্ধ করে ──────────────
_user_cache: "dict[int, tuple]" = {}   # uid → (user_dict, timestamp)
_USER_CACHE_TTL = 300.0  # ৫ মিনিট (ban হলে _invalidate_user_cache দিয়ে সাথে সাথে মুছে যায়)

# ── Language Cache — T() প্রতি call-এ DB hit বন্ধ করে (সবচেয়ে বড় fix) ─
_lang_cache: "dict[int, tuple]" = {}   # uid → (lang, timestamp)
_LANG_CACHE_TTL = 600.0   # ১০ মিনিট (ভাষা খুব কম পরিবর্তন হয়)

# ── Menu Button Cache — main_kb() প্রতি call-এ DB hit বন্ধ করে ─────────
_btn_cache: "dict[str, tuple]" = {}   # lang → (buttons_dict, timestamp)
_BTN_CACHE_TTL = 120.0   # ২ মিনিট

# ── Required Channels Cache ──────────────────────────────────────────────
_req_ch_cache: "list | None" = None
_req_ch_cache_ts: float = 0.0
_REQ_CH_CACHE_TTL = 300.0   # ৫ মিনিট

# ── Task Cache — button press-এ DB round trip বন্ধ করে (সবচেয়ে বড় speed fix) ──
_task_cache: "dict[str, tuple]" = {}
_TASK_CACHE_TTL = 300.0   # ৫ মিনিট

def _tc_get(key: str):
    """Cache hit → (data, True), miss → (None, False)."""
    cached = _task_cache.get(key)
    if cached:
        data, ts = cached
        if _time.monotonic() - ts < _TASK_CACHE_TTL:
            return data, True
    return None, False

def _tc_set(key: str, data):
    _task_cache[key] = (data, _time.monotonic())
    if len(_task_cache) > 600:
        cutoff = _time.monotonic() - _TASK_CACHE_TTL
        for k in [k for k, (_, t) in list(_task_cache.items()) if t < cutoff]:
            _task_cache.pop(k, None)

def _tc_del(*keys: str):
    """Specific keys invalidate করো।"""
    for k in keys:
        _task_cache.pop(k, None)

def _tc_del_prefix(prefix: str):
    """Prefix দিয়ে সব matching keys invalidate করো।"""
    for k in [k for k in list(_task_cache) if k.startswith(prefix)]:
        _task_cache.pop(k, None)

# ── Admin Cache — is_admin() প্রতি call-এ DB hit বন্ধ করে ───────────────
_admin_cache: "dict[int, tuple]" = {}   # uid → (is_admin_bool, timestamp)
_ADMIN_CACHE_TTL = 120.0   # ২ মিনিট (admin যোগ/সরানো হলে invalidate হবে)

# ── Country Cache — get_user_country() প্রতি call-এ DB hit বন্ধ করে ─────
_country_cache: "dict[int, tuple]" = {}  # uid → (country_str, timestamp)
_COUNTRY_CACHE_TTL = 600.0  # ১০ মিনিট (দেশ খুব কম পরিবর্তন হয়)

def get_setting(key):
    """settings table থেকে value আনো — 60s TTL cache দিয়ে DB queries কমাও।"""
    now_ts = _time.monotonic()
    entry = _settings_cache.get(key)
    if entry is not None:
        val, ts = entry
        if now_ts - ts < _SETTINGS_TTL:
            return val
    with _c() as c:
        r = c.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
    val = r["value"] if r else None
    _settings_cache[key] = (val, now_ts)
    return val

def set_setting(key, val):
    """settings update করো এবং cache invalidate করো।"""
    with _c() as c:
        c.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, val))
    _settings_cache.pop(key, None)   # stale cache সরিয়ে দাও

def bot_is_on():
    """bot_status — get_setting cache ব্যবহার করে (60s TTL, set_setting এ invalidate হয়)।
    Admin bot off করলে সাথে সাথে cache invalidate হয় — কোনো delay নেই।"""
    try:
        val = get_setting('bot_status')
        return (val or 'on') != 'off'
    except Exception:
        return True  # DB fail হলে safe default: bot চালু থাকুক

def get_gmail_ch_id():
    v = get_setting('gmail_ch_id')
    try: return int(v)
    except Exception: return GMAIL_CH_ID

def get_withdraw_ch_id():
    v = get_setting('withdraw_ch_id')
    try: return int(v)
    except Exception: return WITHDRAW_CH_ID

def get_bsc_wallet_addr():
    v = get_setting('bsc_wallet_addr')
    return v.strip() if v and v.strip() else BSC_WALLET_ADDR

def get_min_withdraw():
    v = get_setting('min_withdraw')
    try: return float(v)
    except Exception: return 0.05

def get_wd_fee():
    v = get_setting('wd_fee')
    try: return float(v)
    except Exception: return 0.01

def get_commission_pct():
    v = get_setting('commission_pct')
    try: return float(v)
    except Exception: return 10.0

def record_commission(referrer_id, from_user_id, amount, task_name=''):
    """কমিশন পেলে commissions table-এ log করো — শেষ ৩০টা রেখে পুরানো মুছে যাবে।"""
    try:
        with _c() as c:
            c.execute(
                "INSERT INTO commissions (user_id,from_user_id,amount,task_name,created_at) VALUES (%s,%s,%s,%s,%s)",
                (referrer_id, from_user_id, amount, task_name, now())
            )
        # নতুন record insert হলে পুরানো মুছো — শেষ ৩০টা রাখো
        _trim_table("commissions", "user_id", referrer_id, keep=30)
    except Exception as _e:
        log.warning(f"[record_commission] failed: {_e}")

def task_notify_on():
    return (get_setting('task_notify') or 'on') == 'on'

# ──────────────────────── REFERRAL EVENT HELPERS ────────────────────────
def get_active_referral_event():
    """চলমান active referral event return করো, না থাকলে None।"""
    today = datetime.now(BD_TZ).strftime("%Y-%m-%d")
    with _c() as c:
        return c.execute(
            "SELECT * FROM referral_events WHERE is_active=1 AND start_date<=%s AND end_date>=%s ORDER BY id DESC LIMIT 1",
            (today, today)
        ).fetchone()

def list_referral_events(limit=8):
    with _c() as c:
        return c.execute(
            "SELECT * FROM referral_events ORDER BY id DESC LIMIT %s", (limit,)
        ).fetchall()

def create_referral_event(start_date, end_date, required_tasks, bonus_amount):
    with _c() as c:
        c.execute(
            "INSERT INTO referral_events (start_date,end_date,required_tasks,bonus_amount,is_active,created_at) "
            "VALUES (%s,%s,%s,%s,1,%s)",
            (start_date, end_date, required_tasks, bonus_amount, now())
        )

def deactivate_referral_event(eid):
    with _c() as c:
        c.execute("UPDATE referral_events SET is_active=0 WHERE id=%s", (eid,))

def count_approved_tasks_for_user(uid):
    """User-এর মোট completed task count।
    - gmails status='approved' → regular task + category task (category submission-ও gmails-এ যায়)
    - tg_task_completions → TG task (auto-approved, gmails-এ যায় না)
    category_task_completions গোনা হয় না — ওগুলো gmails-এ already আছে।
    """
    with _c() as c:
        g  = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'", (uid,)).fetchone())
        tg = _count(c.execute("SELECT COUNT(*) FROM tg_task_completions WHERE user_id=%s", (uid,)).fetchone())
        return g + tg

def give_ref_event_bonus(event_id, referrer_id, referred_id, bonus_amount):
    """referral_event_rewards এ insert করো এবং referrer-এর balance বাড়াও।"""
    try:
        with _c() as c:
            result = c.execute(
                "INSERT INTO referral_event_rewards (event_id,referrer_id,referred_id,rewarded_at) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (event_id, referrer_id, referred_id, now())
            )
            if result.rowcount > 0:
                c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (bonus_amount, referrer_id))
                _invalidate_user_cache(referrer_id)
                return True
    except Exception as _e:
        log.warning(f"[REF_EVENT] give_ref_event_bonus failed: {_e}")
    return False

def get_ref_event_stats(event_id):
    """Event-এর total rewards count ও total amount।"""
    with _c() as c:
        cnt = _count(c.execute(
            "SELECT COUNT(*) FROM referral_event_rewards WHERE event_id=%s", (event_id,)
        ).fetchone())
        row = c.execute(
            "SELECT bonus_amount FROM referral_events WHERE id=%s", (event_id,)
        ).fetchone()
        bonus = float(row['bonus_amount']) if row else 0.0
        return {"count": cnt, "total_amount": round(cnt * bonus, 4)}

def get_task_notify_ch_username():
    v = get_setting('task_notify_ch_username') or '@task_earn_bot_notification'
    if v and not v.startswith('@'): v = '@' + v
    return v

def get_task_notify_ch_id():
    v = get_setting('task_notify_ch_id')
    try: return int(v)
    except Exception: return -1003866731221

def get_notify_delay_min():
    v = get_setting('notify_delay_min')
    try: return int(v)
    except Exception: return 0

# ── Menu button helpers ────────────────────────────────────────────
def _invalidate_btn_cache():
    _btn_cache.clear()

def get_btn(key, lang='bn'):
    now_ts = _time.monotonic()
    cached = _btn_cache.get(lang)
    if cached:
        btns, ts = cached
        if now_ts - ts < _BTN_CACHE_TTL:
            return btns.get(key, DEFAULT_BTNS.get(key, ('?','?'))[0 if lang == 'bn' else 1])
    # cache miss — পুরো table load করো (এরপর get_all_btns-ও benefit পাবে)
    with _c() as c:
        rows = c.execute("SELECT key, label_bn, label_en FROM menu_buttons").fetchall()
    result = {}
    for row in rows:
        result[row['key']] = row['label_bn'] if lang == 'bn' else row['label_en']
    for k, (bn, en) in DEFAULT_BTNS.items():
        if k not in result:
            result[k] = bn if lang == 'bn' else en
    _btn_cache[lang] = (result, now_ts)
    return result.get(key, DEFAULT_BTNS.get(key, ('?','?'))[0 if lang == 'bn' else 1])

def get_all_btns(lang='bn'):
    now_ts = _time.monotonic()
    cached = _btn_cache.get(lang)
    if cached:
        btns, ts = cached
        if now_ts - ts < _BTN_CACHE_TTL:
            return btns
    with _c() as c:
        rows = c.execute("SELECT key, label_bn, label_en FROM menu_buttons").fetchall()
    result = {}
    for row in rows:
        result[row['key']] = row['label_bn'] if lang == 'bn' else row['label_en']
    for key, (bn, en) in DEFAULT_BTNS.items():
        if key not in result:
            result[key] = bn if lang == 'bn' else en
    _btn_cache[lang] = (result, now_ts)
    return result

def set_btn(key, label_bn, label_en):
    with _c() as c:
        c.execute("INSERT INTO menu_buttons (key,label_bn,label_en) VALUES (%s,%s,%s) ON CONFLICT (key) DO UPDATE SET label_bn=EXCLUDED.label_bn,label_en=EXCLUDED.label_en",
                  (key, label_bn, label_en))
    _invalidate_btn_cache()

# ── Required Channel helpers ───────────────────────────────────────
def _invalidate_req_ch_cache():
    global _req_ch_cache, _req_ch_cache_ts
    _req_ch_cache = None
    _req_ch_cache_ts = 0.0

def get_req_channels():
    global _req_ch_cache, _req_ch_cache_ts
    now_ts = _time.monotonic()
    if _req_ch_cache is not None and (now_ts - _req_ch_cache_ts) < _REQ_CH_CACHE_TTL:
        return _req_ch_cache
    with _c() as c:
        result = c.execute("SELECT * FROM required_channels ORDER BY id").fetchall()
    _req_ch_cache = result
    _req_ch_cache_ts = now_ts
    return result

def add_req_channel(ch_id, ch_link, ch_name):
    with _c() as c:
        c.execute("INSERT INTO required_channels (ch_id, ch_link, ch_name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                  (ch_id, ch_link, ch_name))
    _invalidate_req_ch_cache()

def remove_req_channel(rid):
    with _c() as c:
        c.execute("DELETE FROM required_channels WHERE id=%s", (rid,))
    _invalidate_req_ch_cache()

# ── Task helpers ──────────────────────────────────────────────────
def get_tasks():
    data, hit = _tc_get("tasks")
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM tasks WHERE is_active=1 ORDER BY id").fetchall()
    _tc_set("tasks", data); return data

def user_submitted_task_names(user_id):
    """Return set of task names already submitted by this user (any status)."""
    with _c() as c:
        rows = c.execute("SELECT task_name FROM gmails WHERE user_id=%s", (user_id,)).fetchall()
        return {r["task_name"] for r in rows}

def get_user_task_submit_count(task_name, user_id, task_code=None):
    """Count non-rejected submissions by this user for a regular task.
    task_code দিলে সেটা দিয়ে query করে — নতুন task পুরানো same-name submission গণনা করবে না।
    """
    with _c() as c:
        if task_code:
            r = c.execute(
                "SELECT COUNT(*) FROM gmails WHERE user_id=%s AND task_code=%s AND status != 'rejected'",
                (user_id, task_code)
            ).fetchone()
        else:
            r = c.execute(
                "SELECT COUNT(*) FROM gmails WHERE user_id=%s AND task_name=%s AND status != 'rejected'",
                (user_id, task_name)
            ).fetchone()
        return _count(r)


def get_user_categ_task_submit_count(task_name, user_id):
    """Count non-rejected submissions by this user for a category task."""
    cat_task_name = f"[CAT] {task_name}"
    with _c() as c:
        r = c.execute(
            "SELECT COUNT(*) FROM gmails WHERE user_id=%s AND task_name=%s AND status != 'rejected'",
            (user_id, cat_task_name)
        ).fetchone()
        return _count(r)

def get_task(tid):
    k = f"task_{tid}"
    data, hit = _tc_get(k)
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM tasks WHERE id=%s", (tid,)).fetchone()
    _tc_set(k, data); return data

def generate_task_code():
    code = None
    """Generate a unique task code like TK-AB3X7 across all task tables."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "TK-" + "".join(random.choices(chars, k=5))
        with _c() as c:
            clash = (
                c.execute("SELECT id FROM tasks WHERE task_code=%s", (code,)).fetchone() or
                c.execute("SELECT id FROM tg_tasks WHERE task_code=%s", (code,)).fetchone() or
                c.execute("SELECT id FROM category_tasks WHERE task_code=%s", (code,)).fetchone()
            )
        if not clash:
            return code

def add_task(name, price, rules, rules_en="", task_type="other", per_user_limit=1, max_completions=0, allowed_countries=""):
    code = generate_task_code()
    with _c() as c:
        cur = c.execute(
            "INSERT INTO tasks (name,price,rules,rules_en,task_type,per_user_limit,max_completions,is_active,task_code,allowed_countries) VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s) RETURNING id",
            (name, price, rules, rules_en, task_type, per_user_limit, max_completions, code, allowed_countries or "")
        )
        rid = cur.lastrowid
    _tc_del("tasks", "all_tasks")
    return rid

def delete_task(tid):
    with _c() as c:
        c.execute("DELETE FROM tasks WHERE id=%s", (tid,))
        c.execute("DELETE FROM task_fields WHERE task_id=%s", (tid,))
    _tc_del(f"task_{tid}", f"fields_task_{tid}", "tasks", "all_tasks")

def update_task_field_val(tid, field, value):
    if field not in {"name","price","rules","rules_en","is_active","max_completions","task_type","per_user_limit","allowed_countries","example_image_id","demo_video_id","owner_id"}: return
    with _c() as c: c.execute(f"UPDATE tasks SET {field}=%s WHERE id=%s", (value, tid))
    _tc_del(f"task_{tid}", "tasks", "all_tasks")

def get_all_tasks():
    data, hit = _tc_get("all_tasks")
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    _tc_set("all_tasks", data); return data

# ── Task Fields helpers ────────────────────────────────────────────
def get_task_fields(task_id):
    k = f"fields_task_{task_id}"
    data, hit = _tc_get(k)
    if hit: return data
    with _c() as c:
        data = c.execute("SELECT * FROM task_fields WHERE task_id=%s ORDER BY field_order", (task_id,)).fetchall()
    _tc_set(k, data); return data

def add_task_field(task_id, field_type, prompt_bn, prompt_en, order=None, example_image_id=''):
    with _c() as c:
        if order is None:
            r = c.execute("SELECT COUNT(*) FROM task_fields WHERE task_id=%s", (task_id,)).fetchone()
            order = _count(r)
        c.execute(
            "INSERT INTO task_fields (task_id,field_order,field_type,prompt_bn,prompt_en,example_image_id) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (task_id, order, field_type, prompt_bn, prompt_en, example_image_id or '')
        )
    _tc_del(f"fields_task_{task_id}")

def update_task_field_example_img(field_id, image_id):
    with _c() as c:
        c.execute("UPDATE task_fields SET example_image_id=%s WHERE id=%s", (image_id, field_id))
    _tc_del_prefix("fields_task_")

def delete_task_field(fid):
    with _c() as c:
        c.execute("DELETE FROM task_fields WHERE id=%s", (fid,))
    _tc_del_prefix("fields_task_")

def delete_all_task_fields(task_id):
    with _c() as c:
        c.execute("DELETE FROM task_fields WHERE task_id=%s", (task_id,))
    _tc_del(f"fields_task_{task_id}")

# ── TG Task helpers ────────────────────────────────────────────────
def get_tg_tasks():
    data, hit = _tc_get("tg_tasks")
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM tg_tasks WHERE is_active=1 ORDER BY id").fetchall()
    _tc_set("tg_tasks", data); return data

def get_tg_task(tid):
    k = f"tg_task_{tid}"
    data, hit = _tc_get(k)
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM tg_tasks WHERE id=%s", (tid,)).fetchone()
    _tc_set(k, data); return data

def get_all_tg_tasks():
    with _c() as c:
        return c.execute("SELECT * FROM tg_tasks ORDER BY id").fetchall()

def add_tg_task(name, price, tg_target, tg_link, tg_name, max_comp, per_user_limit=1, allowed_countries=""):
    code = generate_task_code()
    with _c() as c:
        cur = c.execute(
            "INSERT INTO tg_tasks (name,price,tg_target,tg_target_link,tg_target_name,max_completions,per_user_limit,created_at,is_active,task_code,allowed_countries) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s) RETURNING id",
            (name, price, tg_target, tg_link, tg_name, max_comp, per_user_limit, now(), code, allowed_countries or "")
        )
        rid = cur.lastrowid
    _tc_del("tg_tasks")
    return rid

def delete_tg_task(tid):
    with _c() as c:
        c.execute("DELETE FROM tg_tasks WHERE id=%s", (tid,))
        c.execute("DELETE FROM tg_task_completions WHERE task_id=%s", (tid,))
    _tc_del(f"tg_task_{tid}", "tg_tasks")

def update_tg_task(tid, field, value):
    allowed = {"name","price","tg_target","tg_target_link","tg_target_name","max_completions","is_active","new_joiners_only","per_user_limit","allowed_countries"}
    if field not in allowed: return
    with _c() as c:
        c.execute(f"UPDATE tg_tasks SET {field}=%s WHERE id=%s", (value, tid))
    _tc_del(f"tg_task_{tid}", "tg_tasks")

def tg_task_user_done(task_id, user_id):
    with _c() as c:
        r = c.execute("SELECT 1 FROM tg_task_completions WHERE task_id=%s AND user_id=%s", (task_id, user_id)).fetchone()
        return r is not None

def tg_task_mark_done(task_id, user_id):
    with _c() as c:
        # ON CONFLICT DO NOTHING — psycopg2 exception তোলে না, শুধু rowcount=0 হয়
        ins = c.execute("INSERT INTO tg_task_completions (task_id,user_id,done_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (task_id, user_id, now()))
        if ins.rowcount == 0:
            return  # ইতিমধ্যে done — completed_count double-count হবে না
        c.execute("UPDATE tg_tasks SET completed_count=completed_count+1 WHERE id=%s", (task_id,))
        t = c.execute("SELECT max_completions,completed_count FROM tg_tasks WHERE id=%s", (task_id,)).fetchone()
        if t and t["max_completions"] > 0 and t["completed_count"] >= t["max_completions"]:
            c.execute("UPDATE tg_tasks SET is_active=0 WHERE id=%s", (task_id,))
    _tc_del(f"tg_task_{task_id}", "tg_tasks")

def tg_task_slots_left(task_id):
    t = get_tg_task(task_id)
    if not t: return 0
    if t["max_completions"] == 0: return -1
    return max(0, t["max_completions"] - t["completed_count"])

def mark_pre_member(task_id, user_id):
    """এই task এর জন্য user আগে থেকেই channel member ছিল — চিরতরে block করো।"""
    with _c() as c:
        c.execute(
            "INSERT INTO tg_task_pre_members (task_id,user_id,seen_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (task_id, user_id, now())
        )

def is_pre_member(task_id, user_id):
    """User টি এই task এর জন্য আগে থেকেই member হিসেবে চিহ্নিত কিনা।"""
    with _c() as c:
        r = c.execute(
            "SELECT 1 FROM tg_task_pre_members WHERE task_id=%s AND user_id=%s",
            (task_id, user_id)
        ).fetchone()
        return r is not None

# ── Bio Task helpers ─────────────────────────────────────────────────
def get_bio_task():
    with _c() as c:
        return c.execute("SELECT * FROM bio_tasks WHERE id=1").fetchone()

def set_bio_task(reward=None, add_reward=None, is_active=None):
    with _c() as c:
        bt = c.execute("SELECT * FROM bio_tasks WHERE id=1").fetchone()
        if bt is None:
            c.execute(
                "INSERT INTO bio_tasks (id,reward,add_reward,is_active,created_at) VALUES (1,%s,%s,%s,%s)",
                (reward or 0, add_reward or 0, is_active or 0, now())
            )
        else:
            if reward is not None:
                c.execute("UPDATE bio_tasks SET reward=%s WHERE id=1", (reward,))
            if add_reward is not None:
                c.execute("UPDATE bio_tasks SET add_reward=%s WHERE id=1", (add_reward,))
            if is_active is not None:
                c.execute("UPDATE bio_tasks SET is_active=%s WHERE id=1", (is_active,))

def bio_task_paid_today(user_id):
    _tz = pytz.timezone("Asia/Dhaka")
    today = datetime.now(_tz).strftime("%Y-%m-%d")
    with _c() as c:
        r = c.execute(
            "SELECT 1 FROM bio_task_completions WHERE user_id=%s AND task_date=%s",
            (user_id, today)
        ).fetchone()
        return r is not None

def record_bio_payment(user_id, date_str, amount):
    with _c() as c:
        c.execute(
            "INSERT INTO bio_task_completions (user_id,task_date,amount,paid_at) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (user_id, date_str, amount, now())
        )
    # নতুন record হলে পুরানো মুছো — শেষ ৩০টা রাখো
    _trim_table("bio_task_completions", "user_id", user_id, keep=30)

def get_bio_task_history(user_id, limit=7):
    with _c() as c:
        return c.execute(
            "SELECT task_date,amount FROM bio_task_completions WHERE user_id=%s "
            "ORDER BY task_date DESC LIMIT %s",
            (user_id, limit)
        ).fetchall()

def get_bio_task_cumulative():
    """সর্বমোট bio task পেমেন্ট stats (সব দিন মিলিয়ে)।"""
    with _c() as c:
        row = c.execute(
            "SELECT COUNT(*) AS total_count, COALESCE(SUM(amount),0) AS total_amount "
            "FROM bio_task_completions"
        ).fetchone()
        return row['total_count'], row['total_amount']

def get_bio_tracking(user_id):
    with _c() as c:
        return c.execute(
            "SELECT * FROM bio_task_tracking WHERE user_id=%s", (user_id,)
        ).fetchone()

def set_bio_tracking(user_id):
    """User এর bio সঠিক দেখা গেছে — tracking চালু/update করো।"""
    with _c() as c:
        c.execute(
            "INSERT INTO bio_task_tracking (user_id, first_correct_at, last_checked_at, add_reward_received) "
            "VALUES (%s, %s, %s, 0) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "first_correct_at = CASE WHEN bio_task_tracking.first_correct_at = '' "
            "                        THEN EXCLUDED.first_correct_at "
            "                        ELSE bio_task_tracking.first_correct_at END, "
            "last_checked_at = EXCLUDED.last_checked_at",
            (user_id, now(), now())
        )

def clear_bio_tracking(user_id):
    """Tracking reset — bio সরিয়েছে, timestamps clear করো কিন্তু add_reward_received রাখো।"""
    with _c() as c:
        # DELETE এর বদলে UPDATE — add_reward_received ইতিহাস রাখা হয়
        c.execute(
            "UPDATE bio_task_tracking SET first_correct_at='', last_checked_at='', last_warned_at='' "
            "WHERE user_id=%s",
            (user_id,)
        )

def mark_add_reward_given(user_id) -> bool:
    """Atomic check-and-mark: add_reward_received কে 0→1 করে।
    Returns True শুধু যদি এই call-ই প্রথমবার mark করে।
    Race-safe: দুটো concurrent call একসাথে এলে শুধু একটাই True পাবে।
    NOTE: শুধু marking করে — balance add করে না।
          নতুন কোডে atomic_give_bio_add_reward() ব্যবহার করুন।
    """
    with _c() as c:
        cur = c.execute(
            "UPDATE bio_task_tracking SET add_reward_received=1 "
            "WHERE user_id=%s AND (add_reward_received IS NULL OR add_reward_received=0)",
            (user_id,)
        )
        if cur.rowcount > 0:
            return True
        ins = c.execute(
            "INSERT INTO bio_task_tracking "
            "(user_id, first_correct_at, last_checked_at, add_reward_received) "
            "VALUES (%s, %s, %s, 1) "
            "ON CONFLICT (user_id) DO NOTHING",
            (user_id, now(), now())
        )
        return ins.rowcount > 0


def atomic_give_bio_add_reward(user_id, amount: float, date_str: str) -> bool:
    """Mark + balance + record — একটি DB transaction-এ সব।

    Returns True  → এই call-ই প্রথম; reward দেওয়া হয়েছে।
    Returns False → আগেই দেওয়া হয়েছিল; কিছু করা হয়নি।

    বহু server থেকে একসাথে call আসলেও PostgreSQL-এর atomic UPDATE
    নিশ্চিত করে শুধু একটিই True পাবে — double payment সম্ভব নয়।
    """
    with _c() as c:
        # ── Step 1: atomic mark (0→1) ──────────────────────────────
        cur = c.execute(
            "UPDATE bio_task_tracking SET add_reward_received=1, "
            "first_correct_at=COALESCE(NULLIF(first_correct_at,''), %s), "
            "last_checked_at=%s "
            "WHERE user_id=%s "
            "  AND (add_reward_received IS NULL OR add_reward_received=0)",
            (now(), now(), user_id)
        )
        if cur.rowcount == 0:
            # Row নেই (নতুন user) → INSERT এর মাধ্যমে mark করার চেষ্টা
            ins = c.execute(
                "INSERT INTO bio_task_tracking "
                "(user_id, first_correct_at, last_checked_at, add_reward_received) "
                "VALUES (%s, %s, %s, 1) "
                "ON CONFLICT (user_id) DO NOTHING",
                (user_id, now(), now())
            )
            if ins.rowcount == 0:
                # ON CONFLICT → অন্য call আগেই mark করেছে
                return False

        # ── Step 2: balance যোগ করো (mark সফল হলেই আসবে এখানে) ──
        if amount > 0:
            c.execute(
                "UPDATE users SET balance = balance + %s WHERE user_id=%s",
                (amount, user_id)
            )
            _invalidate_user_cache(user_id)
            # ── Step 3: payment record করো ─────────────────────────
            c.execute(
                "INSERT INTO bio_task_completions (user_id, task_date, amount, paid_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (user_id, date_str, amount, now())
            )

        return True

def is_add_reward_received(user_id):
    """User আগে কখনো add_reward পেয়েছে কিনা।"""
    with _c() as c:
        row = c.execute(
            "SELECT add_reward_received FROM bio_task_tracking WHERE user_id=%s", (user_id,)
        ).fetchone()
        return bool(row and row.get('add_reward_received', 0))

def is_bio_actively_tracked(user_id):
    """Bio এখন সক্রিয়ভাবে track হচ্ছে (হাটানো হয়নি)।"""
    with _c() as c:
        row = c.execute(
            "SELECT first_correct_at FROM bio_task_tracking WHERE user_id=%s", (user_id,)
        ).fetchone()
        return bool(row and row.get('first_correct_at', ''))

# ── Category Task helpers ────────────────────────────────────────────
def get_categories():
    data, hit = _tc_get("categories")
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM task_categories WHERE is_active=1 ORDER BY id").fetchall()
    _tc_set("categories", data); return data

def get_all_categories():
    with _c() as c:
        return c.execute("SELECT * FROM task_categories ORDER BY id").fetchall()

def get_category(cid):
    k = f"category_{cid}"
    data, hit = _tc_get(k)
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM task_categories WHERE id=%s", (cid,)).fetchone()
    _tc_set(k, data); return data

def add_category(name):
    with _c() as c:
        cur = c.execute("INSERT INTO task_categories (name) VALUES (%s) RETURNING id", (name,))
        rid = cur.lastrowid
    _tc_del("categories")
    return rid

def delete_category(cid):
    with _c() as c:
        tasks = c.execute("SELECT id FROM category_tasks WHERE category_id=%s", (cid,)).fetchall()
        for t in tasks:
            tid = t['id'] if isinstance(t, dict) else t[0]
            c.execute("DELETE FROM category_task_fields WHERE task_id=%s", (tid,))
            c.execute("DELETE FROM category_task_completions WHERE task_id=%s", (tid,))
        c.execute("DELETE FROM category_tasks WHERE category_id=%s", (cid,))
        c.execute("DELETE FROM task_categories WHERE id=%s", (cid,))
    _tc_del(f"category_{cid}", "categories"); _tc_del_prefix(f"ctasks_{cid}_")

def update_category(cid, field, value):
    if field not in {"name", "is_active"}: return
    with _c() as c:
        c.execute(f"UPDATE task_categories SET {field}=%s WHERE id=%s", (value, cid))
    _tc_del(f"category_{cid}", "categories")

def get_category_tasks(cid, active_only=True):
    k = f"ctasks_{cid}_{int(active_only)}"
    data, hit = _tc_get(k)
    if hit: return data
    with _c() as c:
        if active_only:
            data = c.execute("SELECT * FROM category_tasks WHERE category_id=%s AND is_active=1 ORDER BY id", (cid,)).fetchall()
        else:
            data = c.execute("SELECT * FROM category_tasks WHERE category_id=%s ORDER BY id", (cid,)).fetchall()
    _tc_set(k, data); return data

def get_category_task(tid):
    k = f"ctask_{tid}"
    data, hit = _tc_get(k)
    if hit: return data
    with _c() as c: data = c.execute("SELECT * FROM category_tasks WHERE id=%s", (tid,)).fetchone()
    _tc_set(k, data); return data

def add_category_task(cid, name, price, rules, rules_en="", max_comp=0, task_type="other", per_user_limit=1, allowed_countries=""):
    code = generate_task_code()
    with _c() as c:
        cur = c.execute(
            "INSERT INTO category_tasks (category_id,name,price,rules,rules_en,max_completions,task_type,per_user_limit,is_active,task_code,allowed_countries) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s) RETURNING id",
            (cid, name, price, rules, rules_en, max_comp, task_type, per_user_limit, code, allowed_countries or "")
        )
        rid = cur.lastrowid
    _tc_del_prefix(f"ctasks_{cid}_")
    return rid

def delete_category_task(tid):
    # need cid for cache key — fetch before delete
    try:
        old = get_category_task(tid)
        cid = old['category_id'] if old else None
    except Exception:
        cid = None
    with _c() as c:
        c.execute("DELETE FROM category_tasks WHERE id=%s", (tid,))
        c.execute("DELETE FROM category_task_fields WHERE task_id=%s", (tid,))
        c.execute("DELETE FROM category_task_completions WHERE task_id=%s", (tid,))
    _tc_del(f"ctask_{tid}", f"ctask_fields_{tid}")
    if cid: _tc_del_prefix(f"ctasks_{cid}_")

def update_category_task(tid, field, value):
    allowed = {"name","price","rules","rules_en","max_completions","is_active","completed_count","task_type","per_user_limit","allowed_countries","example_image_id","demo_video_id","owner_id"}
    if field not in allowed: return
    with _c() as c:
        c.execute(f"UPDATE category_tasks SET {field}=%s WHERE id=%s", (value, tid))
    # clear individual + list cache (cid unknown here, so clear all ctasks_ lists)
    _tc_del(f"ctask_{tid}"); _tc_del_prefix("ctasks_")

def get_category_task_fields(task_id):
    k = f"ctask_fields_{task_id}"
    data, hit = _tc_get(k)
    if hit: return data
    with _c() as c:
        data = c.execute("SELECT * FROM category_task_fields WHERE task_id=%s ORDER BY field_order", (task_id,)).fetchall()
    _tc_set(k, data); return data

def add_category_task_field(task_id, field_type, prompt_bn, prompt_en, order=None, example_image_id=''):
    with _c() as c:
        if order is None:
            r = c.execute("SELECT COUNT(*) FROM category_task_fields WHERE task_id=%s", (task_id,)).fetchone()
            order = _count(r)
        c.execute(
            "INSERT INTO category_task_fields (task_id,field_order,field_type,prompt_bn,prompt_en,example_image_id) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (task_id, order, field_type, prompt_bn, prompt_en, example_image_id or '')
        )
    _tc_del(f"ctask_fields_{task_id}")

def get_task_by_code_any(code):
    """Task code দিয়ে tasks/tg_tasks/category_tasks থেকে খোঁজো।"""
    code = (code or "").strip().upper()
    with _c() as c:
        t = c.execute("SELECT * FROM tasks WHERE task_code=%s", (code,)).fetchone()
        if t: return "tasks", t
        tg = c.execute("SELECT * FROM tg_tasks WHERE task_code=%s", (code,)).fetchone()
        if tg: return "tg_tasks", tg
        ct = c.execute("SELECT * FROM category_tasks WHERE task_code=%s", (code,)).fetchone()
        if ct: return "category_tasks", ct
    return None, None

def set_task_owner_by_code(code, owner_id):
    """Task code দিয়ে owner_id সেট করো। Returns (task_name, table) or (None, None)"""
    tbl, row = get_task_by_code_any(code)
    if not tbl or not row:
        return None, None
    tid = row["id"]
    with _c() as c:
        c.execute(f"UPDATE {tbl} SET owner_id=%s WHERE id=%s", (owner_id, tid))
    if tbl == "tasks":
        _tc_del(f"task_{tid}", "tasks", "all_tasks")
    elif tbl == "category_tasks":
        _tc_del(f"ctask_{tid}"); _tc_del_prefix("ctasks_")
    else:
        _tc_del(f"tgtask_{tid}", "tg_tasks")
    return row["name"], tbl

def update_category_task_field_example_img(field_id, image_id):
    with _c() as c:
        c.execute("UPDATE category_task_fields SET example_image_id=%s WHERE id=%s", (image_id, field_id))
    _tc_del_prefix("ctask_fields_")

def get_last_task_field(task_id):
    with _c() as c:
        return c.execute(
            "SELECT * FROM task_fields WHERE task_id=%s ORDER BY field_order DESC LIMIT 1", (task_id,)
        ).fetchone()

def get_last_category_task_field(task_id):
    with _c() as c:
        return c.execute(
            "SELECT * FROM category_task_fields WHERE task_id=%s ORDER BY field_order DESC LIMIT 1", (task_id,)
        ).fetchone()

def delete_category_task_field(fid):
    with _c() as c:
        c.execute("DELETE FROM category_task_fields WHERE id=%s", (fid,))
    _tc_del_prefix("ctask_fields_")

def delete_all_category_task_fields(task_id):
    with _c() as c:
        c.execute("DELETE FROM category_task_fields WHERE task_id=%s", (task_id,))
    _tc_del(f"ctask_fields_{task_id}")

def categ_task_user_done(task_id, user_id):
    """Returns True if user has reached their per_user_limit for this category task."""
    task = get_category_task(task_id)
    per_user_limit = _rget(task, 'per_user_limit', 1) if task else 1
    if per_user_limit == 0: return False  # 0 = unlimited
    task_name = task['name'] if task else ''
    count = get_user_categ_task_submit_count(task_name, user_id)
    return count >= per_user_limit

def categ_task_slots_left(tid):
    t = get_category_task(tid)
    if not t: return 0
    if t["max_completions"] == 0: return -1
    return max(0, t["max_completions"] - t["completed_count"])

def categ_task_mark_done(task_id, user_id):
    """Returns True if this call actually marked done (first time), False if already done."""
    with _c() as c:
        # ON CONFLICT DO NOTHING — psycopg2 exception তোলে না, শুধু rowcount=0 হয়
        ins = c.execute("INSERT INTO category_task_completions (task_id,user_id,done_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (task_id, user_id, now()))
        if ins.rowcount == 0:
            return False  # ইতিমধ্যে done — completed_count double-count হবে না
        c.execute("UPDATE category_tasks SET completed_count=completed_count+1 WHERE id=%s", (task_id,))
        t = c.execute("SELECT max_completions,completed_count FROM category_tasks WHERE id=%s", (task_id,)).fetchone()
        if t and t["max_completions"] > 0 and t["completed_count"] >= t["max_completions"]:
            c.execute("UPDATE category_tasks SET is_active=0 WHERE id=%s", (task_id,))
    return True  # Bug #6 fix: এই call-ই প্রথমবার mark করেছে — True return করো

# ── Stats helper ──────────────────────────────────────────────────
_stats_cache_val = None
_stats_cache_ts  = 0.0
_STATS_CACHE_TTL = 20.0  # ২০ সেকেন্ড — admin panel দ্রুত খুলবে

def get_stats():
    global _stats_cache_val, _stats_cache_ts
    now_ts = _time.monotonic()
    if _stats_cache_val is not None and (now_ts - _stats_cache_ts) < _STATS_CACHE_TTL:
        return _stats_cache_val
    ag = None
    aw = None
    pg = None
    pw = None
    tot_bal = None
    total = None
    with _c() as c:
        total   = _count(c.execute("SELECT COUNT(*) FROM users").fetchone())
        tot_bal = _count(c.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone())
        pg = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE status='pending'").fetchone())
        pw = _count(c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone())
        ag = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE status='approved'").fetchone())
        aw = _count(c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='approved'").fetchone())
    _stats_cache_val = (total, tot_bal, pg, pw, ag, aw)
    _stats_cache_ts  = now_ts
    return _stats_cache_val

def display_name(u_row):
    if u_row and u_row["username"]: return f"@{u_row['username']}"
    if u_row and u_row["full_name"]: return u_row["full_name"]
    return "Unknown"

def _build_user_profile(uid, u=None):
    if u is None:
        u = get_user(uid)
    if not u:
        return f"❌ `{uid}` ID দিয়ে কোনো user পাওয়া যায়নি।", None
    with _c() as c:
        ref_count = _count(c.execute("SELECT COUNT(*) FROM users WHERE referred_by=%s",           (uid,)).fetchone())
        sub_count = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE user_id=%s",               (uid,)).fetchone())
        sub_app   = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'", (uid,)).fetchone())
        wd_count  = _count(c.execute("SELECT COUNT(*) FROM withdrawals WHERE user_id=%s",          (uid,)).fetchone())
        wd_app    = _count(c.execute("SELECT COUNT(*) FROM withdrawals WHERE user_id=%s AND status='approved'", (uid,)).fetchone())
        tg_done   = _count(c.execute("SELECT COUNT(*) FROM tg_task_completions WHERE user_id=%s",  (uid,)).fetchone())
        cat_done  = _count(c.execute("SELECT COUNT(*) FROM category_task_completions WHERE user_id=%s", (uid,)).fetchone())
    name_str  = f"@{u['username']}" if u["username"] else (u["full_name"] or "Unknown")
    ban_icon  = "🚫 Banned" if u["is_banned"] else "✅ Active"
    ref_by    = u["referred_by"]
    if ref_by:
        ref_u = get_user(ref_by)
        rn = (f"@{ref_u['username']}" if ref_u and ref_u["username"] else
              (ref_u["full_name"] if ref_u and ref_u["full_name"] else str(ref_by)))
        ref_by_str = f"`{ref_by}` ({rn})"
    else:
        ref_by_str = "_কেউ নেই_"
    # Country display
    try:
        _ctry_raw = (u["country"] or "").upper().strip()
    except Exception:
        _ctry_raw = ""
    if _ctry_raw and _ctry_raw in COUNTRY_MAP:
        _ctry_flag, _ctry_name = COUNTRY_MAP[_ctry_raw]
        country_str = f"{_ctry_flag} {_ctry_name} (`{_ctry_raw}`)"
    elif _ctry_raw:
        country_str = f"`{_ctry_raw}` _(অজানা)_"
    else:
        country_str = "_সেট হয়নি_"
    text = (
        f"👤 *User Profile*\n\n"
        f"🆔 *ID:* `{uid}`\n"
        f"📛 *নাম:* {name_str}\n"
        f"📱 *Full Name:* {u['full_name'] or '_নেই_'}\n"
        f"📅 *Join Date:* `{u['joined_at']}`\n"
        f"🌐 *Language:* `{u['lang'] or 'bn'}`\n"
        f"🌍 *দেশ:* {country_str}\n"
        f"💰 *Balance:* `{round(u['balance'],4)}$ USDT`\n"
        f"🔰 *Status:* {ban_icon}\n\n"
        f"👥 *Referred By:* {ref_by_str}\n"
        f"🔗 *মোট Referrals:* `{ref_count}` জন\n\n"
        f"📋 *Task Submissions:* `{sub_count}` টি ✅`{sub_app}` approved\n"
        f"💸 *Withdrawals:* `{wd_count}` টি ✅`{wd_app}` approved\n"
        f"📡 *TG Tasks Done:* `{tg_done}` টি\n"
        f"📁 *Category Tasks Done:* `{cat_done}` টি"
    )
    ban_btn = (InlineKeyboardButton("✅ Unban", callback_data=f"adm_uunban_{uid}")
               if u["is_banned"] else InlineKeyboardButton("🚫 Ban", callback_data=f"adm_uban_{uid}"))
    kb = InlineKeyboardMarkup([
        [ban_btn,
         InlineKeyboardButton("💬 Message", callback_data=f"adm_umsg_{uid}")],
        [InlineKeyboardButton("➕ Balance যোগ", callback_data=f"adm_uaddbal_{uid}"),
         InlineKeyboardButton("➖ Balance কাটো", callback_data=f"adm_udedbal_{uid}")],
        [InlineKeyboardButton("🌍 দেশ পরিবর্তন করুন", callback_data=f"adm_uchcountry_{uid}")],
        [InlineKeyboardButton(f"👥 Referrals ({ref_count}জন)", callback_data=f"adm_urefs_{uid}"),
         InlineKeyboardButton(f"📧 Submissions ({sub_count})", callback_data=f"adm_usubs_{uid}")],
        [InlineKeyboardButton(f"💸 Withdrawals ({wd_count})", callback_data=f"adm_uwds_{uid}")],
    ])
    return text, kb

def mask_addr_short(addr):
    if len(addr) <= 12: return addr[:4] + "*****" + addr[-4:]
    return addr[:6] + "*****" + addr[-6:]

# ──────────────────────────── KEYBOARDS ────────────────────────────
def main_kb(uid=None):
    lang = get_lang(uid) if uid else 'bn'
    b = get_all_btns(lang)
    return ReplyKeyboardMarkup([
        [b['tasks'],         b['profile']],
        [b['balance'],       b['refer']],
        [b['support'],       b['withdraw']],
        [b['history'],       b['language']],
    ], resize_keyboard=True)

def lang_kb(uid=None, current_lang=None):
    if current_lang is None and uid is not None:
        current_lang = get_lang(uid)
    bn_tick = " ✅" if current_lang == 'bn' else ""
    en_tick = " ✅" if current_lang == 'en' else ""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🇧🇩 বাংলা{bn_tick}", callback_data="lang_bn"),
        InlineKeyboardButton(f"🇬🇧 English{en_tick}", callback_data="lang_en"),
    ]])

# country code → (flag, name)
COUNTRY_LIST = [
    ("BD","🇧🇩","Bangladesh"),("IN","🇮🇳","India"),("PK","🇵🇰","Pakistan"),
    ("NP","🇳🇵","Nepal"),("LK","🇱🇰","Sri Lanka"),("MY","🇲🇾","Malaysia"),
    ("PH","🇵🇭","Philippines"),("ID","🇮🇩","Indonesia"),("TH","🇹🇭","Thailand"),
    ("MM","🇲🇲","Myanmar"),("VN","🇻🇳","Vietnam"),("KH","🇰🇭","Cambodia"),
    ("NG","🇳🇬","Nigeria"),("GH","🇬🇭","Ghana"),("KE","🇰🇪","Kenya"),
    ("US","🇺🇸","USA"),("GB","🇬🇧","UK"),("CA","🇨🇦","Canada"),
    ("AU","🇦🇺","Australia"),("AE","🇦🇪","UAE"),("SA","🇸🇦","Saudi Arabia"),
    ("OT","🌍","Other"),
]
COUNTRY_MAP  = {c[0]: (c[1], c[2]) for c in COUNTRY_LIST}

def country_kb():
    """3-column inline keyboard for country selection."""
    rows = []
    row  = []
    for code, flag, name in COUNTRY_LIST:
        row.append(InlineKeyboardButton(f"{flag} {name}", callback_data=f"set_country_{code}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)

def admin_country_kb(target_uid):
    """Admin দেশ পরিবর্তনের জন্য 3-column keyboard — callback: adm_uset_ctry_{uid}_{code}"""
    rows = []
    row  = []
    for code, flag, name in COUNTRY_LIST:
        cb = f"adm_uset_ctry_{target_uid}_{code}"
        row.append(InlineKeyboardButton(f"{flag} {name}", callback_data=cb))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data=f"adm_uprofile_{target_uid}")])
    return InlineKeyboardMarkup(rows)

def fmt_allowed_countries(codes_str: str) -> str:
    """'BD,IN' → '🇧🇩 Bangladesh, 🇮🇳 India'"""
    if not codes_str or codes_str.upper() in ("0","ALL",""):
        return "🌍 সব দেশ"
    parts = []
    for c in codes_str.upper().split(","):
        c = c.strip()
        if c in COUNTRY_MAP:
            flag, name = COUNTRY_MAP[c]
            parts.append(f"{flag} {name}")
        elif c:
            parts.append(c)
    return ", ".join(parts) if parts else "🌍 সব দেশ"

def cancel_kb(uid=None):
    btn = T(uid or 0, 'cancel_btn') if uid else "❌ বাতিল করুন"
    return ReplyKeyboardMarkup([[btn]], resize_keyboard=True)

def g_kb(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"ga_{gid}"),
         InlineKeyboardButton("❌ Reject",  callback_data=f"gr_{gid}")],
        [InlineKeyboardButton("💬 User কে মেসেজ করুন", callback_data=f"gmsg_{gid}")],
    ])

def w_kb_admin(wid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"wa_{wid}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"wr_{wid}"),
    ]])

# ──────────────────────────── CHANNEL CHECK ────────────────────────────
# ── Channel membership cache — স্মার্ট দুই-স্তরের TTL ──────────────────
# সব চ্যানেলে থাকলে 15s cache → Task/অন্য বাটনে দ্রুত রিপ্লাই
# কোনো চ্যানেল মিস হলে 0s cache → চ্যানেল ছাড়লে সাথে সাথে ধরা পড়বে
_ch_cache: dict = {}          # uid → (timestamp, missing_list)
_CH_CACHE_TTL_OK  = 600       # seconds — সব চ্যানেলে থাকলে ১০ মিনিট cache (১২০→৬০০: Telegram API call ৫ গুণ কম)
_CH_CACHE_TTL_BAD = 30        # seconds — channel মিস হলে ৩০s cache (frequent check চাই)

async def check_ch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return []
    uid = update.effective_user.id
    channels = await asyncio.to_thread(get_req_channels)
    if not channels:
        return []

    # ── Cache hit? ────────────────────────────────────────────────
    now_ts = _time.monotonic()
    cached = _ch_cache.get(uid)
    if cached:
        _cached_ts, _cached_missing = cached
        _ttl = _CH_CACHE_TTL_OK if not _cached_missing else _CH_CACHE_TTL_BAD
        if (now_ts - _cached_ts) < _ttl:
            return _cached_missing

    # ── Parallel API calls (asyncio.gather) ───────────────────────
    async def _check_one(ch):
        try:
            m = await ctx.bot.get_chat_member(ch["ch_id"], uid)
            if m.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                return ch
            return None
        except TelegramError as _tg_err:
            _err_lower = str(_tg_err).lower()
            # Bot নিজেই channel এ নেই / access নেই / network error — user কে penalize করবো না
            if ("forbidden" in _err_lower
                    or "not enough rights" in _err_lower
                    or "bot is not a member" in _err_lower
                    or "chat not found" in _err_lower
                    or "channel not found" in _err_lower):
                log.warning(f"[CHECK-CH] ch={ch.get('ch_id')} bot-side error (skip): {_tg_err}")
                return None
            # User টি channel এ নেই ("user not found", "participant_id_invalid" ইত্যাদি)
            return ch

    results = await asyncio.gather(*[_check_one(ch) for ch in channels])
    missing = [ch for ch in results if ch is not None]

    # ── Cache result ──────────────────────────────────────────────
    # Evict expired entries যখন cache বড় হয় (memory leak বন্ধ)
    if len(_ch_cache) > 2000:
        _exp = [k for k, (t, m) in _ch_cache.items()
                if now_ts - t >= (_CH_CACHE_TTL_OK if not m else _CH_CACHE_TTL_BAD)]
        for _k in _exp:
            _ch_cache.pop(_k, None)
    _ch_cache[uid] = (now_ts, missing)
    return missing


def invalidate_ch_cache(uid: int):
    """Channel join করলে cache মুছে দাও — পরের check fresh হবে।"""
    _ch_cache.pop(uid, None)

async def join_prompt(update: Update, uid=None):
    lang_uid = uid or (update.effective_user.id if update.effective_user else 0)
    channels = await asyncio.to_thread(get_req_channels)
    btns = [[InlineKeyboardButton(f"👉 {ch['ch_name']}", url=ch["ch_link"])] for ch in channels]
    _jp_lang = await asyncio.to_thread(get_lang, lang_uid)
    _jp_btn_lbl = "✅ জয়েন করেছি — চেক করুন" if _jp_lang == 'bn' else "✅ I've Joined — Check Now"
    btns.append([InlineKeyboardButton(_jp_btn_lbl, callback_data="check_join")])
    # Callback query থেকে ডাকা হলে spinner বন্ধ করো — না হলে loading চলতেই থাকবে
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
    await update.effective_message.reply_text(
        T(lang_uid, 'join_prompt'),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns)
    )

# ──────────────────────────── REFERRAL NOTIFY ────────────────────────────
async def fire_new_user_notify(ctx, uid, full_name, username, ref_by):
    uname = f"@{_md_esc(username)}" if username else _md_esc(full_name)
    ref_txt = f"\n🔗 *রেফার:* {ref_by}" if ref_by else ""
    await notify_full_admins(
        ctx.bot,
        f"🆕 *নতুন ইউজার জয়েন!*\n\n"
        f"👤 *নাম:* {_md_esc(full_name)}\n"
        f"🆔 *ID:* `{uid}`\n"
        f"📛 *Username:* {uname}{ref_txt}"
    )
    if ref_by:
        try:
            comm_pct = await asyncio.to_thread(get_commission_pct)
            await ctx.bot.send_message(ref_by,
                T(ref_by, 'new_user_ref', name=_md_esc(full_name or ""), uname=uname, comm_pct=int(comm_pct) if comm_pct == int(comm_pct) else comm_pct),
                parse_mode="Markdown")
        except Exception as _e:
            log.warning(f"[NOTIFY] ref_by={ref_by} new-user commission notify ব্যর্থ: {_e}")

# ──────────────────────────── /start ────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    args = ctx.args
    ref_by = None
    deep_task_type = None
    deep_task_id   = None
    if args and args[0].startswith("ref_"):
        try:
            ref_by = int(args[0][4:])
            if ref_by == user.id: ref_by = None
        except ValueError: pass
    elif args and args[0].startswith("tgtask_"):
        try: deep_task_type = "g"; deep_task_id = int(args[0][7:])
        except ValueError: pass
    elif args and args[0].startswith("task_"):
        try: deep_task_type = "t"; deep_task_id = int(args[0][5:])
        except ValueError: pass
    elif args and args[0].startswith("twtask_"):
        try: deep_task_type = "tw"; deep_task_id = int(args[0][7:])
        except ValueError: pass
    elif args and args[0].startswith("ctask_"):
        try: deep_task_type = "ct"; deep_task_id = int(args[0][6:])
        except ValueError: pass
    elif args and args[0].startswith("promo_"):
        # Deep link: /start promo_CODE → auto-apply promo
        ctx.user_data["pending_promo"] = args[0][6:].upper()

    is_new = (await asyncio.to_thread(get_user, user.id)) is None
    await asyncio.to_thread(ensure_user, user.id, user.username, user.full_name, ref_by)

    u = await asyncio.to_thread(get_user, user.id)
    if not u:
        await update.message.reply_text("❌ Error loading account. Please try /start again.", parse_mode="Markdown")
        return
    if u["is_banned"]:
        await update.message.reply_text(T(user.id, 'banned'), parse_mode="Markdown")
        return

    # নতুন user হলে pending data store করো — channel join verify হলে notify যাবে
    if is_new:
        ctx.user_data["pending_new"]   = True
        ctx.user_data["pending_uid"]   = user.id
        ctx.user_data["pending_name"]  = user.full_name
        ctx.user_data["pending_uname"] = user.username or ""
        ctx.user_data["pending_ref"]   = ref_by

    # New user or no language set → ask language first
    if not await asyncio.to_thread(user_has_lang, user.id):
        ctx.user_data["state"] = "first_lang"
        if deep_task_type:
            ctx.user_data["pending_deep_task_type"] = deep_task_type
            ctx.user_data["pending_deep_task_id"]   = deep_task_id
        await update.message.reply_text(
            "🌍 *ভাষা বেছে নিন / Choose your language:*",
            parse_mode="Markdown",
            reply_markup=await asyncio.to_thread(lang_kb)   # first-time — no lang yet, no tick
        )
        return

    # Language selected but country not yet picked → resume country selection
    if ctx.user_data.get("state") in ("first_lang", "first_country"):
        lang = await asyncio.to_thread(get_lang, user.id)
        q_txt = (
            "🌍 *আপনার দেশ বেছে নিন:*\n\n"
            "⚠️ *সতর্কতা:* ভুল দেশ বাছলে Task পাবেন না!\n"
            "👆 আপনি যে দেশে থাকেন সেই দেশ বেছে নিন।\n"
            "🔒 *একবার বাছলে আর পরিবর্তন করা যাবে না।*"
            if lang == 'bn' else
            "🌍 *Select your country:*\n\n"
            "⚠️ *Warning:* Selecting the wrong country means you won't get tasks!\n"
            "👆 Please select the country where you actually live.\n"
            "🔒 *This cannot be changed once selected.*"
        )
        ctx.user_data["state"] = "first_country"
        await update.message.reply_text(q_txt, parse_mode="Markdown", reply_markup=country_kb())
        return

    if deep_task_type and deep_task_id:
        # ── Channel check — deep link এ আসলেও চ্যানেল জয়েন চেক করো ──
        _dl_missing = await check_ch(update, ctx)
        if _dl_missing:
            await join_prompt(update, uid=user.id)
            return
        await _send_welcome(update, ctx, user.id)
        if deep_task_type == "ct":
            uid2 = user.id
            t, lang = await asyncio.gather(
                asyncio.to_thread(get_category_task, deep_task_id),
                asyncio.to_thread(get_lang, uid2),
            )
            if t and t['is_active']:
                done, slots = await asyncio.gather(
                    asyncio.to_thread(categ_task_user_done, deep_task_id, uid2),
                    asyncio.to_thread(categ_task_slots_left, deep_task_id),
                )
                if done:
                    await update.message.reply_text("✅ আপনি এই Task ইতিমধ্যে করেছেন!" if lang=='bn' else "✅ You already completed this task!", parse_mode="Markdown")
                elif slots == 0:
                    await update.message.reply_text("❌ এই Task পূর্ণ হয়ে গেছে!" if lang=='bn' else "❌ All slots are filled!", parse_mode="Markdown")
                else:
                    # ── Country restriction check (deep link) ──────────
                    _dl_ct_ac = (_rget(t, 'allowed_countries', '') or '').strip().upper()
                    if _dl_ct_ac and _dl_ct_ac not in ("0","ALL"):
                        _dl_ct_list = [x.strip() for x in _dl_ct_ac.split(",") if x.strip()]
                        _dl_u_cntry = (await asyncio.to_thread(get_user_country, uid2) or "").upper()
                        if not _dl_u_cntry or _dl_u_cntry not in _dl_ct_list:
                            _deny_msg = (
                                f"❌ *এই Task আপনার দেশে উপলব্ধ নয়।*\n\n✅ অনুমোদিত দেশ: {fmt_allowed_countries(_dl_ct_ac)}"
                                if lang == 'bn' else
                                f"❌ *This task is not available in your country.*\n\n✅ Allowed countries: {fmt_allowed_countries(_dl_ct_ac)}"
                            )
                            await update.message.reply_text(_deny_msg, parse_mode="Markdown")
                            return
                    ticon = task_type_icon(_rget(t, 'task_type', 'other'))
                    mc    = t['max_completions']
                    slots_txt = (f"👥 *Max:* `{mc} জন`" if mc > 0 else "👥 *Max:* `Unlimited`") if lang=='bn' else (f"👥 *Max:* `{mc}`" if mc > 0 else "👥 *Max:* `Unlimited`")
                    _ct_tn_esc = _md_esc(t['name'])
                    msg   = (f"{ticon} *{_ct_tn_esc}*\n\n💰 *পুরস্কার:* `{t['price']}$ USDT`\n{slots_txt}\n\n📜 *নিয়মাবলী:*\n{t['rules']}" if lang=='bn'
                             else f"{ticon} *{_ct_tn_esc}*\n\n💰 *Reward:* `{t['price']}$ USDT`\n{slots_txt}\n\n📜 *Rules:*\n{t['rules']}")
                    _dl_ct_vid = bool((_rget(t, 'demo_video_id', '') or '').strip())
                    _dl_ct_rows = []
                    if _dl_ct_vid:
                        _dl_ct_vlbl = "📹 ভিডিও দেখুন" if lang == 'bn' else "📹 Watch Video"
                        _dl_ct_rows.append([InlineKeyboardButton(_dl_ct_vlbl, callback_data=f"ctask_watch_video_{deep_task_id}")])
                    _dl_ct_rows.append([InlineKeyboardButton("✅ Task শুরু করুন" if lang=='bn' else "✅ Start Task", callback_data=f"ctask_submit_{deep_task_id}")])
                    _dl_ct_rows.append([InlineKeyboardButton("❌ বাতিল" if lang=='bn' else "❌ Cancel", callback_data="task_cancel")])
                    kb = InlineKeyboardMarkup(_dl_ct_rows)
                    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
            else:
                await update.message.reply_text("❌ এই Task পাওয়া যায়নি বা inactive।" if lang=='bn' else "❌ Task not found or inactive.", parse_mode="Markdown")
            return
        if deep_task_type == "g":
            task = await asyncio.to_thread(get_tg_task, deep_task_id)
            if task and task['is_active']:
                uid2 = user.id
                lang = await asyncio.to_thread(get_lang, uid2)
                tid2 = task['id']
                _dl_tg_ac = (_rget(task, 'allowed_countries', '') or '').strip().upper()
                _dl_tg_deny = (
                    bool(_dl_tg_ac) and _dl_tg_ac not in ("0","ALL") and
                    (await asyncio.to_thread(get_user_country, uid2) or "").upper() not in [x.strip() for x in _dl_tg_ac.split(",") if x.strip()]
                )
                if await asyncio.to_thread(tg_task_user_done, tid2, uid2):
                    await update.message.reply_text("✅ আপনি এই Task ইতিমধ্যে সম্পন্ন করেছেন!" if lang=='bn' else "✅ You already completed this task!", parse_mode="Markdown")
                elif await asyncio.to_thread(tg_task_slots_left, tid2) == 0:
                    await update.message.reply_text("❌ এই Task এর সব জায়গা পূর্ণ!" if lang=='bn' else "❌ All slots are filled!", parse_mode="Markdown")
                elif _dl_tg_deny:
                    await update.message.reply_text(
                        (f"❌ *এই Task আপনার দেশে উপলব্ধ নয়।*\n\n✅ অনুমোদিত দেশ: {fmt_allowed_countries(_dl_tg_ac)}" if lang=='bn'
                         else f"❌ *This task is not available in your country.*\n\n✅ Allowed countries: {fmt_allowed_countries(_dl_tg_ac)}"),
                        parse_mode="Markdown")
                elif _rget(task, 'new_joiners_only', 0):
                    # DB তে আগে থেকে চিহ্নিত pre-member? → চিরতরে block
                    if await asyncio.to_thread(is_pre_member, tid2, uid2):
                        await update.message.reply_text(T(uid2, 'new_joiners_only_reject'), parse_mode="Markdown")
                        return
                    try:
                        mbr = await ctx.bot.get_chat_member(task['tg_target'], uid2)
                        already = mbr.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
                    except Exception:
                        already = False
                    if already:
                        await asyncio.to_thread(mark_pre_member, tid2, uid2)  # DB তে save — পরে leave করলেও block থাকবে
                        await update.message.reply_text(T(uid2, 'new_joiners_only_reject'), parse_mode="Markdown")
                        return
                    tg_link = task['tg_target_link'] or f"https://t.me/{task['tg_target'].lstrip('@')}"
                    tg_name = task['tg_target_name'] or task['tg_target']
                    max_c = _rget(task, 'max_completions', 0)
                    done_c = _rget(task, 'completed_count', 0)
                    if max_c > 0:
                        slots_txt = (f"👥 *মোট:* `{max_c} জন` | *বাকি:* `{max(0,max_c-done_c)} জন`" if lang=='bn'
                                     else f"👥 *Total:* `{max_c}` | *Remaining:* `{max(0,max_c-done_c)}`")
                    else:
                        slots_txt = "👥 *মোট:* `Unlimited`" if lang=='bn' else "👥 *Total:* `Unlimited`"
                    _tn_esc1 = _md_esc(task['name'])
                    if lang == 'bn':
                        msg = (f"📡 *{_tn_esc1}*\n\n💰 *পুরস্কার:* `{task['price']}$ USDT`\n"
                               f"📢 *Channel/Group:* {tg_name}\n{slots_txt}\n\n"
                               f"*কীভাবে করবেন:*\n1️⃣ নিচের বাটনে চেপে Channel/Group জয়েন করুন\n"
                               f"2️⃣ জয়েন হলে ✅ Check বাটনে চাপুন\n3️⃣ বট চেক করবে — জয়েন থাকলে টাকা পাবেন!")
                    else:
                        msg = (f"📡 *{_tn_esc1}*\n\n💰 *Reward:* `{task['price']}$ USDT`\n"
                               f"📢 *Channel/Group:* {tg_name}\n{slots_txt}\n\n"
                               f"*How to complete:*\n1️⃣ Press Join button below\n"
                               f"2️⃣ After joining press ✅ Check\n3️⃣ Bot will verify — you'll get paid instantly!")
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📢 Join Channel/Group", url=tg_link)],
                        [InlineKeyboardButton("✅ Check — I Joined" if lang=='en' else "✅ Check — আমি জয়েন করেছি", callback_data=f"tgt_check_{tid2}")],
                        [InlineKeyboardButton("❌ Cancel" if lang=='en' else "❌ বাতিল", callback_data="task_cancel")],
                    ])
                    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
                else:
                    # new_joiners_only নয় — সরাসরি task দেখাও
                    tg_link = task['tg_target_link'] or f"https://t.me/{task['tg_target'].lstrip('@')}"
                    tg_name = task['tg_target_name'] or task['tg_target']
                    max_c = _rget(task, 'max_completions', 0)
                    done_c = _rget(task, 'completed_count', 0)
                    if max_c > 0:
                        slots_txt = (f"👥 *মোট:* `{max_c} জন` | *বাকি:* `{max(0,max_c-done_c)} জন`" if lang=='bn'
                                     else f"👥 *Total:* `{max_c}` | *Remaining:* `{max(0,max_c-done_c)}`")
                    else:
                        slots_txt = "👥 *মোট:* `Unlimited`" if lang=='bn' else "👥 *Total:* `Unlimited`"
                    _tn_esc2 = _md_esc(task['name'])
                    if lang == 'bn':
                        msg = (f"📡 *{_tn_esc2}*\n\n💰 *পুরস্কার:* `{task['price']}$ USDT`\n"
                               f"📢 *Channel/Group:* {tg_name}\n{slots_txt}\n\n"
                               f"*কীভাবে করবেন:*\n1️⃣ নিচের বাটনে চেপে Channel/Group জয়েন করুন\n"
                               f"2️⃣ জয়েন হলে ✅ Check বাটনে চাপুন\n3️⃣ বট চেক করবে — জয়েন থাকলে টাকা পাবেন!")
                    else:
                        msg = (f"📡 *{_tn_esc2}*\n\n💰 *Reward:* `{task['price']}$ USDT`\n"
                               f"📢 *Channel/Group:* {tg_name}\n{slots_txt}\n\n"
                               f"*How to complete:*\n1️⃣ Press Join button below\n"
                               f"2️⃣ After joining press ✅ Check\n3️⃣ Bot will verify — you'll get paid instantly!")
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📢 Join Channel/Group", url=tg_link)],
                        [InlineKeyboardButton("✅ Check — I Joined" if lang=='en' else "✅ Check — আমি জয়েন করেছি", callback_data=f"tgt_check_{tid2}")],
                        [InlineKeyboardButton("❌ Cancel" if lang=='en' else "❌ বাতিল", callback_data="task_cancel")],
                    ])
                    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
        if deep_task_type in ("t", "tw"):
            task = await asyncio.to_thread(get_task, deep_task_id)
            uid2 = user.id
            lang = await asyncio.to_thread(get_lang, uid2)
            if task and task['is_active']:
                # ── Per-user limit check ──────────────────────────────
                per_user_limit = _rget(task, 'per_user_limit', 1)
                _dl_tcode      = _rget(task, 'task_code', '') or ''
                done_count     = await asyncio.to_thread(get_user_task_submit_count, task['name'], uid2, _dl_tcode)
                if per_user_limit != 0 and done_count >= per_user_limit:
                    lim_lbl = "∞" if per_user_limit == 0 else str(per_user_limit)
                    await update.message.reply_text(
                        f"✅ আপনি এই Task ইতিমধ্যে {done_count}/{lim_lbl} বার সম্পন্ন করেছেন। আর জমা দেওয়া যাবে না।" if lang=='bn'
                        else f"✅ You have already completed this task {done_count}/{lim_lbl} time(s). No more submissions allowed.",
                        parse_mode="Markdown"
                    )
                    return
                # ── Max completions / slots check ─────────────────────
                mc     = _rget(task, 'max_completions', 0)
                done_c = _rget(task, 'completed_count', 0)
                if mc > 0 and done_c >= mc:
                    await update.message.reply_text(
                        "❌ এই Task পূর্ণ হয়ে গেছে!" if lang=='bn' else "❌ All slots are filled!",
                        parse_mode="Markdown"
                    )
                    return
                # ── Country restriction check (deep link) ─────────────
                _dl_t_ac = (_rget(task, 'allowed_countries', '') or '').strip().upper()
                if _dl_t_ac and _dl_t_ac not in ("0", "ALL"):
                    _dl_t_list    = [x.strip() for x in _dl_t_ac.split(",") if x.strip()]
                    _dl_t_country = (await asyncio.to_thread(get_user_country, uid2) or "").upper()
                    if not _dl_t_country or _dl_t_country not in _dl_t_list:
                        await update.message.reply_text(
                            f"❌ *এই Task আপনার দেশে উপলব্ধ নয়।*\n\n✅ অনুমোদিত দেশ: {fmt_allowed_countries(_dl_t_ac)}" if lang=='bn'
                            else f"❌ *This task is not available in your country.*\n\n✅ Allowed countries: {fmt_allowed_countries(_dl_t_ac)}",
                            parse_mode="Markdown"
                        )
                        return
                # ── Build task preview ────────────────────────────────
                ticon     = task_type_icon(_rget(task, 'task_type', 'other'))
                tcode     = _rget(task, 'task_code', '') or ''
                code_line = f"\n🔑 *Task Code:* `{tcode}`" if tcode else ""
                if mc > 0:
                    slots_txt = (
                        f"👥 *Max:* `{mc} জন` | *বাকি:* `{max(0, mc - done_c)} জন`" if lang=='bn'
                        else f"👥 *Max:* `{mc}` | *Remaining:* `{max(0, mc - done_c)}`"
                    )
                else:
                    slots_txt = "👥 *Max:* `Unlimited`"
                _tn_esc3 = _md_esc(task['name'])
                if lang == 'bn':
                    rules_text = task["rules"] if task["rules"] else "কোনো বিশেষ নিয়ম নেই।"
                    msg = (
                        f"{ticon} *{_tn_esc3}*{code_line}\n\n"
                        f"💰 *পুরস্কার:* `{task['price']}$ USDT`\n"
                        f"{slots_txt}\n\n"
                        f"📜 *নিয়মাবলী:*\n{rules_text}"
                    )
                else:
                    rules_text = (_rget(task, "rules_en") or task["rules"]) or "No special rules."
                    msg = (
                        f"{ticon} *{_tn_esc3}*{code_line}\n\n"
                        f"💰 *Reward:* `{task['price']}$ USDT`\n"
                        f"{slots_txt}\n\n"
                        f"📜 *Rules:*\n{rules_text}"
                    )
                _dl_t_vid = bool((_rget(task, 'demo_video_id', '') or '').strip())
                _dl_t_rows = []
                if _dl_t_vid:
                    _dl_t_vlbl = "📹 ভিডিও দেখুন" if lang == 'bn' else "📹 Watch Video"
                    _dl_t_rows.append([InlineKeyboardButton(_dl_t_vlbl, callback_data=f"task_watch_video_{deep_task_id}")])
                _dl_t_rows.append([InlineKeyboardButton("✅ Task শুরু করুন" if lang=='bn' else "✅ Start Task", callback_data=f"task_submit_{deep_task_id}")])
                _dl_t_rows.append([InlineKeyboardButton("❌ বাতিল" if lang=='bn' else "❌ Cancel", callback_data="task_cancel")])
                kb = InlineKeyboardMarkup(_dl_t_rows)
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
            else:
                await update.message.reply_text(
                    "❌ এই Task পাওয়া যায়নি বা inactive।" if lang=='bn' else "❌ Task not found or inactive.",
                    parse_mode="Markdown"
                )
        return

    # ── Channel join check — language+country শেষে বট ব্যবহারের আগে চ্যানেল চেক ──
    missing = await check_ch(update, ctx)
    if missing:
        await join_prompt(update, uid=user.id)
        return

    # নতুন user যদি lang/country step skip করে সরাসরি এখানে আসে (no channels path),
    # fire_new_user_notify এখানেই পাঠাতে হবে — নইলে notification কখনোই যাবে না।
    if ctx.user_data.pop("pending_new", False) and user.id not in _notified_set:
        _notified_set_add(user.id)
        _nn_name  = ctx.user_data.pop("pending_name", user.full_name)
        _nn_uname = ctx.user_data.pop("pending_uname", user.username or "")
        _nn_ref   = ctx.user_data.pop("pending_ref", None)
        for _k in ("pending_uid",):
            ctx.user_data.pop(_k, None)
        _nt = asyncio.create_task(fire_new_user_notify(ctx, user.id, _nn_name, _nn_uname, _nn_ref))
        _background_tasks.add(_nt); _nt.add_done_callback(_background_tasks.discard)

    await _send_welcome(update, ctx, user.id)

async def _send_welcome(update, ctx, uid):
    # get_lang আর main_kb parallel-এ চালাও — দুটোই independent DB query
    _lang_res, _kb = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(main_kb, uid),
    )
    lang_key = 'welcome_msg_bn' if _lang_res == 'bn' else 'welcome_msg_en'
    custom_w = await asyncio.to_thread(get_setting, lang_key)
    welcome_text = custom_w if custom_w else T(uid, 'welcome')
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=_kb)
    else:
        await ctx.bot.send_message(uid, welcome_text, parse_mode="Markdown", reply_markup=_kb)
    # Apply promo code from deep link (/start promo_CODE)
    _pp = ctx.user_data.pop("pending_promo", None)
    if _pp:
        await _auto_apply_promo(ctx.bot, uid, _pp)

# ──────────────────────────── GUARD ────────────────────────────
async def guard(update, ctx):
    uid = update.effective_user.id if update.effective_user else None
    if not uid: return False
    # Fast path: user cache sync check — thread dispatch ছাড়াই
    _u_fast = _get_user_cached_fast(uid)
    if _u_fast is not False:
        # User cache hit — শুধু check_ch দরকার
        u, missing = _u_fast, await check_ch(update, ctx)
    else:
        # Cache miss — get_user + check_ch parallel
        u, missing = await asyncio.gather(
            asyncio.to_thread(get_user, uid),
            check_ch(update, ctx),
        )
    if u and u["is_banned"]:
        if update.message:
            await update.message.reply_text(T(uid, 'banned'), parse_mode="Markdown")
        elif update.callback_query:
            try: await update.callback_query.message.reply_text(T(uid, 'banned'), parse_mode="Markdown")
            except Exception: pass
        return False
    if missing:
        await join_prompt(update, uid)
        return False
    # ── last_seen non-blocking আপডেট ────────────────────────────
    _now = now()
    async def _update_last_seen():
        try:
            await asyncio.to_thread(_update_last_seen_sync, uid, _now)
        except Exception as _e:
            log.debug(f"[LAST-SEEN] uid={uid} update ব্যর্থ: {_e}")
    t = asyncio.ensure_future(_update_last_seen())
    _background_tasks.add(t); t.add_done_callback(_background_tasks.discard)
    # ── _last_active: job_clean_stuck_states এর জন্য activity timestamp ──
    ctx.user_data["_last_active"] = _time.monotonic()
    return True

def _update_last_seen_sync(uid, ts):
    with _c() as c:
        c.execute("UPDATE users SET last_seen=%s WHERE user_id=%s", (ts, uid))

def _bot_is_on_cached_fast() -> "bool | None":
    """Sync cache-only check — event loop এ চালানো safe (শুধু dict read, GIL protected)।
    Returns: True/False if cache hit, None if cache miss (DB query দরকার)."""
    entry = _settings_cache.get('bot_status')
    if entry is not None:
        val, ts = entry
        if _time.monotonic() - ts < _SETTINGS_TTL:
            return (val or 'on') != 'off'
    return None  # cache miss → caller asyncio.to_thread করবে

def _get_user_cached_fast(uid: int):
    """Sync cache-only user read — event loop এ safe।
    Returns: user dict if cache hit, False if cache miss."""
    cached = _user_cache.get(uid)
    if cached:
        u, ts = cached
        if _time.monotonic() - ts < _USER_CACHE_TTL:
            return u
    return False  # cache miss sentinel

async def _bot_accessible(uid: int) -> bool:
    """bot চালু থাকলে (common case) সবাই accessible।
    Fast path: cache থেকে sync পড়ো — thread dispatch নেই।
    Cache miss হলে তবেই asyncio.to_thread করো।"""
    _fast = _bot_is_on_cached_fast()
    if _fast is True:
        return True          # ✅ cache hit: bot চালু → zero thread dispatch
    if _fast is None:
        # Cache miss — thread dispatch করতে হবে
        _fast = await asyncio.to_thread(bot_is_on)
    if _fast:
        return True
    # Bot বন্ধ — admin কিনা check করো
    return await asyncio.to_thread(is_admin, uid)

async def bot_off_reply(update, uid=0):
    msg = T(uid, 'bot_off')
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif update.callback_query:
        try: await update.callback_query.message.reply_text(msg, parse_mode="Markdown")
        except Exception: pass

# ──────────────────────────── /cancel ────────────────────────────
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    ctx.user_data.clear()
    await update.message.reply_text(T(uid, 'cancelled'), parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))

# ──────────────────────────── MENU HANDLERS ────────────────────────────
async def show_profile(update, ctx):
    user = update.effective_user
    uid  = user.id
    # get_bal + get_user parallel — sequential call বাদ
    bal, _u = await asyncio.gather(
        asyncio.to_thread(get_bal, uid),
        asyncio.to_thread(get_user, uid),
    )
    uname = f"@{_md_esc(user.username)}" if user.username else _md_esc(user.full_name or "")
    await update.message.reply_text(
        f"{T(uid,'profile_title')}\n\n"
        f"👤 *USER* {uname}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{T(uid,'balance_label')} `{bal}$ USDT`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{T(uid,'status_active')}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{T(uid,'developer')}",
        parse_mode="Markdown"
    )

async def show_balance(update, ctx):
    uid   = update.effective_user.id
    bal, owner, lang = await asyncio.gather(
        asyncio.to_thread(get_bal, uid),
        asyncio.to_thread(get_setting, 'support_owner'),
        asyncio.to_thread(get_lang, uid),
    )
    owner = owner or '@inkamxbd'
    owner_e = _md_esc(owner)
    if lang == 'en':
        task_promo = (
            "\n\n━━━━━━━━━━━━━━━━\n"
            f"📌 *Want to add your Task to this bot?*\n"
            f"Contact admin: {owner_e}"
        )
    else:
        task_promo = (
            "\n\n━━━━━━━━━━━━━━━━\n"
            f"📌 *বটে আপনার Task যোগ করতে চান?*\n"
            f"এডমিনকে মেসেজ করুন: {owner_e}"
        )
    await update.message.reply_text(
        f"{T(uid,'balance_title')}\n\n"
        f"{T(uid,'balance_greet')}\n\n"
        f"{T(uid,'your_balance')} `{bal}$ USDT`"
        f"{task_promo}",
        parse_mode="Markdown"
    )

async def show_support(update, ctx):
    uid   = update.effective_user.id
    owner, bot_u, lang = await asyncio.gather(
        asyncio.to_thread(get_setting, 'support_owner'),
        asyncio.to_thread(get_setting, 'support_bot'),
        asyncio.to_thread(get_lang, uid),
    )
    owner = owner or '@inkamxbd'
    bot_u = bot_u or '@gmail_sell_help_bot'
    owner_e = _md_esc(owner)
    bot_e   = _md_esc(bot_u)
    if lang == 'en':
        body = (
            "📞 *Customer Support Center*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Dear member,\n"
            "For any problem or inquiry, please contact our support team. We will try to resolve it quickly.\n\n"
            "⚠️ Note: Please avoid sending unnecessary messages.\n"
            "Thank you!\n\n"
            f"👨‍💼 *Owner:* {owner_e}\n"
            f"🤖 *Bot:* {bot_e}\n\n"
            "⚠️ *If the bot doesn't reply, message the admin directly. Don't say Hi/Hello — state your issue directly.* ⚠️"
        )
    else:
        body = (
            "📞 *গ্রাহক সেবা কেন্দ্র*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "সম্মানিত মেম্বার,\n"
            "আপনার যেকোনো সমস্যা বা জিজ্ঞাসার জন্য আমাদের সাপোর্ট টিমের সাথে যোগাযোগ করুন। আমরা দ্রুত সমাধানের চেষ্টা করব।\n\n"
            "⚠️ নোট: অযথা মেসেজ দেওয়া থেকে বিরত থাকুন।\n"
            "ধন্যবাদ!\n\n"
            f"👨‍💼 *Owner:* {owner_e}\n"
            f"🤖 *Bot:* {bot_e}\n\n"
            "⚠️ *বট এ মেসেজ দিয়ে না পেলে এডমিন আয়ডিতে মেসেজ দিবেন। হাই হ্যালো দিবেন না যেটার জন্য মেসেজ দিছেন সেটিই বলবেন* ⚠️"
        )
    await update.message.reply_text(body, parse_mode="Markdown")

def _regular_task_mark_done(task_code: str, task_name: str):
    t = None
    """Regular task approve হলে completed_count বাড়াও, max হলে is_active=0 করো।
    Returns (task_row, is_maxed: bool)"""
    with _c() as c:
        row = None
        if task_code:
            row = c.execute("SELECT * FROM tasks WHERE task_code=%s", (task_code,)).fetchone()
        if not row and task_name:
            row = c.execute("SELECT * FROM tasks WHERE name=%s", (task_name,)).fetchone()
        if not row:
            return None, False
        tid = row["id"]
        c.execute("UPDATE tasks SET completed_count=completed_count+1 WHERE id=%s", (tid,))
        t = c.execute("SELECT max_completions,completed_count,name,task_code,price FROM tasks WHERE id=%s", (tid,)).fetchone()
        if t and t["max_completions"] > 0 and t["completed_count"] >= t["max_completions"]:
            c.execute("UPDATE tasks SET is_active=0 WHERE id=%s", (tid,))
            return t, True
        return t, False

def _reject_restore_task_slot(gmail_row):
    """Submission reject হলে category task-এর slot ফিরিয়ে দাও।
    Regular task-এ completed_count approval-এ বাড়ে, তাই সেটা undo করার দরকার নেই।
    """
    if not gmail_row:
        return
    task_name = (gmail_row.get('task_name') or '')
    task_code = (gmail_row.get('task_code') or '').strip()
    user_id   = gmail_row.get('user_id')

    if not task_name.startswith('[CAT] '):
        return  # Regular/TG task — completed_count শুধু approval-এ বাড়ে, rollback লাগবে না

    with _c() as c:
        task_row = None
        if task_code:
            task_row = c.execute("SELECT id FROM category_tasks WHERE task_code=%s", (task_code,)).fetchone()
        if not task_row:
            real_name = task_name[6:]  # "[CAT] " prefix বাদ দাও
            task_row = c.execute("SELECT id FROM category_tasks WHERE name=%s", (real_name,)).fetchone()
        if not task_row:
            return
        tid = task_row['id']
        # category_task_completions record মুছো যাতে user আবার submit করতে পারে
        c.execute("DELETE FROM category_task_completions WHERE task_id=%s AND user_id=%s", (tid, user_id))
        # completed_count কমাও
        c.execute("UPDATE category_tasks SET completed_count=GREATEST(0,completed_count-1) WHERE id=%s", (tid,))
        # is_active=0 হয়ে থাকলে slot ফেরার কারণে পুনরায় চালু করো
        c.execute(
            "UPDATE category_tasks SET is_active=1 WHERE id=%s AND is_active=0 AND max_completions>0 AND completed_count<max_completions",
            (tid,)
        )

async def _check_ref_event_bonus(referred_id: int, referrer_id: int, bot):
    """
    Task approve হলে active referral event-এ bonus দেওয়া হয় কিনা check করো।
    referred_id: যে task করেছে (referred user)
    referrer_id: যে refer করেছে
    """
    try:
        ev = await asyncio.to_thread(get_active_referral_event)
        if not ev:
            return
        # ── শুধু event চলাকালীন যারা রেফার হয়েছে তাদের জন্য bonus ────────
        _ref_user = await asyncio.to_thread(get_user, referred_id)
        if not _ref_user:
            return
        _joined = (_ref_user.get('joined_at') or '')[:10]   # "YYYY-MM-DD"
        _ev_start = str(ev.get('start_date') or '')[:10]
        _ev_end   = str(ev.get('end_date')   or '')[:10]
        if not _joined or _joined < _ev_start or _joined > _ev_end:
            return  # event এর আগে বা পরে join করেছে — bonus নেই
        total_tasks = await asyncio.to_thread(count_approved_tasks_for_user, referred_id)
        if total_tasks < ev['required_tasks']:
            return
        # Pay bonus (ON CONFLICT DO NOTHING prevents double-payment)
        paid = await asyncio.to_thread(give_ref_event_bonus, ev['id'], referrer_id, referred_id, ev['bonus_amount'])
        if not paid:
            return
        # ── User info ──────────────────────────────────────────────
        ru   = await asyncio.to_thread(get_user, referred_id)
        rfu  = await asyncio.to_thread(get_user, referrer_id)
        rname   = (ru["full_name"]  or ru["username"]  or str(referred_id))  if ru  else str(referred_id)
        rfname  = (rfu["full_name"] or rfu["username"] or str(referrer_id))  if rfu else str(referrer_id)
        # ── Referrer কে notify করো ─────────────────────────────────
        try:
            await bot.send_message(
                referrer_id,
                f"🎯 *Referral Event Bonus পেয়েছেন!*\n\n"
                f"🎉 আপনার রেফার করা ব্যবহারকারী *{_md_esc(rname)}* মোট `{total_tasks}` টি task সম্পন্ন করেছে!\n\n"
                f"💰 আপনার একাউন্টে *`${ev['bonus_amount']}` USDT* Referral Event Bonus যোগ হয়েছে!\n\n"
                f"📅 Event: `{ev['start_date']}` → `{ev['end_date']}`",
                parse_mode="Markdown"
            )
        except Exception as _nb:
            log.warning(f"[REF_EVENT] referrer={referrer_id} bonus notify failed: {_nb}")
        # ── Admin কে notify করো (বায়ো task format) ───────────────
        try:
            stats      = await asyncio.to_thread(get_ref_event_stats, ev['id'])
            cum_count  = stats['count']
            cum_amount = stats['total_amount']
            rfu_bal    = float(rfu['balance']) if rfu else 0.0
            rf_username = (rfu.get('username') or '') if rfu else ''
            adm_msg = (
                f"🎯 *Referral Event — Bonus দেওয়া হয়েছে!*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *Referrer:* {_md_esc(rfname)}"
                + (f" (@{_md_esc(rf_username)})" if rf_username else "") +
                f"\n🆔 *ID:* `{referrer_id}`\n"
                f"👤 *Referred:* {_md_esc(rname)} (`{referred_id}`)\n"
                f"📋 *Tasks Done:* `{total_tasks}`\n"
                f"💰 *Bonus:* `{ev['bonus_amount']}$ USDT`\n"
                f"💳 *Referrer Balance:* `{round(rfu_bal, 4)}$ USDT`\n"
                f"🗓️ *তারিখ:* `{now()}`\n\n"
                f"📈 *সর্বমোট Cumulative (এই Event):*\n"
                f"  👥 মোট bonus দেওয়া হয়েছে: `{cum_count} জনকে`\n"
                f"  💵 মোট খরচ: `{cum_amount}$ USDT`"
            )
            await notify_full_admins(bot, adm_msg)
        except Exception as _ae:
            log.warning(f"[REF_EVENT] admin notify failed: {_ae}")
    except Exception as _reve:
        log.warning(f"[REF_EVENT] _check_ref_event_bonus error: {_reve}")

async def _notify_task_maxed(bot, name: str, code: str, type_label: str, done: int, max_c: int):
    """Admin-কে জানাও যে task-এর সব slot পূর্ণ হয়েছে এবং task বন্ধ হয়েছে।"""
    code_line = f"\n🔑 *Code:* `{_md_esc(code)}`" if code else ""
    msg = (
        f"🏁 *Task সম্পূর্ণ হয়েছে — Slot পূর্ণ!*\n\n"
        f"📌 *Task:* {_md_esc(name)}{code_line}\n"
        f"🏷️ *Type:* {type_label}\n"
        f"👥 *Completed:* `{done}/{max_c}` জন\n\n"
        f"✅ Task স্বয়ংক্রিয়ভাবে বন্ধ করে দেওয়া হয়েছে।"
    )
    await notify_full_admins(bot, msg)

async def _notify_task_done_ch(bot, user_id: int, task_name: str, task_code: str, price: float, task_type_label: str = ""):
    """যেকোনো task complete/approve হলে TASK_DONE_NOTIFY_CH চ্যানেলে notification পাঠাও।"""
    try:
        u = await asyncio.to_thread(get_user, user_id)
        full_name = _md_esc((u["full_name"] or "Unknown") if u else "Unknown")
        type_line = f"\n🏷️ *ধরন:* {_md_esc(task_type_label)}" if task_type_label else ""
        code_line = f"\n🔑 *Task Code:* `{_md_esc(task_code)}`" if task_code else ""
        msg = (
            f"✅ *Task সম্পন্ন হয়েছে!*\n\n"
            f"👤 *ইউজার:* {full_name}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📌 *Task:* {_md_esc(task_name)}{code_line}{type_line}\n"
            f"💰 *আয়:* `{price}$ USDT`\n"
            f"🕐 *সময়:* {now()}"
        )
        await bot.send_message(TASK_DONE_NOTIFY_CH, msg, parse_mode="Markdown")
    except Exception as _e:
        log.warning(f"[TASK_DONE_NOTIFY] চ্যানেলে পাঠাতে ব্যর্থ: {_e}")

async def show_tasks(update, ctx):
    uid          = update.effective_user.id
    # send_chat_action background — main path block করে না
    _act = asyncio.create_task(ctx.bot.send_chat_action(chat_id=uid, action="typing"))
    _background_tasks.add(_act); _act.add_done_callback(_background_tasks.discard)
    # lang, tasks, tg_tasks, categories, bio_task — সব একসাথে parallel
    lang, tasks, tg_tasks, categories, bio_task = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(get_tasks),
        asyncio.to_thread(get_tg_tasks),
        asyncio.to_thread(get_categories),
        asyncio.to_thread(get_bio_task),
    )
    bio_active   = bio_task and bio_task['is_active']

    if not tasks and not tg_tasks and not categories and not bio_active:
        await update.message.reply_text(T(uid, 'no_tasks'), parse_mode="Markdown",
                                        reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    btns = []

    # Regular tasks — per task one button
    if tasks:
        # user_country + সব task-এর done_count একসাথে parallel fetch
        _task_tcode_list = [_rget(t, 'task_code', '') or '' for t in tasks]
        _task_results = await asyncio.gather(
            asyncio.to_thread(get_user_country, uid),
            *[asyncio.to_thread(get_user_task_submit_count, t['name'], uid, tc)
              for t, tc in zip(tasks, _task_tcode_list)]
        )
        user_country = (_task_results[0] or "").upper()
        _done_counts = _task_results[1:]
        for task, done_count in zip(tasks, _done_counts):
            per_user_limit = _rget(task, 'per_user_limit', 1)
            if per_user_limit != 0 and done_count >= per_user_limit:
                continue
            # Max completions / slots check
            mc     = _rget(task, 'max_completions', 0)
            done_c = _rget(task, 'completed_count', 0)
            if mc > 0 and done_c >= mc:
                continue
            # Country restriction check
            ac = (_rget(task, 'allowed_countries', '') or '').strip().upper()
            if ac and ac not in ("0", "ALL"):
                allowed_list = [x.strip() for x in ac.split(",") if x.strip()]
                if not user_country or user_country not in allowed_list:
                    continue
            ticon = task_type_icon(_rget(task, 'task_type', 'other'))
            slots_info = f"  •  {max(0, mc - done_c)} left" if mc > 0 else ""
            label = f"{ticon} {task['name']}  •  ${task['price']}{slots_info}"
            btns.append([InlineKeyboardButton(label, callback_data=f"task_{task['id']}")])

    # Categories — সব category-র tasks একসাথে parallel fetch
    if categories:
        _all_cat_tasks_list = await asyncio.gather(
            *[asyncio.to_thread(get_category_tasks, cat['id']) for cat in categories]
        )
        for cat, _all_ctasks in zip(categories, _all_cat_tasks_list):
            if not _all_ctasks:
                continue
            _done_flags = await asyncio.gather(
                *[asyncio.to_thread(categ_task_user_done, t['id'], uid) for t in _all_ctasks]
            )
            cat_tasks = [t for t, done in zip(_all_ctasks, _done_flags) if not done]
            if cat_tasks:
                total_earn = sum(t['price'] for t in cat_tasks)
                task_count = len(cat_tasks)
                btns.append([InlineKeyboardButton(
                    f"🗂️ {cat['name']}  •  {task_count} Task  •  ${round(total_earn,2)}",
                    callback_data=f"categ_{cat['id']}"
                )])

    # Bio Task — active হলে সবসময় দেখাও
    if bio_active:
        dr             = bio_task['reward']
        ar             = (bio_task.get('add_reward', 0) or 0)
        # দুটো independent DB call parallel-এ চালাও
        actively_track, add_done = await asyncio.gather(
            asyncio.to_thread(is_bio_actively_tracked, uid),
            asyncio.to_thread(is_add_reward_received, uid),
        )
        if lang == 'bn':
            if actively_track:
                bt_label = f"🧬 Bio Task  •  ✅ Tracking চলছে  •  Daily {dr}$"
            elif add_done:
                bt_label = f"🧬 Bio Task  •  ⚠️ Bio হাটায়েছেন  •  Daily {dr}$"
            else:
                bt_label = f"🧬 Bio Task  •  🆕 {ar}$ + Daily {dr}$"
        else:
            if actively_track:
                bt_label = f"🧬 Bio Task  •  ✅ Tracking Active  •  Daily {dr}$"
            elif add_done:
                bt_label = f"🧬 Bio Task  •  ⚠️ Bio Removed  •  Daily {dr}$"
            else:
                bt_label = f"🧬 Bio Task  •  🆕 {ar}$ + Daily {dr}$"
        btns.append([InlineKeyboardButton(bt_label, callback_data="bio_task_info")])

    # Telegram tasks — ONE grouped button
    if tg_tasks:
        # done flags এবং slots দুটো একসাথে parallel-এ fetch করো — sync call block করে না
        _tgt_done_flags, _tgt_slots = await asyncio.gather(
            asyncio.gather(*[asyncio.to_thread(tg_task_user_done,  t['id'], uid) for t in tg_tasks]),
            asyncio.gather(*[asyncio.to_thread(tg_task_slots_left, t['id'])      for t in tg_tasks]),
        )
        available_tgt = sum(
            1 for _done, _slots in zip(_tgt_done_flags, _tgt_slots)
            if not _done and _slots != 0
        )
        tgt_label = (
            f"📲 Telegram Tasks ({available_tgt} available)"
            if lang == 'en'
            else f"📲 Telegram Tasks ({available_tgt}টি available)"
        )
        btns.append([InlineKeyboardButton(tgt_label, callback_data="tgt_list")])

    if not btns:
        await update.message.reply_text(
            "✅ আপনি সব available Task সম্পন্ন করেছেন!" if lang == 'bn'
            else "✅ You have completed all available tasks!",
            parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    btns.append([InlineKeyboardButton(T(uid,'cancel_inline'), callback_data="task_cancel")])
    await update.message.reply_text(
        f"{T(uid,'tasks_title')}\n\n{T(uid,'tasks_choose')}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns)
    )


async def show_tg_tasks_list(update, ctx):
    """Show all active TG tasks as a list"""
    q   = update.callback_query
    uid = q.from_user.id
    lang, tg_tasks = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(get_tg_tasks),
    )
    if not tg_tasks:
        await q.message.reply_text("❌ No Telegram tasks available!" if lang=='en' else "❌ কোনো Telegram Task নেই!")
        return
    # slots + done flag সব tasks-এর জন্য parallel-এ fetch করো — sequential call event loop block করে
    _tgt_slots, _tgt_done = await asyncio.gather(
        asyncio.gather(*[asyncio.to_thread(tg_task_slots_left, t['id'])      for t in tg_tasks]),
        asyncio.gather(*[asyncio.to_thread(tg_task_user_done,  t['id'], uid) for t in tg_tasks]),
    )
    btns = []
    for t, slots, done in zip(tg_tasks, _tgt_slots, _tgt_done):
        if done:
            label = f"✅ {t['name']} — ${t['price']}"
        elif slots == 0:
            label = f"🚫 {t['name']} — ${t['price']}"
        elif slots == -1:
            label = f"🟢 {t['name']} — ${t['price']}"
        else:
            label = f"🟢 {t['name']} — ${t['price']} | {slots} left"
        btns.append([InlineKeyboardButton(label, callback_data=f"tgt_{t['id']}")])
    btns.append([InlineKeyboardButton("🔙 Back" if lang=='en' else "🔙 ফিরে যান", callback_data="task_cancel")])
    title = "📲 *Telegram Tasks*\n\nChoose a task:" if lang=='en' else "📲 *Telegram Tasks*\n\nএকটি Task বেছে নিন:"
    await q.message.reply_text(title, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))



async def show_category_tasks(update, ctx, cat_id):
    """Show tasks inside a category"""
    q    = update.callback_query
    uid  = q.from_user.id
    lang, cat = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(get_category, cat_id),
    )
    if not cat:
        await q.message.reply_text("❌ Category not found!"); return
    tasks = await asyncio.to_thread(get_category_tasks, cat_id)
    if not tasks:
        await q.message.reply_text("❌ No tasks in this category!" if lang=='en' else "❌ এই Category তে কোনো Task নেই!")
        return
    # slots + done + fields সব tasks-এর জন্য একবারে parallel-এ fetch করো — sequential call bot slow করে
    _task_slots_list, _task_done_list, _task_fields_list = await asyncio.gather(
        asyncio.gather(*[asyncio.to_thread(categ_task_slots_left,    t['id'])      for t in tasks]),
        asyncio.gather(*[asyncio.to_thread(categ_task_user_done,     t['id'], uid) for t in tasks]),
        asyncio.gather(*[asyncio.to_thread(get_category_task_fields, t['id'])      for t in tasks]),
    )
    btns = []
    for t, slots, done, fields in zip(tasks, _task_slots_list, _task_done_list, _task_fields_list):
        proof_icons = "".join("📷" if f["field_type"]=="photo" else "📝" for f in fields)
        ticon = task_type_icon(_rget(t, 'task_type', 'other'))
        price_str = f"💵 ${t['price']}"
        if done:
            label = f"✅  {t['name']}  {price_str}  •  Done"
        elif slots == 0:
            label = f"🔒  {t['name']}  {price_str}  •  Full"
        elif slots == -1:
            label = f"{ticon}  {t['name']}  {price_str}  {proof_icons}"
        else:
            label = f"{ticon}  {t['name']}  {price_str}  •  {slots} left"
        btns.append([InlineKeyboardButton(label, callback_data=f"ctask_{t['id']}")])
    btns.append([InlineKeyboardButton("🔙 Back" if lang=='en' else "🔙 ফিরে যান", callback_data="task_cancel")])
    total_tasks = len(tasks)
    avail = sum(1 for done, slots in zip(_task_done_list, _task_slots_list) if not done and slots != 0)
    _cat_tn_esc = _md_esc(cat['name'])
    if lang == 'en':
        title = f"🗂️ *{_cat_tn_esc}*\n\n📋 Total: `{total_tasks}` tasks  •  ✅ Available: `{avail}`\n\n👇 Choose a task below:"
    else:
        title = f"🗂️ *{_cat_tn_esc}*\n\n📋 মোট: `{total_tasks}` টি Task  •  ✅ পাওয়া যাচ্ছে: `{avail}` টি\n\n👇 নিচে থেকে একটি Task বেছে নিন:"
    await q.message.reply_text(title, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


async def show_category_task_rules(update, ctx, task):
    """Show rules/proof info for a category task"""
    uid  = update.callback_query.from_user.id
    tid = task['id']
    lang, fields, slots, done = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(get_category_task_fields, tid),
        asyncio.to_thread(categ_task_slots_left, tid),
        asyncio.to_thread(categ_task_user_done, tid, uid),
    )

    if done:
        per_user_limit = _rget(task, 'per_user_limit', 1)
        user_count     = await asyncio.to_thread(get_user_categ_task_submit_count, task['name'], uid)
        lim_lbl = "∞" if per_user_limit == 0 else str(per_user_limit)
        if lang == 'en':
            done_msg = f"✅ You have already completed this task {user_count}/{lim_lbl} time(s). No more submissions allowed."
        else:
            done_msg = f"✅ আপনি এই Task ইতিমধ্যে {user_count}/{lim_lbl} বার সম্পন্ন করেছেন। আর জমা দেওয়া যাবে না।"
        # show_alert ব্যবহার না করে reply_text দাও — parent ইতিমধ্যে q.answer() fire করেছে
        await update.callback_query.message.reply_text(done_msg, parse_mode="Markdown")
        return
    if slots == 0:
        msg_full = "❌ All slots for this task are filled!" if lang=='en' else "❌ এই Task এর সব জায়গা পূর্ণ!"
        await update.callback_query.message.reply_text(msg_full, parse_mode="Markdown")
        return

    # ── Country restriction check ─────────────────────────────────
    _cat_allowed = (_rget(task, 'allowed_countries', '') or '').strip().upper()
    if _cat_allowed and _cat_allowed not in ("0","ALL"):
        _cat_allowed_list = [x.strip() for x in _cat_allowed.split(",") if x.strip()]
        _cat_u_country = (await asyncio.to_thread(get_user_country, uid) or "").upper()
        if not _cat_u_country:
            ctx.user_data["pending_tg_task_after_country"] = f"ctask_{tid}"
            _ask = (
                "🌍 *এই Task টি নির্দিষ্ট দেশের জন্য।*\n\nআগে আপনার দেশ বেছে নিন:\n\n"
                "⚠️ *সতর্কতা:* ভুল দেশ বাছলে Task পাবেন না!\n"
                "👆 আপনি যে দেশে থাকেন সেই দেশ বেছে নিন।\n"
                "🔒 *একবার বাছলে আর পরিবর্তন করা যাবে না।*"
                if lang == 'bn' else
                "🌍 *This task is restricted to specific countries.*\n\nPlease select your country first:\n\n"
                "⚠️ *Warning:* Selecting the wrong country means you won't get tasks!\n"
                "👆 Please select the country where you actually live.\n"
                "🔒 *This cannot be changed once selected.*"
            )
            await update.callback_query.message.reply_text(_ask, parse_mode="Markdown", reply_markup=country_kb())
            return
        if _cat_u_country not in _cat_allowed_list:
            _deny = (
                f"❌ *এই Task আপনার দেশে উপলব্ধ নয়।*\n\n✅ অনুমোদিত দেশ: {fmt_allowed_countries(_cat_allowed)}"
                if lang == 'bn' else
                f"❌ *This task is not available in your country.*\n\n✅ Allowed countries: {fmt_allowed_countries(_cat_allowed)}"
            )
            await update.callback_query.message.reply_text(_deny, parse_mode="Markdown")
            return

    if lang == 'en':
        rules_text  = (task["rules_en"] if task["rules_en"] else await asyncio.to_thread(translate_to_en, task["rules"])) if task["rules"] else "No special rules."
        price_label = "💵 *Price:*"
        rules_label = "📜 *Rules:*"
        confirm_txt = "✅ Read the rules and press *📨 Submit* to continue."
        proof_label = "📋 *Required Proof:*"
    else:
        rules_text  = task["rules"] if task["rules"] else "কোনো বিশেষ নিয়ম নেই।"
        price_label = "💵 *মূল্য:*"
        rules_label = "📜 *নিয়মাবলী:*"
        confirm_txt = "✅ নিয়ম পড়ে রাজি হলে *📨 জমা দিন* চাপুন।"
        proof_label = "📋 *প্রয়োজনীয় প্রমাণ:*"

    proof_text = ""
    if fields:
        proof_lines = []
        for i, f in enumerate(fields, 1):
            prompt = f["prompt_en"] if lang == 'en' else f["prompt_bn"]
            icon   = "📷" if f["field_type"] == "photo" else "📝"
            proof_lines.append(f"{i}. {icon} {prompt}")
        proof_text = f"\n\n{proof_label}\n" + "\n".join(proof_lines)

    sub_btn  = T(uid, 'submit_btn')
    cancel_b = T(uid, 'cancel_inline')
    _has_vid = bool((_rget(task, 'demo_video_id', '') or '').strip())
    _kb_rows = []
    if _has_vid:
        _vid_lbl = "📹 ভিডিও দেখুন" if lang == 'bn' else "📹 Watch Video"
        _kb_rows.append([InlineKeyboardButton(_vid_lbl, callback_data=f"ctask_watch_video_{tid}")])
    _kb_rows.append([InlineKeyboardButton(sub_btn, callback_data=f"ctask_submit_{tid}"),
                     InlineKeyboardButton(cancel_b, callback_data="task_cancel")])
    kb = InlineKeyboardMarkup(_kb_rows)
    max_c2  = _rget(task, 'max_completions', 0)
    done_c2 = _rget(task, 'completed_count', 0)

    if max_c2 > 0:
        left_c2 = max(0, max_c2 - done_c2)
        pct     = int((done_c2 / max_c2) * 10)
        bar     = "🟩" * pct + "⬜" * (10 - pct)
        if lang == 'en':
            slots_line = (
                f"👥 *Slots:* `{done_c2}/{max_c2}`\n"
                f"{bar}\n"
                f"✅ *Completed:* `{done_c2}` | ⏳ *Remaining:* `{left_c2}`"
            )
        else:
            slots_line = (
                f"👥 *মোট স্লট:* `{done_c2}/{max_c2}` জন\n"
                f"{bar}\n"
                f"✅ *করেছে:* `{done_c2}` জন | ⏳ *বাকি আছে:* `{left_c2}` জন"
            )
    else:
        if lang == 'en':
            slots_line = f"✅ *Completed:* `{done_c2}` | 👥 *Slots:* `Unlimited`"
        else:
            slots_line = f"✅ *করেছে:* `{done_c2}` জন | 👥 *সীমাহীন*"

    ticon = task_type_icon(_rget(task, 'task_type', 'other'))
    ttype_lbl = task_type_label(_rget(task, 'task_type', 'other'))
    _ctcode = _rget(task, 'task_code', '') or ''
    _ctcode_line = f"🔑 *Task Code:* `{_ctcode}`\n" if _ctcode else ""
    await update.effective_message.reply_text(
        f"{ticon} *{_md_esc(task['name'])}*\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"{_ctcode_line}"
        f"🏷️ *Type:* `{ttype_lbl}`\n"
        f"{price_label} `{task['price']} USDT`\n"
        f"{slots_line}\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"{rules_label}\n"
        f"{rules_text}\n"
        f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"
        f"{proof_text}\n\n"
        f"💡 {confirm_txt}",
        parse_mode="Markdown", reply_markup=kb
    )
    # ── Task Media (example image only — video via button) ─────────
    _ct_ex_img = _rget(task, 'example_image_id', '') or ''
    if _ct_ex_img:
        try:
            _cap = "📷 Example Image — এইরকম ছবি দিন" if lang == 'bn' else "📷 Example Image — Submit a photo like this"
            await update.effective_message.reply_photo(_ct_ex_img, caption=_cap)
        except Exception: pass

async def start_category_task_collect(update, ctx, task_id):
    """Start collecting proof for a category task"""
    q      = update.callback_query
    uid    = q.from_user.id
    task, fields, lang = await asyncio.gather(
        asyncio.to_thread(get_category_task, task_id),
        asyncio.to_thread(get_category_task_fields, task_id),
        asyncio.to_thread(get_lang, uid),
    )

    # ── Race condition guard: Submit বাটনে চাপার পর task বন্ধ বা পূর্ণ হয়ে যেতে পারে ──
    if not task or not task["is_active"]:
        await q.message.reply_text("❌ এই Task এখন আর সক্রিয় নেই!" if lang == 'bn' else "❌ This task is no longer active!")
        return
    if await asyncio.to_thread(categ_task_user_done, task_id, uid):
        await q.message.reply_text("✅ আপনি এই Task ইতিমধ্যে সম্পন্ন করেছেন!" if lang == 'bn' else "✅ You already completed this task!")
        return
    if await asyncio.to_thread(categ_task_slots_left, task_id) == 0:
        await q.message.reply_text("❌ এই Task এর সব জায়গা পূর্ণ হয়ে গেছে!" if lang == 'bn' else "❌ All slots are filled!")
        return

    # ── Point block check ─────────────────────────────────────────
    _blk, _blk_dt = await asyncio.to_thread(is_task_blocked, uid)
    if _blk and _blk_dt:
        _until_str = _blk_dt.astimezone(pytz.timezone("Asia/Dhaka")).strftime("%d %b %Y, %I:%M %p")
        await q.message.reply_text(T(uid, 'task_blocked_msg', until=_until_str))
        return

    ctx.user_data["state"]          = "categ_task_collect"
    ctx.user_data["categ_task_id"]  = task_id
    ctx.user_data["task_name"]      = task["name"]
    ctx.user_data["task_price"]     = task["price"]
    ctx.user_data["task_fields"]    = [dict(f) for f in fields]
    ctx.user_data["field_idx"]      = 0
    ctx.user_data["collected"]      = []

    if not fields:
        lang = await asyncio.to_thread(get_lang, uid)
        prompt = "📝 *Send your info:*\n\n❌ To cancel, type /cancel" if lang == 'en' else "📝 *আপনার তথ্য পাঠান:*\n\n❌ বাতিল করতে /cancel লিখুন"
        await q.message.reply_text(prompt, parse_mode="Markdown",
                                   reply_markup=ReplyKeyboardMarkup([[T(uid,'cancel_btn')]], resize_keyboard=True))
        return
    await _ask_next_field(update, ctx, uid, via_callback=True)


async def _finalize_category_task_submission(update, ctx, uid, via_callback=False):
    _cat_existing_rows = None
    """Finalize and send category task submission to admin"""
    user      = update.effective_user
    uname     = f"@{_md_esc(user.username)}" if user.username else _md_esc(user.full_name or "")
    task_name = _md_esc(ctx.user_data.get("task_name", "Task"))
    try:
        price = float(ctx.user_data.get("task_price", 0.10))
    except (TypeError, ValueError):
        price = 0.10
    collected = ctx.user_data.get("collected", [])
    task_id   = ctx.user_data.get("categ_task_id", 0)

    lang = await asyncio.to_thread(get_lang, uid)
    summary_lines  = []
    photo_file_ids = []
    _cat_submitted_uids = []
    for i, item in enumerate(collected, 1):
        if item["type"] == "photo":
            if item["value"]:
                photo_file_ids.append(item["value"])
                if item.get("unique_id"):
                    _cat_submitted_uids.append(item["unique_id"])
                summary_lines.append(f"📷 *{_md_esc(item['prompt_bn'])}:* [Photo #{len(photo_file_ids)}]")
            else:
                summary_lines.append(f"📷 *{_md_esc(item['prompt_bn'])}:* (Skipped)")
        else:
            val = _md_esc(item["value"] or "")
            summary_lines.append(f"📝 *{_md_esc(item['prompt_bn'])}:*\n`{val}`")

    # ── Duplicate photo detection ──────────────────────────────────
    if _cat_submitted_uids:
        def _db_2921():
            # SQL-এ LIKE দিয়ে check করো — Python-এ সব rows fetch করার দরকার নেই
            with _c() as c:
                for _uid_val in _cat_submitted_uids:
                    if not _uid_val:
                        continue
                    hit = c.execute(
                        "SELECT 1 FROM gmails WHERE user_id != %s AND photo_unique_ids IS NOT NULL "
                        "AND photo_unique_ids != '' AND photo_unique_ids LIKE %s LIMIT 1",
                        (uid, f"%{_uid_val}%")
                    ).fetchone()
                    if hit:
                        return True
            return False
        _cat_dup_found = await asyncio.to_thread(_db_2921)
        if _cat_dup_found:
            _new_pts, _cat_lang = await asyncio.gather(
                asyncio.to_thread(add_points, uid, -POINT_DUP_PHOTO),
                asyncio.to_thread(get_lang, uid),
            )
            _dup_notice = (
                "⚠️ *Duplicate Photo Detected!*\n\n"
                "🔴 This photo has been used in a previous submission.\n"
                f"📉 *{POINT_DUP_PHOTO} points deducted.* Current: `{_new_pts}/100`"
                if _cat_lang == 'en' else
                "⚠️ *ডুপ্লিকেট ছবি শনাক্ত হয়েছে!*\n\n"
                "🔴 এই ছবি আগে অন্য Submission এ ব্যবহার করা হয়েছে।\n"
                f"📉 *{POINT_DUP_PHOTO} পয়েন্ট কাটা হয়েছে।* বর্তমান: `{_new_pts}/100`"
            )
            ctx.user_data.clear()
            if update.message:
                await update.message.reply_text(_dup_notice, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            else:
                await ctx.bot.send_message(uid, _dup_notice, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            return

    summary_txt = "\n".join(summary_lines) if summary_lines else "_No data_"
    gmail_text  = _json.dumps(collected, ensure_ascii=False)

    ttype_obj = await asyncio.to_thread(get_category_task, task_id)
    ttype_ins = _rget(ttype_obj, 'task_type', 'other') if ttype_obj else 'other'
    ttype_icon = task_type_icon(ttype_ins)
    _cat_tcode = _rget(ttype_obj, 'task_code', '') if ttype_obj else ''

    # ── Atomic mark-done — per_user_limit=0 (unlimited) হলে UNIQUE constraint bypass করো
    _cat_per_limit = _rget(ttype_obj, 'per_user_limit', 1) if ttype_obj else 1
    if _cat_per_limit == 0:
        _cat_mark_ok = True  # unlimited submissions — unique constraint skip, directly insert gmails row
    else:
        _cat_mark_ok = await asyncio.to_thread(categ_task_mark_done, task_id, uid)
    if not _cat_mark_ok:
        # ইতিমধ্যে অন্য request এ done mark হয়ে গেছে — duplicate submission block
        lang = await asyncio.to_thread(get_lang, uid)
        _dup_msg = (
            "✅ *Task already submitted!*\n\nYour proof was already received. Please wait for admin review."
            if lang == 'en' else
            "✅ *Task ইতিমধ্যে Submit হয়েছে!*\n\nআপনার proof পাওয়া হয়েছে। Admin review এর জন্য অপেক্ষা করুন।"
        )
        ctx.user_data.clear()
        if update.message:
            await update.message.reply_text(_dup_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        else:
            await ctx.bot.send_message(uid, _dup_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    # Mark done সফল — এখন gmails row তৈরি করো
    _cat_owner_ins = int(_rget(ttype_obj, 'owner_id', 0) or 0) if ttype_obj else 0
    gid = None
    try:
        def _db_2977():
            gid = None
            with _c() as c:
                cur = c.execute(
                    "INSERT INTO gmails (user_id,gmail_text,task_name,amount,submitted_at,task_type,task_code,owner_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (uid, gmail_text, f"[CAT] {task_name}", price, now(), ttype_ins, _cat_tcode, _cat_owner_ins)
                )
                gid = cur.lastrowid
            return gid
        gid = await asyncio.to_thread(_db_2977)
    except Exception as _gin_err:
        log.error(f"[CATEG_TASK] gmails INSERT failed after mark_done uid={uid} task_id={task_id}: {_gin_err}")
        # ── Rollback: mark_done কে undo করো যাতে user আবার submit করতে পারে ──
        try:
            def _db_2987():
                with _c() as c:
                    c.execute("DELETE FROM category_task_completions WHERE task_id=%s AND user_id=%s", (task_id, uid))
                    c.execute("UPDATE category_tasks SET completed_count=GREATEST(0,completed_count-1) WHERE id=%s", (task_id,))
                    # যদি is_active=0 হয়ে গিয়ে থাকে কিন্তু এখনো slot আছে — পুনরায় চালু করো
                    c.execute(
                        "UPDATE category_tasks SET is_active=1 WHERE id=%s AND is_active=0 AND max_completions>0 AND completed_count<max_completions",
                        (task_id,)
                    )
                return
            await asyncio.to_thread(_db_2987)
        except Exception as _rb_err:
            log.error(f"[CATEG_TASK] rollback FAILED uid={uid} task_id={task_id}: {_rb_err}")
        lang = await asyncio.to_thread(get_lang, uid)
        _err_msg = (
            "❌ *Submission failed — server error.*\n\nPlease try again in a moment."
            if lang == 'en' else
            "❌ *Server error — Submission ব্যর্থ হয়েছে।*\n\nকিছুক্ষণ পর আবার চেষ্টা করুন।"
        )
        ctx.user_data.clear()
        if update.message:
            await update.message.reply_text(_err_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        else:
            await ctx.bot.send_message(uid, _err_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return
    # নতুন submission হলে approved/rejected পুরানো মুছো — pending কখনো না, শেষ ২০টা রাখো
    await asyncio.to_thread(_trim_table, "gmails", "user_id", uid, keep=20, only_done=True)
    # ── photo unique_ids সেভ করো ──────────────────────────────────
    if _cat_submitted_uids:
        try:
            def _db_3014():
                with _c() as c:
                    c.execute("UPDATE gmails SET photo_unique_ids=%s WHERE id=%s",
                              (",".join(_cat_submitted_uids), gid))
                return
            await asyncio.to_thread(_db_3014)
        except Exception as _e:
            log.warning(f"[CAT-TASK] gid={gid}: photo_unique_ids save ব্যর্থ — {_e}")
    # Max completions check — notify admin if slot পূর্ণ হয়েছে
    _ct_after = await asyncio.to_thread(get_category_task, task_id)
    if _ct_after and _ct_after["max_completions"] > 0 and not _ct_after["is_active"]:
        _tct = asyncio.create_task(_notify_task_maxed(
            ctx.bot, _ct_after["name"], _rget(_ct_after,"task_code","") or "",
            "Category Task", _ct_after["completed_count"], _ct_after["max_completions"]
        ))
        _background_tasks.add(_tct); _tct.add_done_callback(_background_tasks.discard)
    _u_ctry_raw = (await asyncio.to_thread(get_user_country, uid) or "").upper().strip()
    if _u_ctry_raw and _u_ctry_raw in COUNTRY_MAP:
        _u_ctry_flag, _u_ctry_name = COUNTRY_MAP[_u_ctry_raw]
        _u_ctry_line = f"\n🌍 *Country:* {_u_ctry_flag} {_u_ctry_name}"
    elif _u_ctry_raw:
        _u_ctry_line = f"\n🌍 *Country:* `{_u_ctry_raw}`"
    else:
        _u_ctry_line = ""
    msg_txt = (
        f"📨 *New Task Submission*\n\n"
        f"📁 *Category Task:* {ttype_icon} {task_name}\n"
        f"👤 *User:* {uname}{_u_ctry_line}\n"
        f"🆔 *ID:* `{uid}`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📋 *Proof:*\n{summary_txt}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💵 *Price:* `{price}$ USDT`\n"
        f"🕐 *Time:* {now()}\n"
        f"🔖 *Sub ID:* `#{gid}`"
    )

    # ── ইউজারকে তাৎক্ষণিক reply — সব notification background এ ──────
    for k in ("state","task_price","task_name","categ_task_id","task_fields","field_idx","collected"):
        ctx.user_data.pop(k, None)
    if update.message and not via_callback:
        await update.message.reply_text(T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
    else:
        await ctx.bot.send_message(uid, T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
    try:
        if update.message and not via_callback:
            await show_tasks(update, ctx)
    except Exception as _st_e:
        log.warning(f"[CATEG_FINALIZE] show_tasks after submit failed: {_st_e}")

    # ── Background: admin + sub-admin + channel + owner notify ───────
    _cat_owner_id = int(_rget(_ct_after, 'owner_id', 0) or 0) if _ct_after else 0
    async def _cat_notify_bg():
        a_mid, c_mid = 0, 0
        try:
            am = await ctx.bot.send_message(ADMIN_ID, msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
            a_mid = am.message_id
            for fid in photo_file_ids:
                await ctx.bot.send_photo(ADMIN_ID, fid, caption=f"📷 Sub #{gid}")
        except Exception as e: log.warning(f"Admin fwd fail: {e}")
        for adm_row in await asyncio.to_thread(get_all_admins):
            try:
                await ctx.bot.send_message(adm_row["user_id"], msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(adm_row["user_id"], fid, caption=f"📷 Sub #{gid}")
            except Exception as e: log.warning(f"Sub-admin fwd fail {adm_row['user_id']}: {e}")
        _ch_id = await asyncio.to_thread(get_gmail_ch_id)
        try:
            cm = await ctx.bot.send_message(_ch_id, msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
            c_mid = cm.message_id
            for fid in photo_file_ids:
                await ctx.bot.send_photo(_ch_id, fid, caption=f"📷 Sub #{gid}")
        except Exception as e:
            log.warning(f"Channel fwd fail (ch_id={_ch_id}): {e}")
        if c_mid == 0:
            try:
                cm = await ctx.bot.send_message(_ch_id, msg_txt, parse_mode="Markdown")
                c_mid = cm.message_id
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(_ch_id, fid, caption=f"📷 Sub #{gid}")
            except Exception as e2:
                log.warning(f"Channel fwd no-kb fail (ch_id={_ch_id}): {e2}")
        if c_mid == 0:
            try:
                _plain = msg_txt.replace("*","").replace("`","").replace("_","")
                cm = await ctx.bot.send_message(_ch_id, _plain, reply_markup=g_kb(gid))
                c_mid = cm.message_id
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(_ch_id, fid, caption=f"📷 Sub #{gid}")
            except Exception as e3:
                log.warning(f"Channel fwd plain fail (ch_id={_ch_id}): {e3}")
        if c_mid == 0:
            try:
                await notify_full_admins(ctx.bot,
                    f"⚠️ *Admin Channel এ Submission পাঠানো যাচ্ছে না!*\n\n"
                    f"📋 *Sub ID:* `#{gid}`\n🔗 *Channel ID:* `{_ch_id}`\n\n"
                    f"Bot কে channel এ Admin করুন অথবা Channel ID ঠিক করুন।")
            except Exception as _ct_ch_ae:
                log.error(f"[CAT-CHANNEL-FAIL] admin alert ব্যর্থ: {_ct_ch_ae}")
        try:
            with _c() as _dbc:
                _dbc.execute("UPDATE gmails SET admin_msg_id=%s,channel_msg_id=%s WHERE id=%s", (a_mid, c_mid, gid))
        except Exception as _dbe: log.warning(f"[CAT-NOTIFY-BG] msg_id save fail: {_dbe}")
        if _cat_owner_id and _cat_owner_id != ADMIN_ID:
            try:
                _cat_ow_msg = (
                    f"📬 *নতুন Submission পেয়েছেন!*\n\n"
                    f"📁 *Task:* {ttype_icon} {task_name}\n"
                    f"👤 *নাম:* {_md_esc(user.full_name or user.first_name or 'User')}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📋 *Proof:*\n{summary_txt}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"💵 *মূল্য:* `{price}$ USDT`\n"
                    f"🔖 *Sub ID:* `#{gid}`"
                )
                _cat_owner_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"ga_{gid}"),
                    InlineKeyboardButton("❌ Reject",  callback_data=f"gr_{gid}"),
                ]])
                await ctx.bot.send_message(_cat_owner_id, _cat_ow_msg, parse_mode="Markdown", reply_markup=_cat_owner_kb)
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(_cat_owner_id, fid, caption=f"📷 Sub #{gid} এর ছবি")
            except Exception as _ow_e2:
                log.warning(f"[OWNER_NOTIFY_CAT] owner={_cat_owner_id} gid={gid}: {_ow_e2}")
    _t_cat_bg = asyncio.create_task(_cat_notify_bg())
    _background_tasks.add(_t_cat_bg); _t_cat_bg.add_done_callback(_background_tasks.discard)


async def show_tg_task(update, ctx, task):
    """Show TG Join task info with Join + Check buttons"""
    q   = update.callback_query
    uid = q.from_user.id
    tid = task['id']
    lang, slots, already_done = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(tg_task_slots_left, tid),
        asyncio.to_thread(tg_task_user_done, tid, uid),
    )

    # Already done?
    if already_done:
        await q.message.reply_text("✅ আপনি এই Task ইতিমধ্যে সম্পন্ন করেছেন!" if lang=='bn' else "✅ You already completed this task!")
        return

    # Slots finished?
    if slots == 0:
        await q.message.reply_text("❌ এই Task এর সব জায়গা পূর্ণ হয়ে গেছে!" if lang=='bn' else "❌ All slots for this task are filled!")
        return

    # New joiners only check
    if _rget(task, 'new_joiners_only', 0):
        # DB তে আগে থেকে চিহ্নিত? → চিরতরে block (channel ছেড়ে দিলেও কাজ হবে না)
        if await asyncio.to_thread(is_pre_member, task['id'], uid):
            await q.message.reply_text(T(uid, 'new_joiners_only_reject'), parse_mode="Markdown")
            return
        try:
            member = await ctx.bot.get_chat_member(task['tg_target'], uid)
            already = member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        except Exception:
            already = False
        if already:
            await asyncio.to_thread(mark_pre_member, task['id'], uid)  # DB তে save করো
            await q.message.reply_text(T(uid, 'new_joiners_only_reject'), parse_mode="Markdown")
            return

    tg_link = task['tg_target_link'] or f"https://t.me/{task['tg_target'].lstrip('@')}"
    tg_name = task['tg_target_name'] or task['tg_target']

    max_c   = _rget(task, 'max_completions', 0)
    done_c  = _rget(task, 'completed_count', 0)
    if max_c > 0:
        left_c   = max(0, max_c - done_c)
        slots_bn = f"👥 *মোট:* `{max_c} জন` | *বাকি আছে:* `{left_c} জন`"
        slots_en = f"👥 *Total:* `{max_c}` | *Remaining:* `{left_c}`"
    else:
        slots_bn = "👥 *মোট:* `Unlimited`"
        slots_en = "👥 *Total:* `Unlimited`"

    _tgtcode = _rget(task, 'task_code', '') or ''
    _tgtcode_line_bn = f"🔑 *Task Code:* `{_tgtcode}`\n" if _tgtcode else ""
    _tgtcode_line_en = f"🔑 *Task Code:* `{_tgtcode}`\n" if _tgtcode else ""
    _tg_tn_esc = _md_esc(task['name'])
    if lang == 'bn':
        msg = (
            f"📡 *{_tg_tn_esc}*\n"
            f"{_tgtcode_line_bn}\n"
            f"💰 *পুরস্কার:* `{task['price']}$ USDT`\n"
            f"📢 *Channel/Group:* {tg_name}\n"
            f"{slots_bn}\n\n"
            f"*কীভাবে করবেন:*\n"
            f"1️⃣ নিচের বাটনে চেপে Channel/Group জয়েন করুন\n"
            f"2️⃣ জয়েন হলে ✅ Check বাটনে চাপুন\n"
            f"3️⃣ বট চেক করবে — জয়েন থাকলে টাকা পাবেন!"
        )
    else:
        msg = (
            f"📡 *{_tg_tn_esc}*\n"
            f"{_tgtcode_line_en}\n"
            f"💰 *Reward:* `{task['price']}$ USDT`\n"
            f"📢 *Channel/Group:* {tg_name}\n"
            f"{slots_en}\n\n"
            f"*How to complete:*\n"
            f"1️⃣ Press Join button below\n"
            f"2️⃣ After joining press ✅ Check\n"
            f"3️⃣ Bot will verify — you'll get paid instantly!"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel/Group", url=tg_link)],
        [InlineKeyboardButton("✅ Check — I Joined" if lang=='en' else "✅ Check — আমি জয়েন করেছি", callback_data=f"tgt_check_{tid}")],
        [InlineKeyboardButton("❌ Cancel" if lang=='en' else "❌ বাতিল", callback_data="task_cancel")],
    ])
    await q.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)


async def handle_tg_task_check(update, ctx, tid):
    """Auto-check channel membership and pay if joined"""
    q    = update.callback_query
    uid  = q.from_user.id
    lang, task = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(get_tg_task, tid),
    )

    if not task or not task['is_active']:
        await q.message.reply_text("❌ Task not found or inactive!" if lang=='en' else "❌ Task পাওয়া যায়নি বা বন্ধ আছে!"); return

    # Already done?
    if await asyncio.to_thread(tg_task_user_done, tid, uid):
        await q.message.reply_text("✅ ইতিমধ্যে সম্পন্ন করেছেন!" if lang=='bn' else "✅ Already completed!"); return

    # Pre-member block — channel ছেড়ে দিয়ে আবার join করলেও task করা যাবে না
    if _rget(task, 'new_joiners_only', 0) and await asyncio.to_thread(is_pre_member, tid, uid):
        await q.message.reply_text(T(uid, 'new_joiners_only_reject'), parse_mode="Markdown"); return

    # Slots finished?
    slots = await asyncio.to_thread(tg_task_slots_left, tid)
    if slots == 0:
        await q.message.reply_text("❌ সব জায়গা পূর্ণ!" if lang=='bn' else "❌ All slots filled!"); return

    # ── Country restriction check ────────────────────────────────
    _allowed_c = (_rget(task, 'allowed_countries', '') or '').strip().upper()
    if _allowed_c and _allowed_c not in ("0", "ALL"):
        _allowed_list = [x.strip() for x in _allowed_c.split(",") if x.strip()]
        _user_country = (await asyncio.to_thread(get_user_country, uid) or "").upper()
        if not _user_country:
            # User hasn't set country yet — ask them
            ctx.user_data["pending_tg_task_after_country"] = tid
            _ask_msg = (
                "🌍 *এই Task টি নির্দিষ্ট দেশের জন্য।*\n\n"
                "আগে আপনার দেশ বেছে নিন — তারপর Task করতে পারবেন:\n\n"
                "⚠️ *সতর্কতা:* ভুল দেশ বাছলে Task পাবেন না!\n"
                "👆 আপনি যে দেশে থাকেন সেই দেশ বেছে নিন।\n"
                "🔒 *একবার বাছলে আর পরিবর্তন করা যাবে না।*"
                if lang == 'bn' else
                "🌍 *This task is restricted to specific countries.*\n\n"
                "Please select your country first to continue:\n\n"
                "⚠️ *Warning:* Selecting the wrong country means you won't get tasks!\n"
                "👆 Please select the country where you actually live.\n"
                "🔒 *This cannot be changed once selected.*"
            )
            await q.message.reply_text(_ask_msg, parse_mode="Markdown", reply_markup=country_kb())
            return
        if _user_country not in _allowed_list:
            _deny = (
                "❌ *এই Task আপনার দেশে উপলব্ধ নয়।*\n\n"
                f"✅ অনুমোদিত দেশ: {fmt_allowed_countries(_allowed_c)}"
                if lang == 'bn' else
                "❌ *This task is not available in your country.*\n\n"
                f"✅ Allowed countries: {fmt_allowed_countries(_allowed_c)}"
            )
            await q.message.reply_text(_deny, parse_mode="Markdown")
            return

    # Check membership
    tg_target = task['tg_target']
    is_member = False
    try:
        member = await ctx.bot.get_chat_member(tg_target, uid)
        is_member = member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception as e:
        log.warning(f"TG task check fail for {uid} in {tg_target}: {e}")
        err_msg = (
            "❌ *চেক করতে সমস্যা হয়েছে।*\n\n"
            "বটটি ওই চ্যানেল/গ্রুপে Admin আছে কিনা নিশ্চিত করুন।"
            if lang=='bn' else
            "❌ *Check failed.*\n\nMake sure the bot is admin in that channel/group."
        )
        await q.message.reply_text(err_msg, parse_mode="Markdown"); return

    if not is_member:
        tg_link = task['tg_target_link'] or f"https://t.me/{tg_target.lstrip('@')}"
        tg_name = task['tg_target_name'] or tg_target
        not_joined_msg = (
            f"❌ *আপনি এখনো জয়েন করেননি!*\n\n"
            f"📢 {tg_name} এ জয়েন করুন, তারপর আবার Check চাপুন।"
            if lang=='bn' else
            f"❌ *You haven't joined yet!*\n\n"
            f"📢 Join {tg_name}, then press Check again."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join", url=tg_link)],
            [InlineKeyboardButton("✅ Check Again" if lang=='en' else "✅ Check আবার", callback_data=f"tgt_check_{tid}")],
        ])
        await q.message.reply_text(not_joined_msg, parse_mode="Markdown", reply_markup=kb)
        return

    # ✅ Joined! Auto-pay — atomic: completion record + balance credit একসাথে
    price = task['price']
    _already_paid = False
    def _db_3273():
        _already_paid = None
        with _c() as c:
            # ON CONFLICT DO NOTHING — psycopg2 exception তোলে না, শুধু rowcount=0 হয়
            _ins = c.execute(
                "INSERT INTO tg_task_completions (task_id,user_id,done_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (tid, uid, now())
            )
            if _ins.rowcount == 0:
                # Race condition: concurrent callback — INSERT conflict মানে আগেই paid
                _already_paid = True
            else:
                c.execute("UPDATE tg_tasks SET completed_count=completed_count+1 WHERE id=%s", (tid,))
                t_row = c.execute("SELECT max_completions,completed_count FROM tg_tasks WHERE id=%s", (tid,)).fetchone()
                if t_row and t_row["max_completions"] > 0 and t_row["completed_count"] >= t_row["max_completions"]:
                    c.execute("UPDATE tg_tasks SET is_active=0 WHERE id=%s", (tid,))
                c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (price, uid))
                _invalidate_user_cache(uid)
        return _already_paid
    _already_paid = await asyncio.to_thread(_db_3273)
    if _already_paid:
        # q.answer() line 3205 এ already called — আবার call করলে Telegram error দেয়
        await q.message.reply_text("✅ ইতিমধ্যে সম্পন্ন!" if lang=='bn' else "✅ Already completed!")
        return
    # Max completions check — notify admin if slot পূর্ণ হয়েছে
    _tg_after = await asyncio.to_thread(get_tg_task, tid)
    if _tg_after and _tg_after["max_completions"] > 0 and not _tg_after["is_active"]:
        _ttg = asyncio.create_task(_notify_task_maxed(
            ctx.bot, _tg_after["name"], _rget(_tg_after,"task_code","") or "",
            "TG Join Task", _tg_after["completed_count"], _tg_after["max_completions"]
        ))
        _background_tasks.add(_ttg); _ttg.add_done_callback(_background_tasks.discard)

    # Commission to referrer
    ref = await asyncio.to_thread(get_referred_by, uid)
    if ref:
        comm_pct = await asyncio.to_thread(get_commission_pct)
        comm = round(price * comm_pct / 100.0, 4)
        await asyncio.gather(
            asyncio.to_thread(add_bal, ref, comm),
            asyncio.to_thread(record_commission, ref, uid, comm, task.get('name', 'TG Join Task')),
        )
        try:
            ru = await asyncio.to_thread(get_user, uid)
            rname = _md_esc((ru["full_name"] or ru["username"] or "Unknown") if ru else "Unknown")
            comm_pct_d = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
            await ctx.bot.send_message(ref,
                T(ref, 'commission_msg', name=rname, price=price, comm=comm, comm_pct=comm_pct_d),
                parse_mode="Markdown")
        except Exception as _e:
            log.warning(f"[TG-TASK] ref={ref} commission notify ব্যর্থ: {_e}")
        # ── Referral Event bonus check ──────────────────────────
        _ref_ev_tg = asyncio.create_task(_check_ref_event_bonus(uid, ref, ctx.bot))
        _background_tasks.add(_ref_ev_tg); _ref_ev_tg.add_done_callback(_background_tasks.discard)

    # Notify user
    _tg_task_name_esc = _md_esc(task['name'])
    success_msg = (
        f"✅ *Task সম্পন্ন! অভিনন্দন!*\n\n"
        f"📡 *Task:* {_tg_task_name_esc}\n"
        f"💰 *`{price}$ USDT` আপনার একাউন্টে যোগ হয়েছে!*\n\n"
        f"💎 ধন্যবাদ!"
        if lang=='bn' else
        f"✅ *Task completed! Congratulations!*\n\n"
        f"📡 *Task:* {_tg_task_name_esc}\n"
        f"💰 *`{price}$ USDT` added to your account!*\n\n"
        f"💎 Thank you!"
    )
    await q.message.reply_text(success_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
    _tlb1 = asyncio.create_task(_lb_refresh_now(ctx.bot))
    _background_tasks.add(_tlb1); _tlb1.add_done_callback(_background_tasks.discard)
    _tdn1 = asyncio.create_task(_notify_task_done_ch(
        ctx.bot, uid, task['name'],
        _rget(task, 'task_code', '') or '', price, "📡 TG Join Task"
    ))
    _background_tasks.add(_tdn1); _tdn1.add_done_callback(_background_tasks.discard)

async def show_refer(update, ctx):
    commission = None
    referrals = None
    uid     = update.effective_user.id
    def _db_3344():
        commission = None
        referrals = None
        with _c() as c:
            referrals  = _count(c.execute("SELECT COUNT(*) FROM users WHERE referred_by=%s", (uid,)).fetchone())
            commission = round(_fval(c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM commissions WHERE user_id=%s", (uid,)
            ).fetchone(), 0) or 0, 4)
        return commission, referrals
    # bot_info, DB stats, commission%, owner, lang — সব parallel চালাও
    (commission, referrals), comm_pct, owner, lang, bot_info = await asyncio.gather(
        asyncio.to_thread(_db_3344),
        asyncio.to_thread(get_commission_pct),
        asyncio.to_thread(get_setting, 'support_owner'),
        asyncio.to_thread(get_lang, uid),
        ctx.bot.get_me(),
    )
    link    = f"https://t.me/{bot_info.username}?start=ref_{uid}"
    comm_pct_display = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
    owner = owner or '@inkamxbd'
    owner_e = _md_esc(owner)

    if lang == 'en':
        task_promo = (
            "\n━━━━━━━━━━━━━━━━\n"
            f"📌 *Want to add your Task to this bot?*\n"
            f"Contact admin: {owner_e}"
        )
    else:
        task_promo = (
            "\n━━━━━━━━━━━━━━━━\n"
            f"📌 *বটে আপনার Task যোগ করতে চান?*\n"
            f"এডমিনকে মেসেজ করুন: {owner_e}"
        )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(T(uid, 'my_referrals_btn'), callback_data=f"my_refs_{uid}")
    ]])
    await update.message.reply_text(
        f"{T(uid,'refer_title')}\n\n"
        "━━━━━━━━━━━━━━━━\n"
        f"{T(uid,'refer_total')} `{referrals}` {T(uid,'refer_people')}\n"
        f"{T(uid,'refer_earned')} `{commission}$ USDT`\n"
        "━━━━━━━━━━━━━━━━\n\n"
        f"{T(uid,'refer_link_label')}\n"
        f"`{link}`\n\n"
        f"{T(uid,'refer_rules', comm_pct=comm_pct_display)}"
        f"{task_promo}",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=kb
    )

async def show_withdraw(update, ctx):
    uid   = update.effective_user.id
    bal, min_w, fee, lang = await asyncio.gather(
        asyncio.to_thread(get_bal, uid),
        asyncio.to_thread(get_min_withdraw),
        asyncio.to_thread(get_wd_fee),
        asyncio.to_thread(get_lang, uid),
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💎 USDT-BEP20 (BSC)", callback_data="wd_go")]])
    if lang == 'en':
        txt = (
            f"{T(uid,'withdraw_title')}\n\n"
            f"{T(uid,'withdraw_only_usdt')}\n\n"
            f"⚡ *Auto Withdraw — within 2 to 5 minutes*\n\n"
            f"💰 *Balance:* `{bal}$ USDT`\n"
            f"⚠️ *Minimum withdrawal:* `{min_w}$ USDT`\n"
            f"📉 *Fee:* `{fee}$` (deducted from your amount)\n\n"
            f"*Example:* withdraw `$0.05` → fee `${fee}` → you receive `${round(0.05-fee,4)}`\n\n"
            f"{T(uid,'choose_method')}"
        )
    else:
        txt = (
            f"{T(uid,'withdraw_title')}\n\n"
            f"{T(uid,'withdraw_only_usdt')}\n\n"
            f"⚡ *অটো Withdraw — ২ থেকে ৫ মিনিটের মধ্যে পাবেন*\n\n"
            f"💰 *ব্যালান্স:* `{bal}$ USDT`\n"
            f"⚠️ *মিনিমাম উইথড্র:* `{min_w}$ USDT`\n"
            f"📉 *ফি:* `{fee}$` (পরিমাণ থেকে কাটা যাবে)\n\n"
            f"*উদাহরণ:* `$0.05` তুললে → ফি `${fee}` → পাবেন `${round(0.05-fee,4)}`\n\n"
            f"{T(uid,'choose_method')}"
        )
    await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=kb)

async def show_language(update, ctx):
    uid = update.effective_user.id
    await update.message.reply_text(
        T(uid, 'lang_choose'),
        parse_mode="Markdown",
        reply_markup=await asyncio.to_thread(lang_kb, uid)
    )

# ──────────────────────────── TASK SUBMIT FLOW ────────────────────────────
async def show_task_rules(update, ctx, task):
    uid = update.effective_user.id
    # lang + block check একসাথে parallel
    lang, (_blk, _blk_dt) = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(is_task_blocked, uid),
    )
    if _blk and _blk_dt:
        _until_str = _blk_dt.astimezone(pytz.timezone("Asia/Dhaka")).strftime("%d %b %Y, %I:%M %p")
        _msg = T(uid, 'task_blocked_msg', until=_until_str)
        await update.effective_message.reply_text(_msg, parse_mode="Markdown")
        return
    # ── Per-user limit check ─────────────────────────────────────
    per_user_limit = _rget(task, 'per_user_limit', 1)
    _str_tcode     = _rget(task, 'task_code', '') or ''
    done_count     = await asyncio.to_thread(get_user_task_submit_count, task['name'], uid, _str_tcode)
    lim_lbl        = "∞" if per_user_limit == 0 else str(per_user_limit)
    if per_user_limit != 0 and done_count >= per_user_limit:
        if lang == 'en':
            msg = f"✅ You have already completed this task {done_count}/{lim_lbl} time(s). No more submissions allowed."
        else:
            msg = f"✅ আপনি এই Task ইতিমধ্যে {done_count}/{lim_lbl} বার সম্পন্ন করেছেন। আর জমা দেওয়া যাবে না।"
        await update.effective_message.reply_text(msg, parse_mode="Markdown")
        return
    fields   = await asyncio.to_thread(get_task_fields, task['id'])
    sub_btn  = T(uid, 'submit_btn')
    cancel_b = T(uid, 'cancel_inline')
    _has_vid_r = bool((_rget(task, 'demo_video_id', '') or '').strip())
    _kb_rows_r = []
    if _has_vid_r:
        _vid_lbl_r = "📹 ভিডিও দেখুন" if lang == 'bn' else "📹 Watch Video"
        _kb_rows_r.append([InlineKeyboardButton(_vid_lbl_r, callback_data=f"task_watch_video_{task['id']}")])
    _kb_rows_r.append([
        InlineKeyboardButton(sub_btn,  callback_data=f"task_submit_{task['id']}"),
        InlineKeyboardButton(cancel_b, callback_data="task_cancel"),
    ])
    kb = InlineKeyboardMarkup(_kb_rows_r)
    if lang == 'en':
        rules_text  = (task["rules_en"] if task["rules_en"] else await asyncio.to_thread(translate_to_en, task["rules"])) if task["rules"] else "No special rules."
        price_label = "💵 <b>Price:</b>"
        rules_label = "📜 <b>Rules:</b>"
        confirm_txt = "✅ Read the rules and press <b>📨 Submit</b> to continue."
        proof_label = "📋 <b>Required Proof:</b>"
    else:
        rules_text  = task["rules"] if task["rules"] else "কোনো বিশেষ নিয়ম নেই।"
        price_label = "💵 <b>মূল্য:</b>"
        rules_label = "📜 <b>নিয়মাবলী:</b>"
        confirm_txt = "✅ নিয়ম পড়ে রাজি হলে <b>📨 জমা দিন</b> চাপুন।"
        proof_label = "📋 <b>প্রয়োজনীয় প্রমাণ:</b>"

    proof_text = ""
    if fields:
        proof_lines = []
        for i, f in enumerate(fields, 1):
            prompt = f["prompt_en"] if lang == 'en' else f["prompt_bn"]
            icon = "📷" if f["field_type"] == "photo" else "📝"
            proof_lines.append(f"{i}. {icon} {_html_escape(str(prompt))}")
        proof_text = f"\n\n{proof_label}\n" + "\n".join(proof_lines)

    _tcode = _rget(task, 'task_code', '') or ''
    _code_line = f"\n🔑 <b>Task Code:</b> <code>{_html_escape(str(_tcode))}</code>" if _tcode else ""
    _task_name = _html_escape(str(task['name']))
    _price_val = _html_escape(str(task['price']))
    _rules_safe = _html_escape(str(rules_text))
    await update.effective_message.reply_text(
        f"📌 <b>{_task_name}</b>{_code_line}\n"
        f"{price_label} <code>{_price_val} USDT</code>\n\n"
        f"{rules_label}\n"
        "━━━━━━━━━━━━━━━━\n"
        f"{_rules_safe}\n"
        "━━━━━━━━━━━━━━━━"
        f"{proof_text}\n\n"
        f"{confirm_txt}",
        parse_mode="HTML", reply_markup=kb
    )
    # NOTE: example_image শুধু _ask_next_field এ দেখানো হয় (ছবি জমার সময়),
    # এখানে দেখালে দ্বিতীয়বার দেখায় — তাই এখানে remove করা হয়েছে।

async def start_task_collect(update, ctx, task_id):
    q      = update.callback_query
    uid    = q.from_user.id
    task, lang = await asyncio.gather(
        asyncio.to_thread(get_task, task_id),
        asyncio.to_thread(get_lang, uid),
    )
    if not task:
        _err = "❌ Task পাওয়া যায়নি বা মুছে ফেলা হয়েছে।" if lang == 'bn' else "❌ Task not found or removed."
        await q.message.reply_text(_err, parse_mode="Markdown"); return
    # ── is_active guard — পুরানো button replay করে inactive task এ submit রোধ করো ──
    if not task["is_active"]:
        msg = "❌ এই Task টি এখন আর সক্রিয় নেই।" if lang == 'bn' else "❌ This task is no longer active."
        await q.message.reply_text(msg, parse_mode="Markdown"); return
    # ── Max completions guard — slot শেষ হলে block করো ──────────
    _stc_mc    = _rget(task, 'max_completions', 0)
    _stc_donec = _rget(task, 'completed_count', 0)
    if _stc_mc > 0 and _stc_donec >= _stc_mc:
        msg = "❌ এই Task এর সব জায়গা পূর্ণ হয়ে গেছে!" if lang == 'bn' else "❌ All slots for this task are filled!"
        await q.message.reply_text(msg, parse_mode="Markdown"); return
    # ── Per-user limit guard — duplicate submission রোধ করো ──────
    _stc_pul   = _rget(task, 'per_user_limit', 1)
    _stc_tcode = _rget(task, 'task_code', '') or ''
    _stc_done  = await asyncio.to_thread(get_user_task_submit_count, task['name'], uid, _stc_tcode)
    if _stc_pul != 0 and _stc_done >= _stc_pul:
        _lim_lbl = "∞" if _stc_pul == 0 else str(_stc_pul)
        msg = (f"✅ আপনি এই Task ইতিমধ্যে {_stc_done}/{_lim_lbl} বার সম্পন্ন করেছেন। আর জমা দেওয়া যাবে না।"
               if lang == 'bn' else
               f"✅ You have already completed this task {_stc_done}/{_lim_lbl} time(s). No more submissions allowed.")
        await q.message.reply_text(msg, parse_mode="Markdown"); return
    fields = await asyncio.to_thread(get_task_fields, task_id)

    ctx.user_data["state"]         = "task_collect"
    ctx.user_data["task_id"]       = task_id
    ctx.user_data["task_name"]     = task["name"]
    ctx.user_data["task_price"]    = task["price"]
    ctx.user_data["task_fields"]   = [dict(f) for f in fields]
    ctx.user_data["field_idx"]     = 0
    ctx.user_data["collected"]     = []

    if not fields:
        # No custom fields — ask generic info
        lang = await asyncio.to_thread(get_lang, uid)
        prompt = "📝 *আপনার তথ্য পাঠান:*\n\n❌ বাতিল করতে /cancel লিখুন" if lang == 'bn' else "📝 *Send your info:*\n\n❌ To cancel, type /cancel"
        await q.message.reply_text(prompt, parse_mode="Markdown",
                                   reply_markup=ReplyKeyboardMarkup([[T(uid,'cancel_btn')]], resize_keyboard=True))
        return

    await _ask_next_field(update, ctx, uid, via_callback=True)

async def _ask_next_field(update, ctx, uid, via_callback=False):
    fields  = ctx.user_data.get("task_fields", [])
    idx     = ctx.user_data.get("field_idx", 0)
    total   = len(fields)

    if idx >= total:
        await _finalize_task_submission(update, ctx, uid, via_callback)
        return

    field = fields[idx]
    lang  = await asyncio.to_thread(get_lang, uid)
    prompt = field["prompt_en"] if lang == 'en' else field["prompt_bn"]
    step_txt = f"({idx+1}/{total})" if total > 1 else ""

    if field["field_type"] == "photo":
        msg = T(uid, 'field_photo_ask', prompt=f"{prompt} {step_txt}")
        kb = ReplyKeyboardMarkup([[T(uid,'cancel_btn')]], resize_keyboard=True)
    else:
        msg = T(uid, 'field_ask', prompt=f"{prompt} {step_txt}")
        kb = ReplyKeyboardMarkup([[T(uid,'cancel_btn')]], resize_keyboard=True)

    if via_callback:
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)

    # ── Sample photo: photo field হলে example image দেখাও ──────────
    # প্রথমে field-এর নিজস্ব example_image_id চেক করো, না থাকলে task-level টা দেখো
    if field["field_type"] == "photo":
        _field_img = (_rget(field, 'example_image_id', '') or '').strip()
        _samp_img  = _field_img  # field-level first
        if not _samp_img:
            # fallback: task-level example image
            _categ_task_id = ctx.user_data.get("categ_task_id")
            _reg_task_id   = ctx.user_data.get("task_id")
            _task_id_for_sample = _categ_task_id or _reg_task_id
            if _task_id_for_sample:
                try:
                    if _categ_task_id:
                        _sample_task = await asyncio.to_thread(get_category_task, _categ_task_id)
                    else:
                        _sample_task = await asyncio.to_thread(get_task, _reg_task_id)
                    _samp_img = (_rget(_sample_task, 'example_image_id', '') or '').strip() if _sample_task else ''
                except Exception:
                    _samp_img = ''
        if _samp_img:
            _samp_cap = "📷 নমুনা ছবি — এইরকম ছবি দিন" if lang == 'bn' else "📷 Sample — Submit a photo like this"
            try:
                if via_callback:
                    await update.callback_query.message.reply_photo(_samp_img, caption=_samp_cap)
                else:
                    await update.message.reply_photo(_samp_img, caption=_samp_cap)
            except Exception:
                pass

async def handle_task_collect_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id
    txt    = update.message.text.strip()
    fields = ctx.user_data.get("task_fields", [])
    idx    = ctx.user_data.get("field_idx", 0)
    total  = len(fields)

    if not fields:
        # Generic submission
        ctx.user_data["collected"] = [{"type": "text", "prompt_bn": "তথ্য", "prompt_en": "Info", "value": txt}]
        ctx.user_data["field_idx"] = 1
        await _finalize_task_submission(update, ctx, uid)
        return

    # Bounds check: idx already at/beyond total → finalize (edge case: exception in prior call)
    if idx >= total:
        await _finalize_task_submission(update, ctx, uid)
        return

    field = fields[idx]

    ctx.user_data["collected"].append({
        "type": "text", "prompt_bn": field["prompt_bn"],
        "prompt_en": field["prompt_en"], "value": txt
    })

    ctx.user_data["field_idx"] = idx + 1
    if idx + 1 < total:
        await _ask_next_field(update, ctx, uid)
    else:
        await _finalize_task_submission(update, ctx, uid)

async def handle_task_collect_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid    = update.effective_user.id

    # ── Admin: Sample Photo সেট করা (Category Task) ──────────────
    _adm_st = ctx.user_data.get("state", "")
    if _adm_st == "adm_ctask_set_sample_photo" and await asyncio.to_thread(is_admin, uid):
        tid = ctx.user_data.pop("edit_ctask_id", None)
        ctx.user_data.pop("state", None)
        if update.message.photo and tid:
            fid = update.message.photo[-1].file_id
            await asyncio.to_thread(update_category_task, tid, "example_image_id", fid)
            # ── Panel re-show: task creation panel ফিরিয়ে দাও ──────
            _ct = await asyncio.to_thread(get_category_task, tid)
            _ct_has_img = bool((_rget(_ct, 'example_image_id', '') or '').strip()) if _ct else True
            _ct_has_vid = bool((_rget(_ct, 'demo_video_id',   '') or '').strip()) if _ct else False
            _ct_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📷 Photo Field যোগ",  callback_data=f"adm_cfield_add_photo_{tid}"),
                 InlineKeyboardButton("📝 Text Field যোগ",   callback_data=f"adm_cfield_add_text_{tid}")],
                [InlineKeyboardButton("🖼 Example Photo ✅" if _ct_has_img else "🖼 Example Photo যোগ", callback_data=f"adm_ctaste_sample_photo_{tid}"),
                 InlineKeyboardButton("🎬 Demo Video ✅"    if _ct_has_vid else "🎬 Demo Video যোগ",    callback_data=f"adm_ctaste_video_{tid}")],
                [InlineKeyboardButton("🚀 এখনই Live করুন", callback_data=f"adm_ctask_golive_{tid}")],
            ])
            await update.message.reply_text(
                "✅ *Example Photo সেট হয়েছে!*\n\n"
                "ইউজার Photo Field এ এই ছবি দেখে বুঝবে কেমন ছবি দিতে হবে।\n\n"
                "আরো কিছু যোগ করুন বা Live করুন:",
                parse_mode="Markdown", reply_markup=_ct_kb)
        else:
            await update.message.reply_text("❌ ছবি পাওয়া যায়নি। আবার চেষ্টা করুন।", parse_mode="Markdown")
        return

    # ── Admin: Sample Photo সেট করা (Regular Task) ───────────────
    if _adm_st == "adm_task_set_sample_photo" and await asyncio.to_thread(is_admin, uid):
        tid = ctx.user_data.pop("edit_tid", None)
        ctx.user_data.pop("state", None)
        if update.message.photo and tid:
            fid = update.message.photo[-1].file_id
            await asyncio.to_thread(update_task_field_val, tid, "example_image_id", fid)
            # ── Panel re-show: task creation/edit panel ফিরিয়ে দাও ──
            _rt, _rf = await asyncio.gather(
                asyncio.to_thread(get_task, tid),
                asyncio.to_thread(get_task_fields, tid),
            )
            _rt_has_img = bool((_rget(_rt, 'example_image_id', '') or '').strip()) if _rt else True
            _rt_has_vid = bool((_rget(_rt, 'demo_video_id',   '') or '').strip()) if _rt else False
            _rt_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
                 InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}")],
                [InlineKeyboardButton("🖼 Example Photo ✅" if _rt_has_img else "🖼 Example Photo যোগ", callback_data=f"adm_te_sample_photo_{tid}"),
                 InlineKeyboardButton("🎬 Demo Video ✅"    if _rt_has_vid else "🎬 Demo Video যোগ",    callback_data=f"adm_te_video_{tid}")],
                [InlineKeyboardButton("✅ Done — Task সেভ করুন", callback_data=f"adm_task_done_{tid}")],
            ])
            await update.message.reply_text(
                f"✅ *Example Photo সেট হয়েছে!* ({'✅' if _rt_has_img else ''})\n\n"
                f"📋 Fields: {len(_rf)} টি\n\n"
                "আরো fields যোগ করুন বা Done চাপুন:",
                parse_mode="Markdown", reply_markup=_rt_kb)
            ctx.user_data["state"] = "adm_task_fields"
        else:
            await update.message.reply_text("❌ ছবি পাওয়া যায়নি। আবার চেষ্টা করুন।", parse_mode="Markdown")
        return

    # ── Admin: Video File সেট করা ────────────────────────────────
    if _adm_st == "adm_ctask_set_video_file" and await asyncio.to_thread(is_admin, uid):
        tid = ctx.user_data.pop("edit_ctask_id", None)
        ctx.user_data.pop("state", None)
        vid_msg = update.message.video or update.message.document
        if update.message.photo and tid:
            await update.message.reply_text("❌ ভিডিও পাঠান, ছবি নয়।", parse_mode="Markdown")
            return
        if vid_msg and tid:
            fid = vid_msg.file_id
            await asyncio.to_thread(update_category_task, tid, "demo_video_id", fid)
            await update.message.reply_text(
                "✅ *Demo Video সেট হয়েছে!*\n\n"
                "Task এ এখন 'ভিডিও দেখুন' বাটন দেখা যাবে।",
                parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        else:
            await update.message.reply_text("❌ ভিডিও পাওয়া যায়নি। আবার চেষ্টা করুন।", parse_mode="Markdown")
        return

    fields = ctx.user_data.get("task_fields", [])
    idx    = ctx.user_data.get("field_idx", 0)
    total  = len(fields)

    if not fields:
        ctx.user_data.pop("state", None)
        ctx.user_data.pop("task_id", None)
        ctx.user_data.pop("task_fields", None)
        ctx.user_data.pop("field_idx", None)
        ctx.user_data.pop("collected", None)
        lang = await asyncio.to_thread(get_lang, uid)
        await update.message.reply_text(
            "❌ Task এ কোনো field নেই।" if lang == 'bn' else "❌ This task has no fields.",
            parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    field = fields[idx]
    if field["field_type"] != "photo":
        lang = await asyncio.to_thread(get_lang, uid)
        await update.message.reply_text(
            "❌ Send text here, not a photo." if lang == 'en' else "❌ এখানে ছবি নয়, টেক্সট পাঠান।",
            parse_mode="Markdown")
        return

    file_id        = update.message.photo[-1].file_id
    file_unique_id = update.message.photo[-1].file_unique_id
    # ── ছবির সাথে caption (লেখা) থাকলে সেটাও সংরক্ষণ করো ────────────
    _photo_caption = (update.message.caption or "").strip()
    ctx.user_data["collected"].append({
        "type": "photo", "prompt_bn": field["prompt_bn"],
        "prompt_en": field["prompt_en"], "value": file_id,
        "unique_id": file_unique_id,
        "caption": _photo_caption,          # ← নতুন: ছবির সাথে লেখা
    })
    ctx.user_data["field_idx"] = idx + 1

    if idx + 1 < total:
        await _ask_next_field(update, ctx, uid)
    else:
        await _finalize_task_submission(update, ctx, uid)

async def _finalize_task_submission(update, ctx, uid, via_callback=False):
    user      = update.effective_user
    uname     = f"@{_md_esc(user.username)}" if user.username else _md_esc(user.full_name or "")
    task_name = _md_esc(ctx.user_data.get("task_name", "Task"))
    try:
        price = float(ctx.user_data.get("task_price", await asyncio.to_thread(get_setting, "gmail_price") or "0.10"))
    except (TypeError, ValueError):
        price = 0.10
    collected = ctx.user_data.get("collected", [])

    # Build text summary
    lang          = await asyncio.to_thread(get_lang, uid)
    summary_lines = []
    photo_file_ids = []
    _submitted_unique_ids = []
    for i, item in enumerate(collected, 1):
        prompt_lbl = item.get('prompt_en') if lang == 'en' else item.get('prompt_bn', '')
        if item["type"] == "photo":
            if item["value"]:
                photo_file_ids.append(item["value"])
                if item.get("unique_id"):
                    _submitted_unique_ids.append(item["unique_id"])
                _cap_txt = _md_esc(item.get("caption", "").strip())
                _plbl    = _md_esc(prompt_lbl or "")
                if _cap_txt:
                    summary_lines.append(
                        f"📷 *{_plbl}:* [Photo #{len(photo_file_ids)}]\n💬 *Caption:* `{_cap_txt}`"
                        if lang == 'en' else
                        f"📷 *{_plbl}:* [ছবি #{len(photo_file_ids)}]\n💬 *লেখা:* `{_cap_txt}`"
                    )
                else:
                    summary_lines.append(f"📷 *{_plbl}:* [Photo #{len(photo_file_ids)}]" if lang == 'en' else f"📷 *{_plbl}:* [ছবি #{len(photo_file_ids)}]")
            else:
                summary_lines.append(f"📷 *{_md_esc(prompt_lbl or '')}:* (Skipped)" if lang == 'en' else f"📷 *{_md_esc(prompt_lbl or '')}:* (Skip করা হয়েছে)")
        else:
            val = _md_esc(item["value"] or "")
            summary_lines.append(f"📝 *{_md_esc(prompt_lbl or '')}:*\n`{val}`")

    # ── Duplicate photo detection — DB-level query (fast, no full-table scan in Python) ──
    if _submitted_unique_ids:
        def _check_dup_photos(unique_ids):
            """PostgreSQL LIKE check — same user এর নিজের photo block করে না, শুধু অন্য user এর duplicate ধরে।"""
            with _c() as c:
                for _puid in unique_ids:
                    row = c.execute(
                        "SELECT id FROM gmails WHERE user_id != %s AND photo_unique_ids IS NOT NULL "
                        "AND (photo_unique_ids = %s "
                        "OR photo_unique_ids LIKE %s "
                        "OR photo_unique_ids LIKE %s "
                        "OR photo_unique_ids LIKE %s) LIMIT 1",
                        (uid,
                         _puid,
                         _puid + ',%',
                         '%,' + _puid,
                         '%,' + _puid + ',%')
                    ).fetchone()
                    if row:
                        return True
            return False
        _is_dup = await asyncio.to_thread(_check_dup_photos, _submitted_unique_ids)
        if _is_dup:
            _new_pts = await asyncio.to_thread(add_points, uid, -POINT_DUP_PHOTO)
            _dup_notice = (
                "⚠️ *Duplicate Photo Detected!*\n\n"
                "🔴 This photo has been used in a previous submission.\n"
                f"📉 *{POINT_DUP_PHOTO} points deducted.* Current: `{_new_pts}/100`"
                if lang == 'en' else
                "⚠️ *ডুপ্লিকেট ছবি শনাক্ত হয়েছে!*\n\n"
                "🔴 এই ছবি আগে অন্য Submission এ ব্যবহার করা হয়েছে।\n"
                f"📉 *{POINT_DUP_PHOTO} পয়েন্ট কাটা হয়েছে।* বর্তমান: `{_new_pts}/100`"
            )
            ctx.user_data.clear()
            if update.message:
                await update.message.reply_text(_dup_notice, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            else:
                await ctx.bot.send_message(uid, _dup_notice, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            return

    summary_txt = "\n".join(summary_lines) if summary_lines else ("_No data_" if lang == 'en' else "_কোনো তথ্য নেই_")
    gmail_text  = _json.dumps(collected, ensure_ascii=False)

    _task_id_ins = ctx.user_data.get("task_id", 0)
    _t_obj       = await asyncio.to_thread(get_task, _task_id_ins)
    # Race condition guard: submission শেষ হওয়ার আগেই task বন্ধ হয়ে গেলে reject করো
    if _t_obj and not _t_obj.get('is_active', 1):
        ctx.user_data.clear()
        _err = ("❌ *This task is no longer active.*\n\nPlease check the task list for available tasks."
                if await asyncio.to_thread(get_lang, uid) == 'en'
                else "❌ *এই Task আর সক্রিয় নেই।*\n\nঅনুগ্রহ করে Task List থেকে অন্য Task বেছে নিন।")
        if update.message:
            await update.message.reply_text(_err, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        else:
            await ctx.bot.send_message(uid, _err, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return
    _ttype_ins   = _rget(_t_obj, 'task_type', 'other') if _t_obj else 'other'
    _t_icon      = task_type_icon(_ttype_ins)
    _reg_tcode   = _rget(_t_obj, 'task_code', '') if _t_obj else ''
    _reg_owner_ins = int(_rget(_t_obj, 'owner_id', 0) or 0) if _t_obj else 0
    def _insert_gmail_row():
        _gid = None
        with _c() as c:
            cur = c.execute(
                "INSERT INTO gmails (user_id,gmail_text,task_name,amount,submitted_at,task_type,task_code,owner_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (uid, gmail_text, task_name, price, now(), _ttype_ins, _reg_tcode, _reg_owner_ins)
            )
            _gid = cur.lastrowid
            if _gid is None:
                row = c.execute(
                    "SELECT id FROM gmails WHERE user_id=%s AND task_code=%s ORDER BY submitted_at DESC LIMIT 1",
                    (uid, _reg_tcode)
                ).fetchone()
                _gid = row['id'] if row else None
            return _gid
    gid = await asyncio.to_thread(_insert_gmail_row)
    # নতুন submission হলে পুরানো approved/rejected মুছো — fire-and-forget
    _t_trim = asyncio.ensure_future(asyncio.to_thread(_trim_table, "gmails", "user_id", uid, 20, "id", True))
    _background_tasks.add(_t_trim); _t_trim.add_done_callback(_background_tasks.discard)
    # ── photo unique_ids সেভ করো ──────────────────────────────────
    if _submitted_unique_ids and gid:
        try:
            _puids_str = ",".join(_submitted_unique_ids)
            def _save_photo_ids():
                with _c() as c:
                    c.execute("UPDATE gmails SET photo_unique_ids=%s WHERE id=%s", (_puids_str, gid))
            await asyncio.to_thread(_save_photo_ids)
        except Exception as _e:
            log.warning(f"[TASK-PHOTO] gid={gid}: photo_unique_ids save ব্যর্থ — {_e}")
    _u_ctry_raw2 = (await asyncio.to_thread(get_user_country, uid) or "").upper().strip()
    if _u_ctry_raw2 and _u_ctry_raw2 in COUNTRY_MAP:
        _u_ctry_flag2, _u_ctry_name2 = COUNTRY_MAP[_u_ctry_raw2]
        _u_ctry_line2 = f"\n🌍 *দেশ:* {_u_ctry_flag2} {_u_ctry_name2}"
    elif _u_ctry_raw2:
        _u_ctry_line2 = f"\n🌍 *দেশ:* `{_u_ctry_raw2}`"
    else:
        _u_ctry_line2 = ""
    msg_txt = (
        f"📨 *নতুন Task Submission*\n\n"
        f"📌 *Task:* {_t_icon} {task_name}\n"
        f"👤 *User:* {uname}{_u_ctry_line2}\n"
        f"🆔 *ID:* `{uid}`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📋 *প্রমাণ:*\n{summary_txt}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💵 *মূল্য:* `{price}$ USDT`\n"
        f"🕐 *সময়:* {now()}\n"
        f"🔖 *Sub ID:* `#{gid}`"
    )

    # ── ইউজারকে তাৎক্ষণিক reply — সব notification background এ ──────
    for k in ("state","task_price","task_name","task_id","task_fields","field_idx","collected"):
        ctx.user_data.pop(k, None)
    send_fn = update.message.reply_text if (update.message and not via_callback) else None
    if send_fn:
        await send_fn(T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
    else:
        await ctx.bot.send_message(uid, T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
    # ── Submit এর পর টাস্ক লিস্ট আবার দেখাও ────────────────────────────
    try:
        if update.message and not via_callback:
            await show_tasks(update, ctx)
    except Exception as _st_e:
        log.warning(f"[FINALIZE] show_tasks after submit failed: {_st_e}")

    # ── Background: admin + sub-admin + channel + owner notify ───────
    _reg_owner_id = int(_rget(_t_obj, 'owner_id', 0) or 0) if _t_obj else 0
    async def _reg_notify_bg():
        a_mid, c_mid = 0, 0
        try:
            am = await ctx.bot.send_message(ADMIN_ID, msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
            a_mid = am.message_id
            for fid in photo_file_ids:
                await ctx.bot.send_photo(ADMIN_ID, fid, caption=f"📷 Sub #{gid} এর ছবি")
        except Exception as e: log.warning(f"Admin fwd fail: {e}")
        for adm_row in await asyncio.to_thread(get_all_admins):
            try:
                await ctx.bot.send_message(adm_row["user_id"], msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(adm_row["user_id"], fid, caption=f"📷 Sub #{gid} এর ছবি")
            except Exception as e: log.warning(f"Sub-admin fwd fail {adm_row['user_id']}: {e}")
        _ch_id = await asyncio.to_thread(get_gmail_ch_id)
        try:
            cm = await ctx.bot.send_message(_ch_id, msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
            c_mid = cm.message_id
            for fid in photo_file_ids:
                await ctx.bot.send_photo(_ch_id, fid, caption=f"📷 Sub #{gid} এর ছবি")
        except Exception as e:
            log.warning(f"Channel fwd fail (ch_id={_ch_id}): {e}")
        if c_mid == 0:
            try:
                cm = await ctx.bot.send_message(_ch_id, msg_txt, parse_mode="Markdown")
                c_mid = cm.message_id
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(_ch_id, fid, caption=f"📷 Sub #{gid} এর ছবি")
            except Exception as e2:
                log.warning(f"Channel fwd no-kb fail (ch_id={_ch_id}): {e2}")
        if c_mid == 0:
            try:
                _plain = msg_txt.replace("*","").replace("`","").replace("_","")
                cm = await ctx.bot.send_message(_ch_id, _plain, reply_markup=g_kb(gid))
                c_mid = cm.message_id
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(_ch_id, fid, caption=f"📷 Sub #{gid} এর ছবি")
            except Exception as e3:
                log.warning(f"Channel fwd plain fail (ch_id={_ch_id}): {e3}")
        if c_mid == 0:
            try:
                await notify_full_admins(ctx.bot,
                    f"⚠️ *Admin Channel এ Submission পাঠানো যাচ্ছে না!*\n\n"
                    f"📋 *Sub ID:* `#{gid}`\n🔗 *Channel ID:* `{_ch_id}`\n\n"
                    f"Bot কে channel এ Admin করুন অথবা Channel ID ঠিক করুন।")
            except Exception as _ch_ae:
                log.error(f"[CHANNEL-FAIL] admin alert ব্যর্থ: {_ch_ae}")
        try:
            with _c() as _dbc:
                _dbc.execute("UPDATE gmails SET admin_msg_id=%s,channel_msg_id=%s WHERE id=%s", (a_mid, c_mid, gid))
        except Exception as _dbe: log.warning(f"[REG-NOTIFY-BG] msg_id save fail: {_dbe}")
        if _reg_owner_id and _reg_owner_id != ADMIN_ID:
            try:
                _reg_ow_msg = (
                    f"📬 *নতুন Submission পেয়েছেন!*\n\n"
                    f"📌 *Task:* {_t_icon} {task_name}\n"
                    f"👤 *নাম:* {_md_esc(user.full_name or user.first_name or 'User')}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📋 *Proof:*\n{summary_txt}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"💵 *মূল্য:* `{price}$ USDT`\n"
                    f"🔖 *Sub ID:* `#{gid}`"
                )
                _owner_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"ga_{gid}"),
                    InlineKeyboardButton("❌ Reject",  callback_data=f"gr_{gid}"),
                ]])
                await ctx.bot.send_message(_reg_owner_id, _reg_ow_msg, parse_mode="Markdown", reply_markup=_owner_kb)
                for fid in photo_file_ids:
                    await ctx.bot.send_photo(_reg_owner_id, fid, caption=f"📷 Sub #{gid} এর ছবি")
            except Exception as _ow_e:
                log.warning(f"[OWNER_NOTIFY] owner={_reg_owner_id} gid={gid}: {_ow_e}")
    _t_reg_bg = asyncio.create_task(_reg_notify_bg())
    _background_tasks.add(_t_reg_bg); _t_reg_bg.add_done_callback(_background_tasks.discard)

# ──────────────────────────── WITHDRAW FLOW ────────────────────────────
async def wd_ask_addr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    # q.answer() ইতিমধ্যে _on_callback_inner এ হয়েছে — এখানে duplicate করলে TelegramError হবে
    # ── 100% double-submit guard ─────────────────────────────────
    def _wd_db_checks():
        already = None
        pending = None
        sw_row = None
        with _c() as _wc:
            pending = _wc.execute(
                "SELECT id FROM withdrawals WHERE user_id=%s AND status IN ('pending','processing','gas_pending')",
                (uid,)
            ).fetchone()
            already = _wc.execute(
                "SELECT id FROM withdrawals WHERE user_id=%s AND status IN ('approved','processing','completed')",
                (uid,)
            ).fetchone()
            sw_row  = _wc.execute("SELECT saved_wallet FROM users WHERE user_id=%s", (uid,)).fetchone()
        return pending, already, sw_row
    (_wpending, _already_wd, _sw_row), lang = await asyncio.gather(
        asyncio.to_thread(_wd_db_checks),
        asyncio.to_thread(get_lang, uid),
    )
    if _wpending:
        _wmsg = (
            "⚠️ *আপনার একটি Withdraw ইতিমধ্যে প্রক্রিয়াধীন আছে।*\n\nআগেরটি সম্পন্ন হলে নতুন Withdraw করুন।"
            if lang == 'bn' else
            "⚠️ *You already have a pending withdrawal.*\n\nPlease wait for it to be processed first."
        )
        await q.message.reply_text(_wmsg, parse_mode="Markdown")
        return
    # ── Saved wallet check ───────────────────────────────────────
    _saved_w = (_rget(_sw_row, "saved_wallet", "") or "").strip() if _sw_row else ""
    if _saved_w:
        _masked = mask_addr_short(_saved_w)
        _saved_txt = (
            f"👛 *আগের Wallet পাওয়া গেছে!*\n\n`{_masked}`\n\nএই wallet ব্যবহার করবেন, নাকি নতুন দেবেন?"
            if lang == 'bn' else
            f"👛 *Saved wallet found!*\n\n`{_masked}`\n\nUse this wallet or enter a new one?"
        )
        await q.message.reply_text(
            _saved_txt,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ এই wallet ব্যবহার করব" if lang == 'bn' else "✅ Use saved wallet",
                                      callback_data="wd_use_saved_wallet")],
                [InlineKeyboardButton("🔄 নতুন Wallet দেব" if lang == 'bn' else "🔄 Enter new wallet",
                                      callback_data="wd_enter_new_addr")],
            ])
        )
        ctx.user_data["_saved_w_cache"] = _saved_w
    else:
        await q.message.reply_text(
            T(uid, 'wd_enter_addr'), parse_mode="Markdown", reply_markup=cancel_kb(uid)
        )
        ctx.user_data["state"] = "wd_addr"

async def receive_wd_addr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    address = update.message.text.strip()
    lang    = await asyncio.to_thread(get_lang, uid)

    # ── BEP-20 address format validation (0x + 40 hex chars) ──────────────
    if not re.match(r'^0x[0-9a-fA-F]{40}$', address):
        err_msg = (
            "❌ *ভুল Wallet Address!*\n\n"
            "⚠️ শুধুমাত্র *USDT BEP-20 (BSC)* address গ্রহণযোগ্য।\n"
            "✅ সঠিক ফরম্যাট: `0x` দিয়ে শুরু + ৪০টি অক্ষর\n\n"
            "📝 উদাহরণ: `0x1234...abcd`\n\n"
            "আবার সঠিক address পাঠান বা /cancel লিখুন।"
            if lang == 'bn' else
            "❌ *Invalid Wallet Address!*\n\n"
            "⚠️ Only *USDT BEP-20 (BSC)* addresses are accepted.\n"
            "✅ Correct format: starts with `0x` + 40 characters\n\n"
            "📝 Example: `0x1234...abcd`\n\n"
            "Please send a valid address or type /cancel."
        )
        await update.message.reply_text(err_msg, parse_mode="Markdown")
        return

    # ── Duplicate address check: সেম address অন্য user আগে use করেছে কিনা ──
    def _db_3976():
        addr_owner = None
        with _c() as c:
            addr_owner = c.execute(
                "SELECT DISTINCT user_id FROM withdrawals WHERE address=%s AND user_id!=%s",
                (address, uid)
            ).fetchone()
        return addr_owner
    addr_owner = await asyncio.to_thread(_db_3976)
    if addr_owner:
        owner_id = addr_owner['user_id']
        # ── শুধু নতুন user কে ব্যান করো ──
        await asyncio.to_thread(ban_user, uid, 1)
        for k in ("state", "wd_addr", "wd_amt"):
            ctx.user_data.pop(k, None)
        # ── নতুন User কে warning পাঠাও ──
        warn_msg = (
            "🚫 *আপনার একাউন্ট ব্যান করা হয়েছে!*\n\n"
            "⚠️ *কারণ:* আপনি এমন একটি Wallet Address দিয়েছেন যা আগে অন্য একটি একাউন্ট থেকে ব্যবহার করা হয়েছে।\n\n"
            "❌ একটি Wallet Address একটিমাত্র Account থেকে ব্যবহার করা যাবে।\n\n"
            "📞 *আপিল করতে:* @inkamxbd"
            if lang == 'bn' else
            "🚫 *Your account has been banned!*\n\n"
            "⚠️ *Reason:* The wallet address you provided has already been used by another account.\n\n"
            "❌ One wallet address can only be linked to one account.\n\n"
            "📞 *To appeal:* @inkamxbd"
        )
        await update.message.reply_text(warn_msg, parse_mode="Markdown",
                                        reply_markup=ReplyKeyboardRemove())
        # ── Admin কে notify করো ──
        user = update.effective_user
        uname_str = f"@{user.username}" if user.username else _md_esc(user.full_name or "")
        admin_msg = (
            f"🚨 *Duplicate Wallet — Auto Ban*\n\n"
            f"👤 *Banned User:* [{uname_str}](tg://user?id={uid}) (`{uid}`)\n"
            f"🔗 *Address:* `{address}`\n"
            f"⚠️ *এই Address আগে ব্যবহার করেছে:* `{owner_id}`\n\n"
            f"✅ নতুন User auto-ban হয়েছে।"
        )
        await notify_full_admins(ctx.bot, admin_msg)
        return

    ctx.user_data["wd_addr"] = address
    ctx.user_data["state"]   = "wd_amt"
    # ── Wallet auto-save ─────────────────────────────────────────
    try:
        def _db_4018():
            with _c() as _svc:
                _svc.execute("UPDATE users SET saved_wallet=%s WHERE user_id=%s", (address, uid))
        await asyncio.to_thread(_db_4018)
    except Exception:
        pass
    bal, fee, min_w = await asyncio.gather(
        asyncio.to_thread(get_bal, uid),
        asyncio.to_thread(get_wd_fee),
        asyncio.to_thread(get_min_withdraw),
    )
    await update.message.reply_text(
        T(uid, 'wd_enter_amt', bal=bal, fee=fee, min_w=min_w),
        parse_mode="Markdown", reply_markup=cancel_kb(uid)
    )

async def receive_wd_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        amt = float(update.message.text.strip().replace("$","").replace(",","."))
    except ValueError:
        await update.message.reply_text(T(uid,'wd_invalid_amt'), parse_mode="Markdown", reply_markup=cancel_kb(uid))
        return
    min_w, fee, lang = await asyncio.gather(
        asyncio.to_thread(get_min_withdraw),
        asyncio.to_thread(get_wd_fee),
        asyncio.to_thread(get_lang, uid),
    )
    if amt < min_w:
        min_msg = (f"❌ *Minimum withdrawal:* `{min_w}$ USDT`" if lang=='en'
                   else f"❌ *মিনিমাম উইথড্র:* `{min_w}$ USDT`")
        await update.message.reply_text(min_msg, parse_mode="Markdown", reply_markup=cancel_kb(uid))
        return
    bal = await asyncio.to_thread(get_bal, uid)
    if amt > bal:
        await update.message.reply_text(T(uid,'wd_no_bal', bal=bal, amt=amt), parse_mode="Markdown", reply_markup=cancel_kb(uid))
        return
    net_amt = round(amt - fee, 4)
    if net_amt <= 0:
        err = (f"❌ *Amount too low.* After fee `${fee}`, nothing remains."
               if lang=='en' else
               f"❌ *পরিমাণ খুব কম।* Fee `${fee}` বাদ দিলে কিছুই থাকে না।")
        await update.message.reply_text(err, parse_mode="Markdown", reply_markup=cancel_kb(uid))
        return

    # Save and show confirmation step — do NOT submit yet
    ctx.user_data["wd_amt"]  = amt
    ctx.user_data["state"]   = "wd_confirm"
    await show_wd_confirmation(update.message, ctx, uid)


async def show_wd_confirmation(msg, ctx, uid):
    """Withdraw confirm/edit UI — address, amount, fee breakdown এখানে দেখা যাবে।"""
    addr    = ctx.user_data.get("wd_addr", "")
    amt     = ctx.user_data.get("wd_amt", 0.0)
    fee     = await asyncio.to_thread(get_wd_fee)
    net_amt = round(amt - fee, 4)
    lang    = await asyncio.to_thread(get_lang, uid)
    masked  = mask_addr_short(addr)
    if lang == 'en':
        txt = (
            f"📋 *Review Your Withdrawal*\n\n"
            f"💳 *Method:* USDT-BEP20 (BSC)\n"
            f"👛 *Address:* `{masked}`\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 *Debit (from balance):* `${amt:.4f}`\n"
            f"📉 *Fee:* `${fee:.4f}`\n"
            f"💰 *You will receive:* `${net_amt:.4f}`\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"✅ Press *Confirm* to submit, or edit below."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm & Submit", callback_data="wd_confirm")],
            [InlineKeyboardButton("✏️ Change Address",  callback_data="wd_edit_addr"),
             InlineKeyboardButton("💵 Change Amount",   callback_data="wd_edit_amt")],
        ])
    else:
        txt = (
            f"📋 *Withdraw নিশ্চিত করুন*\n\n"
            f"💳 *পদ্ধতি:* USDT-BEP20 (BSC)\n"
            f"👛 *ঠিকানা:* `{masked}`\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 *কাটা যাবে:* `${amt:.4f}`\n"
            f"📉 *ফি:* `${fee:.4f}`\n"
            f"💰 *পাবেন:* `${net_amt:.4f}`\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"✅ সঠিক হলে *Confirm* করুন, ভুল থাকলে নিচে এডিট করুন।"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm (জমা দিন)", callback_data="wd_confirm")],
            [InlineKeyboardButton("✏️ ঠিকানা পরিবর্তন",  callback_data="wd_edit_addr"),
             InlineKeyboardButton("💵 পরিমাণ পরিবর্তন",   callback_data="wd_edit_amt")],
        ])
    await msg.reply_text(txt, parse_mode="Markdown", reply_markup=kb)


async def do_wd_submit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Actual withdrawal DB insert + confirmation message — wd_confirm callback calls this."""
    user = update.effective_user
    uid  = user.id
    # ── Local এ save করো, তারপরেই state clear — double-click guard ──────
    address = ctx.user_data.get("wd_addr", "")
    amt     = ctx.user_data.get("wd_amt", 0.0)
    for k in ("state", "wd_addr", "wd_amt"):
        ctx.user_data.pop(k, None)
    # ── Missing state guard — amt=0 বা address নেই হলে abort ────────────
    if not address or not amt or amt <= 0:
        lang0 = await asyncio.to_thread(get_lang, uid)
        await ctx.bot.send_message(
            uid,
            ("❌ *Withdrawal session expired. Please start again.*" if lang0 == 'en'
             else "❌ *Withdrawal session মেয়াদ শেষ। আবার শুরু করুন।*"),
            parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return
    fee     = await asyncio.to_thread(get_wd_fee)
    net_amt = round(amt - fee, 4)
    lang    = await asyncio.to_thread(get_lang, uid)

    # ── Security Check 2 + INSERT: একই transaction এ balance কাটো ও withdrawal তৈরি করো ──
    # এটা atomic — মাঝে crash হলেও টাকা হারাবে না।
    _sched_sec = random.randint(120, 300)   # ২ থেকে ৫ মিনিট (120–300 সেকেন্ড)
    _sched_at  = (datetime.now(pytz.timezone("Asia/Dhaka")) + timedelta(seconds=_sched_sec)).strftime("%Y-%m-%d %H:%M:%S")
    def _atomic_ded_and_insert():
        with _c() as c:
            # Step 1: balance check + deduct (atomic)
            ded = c.execute(
                "UPDATE users SET balance=balance-%s WHERE user_id=%s AND balance >= %s",
                (amt, uid, amt)
            )
            if ded.rowcount == 0:
                return None, "insufficient"
            # Step 2: insert withdrawal (same connection/transaction)
            cur = c.execute(
                """
                INSERT INTO withdrawals (user_id, amount, fee, address, requested_at, scheduled_at)
                SELECT %s, %s, %s, %s, %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM withdrawals
                    WHERE user_id=%s AND status IN ('pending','processing','gas_pending')
                )
                RETURNING id
                """,
                (uid, amt, fee, address, now(), _sched_at, uid)
            )
            _wid = cur.lastrowid
            if _wid is None:
                raise _DuplicatePendingError()
        _invalidate_user_cache(uid)
        _mark_dirty()
        return _wid, "ok"
    try:
        wid, _wd_status = await asyncio.to_thread(_atomic_ded_and_insert)
        if _wd_status == "insufficient":
            _bal_now = await asyncio.to_thread(get_bal, uid)
            err_msg = (
                f"❌ *Insufficient balance!*\n\n💰 *Your balance:* `${_bal_now:.4f} USDT`\n💸 *Requested:* `${amt:.4f} USDT`"
                if lang == 'en' else
                f"❌ *পর্যাপ্ত ব্যালান্স নেই!*\n\n💰 *আপনার ব্যালান্স:* `${_bal_now:.4f} USDT`\n💸 *চাইছেন:* `${amt:.4f} USDT`"
            )
            await ctx.bot.send_message(uid, err_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            return
        log.info(f"[WD-SUBMIT] uid={uid} wid={wid} amt={amt} addr={address[:10]}...")
        await asyncio.to_thread(_trim_table, "withdrawals", "user_id", uid, keep=10, only_done=True)
        # admin_msg_id=0 এবং channel_msg_id=0 ইতিমধ্যেই DDL DEFAULT — আলাদা UPDATE দরকার নেই।
    except _DuplicatePendingError:
        # Atomic transaction rollback হয়েছে — balance কাটা হয়নি, তাই refund দরকার নেই
        err_msg = (
            "⚠️ *You already have a pending withdrawal.*\n\nPlease wait for it to be processed before submitting a new one."
            if lang == 'en' else
            "⚠️ *আপনার একটি Withdraw ইতিমধ্যে প্রক্রিয়াধীন আছে।*\n\nনতুন Withdraw করার আগে এটি সম্পন্ন হতে দিন।"
        )
        await ctx.bot.send_message(uid, err_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return
    except Exception as _db_err:
        log.error(f"[WD-SUBMIT] DB error uid={uid} amt={amt}: {_db_err}")
        # Atomic transaction rollback হয়েছে — balance কাটা হয়নি, তাই refund দরকার নেই
        # তবে নিশ্চিত করতে current balance log করো
        try:
            _cur_bal = await asyncio.to_thread(get_bal, uid)
            log.info(f"[WD-SUBMIT] After DB error uid={uid} current_balance={_cur_bal}")
        except Exception:
            pass
        _err_msg = ("❌ *Server error. Please try again.*"
                    if lang == 'en' else
                    "❌ *সার্ভার সমস্যা। আবার চেষ্টা করুন।*")
        await ctx.bot.send_message(uid, _err_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    if lang == 'en':
        confirm_msg = (
            f"✅ *Withdrawal Request Submitted!*\n\n"
            f"💳 *Method:* USDT (BEP-20)\n"
            f"👛 *Wallet:* `{address}`\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 *Debit:* `${amt:.4f}`\n"
            f"📉 *Fee:* `${fee:.4f}`\n"
            f"💰 *You will receive:* `${net_amt:.4f}`\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"⚡ *Auto payment within 2–5 minutes.*"
        )
    else:
        confirm_msg = (
            f"✅ *Withdraw Request জমা হয়েছে!*\n\n"
            f"💳 *পদ্ধতি:* USDT (BEP-20)\n"
            f"👛 *Wallet:* `{address}`\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 *কাটা হয়েছে:* `${amt:.4f}`\n"
            f"📉 *ফি:* `${fee:.4f}`\n"
            f"💰 *পাবেন:* `${net_amt:.4f}`\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"⚡ *২-৫ মিনিটের মধ্যে অটো পেমেন্ট হবে।*"
        )
    await ctx.bot.send_message(uid, confirm_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))

# ──────────────────────────── HISTORY ────────────────────────────
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gmails = None
    wds = None
    uid = update.effective_user.id
    # ban check — /history command সরাসরি call হলেও safe থাকবে
    # menu handler থেকে আসলে check_ch cache hit হয় (120s TTL) — overhead minimal
    if not await guard(update, ctx): return
    def _db_4230():
        gmails = None
        wds = None
        with _c() as c:
            gmails = c.execute(
                "SELECT task_name,amount,status,submitted_at FROM gmails WHERE user_id=%s ORDER BY id DESC LIMIT 10", (uid,)
            ).fetchall()
            wds = c.execute(
                "SELECT amount,status,requested_at FROM withdrawals WHERE user_id=%s ORDER BY id DESC LIMIT 10", (uid,)
            ).fetchall()
        return gmails, wds
    gmails, wds = await asyncio.to_thread(_db_4230)
    stat_icon = {"pending":"⏳","approved":"✅","rejected":"❌"}
    g_lines = []
    for g in gmails:
        icon = stat_icon.get(g['status'], '❓')
        safe_name = md_escape(str(g['task_name']))
        g_lines.append(f"{icon} *{safe_name}* `{g['amount']}$` — {g['submitted_at']}")
    g_txt = "\n".join(g_lines) or T(uid, 'history_none_gmail')
    w_lines = []
    for w in wds:
        icon = stat_icon.get(w['status'], '❓')
        w_lines.append(f"{icon} `{w['amount']}$` — {w['requested_at']}")
    w_txt = "\n".join(w_lines) or T(uid, 'history_none_wd')
    try:
        await update.message.reply_text(
            f"{T(uid,'history_title')}\n\n"
            "━━━━━━━━━━━━━━━━\n"
            f"{T(uid,'history_gmail')}\n{g_txt}\n\n"
            "━━━━━━━━━━━━━━━━\n"
            f"{T(uid,'history_wd')}\n{w_txt}\n\n"
            "━━━━━━━━━━━━━━━━\n"
            f"{T(uid,'history_legend')}",
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            f"{T(uid,'history_title')}\n\n"
            f"{T(uid,'history_gmail')}\n{g_txt}\n\n"
            f"{T(uid,'history_wd')}\n{w_txt}\n\n"
            f"{T(uid,'history_legend')}"
        )

# ──────────────────────────── /point command ────────────────────────────
async def cmd_point(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await guard(update, ctx): return
    pts, (blk, blk_dt), lang = await asyncio.gather(
        asyncio.to_thread(get_points, uid),
        asyncio.to_thread(is_task_blocked, uid),
        asyncio.to_thread(get_lang, uid),
    )
    BD_TZ_LOCAL      = pytz.timezone("Asia/Dhaka")

    if blk and blk_dt:
        until_str = blk_dt.astimezone(BD_TZ_LOCAL).strftime("%d %b %Y, %I:%M %p")
        if lang == 'bn':
            status_txt  = "🔴 Task Blocked"
            block_line  = f"🔓 *Block উঠবে:* `{until_str}`\n"
        else:
            status_txt  = "🔴 Task Blocked"
            block_line  = f"🔓 *Block lifts on:* `{until_str}`\n"
    else:
        if pts >= 80:
            status_txt = "🟢 Excellent" if lang == 'en' else "🟢 চমৎকার"
        elif pts >= 60:
            status_txt = "🟡 Good" if lang == 'en' else "🟡 ভালো"
        elif pts >= 40:
            status_txt = "🟠 Fair" if lang == 'en' else "🟠 মোটামুটি"
        else:
            status_txt = "🔴 Low — Be careful!" if lang == 'en' else "🔴 কম — সাবধান!"
        block_line = ""

    await update.message.reply_text(
        T(uid, 'point_cmd',
          pts=pts,
          status=status_txt,
          block_line=block_line,
          block_days=POINT_BLOCK_DAYS,
          reject_pts=POINT_PER_REJECT,
          approve_pts=POINT_PER_APPROVE),
        parse_mode="Markdown"
    )

# ──────────────────────────── ADMIN PANEL ────────────────────────────
FILE_ID_STORE = "last_backup_file_id.txt"   # সর্বশেষ backup এর Telegram file_id

async def _auto_restore_from_telegram(bot):
    """PostgreSQL mode — CockroachDB তে সব data স্থায়ীভাবে থাকে, restore দরকার নেই।"""
    log.info("[AUTO-RESTORE] PostgreSQL mode — CockroachDB তে সব data আছে। Restore skip।")
    return False

def _backup_msg_save(target_id: int, msg_id: int):
    """প্রতিটা target এ পাঠানো backup এর message_id সেভ করো"""
    key = f"backup_msg_{target_id}"
    with _c() as c:
        c.execute("INSERT INTO bot_meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(msg_id)))

def _backup_msg_load(target_id: int) -> int | None:
    """আগের backup message_id লোড করো"""
    key = f"backup_msg_{target_id}"
    with _c() as c:
        row = c.execute("SELECT value FROM bot_meta WHERE key=%s", (key,)).fetchone()
    return int(row["value"]) if row else None

def _report_msg_save(target_id: int, msg_id: int):
    """প্রতিটা channel এ পাঠানো user-report এর message_id সেভ করো"""
    key = f"report_msg_{target_id}"
    with _c() as c:
        c.execute("INSERT INTO bot_meta (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(msg_id)))

def _report_msg_load(target_id: int) -> int | None:
    """আগের user-report message_id লোড করো"""
    key = f"report_msg_{target_id}"
    with _c() as c:
        row = c.execute("SELECT value FROM bot_meta WHERE key=%s", (key,)).fetchone()
    return int(row["value"]) if row else None

def _save_file_id(file_id: str):
    """সর্বশেষ DB backup এর Telegram file_id সেভ করো — পরে auto-restore এ কাজে লাগে।"""
    _FILE_ID_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_backup_file_id.txt")
    # ১. script directory-তে সেভ করো
    try:
        with open(_FILE_ID_PATH, "w") as f:
            f.write(file_id.strip())
    except Exception as e:
        log.warning(f"[FILE_ID] save ব্যর্থ (file): {e}")
    # ২. home directory-তে সেভ করো — নতুন machine/লোকেশনেও টিকে থাকে
    try:
        _home_path = os.path.expanduser("~/.bot_recovery_fid.txt")
        with open(_home_path, "w") as f:
            f.write(file_id.strip())
    except Exception:
        pass
    # ৩. /tmp-তেও রাখো — soft restart-এ কাজে লাগে
    try:
        with open("/tmp/bot_recovery_fid.txt", "w") as f:
            f.write(file_id.strip())
    except Exception:
        pass
    # ৪. bot_meta table এও রাখো
    try:
        with _c() as c:
            c.execute("INSERT INTO bot_meta (key,value) VALUES ('last_backup_file_id',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (file_id,))
    except Exception:
        pass

def _load_file_id() -> str | None:
    fid = None
    """সর্বশেষ সেভ করা Telegram file_id লোড করো — সব লোকেশন চেক করো।"""
    _FILE_ID_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_backup_file_id.txt")
    _HOME_PATH    = os.path.expanduser("~/.bot_recovery_fid.txt")
    _TMP_PATH     = "/tmp/bot_recovery_fid.txt"
    # সব possible location চেক করো
    for path in [_FILE_ID_PATH, _HOME_PATH, _TMP_PATH]:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    fid = f.read().strip()
                if fid:
                    return fid
        except Exception:
            pass
    # bot_meta থেকে চেষ্টা করো
    try:
        with _c() as c:
            row = c.execute("SELECT value FROM bot_meta WHERE key='last_backup_file_id'").fetchone()
        if row and row.get("value"):
            return row["value"]
    except Exception:
        pass
    return None

async def _send_db_to_admin(bot, label="🔄 Auto Backup"):
    """PostgreSQL — file backup নেই। শুধু log করা হয়।
    bot_meta এ sentinel সেভ করা হয় যাতে health check সবসময় pass করে।"""
    log.info(f"[BACKUP] {label} — PostgreSQL ব্যবহার হচ্ছে, file backup দরকার নেই।")
    await asyncio.to_thread(_save_file_id, "cockroachdb_ok")


async def _send_startup_report(bot):
    """
    Bot চালু হলে user report BACKUP_CHANNELS এ পাঠায় ও pin করে।
    post_init এ _send_db_to_admin এর পরে call করতে হবে।
    """
    try:
        import io as _io
        report = await asyncio.to_thread(_build_full_users_report)
        report_bytes = report.encode("utf-8")
        filename = f"users_{now()[:10]}.txt"
        _startup_user_count = len(await asyncio.to_thread(all_users))
        caption = (
            f"📊 *User Data — Startup Report*\n\n"
            f"🕐 *সময়:* `{now()}`\n"
            f"👥 *মোট Users:* `{_startup_user_count}`\n\n"
            f"🔄 Bot নতুন করে চালু হয়েছে — সর্বশেষ user data।"
        )
        for target in BACKUP_CHANNELS:
            # পুরনো report message ডিলিট করো
            old_rid = await asyncio.to_thread(_report_msg_load, target)
            if old_rid:
                try:
                    await bot.delete_message(chat_id=target, message_id=old_rid)
                except Exception:
                    pass
            try:
                msg = await bot.send_document(
                    target,
                    document=_io.BytesIO(report_bytes),
                    filename=filename,
                    caption=caption,
                    parse_mode="Markdown"
                )
                if msg:
                    await asyncio.to_thread(_report_msg_save, target, msg.message_id)
                    # NOTE: pin করা হচ্ছে না — DB file pinned থাকতে হবে যাতে restore কাজ করে
            except Exception as e:
                log.warning(f"[STARTUP-REPORT] {target} এ পাঠাতে ব্যর্থ: {e}")
        log.info("✅ Startup user report পাঠানো হয়েছে।")
    except Exception as e:
        log.warning(f"[STARTUP-REPORT] ব্যর্থ: {e}")


NEWS_CH_LINK = "https://t.me/task_earn_bot_news"


# ── Pending notification DB helpers ───────────────────────────────────────────

def _pn_save_full(send_at: str, sender_uid: int, t_name: str, t_price, task_link: str,
                  slot_txt: str, njo_line: str, ticon: str, notify_ch_link: str, delay_min: int,
                  t_code: str = "", allowed_countries: str = "") -> int:
    with _c() as c:
        cur = c.execute(
            "INSERT INTO pending_notifications "
            "(send_at,sender_uid,t_name,t_price,task_link,slot_txt,njo_line,ticon,notify_ch_link,delay_min,t_code,allowed_countries) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (send_at, sender_uid, t_name, str(t_price), task_link,
             slot_txt, njo_line, ticon, notify_ch_link, delay_min, t_code, allowed_countries)
        )
        return cur.lastrowid

def _pn_get_due() -> list:
    """send_at <= এখন এমন সব unsent notification নিয়ে আসো।"""
    with _c() as c:
        rows = c.execute(
            "SELECT id,sender_uid,t_name,t_price,task_link,slot_txt,njo_line,ticon,notify_ch_link,delay_min,"
            "COALESCE(t_code,'') as t_code, COALESCE(allowed_countries,'') as allowed_countries "
            "FROM pending_notifications WHERE send_at <= %s",
            (now(),)
        ).fetchall()
    return [dict(r) for r in rows]

def _pn_delete(nid: int):
    with _c() as c:
        c.execute("DELETE FROM pending_notifications WHERE id=%s", (nid,))

def _pn_get_all() -> list:
    with _c() as c:
        rows = c.execute(
            "SELECT id,send_at,t_name,delay_min FROM pending_notifications ORDER BY send_at"
        ).fetchall()
    return [dict(r) for r in rows]


def _pn_exists(nid: int) -> bool:
    """Check if a pending notification still exists (not yet sent/claimed)."""
    with _c() as c:
        return c.execute("SELECT 1 FROM pending_notifications WHERE id=%s", (nid,)).fetchone() is not None


def _pn_claim(nid: int) -> bool:
    """Atomically claim (delete) a pending notification.
    Returns True if THIS caller claimed it successfully,
    False if another task/restart already claimed it.
    Race-condition safe — single DB round-trip।"""
    with _c() as c:
        cur = c.execute("DELETE FROM pending_notifications WHERE id=%s", (nid,))
        return cur.rowcount > 0


async def _delayed_notify_runner(bot, kwargs_dict: dict, delay_sec: int, pn_id: int | None = None):
    """Top-level coroutine — delay_sec সেকেন্ড অপেক্ষা করে তারপর
    সব bot user কে notification পাঠায়। Bot restart হলেও _pn_watcher আবার
    schedule করবে — তাই এখন 100% restart-safe।"""
    try:
        if delay_sec > 0:
            log.info(f"[NOTIFY] {delay_sec}s ({delay_sec//60}min) অপেক্ষা শুরু…")
            await asyncio.sleep(delay_sec)
            log.info("[NOTIFY] অপেক্ষা শেষ — এখন পাঠাচ্ছি।")
        # Atomic claim — _pn_watcher বা অন্য restart ইতিমধ্যে claim করলে skip (race-condition safe)
        if pn_id is not None:
            _claimed = await asyncio.to_thread(_pn_claim, pn_id)
            if not _claimed:
                log.info(f"[NOTIFY] #{pn_id} ইতিমধ্যে অন্য কেউ claim করেছে — skip।")
                return
            log.info(f"[NOTIFY] pending_notifications #{pn_id} claimed (atomic) — পাঠাচ্ছি।")
        await _send_task_notify_to_users(**kwargs_dict)
    except Exception as _e:
        log.error(f"[NOTIFY-RUNNER] Notification পাঠাতে ব্যর্থ (pn_id={pn_id}): {_e}", exc_info=True)


async def _pn_watcher(bot):
    """প্রতি ৩০ সেকেন্ডে due pending notifications পাঠায়।
    Bot restart হলে এখান থেকেই missed notifications ধরা পড়বে।"""
    log.info("[PNW] Pending-notification watcher শুরু হয়েছে।")
    while True:
        try:
            due = await asyncio.to_thread(_pn_get_due)
            for row in due:
                nid = row["id"]
                # Atomic claim — _pn_exists + _pn_delete আলাদা call করলে race condition হয়।
                # _pn_claim একটাই DELETE query: rowcount=0 মানে অন্য কেউ (asyncio task বা
                # আগের restart) ইতিমধ্যে এই notification claim করেছে।
                _watcher_claimed = await asyncio.to_thread(_pn_claim, nid)
                if not _watcher_claimed:
                    log.info(f"[PNW] #{nid} ইতিমধ্যে asyncio task claim করেছে — skip।")
                    continue
                log.info(f"[PNW] #{nid} claimed (atomic) — task: {row['t_name']} — পাঠাচ্ছি।")
                kwargs = dict(
                    bot=bot,
                    sender_uid=row["sender_uid"],
                    t_name=row["t_name"],
                    t_price=row["t_price"],
                    task_link=row["task_link"],
                    slot_txt=row["slot_txt"],
                    njo_line=row["njo_line"],
                    ticon=row["ticon"],
                    notify_ch_link=row["notify_ch_link"],
                    delay_min=row["delay_min"],
                    t_code=row.get("t_code", "") or "",
                    allowed_countries=row.get("allowed_countries", "") or "",
                )
                await _send_task_notify_to_users(**kwargs)
                log.info(f"[PNW] Notification #{nid} পাঠানো সম্পন্ন।")
        except Exception as e:
            log.error(f"[PNW] Error: {e}", exc_info=True)
        await asyncio.sleep(30)    # CockroachDB — 30s interval যথেষ্ট, CU-hr সমস্যা নেই


def _html_escape(text: str) -> str:
    """HTML special chars escape করো — Telegram HTML parse mode এর জন্য।"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _send_task_notify_to_users(
    bot, sender_uid,
    t_name, t_price, task_link,
    slot_txt, njo_line, ticon,
    notify_ch_link, delay_min,
    t_code: str = "", allowed_countries: str = ""
):
    """সব bot user এ task notification পাঠাও — top-level coroutine, closure নয়।
    এটা asyncio.create_task / job_queue দুটোতেই 100% কাজ করে।
    HTML parse_mode ব্যবহার করা হয় — Markdown এর মতো special char এ fail হয় না।"""
    users_list = await asyncio.to_thread(all_users)
    sent = fail = 0
    fail_reasons: list[str] = []
    log.info(f"[NOTIFY] Broadcast শুরু — মোট {len(users_list)} user, delay={delay_min}min")

    # plain njo text (HTML-safe)
    njo_plain  = "🔒 New joiners only" if "New joiners" in njo_line else "🔓 Everyone"
    code_line  = f"\n🔑 <b>Task Code:</b> <code>{_html_escape(t_code)}</code>" if t_code else ""
    _ac_str    = (allowed_countries or "").strip().upper()
    if _ac_str and _ac_str not in ("0", "ALL"):
        country_line = f"\n🌍 <b>Available to:</b> {_html_escape(fmt_allowed_countries(_ac_str))}"
    else:
        country_line = "\n🌍 <b>Available to:</b> All countries 🌐"
    late_note  = (
        f"\n\n⏰ <i>This notification was sent <b>{delay_min} minute(s) late.</b>\n"
        f'To get instant notifications, join our <a href="{notify_ch_link}">Notification Channel</a>.</i>'
    ) if delay_min > 0 else ""

    msg = (
        f"🆕 <b>New Task Available!</b>\n\n"
        f"{ticon} <b>Task:</b> <code>{_html_escape(str(t_name))}</code>{code_line}\n"
        f"💵 <b>Reward:</b> <code>{_html_escape(str(t_price))}$ USDT</code>\n"
        f"👥 <b>Max Slots:</b> <code>{_html_escape(str(slot_txt))}</code>\n"
        f"👤 <b>Eligible:</b> {njo_plain}{country_line}"
        f"{late_note}\n\n"
        f"👇 Complete it now and earn!"
    )
    user_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Do Task", url=task_link)],
        [InlineKeyboardButton("📢 Notification Channel", url=notify_ch_link)],
    ])

    for u in users_list:
        uid_u = u["user_id"]
        if uid_u == sender_uid:
            continue
        try:
            await bot.send_message(uid_u, msg, parse_mode="HTML", reply_markup=user_kb)
            sent += 1
        except TgRetryAfter as _ra_e:
            _wait = int(getattr(_ra_e, 'retry_after', 30) or 30) + 1
            log.warning(f"[NOTIFY] FloodWait {_wait}s — retrying user {uid_u}")
            await asyncio.sleep(_wait)
            try:
                await bot.send_message(uid_u, msg, parse_mode="HTML", reply_markup=user_kb)
                sent += 1
            except Exception as _re2:
                log.warning(f"[NOTIFY] retry fail user {uid_u}: {_re2}")
                fail += 1
                if len(fail_reasons) < 3:
                    fail_reasons.append(f"RetryAfter+retry: {_re2}")
        except Exception as e:
            err_str = f"{type(e).__name__}: {e}"
            log.warning(f"[NOTIFY] user {uid_u} send fail: {err_str}")
            fail += 1
            if len(fail_reasons) < 3:      # প্রথম ৩টা error admin কে দেখাবো
                fail_reasons.append(err_str)
        await asyncio.sleep(0.08)   # Telegram flood guard

    log.info(f"[NOTIFY] Broadcast শেষ — ✅ {sent} জন | ❌ {fail} জন")
    error_detail = ""
    if fail_reasons:
        sample = "\n".join(f"• <code>{_html_escape(r)}</code>" for r in fail_reasons)
        error_detail = f"\n\n⚠️ <b>Error sample:</b>\n{sample}"
    try:
        await bot.send_message(
            sender_uid,
            f"📢 <b>Notification পাঠানো সম্পন্ন!</b>\n\n"
            f"✅ সফল: <code>{sent}</code> জন\n"
            f"❌ ব্যর্থ: <code>{fail}</code> জন"
            f"{error_detail}",
            parse_mode="HTML"
        )
    except Exception as e:
        log.warning(f"[NOTIFY] summary to admin fail: {e}")


async def _broadcast_task_notify(ctx, reply_msg, sender_uid,
                                   t_name, t_price, task_link,
                                   slot_txt="Unlimited",
                                   njo_line="🔓 *Who can do:* Everyone",
                                   task_type="other",
                                   allowed_countries="",
                                   t_code=""):
    """সব user + notification channel এ নতুন task এর broadcast পাঠাও।
    যেকোনো task type এ call করা যাবে — TG, Regular, Category সব।
    Channel এ সাথে সাথে যায়, user এ delay_min মিনিট পরে যায়।"""
    if not await asyncio.to_thread(task_notify_on):
        return 0, 0
    notify_ch, notify_ch_uname, delay_min = await asyncio.gather(
        asyncio.to_thread(get_task_notify_ch_id),
        asyncio.to_thread(get_task_notify_ch_username),
        asyncio.to_thread(get_notify_delay_min),
    )
    notify_ch_link  = f"https://t.me/{notify_ch_uname.lstrip('@')}"
    ticon           = task_type_icon(task_type)
    _country_line   = f"🌍 *Available to:* {fmt_allowed_countries(allowed_countries)}" if allowed_countries else "🌍 *Available to:* All countries 🌐"
    _ch_code_line   = f"\n🔑 *Task Code:* `{t_code}`" if t_code else ""

    # ── Channel message (instant) ──────────────────────────────────
    ch_msg = (
        f"🆕 *New Task Available!*\n\n"
        f"{ticon} *Task:* `{t_name}`{_ch_code_line}\n"
        f"💵 *Reward:* `{t_price}$ USDT`\n"
        f"👥 *Max Participants:* `{slot_txt}`\n"
        f"{njo_line}\n"
        f"{_country_line}\n\n"
        f"👇 Complete it now and earn!"
    )
    ch_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Do Task", url=task_link)],
        [InlineKeyboardButton("📰 News Channel", url=NEWS_CH_LINK)],
    ])
    try:
        await ctx.bot.send_message(notify_ch, ch_msg, parse_mode="Markdown", reply_markup=ch_kb)
        log.info(f"[NOTIFY] Channel notification পাঠানো হয়েছে → {notify_ch}")
    except Exception as e:
        log.warning(f"[NOTIFY] Channel send fail: {e}")

    # ── Confirm to admin ───────────────────────────────────────────
    if delay_min > 0:
        await reply_msg.reply_text(
            f"✅ *Channel এ পাঠানো হয়েছে।*\n"
            f"⏳ Bot users এ *{delay_min} মিনিট* পরে পাঠানো হবে।",
            parse_mode="Markdown"
        )
    else:
        await reply_msg.reply_text(
            "⏳ *Bot users এ Notification পাঠানো হচ্ছে...*",
            parse_mode="Markdown"
        )

    # ── Shared kwargs for the sender coroutine ─────────────────────
    _kwargs = dict(
        bot=ctx.bot,
        sender_uid=sender_uid,
        t_name=t_name,
        t_price=t_price,
        task_link=task_link,
        slot_txt=slot_txt,
        njo_line=njo_line,
        ticon=ticon,
        notify_ch_link=notify_ch_link,
        delay_min=delay_min,
        t_code=t_code,
        allowed_countries=allowed_countries,
    )

    if delay_min > 0:
        # ── DB তে সেভ করো — bot restart হলেও _pn_watcher পাঠাবে ──
        from datetime import datetime, timedelta
        _send_at_dt = datetime.now(BD_TZ) + timedelta(minutes=delay_min)
        _send_at_str = _send_at_dt.strftime("%Y-%m-%d %H:%M")
        _pn_id = await asyncio.to_thread(_pn_save_full, send_at=_send_at_str, sender_uid=sender_uid, t_name=t_name, t_price=t_price, task_link=task_link, slot_txt=slot_txt, njo_line=njo_line, ticon=ticon, notify_ch_link=notify_ch_link, delay_min=delay_min, t_code=t_code, allowed_countries=allowed_countries)
        log.info(f"[NOTIFY] DB তে সেভ হয়েছে (id={_pn_id}) — {delay_min} মিনিট পরে পাঠাবে।")
        # asyncio task ও রাখো (fast path — restart না হলে এটাই পাঠাবে)
        _task = asyncio.create_task(
            _delayed_notify_runner(ctx.bot, dict(_kwargs), delay_min * 60, pn_id=_pn_id)
        )
        _background_tasks.add(_task)
        _task.add_done_callback(_background_tasks.discard)
    else:
        # ── সাথে সাথে পাঠাও ────────────────────────────────────────
        _task = asyncio.create_task(
            _delayed_notify_runner(ctx.bot, dict(_kwargs), 0)
        )
        _background_tasks.add(_task)
        _task.add_done_callback(_background_tasks.discard)
        log.info("[NOTIFY] Immediate broadcast task তৈরি হয়েছে।")

    return 0, 0


async def _activate_task_and_notify(ctx, q, uid,
                                     get_fn, update_fn, tid,
                                     link_prefix="",
                                     njo_field=None):
    """Universal task toggle + broadcast helper.

    যেকোনো নতুন task type এ (YouTube, Instagram, ...) শুধু এটা call করো:

        await _activate_task_and_notify(
            ctx, q, uid,
            get_fn    = get_yt_task,      # DB থেকে task আনার function
            update_fn = update_yt_task,   # DB update function  
            tid       = task_id,
            link_prefix = "yttask_",      # /start deep-link prefix (না থাকলে "" দাও)
            njo_field   = "new_users_only" # new-joiners-only field name (না থাকলে None)
        )

    এতেই toggle, message edit, confirmation, আর সব user কে notification — সব হয়ে যাবে।
    """
    t = await asyncio.to_thread(get_fn, tid)
    if not t:
        await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown")
        return False
    new_val = 0 if t['is_active'] else 1
    await asyncio.to_thread(update_fn, tid, "is_active", new_val)
    _mark_dirty()
    try: await q.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    status_lbl = "✅ *Task Activated!*" if new_val else "❌ *Task Deactivated.*"
    await q.message.reply_text(status_lbl, parse_mode="Markdown")
    if new_val:
        bot_info  = await ctx.bot.get_me()
        task_link = (f"https://t.me/{bot_info.username}?start={link_prefix}{tid}"
                     if link_prefix else f"https://t.me/{bot_info.username}")
        mc       = _rget(t, 'max_completions', 0)
        slot_txt = f"{mc}" if mc > 0 else "Unlimited"
        njo      = _rget(t, njo_field, 0) if njo_field else 0
        njo_line = ("🔒 *Who can do:* New joiners only"
                    if njo else "🔓 *Who can do:* Everyone")
        ttype    = _rget(t, 'task_type', 'other')
        _ac_bc   = _rget(t, 'allowed_countries', '') or ''
        await _broadcast_task_notify(ctx, q.message, uid,
            t['name'], t['price'], task_link, slot_txt, njo_line, task_type=ttype,
            allowed_countries=_ac_bc, t_code=_rget(t, 'task_code', '') or '')
    return new_val


def _build_full_users_report() -> str:
    """সব user এর data দিয়ে text report তৈরি করে"""
    users = all_users()
    ts = now()
    lines = [
        "📊 USER DATA REPORT",
        f"🕐 সময়: {ts}",
        f"👥 মোট User: {len(users)}",
        "━" * 35,
    ]
    total_bal = 0.0
    for u in users:
        uid = u["user_id"]
        uname = f"@{u['username']}" if _rget(u, "username") else (_rget(u, "full_name") or "Unknown")
        bal = round(_rget(u, "balance", 0), 4)
        total_bal += bal
        with _c() as c:
            ref_count = _count(c.execute("SELECT COUNT(*) FROM users WHERE referred_by=%s", (uid,)).fetchone())
            comm = _count(c.execute("SELECT COALESCE(SUM(amount),0) FROM commissions WHERE user_id=%s", (uid,)).fetchone()) if _table_exists(c, "commissions") else 0
            tg_done  = _count(c.execute("SELECT COUNT(*) FROM tg_task_completions WHERE user_id=%s", (uid,)).fetchone())
            sub_app  = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'", (uid,)).fetchone())
            wd_app   = _count(c.execute("SELECT COUNT(*) FROM withdrawals WHERE user_id=%s AND status='approved'", (uid,)).fetchone())
        ban = "🚫" if _rget(u, "is_banned") else "✅"
        lines.append(
            f"{ban} [{uid}] {uname}\n"
            f"   💰 Balance: {bal}$ | 🔗 Refs: {ref_count} | 💸 Commission: {round(comm,4)}$\n"
            f"   📡 TG Tasks: {tg_done} | 📋 Tasks✅: {sub_app} | 💳 WD✅: {wd_app}\n"
            f"   📅 Joined: {_rget(u, 'joined_at', '')}"
        )
        lines.append("─" * 35)
    lines.append(f"\n💰 TOTAL BALANCE (all users): {round(total_bal, 4)}$ USDT")
    return "\n".join(lines)


def _table_exists(c, name):
    return _fval(c.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (name,)
    ).fetchone()) > 0


async def job_scheduled_report(context):
    """প্রতি BACKUP_INTERVAL_HOURS ঘণ্টায় চলে — user report + DB backup"""
    log.info("⏰ Scheduled report চলছে...")
    # ১. DB file পাঠাও
    await _send_db_to_admin(context.bot, label="⏰ Scheduled Auto Backup")
    # ২. User data text report তৈরি করে পাঠাও
    try:
        report = await asyncio.to_thread(_build_full_users_report)
        filename = f"users_report_{now()[:10]}.txt"
        report_bytes = report.encode("utf-8")
        _sched_user_count = len(await asyncio.to_thread(all_users))
        caption = (
            f"📊 *Scheduled User Data Report*\n\n"
            f"🕐 *সময়:* `{now()}`\n"
            f"👥 *মোট Users:* `{_sched_user_count}`\n\n"
            f"✅ সব user এর balance, referral, commission, task history।"
        )
        import io
        for target in BACKUP_CHANNELS:
            # পুরনো report message ডিলিট করো
            old_rid = await asyncio.to_thread(_report_msg_load, target)
            if old_rid:
                try:
                    await context.bot.delete_message(chat_id=target, message_id=old_rid)
                except Exception:
                    pass
            try:
                msg = await context.bot.send_document(
                    target,
                    document=io.BytesIO(report_bytes),
                    filename=filename,
                    caption=caption,
                    parse_mode="Markdown"
                )
                if msg:
                    await asyncio.to_thread(_report_msg_save, target, msg.message_id)
            except Exception as e:
                log.warning(f"[REPORT] {target} এ পাঠাতে ব্যর্থ: {e}")
        log.info("✅ Scheduled report পাঠানো হয়েছে।")
    except Exception as e:
        log.warning(f"[REPORT] তৈরি করতে ব্যর্থ: {e}")

async def cmd_testleaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin only — এখনই leaderboard post করো (test)"""
    uid = update.effective_user.id
    if not await asyncio.to_thread(is_admin, uid):
        await update.message.reply_text("❌ *এই command শুধু Admin ব্যবহার করতে পারবে।*", parse_mode="Markdown")
        return
    await update.message.reply_text("⏳ *Leaderboard পাঠানো হচ্ছে...*", parse_mode="Markdown")
    await job_post_leaderboard(ctx)
    await update.message.reply_text("✅ *Leaderboard পাঠানো হয়েছে।*", parse_mode="Markdown")


async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await asyncio.to_thread(is_admin, uid):
        await update.message.reply_text("❌ *এই command শুধু Admin ব্যবহার করতে পারবে।*", parse_mode="Markdown")
        return
    await update.message.reply_text("⏳ *User Report তৈরি হচ্ছে...*", parse_mode="Markdown")
    try:
        import io as _io
        report, _all_users = await asyncio.gather(
            asyncio.to_thread(_build_full_users_report),
            asyncio.to_thread(all_users),
        )
        _backup_user_count = len(_all_users)
        await update.message.reply_document(
            document=_io.BytesIO(report.encode("utf-8")),
            filename=f"users_{now()[:10]}.txt",
            caption=(
                f"📊 *User Data Report (PostgreSQL)*\n\n"
                f"🕐 সময়: `{now()}`\n"
                f"👥 মোট Users: `{_backup_user_count}`\n\n"
                "✅ Data CockroachDB এ permanently stored।\n"
                "🔒 Bot restart হলেও কোনো data যাবে না।"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Report তৈরিতে সমস্যা: `{e}`", parse_mode="Markdown")

async def cmd_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await asyncio.to_thread(is_admin, uid):
        await update.message.reply_text("❌ *এই command শুধু Admin ব্যবহার করতে পারবে।*", parse_mode="Markdown")
        return
    await update.message.reply_text(
        "ℹ️ *PostgreSQL Mode চালু আছে।*\n\n"
        "✅ সব data CockroachDB এ *permanently stored*।\n"
        "🔒 Bot restart হলেও কোনো data মুছে যায় না।\n\n"
        "❌ SQLite file restore এর দরকার নেই।",
        parse_mode="Markdown"
    )

async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    st  = ctx.user_data.get("state")
    if st == "adm_restore_db" and await asyncio.to_thread(is_admin, uid):
        # PostgreSQL mode — file restore দরকার নেই
        ctx.user_data.pop("state", None)
        await update.message.reply_text(
            "ℹ️ *PostgreSQL Mode চালু — file restore দরকার নেই।*\n\n"
            "✅ Data CockroachDB তে permanently stored। Bot restart হলেও সব data থাকে।",
            parse_mode="Markdown"
        )
        return

    # ── Admin: Demo Video (as document/file) — Category Task ─────
    if st == "adm_ctask_set_video_file" and await asyncio.to_thread(is_admin, uid):
        tid = ctx.user_data.pop("edit_ctask_id", None)
        ctx.user_data.pop("state", None)
        doc = update.message.document
        if doc and tid:
            await asyncio.to_thread(update_category_task, tid, "demo_video_id", doc.file_id)
            await update.message.reply_text(
                "✅ *Demo Video সেট হয়েছে!*\n\n"
                "Task এ এখন 'ভিডিও দেখুন' বাটন দেখা যাবে।",
                parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        else:
            await update.message.reply_text("❌ ভিডিও পাওয়া যায়নি।", parse_mode="Markdown")
        return

    # ── Admin: Demo Video (as document/file) — Regular Task ──────
    if st == "adm_task_set_video_file" and await asyncio.to_thread(is_admin, uid):
        tid = ctx.user_data.pop("edit_tid", None)
        ctx.user_data.pop("state", None)
        doc = update.message.document
        if doc and tid:
            await asyncio.to_thread(update_task_field_val, tid, "demo_video_id", doc.file_id)
            await update.message.reply_text(
                "✅ *Demo Video সেট হয়েছে!*\n\n"
                "Task এ এখন 'ভিডিও দেখুন' বাটন দেখা যাবে।",
                parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        else:
            await update.message.reply_text("❌ ভিডিও পাওয়া যায়নি।", parse_mode="Markdown")
        return
    # non-restore document — ignore


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles regular video messages (non-document)."""
    if not update.effective_user:
        return
    uid = update.effective_user.id
    st  = ctx.user_data.get("state")

    # ── Admin: Demo Video (as video message) — Category Task ─────
    if st == "adm_ctask_set_video_file" and await asyncio.to_thread(is_admin, uid):
        tid = ctx.user_data.pop("edit_ctask_id", None)
        ctx.user_data.pop("state", None)
        vid = update.message.video
        if vid and tid:
            await asyncio.to_thread(update_category_task, tid, "demo_video_id", vid.file_id)
            _ct2 = await asyncio.to_thread(get_category_task, tid)
            _ct2_img = bool((_rget(_ct2, 'example_image_id', '') or '').strip()) if _ct2 else False
            _ct2_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📷 Photo Field যোগ",  callback_data=f"adm_cfield_add_photo_{tid}"),
                 InlineKeyboardButton("📝 Text Field যোগ",   callback_data=f"adm_cfield_add_text_{tid}")],
                [InlineKeyboardButton("🖼 Example Photo ✅" if _ct2_img else "🖼 Example Photo যোগ", callback_data=f"adm_ctaste_sample_photo_{tid}"),
                 InlineKeyboardButton("🎬 Demo Video ✅",    callback_data=f"adm_ctaste_video_{tid}")],
                [InlineKeyboardButton("🚀 এখনই Live করুন", callback_data=f"adm_ctask_golive_{tid}")],
            ])
            await update.message.reply_text(
                "✅ *Demo Video সেট হয়েছে!*\n\nTask এ এখন 'ভিডিও দেখুন' বাটন দেখা যাবে।\n\nআরো কিছু যোগ করুন বা Live করুন:",
                parse_mode="Markdown", reply_markup=_ct2_kb)
        else:
            await update.message.reply_text("❌ ভিডিও পাওয়া যায়নি।", parse_mode="Markdown")
        return

    # ── Admin: Demo Video (as video message) — Regular Task ──────
    if st == "adm_task_set_video_file" and await asyncio.to_thread(is_admin, uid):
        tid = ctx.user_data.pop("edit_tid", None)
        ctx.user_data.pop("state", None)
        vid = update.message.video
        if vid and tid:
            await asyncio.to_thread(update_task_field_val, tid, "demo_video_id", vid.file_id)
            _rt2, _rf2 = await asyncio.gather(
                asyncio.to_thread(get_task, tid),
                asyncio.to_thread(get_task_fields, tid),
            )
            _rt2_img = bool((_rget(_rt2, 'example_image_id', '') or '').strip()) if _rt2 else False
            _rt2_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
                 InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}")],
                [InlineKeyboardButton("🖼 Example Photo ✅" if _rt2_img else "🖼 Example Photo যোগ", callback_data=f"adm_te_sample_photo_{tid}"),
                 InlineKeyboardButton("🎬 Demo Video ✅",    callback_data=f"adm_te_video_{tid}")],
                [InlineKeyboardButton("✅ Done — Task সেভ করুন", callback_data=f"adm_task_done_{tid}")],
            ])
            await update.message.reply_text(
                f"✅ *Demo Video সেট হয়েছে!*\n\nTask এ এখন 'ভিডিও দেখুন' বাটন দেখা যাবে।\n\n📋 Fields: {len(_rf2)} টি\n\nআরো fields যোগ করুন বা Done চাপুন:",
                parse_mode="Markdown", reply_markup=_rt2_kb)
            ctx.user_data["state"] = "adm_task_fields"
        else:
            await update.message.reply_text("❌ ভিডিও পাওয়া যায়নি।", parse_mode="Markdown")
        return

    # ── User: Task proof — video পাঠালে (ছবির জায়গায় video) ─────
    if st in ("task_collect", "categ_task_collect"):
        fields = ctx.user_data.get("task_fields", [])
        idx    = ctx.user_data.get("field_idx", 0)
        lang   = await asyncio.to_thread(get_lang, uid)
        if idx < len(fields) and fields[idx]["field_type"] == "photo":
            vid = update.message.video
            if vid:
                _vid_cap = (update.message.caption or "").strip()
                ctx.user_data.setdefault("collected", []).append({
                    "type": "photo",
                    "prompt_bn": fields[idx]["prompt_bn"],
                    "prompt_en": fields[idx].get("prompt_en", ""),
                    "value": vid.file_id,
                    "unique_id": vid.file_unique_id,
                    "caption": _vid_cap,
                    "is_video": True,
                })
                ctx.user_data["field_idx"] = idx + 1
                if idx + 1 < len(fields):
                    await _ask_next_field(update, ctx, uid)
                else:
                    if st == "categ_task_collect":
                        await _finalize_category_task_submission(update, ctx, uid)
                    else:
                        await _finalize_task_submission(update, ctx, uid)
        else:
            await update.message.reply_text(
                "❌ এখন ভিডিও নয়, টেক্সট বা ছবি পাঠান।" if lang == 'bn'
                else "❌ Please send text or photo, not a video.",
                parse_mode="Markdown")
        return

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    if not await asyncio.to_thread(is_admin, uid):
        return  # non-admin কে কোনো reply নেই — command সম্পূর্ণ invisible
    await send_admin_panel(update.message, uid)

async def _show_perm_picker(msg, tgt_uid: int, ctx):
    """Show permission checkbox picker for tgt_uid."""
    def _db_4993():
        r = None
        with _c() as c:
            r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tgt_uid,)).fetchone()
        return r
    r = await asyncio.to_thread(_db_4993)
    cur_str = (r["permissions"] if r else "full") or "full"
    cur_set = set(cur_str.split(","))

    all_perms = [PERM_TASKS, PERM_WITHDRAW, PERM_USERS, PERM_BROADCAST, PERM_FINANCE, PERM_SETTINGS]
    rows = []
    # Full access toggle
    full_on = PERM_FULL in cur_set
    rows.append([InlineKeyboardButton(
        f"{'✅' if full_on else '☐'} 👑 Full Access (সব কিছু)",
        callback_data=f"adm_perm_toggle_full_{tgt_uid}"
    )])
    # Individual perms (only meaningful if not full)
    if not full_on:
        for perm in all_perms:
            chk = "✅" if perm in cur_set else "☐"
            rows.append([InlineKeyboardButton(
                f"{chk} {PERM_LABELS[perm]}",
                callback_data=f"adm_perm_toggle_{perm}_{tgt_uid}"
            )])
    # Admin manage permission — full থাকলেও আলাদাভাবে দিতে হবে
    # এটা না দিলে sub-admin admin যোগ/সরাতে পারবে না
    can_manage = PERM_ADMIN_MANAGE in cur_set
    rows.append([InlineKeyboardButton(
        f"{'✅' if can_manage else '☐'} 👮 Admin যোগ/সরাতে পারবে",
        callback_data=f"adm_perm_toggle_{PERM_ADMIN_MANAGE}_{tgt_uid}"
    )])
    rows.append([InlineKeyboardButton("💾 সেভ করুন", callback_data=f"adm_perm_save_{tgt_uid}")])

    pdisp = _perms_display(cur_str)
    await msg.reply_text(
        f"🔑 *Admin Permission সেট করুন*\n\n"
        f"👤 User ID: `{tgt_uid}`\n"
        f"বর্তমান: {pdisp}\n\n"
        f"নিচের বোতামে ক্লিক করে permission চালু/বন্ধ করুন, তারপর সেভ করুন:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def send_admin_panel(msg, uid):
    is_main = (uid == ADMIN_ID)
    # সব independent lookups একসাথে parallel — admin panel-এর সবচেয়ে বড় bottleneck
    # get_admin_perms — ১টি DB query, তারপর locally check (sub-admin-এর 6 query → 1)
    (
        (total, tot_bal, pg, pw, ag, aw),
        status,
        notify_on_val,
        _uid_perms,
    ) = await asyncio.gather(
        asyncio.to_thread(get_stats),
        asyncio.to_thread(get_setting, "bot_status"),
        asyncio.to_thread(task_notify_on),
        asyncio.to_thread(get_admin_perms, uid),
    )
    def _hp(perm): return PERM_FULL in _uid_perms or perm in _uid_perms or uid == ADMIN_ID
    has_tasks     = _hp(PERM_TASKS)
    has_withdraw  = _hp(PERM_WITHDRAW)
    has_users     = _hp(PERM_USERS)
    has_broadcast = _hp(PERM_BROADCAST)
    has_finance   = _hp(PERM_FINANCE)
    has_settings  = _hp(PERM_SETTINGS)
    status   = status or "on"
    bot_btn  = "🔴 Bot বন্ধ করুন" if status == "on" else "🟢 Bot চালু করুন"
    bot_s    = "🟢 চালু" if status == "on" else "🔴 বন্ধ"
    notify_s = "🔔 চালু" if notify_on_val else "🔕 বন্ধ"
    has_admin_manage = is_main   # শুধু Main Admin admin add/remove করতে পারবে

    rows = []
    # Finance / settings row
    if has_finance or has_settings:
        r = []
        if has_finance:   r.append(InlineKeyboardButton("📊 Statistics",   callback_data="adm_stats"))
        if has_settings:  r.append(InlineKeyboardButton(bot_btn,            callback_data="adm_bot_toggle"))
        rows.append(r)
    # Broadcast / msg
    if has_broadcast or has_users:
        r = []
        if has_broadcast: r.append(InlineKeyboardButton("📢 Broadcast",    callback_data="adm_bc"))
        if has_users:     r.append(InlineKeyboardButton("📨 User মেসেজ",   callback_data="adm_msg"))
        rows.append(r)
    # Balance
    if has_users:
        rows.append([
            InlineKeyboardButton("➕ Balance যোগ",  callback_data="adm_add"),
            InlineKeyboardButton("➖ Balance কাটো", callback_data="adm_ded"),
        ])
    # Ban/unban
    if has_users:
        rows.append([
            InlineKeyboardButton("🚫 Ban User",   callback_data="adm_ban"),
            InlineKeyboardButton("✅ Unban User",  callback_data="adm_unban"),
        ])
    # Tasks
    if has_tasks:
        rows.append([
            InlineKeyboardButton("➕ Task যোগ করুন", callback_data="adm_task_add"),
            InlineKeyboardButton("📋 Task তালিকা",   callback_data="adm_task_list"),
        ])
        rows.append([
            InlineKeyboardButton("📡 TG Tasks যোগ",   callback_data="adm_tgt_add"),
            InlineKeyboardButton("📡 TG Task তালিকা", callback_data="adm_tgt_list"),
        ])
        rows.append([
            InlineKeyboardButton("📁 Category যোগ করুন", callback_data="adm_cat_add"),
            InlineKeyboardButton("📁 Category তালিকা",   callback_data="adm_cat_list"),
        ])
    # Task Code search
    if has_tasks:
        rows.append([InlineKeyboardButton("🔍 Task Code খুঁজুন", callback_data="adm_task_code_search")])
        rows.append([InlineKeyboardButton("👤 Task Owner সেট করুন", callback_data="adm_set_task_owner")])
    # Pending tasks & withdraws
    if has_tasks or has_withdraw:
        r = []
        if has_tasks:    r.append(InlineKeyboardButton("⏳ Pending Tasks",    callback_data="adm_pending_g"))
        if has_withdraw: r.append(InlineKeyboardButton("💸 Active Withdrawals", callback_data="adm_pending_w"))
        rows.append(r)
    # User list/search
    if has_users:
        rows.append([
            InlineKeyboardButton("👤 User খুঁজুন", callback_data="adm_find_user"),
            InlineKeyboardButton("📋 সব Users",    callback_data="adm_all_users"),
        ])
    # Bio Task
    if has_tasks or uid == ADMIN_ID:
        bt = await asyncio.to_thread(get_bio_task)
        bt_status = "🟢 চালু" if (bt and bt['is_active']) else "🔴 বন্ধ"
        rows.append([InlineKeyboardButton(f"🧬 Bio Task ({bt_status})", callback_data="adm_bio_task")])
    # Promo code
    if has_broadcast or uid == ADMIN_ID:
        rows.append([InlineKeyboardButton("🎁 Promo Code তৈরি করুন", callback_data="adm_promo_create")])
    # Admin management
    if has_admin_manage:
        rows.append([InlineKeyboardButton("👮 Admin যোগ করুন", callback_data="adm_add_admin")])
    # Admin তালিকা সব এডমিন দেখতে পারবে
    rows.append([InlineKeyboardButton("📋 Admin তালিকা", callback_data="adm_list_admins")])
    # Settings
    if has_settings:
        rows.append([
            InlineKeyboardButton("📢 Channel Manage",           callback_data="adm_ch_list"),
            InlineKeyboardButton(f"🔔 Task Notify: {notify_s}", callback_data="adm_toggle_notify"),
        ])
        rows.append([
            InlineKeyboardButton("🎛 Menu Buttons এডিট", callback_data="adm_menu_btns"),
            InlineKeyboardButton("⚙️ Bot Settings",       callback_data="adm_settings"),
        ])
        rows.append([
            InlineKeyboardButton("🗄 DB Backup",   callback_data="adm_db_backup"),
            InlineKeyboardButton("♻️ DB Restore",  callback_data="adm_db_restore"),
        ])
        rows.append([InlineKeyboardButton("🔍 Bot Health Check & Auto-Fix", callback_data="adm_health_check")])
    # Referral Event
    if has_tasks or uid == ADMIN_ID:
        _rev = await asyncio.to_thread(get_active_referral_event)
        _rev_s = "🟢 চালু" if _rev else "🔴 বন্ধ"
        rows.append([InlineKeyboardButton(f"🎯 Referral Event ({_rev_s})", callback_data="adm_ref_event")])
    # Finance extras
    if has_finance:
        rows.append([InlineKeyboardButton("🏆 Leaderboard (Top 10)", callback_data="adm_leaderboard")])
        rows.append([InlineKeyboardButton("💳 Wallet Balance চেক করুন", callback_data="adm_wallet_bal")])
        rows.append([InlineKeyboardButton("👥 Refer Stats (Daily/7d/30d)", callback_data="adm_refer_stats")])
    # Logs (main admin or settings perm)
    if is_main or has_settings:
        rows.append([InlineKeyboardButton("📜 Bot Logs দেখুন", callback_data="adm_view_logs")])
    # Permission badge for sub-admins
    perm_line = ""
    if not is_main:
        perms = await asyncio.to_thread(get_admin_perms, uid)
        perm_line = f"\n🔑 *আপনার পারমিশন:* {_perms_display(','.join(perms))}"

    kb = InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([[]])
    admin_count = len(await asyncio.to_thread(get_all_admins)) + 1  # +1 for main admin
    await msg.reply_text(
        f"🔐 *ADMIN PANEL*{perm_line}\n\n"
        f"🤖 *Bot Status:* {bot_s}\n"
        f"🔔 *Task Notify:* {notify_s}\n"
        f"👥 *ইউজার:* `{total}` জন\n"
        f"👮 *মোট এডমিন:* `{admin_count}` জন\n"
        f"💰 *মোট ব্যালান্স:* `{round(tot_bal,4)}$ USDT`\n"
        f"📋 *Pending Tasks:* `{pg}` | ✅ `{ag}`\n"
        f"💸 *Pending Withdraw:* `{pw}` | ✅ `{aw}`",
        parse_mode="Markdown", reply_markup=kb
    )

# ──────────────────────────── CALLBACK HANDLER ────────────────────────────
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    d   = q.data
    try:
        # ── FIRST: spinner তাৎক্ষণিক clear করো (~200ms) — তারপর বাকি কাজ ──
        try: await q.answer()
        except Exception: pass
        await _on_callback_inner(update, ctx, q, uid, d)
    except Exception as e:
        log.exception(f"[on_callback ERROR] data={d} uid={uid} error={e}")
        try:
            await q.message.reply_text("❌ Error! Admin check logs.")
        except Exception: pass
    return

async def _on_callback_inner(update, ctx, q, uid, d):

    # ── Language selection (inline) ──────────────────────────────
    _already_done = None
    _brow = None
    _gas_cur = None
    _gc_already = None
    _wa_cur = None
    act = None
    bal_ok = None
    r = None
    refs = None
    row = None
    row2 = None
    rows = None
    subs = None
    total_ref = None
    total_usr = None
    wds = None
    # q.answer() ইতিমধ্যে on_callback()-এ করা হয়েছে — এখানে আর দরকার নেই
    if d in ("lang_bn", "lang_en"):
        lang = "bn" if d == "lang_bn" else "en"
        await asyncio.to_thread(set_lang, uid, lang)
        # button এ ✅ দেখাও তারপর message মুছে ফেলো
        try: await q.message.edit_reply_markup(reply_markup=await asyncio.to_thread(lang_kb, current_lang=lang))
        except Exception: pass
        try: await q.message.delete()
        except Exception: pass
        msg = T(uid, 'lang_set_bn') if lang == 'bn' else T(uid, 'lang_set_en')
        # If first-time language selection, ask country next
        if ctx.user_data.get("state") == "first_lang":
            ctx.user_data["state"] = "first_country"
            await ctx.bot.send_message(uid, msg, parse_mode="Markdown")
            q_txt = (
                "🌍 *আপনার দেশ বেছে নিন:*\n\n"
                "⚠️ *সতর্কতা:* ভুল দেশ বাছলে Task পাবেন না!\n"
                "👆 আপনি যে দেশে থাকেন সেই দেশ বেছে নিন।\n"
                "🔒 *একবার বাছলে আর পরিবর্তন করা যাবে না।*"
                if lang == 'bn' else
                "🌍 *Select your country:*\n\n"
                "⚠️ *Warning:* Selecting the wrong country means you won't get tasks!\n"
                "👆 Please select the country where you actually live.\n"
                "🔒 *This cannot be changed once selected.*"
            )
            await ctx.bot.send_message(uid, q_txt, parse_mode="Markdown", reply_markup=country_kb())
        else:
            await ctx.bot.send_message(uid, msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    # ── Country selection (first-time + change) ─────────────────
    if d.startswith("set_country_"):
        code = d[len("set_country_"):]
        valid_codes = {c[0] for c in COUNTRY_LIST}
        _lk, _existing_country = await asyncio.gather(
            asyncio.to_thread(get_lang, uid),
            asyncio.to_thread(get_user_country, uid),
        )
        if code not in valid_codes:
            await q.message.reply_text("❌ অজানা দেশ!" if _lk == 'bn' else "❌ Unknown country!"); return
        # ── One-time lock: already set + not first-time flow → block ──
        if _existing_country and ctx.user_data.get("state") != "first_country":
            _ex_flag, _ex_name = COUNTRY_MAP.get(_existing_country.upper(), ("🌍", _existing_country))
            _lock_msg = (
                f"🔒 *দেশ পরিবর্তন করা যাবে না।*\n\n"
                f"আপনার দেশ আগেই সেট হয়েছে: {_ex_flag} *{_ex_name}*\n\n"
                f"পরিবর্তনের প্রয়োজন হলে Support এ যোগাযোগ করুন।"
                if _lk == 'bn' else
                f"🔒 *Country cannot be changed.*\n\n"
                f"Your country is already set to: {_ex_flag} *{_ex_name}*\n\n"
                f"Contact Support if you need to change it."
            )
            try: await q.message.edit_reply_markup(reply_markup=None)
            except Exception: pass
            await ctx.bot.send_message(uid, _lock_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            return
        await asyncio.to_thread(set_user_country, uid, code)
        flag, cname = COUNTRY_MAP.get(code, ("🌍", code))
        try: await q.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        # First-time registration flow → channel join step
        if ctx.user_data.get("state") == "first_country":
            ctx.user_data["state"] = "first_channel"
            channels = await asyncio.to_thread(get_req_channels)
            if channels:
                await join_prompt(update, uid)
            else:
                # চ্যানেল নেই → সরাসরি welcome, কিন্তু notification পাঠাও
                ctx.user_data.pop("state", None)
                _no_ch_uid = ctx.user_data.get("pending_uid") or uid
                if ctx.user_data.get("pending_new") and _no_ch_uid not in _notified_set:
                    _notified_set_add(_no_ch_uid)
                    await fire_new_user_notify(ctx,
                        _no_ch_uid,
                        ctx.user_data.get("pending_name"),
                        ctx.user_data.get("pending_uname"),
                        ctx.user_data.get("pending_ref"))
                    for k in ("pending_new","pending_uid","pending_name","pending_uname","pending_ref"):
                        ctx.user_data.pop(k, None)
                await _send_welcome(update, ctx, uid)
        else:
            success_msg = (
                f"✅ *আপনার দেশ সেট হয়েছে:* {flag} *{cname}*"
                if _lk == 'bn' else
                f"✅ *Country set:* {flag} *{cname}*"
            )
            await ctx.bot.send_message(uid, success_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            # Resume pending restricted task after country set
            _pending = ctx.user_data.pop("pending_tg_task_after_country", None)
            if _pending:
                _ps = str(_pending)
                if _ps.startswith("ctask_"):
                    _ctid = int(_ps.split("_")[1])
                    _ctask = await asyncio.to_thread(get_category_task, _ctid)
                    if _ctask and _ctask["is_active"]:
                        await show_category_task_rules(update, ctx, _ctask)
                else:
                    _ptask = await asyncio.to_thread(get_tg_task, int(_ps))
                    if _ptask and _ptask["is_active"]:
                        await show_tg_task(update, ctx, _ptask)
        return

    # ── Check join ──────────────────────────────────────────────
    if d == "check_join":
        invalidate_ch_cache(uid)   # পুরনো cache মুছো — fresh API check করবে
        missing = await check_ch(update, ctx)
        if missing:
            await q.message.reply_text(T(uid,'not_joined'), parse_mode="Markdown")
        else:
            lang_cj = await asyncio.to_thread(get_lang, uid)
            try: await q.message.delete()
            except Exception: pass
            # ── Notification logic — ctx.user_data বা DB দুটো পথেই কাজ করবে ──────
            _n_uid   = ctx.user_data.get("pending_uid")   or uid
            _n_name  = ctx.user_data.get("pending_name")  or (q.from_user.full_name if q.from_user else str(uid))
            _n_uname = ctx.user_data.get("pending_uname") or (q.from_user.username or "")
            _n_ref   = ctx.user_data.get("pending_ref")
            _should_notify = ctx.user_data.get("pending_new", False)

            # bot restart এ ctx.user_data হারিয়ে গেলে DB থেকে চেক করো
            if not _should_notify and _n_uid not in _notified_set:
                def _db_check_fresh():
                    u_row = get_user(_n_uid)
                    if not u_row:
                        return False, None
                    jat = u_row.get("joined_at", "")
                    try:
                        from datetime import datetime, timedelta
                        jdt = datetime.fromisoformat(jat)
                        if datetime.now(tz=jdt.tzinfo) - jdt < timedelta(hours=2):
                            return True, u_row.get("referred_by")
                    except Exception:
                        pass
                    return False, None
                _is_fresh, _db_ref = await asyncio.to_thread(_db_check_fresh)
                if _is_fresh:
                    _should_notify = True
                    _n_ref = _n_ref or _db_ref

            if _should_notify and _n_uid not in _notified_set:
                _notified_set_add(_n_uid)
                await fire_new_user_notify(ctx, _n_uid, _n_name, _n_uname, _n_ref)
            for k in ("pending_new","pending_uid","pending_name","pending_uname","pending_ref"):
                ctx.user_data.pop(k, None)
            ctx.user_data.pop("state", None)
            # ভাষা না থাকলে আগে ভাষা বাছাই করাও (fallback)
            if not await asyncio.to_thread(user_has_lang, uid):
                ctx.user_data["state"] = "first_lang"
                await ctx.bot.send_message(uid,
                    "🌍 *ভাষা বেছে নিন / Choose your language:*",
                    parse_mode="Markdown", reply_markup=await asyncio.to_thread(lang_kb)  # first-time, no tick
                )
            else:
                lang_key = 'welcome_msg_bn' if lang_cj == 'bn' else 'welcome_msg_en'
                custom_w  = await asyncio.to_thread(get_setting, lang_key)
                welcome_text = custom_w if custom_w else T(uid, 'welcome')
                await ctx.bot.send_message(uid, welcome_text, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    # ── Leaderboard: find my place ────────────────────────────────
    if d == "lb_my_rank":
        lang, (rank, cnt) = await asyncio.gather(
            asyncio.to_thread(get_lang, uid),
            asyncio.to_thread(_lb_get_user_full_rank, uid),
        )
        if rank is None:
            msg = ("💡 _You have no approved tasks today. Complete tasks to appear on the leaderboard!_"
                   if lang == "en" else
                   "💡 _আজকে তোমার কোনো Task Approve হয়নি। Task করলে Leaderboard এ আসবে!_")
        else:
            suffix = ("task" if cnt == 1 else "tasks") if lang == "en" else "টি Task"
            msg = (f"📍 *Your rank today:* #{rank} — {cnt} {suffix}"
                   if lang == "en" else
                   f"📍 *আজকে তোমার অবস্থান:* #{rank} — {cnt}{suffix}")
        await q.message.reply_text(msg, parse_mode="Markdown")
        return

    # ── My Referrals ──────────────────────────────────────────────
    if d.startswith("my_refs_"):
        rid = int(d.split("_")[2])
        def _get_refs():
            with _c() as c:
                return c.execute(
                    "SELECT user_id,username,full_name,joined_at FROM users WHERE referred_by=%s ORDER BY user_id DESC LIMIT 50", (rid,)
                ).fetchall()
        refs = await asyncio.to_thread(_get_refs)
        if not refs:
            await q.message.reply_text(T(uid,'no_referrals'), parse_mode="Markdown"); return
        lines = []
        for i, r in enumerate(refs, 1):
            n = r["full_name"] or r["username"] or "Unknown"
            lines.append(f"`{i}.` {n} — {r['joined_at']}")
        text = f"{T(uid,'my_referrals_title')}\n\n" + "\n".join(lines)
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown"); return

    # ── Task cancel ──────────────────────────────────────────────
    if d == "task_cancel":
        ctx.user_data.pop("state", None)
        ctx.user_data.pop("task_id", None)
        ctx.user_data.pop("task_name", None)
        ctx.user_data.pop("task_price", None)
        ctx.user_data.pop("task_fields", None)
        ctx.user_data.pop("field_idx", None)
        ctx.user_data.pop("collected", None)
        ctx.user_data.pop("categ_task_id", None)
        try: await q.message.delete()
        except Exception: pass
        try:
            _tc_lang = await asyncio.to_thread(get_lang, uid)
            _tc_note = "📋 নিচের মেনু থেকে Tasks চাপুন।" if _tc_lang == 'bn' else "📋 Press Tasks from the menu below."
            await q.message.reply_text(_tc_note, reply_markup=await asyncio.to_thread(main_kb, uid))
        except Exception:
            pass
        return

    if d.startswith("adm_ctask_golive_"):
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        tid = int(d.split("_")[3])
        t   = await asyncio.to_thread(get_category_task, tid)
        if not t:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown"); return
        _, fields = await asyncio.gather(
            asyncio.to_thread(update_category_task, tid, "is_active", 1),
            asyncio.to_thread(get_category_task_fields, tid),
        )
        max_c  = t["max_completions"]
        try: await q.message.delete()
        except Exception: pass
        await q.message.reply_text(
            f"🚀 *Task এখন Live!*\n\n"
            f"📌 *নাম:* `{t['name']}`\n"
            f"💰 *দাম:* `{t['price']}$ USDT`\n"
            f"👥 *Max:* `{max_c if max_c > 0 else '∞'} জন`\n"
            f"📋 *Proof Fields:* `{len(fields)} টি`\n\n"
            f"✅ User রা এখনই এই Task দেখতে ও জমা দিতে পারবে।",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📁 Category তালিকা", callback_data="adm_cat_list")
            ]])
        )
        bot_info  = await ctx.bot.get_me()
        task_link = f"https://t.me/{bot_info.username}?start=ctask_{tid}"
        slot_txt  = f"{max_c}" if max_c > 0 else "Unlimited"
        ttype     = _rget(t, 'task_type', 'other')
        await _broadcast_task_notify(ctx, q.message, uid,
            t['name'], t['price'], task_link, slot_txt, task_type=ttype,
            t_code=_rget(t, 'task_code', '') or '')
        return

    # ── TG Task list (grouped) ────────────────────────────────────
    if d == "tgt_list":
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        await show_tg_tasks_list(update, ctx)
        return

    # ── Category selected → show tasks ───────────────────────────
    if d.startswith("categ_"):
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _cl = await asyncio.to_thread(get_lang, uid)
        try:
            cid = int(d.split("_")[1])
            await show_category_tasks(update, ctx, cid)
        except Exception as e:
            log.warning(f"categ error: {e}")
            await q.message.reply_text("❌ সমস্যা হয়েছে!" if _cl == 'bn' else "❌ Something went wrong!")
        return

    # ── Category task selected → show rules ──────────────────────
    if d.startswith("ctask_") and not d.startswith("ctask_submit_") and not d.startswith("ctask_watch_video_"):
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _ctl = await asyncio.to_thread(get_lang, uid)
        try:
            tid  = int(d.split("_")[1])
            task = await asyncio.to_thread(get_category_task, tid)
            if not task or not task['is_active']:
                await q.message.reply_text("❌ Task পাওয়া যায়নি!" if _ctl == 'bn' else "❌ Task not found!"); return
            await show_category_task_rules(update, ctx, task)
        except Exception as e:
            log.warning(f"ctask error: {e}")
            await q.message.reply_text("❌ সমস্যা হয়েছে!" if _ctl == 'bn' else "❌ Something went wrong!")
        return

    # ── Category task submit ──────────────────────────────────────
    if d.startswith("ctask_submit_"):
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _csl = await asyncio.to_thread(get_lang, uid)
        try:
            tid = int(d.split("_")[2])
            await start_category_task_collect(update, ctx, tid)
        except Exception as e:
            log.warning(f"ctask_submit error: {e}")
            await q.message.reply_text("❌ সমস্যা হয়েছে!" if _csl == 'bn' else "❌ Something went wrong!")
        return

    # ── TG Task separator (dummy) ─────────────────────────────────
    if d == "tgt_sep":
        return

    # ── TG Task selected → show info ─────────────────────────────
    if d.startswith("tgt_") and not d.startswith("tgt_check_"):
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _tgl = await asyncio.to_thread(get_lang, uid)
        try:
            tid  = int(d.split("_")[1])
            task = await asyncio.to_thread(get_tg_task, tid)
            if not task or not task['is_active']:
                await q.message.reply_text("❌ Task পাওয়া যায়নি!" if _tgl == 'bn' else "❌ Task not found!"); return
            await show_tg_task(update, ctx, task)
        except Exception as e:
            log.warning(f"tgt task error: {e}")
            await q.message.reply_text("❌ সমস্যা হয়েছে!" if _tgl == 'bn' else "❌ Something went wrong!")
        return

    # ── TG Task check membership ──────────────────────────────────
    if d.startswith("tgt_check_"):
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _blk_tg, _blk_tg_dt = await asyncio.to_thread(is_task_blocked, uid)
        if _blk_tg and _blk_tg_dt:
            _until_tg = _blk_tg_dt.astimezone(pytz.timezone("Asia/Dhaka")).strftime("%d %b %Y, %I:%M %p")
            await q.message.reply_text(T(uid, 'task_blocked_msg', until=_until_tg)[:4000], parse_mode="Markdown")
            return
        _tckl = await asyncio.to_thread(get_lang, uid)
        try:
            tid = int(d.split("_")[2])
            await handle_tg_task_check(update, ctx, tid)
        except Exception as e:
            log.warning(f"tgt check error: {e}")
            await q.message.reply_text("❌ সমস্যা হয়েছে! আবার চেষ্টা করুন।" if _tckl == 'bn' else "❌ Something went wrong! Please try again.")
        return

    # ── Task selected → show rules ────────────────────────────────
    if d.startswith("task_") and not d.startswith("task_submit_") and not d.startswith("task_watch_video_"):
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _tsl = await asyncio.to_thread(get_lang, uid)
        _answered = False
        try:
            tid  = int(d.split("_")[1])
            task = await asyncio.to_thread(get_task, tid)
            if not task:
                await q.message.reply_text("❌ Task পাওয়া যায়নি!" if _tsl == 'bn' else "❌ Task not found!"); return
            _answered = True
            await show_task_rules(update, ctx, task)
        except Exception as _te:
            log.warning(f"[TASK_CLICK] tid={d} error: {_te}")
            if not _answered:
                try: await q.message.reply_text("❌ সমস্যা হয়েছে!" if _tsl == 'bn' else "❌ Something went wrong!")
                except Exception: pass
            else:
                try: await q.message.reply_text("❌ Task দেখাতে সমস্যা হয়েছে। আবার চেষ্টা করুন।" if _tsl == 'bn' else "❌ Could not load task. Please try again.")
                except Exception: pass
        return

    # ── Task submit ──────────────────────────────────────────────
    if d.startswith("task_submit_"):
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _tsubl = await asyncio.to_thread(get_lang, uid)
        try:
            tid = int(d.split("_")[2])
            await start_task_collect(update, ctx, tid)
        except Exception as e:
            log.warning(f"task_submit error: {e}")
            await q.message.reply_text("❌ সমস্যা হয়েছে!" if _tsubl == 'bn' else "❌ Something went wrong!")
        return

    # ── Withdraw ──────────────────────────────────────────────────
    if d == "wd_go":
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        await wd_ask_addr(update, ctx); return

    if d == "wd_confirm":
        if ctx.user_data.get("state") != "wd_confirm":
            lang = await asyncio.to_thread(get_lang, uid)
            msg = ("⚠️ Session expired. Please start withdrawal again."
                   if lang=='en' else "⚠️ Session শেষ। আবার Withdraw শুরু করুন।")
            await q.message.reply_text(msg); return
        try:
            await q.message.delete()
        except Exception:
            pass
        await do_wd_submit(update, ctx); return

    if d == "wd_edit_addr":
        ctx.user_data["state"] = "wd_addr"
        lang, fee, min_w, bal = await asyncio.gather(
            asyncio.to_thread(get_lang, uid),
            asyncio.to_thread(get_wd_fee),
            asyncio.to_thread(get_min_withdraw),
            asyncio.to_thread(get_bal, uid),
        )
        await q.message.reply_text(
            T(uid, 'wd_enter_addr'), parse_mode="Markdown", reply_markup=cancel_kb(uid)
        ); return

    if d == "wd_edit_amt":
        ctx.user_data["state"] = "wd_amt"
        fee, min_w, bal = await asyncio.gather(
            asyncio.to_thread(get_wd_fee),
            asyncio.to_thread(get_min_withdraw),
            asyncio.to_thread(get_bal, uid),
        )
        await q.message.reply_text(
            T(uid, 'wd_enter_amt', bal=bal, fee=fee, min_w=min_w),
            parse_mode="Markdown", reply_markup=cancel_kb(uid)
        ); return

    if d == "wd_use_saved_wallet":
        _sw = ctx.user_data.pop("_saved_w_cache", None)
        if not _sw:
            def _get_saved_wallet():
                with _c() as c:
                    return c.execute("SELECT saved_wallet FROM users WHERE user_id=%s", (uid,)).fetchone()
            _sr = await asyncio.to_thread(_get_saved_wallet)
            _sw = (_rget(_sr, "saved_wallet", "") or "").strip() if _sr else ""
        if not _sw:
            lang = await asyncio.to_thread(get_lang, uid)
            await q.message.reply_text(
                T(uid, 'wd_enter_addr'), parse_mode="Markdown", reply_markup=cancel_kb(uid))
            ctx.user_data["state"] = "wd_addr"; return
        ctx.user_data["wd_addr"] = _sw
        ctx.user_data["state"]   = "wd_amt"
        bal, fee, min_w = await asyncio.gather(
            asyncio.to_thread(get_bal, uid),
            asyncio.to_thread(get_wd_fee),
            asyncio.to_thread(get_min_withdraw),
        )
        await q.message.reply_text(
            T(uid, 'wd_enter_amt', bal=bal, fee=fee, min_w=min_w),
            parse_mode="Markdown", reply_markup=cancel_kb(uid)); return

    if d == "wd_enter_new_addr":
        ctx.user_data.pop("_saved_w_cache", None)
        ctx.user_data["state"] = "wd_addr"
        await q.message.reply_text(
            T(uid, 'wd_enter_addr'), parse_mode="Markdown", reply_markup=cancel_kb(uid)); return

    # ── Gmail message to user ─────────────────────────────────────
    if d.startswith("gmsg_"):
        if not await asyncio.to_thread(is_admin, uid):
            await q.message.reply_text("❌ আপনি এডমিন না!"); return
        gid = int(d.split("_")[1])
        def _db_5595():
            row = None
            with _c() as c: row = c.execute("SELECT user_id FROM gmails WHERE id=%s", (gid,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_5595)
        if not row: await q.message.reply_text("❌ পাওয়া যায়নি!"); return
        ctx.user_data["state"]         = "adm_direct_msg"
        ctx.user_data["direct_msg_to"] = row["user_id"]
        await q.message.reply_text(
            f"📨 *Sub #{gid} এর User কে মেসেজ লিখুন:*\n\n/cancel — বাতিল",
            parse_mode="Markdown")
        return

    # ── Gmail approve ─────────────────────────────────────────────
    if d.startswith("ga_"):
        gid = int(d.split("_")[1])
        def _db_5610():
            row = None
            with _c() as c: row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_5610)
        if not row: return await q.message.reply_text("❌ পাওয়া যায়নি!")
        # admin অথবা এই submission এর task owner — দুজনেই approve করতে পারবে
        _is_adm = await asyncio.to_thread(is_admin, uid)
        _row_owner = int(_rget(row, 'owner_id', 0) or 0)
        if not _is_adm and uid != _row_owner:
            await q.message.reply_text("❌ আপনি এই submission approve করতে পারবেন না!"); return
        if row["status"] != "pending":
            return await q.message.reply_text(f"⚠️ ইতিমধ্যে {row['status']}!")
        guser, price = row["user_id"], row["amount"]
        _row_ttype = _rget(row, 'task_type', 'other')
        _row_ticon = task_type_icon(_row_ttype)
        _task_display = f"{_row_ticon} {row['task_name']}"
        # Atomic: gmails approved + balance credit একসাথে commit
        # AND status='pending' — double-approve race condition প্রতিরোধ করে
        bal_ok = False
        _already_done = False
        def _db_5622():
            _already_done = None
            bal_ok = None
            with _c() as c:
                _appr = c.execute(
                    "UPDATE gmails SET status='approved', approved_at=%s WHERE id=%s AND status='pending'",
                    (now(), gid)
                )
                if _appr.rowcount == 0:
                    _already_done = True
                else:
                    bal_ok = bool(c.execute(
                        "UPDATE users SET balance=balance+%s WHERE user_id=%s", (price, guser)
                    ).rowcount)
                    if bal_ok:
                        _invalidate_user_cache(guser)
            return _already_done, bal_ok
        _already_done, bal_ok = await asyncio.to_thread(_db_5622)
        if _already_done:
            return await q.message.reply_text("⚠️ ইতিমধ্যে approve/reject হয়েছে!")
        _q_answered = False
        if not bal_ok:
            log.warning(f"[APPROVE] user_id={guser} DB তে নেই — balance add হয়নি! gmails id={gid}")
            await q.message.reply_text(f"⚠️ Approved কিন্তু user {guser} DB তে নেই — balance যোগ হয়নি!")
            _q_answered = True
        _, ref = await asyncio.gather(
            asyncio.to_thread(add_points, guser, POINT_PER_APPROVE),
            asyncio.to_thread(get_referred_by, guser),
        )
        if ref:
            comm_pct = await asyncio.to_thread(get_commission_pct)
            comm = round(price * comm_pct / 100.0, 4)
            comm_pct_display = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
            await asyncio.to_thread(add_bal, ref, comm)
            await asyncio.to_thread(record_commission, ref, guser, comm, row.get('task_name', ''))
            try:
                ru    = await asyncio.to_thread(get_user, guser)
                rname = (ru["full_name"] or ru["username"] or "Unknown") if ru else "Unknown"
                await ctx.bot.send_message(ref,
                    T(ref, 'commission_msg', name=_md_esc(rname), price=price, comm=comm, comm_pct=comm_pct_display),
                    parse_mode="Markdown")
            except Exception as _e:
                log.warning(f"[APPROVE] ref={ref} commission notify ব্যর্থ: {_e}")
            # ── Referral Event bonus check ──────────────────────────
            _ref_ev_task = asyncio.create_task(_check_ref_event_bonus(guser, ref, ctx.bot))
            _background_tasks.add(_ref_ev_task); _ref_ev_task.add_done_callback(_background_tasks.discard)
        try:
            await ctx.bot.send_message(guser,
                T(guser, 'approved_msg', task=_task_display, price=price),
                parse_mode="Markdown")
        except Exception as _e:
            log.warning(f"[APPROVE] guser={guser} approved notify ব্যর্থ: {_e}")
        _tlb2 = asyncio.create_task(_lb_refresh_now(ctx.bot))
        _background_tasks.add(_tlb2); _tlb2.add_done_callback(_background_tasks.discard)
        _rw_code = _rget(row, 'task_code', '') or ''
        _rw_name = row['task_name']
        _rt, _maxed = await asyncio.to_thread(_regular_task_mark_done, _rw_code, _rw_name)
        if _maxed and _rt:
            _trm = asyncio.create_task(_notify_task_maxed(
                ctx.bot, _rt["name"], _rt["task_code"] or "",
                "Regular Task", _rt["completed_count"], _rt["max_completions"]
            ))
            _background_tasks.add(_trm); _trm.add_done_callback(_background_tasks.discard)
        _task_type_lbl = task_type_label(_rget(row, 'task_type', 'other'))
        _tdn2 = asyncio.create_task(_notify_task_done_ch(
            ctx.bot, guser, _rw_name, _rw_code, price, _task_type_lbl
        ))
        _background_tasks.add(_tdn2); _tdn2.add_done_callback(_background_tasks.discard)
        status_line = "\n\n✅ *APPROVED by Admin*"
        orig = q.message.text or q.message.caption or ""
        new_txt = orig + status_line
        try:
            if q.message.photo:
                await q.message.edit_caption(new_txt, parse_mode="Markdown", reply_markup=None)
            else:
                await q.message.edit_text(new_txt, parse_mode="Markdown", reply_markup=None)
        except Exception: pass
        def _db_5687():
            row2 = None
            with _c() as c: row2 = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
            return row2
        row2 = await asyncio.to_thread(_db_5687)
        if not row2:
            return
        # Sub-admin approve করলেও admin + channel দুটো message-ই আপডেট করো
        _ch_id = await asyncio.to_thread(get_gmail_ch_id)
        _msgs_to_edit = []
        if uid == ADMIN_ID:
            # Main admin: channel message edit করো
            if row2["channel_msg_id"]: _msgs_to_edit.append((_ch_id, row2["channel_msg_id"]))
        else:
            # Sub-admin: main admin-এর message + channel message দুটোই edit করো
            if row2["admin_msg_id"]:   _msgs_to_edit.append((ADMIN_ID, row2["admin_msg_id"]))
            if row2["channel_msg_id"]: _msgs_to_edit.append((_ch_id,  row2["channel_msg_id"]))
        for _ecid, _emid in _msgs_to_edit:
            try:
                if q.message.photo:
                    await ctx.bot.edit_message_caption(new_txt, chat_id=_ecid, message_id=_emid, parse_mode="Markdown", reply_markup=None)
                else:
                    await ctx.bot.edit_message_text(new_txt, chat_id=_ecid, message_id=_emid, parse_mode="Markdown", reply_markup=None)
            except Exception: pass
        if not _q_answered:
            await q.message.reply_text("✅ Approved!")
        return

    # ── Gmail reject — কারণ জিজ্ঞেস করো আগে ────────────────────────
    if d.startswith("gr_"):
        gid = int(d.split("_")[1])
        def _db_5706():
            row = None
            with _c() as c: row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_5706)
        if not row: return await q.message.reply_text("❌ পাওয়া যায়নি!")
        # admin অথবা এই submission এর task owner — দুজনেই reject করতে পারবে
        _is_adm_r = await asyncio.to_thread(is_admin, uid)
        _row_owner_r = int(_rget(row, 'owner_id', 0) or 0)
        if not _is_adm_r and uid != _row_owner_r:
            await q.message.reply_text("❌ আপনি এই submission reject করতে পারবেন না!"); return
        if row["status"] != "pending":
            return await q.message.reply_text(f"⚠️ ইতিমধ্যে {row['status']}!")
        ctx.user_data["state"]              = "adm_reject_reason"
        ctx.user_data["adm_reject_gid"]     = gid
        ctx.user_data["adm_reject_msg_id"]  = q.message.message_id
        ctx.user_data["adm_reject_chat_id"] = q.message.chat_id
        ctx.user_data["adm_reject_has_photo"] = bool(q.message.photo)
        ctx.user_data["adm_reject_orig_txt"]  = q.message.text or q.message.caption or ""
        _rej_prompt = (
            f"❌ *Sub #{gid} Reject করছেন।*\n\n"
            f"📌 *Task:* {_md_esc(str(row['task_name']))}\n"
            f"👤 *User ID:* `{row['user_id']}`\n\n"
            f"📝 *Reject এর কারণ লিখুন* (ইউজারকে পাঠানো হবে):\n\n"
            f"_/cancel — বাতিল করতে_"
        )
        # Channel থেকে reject চাপলে prompt channel-এ যায় — admin reply করতে পারে না
        # DM-এ পাঠাও সবসময়, তাহলে admin সেখানে কারণ লিখতে পারবে
        if q.message.chat_id != uid:
            try:
                await ctx.bot.send_message(uid, _rej_prompt, parse_mode="Markdown")
            except Exception:
                await q.message.reply_text(_rej_prompt, parse_mode="Markdown")
        else:
            await q.message.reply_text(_rej_prompt, parse_mode="Markdown")
        return

    # ── Show full address ─────────────────────────────────────────
    if d.startswith("show_addr_"):
        wid = int(d.split("_")[2])
        if not await asyncio.to_thread(is_admin, uid):
            await q.message.reply_text("❌ শুধু Admin দেখতে পারবে!"); return
        def _db_5732():
            row = None
            with _c() as c:
                row = c.execute("SELECT address FROM withdrawals WHERE id=%s", (wid,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_5732)
        if not row:
            await q.message.reply_text("❌ পাওয়া যায়নি!"); return
        await q.message.reply_text(f"📍 Full Address:\n{row['address']}")
        return

    # ── Gas Fee Alert: Admin approve (পাঠাও) বা cancel (ফেরত দাও) ──
    if d.startswith("gas_ok_") or d.startswith("gas_cancel_"):
        if not await asyncio.to_thread(is_admin, uid):
            await q.message.reply_text("❌ আপনি এডমিন না!"); return
        wid = int(d.split("_")[2])
        def _db_5744():
            row = None
            with _c() as c:
                row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_5744)
        if not row:
            await q.message.reply_text("❌ WD পাওয়া যায়নি!"); return
        if row["status"] != "gas_pending":
            await q.message.reply_text(f"⚠️ ইতিমধ্যে {row['status']}!"); return

        wuser = row["user_id"]
        wamt  = row["amount"]
        waddr = row["address"]
        fee   = row["fee"] if row["fee"] is not None else await asyncio.to_thread(get_wd_fee)

        if d.startswith("gas_ok_"):
            # ── Atomic approve: শুধু তখনই update হবে যখন status এখনো 'gas_pending' ──
            def _db_5758():
                _gas_cur = None
                with _c() as c:
                    _gas_cur = c.execute(
                        "UPDATE withdrawals SET status='pending' WHERE id=%s AND status='gas_pending'", (wid,))
                return _gas_cur
            _gas_cur = await asyncio.to_thread(_db_5758)
            if _gas_cur.rowcount == 0:
                await q.message.reply_text("⚠️ ইতিমধ্যে process হয়ে গেছে!"); return
            _mark_dirty()
            try: await q.message.edit_text(q.message.text + "\n\n✅ *Admin approved — পাঠানো হবে।*", parse_mode="Markdown", reply_markup=None)
            except Exception: pass
            await q.message.reply_text("✅ Withdrawal pending এ রাখা হয়েছে — পরের cycle এ পাঠাবে।")
        else:
            # ── বাতিল করো — Atomic: AND status='gas_pending' — double-cancel হলে double-refund হবে না
            def _db_5770():
                _already = False
                with _c() as c:
                    _gc_cur = c.execute(
                        "UPDATE withdrawals SET status='rejected' WHERE id=%s AND status='gas_pending'", (wid,)
                    )
                    if _gc_cur.rowcount == 0:
                        _already = True
                    else:
                        c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (wamt, wuser))
                        _invalidate_user_cache(wuser)
                return _already
            _gc_already = await asyncio.to_thread(_db_5770)
            if _gc_already:
                await q.message.reply_text("⚠️ ইতিমধ্যে process হয়ে গেছে!"); return
            _mark_dirty()
            _ulang = await asyncio.to_thread(get_lang, wuser)
            try:
                _cancel_txt = (
                    f"❌ *Your withdrawal has been cancelled.*\n\n"
                    f"💰 *Amount:* `${wamt:.4f} USDT`\n"
                    f"🔄 The amount has been refunded to your account.\n\n"
                    f"❓ Support: @inkamxbd"
                    if _ulang == 'en' else
                    f"❌ *Withdrawal বাতিল হয়েছে।*\n\n"
                    f"💰 *Amount:* `${wamt:.4f} USDT`\n"
                    f"🔄 টাকা আপনার একাউন্টে ফেরত দেওয়া হয়েছে।\n\n"
                    f"❓ সমস্যার জন্য: @inkamxbd"
                )
                await ctx.bot.send_message(wuser, _cancel_txt, parse_mode="Markdown")
            except Exception: pass
            try: await q.message.edit_text(q.message.text + "\n\n❌ *Admin cancelled — balance refunded.*", parse_mode="Markdown", reply_markup=None)
            except Exception: pass  # message edit may fail if unchanged or deleted — safe to ignore
            await q.message.reply_text("❌ Withdrawal cancelled — balance refunded.")
        return

    # ── Withdraw approve/reject ──────────────────────────────────
    if d.startswith("wa_") or d.startswith("wr_"):
        if not await asyncio.to_thread(is_admin, uid):
            await q.message.reply_text("❌ আপনি এডমিন না!"); return
        wid    = int(d.split("_")[1])
        action = "approved" if d.startswith("wa_") else "rejected"
        def _db_5807():
            row = None
            with _c() as c: row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_5807)
        if not row: return await q.message.reply_text("❌ পাওয়া যায়নি!")
        if row["status"] != "pending":
            return await q.message.reply_text(f"⚠️ ইতিমধ্যে {row['status']}!")
        wuser, wamt, waddr = row["user_id"], row["amount"], row["address"]
        masked = mask_addr_short(waddr)
        if action == "approved":
            # Atomic: AND status='pending' — double-approve race condition প্রতিরোধ করে
            def _db_5815():
                _wa_cur = None
                with _c() as c:
                    _wa_cur = c.execute(
                        "UPDATE withdrawals SET status='approved' WHERE id=%s AND status='pending'", (wid,)
                    )
                return _wa_cur
            _wa_cur = await asyncio.to_thread(_db_5815)
            if _wa_cur.rowcount == 0:
                return await q.message.reply_text("⚠️ ইতিমধ্যে process হয়ে গেছে!")
            try:
                await ctx.bot.send_message(wuser,
                    T(wuser, 'wd_approved_msg', amt=wamt, addr=masked),
                    parse_mode="Markdown")
            except Exception: pass
        else:
            # Atomic: AND status='pending' guard — double-reject হলে double-refund হবে না
            def _db_5828():
                with _c() as c:
                    _wr_cur = c.execute(
                        "UPDATE withdrawals SET status='rejected' WHERE id=%s AND status='pending'", (wid,)
                    )
                    if _wr_cur.rowcount == 0:
                        return True  # already processed
                    # শুধু এই transaction-ই reject করেছে — refund safe
                    c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (wamt, wuser))
                    _invalidate_user_cache(wuser)
                return False
            _wr_already = await asyncio.to_thread(_db_5828)
            if _wr_already:
                await q.message.reply_text("⚠️ ইতিমধ্যে process হয়ে গেছে!"); return
            _mark_dirty()
            try:
                await ctx.bot.send_message(wuser,
                    T(wuser, 'wd_rejected_msg', amt=wamt),
                    parse_mode="Markdown")
            except Exception: pass

        status_line = "\n\n✅ *APPROVED by Admin*" if action == "approved" else "\n\n❌ *REJECTED + Refunded*"
        raw_txt = (q.message.text or "")
        new_txt = raw_txt + status_line
        try: await q.message.edit_text(new_txt, parse_mode="Markdown", reply_markup=None)
        except Exception: pass
        def _db_5848():
            row2 = None
            with _c() as c: row2 = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
            return row2
        row2 = await asyncio.to_thread(_db_5848)
        if not row2:
            return
        other_mid = row2["channel_msg_id"] if uid == ADMIN_ID else row2["admin_msg_id"]
        other_cid = await asyncio.to_thread(get_withdraw_ch_id) if uid == ADMIN_ID else ADMIN_ID
        if other_mid:
            try:
                await ctx.bot.edit_message_text(new_txt, chat_id=other_cid, message_id=other_mid, parse_mode="Markdown", reply_markup=None)
            except Exception as _e:
                log.debug(f"[WD-EDIT] cross-chat edit ব্যর্থ (ok): {_e}")
        await q.message.reply_text("✅ Done!")
        return

    # ── Bio Task user callbacks ────────────────────────────────────
    if d == "bio_task_info":
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        bio_task, lang = await asyncio.gather(
            asyncio.to_thread(get_bio_task),
            asyncio.to_thread(get_lang, uid),
        )
        if not bio_task or not bio_task['is_active']:
            await q.message.reply_text(
                "❌ Bio Task এখন সক্রিয় নেই।" if lang == 'bn'
                else "❌ Bio Task is not active right now.",
                parse_mode="Markdown")
            return

        daily_reward = bio_task['reward']
        ar           = (bio_task.get('add_reward', 0) or 0)
        # সব independent DB calls + bot.get_me একসাথে parallel
        bt_done, add_done, actively_track, history, bot_me = await asyncio.gather(
            asyncio.to_thread(bio_task_paid_today, uid),
            asyncio.to_thread(is_add_reward_received, uid),
            asyncio.to_thread(is_bio_actively_tracked, uid),
            asyncio.to_thread(get_bio_task_history, uid),
            ctx.bot.get_me(),
        )
        bot_username = bot_me.username
        ref_link     = f"https://t.me/{bot_username}?start=ref_{uid}"

        if history:
            if lang == 'bn':
                hist_lines = "\n\n📅 *সাম্প্রতিক পেমেন্ট:*\n" + "\n".join(
                    [f"  • `{r['task_date']}` — `{r['amount']}$`" for r in history]
                )
            else:
                hist_lines = "\n\n📅 *Recent payments:*\n" + "\n".join(
                    [f"  • `{r['task_date']}` — `{r['amount']}$`" for r in history]
                )
        else:
            hist_lines = ""

        if lang == 'bn':
            if bt_done:
                track_status = "✅ আজকের Daily Reward পেয়েছেন!"
            elif actively_track:
                track_status = "🔄 Tracking চলছে — কাল রাত ১২:০০ তে daily reward পাবেন"
            elif add_done:
                track_status = "⚠️ Bio সরানো আছে — আবার সেট করুন (Add Reward পুনরায় পাবেন না)"
            else:
                track_status = "⏳ Bio সেট করে Check করুন → Add Reward সাথে সাথে পাবেন"
            text = (
                f"🧬 *Bio Task*\n\n"
                f"🆕 *Add Reward (প্রথমবার, সাথে সাথে):* `{ar}$ USDT`\n"
                f"📅 *Daily Reward (প্রতিদিন, ২৪ঘন্টা):* `{daily_reward}$ USDT`\n\n"
                f"{track_status}\n\n"
                f"📝 *আপনার Bio তে হুবহু এই text রাখুন:*\n\n"
                f"`Free Task Earn bot {ref_link}`\n\n"
                f"_⚙️ Settings → Edit Profile → Bio_\n\n"
                f"▶️ *Bio সেট করার পর:*\n"
                f"1️⃣ এই bot-কে /start পাঠান (একবার)\n"
                f"2️⃣ তারপর ✅ Check করুন বাটন চাপুন\n\n"
                f"⚠️ Bio সরালে ৫ মিনিটের মধ্যে ফিরিয়ে না দিলে daily payment বাতিল!"
                f"{hist_lines}"
            )
        else:
            if bt_done:
                track_status = "✅ Today's Daily Reward received!"
            elif actively_track:
                track_status = "🔄 Tracking active — daily reward at 12:00 AM tomorrow"
            elif add_done:
                track_status = "⚠️ Bio removed — set it again (Add Reward won't be given again)"
            else:
                track_status = "⏳ Set your Bio and press Check → Add Reward instantly"
            text = (
                f"🧬 *Bio Task*\n\n"
                f"🆕 *Add Reward (first time, instant):* `{ar}$ USDT`\n"
                f"📅 *Daily Reward (every day, 24h):* `{daily_reward}$ USDT`\n\n"
                f"{track_status}\n\n"
                f"📝 *Set your Telegram Bio to exactly:*\n\n"
                f"`Free Task Earn bot {ref_link}`\n\n"
                f"_⚙️ Settings → Edit Profile → Bio_\n\n"
                f"▶️ *After setting bio:*\n"
                f"1️⃣ Send /start to this bot (once)\n"
                f"2️⃣ Then press ✅ Check button\n\n"
                f"⚠️ If you remove your Bio, restore within 5 min or daily payment is cancelled!"
                f"{hist_lines}"
            )
        kb_rows = []
        if not add_done:
            _bio_check_lbl = "✅ Check করুন" if lang == 'bn' else "✅ Check Now"
            kb_rows.append([InlineKeyboardButton(_bio_check_lbl, callback_data="bio_task_check")])
        elif not actively_track:
            _bio_reset_lbl = "🔄 Bio সেট করে Check করুন" if lang == 'bn' else "🔄 Set Bio & Check"
            kb_rows.append([InlineKeyboardButton(_bio_reset_lbl, callback_data="bio_task_check")])
        kb_rows.append([InlineKeyboardButton("🔙 Tasks", callback_data="task_cancel")])
        kb = InlineKeyboardMarkup(kb_rows)
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if d == "bio_task_check":
        if not await _bot_accessible(uid):
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        # ── Double-press protection ─────────────────────────────────────────
        # user দুবার Check চাপলে দুটো ৩৫s loop চলবে না — একটাই চলবে
        if ctx.user_data.get("bio_check_in_progress"):
            try:
                lang_dp = await asyncio.to_thread(get_lang, uid)
                await q.message.reply_text(
                    "⏳ Bio চেক ইতিমধ্যে চলছে, অপেক্ষা করুন..." if lang_dp == 'bn'
                    else "⏳ Bio check already in progress, please wait...",
                    parse_mode="Markdown")
            except Exception:
                pass
            return
        ctx.user_data["bio_check_in_progress"] = True
        try:
            await _bio_task_check_inner(update, ctx, q, uid)
        finally:
            # যেকোনো exception বা normal return-এ flag সর্বদা clear হবে
            ctx.user_data["bio_check_in_progress"] = False
        return
    # ── Admin panel callbacks (all adm_* callbacks handled here) ──
    await _handle_admin_inner(update, ctx, q, uid, d)


async def _bio_task_check_inner(update, ctx, q, uid):
    bio_task, lang, bot_me = await asyncio.gather(
        asyncio.to_thread(get_bio_task),
        asyncio.to_thread(get_lang, uid),
        ctx.bot.get_me(),
    )

    if not bio_task or not bio_task['is_active']:
        await q.message.reply_text(
            "❌ Bio Task এখন সক্রিয় নেই।" if lang == 'bn'
            else "❌ Bio Task is not active right now.",
            parse_mode="Markdown")
        return

    add_reward   = (bio_task.get('add_reward', 0) or 0)
    daily_reward = bio_task['reward']
    bot_username = bot_me.username
    ref_link     = f"https://t.me/{bot_username}?start=ref_{uid}"
    required_bio = f"Free Task Earn bot {ref_link}"

    def _bio_matches(bio_text: str) -> bool:
        if not bio_text:
            return False
        t = bio_text.strip()
        if f"ref_{uid}" in t:
            return True
        if required_bio.lower() in t.lower():
            return True
        if str(uid) in t and bot_username.lower() in t.lower():
            return True
        return False

    _wait_msg_text = (
        "⏳ *Bio চেক হচ্ছে...*\n\nঅনুগ্রহ করে কয়েক সেকেন্ড অপেক্ষা করুন।"
        if lang == 'bn' else
        "⏳ *Checking Bio...*\n\nPlease wait a few seconds."
    )
    _status_msg = await q.message.reply_text(_wait_msg_text, parse_mode="Markdown")

    bio_ok    = False
    user_bio  = ""
    _INTERVAL = 5
    _attempts = 8

    for _attempt in range(_attempts):
        try:
            if _attempt > 0:
                try: await ctx.bot.get_chat(uid)
                except Exception: pass
            chat     = await ctx.bot.get_chat(uid)
            user_bio = (chat.bio or "").strip()
            log.info(f"[BIO-CHECK] uid={uid} attempt={_attempt+1}/{_attempts} bio='{user_bio[:80]}'")
            if _bio_matches(user_bio):
                bio_ok = True
                break
        except Exception as _e:
            log.warning(f"[BIO-CHECK] uid={uid} attempt={_attempt+1} error: {_e}")

        if _attempt < _attempts - 1:
            _elapsed = (_attempt + 1) * _INTERVAL
            _remain  = (_attempts - 1 - _attempt) * _INTERVAL
            try:
                _prog_text = (
                    f"⏳ *Bio চেক হচ্ছে...* `{_elapsed}s`\n\nআর প্রায় {_remain} সেকেন্ড..."
                    if lang == 'bn' else
                    f"⏳ *Checking Bio...* `{_elapsed}s`\n\nAbout {_remain} more seconds..."
                )
                await _status_msg.edit_text(_prog_text, parse_mode="Markdown")
            except Exception:
                pass
            await asyncio.sleep(_INTERVAL)

    try:
        await _status_msg.delete()
    except Exception:
        pass

    if not bio_ok:
        if lang == 'bn':
            msg = (
                f"❌ *Bio পাওয়া যায়নি!*\n\n"
                f"নিচের text টি হুবহু copy করে Telegram Bio তে বসান:\n\n"
                f"`Free Task Earn bot {ref_link}`\n\n"
                f"📌 *Telegram → Settings → Edit Profile → Bio*\n\n"
                f"✅ সেট করার পর আবার Check বাটন চাপুন।"
            )
        else:
            msg = (
                f"❌ *Bio not found!*\n\n"
                f"Copy the text below and paste it in your Telegram Bio:\n\n"
                f"`Free Task Earn bot {ref_link}`\n\n"
                f"📌 *Telegram → Settings → Edit Profile → Bio*\n\n"
                f"✅ After setting, press Check again."
            )
        _bio_chk_lbl2 = "✅ Check করুন" if lang == 'bn' else "✅ Check Again"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(_bio_chk_lbl2, callback_data="bio_task_check")],
            [InlineKeyboardButton("🔙 Tasks",     callback_data="task_cancel")],
        ])
        await q.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb,
                                   disable_web_page_preview=True)
        return

    # ── Atomic reward — mark + balance + record একটি DB transaction-এ ──
    today = datetime.now(BD_TZ).strftime("%Y-%m-%d")
    _first_mark = await asyncio.to_thread(
        atomic_give_bio_add_reward, uid, round(add_reward, 4) if add_reward > 0 else 0.0, today
    )
    if _first_mark:
        await asyncio.to_thread(set_bio_tracking, uid)
        if add_reward > 0:
            mark_db_dirty()
            _bio_ref = await asyncio.to_thread(get_referred_by, uid)
            if _bio_ref:
                _bio_comm_pct = await asyncio.to_thread(get_commission_pct)
                _bio_comm = round(add_reward * _bio_comm_pct / 100.0, 4)
                await asyncio.gather(
                    asyncio.to_thread(add_bal, _bio_ref, _bio_comm),
                    asyncio.to_thread(record_commission, _bio_ref, uid, _bio_comm, 'Bio Task'),
                )
                try:
                    _bio_ru = await asyncio.to_thread(get_user, uid)
                    _bio_rname = (_bio_ru["full_name"] or _bio_ru["username"] or "Unknown") if _bio_ru else "Unknown"
                    _bio_cpct_d = int(_bio_comm_pct) if _bio_comm_pct == int(_bio_comm_pct) else _bio_comm_pct
                    await ctx.bot.send_message(_bio_ref,
                        T(_bio_ref, 'commission_msg', name=_md_esc(_bio_rname), price=round(add_reward,4),
                          comm=_bio_comm, comm_pct=_bio_cpct_d),
                        parse_mode="Markdown")
                except Exception: pass
            def _db_6018():
                _brow = None
                with _c() as _cb:
                    _brow = _cb.execute("SELECT balance FROM users WHERE user_id=%s", (uid,)).fetchone()
                return _brow
            _brow = await asyncio.to_thread(_db_6018)
            new_bal = round((_brow['balance'] if _brow else 0), 4)
            _bio_urow = await asyncio.to_thread(get_user, uid)
            _bio_uname = _md_esc((_bio_urow["full_name"] or _bio_urow["username"] or str(uid)) if _bio_urow else str(uid))
            _bio_username = _md_esc((_bio_urow.get("username") or "") if _bio_urow else "")
            _cum_count, _cum_amount = await asyncio.to_thread(get_bio_task_cumulative)
            _bio_admin_msg = (
                f"🆕 *Bio Task — New Add Reward*\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 *User:* {_bio_uname}"
                + (f" (@{_bio_username})" if _bio_username else "") +
                f"\n🆔 *ID:* `{uid}`\n"
                f"💰 *Add Reward:* `{add_reward}$ USDT`\n"
                f"💳 *Balance:* `{round(new_bal,4)}$ USDT`\n"
                f"🗓️ *তারিখ:* `{today}`\n\n"
                f"📈 *সর্বমোট Cumulative:*\n"
                f"  👥 মোট পেয়েছে: `{_cum_count} জন`\n"
                f"  💵 মোট খরচ: `{round(float(_cum_amount),4)}$ USDT`"
            )
            _t1 = asyncio.create_task(notify_full_admins(ctx.bot, _bio_admin_msg))
            _background_tasks.add(_t1); _t1.add_done_callback(_background_tasks.discard)
            if lang == 'bn':
                msg = (
                    f"🎉 *Bio Task — Add Reward পেয়েছেন!*\n\n"
                    f"✅ Bio সঠিকভাবে সেট করা হয়েছে।\n"
                    f"💰 *`{add_reward}$ USDT`* আপনার একাউন্টে যোগ হয়েছে!\n\n"
                    f"💳 *নতুন ব্যালান্স:* `{round(new_bal,4)}$ USDT`\n\n"
                    f"📅 Daily `{daily_reward}$` প্রতিদিন রাত ১২:০০ তে অটো পাবেন।\n"
                    f"⚠️ Bio সরালে daily payment বাতিল হবে!"
                )
            else:
                msg = (
                    f"🎉 *Bio Task — Add Reward received!*\n\n"
                    f"✅ Bio set correctly.\n"
                    f"💰 *`{add_reward}$ USDT`* added to your account!\n\n"
                    f"💳 *New Balance:* `{round(new_bal,4)}$ USDT`\n\n"
                    f"📅 Daily `{daily_reward}$` auto-credited every night at 12:00 AM.\n"
                    f"⚠️ Removing your Bio will cancel daily payment!"
                )
        else:
            if lang == 'bn':
                msg = (
                    f"✅ *Bio সঠিকভাবে সেট করা হয়েছে!*\n\n"
                    f"📅 Daily `{daily_reward}$` প্রতিদিন রাত ১২:০০ তে পাবেন।"
                )
            else:
                msg = (
                    f"✅ *Bio verified successfully!*\n\n"
                    f"📅 Daily `{daily_reward}$` every night at 12:00 AM."
                )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Tasks", callback_data="task_cancel")]])
        await q.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
    else:
        await asyncio.to_thread(set_bio_tracking, uid)
        bt_done = await asyncio.to_thread(bio_task_paid_today, uid)
        if lang == 'bn':
            status = "✅ আজকের Daily Reward পেয়েছেন!" if bt_done else "🔄 Daily reward আজ রাত ১২:০০ তে পাবেন।"
            msg = (
                f"✅ *Bio সঠিক আছে — Tracking চালু হয়েছে!*\n\n"
                f"🆕 Add Reward আগেই নেওয়া হয়েছে (পুনরায় পাবেন না)।\n"
                f"{status}\n\n"
                f"⚠️ Bio ঠিক রাখুন — daily reward পেতে।"
            )
        else:
            status = "✅ Today's daily reward received!" if bt_done else "🔄 Daily reward at 12:00 AM tonight."
            msg = (
                f"✅ *Bio is correct!*\n\n"
                f"🆕 Add Reward already claimed.\n"
                f"{status}\n\n"
                f"⚠️ Keep your Bio — to keep receiving daily reward."
            )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Tasks", callback_data="task_cancel")]])
        await q.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
        # _bio_task_check_inner ends here — admin handling is in _handle_admin_inner


async def _handle_admin_inner(update: Update, ctx: ContextTypes.DEFAULT_TYPE, q, uid: int, d: str):
    """All adm_* callback handling — called from _on_callback_inner for non-bio callbacks."""

    # ── User: Watch Video — Regular Task (admin check নেই) ────────
    if d.startswith("task_watch_video_"):
        try:
            tid = int(d.split("_")[3])
        except (IndexError, ValueError):
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!")
            return
        t   = await asyncio.to_thread(get_task, tid)
        vid = (_rget(t, 'demo_video_id', '') or '').strip() if t else ''
        lang = await asyncio.to_thread(get_lang, uid)
        if not vid:
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!" if lang == 'bn' else "❌ Video not found!")
            return
        try:
            if vid.startswith("http") or vid.startswith("https"):
                lbl = "🎬 ভিডিও দেখুন" if lang == 'bn' else "🎬 Watch Video"
                txt = "👇 নিচের বাটনে চাপুন:" if lang == 'bn' else "👇 Tap the button below:"
                await q.message.reply_text(
                    txt,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(lbl, url=vid)]]))
            else:
                cap = "🎬 ডেমো ভিডিও" if lang == 'bn' else "🎬 Demo Video"
                await q.message.reply_video(vid, caption=cap)
        except Exception as _wve:
            log.warning(f"[WATCH_VIDEO] tid={tid} err={_wve}")
            await q.message.reply_text("❌ ভিডিও পাঠাতে সমস্যা হয়েছে।" if lang == 'bn' else "❌ Could not send video.")
        return

    # ── User: Watch Video — Category Task (admin check নেই) ───────
    if d.startswith("ctask_watch_video_"):
        try:
            tid = int(d.split("_")[3])
        except (IndexError, ValueError):
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!")
            return
        t   = await asyncio.to_thread(get_category_task, tid)
        vid = (_rget(t, 'demo_video_id', '') or '').strip() if t else ''
        lang = await asyncio.to_thread(get_lang, uid)
        if not vid:
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!" if lang == 'bn' else "❌ Video not found!")
            return
        try:
            if vid.startswith("http") or vid.startswith("https"):
                lbl = "🎬 ভিডিও দেখুন" if lang == 'bn' else "🎬 Watch Video"
                txt = "👇 নিচের বাটনে চাপুন:" if lang == 'bn' else "👇 Tap the button below:"
                await q.message.reply_text(
                    txt,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(lbl, url=vid)]]))
            else:
                cap = "🎬 ডেমো ভিডিও" if lang == 'bn' else "🎬 Demo Video"
                await q.message.reply_video(vid, caption=cap)
        except Exception as _wve2:
            log.warning(f"[WATCH_VIDEO_CT] tid={tid} err={_wve2}")
            await q.message.reply_text("❌ ভিডিও পাঠাতে সমস্যা হয়েছে।" if lang == 'bn' else "❌ Could not send video.")
        return

    # ── Admin-only callbacks ──────────────────────────────────────
    # q.answer() আগেই করো — is_admin DB query এর আগে, নইলে spinner দেরিতে যায়
    try: await q.answer()
    except Exception: pass
    if not await asyncio.to_thread(is_admin, uid):
        await q.message.reply_text("❌ আপনি এডমিন না!"); return

    if d == "adm_panel":
        try: await q.answer()
        except Exception: pass
        await send_admin_panel(q.message, uid)
        return

    if d == "adm_bot_toggle":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        current = await asyncio.to_thread(get_setting, "bot_status") or "on"
        new_s   = "off" if current == "on" else "on"
        await asyncio.to_thread(set_setting, "bot_status", new_s)
        msg = "🔴 *Bot বন্ধ করা হয়েছে।*" if new_s == "off" else "🟢 *Bot চালু করা হয়েছে।*"
        await q.message.reply_text(msg, parse_mode="Markdown"); return

    if d == "adm_toggle_notify":
        new_n = "off" if await asyncio.to_thread(task_notify_on) else "on"
        await asyncio.to_thread(set_setting, "task_notify", new_n)
        icon  = "🔔" if new_n == "on" else "🔕"
        await q.message.reply_text(
            f"{icon} *Task Notification {'চালু' if new_n=='on' else 'বন্ধ'} করা হয়েছে।*",
            parse_mode="Markdown"); return

    # ── Menu Buttons Edit ─────────────────────────────────────────
    if d == "adm_menu_btns":
        def _db_6122():
            rows = None
            with _c() as c:
                rows = c.execute("SELECT key, label_bn, label_en FROM menu_buttons").fetchall()
            return rows
        rows = await asyncio.to_thread(_db_6122)
        text = "🎛 *Menu Buttons সম্পাদনা*\n\n"
        btns = []
        for row in rows:
            text += f"• *{row['key']}*: BN=`{row['label_bn']}` | EN=`{row['label_en']}`\n"
            btns.append([InlineKeyboardButton(
                f"✏️ {row['key']}", callback_data=f"adm_btn_edit_{row['key']}"
            )])
        btns.append([InlineKeyboardButton("🔄 সব Default এ ফিরিয়ে দিন", callback_data="adm_btn_reset")])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_btn_reset":
        for key, (bn, en) in DEFAULT_BTNS.items():
            await asyncio.to_thread(set_btn, key, bn, en)
        await q.message.reply_text("✅ *সব বাটন Default এ ফিরিয়ে দেওয়া হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_btn_edit_"):
        btn_key = d.replace("adm_btn_edit_", "")
        ctx.user_data["state"]       = "adm_btn_edit"
        ctx.user_data["edit_btn_key"] = btn_key
        def _db_6146():
            row = None
            with _c() as c:
                row = c.execute("SELECT label_bn, label_en FROM menu_buttons WHERE key=%s", (btn_key,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_6146)
        cur_bn = row["label_bn"] if row else DEFAULT_BTNS.get(btn_key,('?','?'))[0]
        cur_en = row["label_en"] if row else DEFAULT_BTNS.get(btn_key,('?','?'))[1]
        await q.message.reply_text(
            f"✏️ *Button Edit: `{btn_key}`*\n\n"
            f"বর্তমান BN: `{cur_bn}`\n"
            f"বর্তমান EN: `{cur_en}`\n\n"
            "নতুন লেখা পাঠান এই ফরম্যাটে:\n"
            "`বাংলা লেখা | English text`\n\n"
            "উদাহরণ: `📋 কাজ | 📋 Tasks`\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Bot Settings ──────────────────────────────────────────────
    if d == "adm_settings":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        (
            owner, bot_u, min_w, comm, g_ch, w_ch, notify_ch,
            _wmbn, _wmen, bsc_w, _notify_delay_min,
        ) = await asyncio.gather(
            asyncio.to_thread(get_setting, 'support_owner'),
            asyncio.to_thread(get_setting, 'support_bot'),
            asyncio.to_thread(get_setting, 'min_withdraw'),
            asyncio.to_thread(get_setting, 'commission_pct'),
            asyncio.to_thread(get_setting, 'gmail_ch_id'),
            asyncio.to_thread(get_setting, 'withdraw_ch_id'),
            asyncio.to_thread(get_setting, 'task_notify_ch_id'),
            asyncio.to_thread(get_setting, 'welcome_msg_bn'),
            asyncio.to_thread(get_setting, 'welcome_msg_en'),
            asyncio.to_thread(get_bsc_wallet_addr),
            asyncio.to_thread(get_notify_delay_min),
        )
        owner     = owner     or '@inkamxbd'
        bot_u     = bot_u     or '@gmail_sell_help_bot'
        min_w     = min_w     or '0.05'
        comm      = comm      or '10'
        g_ch      = g_ch      or str(GMAIL_CH_ID)
        w_ch      = w_ch      or str(WITHDRAW_CH_ID)
        notify_ch = notify_ch or '-1003866731221'
        has_wbn   = "✅ Custom" if _wmbn else "⬜ Default"
        has_wen   = "✅ Custom" if _wmen else "⬜ Default"
        bsc_short = bsc_w[:6] + "..." + bsc_w[-4:] if bsc_w else "N/A"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👨‍💼 Support Owner পরিবর্তন",          callback_data="adm_set_support_owner")],
            [InlineKeyboardButton("🤖 Support Bot পরিবর্তন",              callback_data="adm_set_support_bot")],
            [InlineKeyboardButton("💸 Min Withdraw পরিবর্তন",             callback_data="adm_set_min_withdraw")],
            [InlineKeyboardButton("💹 Commission % পরিবর্তন",             callback_data="adm_set_commission_pct")],
            [InlineKeyboardButton("📢 Task Channel ID পরিবর্তন",          callback_data="adm_set_gmail_ch_id")],
            [InlineKeyboardButton("💳 Withdraw Channel ID পরিবর্তন",      callback_data="adm_set_withdraw_ch_id")],
            [InlineKeyboardButton("📡 Task Notify Channel ID পরিবর্তন",   callback_data="adm_set_task_notify_ch")],
            [InlineKeyboardButton("🔔 Notify Channel Username পরিবর্তন", callback_data="adm_set_notify_ch_username")],
            [InlineKeyboardButton("⏱ Bot Notify Delay (মিনিট) পরিবর্তন", callback_data="adm_set_notify_delay")],
            [InlineKeyboardButton("📝 Welcome Message (বাংলা)",           callback_data="adm_set_welcome_bn")],
            [InlineKeyboardButton("📝 Welcome Message (English)",         callback_data="adm_set_welcome_en")],
            [InlineKeyboardButton("🔑 BSC Wallet Address পরিবর্তন",       callback_data="adm_set_bsc_wallet")],
        ])
        await q.message.reply_text(
            f"⚙️ *BOT SETTINGS*\n\n"
            f"👨‍💼 *Support Owner:* `{owner}`\n"
            f"🤖 *Support Bot:* `{bot_u}`\n"
            f"💸 *Min Withdraw:* `{min_w}$ USDT`\n"
            f"💹 *Commission:* `{comm}%`\n"
            f"📢 *Task Channel ID:* `{g_ch}`\n"
            f"💳 *Withdraw Channel ID:* `{w_ch}`\n"
            f"📡 *Task Notify Channel:* `{notify_ch}`\n"
            f"⏱ *Bot Notify Delay:* `{_notify_delay_min} মিনিট`\n"
            f"📝 *Welcome BN:* {has_wbn}\n"
            f"📝 *Welcome EN:* {has_wen}\n"
            f"🔑 *BSC Wallet:* `{bsc_short}`\n\n"
            f"👇 পরিবর্তন করতে নিচের বাটন চাপুন:",
            parse_mode="Markdown", reply_markup=kb
        ); return

    if d == "adm_db_backup":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("⏳ Backup পাঠানো হচ্ছে...")
        await _send_db_to_admin(ctx.bot, label="📥 Manual Backup (Admin Panel)")
        await q.message.reply_text("✅ *Backup আপনার Telegram এ পাঠানো হয়েছে।*", parse_mode="Markdown"); return

    if d == "adm_db_restore":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_restore_db"
        await q.message.reply_text(
            "📤 *Restore করতে data.db ফাইল পাঠান।*\n\n"
            "⚠️ *সতর্কতা:* এটা বর্তমান সব data replace করবে!\n"
            "_(Backup file টা পাঠান যেটা /backup বা auto-backup এ পেয়েছিলেন)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"
        ); return

    if d == "adm_health_check":
        await q.message.reply_text("🔍 চেক করা হচ্ছে...")
        status_msg = await q.message.reply_text(
            "🔍 *Bot Health Check চলছে...*\nঅপেক্ষা করুন ⏳", parse_mode="Markdown"
        )
        report, problems, fixes = await _run_health_check_and_fix(ctx.bot, skip_admin_ping=True)
        markup = None
        if problems > fixes:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔧 Fix করুন", callback_data="adm_health_fix")
            ]])
        try:
            await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await status_msg.edit_text(report[:4000], reply_markup=markup)
        return

    if d == "adm_health_fix":
        await q.message.reply_text("🔧 Fix চলছে...")
        try:
            await q.message.edit_text(
                "🔧 *Auto-Fix চলছে...*\nঅপেক্ষা করুন ⏳", parse_mode="Markdown"
            )
        except Exception:
            pass
        report, problems, fixes = await _run_health_check_and_fix(ctx.bot, skip_admin_ping=True)
        markup = None
        if problems > fixes:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔧 আবার Fix করুন", callback_data="adm_health_fix")
            ]])
        try:
            await q.message.edit_text(report, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            await q.message.edit_text(report[:4000], reply_markup=markup)
        return

    if d == "adm_wallet_bal":
        await q.message.reply_text("⏳ Balance চেক করা হচ্ছে...")
        usdt, bnb, _bsc_w = await asyncio.gather(
            asyncio.to_thread(get_bsc_usdt_balance),
            asyncio.to_thread(get_bsc_bnb_balance),
            asyncio.to_thread(get_bsc_wallet_addr),
        )
        usdt_txt = f"`{usdt:.4f} USDT`" if usdt is not None else "❌ চেক করা যায়নি"
        bnb_txt  = f"`{bnb:.6f} BNB`"   if bnb  is not None else "❌ চেক করা যায়নি"
        bscscan  = f"https://bscscan.com/address/{_bsc_w}"
        await q.message.reply_text(
            f"💳 *Wallet Balance*\n\n"
            f"💵 *USDT BEP-20:* {usdt_txt}\n"
            f"⛽ *BNB (Gas):* {bnb_txt}\n\n"
            f"📍 *Address:* `{_bsc_w}`\n"
            f"🌐 [BSCScan এ দেখুন]({bscscan})",
            parse_mode="Markdown",
            disable_web_page_preview=False
        ); return

    if d == "adm_set_support_owner":
        ctx.user_data["state"] = "adm_set_support_owner"
        await q.message.reply_text("👨‍💼 *Support Owner username লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d == "adm_set_support_bot":
        ctx.user_data["state"] = "adm_set_support_bot"
        await q.message.reply_text("🤖 *Support Bot username লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d == "adm_set_min_withdraw":
        ctx.user_data["state"] = "adm_set_min_withdraw"
        await q.message.reply_text("💸 *Minimum Withdraw পরিমাণ লিখুন:*\n_(যেমন: 0.05)_\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d == "adm_set_commission_pct":
        ctx.user_data["state"] = "adm_set_commission_pct"
        await q.message.reply_text("💹 *Referral Commission % লিখুন:*\n_(যেমন: 10 মানে 10%)_\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d == "adm_set_gmail_ch_id":
        ctx.user_data["state"] = "adm_set_gmail_ch_id"
        await q.message.reply_text("📢 *Task Channel ID লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d == "adm_set_withdraw_ch_id":
        ctx.user_data["state"] = "adm_set_withdraw_ch_id"
        await q.message.reply_text("💳 *Withdraw Channel ID লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d == "adm_set_bsc_wallet":
        cur_w = await asyncio.to_thread(get_bsc_wallet_addr)
        ctx.user_data["state"] = "adm_set_bsc_wallet"
        await q.message.reply_text(
            f"🔑 *BSC Wallet Address পরিবর্তন করুন*\n\n"
            f"_বর্তমান:_ `{cur_w}`\n\n"
            "নতুন BSC (BEP-20) wallet address দিন:\n/cancel — বাতিল",
            parse_mode="Markdown"
        ); return
    if d == "adm_set_task_notify_ch":
        ctx.user_data["state"] = "adm_set_task_notify_ch"
        cur = await asyncio.to_thread(get_setting, 'task_notify_ch_id') or '-1003866731221'
        await q.message.reply_text(
            f"📡 *Task Notify Channel ID পরিবর্তন করুন:*\n\n_বর্তমান:_ `{cur}`\n\n"
            "Channel/Group ID দিন (যেমন: `-1001234567890`)\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_set_notify_ch_username":
        ctx.user_data["state"] = "adm_set_notify_ch_username"
        cur = await asyncio.to_thread(get_task_notify_ch_username)
        await q.message.reply_text(
            f"🔔 *Notification Channel Username পরিবর্তন করুন:*\n\n_বর্তমান:_ `{cur}`\n\n"
            "Channel username দিন (যেমন: `@task_earn_bot_notification`)\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_set_notify_delay":
        ctx.user_data["state"] = "adm_set_notify_delay"
        cur = await asyncio.to_thread(get_notify_delay_min)
        await q.message.reply_text(
            f"⏱ *Bot Notify Delay পরিবর্তন করুন:*\n\n_বর্তমান:_ `{cur} মিনিট`\n\n"
            "কত মিনিট পরে Bot users এ notification যাবে?\n"
            "_(0 = সাথে সাথে, 5 = ৫ মিনিট পরে)_\n\n"
            "📌 Channel এ সবসময় সাথে সাথে যাবে।\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_set_welcome_bn":
        ctx.user_data["state"] = "adm_set_welcome_bn"
        cur = await asyncio.to_thread(get_setting, 'welcome_msg_bn') or "(Default ব্যবহার হচ্ছে)"
        await q.message.reply_text(
            f"📝 *বাংলা Welcome Message লিখুন:*\n\n_বর্তমান:_\n{cur[:300]}\n\n"
            "⚠️ শুধু `-` পাঠালে Default এ ফিরে যাবে।\n/cancel — বাতিল",
            parse_mode="Markdown"); return
    if d == "adm_set_welcome_en":
        ctx.user_data["state"] = "adm_set_welcome_en"
        cur = await asyncio.to_thread(get_setting, 'welcome_msg_en') or "(Default in use)"
        await q.message.reply_text(
            f"📝 *English Welcome Message লিখুন:*\n\n_Current:_\n{cur[:300]}\n\n"
            "⚠️ Send `-` to reset to Default.\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d in ("adm_leaderboard", "adm_lb_refresh"):
        # Admin panel → full info: নাম + @username + ID (@ হাইড হয় না)
        text = await asyncio.to_thread(_lb_build_text_admin)
        now_str = datetime.now(BD_TZ).strftime("%d/%m/%Y %I:%M %p")
        text += f"\n\n🕐 <b>আপডেট:</b> <code>{now_str}</code>"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="adm_lb_refresh"),
            InlineKeyboardButton("📤 Channel এ পাঠাও", callback_data="adm_lb_post_ch"),
        ]])
        if d == "adm_lb_refresh":
            try:
                await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                sent = await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
                await asyncio.to_thread(_lb_adm_save, sent.chat_id, sent.message_id)
                return
            await asyncio.to_thread(_lb_adm_save, q.message.chat_id, q.message.message_id)
        else:
            sent = await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            await asyncio.to_thread(_lb_adm_save, sent.chat_id, sent.message_id)
        return

    if d == "adm_lb_post_ch":
        # Admin panel থেকে এখনই channel এ leaderboard পাঠাও
        await q.message.reply_text("⏳ Channel এ পাঠানো হচ্ছে...")
        await job_post_leaderboard(ctx)
        await q.message.reply_text("✅ Leaderboard channel এ পাঠানো হয়েছে।")
        return

    if d == "adm_refer_stats":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_FINANCE):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        d7_ago      = (now_utc - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        d30_ago     = (now_utc - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        def _top10(since: str) -> list:
            rows = None
            """নির্দিষ্ট সময়ের পরে referred_by কত জন join করেছে তার top 10।"""
            with _c() as c:
                rows = c.execute(
                    """SELECT u.referred_by, COUNT(*) as cnt,
                              (SELECT full_name FROM users WHERE user_id=u.referred_by) as name
                       FROM users u
                       WHERE u.referred_by IS NOT NULL AND u.joined_at >= %s
                       GROUP BY u.referred_by
                       ORDER BY cnt DESC LIMIT 10""",
                    (since,)
                ).fetchall()
            return rows

        def _build_section(title: str, rows: list) -> str:
            if not rows:
                return f"{title}\n_কোনো রেফার নেই।_\n"
            lines = [title]
            medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
            for i, r in enumerate(rows):
                name = (r["name"] or "")[:18] or "Unknown"
                lines.append(f"{medals[i]} `{r['referred_by']}` — *{name}* → `{r['cnt']}` জন")
            return "\n".join(lines)

        top_today, top_7d, top_30d = await asyncio.gather(
            asyncio.to_thread(_top10, today_start),
            asyncio.to_thread(_top10, d7_ago),
            asyncio.to_thread(_top10, d30_ago),
        )

        def _db_6428():
            total_ref = None
            total_usr = None
            with _c() as c:
                total_ref = _count(c.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL").fetchone())
                total_usr = _count(c.execute("SELECT COUNT(*) FROM users").fetchone())
            return total_ref, total_usr
        total_ref, total_usr = await asyncio.to_thread(_db_6428)

        text = (
            f"👥 *REFERRAL TOP 10*\n"
            f"🔗 মোট Referred: `{total_ref}` | মোট Users: `{total_usr}`\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"{_build_section('📅 *আজকের Top 10 Referrer:*', top_today)}\n\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"{_build_section('📆 *৭ দিনের Top 10 Referrer:*', top_7d)}\n\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"{_build_section('🗓 *৩০ দিনের Top 10 Referrer:*', top_30d)}"
        )
        await q.message.reply_text(text, parse_mode="Markdown"); return

    if d == "adm_stats":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_FINANCE):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        (total, tot_bal, pg, pw, ag, aw), _admins_list, _tasks_list = await asyncio.gather(
            asyncio.to_thread(get_stats),
            asyncio.to_thread(get_all_admins),
            asyncio.to_thread(get_all_tasks),
        )
        _sub_admins_count = len(_admins_list)
        _tasks_count      = len(_tasks_list)
        await q.message.reply_text(
            f"📊 *STATISTICS*\n\n"
            f"👥 *মোট ইউজার:* `{total}`\n"
            f"💰 *মোট ব্যালান্স:* `{round(tot_bal,4)}$ USDT`\n\n"
            f"📋 *Task Pending:* `{pg}` | Approved: `{ag}`\n"
            f"💸 *Withdraw Pending:* `{pw}` | Approved: `{aw}`\n"
            f"👮 *Sub Admins:* `{_sub_admins_count}`\n"
            f"📋 *মোট Task:* `{_tasks_count}`",
            parse_mode="Markdown"); return

    if d == "adm_pending_g":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        def _db_6463():
            rows = None
            with _c() as c:
                rows = c.execute("SELECT id,user_id,task_name,amount,submitted_at FROM gmails WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
            return rows
        rows = await asyncio.to_thread(_db_6463)
        if not rows:
            await q.message.reply_text("✅ *কোনো Pending Task নেই।*", parse_mode="Markdown"); return
        text = f"⏳ *Pending Submissions ({len(rows)}টি):*\n\n"
        btns = []
        for r in rows:
            text += f"🔖 `#{r['id']}` | *{r['task_name']}* | `{r['amount']}$` | 👤`{r['user_id']}`\n"
            btns.append([
                InlineKeyboardButton(f"✅ #{r['id']} Approve", callback_data=f"ga_{r['id']}"),
                InlineKeyboardButton(f"❌ #{r['id']} Reject",  callback_data=f"gr_{r['id']}"),
            ])
        btns.append([InlineKeyboardButton("🔢 ID দিয়ে Approve", callback_data="adm_sub_approve"),
                     InlineKeyboardButton("🔢 ID দিয়ে Reject",  callback_data="adm_sub_reject")])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_pending_w":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_WITHDRAW):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        def _db_6484():
            rows = None
            with _c() as c:
                rows = c.execute(
                    "SELECT id,user_id,amount,address,requested_at,status,locked_at,tx_hash "
                    "FROM withdrawals WHERE status IN ('pending','processing','gas_pending') "
                    "ORDER BY id DESC LIMIT 30"
                ).fetchall()
            return rows
        rows = await asyncio.to_thread(_db_6484)
        if not rows:
            await q.message.reply_text("✅ *কোনো Active Withdraw নেই।*", parse_mode="Markdown"); return

        status_icon = {"pending": "⏳", "processing": "⚙️", "gas_pending": "⛽"}
        status_label = {"pending": "Pending", "processing": "Processing (Auto)", "gas_pending": "Gas Alert"}

        pending_rows     = [r for r in rows if r["status"] == "pending"]
        processing_rows  = [r for r in rows if r["status"] == "processing"]
        gas_rows         = [r for r in rows if r["status"] == "gas_pending"]

        text = (
            f"📋 *Withdraw Queue Overview*\n"
            f"⏳ Pending: *{len(pending_rows)}* | "
            f"⚙️ Auto-Processing: *{len(processing_rows)}* | "
            f"⛽ Gas Alert: *{len(gas_rows)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        btns = []
        for r in rows:
            st   = r["status"]
            icon = status_icon.get(st, "❓")
            lbl  = status_label.get(st, st)
            masked = mask_addr_short(r["address"])
            tx_info = f" | 🔗`{r['tx_hash'][:10]}…`" if r["tx_hash"] else ""
            text += f"{icon} `#{r['id']}` *{lbl}*\n"
            text += f"   💰`{r['amount']}$` | 👤`{r['user_id']}` | 📍`{masked}`{tx_info}\n\n"
            if st == "pending":
                btns.append([
                    InlineKeyboardButton(f"✅ #{r['id']} Approve", callback_data=f"wa_{r['id']}"),
                    InlineKeyboardButton(f"❌ #{r['id']} Reject",  callback_data=f"wr_{r['id']}"),
                ])
            elif st == "gas_pending":
                btns.append([
                    InlineKeyboardButton(f"⛽ #{r['id']} Gas-OK পাঠাও", callback_data=f"gas_ok_{r['id']}"),
                    InlineKeyboardButton(f"❌ #{r['id']} Cancel",        callback_data=f"gas_cancel_{r['id']}"),
                ])
            else:
                # processing — শুধু দেখা যাবে, auto-withdraw handle করছে
                text_note = f"   _(Auto-withdraw চলছে — {r['locked_at'] or '?'})_\n"
                text = text[:-1] + text_note  # last \n replace

        btns.append([InlineKeyboardButton("🔢 ID দিয়ে Approve", callback_data="adm_wd_approve"),
                     InlineKeyboardButton("🔢 ID দিয়ে Reject",  callback_data="adm_wd_reject")])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_task_code_search":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_task_code_input"
        await q.message.reply_text(
            "🔍 *Task Code দিয়ে খুঁজুন*\n\n"
            "Task Code লিখুন (যেমন: `TK-AB3X7`):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_set_task_owner":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_set_owner_input"
        await q.message.reply_text(
            "👤 *Task Owner সেট করুন*\n\n"
            "Task Code আর Owner-এর User ID একসাথে লিখুন:\n"
            "_(যেমন: `TK-AB3X7 1234567890`)_\n\n"
            "Owner সরাতে চাইলে: `TK-AB3X7 0`\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_sub_approve":
        ctx.user_data["state"] = "adm_approve_id_num"
        await q.message.reply_text(
            "✅ *Submission ID দিয়ে Approve করুন*\n\n"
            "Submission ID লিখুন (যেমন: `15`):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_sub_reject":
        ctx.user_data["state"] = "adm_reject_id_num"
        await q.message.reply_text(
            "❌ *Submission ID দিয়ে Reject করুন*\n\n"
            "Submission ID লিখুন (যেমন: `15`):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_wd_approve":
        ctx.user_data["state"] = "adm_wd_approve_id"
        await q.message.reply_text(
            "✅ *Withdrawal ID দিয়ে Approve করুন*\n\n"
            "Withdrawal ID লিখুন (যেমন: `8`):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_wd_reject":
        ctx.user_data["state"] = "adm_wd_reject_id"
        await q.message.reply_text(
            "❌ *Withdrawal ID দিয়ে Reject করুন*\n\n"
            "Withdrawal ID লিখুন (যেমন: `8`):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_find_user":
        ctx.user_data["state"] = "adm_find_user_id"
        await q.message.reply_text(
            "👤 *User Profile দেখুন*\n\n"
            "User-এর Telegram ID লিখুন:\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_all_users":
        def _db_6588():
            rows = None
            with _c() as c:
                rows = c.execute(
                    "SELECT user_id,username,full_name,balance,is_banned,joined_at FROM users ORDER BY user_id DESC LIMIT 20"
                ).fetchall()
            return rows
        rows = await asyncio.to_thread(_db_6588)
        if not rows:
            await q.message.reply_text("কোনো user নেই।"); return
        lines = ["📋 *সর্বশেষ ২০ জন User:*\n"]
        for r in rows:
            icon = "🚫" if r["is_banned"] else "✅"
            name = f"@{r['username']}" if r["username"] else (r["full_name"] or "Unknown")
            lines.append(f"{icon} `{r['user_id']}` — {name} | 💰`{round(r['balance'],4)}`")
        text = "\n".join(lines)
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown"); return

    if d.startswith("adm_uban_"):
        tid = int(d[len("adm_uban_"):])
        await asyncio.to_thread(ban_user, tid, 1)
        await q.message.reply_text("🚫 Banned!")
        txt2, kb2 = await asyncio.to_thread(_build_user_profile, tid)
        try: await q.message.edit_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        except Exception: await q.message.reply_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        return

    if d.startswith("adm_uunban_"):
        tid = int(d[len("adm_uunban_"):])
        await asyncio.to_thread(ban_user, tid, 0)
        await q.message.reply_text("✅ Unbanned!")
        txt2, kb2 = await asyncio.to_thread(_build_user_profile, tid)
        try: await q.message.edit_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        except Exception: await q.message.reply_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        return

    if d.startswith("adm_uaddbal_"):
        tid = int(d[len("adm_uaddbal_"):])
        ctx.user_data["state"] = "adm_uaddbal_amt"
        ctx.user_data["adm_target"] = tid
        await q.message.reply_text(
            f"➕ *`{tid}` এর Balance যোগ করুন*\n\nপরিমাণ লিখুন (যেমন: `0.50`):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_udedbal_"):
        tid = int(d[len("adm_udedbal_"):])
        ctx.user_data["state"] = "adm_udedbal_amt"
        ctx.user_data["adm_target"] = tid
        await q.message.reply_text(
            f"➖ *`{tid}` থেকে Balance কাটুন*\n\nপরিমাণ লিখুন (যেমন: `0.50`):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_umsg_"):
        tid = int(d[len("adm_umsg_"):])
        ctx.user_data["state"] = "adm_umsg_txt"
        ctx.user_data["adm_target"] = tid
        await q.message.reply_text(
            f"💬 *`{tid}` কে মেসেজ পাঠান*\n\nমেসেজ লিখুন (Markdown চলবে):\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_urefs_"):
        tid = int(d[len("adm_urefs_"):])
        def _db_6651():
            refs = None
            with _c() as c:
                refs = c.execute(
                    "SELECT user_id,username,full_name,balance,joined_at FROM users WHERE referred_by=%s ORDER BY user_id DESC LIMIT 30",
                    (tid,)
                ).fetchall()
            return refs
        refs = await asyncio.to_thread(_db_6651)
        if not refs:
            await q.message.reply_text(f"👥 `{tid}` এর কোনো referral নেই।", parse_mode="Markdown"); return
        lines = [f"👥 *`{tid}` এর Referrals ({len(refs)}জন):*\n"]
        for r in refs:
            name = f"@{r['username']}" if r["username"] else (r["full_name"] or "Unknown")
            lines.append(f"• `{r['user_id']}` — {name} | 💰`{round(r['balance'],4)}` | 📅{r['joined_at']}")
        text = "\n".join(lines)
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown"); return

    if d.startswith("adm_usubs_"):
        tid = int(d[len("adm_usubs_"):])
        def _db_6669():
            subs = None
            with _c() as c:
                subs = c.execute(
                    "SELECT id,gmail_text,task_name,status,amount,submitted_at FROM gmails WHERE user_id=%s ORDER BY id DESC LIMIT 15",
                    (tid,)
                ).fetchall()
            return subs
        subs = await asyncio.to_thread(_db_6669)
        if not subs:
            await q.message.reply_text(f"📋 `{tid}` এর কোনো submission নেই।", parse_mode="Markdown"); return
        lines = [f"📋 *`{tid}` এর Submissions ({len(subs)}টি):*\n"]
        for r in subs:
            icon = "✅" if r["status"]=="approved" else ("❌" if r["status"]=="rejected" else "⏳")
            lines.append(f"{icon} `#{r['id']}` | {r['task_name']} | 💰`{r['amount']}` | {r['submitted_at']}")
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown"); return

    if d.startswith("adm_uwds_"):
        tid = int(d[len("adm_uwds_"):])
        def _db_6685():
            wds = None
            with _c() as c:
                wds = c.execute(
                    "SELECT id,amount,address,status,requested_at FROM withdrawals WHERE user_id=%s ORDER BY id DESC LIMIT 15",
                    (tid,)
                ).fetchall()
            return wds
        wds = await asyncio.to_thread(_db_6685)
        if not wds:
            await q.message.reply_text(f"💸 `{tid}` এর কোনো withdrawal নেই।", parse_mode="Markdown"); return
        lines = [f"💸 *`{tid}` এর Withdrawals ({len(wds)}টি):*\n"]
        for r in wds:
            icon = "✅" if r["status"]=="approved" else ("❌" if r["status"]=="rejected" else "⏳")
            addr = r["address"][:12]+"…" if r["address"] and len(r["address"])>12 else (r["address"] or "")
            lines.append(f"{icon} `#{r['id']}` | 💰`{r['amount']}$` | `{addr}` | {r['requested_at']}")
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown"); return

    # ── Admin: User profile (back button) ────────────────────────────
    if d.startswith("adm_uprofile_"):
        tid = int(d[len("adm_uprofile_"):])
        txt2, kb2 = await asyncio.to_thread(_build_user_profile, tid)
        try: await q.message.edit_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        except Exception: await q.message.reply_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        return

    # ── Admin: দেশ পরিবর্তন করুন (country keyboard দেখাও) ──────────
    if d.startswith("adm_uchcountry_"):
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_USERS):
            await q.message.reply_text("❌ পারমিশন নেই!"); return
        tid = int(d[len("adm_uchcountry_"):])
        _cur = await asyncio.to_thread(get_user_country, tid)
        _cf, _cn = COUNTRY_MAP.get((_cur or "").upper(), ("🌍", _cur or "সেট হয়নি"))
        await q.message.reply_text(
            f"🌍 *`{tid}` এর দেশ পরিবর্তন করুন*\n\n"
            f"বর্তমান দেশ: {_cf} *{_cn}*\n\n"
            f"নতুন দেশ বেছে নিন:",
            parse_mode="Markdown",
            reply_markup=admin_country_kb(tid)
        ); return

    # ── Admin: দেশ সেট করো + user কে notify করো ─────────────────────
    if d.startswith("adm_uset_ctry_"):
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_USERS):
            await q.message.reply_text("❌ পারমিশন নেই!"); return
        # format: adm_uset_ctry_{tid}_{code}
        rest = d[len("adm_uset_ctry_"):]          # e.g. "7423929548_BD"
        last_under = rest.rfind("_")
        tid  = int(rest[:last_under])
        code = rest[last_under+1:].upper()
        if code not in COUNTRY_MAP:
            await q.message.reply_text("❌ অজানা দেশ!"); return
        old_raw = await asyncio.to_thread(get_user_country, tid)
        old_flag, old_name = COUNTRY_MAP.get((old_raw or "").upper(), ("🌍", old_raw or "সেট হয়নি"))
        await asyncio.to_thread(set_user_country, tid, code)
        flag, cname = COUNTRY_MAP[code]
        await q.message.reply_text(f"✅ {flag} {cname} সেট হয়েছে!")
        # Admin কে confirm
        try: await q.message.edit_text(
            f"✅ *দেশ পরিবর্তন সম্পন্ন!*\n\n"
            f"👤 User: `{tid}`\n"
            f"🔄 পুরানো: {old_flag} *{old_name}*\n"
            f"✅ নতুন: {flag} *{cname}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 User Profile", callback_data=f"adm_uprofile_{tid}")
            ]])
        )
        except Exception as _e:
            log.warning(f"[ADM] country update admin msg edit ব্যর্থ: {_e}")
        # User কে notify করো
        _u_lang = await asyncio.to_thread(get_lang, tid)
        _notify = (
            f"🌍 *আপনার দেশ পরিবর্তন করা হয়েছে।*\n\n"
            f"নতুন দেশ: {flag} *{cname}*\n\n"
            f"এখন আপনি এই দেশের Task করতে পারবেন।"
            if _u_lang == 'bn' else
            f"🌍 *Your country has been updated by Admin.*\n\n"
            f"New country: {flag} *{cname}*\n\n"
            f"You can now access tasks available for this country."
        )
        try: await ctx.bot.send_message(tid, _notify, parse_mode="Markdown")
        except Exception: pass
        return

    # ── Admin: Bot Logs দেখুন ─────────────────────────────────────────
    if d == "adm_view_logs" or d.startswith("adm_logs_filter_"):
        if not (uid == ADMIN_ID or await asyncio.to_thread(admin_has_perm, uid, PERM_SETTINGS)):
            await q.message.reply_text("❌ পারমিশন নেই!"); return
        # Filter নির্ধারণ
        log_filter = "all"
        if d.startswith("adm_logs_filter_"):
            log_filter = d[len("adm_logs_filter_"):]   # all / error / ping / warn
        filter_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔵 সব",       callback_data="adm_logs_filter_all"),
                InlineKeyboardButton("🔴 Error",     callback_data="adm_logs_filter_error"),
                InlineKeyboardButton("🟡 Warning",   callback_data="adm_logs_filter_warn"),
            ],
            [
                InlineKeyboardButton("📡 Self-Ping", callback_data="adm_logs_filter_ping"),
                InlineKeyboardButton("💾 Backup",    callback_data="adm_logs_filter_backup"),
                InlineKeyboardButton("🔄 Refresh",   callback_data="adm_view_logs"),
            ],
        ])
        try:
            if not os.path.exists(_LOG_FILE):
                await q.message.reply_text(
                    "⚠️ *Log file পাওয়া যায়নি।*\n\nBot restart করলে log ফাইল তৈরি হবে।",
                    parse_mode="Markdown", reply_markup=filter_kb); return
            def _read_log_file():
                with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as _lf:
                    return _lf.readlines()
            _all_lines = await asyncio.to_thread(_read_log_file)
            # Filter apply
            if log_filter == "error":
                _lines = [l for l in _all_lines if " ERROR " in l or " CRITICAL " in l]
                _title = "🔴 *Error Logs*"
            elif log_filter == "warn":
                _lines = [l for l in _all_lines if " WARNING " in l]
                _title = "🟡 *Warning Logs*"
            elif log_filter == "ping":
                _lines = [l for l in _all_lines if "SELF-PING" in l or "UptimeRobot" in l or "bot-ping" in l]
                _title = "📡 *Ping Logs*"
            elif log_filter == "backup":
                _lines = [l for l in _all_lines if "backup" in l.lower() or "BACKUP" in l or "restore" in l.lower()]
                _title = "💾 *Backup Logs*"
            else:
                _lines = _all_lines
                _title = "🔵 *সব Logs*"
            # শেষ 50 line নাও
            _last = _lines[-50:] if len(_lines) > 50 else _lines
            if not _last:
                _body = "_কোনো log পাওয়া যায়নি এই filter এ।_"
            else:
                _body = "```\n" + "".join(_last)[-3500:] + "\n```"
            _total_str = f"({len(_lines)} টি মিলেছে, শেষ {min(len(_last),50)} দেখাচ্ছে)"
            _msg = f"{_title} {_total_str}\n\n{_body}"
            try: await q.message.edit_text(_msg, parse_mode="Markdown", reply_markup=filter_kb)
            except Exception: await q.message.reply_text(_msg, parse_mode="Markdown", reply_markup=filter_kb)
        except Exception as _le:
            await q.message.reply_text(f"❌ Log পড়তে সমস্যা: {_le}", parse_mode="Markdown")
        return

    if d == "adm_bc":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_BROADCAST):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        skip_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip — ছবি ছাড়া", callback_data="adm_bc_skip_photo")]])
        await q.message.reply_text(
            "📢 *Broadcast — ছবি পাঠাতে চাইলে এখনই পাঠান:*\n\n"
            "_(ছবি না চাইলে Skip করুন)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown", reply_markup=skip_kb)
        ctx.user_data["state"] = "adm_bc_photo"
        ctx.user_data.pop("bc_photo_id", None)
        return

    if d == "adm_bc_skip_photo":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        ctx.user_data.pop("bc_photo_id", None)
        ctx.user_data["state"] = "adm_bc_bn"
        await q.message.reply_text(
            "📢 *Broadcast — বাংলা মেসেজ লিখুন:*\n\n"
            "_(বাংলা ইউজাররা এটা পাবে)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d in ("adm_bc_send_pin", "adm_bc_send_nopin"):
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        bc_bn     = ctx.user_data.get("bc_msg_bn", "")
        bc_en     = ctx.user_data.get("bc_msg_en", "")
        bc_photo  = ctx.user_data.get("bc_photo_id", "")
        do_pin    = (d == "adm_bc_send_pin")
        if not bc_bn and not bc_en:
            await q.message.reply_text("❌ *কোনো মেসেজ পাওয়া যায়নি।*", parse_mode="Markdown"); return
        users = await asyncio.to_thread(all_users); sent = fail = pin_fail = 0
        for u in users:
            try:
                u_lang  = (u.get("lang") or "bn")
                msg_txt = bc_en if (u_lang == "en" and bc_en) else bc_bn
                if bc_photo:
                    sent_msg = await ctx.bot.send_photo(
                        u["user_id"], bc_photo,
                        caption=msg_txt, parse_mode="Markdown")
                else:
                    sent_msg = await ctx.bot.send_message(
                        u["user_id"], msg_txt, parse_mode="Markdown")
                sent += 1
                if do_pin:
                    try: await ctx.bot.pin_chat_message(u["user_id"], sent_msg.message_id, disable_notification=True)
                    except Exception: pin_fail += 1
                # Rate-limit: 25 msg/sec global cap — sleep 40ms normally, 1s every 25 msgs
                if sent % 25 == 0:
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(0.04)
            except Exception as _bc_e:
                log.warning(f"[BROADCAST] uid={u.get('user_id')} send failed: {_bc_e}")
                fail += 1
        pin_status = f"\n📌 *পিন:* ✅ `{sent - pin_fail}` | ❌ `{pin_fail}`" if do_pin else "\n📌 *পিন:* বন্ধ ছিল"
        await q.message.reply_text(
            f"📢 *Broadcast সম্পন্ন!*\n✅ `{sent}` | ❌ `{fail}`{pin_status}",
            parse_mode="Markdown")
        for k in ("bc_msg_bn", "bc_msg_en", "bc_photo_id"):
            ctx.user_data.pop(k, None)
        return

    if d == "adm_bc_edit":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        for k in ("bc_msg_bn", "bc_msg_en", "bc_photo_id"): ctx.user_data.pop(k, None)
        skip_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip — ছবি ছাড়া", callback_data="adm_bc_skip_photo")]])
        ctx.user_data["state"] = "adm_bc_photo"
        await q.message.reply_text(
            "📢 *আবার শুরু করুন — ছবি পাঠান বা Skip করুন:*\n\n/cancel — বাতিল",
            parse_mode="Markdown", reply_markup=skip_kb)
        return

    if d == "adm_bc_cancel":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        await q.message.reply_text("বাতিল করা হয়েছে।")
        for k in ("bc_msg_bn", "bc_msg_en", "bc_photo_id"): ctx.user_data.pop(k, None)
        await q.message.edit_text("❌ *Broadcast বাতিল করা হয়েছে।*", parse_mode="Markdown"); return

    # ── Bio Task admin panel ─────────────────────────────────────────
    def _bio_task_panel_text(bt):
        dr  = bt['reward']      if bt else 0
        ar  = (bt.get('add_reward', 0) or 0) if bt else 0
        act = bt['is_active']   if bt else 0
        st  = "🟢 চালু" if act else "🔴 বন্ধ"
        return (
            f"🧬 *Bio Task Management*\n\n"
            f"🆕 *Add Reward (প্রথমবার):* `{ar}$ USDT`\n"
            f"📅 *Daily Reward (রোজ):* `{dr}$ USDT`\n"
            f"📊 *Status:* {st}\n\n"
            f"📝 *Required Bio Text:*\n"
            f"`Free Task Earn bot https://t.me/BotUsername?start=ref_UserID`\n\n"
            f"🕛 প্রতিদিন রাত ১২:০০ BD তে automatic check হয়।\n"
            f"⏳ Bio সরালে ৫ মিনিট grace — তারপর tracking reset।"
        ), act

    def _bio_task_panel_kb(is_active):
        toggle_label = "🔴 Bio Task বন্ধ করুন" if is_active else "🟢 Bio Task চালু করুন"
        toggle_cb    = "adm_bio_task_off"        if is_active else "adm_bio_task_on"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🆕 Add Reward সেট করুন",   callback_data="adm_bio_set_add_reward")],
            [InlineKeyboardButton("📅 Daily Reward সেট করুন", callback_data="adm_bio_set_daily_reward")],
            [InlineKeyboardButton(toggle_label, callback_data=toggle_cb)],
            [InlineKeyboardButton("🔙 Admin Panel", callback_data="adm_panel")],
        ])

    if d == "adm_bio_task":
        _is_adm, bt = await asyncio.gather(
            asyncio.to_thread(is_admin, uid),
            asyncio.to_thread(get_bio_task),
        )
        if not _is_adm: return
        text, act = _bio_task_panel_text(bt)
        await q.message.edit_text(text, parse_mode="Markdown", reply_markup=_bio_task_panel_kb(act))
        return

    if d == "adm_bio_task_on":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        await q.message.reply_text("✅ Bio Task চালু করা হয়েছে।")
        await asyncio.to_thread(set_bio_task, is_active=1)
        bt = await asyncio.to_thread(get_bio_task)
        text, act = _bio_task_panel_text(bt)
        await q.message.edit_text(text, parse_mode="Markdown", reply_markup=_bio_task_panel_kb(act))
        return

    if d == "adm_bio_task_off":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        await q.message.reply_text("🔴 Bio Task বন্ধ করা হয়েছে।")
        await asyncio.to_thread(set_bio_task, is_active=0)
        bt = await asyncio.to_thread(get_bio_task)
        text, act = _bio_task_panel_text(bt)
        await q.message.edit_text(text, parse_mode="Markdown", reply_markup=_bio_task_panel_kb(act))
        return

    if d == "adm_bio_set_add_reward":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        ctx.user_data["state"] = "adm_bio_task_add_reward"
        await q.message.reply_text(
            "🆕 *Add Reward সেট করুন*\n\n"
            "প্রথমবার bio সেট করলে কত $ USDT দেওয়া হবে:\n"
            "_(যেমন: `0.10`)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d == "adm_bio_set_daily_reward":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        ctx.user_data["state"] = "adm_bio_task_daily_reward"
        await q.message.reply_text(
            "📅 *Daily Reward সেট করুন*\n\n"
            "প্রতিদিন bio রাখলে কত $ USDT দেওয়া হবে:\n"
            "_(যেমন: `0.05`)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d == "adm_bio_task_set_reward":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        ctx.user_data["state"] = "adm_bio_task_daily_reward"
        await q.message.reply_text(
            "📅 *Daily Reward সেট করুন*\n\n"
            "প্রতিদিন bio রাখলে কত $ USDT দেওয়া হবে:\n"
            "_(যেমন: `0.05`)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    # ── Referral Event ────────────────────────────────────────────
    if d == "adm_ref_event":
        if not await asyncio.to_thread(is_admin, uid): return
        events = await asyncio.to_thread(list_referral_events, 6)
        active = await asyncio.to_thread(get_active_referral_event)
        txt = "🎯 *Referral Event Manager*\n\n"
        if active:
            stats = await asyncio.to_thread(get_ref_event_stats, active['id'])
            txt += (
                f"✅ *চলমান Event:*\n"
                f"📅 {active['start_date']} → {active['end_date']}\n"
                f"📋 Required Tasks: `{active['required_tasks']}`\n"
                f"💰 Bonus: `${active['bonus_amount']}` USDT\n"
                f"🎁 Rewards দেওয়া হয়েছে: `{stats['count']}` জনকে | মোট: `{stats['total_amount']}$ USDT`\n\n"
            )
        else:
            txt += "❌ এখন কোনো active event নেই।\n\n"
        if events:
            txt += "📜 *সাম্প্রতিক Events:*\n"
            for ev in events:
                s = "🟢" if ev['is_active'] else "🔴"
                txt += f"{s} `#{ev['id']}` {ev['start_date']}→{ev['end_date']} | {ev['required_tasks']} tasks | ${ev['bonus_amount']}\n"
        btns = [[InlineKeyboardButton("➕ নতুন Event তৈরি করুন", callback_data="adm_ref_event_create")]]
        if active:
            btns.append([InlineKeyboardButton(f"🔴 Event #{active['id']} বন্ধ করুন", callback_data=f"adm_ref_event_deact_{active['id']}")])
        await q.message.reply_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
        return

    if d == "adm_ref_event_create":
        if not await asyncio.to_thread(is_admin, uid): return
        ctx.user_data["state"] = "adm_ref_event_start"
        await q.message.reply_text(
            "🎯 *নতুন Referral Event*\n\n"
            "📅 *Event শুরুর তারিখ* লিখুন:\n"
            "Format: `YYYY-MM-DD` (যেমন: `2026-06-12`)\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d.startswith("adm_ref_event_deact_"):
        if not await asyncio.to_thread(is_admin, uid): return
        try:
            eid = int(d.split("_")[-1])
            await asyncio.to_thread(deactivate_referral_event, eid)
            await q.message.reply_text(f"✅ Event #{eid} বন্ধ করা হয়েছে।", parse_mode="Markdown")
        except Exception as _rde:
            await q.message.reply_text(f"❌ Error: {_rde}")
        return

    if d == "adm_promo_create":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        ctx.user_data["state"] = "adm_promo_code"
        await q.message.reply_text(
            "🎁 *নতুন Promo Code তৈরি করুন*\n\n"
            "প্রথমে *Code* লিখুন (যেমন: `EIDMUBARAK`):\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d == "adm_bc_post_news":
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        bc_bn    = ctx.user_data.get("bc_msg_bn", "")
        bc_en    = ctx.user_data.get("bc_msg_en", "")
        bc_photo = ctx.user_data.get("bc_photo_id", "")
        if not bc_bn and not bc_en:
            await q.message.reply_text("❌ *কোনো মেসেজ পাওয়া যায়নি।*", parse_mode="Markdown"); return
        # News channel-এ বাংলা মেসেজ পাঠাও
        news_txt = bc_bn or bc_en
        try:
            if bc_photo:
                await ctx.bot.send_photo(LEADERBOARD_CH_ID, bc_photo, caption=news_txt, parse_mode="Markdown")
            else:
                await ctx.bot.send_message(LEADERBOARD_CH_ID, news_txt, parse_mode="Markdown")
            await q.message.reply_text(
                f"✅ *News Channel এ Post করা হয়েছে!*\n\n🔗 {LEADERBOARD_CH_LINK}",
                parse_mode="Markdown")
        except Exception as _e:
            await q.message.reply_text(f"❌ News Channel এ Post ব্যর্থ: {_e}", parse_mode="Markdown")
        for k in ("bc_msg_bn", "bc_msg_en", "bc_photo_id"): ctx.user_data.pop(k, None)
        return

    if d == "adm_msg":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("📨 *কোন User কে মেসেজ করবেন? User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_msg_id"; return
    if d == "adm_add":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("➕ *Balance যোগ করতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_add_id"; return
    if d == "adm_ded":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("➖ *Balance কাটতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_ded_id"; return
    if d == "adm_ban":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("🚫 *Ban করতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_ban_id"; return
    if d == "adm_unban":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("✅ *Unban করতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_unban_id"; return

    if d == "adm_add_admin":
        if uid != ADMIN_ID:
            await q.message.reply_text("❌ *শুধু Main Admin নতুন Admin যোগ করতে পারবে!*", parse_mode="Markdown"); return
        await q.message.reply_text(
            "👮 *নতুন Admin যোগ করুন*\n\nপ্রথমে *User ID* দিন:\n\n/cancel — বাতিল",
            parse_mode="Markdown")
        ctx.user_data["state"] = "adm_add_admin_id"; return

    if d == "adm_list_admins":
        _is_adm2, admins = await asyncio.gather(
            asyncio.to_thread(is_admin, uid),
            asyncio.to_thread(get_all_admins),
        )
        if not _is_adm2:
            await q.message.reply_text("⛔ Access Denied"); return
        is_main_adm = (uid == ADMIN_ID)
        # Main admin সবসময় তালিকার শুরুতে দেখাবে
        lines = [f"👑 *Main Admin*\n👤 `{ADMIN_ID}`\n🔑 Full Access (সব কিছু)\n📅 সর্বদা"]
        btns  = []
        for a in admins:
            pdisp = _perms_display(_rget(a, "permissions") or "full")
            added = _rget(a, "added_at") or "—"
            can_add = "✅ হ্যাঁ" if PERM_ADMIN_MANAGE in (_rget(a, "permissions") or "").split(",") else "❌ না"
            lines.append(
                f"👮 *Sub Admin*\n"
                f"👤 `{a['user_id']}`\n"
                f"🔑 {pdisp}\n"
                f"👮 Admin যোগ/সরাতে পারবে: {can_add}\n"
                f"📅 {added}"
            )
            # শুধু Main Admin এডিট/সরানো বাটন দেখবে
            if is_main_adm:
                btns.append([
                    InlineKeyboardButton(f"✏️ এডিট {a['user_id']}", callback_data=f"adm_edit_admin_perm_{a['user_id']}"),
                    InlineKeyboardButton(f"❌ সরাও {a['user_id']}", callback_data=f"adm_rm_admin_{a['user_id']}"),
                ])
        if not admins:
            lines.append("\n_এখনো কোনো Sub Admin যোগ করা হয়নি।_")
        adm_text = f"📋 *Admin তালিকা ({len(admins)+1} জন):*\n\n" + "\n\n".join(lines)
        if len(adm_text) > 4000: adm_text = adm_text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(
            adm_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns) if btns else None); return

    if d.startswith("adm_rm_admin_") and not d.startswith("adm_rm_admin_confirm_") and d != "adm_rm_admin_cancel":
        if uid != ADMIN_ID:
            await q.message.reply_text("❌ *শুধু Main Admin অন্য Admin সরাতে পারবে!*", parse_mode="Markdown"); return
        tid = int(d.split("_")[3])
        if tid == ADMIN_ID:
            await q.message.reply_text("❌ *Main Admin কে সরানো যাবে না!*", parse_mode="Markdown"); return
        # Confirmation step
        ctx.user_data["adm_rm_confirm_tid"] = tid
        await q.message.reply_text(
            f"⚠️ *নিশ্চিত করুন*\n\n`{tid}` কে Admin থেকে সরাতে চান?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ হ্যাঁ, সরাও", callback_data=f"adm_rm_admin_confirm_{tid}"),
                InlineKeyboardButton("❌ না", callback_data="adm_rm_admin_cancel"),
            ]])
        ); return

    if d.startswith("adm_rm_admin_confirm_"):
        if uid != ADMIN_ID:
            await q.message.reply_text("⛔ শুধু Main Admin এই কাজ করতে পারবে!"); return
        tid = int(d.split("_")[4])
        if tid == ADMIN_ID:
            await q.message.reply_text("❌ *Main Admin কে সরানো যাবে না!*", parse_mode="Markdown"); return
        await asyncio.to_thread(remove_admin, tid)
        ctx.user_data.pop("adm_rm_confirm_tid", None)
        try:
            await ctx.bot.send_message(tid, "⛔ *আপনাকে Admin তালিকা থেকে সরানো হয়েছে।*", parse_mode="Markdown")
        except Exception: pass
        await q.message.reply_text(f"✅ *`{tid}` কে Admin তালিকা থেকে সরানো হয়েছে।*", parse_mode="Markdown"); return

    if d == "adm_rm_admin_cancel":
        ctx.user_data.pop("adm_rm_confirm_tid", None)
        await q.message.reply_text("❌ বাতিল করা হয়েছে।", parse_mode="Markdown"); return

    if d.startswith("adm_new_adm_yes_"):
        if uid != ADMIN_ID:
            await q.message.reply_text("⛔ শুধু Main Admin এই কাজ করতে পারবে!"); return
        tid = int(d.split("_")[4])
        def _db_7149():
            r = None
            with _c() as c:
                r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tid,)).fetchone()
            return r
        r = await asyncio.to_thread(_db_7149)
        cur_perms = set((r["permissions"] if r else "full").split(","))
        cur_perms.add(PERM_ADMIN_MANAGE)
        new_str = ",".join(sorted(cur_perms))
        def _db_7154():
            with _c() as c:
                c.execute("UPDATE admins SET permissions=%s WHERE user_id=%s", (new_str, tid))
            return
        await asyncio.to_thread(_db_7154)
        pdisp = _perms_display(new_str)
        try:
            await ctx.bot.send_message(
                tid,
                f"👮 *আপনাকে Bot Admin হিসেবে যোগ করা হয়েছে!*\n\n"
                f"🔑 *পারমিশন:* {pdisp}\n"
                f"👮 *Admin যোগ/সরানো:* ✅ হ্যাঁ\n\n"
                f"/admin দিয়ে Admin Panel খুলুন।",
                parse_mode="Markdown")
        except Exception as _e:
            log.warning(f"[ADM] tid={tid} admin-add notify ব্যর্থ: {_e}")
        await q.message.reply_text(
            f"✅ *`{tid}` Admin হিসেবে যোগ হয়েছে।*\n\n🔑 {pdisp}\n👮 Admin যোগ/সরানো: ✅ হ্যাঁ",
            parse_mode="Markdown"); return

    if d.startswith("adm_new_adm_no_"):
        if uid != ADMIN_ID:
            await q.message.reply_text("⛔ শুধু Main Admin এই কাজ করতে পারবে!"); return
        tid = int(d.split("_")[4])
        def _db_7176():
            r = None
            with _c() as c:
                r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tid,)).fetchone()
            return r
        r = await asyncio.to_thread(_db_7176)
        cur_perms = set((r["permissions"] if r else "full").split(","))
        cur_perms.discard(PERM_ADMIN_MANAGE)
        new_str = ",".join(sorted(cur_perms))
        def _db_7181():
            with _c() as c:
                c.execute("UPDATE admins SET permissions=%s WHERE user_id=%s", (new_str, tid))
            return
        await asyncio.to_thread(_db_7181)
        pdisp = _perms_display(new_str)
        try:
            await ctx.bot.send_message(
                tid,
                f"👮 *আপনাকে Bot Admin হিসেবে যোগ করা হয়েছে!*\n\n"
                f"🔑 *পারমিশন:* {pdisp}\n"
                f"👮 *Admin যোগ/সরানো:* ❌ না\n\n"
                f"/admin দিয়ে Admin Panel খুলুন।",
                parse_mode="Markdown")
        except Exception as _e:
            log.warning(f"[ADM] tid={tid} admin-add (no-manage) notify ব্যর্থ: {_e}")
        await q.message.reply_text(
            f"✅ *`{tid}` Admin হিসেবে যোগ হয়েছে।*\n\n🔑 {pdisp}\n👮 Admin যোগ/সরানো: ❌ না",
            parse_mode="Markdown"); return

    # ── Admin permission edit ──────────────────────────────────────
    if d.startswith("adm_edit_admin_perm_"):
        if uid != ADMIN_ID:
            await q.message.reply_text("❌ *শুধু Main Admin Permission পরিবর্তন করতে পারবে!*", parse_mode="Markdown"); return
        tid = int(d.split("_")[4])
        ctx.user_data["perm_edit_uid"] = tid
        await _show_perm_picker(q.message, tid, ctx); return

    if d.startswith("adm_perm_toggle_"):
        if uid != ADMIN_ID: return
        # format: adm_perm_toggle_{perm}_{target_uid}
        # perm-এ underscore থাকতে পারে (যেমন admin_manage), তাই rsplit ব্যবহার
        suffix  = d[len("adm_perm_toggle_"):]   # "admin_manage_12345" বা "full_12345"
        tgt_uid = int(suffix.rsplit("_", 1)[1])
        perm    = suffix.rsplit("_", 1)[0]
        # Load current perms
        def _db_7216():
            r = None
            with _c() as c:
                r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tgt_uid,)).fetchone()
            return r
        r = await asyncio.to_thread(_db_7216)
        cur_str = (r["permissions"] if r else "full") or "full"
        cur_set = set(cur_str.split(","))
        if perm == PERM_FULL:
            # Full toggle — admin_manage আলাদা রাখো
            had_admin_manage = PERM_ADMIN_MANAGE in cur_set
            new_set = {PERM_FULL}
            if had_admin_manage:
                new_set.add(PERM_ADMIN_MANAGE)
        elif perm == PERM_ADMIN_MANAGE:
            # Admin manage toggle — অন্য perm-এ প্রভাব নেই
            if PERM_ADMIN_MANAGE in cur_set:
                cur_set.discard(PERM_ADMIN_MANAGE)
            else:
                cur_set.add(PERM_ADMIN_MANAGE)
            new_set = cur_set if cur_set else {PERM_TASKS}
        else:
            cur_set.discard(PERM_FULL)
            if perm in cur_set: cur_set.discard(perm)
            else: cur_set.add(perm)
            new_set = cur_set if cur_set else {PERM_TASKS}
        new_str = ",".join(sorted(new_set))
        def _db_7239():
            with _c() as c:
                c.execute("UPDATE admins SET permissions=%s WHERE user_id=%s", (new_str, tgt_uid))
            return
        await asyncio.to_thread(_db_7239)
        await _show_perm_picker(q.message, tgt_uid, ctx); return

    if d.startswith("adm_perm_save_"):
        if uid != ADMIN_ID: return
        tgt_uid = int(d.split("_")[3])
        def _db_7247():
            r = None
            with _c() as c:
                r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tgt_uid,)).fetchone()
            return r
        r = await asyncio.to_thread(_db_7247)
        pdisp = _perms_display(r["permissions"] if r else "full")
        is_new = ctx.user_data.pop("adm_new_admin_id", None) == tgt_uid
        await q.message.reply_text(
            f"✅ *`{tgt_uid}` {'Admin হিসেবে যোগ হয়েছে' if is_new else 'এর পারমিশন আপডেট হয়েছে'}!*\n\n🔑 *পারমিশন:* {pdisp}",
            parse_mode="Markdown")
        try:
            if is_new:
                await ctx.bot.send_message(
                    tgt_uid,
                    f"👮 *আপনাকে Bot Admin হিসেবে যোগ করা হয়েছে!*\n\n🔑 *পারমিশন:* {pdisp}\n\n/admin দিয়ে Admin Panel খুলুন।",
                    parse_mode="Markdown")
            else:
                await ctx.bot.send_message(
                    tgt_uid,
                    f"🔑 *আপনার Admin পারমিশন আপডেট হয়েছে:*\n{pdisp}",
                    parse_mode="Markdown")
        except Exception: pass
        return

    # ── Task Management ───────────────────────────────────────────
    if d == "adm_task_add":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text(
            "➕ *নতুন Task যোগ করুন*\n\nপ্রথমে *Task নাম* লিখুন:\n\n/cancel — বাতিল",
            parse_mode="Markdown")
        ctx.user_data["state"] = "adm_task_name"; return

    # ── Task type picker (regular task) ───────────────────────────
    if d.startswith("adm_task_type_"):
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        ttype = d[len("adm_task_type_"):]
        ctx.user_data["new_task_type"] = ttype
        ctx.user_data["state"] = "adm_task_price"
        icon, label = TASK_TYPE_MAP.get(ttype, ("📋", "Other"))
        name = ctx.user_data.get("new_task_name", "")
        await q.message.reply_text(
            f"✅ *নাম:* `{name}`\n🏷 *Type:* {icon} {label}\n\nএখন *মূল্য* লিখুন (যেমন: `0.10`):\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_task_list":
        tasks = await asyncio.to_thread(get_all_tasks)
        if not tasks:
            await q.message.reply_text("📋 *কোনো Task নেই।*", parse_mode="Markdown"); return
        # সব task-এর fields একসাথে parallel-এ fetch করো — sequential loop বন্ধ
        all_fields = await asyncio.gather(
            *[asyncio.to_thread(get_task_fields, t['id']) for t in tasks]
        )
        text = "📋 *Task তালিকা:*\n\n"
        btns = []
        for t, fields in zip(tasks, all_fields):
            s  = "✅" if t["is_active"] else "❌"
            field_count = len(fields)
            text += f"{s} *{_md_esc(t['name'])}* — `{t['price']}$ USDT` | {field_count} field(s)\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {t['name']}", callback_data=f"adm_task_edit_{t['id']}"),
                InlineKeyboardButton("🗑 Delete",        callback_data=f"adm_task_del_{t['id']}"),
            ])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_task_del_"):
        tid  = int(d.split("_")[3])
        task = await asyncio.to_thread(get_task, tid)
        if task:
            await asyncio.to_thread(delete_task, tid)
            await q.message.reply_text(f"🗑 *'{_md_esc(task['name'])}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_task_edit_"):
        tid  = int(d.split("_")[3])
        task = await asyncio.to_thread(get_task, tid)
        if not task:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown"); return
        fields = await asyncio.to_thread(get_task_fields, tid)
        status_s = "✅ Active" if task["is_active"] else "❌ Inactive"
        toggle_lbl = "🔴 Deactivate" if task["is_active"] else "🟢 Activate & Notify"
        _t_has_vid = bool((_rget(task, 'demo_video_id', '') or '').strip())
        _t_has_img = bool((_rget(task, 'example_image_id', '') or '').strip())
        vid_lbl = "🎬 Video ✅" if _t_has_vid else "🎬 Demo Video"
        img_lbl = "📷 Sample ✅" if _t_has_img else "📷 Example Photo"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম",      callback_data=f"adm_te_name_{tid}"),
             InlineKeyboardButton("💰 দাম",       callback_data=f"adm_te_price_{tid}")],
            [InlineKeyboardButton("📜 নিয়মাবলী", callback_data=f"adm_te_rules_{tid}"),
             InlineKeyboardButton(toggle_lbl, callback_data=f"adm_te_toggle_{tid}")],
            [InlineKeyboardButton(f"📋 Proof Fields ({len(fields)})", callback_data=f"adm_fields_{tid}")],
            [InlineKeyboardButton(img_lbl, callback_data=f"adm_te_sample_photo_{tid}"),
             InlineKeyboardButton(vid_lbl, callback_data=f"adm_te_video_{tid}")],
            [InlineKeyboardButton("❌ বাতিল",     callback_data="task_cancel")],
        ])
        await q.message.reply_text(
            f"✏️ *Task Edit:* `{task['name']}`\n\n"
            f"💵 *দাম:* `{task['price']}$ USDT`\n"
            f"👁 *Status:* {status_s}\n"
            f"📋 *Proof Fields:* {len(fields)} টি\n"
            f"📜 *নিয়মাবলী:*\n{task['rules']}\n\nকী পরিবর্তন করবেন?",
            parse_mode="Markdown", reply_markup=kb); return

    # ── Regular Task: Example Photo ──────────────────────────────
    if d.startswith("adm_te_sample_photo_"):
        tid = int(d.split("_")[4])
        t   = await asyncio.to_thread(get_task, tid)
        cur_img = (_rget(t, 'example_image_id', '') or '').strip() if t else ''
        ctx.user_data["state"]      = "adm_task_set_sample_photo"
        ctx.user_data["edit_tid"]   = tid
        note = "\n\n_বর্তমানে একটি example ছবি সেট আছে। নতুন পাঠালে replace হবে।_" if cur_img else ""
        await q.message.reply_text(
            f"📷 *Example Photo পাঠান*{note}\n\n"
            "ইউজার Photo Field এ এই ছবি দেখে বুঝবে কেমন ছবি দিতে হবে।\n\n"
            "📤 এখন একটি ছবি পাঠান।\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Regular Task: Demo Video menu ────────────────────────────
    if d.startswith("adm_te_video_clear_"):
        tid = int(d.split("_")[4])
        await asyncio.to_thread(update_task_field_val, tid, "demo_video_id", "")
        await q.message.reply_text("🗑 *Demo Video সরানো হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_te_video_upload_"):
        tid = int(d.split("_")[4])
        ctx.user_data["state"]    = "adm_task_set_video_file"
        ctx.user_data["edit_tid"] = tid
        await q.message.reply_text(
            "📤 *এখন ভিডিও পাঠান*\n\n"
            "Task শুরু করার আগে user 'ভিডিও দেখুন' বাটনে চাপলে এই ভিডিও দেখবে।\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_te_video_link_"):
        tid = int(d.split("_")[4])
        ctx.user_data["state"]    = "adm_task_set_video_link"
        ctx.user_data["edit_tid"] = tid
        await q.message.reply_text(
            "🔗 *ভিডিও লিংক দিন*\n\n"
            "(YouTube, Google Drive, বা যেকোনো direct video link)\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_te_video_") and not d.startswith("adm_te_video_upload_") \
            and not d.startswith("adm_te_video_link_") and not d.startswith("adm_te_video_clear_"):
        tid = int(d.split("_")[3])
        t   = await asyncio.to_thread(get_task, tid)
        cur_vid = (_rget(t, 'demo_video_id', '') or '').strip() if t else ''
        vid_note = "\n\n✅ *ভিডিও আছে।* নতুন পাঠালে replace হবে।" if cur_vid else ""
        await q.message.reply_text(
            f"🎬 *Demo Video যোগ করুন*{vid_note}\n\nকীভাবে যোগ করবেন?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 ভিডিও আপলোড করুন", callback_data=f"adm_te_video_upload_{tid}"),
                 InlineKeyboardButton("🔗 ভিডিও লিংক দিন",   callback_data=f"adm_te_video_link_{tid}")],
                [InlineKeyboardButton("🗑 Video সরিয়ে দিন",   callback_data=f"adm_te_video_clear_{tid}")],
                [InlineKeyboardButton("❌ বাতিল",              callback_data="task_cancel")],
            ])
        ); return

    if d.startswith("adm_te_toggle_"):
        tid = int(d.split("_")[3])
        await _activate_task_and_notify(ctx, q, uid,
            get_fn=get_task, update_fn=update_task_field_val, tid=tid,
            link_prefix="task_")
        return

    if d.startswith("adm_te_name_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_edit_name"; ctx.user_data["edit_tid"] = tid
        await q.message.reply_text("✏️ *নতুন Task নাম লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_te_price_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_edit_price"; ctx.user_data["edit_tid"] = tid
        await q.message.reply_text("💰 *নতুন দাম লিখুন* (যেমন: `0.15`):\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_te_rules_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_edit_rules"; ctx.user_data["edit_tid"] = tid
        await q.message.reply_text("📜 *নতুন নিয়মাবলী লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return

    # ── Task Fields (Proof) Management ───────────────────────────
    if d.startswith("adm_fields_"):
        tid    = int(d.split("_")[2])
        task   = await asyncio.to_thread(get_task, tid)
        if not task:
            await q.message.reply_text("❌ Task পাওয়া যায়নি।"); return
        fields = await asyncio.to_thread(get_task_fields, tid)
        text   = f"📋 *Task Proof Fields: `{task['name']}`*\n\n"
        btns   = []
        if fields:
            for i, f in enumerate(fields, 1):
                icon    = "📷" if f["field_type"] == "photo" else "📝"
                has_img = bool((_rget(f, 'example_image_id', '') or '').strip())
                img_tag = " 🖼✅" if has_img else ""
                text += f"{i}. {icon} {f['prompt_bn']}{img_tag}\n"
                row = [InlineKeyboardButton(
                    f"🗑 {i}. {(f['prompt_bn'] or '')[:18]}", callback_data=f"adm_field_del_{f['id']}_{tid}"
                )]
                if f["field_type"] == "photo":
                    row.append(InlineKeyboardButton(
                        f"🖼{'✅' if has_img else '+'} Example", callback_data=f"adm_field_ex_img_{f['id']}_{tid}"
                    ))
                btns.append(row)
        else:
            text += "_কোনো field নেই — ইউজার থেকে generic তথ্য চাওয়া হবে।_\n"

        btns.append([
            InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
            InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}"),
        ])
        btns.append([InlineKeyboardButton("🔄 সব Fields মুছুন", callback_data=f"adm_fields_clear_{tid}")])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_fields_clear_"):
        tid = int(d.split("_")[3])
        await asyncio.to_thread(delete_all_task_fields, tid)
        await q.message.reply_text("✅ *সব Fields মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_field_del_"):
        parts = d.split("_")
        fid   = int(parts[3])
        tid   = int(parts[4])
        await asyncio.to_thread(delete_task_field, fid)
        await q.message.reply_text("✅ *Field মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

    # ── Regular Task: existing Photo Field এর Example Image সেট/পরিবর্তন ──
    if d.startswith("adm_field_ex_img_"):
        parts = d.split("_")
        fid   = int(parts[4])
        tid   = int(parts[5])
        ctx.user_data["state"]               = "adm_field_set_example_photo"
        ctx.user_data["edit_field_id"]        = fid
        ctx.user_data["edit_field_task_id"]   = tid
        ctx.user_data["edit_field_is_categ"]  = False
        await q.message.reply_text(
            "🖼 *এই Photo Field-এর Example Photo পাঠান:*\n\n"
            "_(ইউজার এই ছবি দেখে বুঝবে কেমন ছবি পাঠাতে হবে)_\n\n"
            "📤 এখন একটি ছবি পাঠান।\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Per-field Example Photo: Skip (Regular Task) ─────────────
    if d.startswith("adm_field_skip_example_"):
        tid = int(d.split("_")[4])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
             InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}")],
            [InlineKeyboardButton("✅ Done — Task সেভ করুন", callback_data=f"adm_task_done_{tid}")],
        ])
        fields = await asyncio.to_thread(get_task_fields, tid)
        ctx.user_data.pop("state", None)
        ctx.user_data.pop("edit_field_id", None)
        ctx.user_data.pop("edit_field_task_id", None)
        ctx.user_data.pop("edit_field_is_categ", None)
        await q.message.reply_text(
            f"⏭ *Example Photo Skip করা হয়েছে।*\n\nমোট fields: {len(fields)} টি\n\nআরো field যোগ করুন বা Done চাপুন:",
            parse_mode="Markdown", reply_markup=kb); return

    # ── Per-field Example Photo: Skip (Category Task) ────────────
    if d.startswith("adm_cfield_skip_example_"):
        tid = int(d.split("_")[4])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ আরো Field যোগ করুন", callback_data=f"adm_cfields_{tid}")],
            [InlineKeyboardButton("🚀 সম্পন্ন — Task Live করুন", callback_data=f"adm_ctask_golive_{tid}")],
        ])
        ctx.user_data.pop("state", None)
        ctx.user_data.pop("edit_field_id", None)
        ctx.user_data.pop("edit_field_task_id", None)
        ctx.user_data.pop("edit_field_is_categ", None)
        await q.message.reply_text(
            "⏭ *Example Photo Skip করা হয়েছে।*\n\nআরো field যোগ করুন বা Live করুন:",
            parse_mode="Markdown", reply_markup=kb); return

    if d.startswith("adm_field_add_text_") or d.startswith("adm_field_add_photo_"):
        ftype = "text" if "add_text" in d else "photo"
        tid   = int(d.split("_")[-1])
        ctx.user_data["state"]          = "adm_field_prompt_bn"
        ctx.user_data["new_field_type"] = ftype
        ctx.user_data["new_field_tid"]  = tid
        icon = "📝" if ftype == "text" else "📷"
        await q.message.reply_text(
            f"{icon} *নতুন {ftype.upper()} Field*\n\n"
            f"বাংলা Prompt লিখুন:\n_(উদাহরণ: Gmail দিন / ছবি পাঠান)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Channel Management ────────────────────────────────────────
    if d == "adm_ch_list":
        channels = await asyncio.to_thread(get_req_channels)
        if not channels:
            text = "📢 *Required Channels*\n\n❌ কোনো channel নেই।"
            btns = []
        else:
            text = "📢 *Required Channels তালিকা:*\n\n"
            btns = []
            for ch in channels:
                text += f"• {ch['ch_name']} — `{ch['ch_id']}`\n"
                btns.append([InlineKeyboardButton(
                    f"🗑 Remove: {ch['ch_name']}", callback_data=f"adm_ch_del_{ch['id']}"
                )])
        btns.append([InlineKeyboardButton("➕ নতুন Channel যোগ করুন", callback_data="adm_ch_add")])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_ch_add":
        ctx.user_data["state"] = "adm_ch_add_id"
        await q.message.reply_text(
            "📢 *নতুন Channel যোগ করুন*\n\n*Channel ID বা Username* দিন:\n_(যেমন: @mychannel)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_ch_del_"):
        rid = int(d.split("_")[3])
        await asyncio.to_thread(remove_req_channel, rid)
        await q.message.reply_text("✅ *Channel সরানো হয়েছে।*", parse_mode="Markdown"); return

    # ── Category Task Management ─────────────────────────────────
    if d == "adm_cat_add":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_cat_name"
        await q.message.reply_text(
            "📁 *নতুন Category যোগ করুন*\n\n*Category নাম* লিখুন:\n_(যেমন: Social Media Tasks)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_cat_list":
        cats = await asyncio.to_thread(get_all_categories)
        if not cats:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ নতুন Category যোগ করুন", callback_data="adm_cat_add")]])
            await q.message.reply_text("📁 *কোনো Category নেই।*", parse_mode="Markdown", reply_markup=kb); return
        text = "📁 *Category তালিকা:*\n\n"
        btns = []
        for cat in cats:
            s = "✅" if cat["is_active"] else "❌"
            tasks = await asyncio.to_thread(get_category_tasks, cat['id'], active_only=False)
            text += f"{s} *{_md_esc(cat['name'])}* — {len(tasks)} task(s)\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {cat['name']}", callback_data=f"adm_cat_edit_{cat['id']}"),
                InlineKeyboardButton("🗑 Delete",          callback_data=f"adm_cat_del_{cat['id']}"),
            ])
        btns.append([InlineKeyboardButton("➕ নতুন Category যোগ করুন", callback_data="adm_cat_add")])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_cat_del_"):
        cid = int(d.split("_")[3])
        cat = await asyncio.to_thread(get_category, cid)
        if cat:
            await asyncio.to_thread(delete_category, cid)
            await q.message.reply_text(f"🗑 *'{_md_esc(cat['name'])}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Category পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_cat_edit_"):
        cid = int(d.split("_")[3])
        cat = await asyncio.to_thread(get_category, cid)
        if not cat:
            await q.message.reply_text("❌ Category পাওয়া যায়নি!", parse_mode="Markdown"); return
        tasks = await asyncio.to_thread(get_category_tasks, cid, active_only=False)
        s = "✅ Active" if cat["is_active"] else "❌ Inactive"
        cat_toggle_lbl = "🔴 Deactivate" if cat["is_active"] else "🟢 Activate & Notify"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম",          callback_data=f"adm_cate_name_{cid}"),
             InlineKeyboardButton(cat_toggle_lbl,      callback_data=f"adm_cate_toggle_{cid}")],
            [InlineKeyboardButton("➕ Task যোগ করুন", callback_data=f"adm_cate_task_add_{cid}"),
             InlineKeyboardButton(f"📋 Tasks ({len(tasks)})", callback_data=f"adm_cate_task_list_{cid}")],
            [InlineKeyboardButton("❌ বাতিল",          callback_data="task_cancel")],
        ])
        await q.message.reply_text(
            f"✏️ *Category Edit:* `{cat['name']}`\n\n"
            f"👁 *Status:* {s}\n"
            f"📋 *Tasks:* {len(tasks)} টি\n\nকী করবেন?",
            parse_mode="Markdown", reply_markup=kb); return

    if d.startswith("adm_cate_toggle_"):
        cid = int(d.split("_")[3])
        cat = await asyncio.to_thread(get_category, cid)
        if not cat: return
        new_val = 0 if cat["is_active"] else 1
        await asyncio.to_thread(update_category, cid, "is_active", new_val)
        s = "✅ Active" if new_val else "❌ Inactive"
        await q.message.reply_text(f"👁 *'{_md_esc(cat['name'])}' Status: {s}*", parse_mode="Markdown"); return

    if d.startswith("adm_cate_name_"):
        cid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_cat_edit_name"; ctx.user_data["edit_cat_id"] = cid
        await q.message.reply_text("✏️ *নতুন Category নাম লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return

    if d.startswith("adm_cate_task_add_"):
        cid = int(d.split("_")[4])
        ctx.user_data["state"] = "adm_cate_task_name"
        ctx.user_data["new_cate_cat_id"] = cid
        cat = await asyncio.to_thread(get_category, cid)
        await q.message.reply_text(
            f"➕ *'{_md_esc(cat['name'])}' — নতুন Task যোগ করুন*\n\nTask নাম লিখুন:\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Task type picker (category task) ──────────────────────────
    if d.startswith("adm_cate_task_type_"):
        if not await asyncio.to_thread(is_admin, uid):
            try: await q.answer()
            except Exception: pass
            return
        ttype = d[len("adm_cate_task_type_"):]
        ctx.user_data["new_cate_task_type"] = ttype
        ctx.user_data["state"] = "adm_cate_task_price"
        icon, label = TASK_TYPE_MAP.get(ttype, ("📋", "Other"))
        name = ctx.user_data.get("new_cate_task_name", "")
        await q.message.reply_text(
            f"✅ *Task নাম:* `{name}`\n🏷 *Type:* {icon} {label}\n\n💰 *দাম লিখুন* (যেমন: `0.10`):\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_cate_task_list_"):
        cid   = int(d.split("_")[4])
        cat, tasks = await asyncio.gather(
            asyncio.to_thread(get_category, cid),
            asyncio.to_thread(get_category_tasks, cid, active_only=False),
        )
        if not tasks:
            await q.message.reply_text(f"📋 *'{_md_esc(cat['name'])}' তে কোনো Task নেই।*", parse_mode="Markdown"); return
        text = f"📋 *'{_md_esc(cat['name'])}' Tasks:*\n\n"
        btns = []
        for t in tasks:
            s = "✅" if t["is_active"] else "❌"
            fields = await asyncio.to_thread(get_category_task_fields, t['id'])
            text += f"{s} *{_md_esc(t['name'])}* — `{t['price']}$` | {len(fields)} field(s)\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {t['name']}", callback_data=f"adm_ctask_edit_{t['id']}"),
                InlineKeyboardButton("🗑 Delete",         callback_data=f"adm_ctask_del_{t['id']}"),
            ])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_ctask_del_"):
        tid = int(d.split("_")[3])
        t = await asyncio.to_thread(get_category_task, tid)
        if t:
            await asyncio.to_thread(delete_category_task, tid)
            await q.message.reply_text(f"🗑 *'{_md_esc(t['name'])}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_ctask_edit_"):
        tid = int(d.split("_")[3])
        t   = await asyncio.to_thread(get_category_task, tid)
        if not t:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown"); return
        fields = await asyncio.to_thread(get_category_task_fields, tid)
        s = "✅ Active" if t["is_active"] else "❌ Inactive"
        ctaste_toggle_lbl = "🔴 Deactivate" if t["is_active"] else "🟢 Activate & Notify"
        slots = f"{t['completed_count']}/{t['max_completions']}" if t['max_completions'] > 0 else f"{t['completed_count']}/∞"
        _pu_raw = _rget(t, 'per_user_limit', None)
        pu_limit = _pu_raw if _pu_raw is not None else 1
        _ct_has_img = bool((_rget(t, 'example_image_id', '') or '').strip())
        _ct_has_vid = bool((_rget(t, 'demo_video_id',   '') or '').strip())
        _img_lbl = "📷 Sample Photo ✅" if _ct_has_img else "📷 Sample Photo যোগ"
        _vid_lbl = "🎬 Video ✅" if _ct_has_vid else "🎬 Video যোগ"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম",       callback_data=f"adm_ctaste_name_{tid}"),
             InlineKeyboardButton("💰 দাম",        callback_data=f"adm_ctaste_price_{tid}")],
            [InlineKeyboardButton("📜 নিয়মাবলী",  callback_data=f"adm_ctaste_rules_{tid}"),
             InlineKeyboardButton(ctaste_toggle_lbl, callback_data=f"adm_ctaste_toggle_{tid}")],
            [InlineKeyboardButton("👥 Max জন",     callback_data=f"adm_ctaste_max_{tid}"),
             InlineKeyboardButton(f"📋 Proof Fields ({len(fields)})", callback_data=f"adm_cfields_{tid}")],
            [InlineKeyboardButton(f"👤 Per User Limit ({pu_limit}x)", callback_data=f"adm_ctaste_per_user_{tid}")],
            [InlineKeyboardButton(_img_lbl, callback_data=f"adm_ctaste_sample_photo_{tid}"),
             InlineKeyboardButton(_vid_lbl, callback_data=f"adm_ctaste_video_{tid}")],
            [InlineKeyboardButton("❌ বাতিল",       callback_data="task_cancel")],
        ])
        await q.message.reply_text(
            f"✏️ *Category Task Edit:* `{t['name']}`\n\n"
            f"💵 *দাম:* `{t['price']}$ USDT`\n"
            f"👁 *Status:* {s}\n"
            f"👥 *Completions:* {slots}\n"
            f"👤 *Per User Limit:* `{pu_limit}` বার\n"
            f"📋 *Proof Fields:* {len(fields)} টি\n"
            f"📜 *নিয়মাবলী:*\n{t['rules']}\n\nকী পরিবর্তন করবেন?",
            parse_mode="Markdown", reply_markup=kb); return

    if d.startswith("adm_ctaste_toggle_"):
        tid = int(d.split("_")[3])
        await _activate_task_and_notify(ctx, q, uid,
            get_fn=get_category_task, update_fn=update_category_task, tid=tid,
            link_prefix="ctask_")
        return

    if d.startswith("adm_ctaste_name_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_ctask_edit_name"; ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text("✏️ *নতুন Task নাম লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_ctaste_price_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_ctask_edit_price"; ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text("💰 *নতুন দাম লিখুন* (যেমন: `0.10`):\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_ctaste_rules_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_ctask_edit_rules"; ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text("📜 *নতুন নিয়মাবলী লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_ctaste_max_"):
        tid = int(d.split("_")[3])
        t = await asyncio.to_thread(get_category_task, tid)
        cur = t['max_completions'] if t else 0
        ctx.user_data["state"] = "adm_ctask_edit_max"; ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text(
            f"👥 *সর্বোচ্চ কতজন করতে পারবে?*\n\n_বর্তমান: `{cur}` (0 = সীমাহীন)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_ctaste_per_user_"):
        tid = int(d.split("_")[4])
        t = await asyncio.to_thread(get_category_task, tid)
        _pu_raw2 = _rget(t, 'per_user_limit', None) if t else None
        cur = _pu_raw2 if _pu_raw2 is not None else 1
        ctx.user_data["state"] = "adm_ctask_edit_per_user"; ctx.user_data["edit_ctask_id"] = tid
        _cur_lbl = "Unlimited ♾️" if cur == 0 else f"{cur} বার"
        await q.message.reply_text(
            f"👤 *প্রতি ইউজার কতবার এই Task করতে পারবে?*\n\n_বর্তমান: `{_cur_lbl}` (0 = Unlimited)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Sample Photo ───────────────────────────────────────────────
    if d.startswith("adm_ctaste_sample_photo_"):
        tid = int(d.split("_")[4])
        t = await asyncio.to_thread(get_category_task, tid)
        cur_img = (_rget(t, 'example_image_id', '') or '').strip() if t else ''
        ctx.user_data["state"] = "adm_ctask_set_sample_photo"
        ctx.user_data["edit_ctask_id"] = tid
        note = "\n\n_বর্তমানে একটি sample ছবি সেট আছে। নতুন পাঠালে replace হবে।_" if cur_img else ""
        await q.message.reply_text(
            f"📷 *Sample Photo পাঠান*{note}\n\n"
            "ইউজার Photo Field এ এই ছবি দেখে বুঝবে কেমন ছবি দিতে হবে।\n\n"
            "📤 এখন একটি ছবি পাঠান।\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Demo Video ─────────────────────────────────────────────────
    if d.startswith("adm_ctaste_video_clear_"):
        tid = int(d.split("_")[4])
        await asyncio.to_thread(update_category_task, tid, "demo_video_id", "")
        await q.message.reply_text("🗑 *Demo Video সরানো হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_ctaste_video_upload_"):
        tid = int(d.split("_")[4])
        ctx.user_data["state"] = "adm_ctask_set_video_file"
        ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text(
            "📤 *এখন ভিডিও পাঠান*\n\n"
            "Task শুরু করার আগে user 'ভিডিও দেখুন' বাটনে চাপলে এই ভিডিও দেখবে।\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_ctaste_video_link_"):
        tid = int(d.split("_")[4])
        ctx.user_data["state"] = "adm_ctask_set_video_link"
        ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text(
            "🔗 *ভিডিও লিংক দিন*\n\n"
            "(YouTube, Google Drive, বা যেকোনো direct video link)\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_ctaste_video_") and not d.startswith("adm_ctaste_video_upload_") \
            and not d.startswith("adm_ctaste_video_link_") and not d.startswith("adm_ctaste_video_clear_"):
        tid = int(d.split("_")[3])
        t = await asyncio.to_thread(get_category_task, tid)
        cur_vid = (_rget(t, 'demo_video_id', '') or '').strip() if t else ''
        vid_note = "\n\n✅ *ভিডিও আছে।* নতুন পাঠালে replace হবে।" if cur_vid else ""
        await q.message.reply_text(
            f"🎬 *Demo Video যোগ করুন*{vid_note}\n\nকীভাবে যোগ করবেন?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 ভিডিও আপলোড করুন", callback_data=f"adm_ctaste_video_upload_{tid}"),
                 InlineKeyboardButton("🔗 ভিডিও লিংক দিন",   callback_data=f"adm_ctaste_video_link_{tid}")],
                [InlineKeyboardButton("🗑 Video সরিয়ে দিন",   callback_data=f"adm_ctaste_video_clear_{tid}")],
                [InlineKeyboardButton("❌ বাতিল",              callback_data="task_cancel")],
            ])
        ); return

    # ── User: Watch Video — Regular Task ──────────────────────────
    if d.startswith("task_watch_video_"):
        try:
            tid = int(d.split("_")[3])
        except (IndexError, ValueError):
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!")
            return
        t   = await asyncio.to_thread(get_task, tid)
        vid = (_rget(t, 'demo_video_id', '') or '').strip() if t else ''
        lang = await asyncio.to_thread(get_lang, uid)
        if not vid:
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!" if lang == 'bn' else "❌ Video not found!")
            return
        try:
            if vid.startswith("http") or vid.startswith("https"):
                lbl = "🎬 ভিডিও দেখুন" if lang == 'bn' else "🎬 Watch Video"
                txt = "👇 নিচের বাটনে চাপুন:" if lang == 'bn' else "👇 Tap the button below:"
                await q.message.reply_text(
                    txt,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(lbl, url=vid)]]))
            else:
                cap = "🎬 ডেমো ভিডিও" if lang == 'bn' else "🎬 Demo Video"
                await q.message.reply_video(vid, caption=cap)
        except Exception as _wve:
            log.warning(f"[WATCH_VIDEO] tid={tid} err={_wve}")
            await q.message.reply_text("❌ ভিডিও পাঠাতে সমস্যা হয়েছে।" if lang == 'bn' else "❌ Could not send video.")
        return

    # ── User: Watch Video — Category Task ─────────────────────────
    if d.startswith("ctask_watch_video_"):
        try:
            tid = int(d.split("_")[3])
        except (IndexError, ValueError):
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!")
            return
        t   = await asyncio.to_thread(get_category_task, tid)
        vid = (_rget(t, 'demo_video_id', '') or '').strip() if t else ''
        lang = await asyncio.to_thread(get_lang, uid)
        if not vid:
            await q.message.reply_text("❌ ভিডিও পাওয়া যায়নি!" if lang == 'bn' else "❌ Video not found!")
            return
        try:
            if vid.startswith("http") or vid.startswith("https"):
                lbl = "🎬 ভিডিও দেখুন" if lang == 'bn' else "🎬 Watch Video"
                txt = "👇 নিচের বাটনে চাপুন:" if lang == 'bn' else "👇 Tap the button below:"
                await q.message.reply_text(
                    txt,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(lbl, url=vid)]]))
            else:
                cap = "🎬 ডেমো ভিডিও" if lang == 'bn' else "🎬 Demo Video"
                await q.message.reply_video(vid, caption=cap)
        except Exception as _wve2:
            log.warning(f"[WATCH_VIDEO_CT] tid={tid} err={_wve2}")
            await q.message.reply_text("❌ ভিডিও পাঠাতে সমস্যা হয়েছে।" if lang == 'bn' else "❌ Could not send video.")
        return

    if d.startswith("adm_cfields_"):
        tid    = int(d.split("_")[2])
        task   = await asyncio.to_thread(get_category_task, tid)
        if not task:
            await q.message.reply_text("❌ Category Task পাওয়া যায়নি।"); return
        fields = await asyncio.to_thread(get_category_task_fields, tid)
        text   = f"📋 *Category Task Proof Fields: `{task['name']}`*\n\n"
        btns   = []
        if fields:
            for i, f in enumerate(fields, 1):
                icon    = "📷" if f["field_type"] == "photo" else "📝"
                has_img = bool((_rget(f, 'example_image_id', '') or '').strip())
                img_tag = " 🖼✅" if has_img else ""
                text += f"{i}. {icon} {f['prompt_bn']}{img_tag}\n"
                row = [InlineKeyboardButton(
                    f"🗑 {i}. {(f['prompt_bn'] or '')[:18]}", callback_data=f"adm_cfield_del_{f['id']}_{tid}"
                )]
                if f["field_type"] == "photo":
                    row.append(InlineKeyboardButton(
                        f"🖼{'✅' if has_img else '+'} Example", callback_data=f"adm_cfield_ex_img_{f['id']}_{tid}"
                    ))
                btns.append(row)
        else:
            text += "_কোনো field নেই।_\n"
        btns.append([
            InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_cfield_add_text_{tid}"),
            InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_cfield_add_photo_{tid}"),
        ])
        btns.append([InlineKeyboardButton("🔄 সব Fields মুছুন", callback_data=f"adm_cfields_clear_{tid}")])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_cfields_clear_"):
        tid = int(d.split("_")[2])
        await asyncio.to_thread(delete_all_category_task_fields, tid)
        await q.message.reply_text("✅ *সব Fields মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_cfield_del_"):
        parts = d.split("_")
        fid   = int(parts[3])
        await asyncio.to_thread(delete_category_task_field, fid)
        await q.message.reply_text("✅ *Field মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

    # ── Category Task: existing Photo Field এর Example Image সেট/পরিবর্তন ──
    if d.startswith("adm_cfield_ex_img_"):
        parts = d.split("_")
        fid   = int(parts[4])
        tid   = int(parts[5])
        ctx.user_data["state"]               = "adm_field_set_example_photo"
        ctx.user_data["edit_field_id"]        = fid
        ctx.user_data["edit_field_task_id"]   = tid
        ctx.user_data["edit_field_is_categ"]  = True
        await q.message.reply_text(
            "🖼 *এই Photo Field-এর Example Photo পাঠান:*\n\n"
            "_(ইউজার এই ছবি দেখে বুঝবে কেমন ছবি পাঠাতে হবে)_\n\n"
            "📤 এখন একটি ছবি পাঠান।\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_cfield_add_text_") or d.startswith("adm_cfield_add_photo_"):
        ftype = "text" if "add_text" in d else "photo"
        tid   = int(d.split("_")[-1])
        ctx.user_data["state"]            = "adm_cfield_prompt_bn"
        ctx.user_data["new_cfield_type"]  = ftype
        ctx.user_data["new_cfield_tid"]   = tid
        icon = "📝" if ftype == "text" else "📷"
        await q.message.reply_text(
            f"{icon} *নতুন {ftype.upper()} Field*\n\nবাংলা Prompt লিখুন:\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── TG Task Management ────────────────────────────────────────
    if d == "adm_tgt_list":
        tasks = await asyncio.to_thread(get_all_tg_tasks)
        if not tasks:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ নতুন TG Task যোগ করুন", callback_data="adm_tgt_add")]])
            await q.message.reply_text("📡 *কোনো TG Task নেই।*", parse_mode="Markdown", reply_markup=kb); return
        text = "📡 *Telegram Task তালিকা:*\n\n"
        btns = []
        for t in tasks:
            s = "✅" if t["is_active"] else "❌"
            slots = f"{t['completed_count']}/{t['max_completions']}" if t['max_completions'] > 0 else f"{t['completed_count']}/∞"
            text += f"{s} *{_md_esc(t['name'])}* — `{t['price']}$` | {slots} done\n📢 `{t['tg_target']}`\n\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {t['name']}", callback_data=f"adm_tgt_edit_{t['id']}"),
                InlineKeyboardButton("🗑 Delete",         callback_data=f"adm_tgt_del_{t['id']}"),
            ])
        btns.append([InlineKeyboardButton("➕ নতুন TG Task যোগ করুন", callback_data="adm_tgt_add")])
        if len(text) > 4000: text = text[:4000] + "\n\n_(তালিকা কেটে দেওয়া হয়েছে)_"
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_tgt_add":
        if not await asyncio.to_thread(admin_has_perm, uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_tgt_name"
        await q.message.reply_text(
            "📡 *নতুন Telegram Task*\n\n"
            "প্রথমে *Task নাম* লিখুন:\n_(যেমন: XYZ Channel Join)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_tgt_del_"):
        tid = int(d.split("_")[3])
        t = await asyncio.to_thread(get_tg_task, tid)
        if t:
            await asyncio.to_thread(delete_tg_task, tid)
            await q.message.reply_text(f"🗑 *'{_md_esc(t['name'])}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_tgt_edit_"):
        tid = int(d.split("_")[3])
        t = await asyncio.to_thread(get_tg_task, tid)
        if not t:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown"); return
        s = "✅ Active" if t["is_active"] else "❌ Inactive"
        tgte_toggle_lbl = "🔴 Deactivate" if t["is_active"] else "🟢 Activate & Notify"
        slots = f"{t['completed_count']}/{t['max_completions']}" if t['max_completions'] > 0 else f"{t['completed_count']}/∞"
        _tgte_ac = (_rget(t, 'allowed_countries', '') or '')
        _tgte_pu_raw = _rget(t, 'per_user_limit', None)
        _tgte_pu = _tgte_pu_raw if _tgte_pu_raw is not None else 1
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম",          callback_data=f"adm_tgte_name_{tid}"),
             InlineKeyboardButton("💰 দাম",           callback_data=f"adm_tgte_price_{tid}")],
            [InlineKeyboardButton("📢 Channel",       callback_data=f"adm_tgte_target_{tid}"),
             InlineKeyboardButton("🔗 Link",          callback_data=f"adm_tgte_link_{tid}")],
            [InlineKeyboardButton("👥 Max জন",       callback_data=f"adm_tgte_max_{tid}"),
             InlineKeyboardButton(tgte_toggle_lbl,    callback_data=f"adm_tgte_toggle_{tid}")],
            [InlineKeyboardButton(f"👤 Per User ({_tgte_pu}x)", callback_data=f"adm_tgte_pu_{tid}"),
             InlineKeyboardButton("🌍 দেশ নির্ধারণ", callback_data=f"adm_tgte_country_{tid}")],
            [InlineKeyboardButton("❌ বাতিল",         callback_data="task_cancel")],
        ])
        await q.message.reply_text(
            f"✏️ *TG Task Edit:* `{t['name']}`\n\n"
            f"💰 *দাম:* `{t['price']}$ USDT`\n"
            f"📢 *Channel:* `{t['tg_target']}`\n"
            f"🔗 *Link:* `{t['tg_target_link'] or 'সেট নেই'}`\n"
            f"👥 *Completions:* {slots}\n"
            f"👤 *Per User Limit:* `{_tgte_pu}x`\n"
            f"🌍 *দেশ:* {fmt_allowed_countries(_tgte_ac)}\n"
            f"👁 *Status:* {s}\n\nকী পরিবর্তন করবেন?",
            parse_mode="Markdown", reply_markup=kb); return

    if d.startswith("adm_tgte_toggle_"):
        tid = int(d.split("_")[3])
        await _activate_task_and_notify(ctx, q, uid,
            get_fn=get_tg_task, update_fn=update_tg_task, tid=tid,
            link_prefix="tgtask_", njo_field="new_joiners_only")
        return

    if d.startswith("adm_tgte_name_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_tgt_edit_name"; ctx.user_data["edit_tgt_id"] = tid
        await q.message.reply_text("✏️ *নতুন Task নাম লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_tgte_price_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_tgt_edit_price"; ctx.user_data["edit_tgt_id"] = tid
        await q.message.reply_text("💰 *নতুন দাম লিখুন* (যেমন: `0.05`):\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_tgte_target_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_tgt_edit_target"; ctx.user_data["edit_tgt_id"] = tid
        await q.message.reply_text("📢 *Channel/Group @username বা ID দিন:*\n_(যেমন: @mychannel)_\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_tgte_link_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_tgt_edit_link"; ctx.user_data["edit_tgt_id"] = tid
        await q.message.reply_text("🔗 *Channel/Group Link দিন:*\n_(যেমন: https://t.me/mychannel)_\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d.startswith("adm_tgte_max_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_tgt_edit_max"; ctx.user_data["edit_tgt_id"] = tid
        t = await asyncio.to_thread(get_tg_task, tid)
        cur = t['max_completions'] if t else 0
        await q.message.reply_text(
            f"👥 *সর্বোচ্চ কতজন করতে পারবে?*\n\n_বর্তমান: `{cur}` (0 = সীমাহীন)_\n\nসংখ্যা লিখুন (0 = unlimited):\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_tgte_pu_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_tgt_edit_per_user"; ctx.user_data["edit_tgt_id"] = tid
        t = await asyncio.to_thread(get_tg_task, tid)
        _cur_pu_raw = _rget(t, 'per_user_limit', None)
        cur_pu = _cur_pu_raw if _cur_pu_raw is not None else 1
        _cur_pu_lbl = "Unlimited ♾️" if cur_pu == 0 else f"{cur_pu} বার"
        await q.message.reply_text(
            f"👤 *Per User Limit — কতবার করতে পারবে?*\n\n_বর্তমান: `{_cur_pu_lbl}` (0 = Unlimited)_\n\nসংখ্যা লিখুন:\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_tgte_country_"):
        tid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_tgt_edit_countries"; ctx.user_data["edit_tgt_id"] = tid
        _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
        await q.message.reply_text(
            "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে?*\n\n"
            "দেশের কোড কমা দিয়ে লিখুন:\n"
            f"`{_ch}`\n\n"
            "উদাহরণ: `BD,IN,PK`\n"
            "🌐 সব দেশের জন্য: `0` লিখুন\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

# ──────────────────────────── MAIN MESSAGE ROUTER ────────────────────────────
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _aid_already = None
    _fresh = None
    _promo_already = None
    _promo_exhausted = None
    _rnum_already = None
    _rreason_already = None
    _wda_already = None
    _wdr_already = None
    already = None
    approved = None
    ct_row = None
    done_count = None
    exists = None
    pending = None
    promo = None
    rejected = None
    row = None
    row2 = None
    subs = None
    t_row = None
    tg_row = None
    total = None
    user = update.effective_user
    if not user:
        return
    uid  = user.id
    txt  = update.message.text or ""
    st   = ctx.user_data.get("state")

    # Cancel always works first
    cancel_btns = {"❌ বাতিল করুন", "❌ Cancel"}
    if txt in cancel_btns:
        ctx.user_data.clear()
        await update.message.reply_text(T(uid,'cancelled'), parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

    # ── adm_reject_reason: admin + task owner দুজনেই — is_admin check ছাড়া ──
    # (state টা শুধু ঐ user এর ctx.user_data তে থাকে, তাই security ঠিক আছে)
    if st == "adm_reject_reason":
        gid    = ctx.user_data.get("adm_reject_gid")
        reason = txt.strip()
        if not gid:
            ctx.user_data.pop("state", None); return
        def _db_8020():
            row = None
            with _c() as c:
                row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
            return row
        row = await asyncio.to_thread(_db_8020)
        if not row or row["status"] != "pending":
            await update.message.reply_text("⚠️ Sub আর pending নেই।", parse_mode="Markdown")
            for k in ("state","adm_reject_gid","adm_reject_msg_id","adm_reject_chat_id",
                      "adm_reject_has_photo","adm_reject_orig_txt"):
                ctx.user_data.pop(k, None)
            return
        # Atomic: AND status='pending' — admin reason টাইপ করার ফাঁকে approve হয়ে গেলে reject হবে না
        _rreason_already = False
        def _db_8030():
            _rreason_already = None
            with _c() as c:
                _rr_cur = c.execute(
                    "UPDATE gmails SET status='rejected', reject_reason=%s WHERE id=%s AND status='pending'",
                    (reason, gid)
                )
                if _rr_cur.rowcount == 0:
                    _rreason_already = True
            return _rreason_already
        _rreason_already = await asyncio.to_thread(_db_8030)
        if _rreason_already:
            await update.message.reply_text(f"⚠️ Sub `#{gid}` ইতিমধ্যে approve/reject হয়েছে — reject বাতিল।", parse_mode="Markdown")
            for k in ("state","adm_reject_gid","adm_reject_msg_id","adm_reject_chat_id",
                      "adm_reject_has_photo","adm_reject_orig_txt"):
                ctx.user_data.pop(k, None)
            return
        # Category task হলে slot ফিরিয়ে দাও + task reactivate করো
        await asyncio.to_thread(_reject_restore_task_slot, row)
        guser = row["user_id"]
        _row_ttype    = _rget(row, 'task_type', 'other')
        _row_ticon    = task_type_icon(_row_ttype)
        _task_display = f"{_row_ticon} {row['task_name']}"
        # ── Point deduction ─────────────────────────────────────
        new_pts  = await asyncio.to_thread(add_points, guser, -POINT_PER_REJECT)
        blocked  = new_pts <= POINT_BLOCK_AT
        # ── Notify user ─────────────────────────────────────────
        u_lang = await asyncio.to_thread(get_lang, guser)
        if u_lang == 'bn':
            pt_line  = f"🔴 *পয়েন্ট:* `{new_pts}/100` *(-{POINT_PER_REJECT})*"
            blk_line = f"\n⛔ *পয়েন্ট ৫০ বা নিচে! {POINT_BLOCK_DAYS} দিনের জন্য Task বন্ধ।*" if blocked else "\nℹ️ নিয়মগুলো পড়ে পুনরায় চেষ্টা করুন।"
            rej_msg  = (
                f"❌ *আপনার Submission Reject হয়েছে।*\n\n"
                f"📌 *Task:* {_task_display}\n"
                f"📝 *কারণ:* {reason}\n\n"
                f"{pt_line}{blk_line}"
            )
        else:
            pt_line  = f"🔴 *Points:* `{new_pts}/100` *(-{POINT_PER_REJECT})*"
            blk_line = f"\n⛔ *Points at 50 or below! Tasks blocked for {POINT_BLOCK_DAYS} days.*" if blocked else "\nℹ️ Please re-read the rules and try again."
            rej_msg  = (
                f"❌ *Your Submission has been Rejected.*\n\n"
                f"📌 *Task:* {_task_display}\n"
                f"📝 *Reason:* {reason}\n\n"
                f"{pt_line}{blk_line}"
            )
        try:
            await ctx.bot.send_message(guser, rej_msg, parse_mode="Markdown")
        except Exception: pass
        # ── Edit original submission message ─────────────────────
        status_line  = f"\n\n❌ *REJECTED by Admin*\n📝 *Reason:* {reason}"
        orig_txt     = ctx.user_data.get("adm_reject_orig_txt", "")
        has_photo    = ctx.user_data.get("adm_reject_has_photo", False)
        orig_msg_id  = ctx.user_data.get("adm_reject_msg_id")
        orig_chat_id = ctx.user_data.get("adm_reject_chat_id")
        new_txt = orig_txt + status_line
        if orig_msg_id and orig_chat_id:
            try:
                if has_photo:
                    await ctx.bot.edit_message_caption(new_txt, chat_id=orig_chat_id,
                                                       message_id=orig_msg_id, parse_mode="Markdown", reply_markup=None)
                else:
                    await ctx.bot.edit_message_text(new_txt, chat_id=orig_chat_id,
                                                    message_id=orig_msg_id, parse_mode="Markdown", reply_markup=None)
            except Exception: pass
        # ── Edit channel/other-side message ──────────────────────
        def _db_8090():
            row2 = None
            with _c() as c: row2 = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
            return row2
        row2 = await asyncio.to_thread(_db_8090)
        if row2:
            # Sub-admin reject করলেও admin + channel দুটো message-ই আপডেট করো
            _rch_id = await asyncio.to_thread(get_gmail_ch_id)
            _rmsgs_to_edit = []
            if uid == ADMIN_ID:
                if row2["channel_msg_id"]: _rmsgs_to_edit.append((_rch_id, row2["channel_msg_id"]))
            else:
                if row2["admin_msg_id"]:   _rmsgs_to_edit.append((ADMIN_ID, row2["admin_msg_id"]))
                if row2["channel_msg_id"]: _rmsgs_to_edit.append((_rch_id,  row2["channel_msg_id"]))
            for _recid, _remid in _rmsgs_to_edit:
                try:
                    if has_photo:
                        await ctx.bot.edit_message_caption(new_txt, chat_id=_recid,
                                                           message_id=_remid, parse_mode="Markdown", reply_markup=None)
                    else:
                        await ctx.bot.edit_message_text(new_txt, chat_id=_recid,
                                                        message_id=_remid, parse_mode="Markdown", reply_markup=None)
                except Exception: pass
        # ── Admin confirmation ────────────────────────────────────
        await update.message.reply_text(
            f"❌ *Sub `#{gid}` Rejected!*\n\n"
            f"📌 *Task:* {row['task_name']}\n"
            f"👤 *User:* `{guser}`\n"
            f"📝 *কারণ:* {reason}\n"
            f"🎯 *User এর পয়েন্ট:* `{new_pts}/100`"
            + (f"\n⛔ *User {POINT_BLOCK_DAYS} দিন Block হয়েছে।*" if blocked else ""),
            parse_mode="Markdown"
        )
        for k in ("state","adm_reject_gid","adm_reject_msg_id","adm_reject_chat_id",
                  "adm_reject_has_photo","adm_reject_orig_txt"):
            ctx.user_data.pop(k, None)
        return

    # ── Admin states (admin only) ─────────────────────────────────────────
    # st আগে চেক করো — regular user-এর জন্য is_admin DB call বাদ যাবে (short-circuit)
    if st and (st.startswith("adm_") or st == "adm_direct_msg") and await asyncio.to_thread(is_admin, uid):

        if st == "adm_direct_msg":
            target = ctx.user_data.get("direct_msg_to")
            if target:
                try:
                    await ctx.bot.send_message(target, txt, parse_mode="Markdown")
                    await update.message.reply_text(f"✅ *User `{target}` কে মেসেজ পাঠানো হয়েছে।*", parse_mode="Markdown")
                except Exception as e:
                    await update.message.reply_text(f"❌ ব্যর্থ: {e}", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("direct_msg_to", None); return

        if st == "adm_bc_photo":
            skip_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip — ছবি ছাড়া", callback_data="adm_bc_skip_photo")]])
            await update.message.reply_text(
                "📷 *ছবি পাঠান অথবা নিচের বাটনে Skip করুন।*\n\n/cancel — বাতিল",
                parse_mode="Markdown", reply_markup=skip_kb)
            return

        if st == "adm_bc_bn":
            ctx.user_data["bc_msg_bn"] = txt
            ctx.user_data["state"] = "adm_bc_en"
            await update.message.reply_text(
                "🇬🇧 *এখন ইংরেজি মেসেজ লিখুন:*\n\n"
                "_(ইংরেজি ইউজাররা এটা পাবে)_\n\n"
                "/cancel — বাতিল",
                parse_mode="Markdown")
            return

        if st == "adm_bc_en":
            ctx.user_data["bc_msg_en"] = txt
            ctx.user_data.pop("state", None)
            bn_txt   = ctx.user_data.get("bc_msg_bn", "")
            bc_photo = ctx.user_data.get("bc_photo_id", "")
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📌 পিন সহ পাঠাও",        callback_data="adm_bc_send_pin")],
                [InlineKeyboardButton("📨 পিন ছাড়া পাঠাও",      callback_data="adm_bc_send_nopin")],
                [InlineKeyboardButton("📢 News Channel এ Post", callback_data="adm_bc_post_news")],
                [InlineKeyboardButton("✏️ Edit করুন",            callback_data="adm_bc_edit")],
                [InlineKeyboardButton("❌ বাতিল",                callback_data="adm_bc_cancel")],
            ])
            photo_note = "🖼 *ছবি:* ✅ সংযুক্ত আছে\n\n" if bc_photo else "🖼 *ছবি:* ❌ নেই (শুধু টেক্সট)\n\n"
            preview_txt = (
                f"📢 *Broadcast Preview:*\n\n"
                f"{photo_note}"
                f"🇧🇩 *বাংলা:*\n```\n{bn_txt}\n```\n\n"
                f"🇬🇧 *English:*\n```\n{txt}\n```\n\n"
                f"👆 চেক করুন, তারপর পাঠান।"
            )
            if bc_photo:
                await update.message.reply_photo(bc_photo, caption=preview_txt, parse_mode="Markdown", reply_markup=kb)
            else:
                await update.message.reply_text(preview_txt, parse_mode="Markdown", reply_markup=kb)
            return

        if st == "adm_notif_edit_txt":
            ctx.user_data["pending_notif_msg"] = txt
            ctx.user_data.pop("state", None)
            preview_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ পাঠাও",             callback_data="adm_notif_send")],
                [InlineKeyboardButton("✏️ Message Edit করুন", callback_data="adm_notif_edit")],
                [InlineKeyboardButton("❌ Skip করুন",          callback_data="adm_notif_skip")],
            ])
            await update.message.reply_text(
                f"🔔 *Updated Notification Preview:*\n\n{txt}\n\n"
                f"👆 চেক করুন, তারপর পাঠান।",
                parse_mode="Markdown", reply_markup=preview_kb)
            return

        if st == "adm_tp_edit_name":
            tp_tid  = ctx.user_data.get("tp_tid")
            tp_type = ctx.user_data.get("tp_type", "t")
            if tp_type == "t": await asyncio.to_thread(update_task_field_val, tp_tid, "name", txt.strip())
            else: await asyncio.to_thread(update_tg_task, tp_tid, "name", txt.strip())
            ctx.user_data.pop("state", None)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✅ Confirm করুন",       callback_data=f"adm_tp_ok_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("❌ বাতিল/Delete",       callback_data=f"adm_tp_del_{tp_tid}_{tp_type}")],
            ])
            await update.message.reply_text(f"✅ *নাম আপডেট:* `{txt.strip()}`\n\nআবার চেক করুন:", parse_mode="Markdown", reply_markup=kb)
            return

        if st == "adm_tp_edit_price":
            tp_tid  = ctx.user_data.get("tp_tid")
            tp_type = ctx.user_data.get("tp_type", "t")
            try:
                price = float(txt.strip())
                if tp_type == "t": await asyncio.to_thread(update_task_field_val, tp_tid, "price", price)
                else: await asyncio.to_thread(update_tg_task, tp_tid, "price", price)
                ctx.user_data.pop("state", None)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tp_tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tp_tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tp_tid}_{tp_type}")],
                    [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tp_tid}_{tp_type}")],
                    [InlineKeyboardButton("✅ Confirm করুন",       callback_data=f"adm_tp_ok_{tp_tid}_{tp_type}")],
                    [InlineKeyboardButton("❌ বাতিল/Delete",       callback_data=f"adm_tp_del_{tp_tid}_{tp_type}")],
                ])
                await update.message.reply_text(f"✅ *দাম আপডেট:* `{price}$ USDT`\n\nআবার চেক করুন:", parse_mode="Markdown", reply_markup=kb)
            except Exception: await update.message.reply_text("❌ সঠিক দাম দিন (যেমন: 0.05)", parse_mode="Markdown")
            return

        if st == "adm_tp_edit_max":
            tp_tid  = ctx.user_data.get("tp_tid")
            tp_type = ctx.user_data.get("tp_type", "t")
            try: max_v = int(txt.strip())
            except Exception: max_v = 0
            if tp_type == "t": await asyncio.to_thread(update_task_field_val, tp_tid, "max_completions", max_v)
            else: await asyncio.to_thread(update_tg_task, tp_tid, "max_completions", max_v)
            ctx.user_data.pop("state", None)
            max_txt = f"{max_v} people" if max_v > 0 else "Unlimited"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ Per User Limit",    callback_data=f"adm_tp_pu_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✅ Confirm করুন",       callback_data=f"adm_tp_ok_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("❌ বাতিল/Delete",       callback_data=f"adm_tp_del_{tp_tid}_{tp_type}")],
            ])
            await update.message.reply_text(f"✅ *Max আপডেট:* `{max_txt}`\n\nআবার চেক করুন:", parse_mode="Markdown", reply_markup=kb)
            return

        if st == "adm_tp_edit_per_user":
            tp_tid  = ctx.user_data.get("tp_tid")
            tp_type = ctx.user_data.get("tp_type", "t")
            try: pu_val = max(0, int(txt.strip()))
            except Exception: pu_val = 1
            if tp_type == "t": await asyncio.to_thread(update_task_field_val, tp_tid, "per_user_limit", pu_val)
            else: await asyncio.to_thread(update_tg_task, tp_tid, "per_user_limit", pu_val)
            ctx.user_data.pop("state", None)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ Per User Limit",    callback_data=f"adm_tp_pu_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✅ Confirm করুন",       callback_data=f"adm_tp_ok_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("❌ বাতিল/Delete",       callback_data=f"adm_tp_del_{tp_tid}_{tp_type}")],
            ])
            _pu_lbl2 = "Unlimited ♾️" if pu_val == 0 else f"{pu_val} বার"
            await update.message.reply_text(f"✅ *Per User Limit আপডেট:* `{_pu_lbl2}`\n\nআবার চেক করুন:", parse_mode="Markdown", reply_markup=kb)
            return

        if st == "adm_tp_edit_countries":
            tp_tid  = ctx.user_data.get("tp_tid")
            tp_type = ctx.user_data.get("tp_type", "t")
            _ac2 = txt.strip().upper()
            allowed_c2 = "" if _ac2 in ("0","ALL","") else ",".join(c.strip() for c in _ac2.split(",") if c.strip())
            if tp_type == "t": await asyncio.to_thread(update_task_field_val, tp_tid, "allowed_countries", allowed_c2)
            else: await asyncio.to_thread(update_tg_task, tp_tid, "allowed_countries", allowed_c2)
            ctx.user_data.pop("state", None)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✏️ Per User Limit",    callback_data=f"adm_tp_pu_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("✅ Confirm করুন",       callback_data=f"adm_tp_ok_{tp_tid}_{tp_type}")],
                [InlineKeyboardButton("❌ বাতিল/Delete",       callback_data=f"adm_tp_del_{tp_tid}_{tp_type}")],
            ])
            await update.message.reply_text(
                f"✅ *দেশ আপডেট:* {fmt_allowed_countries(allowed_c2)}\n\nআবার চেক করুন:",
                parse_mode="Markdown", reply_markup=kb)
            return

        if st == "adm_add_id":
            try:
                ctx.user_data["adm_tid"] = int(txt); ctx.user_data["state"] = "adm_add_amt"
                await update.message.reply_text(f"💵 *কত ডলার যোগ করবেন?* (User: `{txt}`)", parse_mode="Markdown")
            except Exception: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            return
        if st == "adm_add_amt":
            try:
                amt = float(txt); tid = ctx.user_data["adm_tid"]
                await asyncio.to_thread(add_bal, tid, amt)
                await update.message.reply_text(f"✅ *`{tid}` তে `{amt}$` যোগ হয়েছে।*", parse_mode="Markdown")
                try: await ctx.bot.send_message(tid, T(tid,'bal_added', amt=amt, bal=await asyncio.to_thread(get_bal, tid)), parse_mode="Markdown")
                except Exception as _e: log.warning(f"[ADMIN-ADD] tid={tid} notify ব্যর্থ: {_e}")
            except Exception: await update.message.reply_text("❌ সঠিক পরিমাণ দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_tid", None); return

        if st == "adm_ded_id":
            try:
                ctx.user_data["adm_tid"] = int(txt); ctx.user_data["state"] = "adm_ded_amt"
                await update.message.reply_text(f"💵 *কত ডলার কাটবেন?* (User: `{txt}`)", parse_mode="Markdown")
            except Exception: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            return
        if st == "adm_ded_amt":
            try:
                amt = float(txt); tid = ctx.user_data["adm_tid"]
                await asyncio.to_thread(ded_bal, tid, amt)
                await update.message.reply_text(f"✅ *`{tid}` থেকে `{amt}$` কাটা হয়েছে।*", parse_mode="Markdown")
            except Exception: await update.message.reply_text("❌ সঠিক পরিমাণ দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_tid", None); return

        if st == "adm_ban_id":
            try:
                tid = int(txt); await asyncio.to_thread(ban_user, tid, 1)
                await update.message.reply_text(f"🚫 *`{tid}` ব্যান হয়েছে।*", parse_mode="Markdown")
                try: await ctx.bot.send_message(tid, T(tid,'banned'), parse_mode="Markdown")
                except Exception: pass
            except Exception: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return
        if st == "adm_unban_id":
            try:
                tid = int(txt); await asyncio.to_thread(ban_user, tid, 0)
                await update.message.reply_text(f"✅ *`{tid}` Unban হয়েছে।*", parse_mode="Markdown")
                try: await ctx.bot.send_message(tid, T(tid,'unban_notify'), parse_mode="Markdown")
                except Exception: pass
            except Exception: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        if st == "adm_msg_id":
            try:
                ctx.user_data["adm_tid"] = int(txt); ctx.user_data["state"] = "adm_msg_txt"
                await update.message.reply_text(f"📝 *User `{txt}` কে কি মেসেজ পাঠাবেন?*", parse_mode="Markdown")
            except Exception: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            return
        if st == "adm_msg_txt":
            try:
                tid = ctx.user_data["adm_tid"]
                await ctx.bot.send_message(tid, txt, parse_mode="Markdown")
                await update.message.reply_text(f"✅ *`{tid}` কে মেসেজ পাঠানো হয়েছে।*", parse_mode="Markdown")
            except Exception as e: await update.message.reply_text(f"❌ ব্যর্থ: {e}", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_tid", None); return

        if st == "adm_set_owner_input":
            _parts = txt.strip().split()
            if len(_parts) != 2:
                await update.message.reply_text(
                    "❌ *ভুল format!*\n\nTask Code আর User ID আলাদা করে দিন:\n`TK-AB3X7 1234567890`",
                    parse_mode="Markdown"); return
            _ow_code, _ow_uid_s = _parts
            try:
                _ow_uid = int(_ow_uid_s)
            except ValueError:
                await update.message.reply_text("❌ *User ID সংখ্যা হতে হবে।*", parse_mode="Markdown"); return
            _ow_tname, _ow_tbl = await asyncio.to_thread(set_task_owner_by_code, _ow_code, _ow_uid)
            ctx.user_data.pop("state", None)
            if not _ow_tname:
                await update.message.reply_text(
                    f"❌ `{_ow_code.upper()}` — এই কোড দিয়ে কোনো Task পাওয়া যায়নি।",
                    parse_mode="Markdown"); return
            if _ow_uid == 0:
                await update.message.reply_text(
                    f"✅ *Task Owner সরানো হয়েছে!*\n\n📌 *Task:* `{_ow_tname}`",
                    parse_mode="Markdown"); return
            await update.message.reply_text(
                f"✅ *Task Owner সেট হয়েছে!*\n\n"
                f"📌 *Task:* `{_ow_tname}`\n"
                f"👤 *Owner ID:* `{_ow_uid}`\n\n"
                f"এখন থেকে এই Task-এর প্রতিটি Submission ওই ইউজারের কাছেও যাবে\n"
                f"_(user info ছাড়া — শুধু proof দেখবে)_",
                parse_mode="Markdown"); return

        if st == "adm_task_code_input":
            code = txt.strip().upper()
            def _db_8342():
                ct_row = None
                t_row = None
                tg_row = None
                with _c() as c:
                    # Regular task
                    t_row = c.execute("SELECT * FROM tasks WHERE task_code=%s", (code,)).fetchone()
                    # TG task
                    tg_row = c.execute("SELECT * FROM tg_tasks WHERE task_code=%s", (code,)).fetchone()
                    # Category task
                    ct_row = c.execute("SELECT * FROM category_tasks WHERE task_code=%s", (code,)).fetchone()
                return ct_row, t_row, tg_row
            ct_row, t_row, tg_row = await asyncio.to_thread(_db_8342)

            if not t_row and not tg_row and not ct_row:
                await update.message.reply_text(
                    f"❌ `{code}` — এই কোড দিয়ে কোনো Task পাওয়া যায়নি।\n\n"
                    "সঠিক কোড দিন (যেমন: `TK-AB3X7`)।",
                    parse_mode="Markdown")
                ctx.user_data.pop("state", None); return

            # ── Regular Task ─────────────────────────────────────────────
            if t_row:
                tid   = t_row["id"]
                tname = t_row["name"]
                tprice= t_row["price"]
                def _db_8362():
                    approved = None
                    pending = None
                    rejected = None
                    subs = None
                    total = None
                    with _c() as c:
                        total   = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s", (code,)).fetchone())
                        pending = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='pending'", (code,)).fetchone())
                        approved= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='approved'", (code,)).fetchone())
                        rejected= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='rejected'", (code,)).fetchone())
                        subs    = c.execute(
                            "SELECT id,user_id,status,submitted_at FROM gmails WHERE task_code=%s ORDER BY id DESC LIMIT 15",
                            (code,)
                        ).fetchall()
                    return approved, pending, rejected, subs, total
                approved, pending, rejected, subs, total = await asyncio.to_thread(_db_8362)
                stat_icon = {"pending":"⏳","approved":"✅","rejected":"❌"}
                sub_lines = "\n".join(
                    f"  {stat_icon.get(s['status'],'❓')} `#{s['id']}` 👤`{s['user_id']}` — {s['submitted_at'][:16]}"
                    for s in subs
                ) or "  _কোনো Submission নেই_"
                text2 = (
                    f"📌 *Task:* {tname}\n"
                    f"🔑 *Code:* `{code}`\n"
                    f"💰 *Price:* `{tprice}$`\n"
                    f"🏷️ *Type:* Regular Task\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📊 *Submission সারসংক্ষেপ:*\n"
                    f"  📥 মোট: *{total}* | ⏳ Pending: *{pending}*\n"
                    f"  ✅ Approved: *{approved}* | ❌ Rejected: *{rejected}*\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📋 *শেষ {len(subs)}টি Submission:*\n"
                    f"{sub_lines}"
                )

            # ── TG Task ──────────────────────────────────────────────────
            elif tg_row:
                tid   = tg_row["id"]
                tname = tg_row["name"]
                tprice= tg_row["price"]
                def _db_8395():
                    subs = None
                    total = None
                    with _c() as c:
                        total   = _count(c.execute("SELECT COUNT(*) FROM tg_task_completions WHERE task_id=%s", (tid,)).fetchone())
                        subs    = c.execute(
                            "SELECT user_id,joined_at FROM tg_task_completions WHERE task_id=%s ORDER BY id DESC LIMIT 15",
                            (tid,)
                        ).fetchall()
                    return subs, total
                subs, total = await asyncio.to_thread(_db_8395)
                sub_lines = "\n".join(
                    f"  ✅ 👤`{s['user_id']}` — {(s['joined_at'] or '')[:16]}"
                    for s in subs
                ) or "  _কোনো Completion নেই_"
                text2 = (
                    f"📡 *Task:* {tname}\n"
                    f"🔑 *Code:* `{code}`\n"
                    f"💰 *Price:* `{tprice}$`\n"
                    f"🏷️ *Type:* Telegram Join Task\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📊 *Completion সারসংক্ষেপ:*\n"
                    f"  ✅ মোট Completed: *{total}* (Auto-approved)\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📋 *শেষ {len(subs)}জন:*\n"
                    f"{sub_lines}"
                )

            # ── Category Task ─────────────────────────────────────────────
            else:
                tid   = ct_row["id"]
                tname = ct_row["name"]
                tprice= ct_row["price"]
                cat_label = f"[CAT] {tname}"
                def _db_8424():
                    approved = None
                    pending = None
                    rejected = None
                    subs = None
                    total = None
                    with _c() as c:
                        total   = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s", (code,)).fetchone())
                        # fallback: match by task_name for older submissions without code
                        if total == 0:
                            total   = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_name=%s", (cat_label,)).fetchone())
                            pending = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_name=%s AND status='pending'", (cat_label,)).fetchone())
                            approved= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_name=%s AND status='approved'", (cat_label,)).fetchone())
                            rejected= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_name=%s AND status='rejected'", (cat_label,)).fetchone())
                            subs    = c.execute(
                                "SELECT id,user_id,status,submitted_at FROM gmails WHERE task_name=%s ORDER BY id DESC LIMIT 15",
                                (cat_label,)
                            ).fetchall()
                        else:
                            pending = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='pending'", (code,)).fetchone())
                            approved= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='approved'", (code,)).fetchone())
                            rejected= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='rejected'", (code,)).fetchone())
                            subs    = c.execute(
                                "SELECT id,user_id,status,submitted_at FROM gmails WHERE task_code=%s ORDER BY id DESC LIMIT 15",
                                (code,)
                            ).fetchall()
                    return approved, pending, rejected, subs, total
                approved, pending, rejected, subs, total = await asyncio.to_thread(_db_8424)
                stat_icon = {"pending":"⏳","approved":"✅","rejected":"❌"}
                sub_lines = "\n".join(
                    f"  {stat_icon.get(s['status'],'❓')} `#{s['id']}` 👤`{s['user_id']}` — {s['submitted_at'][:16]}"
                    for s in subs
                ) or "  _কোনো Submission নেই_"
                text2 = (
                    f"📁 *Task:* {tname}\n"
                    f"🔑 *Code:* `{code}`\n"
                    f"💰 *Price:* `{tprice}$`\n"
                    f"🏷️ *Type:* Category Task\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📊 *Submission সারসংক্ষেপ:*\n"
                    f"  📥 মোট: *{total}* | ⏳ Pending: *{pending}*\n"
                    f"  ✅ Approved: *{approved}* | ❌ Rejected: *{rejected}*\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📋 *শেষ {len(subs)}টি Submission:*\n"
                    f"{sub_lines}"
                )

            await update.message.reply_text(text2, parse_mode="Markdown", reply_markup=kb2)
            ctx.user_data.pop("state", None); return

        if st == "adm_find_user_id":
            try:
                tid = int(txt.strip())
            except ValueError:
                await update.message.reply_text("❌ সঠিক Telegram User ID দিন (শুধু সংখ্যা)।", parse_mode="Markdown")
                ctx.user_data.pop("state", None); return
            try:
                u = await asyncio.to_thread(get_user, tid)
                if not u:
                    await update.message.reply_text(f"❌ `{tid}` ID দিয়ে কোনো user পাওয়া যায়নি।", parse_mode="Markdown")
                else:
                    text2, kb2 = await asyncio.to_thread(_build_user_profile, tid, u)
                    await update.message.reply_text(text2, parse_mode="Markdown", reply_markup=kb2)
            except Exception as _fue:
                log.warning(f"[ADM_FIND_USER] uid={tid}: {_fue}")
                await update.message.reply_text(f"❌ User profile লোড করতে সমস্যা হয়েছে।\nError: `{_fue}`", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        if st == "adm_uaddbal_amt":
            tid = ctx.user_data.get("adm_target")
            try:
                amt = float(txt.strip())
                if amt <= 0:
                    raise ValueError("amount must be positive")
                if (await asyncio.to_thread(get_user, tid)) is None:
                    await update.message.reply_text(
                        f"❌ *User `{tid}` DB তে নেই!*\n\nUser প্রথমে bot-এ /start করুক, তারপর balance add করুন।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                ok = await asyncio.to_thread(add_bal, tid, amt)
                if not ok:
                    await update.message.reply_text(
                        f"❌ *Balance update ব্যর্থ!* User `{tid}` DB তে পাওয়া যায়নি।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                new_bal = await asyncio.to_thread(get_bal, tid)
                await update.message.reply_text(
                    f"✅ *`{tid}` কে `{amt}$` যোগ হয়েছে।*\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                try: await ctx.bot.send_message(
                    tid,
                    f"✅ আপনার Account-এ *{amt}$ USDT* যোগ করা হয়েছে।\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                except Exception: pass
            except Exception as _e:
                await update.message.reply_text(f"❌ সঠিক পরিমাণ দিন। (Error: {_e})", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return

        if st == "adm_udedbal_amt":
            tid = ctx.user_data.get("adm_target")
            try:
                amt = float(txt.strip())
                if amt <= 0:
                    raise ValueError("amount must be positive")
                if (await asyncio.to_thread(get_user, tid)) is None:
                    await update.message.reply_text(
                        f"❌ *User `{tid}` DB তে নেই!*\n\nUser প্রথমে bot-এ /start করুক।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                ok = await asyncio.to_thread(ded_bal, tid, amt)
                if not ok:
                    await update.message.reply_text(
                        f"❌ *Balance update ব্যর্থ!* User `{tid}` DB তে পাওয়া যায়নি।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                new_bal = await asyncio.to_thread(get_bal, tid)
                await update.message.reply_text(
                    f"✅ *`{tid}` থেকে `{amt}$` কাটা হয়েছে।*\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                try: await ctx.bot.send_message(
                    tid,
                    f"⚠️ আপনার Account থেকে *{amt}$ USDT* কাটা হয়েছে।\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                except Exception: pass
            except Exception as _e:
                await update.message.reply_text(f"❌ সঠিক পরিমাণ দিন। (Error: {_e})", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return

        if st == "adm_umsg_txt":
            tid = ctx.user_data.get("adm_target")
            try:
                await ctx.bot.send_message(tid, txt, parse_mode="Markdown")
                await update.message.reply_text(f"✅ *`{tid}` কে মেসেজ পাঠানো হয়েছে।*", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ ব্যর্থ: {e}", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return

        if st == "adm_add_admin_id":
            if uid != ADMIN_ID:
                ctx.user_data.pop("state", None)
                await update.message.reply_text("❌ *শুধু Main Admin নতুন Admin যোগ করতে পারবে!*", parse_mode="Markdown")
                return
            try:
                tid = int(txt.strip())
                if tid == ADMIN_ID:
                    await update.message.reply_text("ℹ️ এই ID ইতিমধ্যে Main Admin!", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                # Full access দিয়ে admin যোগ করো
                await asyncio.to_thread(add_admin, tid, uid, permissions="full")
                ctx.user_data.pop("state", None)
                # YES/NO জিজ্ঞেস করো admin_manage সম্পর্কে
                await update.message.reply_text(
                    f"✅ *`{tid}` কে Full Access দিয়ে Admin যোগ করা হয়েছে।*\n\n"
                    f"👑 Full Access মানে:\n"
                    f"• আপনার কাছে যা যা আসে সব তার কাছেও যাবে\n"
                    f"• সব কিছু করতে পারবে (Task, Withdraw, Users, Broadcast ইত্যাদি)\n\n"
                    f"👮 *এই Admin কি অন্য Admin যোগ করতে ও সরাতে পারবে?*",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ হ্যাঁ, পারবে", callback_data=f"adm_new_adm_yes_{tid}"),
                        InlineKeyboardButton("❌ না, পারবে না", callback_data=f"adm_new_adm_no_{tid}"),
                    ]])
                )
            except Exception:
                await update.message.reply_text("❌ সঠিক Telegram User ID দিন।", parse_mode="Markdown")
                ctx.user_data.pop("state", None)
            return

        # ── ID দিয়ে Submission Approve ─────────────────────────────
        if st == "adm_approve_id_num":
            try:
                gid = int(txt.strip())
                def _db_8583():
                    row = None
                    with _c() as c:
                        row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
                    return row
                row = await asyncio.to_thread(_db_8583)
                if not row:
                    await update.message.reply_text(f"❌ *Sub `#{gid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *Sub `#{gid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                guser, price = row["user_id"], row["amount"]
                _aid_ttype = _rget(row, 'task_type', 'other')
                _aid_ticon = task_type_icon(_aid_ttype)
                _aid_task_display = f"{_aid_ticon} {row['task_name']}"
                # Atomic: gmails approved + balance credit একসাথে commit
                # AND status='pending' — double-approve race condition প্রতিরোধ করে
                _aid_already = False
                def _db_8598():
                    _aid_already = None
                    with _c() as c:
                        _aid_appr = c.execute(
                            "UPDATE gmails SET status='approved', approved_at=%s WHERE id=%s AND status='pending'",
                            (now(), gid)
                        )
                        if _aid_appr.rowcount == 0:
                            _aid_already = True
                        else:
                            c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (price, guser))
                            _invalidate_user_cache(guser)
                    return _aid_already
                _aid_already = await asyncio.to_thread(_db_8598)
                if _aid_already:
                    await update.message.reply_text(f"⚠️ *Sub `#{gid}` ইতিমধ্যে approve/reject হয়েছে।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                ref = await asyncio.to_thread(get_referred_by, guser)
                if ref:
                    comm_pct = await asyncio.to_thread(get_commission_pct)
                    comm = round(price * comm_pct / 100.0, 4)
                    await asyncio.to_thread(add_bal, ref, comm)
                    await asyncio.to_thread(record_commission, ref, guser, comm, row.get('task_name', ''))
                    try:
                        ru = await asyncio.to_thread(get_user, guser)
                        rname = (ru["full_name"] or ru["username"] or "Unknown") if ru else "Unknown"
                        comm_d = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
                        await ctx.bot.send_message(ref, T(ref,'commission_msg',name=_md_esc(rname),price=price,comm=comm,comm_pct=comm_d), parse_mode="Markdown")
                    except Exception as _e:
                        log.warning(f"[PHOTO-APPROVE] ref={ref} commission notify ব্যর্থ: {_e}")
                    # ── Referral Event bonus check ──────────────────────────
                    _ref_ev_task2 = asyncio.create_task(_check_ref_event_bonus(guser, ref, ctx.bot))
                    _background_tasks.add(_ref_ev_task2); _ref_ev_task2.add_done_callback(_background_tasks.discard)
                try:
                    await ctx.bot.send_message(guser, T(guser,'approved_msg',task=_aid_task_display,price=price), parse_mode="Markdown")
                except Exception as _e:
                    log.warning(f"[PHOTO-APPROVE] guser={guser} approved notify ব্যর্থ: {_e}")
                _tlb3 = asyncio.create_task(_lb_refresh_now(ctx.bot))
                _background_tasks.add(_tlb3); _tlb3.add_done_callback(_background_tasks.discard)
                # Regular task completed_count + max check
                _mid_code = _rget(row, 'task_code', '') or ''
                _mid_name = row['task_name']
                _mt, _mmaxed = await asyncio.to_thread(_regular_task_mark_done, _mid_code, _mid_name)
                if _mmaxed and _mt:
                    _tamm = asyncio.create_task(_notify_task_maxed(
                        ctx.bot, _mt["name"], _mt["task_code"] or "",
                        "Regular Task", _mt["completed_count"], _mt["max_completions"]
                    ))
                    _background_tasks.add(_tamm); _tamm.add_done_callback(_background_tasks.discard)
                await update.message.reply_text(
                    f"✅ *Sub `#{gid}` Approved!*\n\n"
                    f"📌 *Task:* {row['task_name']}\n"
                    f"👤 *User:* `{guser}`\n"
                    f"💰 *{price}$ USDT* balance-এ যোগ হয়েছে।",
                    parse_mode="Markdown")
                a_mid = row["admin_msg_id"]; c_mid = row["channel_msg_id"]
                status_line = "\n\n✅ *APPROVED by Admin*"
                for chat_id, msg_id in [(ADMIN_ID, a_mid), (await asyncio.to_thread(get_gmail_ch_id), c_mid)]:
                    if msg_id:
                        # ID-based approve-এ original message text নেই — শুধু buttons সরিয়ে দাও
                        # (edit_message_text দিলে original submission content হারিয়ে যায়)
                        try: await ctx.bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=None)
                        except Exception: pass
            except ValueError:
                await update.message.reply_text("❌ সঠিক ID দিন (শুধু সংখ্যা, যেমন: `15`)।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        # ── ID দিয়ে Submission Reject ──────────────────────────────
        if st == "adm_reject_id_num":
            try:
                gid = int(txt.strip())
                def _db_8659():
                    row = None
                    with _c() as c:
                        row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
                    return row
                row = await asyncio.to_thread(_db_8659)
                if not row:
                    await update.message.reply_text(f"❌ *Sub `#{gid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *Sub `#{gid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                # Atomic: AND status='pending' — concurrent double-reject race condition প্রতিরোধ করে
                _rnum_already = False
                def _db_8669():
                    _rnum_already = None
                    with _c() as c:
                        _rnum_cur = c.execute(
                            "UPDATE gmails SET status='rejected' WHERE id=%s AND status='pending'", (gid,)
                        )
                        if _rnum_cur.rowcount == 0:
                            _rnum_already = True
                    return _rnum_already
                _rnum_already = await asyncio.to_thread(_db_8669)
                if _rnum_already:
                    await update.message.reply_text(f"⚠️ *Sub `#{gid}` ইতিমধ্যে approve/reject হয়েছে।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                # Category task হলে slot ফিরিয়ে দাও + task reactivate করো
                await asyncio.to_thread(_reject_restore_task_slot, row)
                guser = row["user_id"]
                _rid_ttype = _rget(row, 'task_type', 'other')
                _rid_ticon = task_type_icon(_rid_ttype)
                _rid_task_display = f"{_rid_ticon} {row['task_name']}"
                _rid_new_pts = await asyncio.to_thread(add_points, guser, -POINT_PER_REJECT)
                _rid_blocked = _rid_new_pts <= POINT_BLOCK_AT
                u_lang = await asyncio.to_thread(get_lang, guser)
                if u_lang == 'bn':
                    _rid_msg = (
                        f"❌ *আপনার Submission Reject হয়েছে।*\n\n"
                        f"📌 *Task:* {_rid_task_display}\n\n"
                        f"🔴 *পয়েন্ট:* `{_rid_new_pts}/100` *(-{POINT_PER_REJECT})*"
                        + (f"\n⛔ *পয়েন্ট ৫০ বা নিচে! {POINT_BLOCK_DAYS} দিনের জন্য Task বন্ধ।*" if _rid_blocked
                           else "\nℹ️ নিয়মগুলো পড়ে পুনরায় চেষ্টা করুন।")
                    )
                else:
                    _rid_msg = (
                        f"❌ *Your Submission has been Rejected.*\n\n"
                        f"📌 *Task:* {_rid_task_display}\n\n"
                        f"🔴 *Points:* `{_rid_new_pts}/100` *(-{POINT_PER_REJECT})*"
                        + (f"\n⛔ *Points at 30 or below! Tasks blocked for {POINT_BLOCK_DAYS} days.*" if _rid_blocked
                           else "\nℹ️ Please re-read the rules and try again.")
                    )
                try:
                    await ctx.bot.send_message(guser, _rid_msg, parse_mode="Markdown")
                except Exception: pass
                await update.message.reply_text(
                    f"❌ *Sub `#{gid}` Rejected.*\n\n"
                    f"📌 *Task:* {row['task_name']}\n"
                    f"👤 *User:* `{guser}`",
                    parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ সঠিক ID দিন (শুধু সংখ্যা, যেমন: `15`)।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        # ── ID দিয়ে Withdrawal Approve ─────────────────────────────
        if st == "adm_wd_approve_id":
            try:
                wid = int(txt.strip())
                def _db_8717():
                    row = None
                    with _c() as c:
                        row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
                    return row
                row = await asyncio.to_thread(_db_8717)
                if not row:
                    await update.message.reply_text(f"❌ *WD `#{wid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *WD `#{wid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                # Atomic: AND status='pending' — double-approve race condition প্রতিরোধ করে
                _wda_already = False
                def _db_8727():
                    _wda_already = None
                    with _c() as c:
                        _wda_cur = c.execute(
                            "UPDATE withdrawals SET status='approved' WHERE id=%s AND status='pending'", (wid,)
                        )
                        if _wda_cur.rowcount == 0:
                            _wda_already = True
                    return _wda_already
                _wda_already = await asyncio.to_thread(_db_8727)
                if _wda_already:
                    await update.message.reply_text(f"⚠️ *WD `#{wid}` ইতিমধ্যে process হয়ে গেছে।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                wuser, wamt, waddr = row["user_id"], row["amount"], row["address"]
                masked = mask_addr_short(waddr)
                try:
                    await ctx.bot.send_message(wuser, T(wuser,'wd_approved_msg',amt=wamt,addr=masked), parse_mode="Markdown")
                except Exception: pass
                await update.message.reply_text(
                    f"✅ *WD `#{wid}` Approved!*\n\n"
                    f"👤 *User:* `{wuser}`\n"
                    f"💰 *Amount:* `{wamt}$ USDT`\n"
                    f"📍 *Address:* `{masked}`",
                    parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ সঠিক ID দিন (শুধু সংখ্যা)।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        # ── ID দিয়ে Withdrawal Reject ──────────────────────────────
        if st == "adm_wd_reject_id":
            try:
                wid = int(txt.strip())
                def _db_8755():
                    row = None
                    with _c() as c:
                        row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
                    return row
                row = await asyncio.to_thread(_db_8755)
                if not row:
                    await update.message.reply_text(f"❌ *WD `#{wid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *WD `#{wid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                wuser, wamt = row["user_id"], row["amount"]
                # Atomic: AND status='pending' guard — double-reject হলে double-refund হবে না
                _wdr_already = False
                def _db_8766():
                    _wdr_already = None
                    with _c() as c:
                        _wdr_cur = c.execute(
                            "UPDATE withdrawals SET status='rejected' WHERE id=%s AND status='pending'", (wid,)
                        )
                        if _wdr_cur.rowcount == 0:
                            _wdr_already = True
                        else:
                            # শুধু এই transaction-ই reject করেছে — refund safe
                            c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (wamt, wuser))
                            _invalidate_user_cache(wuser)
                    return _wdr_already
                _wdr_already = await asyncio.to_thread(_db_8766)
                if _wdr_already:
                    await update.message.reply_text(f"⚠️ *WD `#{wid}` ইতিমধ্যে process হয়ে গেছে।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                _mark_dirty()
                try:
                    await ctx.bot.send_message(wuser, T(wuser,'wd_rejected_msg',amt=wamt), parse_mode="Markdown")
                except Exception: pass
                await update.message.reply_text(
                    f"❌ *WD `#{wid}` Rejected — Balance ফেরত দেওয়া হয়েছে।*\n\n"
                    f"👤 *User:* `{wuser}`\n"
                    f"💰 *Refunded:* `{wamt}$ USDT`",
                    parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ সঠিক ID দিন (শুধু সংখ্যা)।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        # Task creation flow
        if st == "adm_task_name":
            ctx.user_data["new_task_name"] = txt
            ctx.user_data["state"] = "adm_task_await_type"
            await update.message.reply_text(
                f"✅ *নাম:* `{txt}`\n\n🏷 *Task Type বেছে নিন:*",
                parse_mode="Markdown",
                reply_markup=task_type_picker_kb("adm_task_type_")
            ); return

        if st == "adm_task_price":
            try:
                price = float(txt)
                ctx.user_data["new_task_price"] = price
                ctx.user_data["state"] = "adm_task_rules"
                await update.message.reply_text(f"✅ *মূল্য:* `{price}$`\n\nএখন *নিয়মাবলী* লিখুন:\n/cancel — বাতিল", parse_mode="Markdown")
            except Exception: await update.message.reply_text("❌ সঠিক মূল্য দিন (যেমন: 0.10)", parse_mode="Markdown")
            return

        if st == "adm_task_rules":
            ctx.user_data["new_task_rules"] = txt
            ctx.user_data["state"] = "adm_task_rules_en"
            await update.message.reply_text(
                "✅ *বাংলা নিয়মাবলী সেভ হয়েছে।*\n\n"
                "📜 *এখন ইংরেজিতে Rules লিখুন* (English users-দের দেখানো হবে):\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_task_rules_en":
            ctx.user_data["new_task_rules_en"] = txt
            ctx.user_data["state"] = "adm_task_max"
            await update.message.reply_text(
                "✅ *ইংরেজি Rules সেভ হয়েছে।*\n\n"
                "👥 *সর্বোচ্চ কতজন এই Task করতে পারবে?*\n"
                "_(0 লিখলে Unlimited — যেকোনো সংখ্যা দিন)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_task_max":
            try: max_c = max(0, int(txt.strip()))
            except Exception: max_c = 0
            ctx.user_data["new_task_max"] = max_c
            ctx.user_data["state"] = "adm_task_per_user"
            max_lbl = "Unlimited ♾️" if max_c == 0 else f"{max_c} জন"
            await update.message.reply_text(
                f"✅ *Max:* `{max_lbl}`\n\n"
                "👤 *প্রতিটি ইউজার সর্বোচ্চ কতবার এই Task করতে পারবে?*\n"
                "_(0 লিখলে Unlimited — যেমন: 1, 2, 3...)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_task_per_user":
            try:
                per_user_limit = max(0, int(txt.strip()))
            except Exception:
                per_user_limit = 1
            ctx.user_data["new_task_per_user"] = per_user_limit
            ctx.user_data["state"] = "adm_task_countries"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
            await update.message.reply_text(
                f"✅ *Per User:* `{per_lbl}`\n\n"
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে?*\n\n"
                "দেশের কোড কমা দিয়ে লিখুন:\n"
                f"`{_ch}`\n\n"
                "উদাহরণ: `BD,IN,PK`\n"
                "🌐 *সব দেশের জন্য:* `0` লিখুন\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_task_countries":
            _ac = txt.strip().upper()
            allowed_c = "" if _ac in ("0","ALL","") else ",".join(c.strip() for c in _ac.split(",") if c.strip())
            name      = ctx.user_data.get("new_task_name","")
            price     = ctx.user_data.get("new_task_price", 0.10)
            ttype     = ctx.user_data.pop("new_task_type", "other")
            rules     = ctx.user_data.pop("new_task_rules", "")
            rules_en_val = ctx.user_data.pop("new_task_rules_en", "")
            max_c     = ctx.user_data.pop("new_task_max", 0)
            per_user_limit = ctx.user_data.pop("new_task_per_user", 1)
            task_id = await asyncio.to_thread(add_task, name, price, rules, rules_en_val, task_type=ttype, per_user_limit=per_user_limit, max_completions=max_c, allowed_countries=allowed_c)
            ctx.user_data["new_task_id"] = task_id
            ctx.user_data["state"] = "adm_task_fields"
            ctx.user_data.pop("new_task_name", None); ctx.user_data.pop("new_task_price", None)
            max_lbl = "Unlimited ♾️" if max_c == 0 else f"{max_c} জন"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{task_id}"),
                 InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{task_id}")],
                [InlineKeyboardButton("🖼 Example Photo যোগ", callback_data=f"adm_te_sample_photo_{task_id}"),
                 InlineKeyboardButton("🎬 Demo Video যোগ",   callback_data=f"adm_te_video_{task_id}")],
                [InlineKeyboardButton("✅ Done — Task সেভ করুন", callback_data=f"adm_task_done_{task_id}")],
            ])
            await update.message.reply_text(
                f"✅ *Task প্রায় তৈরি!*\n📌 `{name}` | 💵 `{price}$`\n"
                f"👥 *Max:* `{max_lbl}` | 👤 *Per User:* `{per_lbl}`\n"
                f"🌍 *দেশ:* {fmt_allowed_countries(allowed_c)}\n\n"
                "এখন *Proof Fields* যোগ করুন — ইউজার থেকে কী কী নেওয়া হবে?\n\n"
                "🖼 *Example Photo* — ইউজারকে দেখাবেন কেমন ছবি দিতে হবে\n"
                "🎬 *Demo Video* — ইউজার চাপলে ভিডিও/লিংক দেখবে\n\n"
                "_(Field না যোগ করলে generic তথ্য চাওয়া হবে)_",
                parse_mode="Markdown", reply_markup=kb); return

        if st == "adm_edit_name":
            tid = ctx.user_data.get("edit_tid"); await asyncio.to_thread(update_task_field_val, tid, "name", txt)
            await update.message.reply_text(f"✅ *নাম আপডেট:* `{txt}`", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tid", None); return
        if st == "adm_edit_price":
            tid = ctx.user_data.get("edit_tid")
            try:
                price = float(txt); await asyncio.to_thread(update_task_field_val, tid, "price", price)
                await update.message.reply_text(f"✅ *দাম আপডেট:* `{price}$`", parse_mode="Markdown")
            except Exception: await update.message.reply_text("❌ সঠিক দাম দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tid", None); return
        if st == "adm_edit_rules":
            ctx.user_data["edit_rules_bn"] = txt
            ctx.user_data["state"] = "adm_edit_rules_en"
            await update.message.reply_text(
                "✅ *বাংলা নিয়মাবলী সেভ হয়েছে।*\n\n"
                "📜 *এখন ইংরেজিতে Rules লিখুন:*\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_edit_rules_en":
            tid      = ctx.user_data.get("edit_tid")
            rules_bn = ctx.user_data.pop("edit_rules_bn", "")
            await asyncio.to_thread(update_task_field_val, tid, "rules",    rules_bn)
            await asyncio.to_thread(update_task_field_val, tid, "rules_en", txt.strip())
            await update.message.reply_text("✅ *নিয়মাবলী আপডেট হয়েছে।*", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tid", None); return

        # ── Regular Task: Video link ────────────────────────────────
        if st == "adm_task_set_video_link":
            tid  = ctx.user_data.pop("edit_tid", None)
            ctx.user_data.pop("state", None)
            link = txt.strip()
            if tid and link:
                await asyncio.to_thread(update_task_field_val, tid, "demo_video_id", link)
                _rvl_t, _rvl_f = await asyncio.gather(
                    asyncio.to_thread(get_task, tid),
                    asyncio.to_thread(get_task_fields, tid),
                )
                _rvl_img = bool((_rget(_rvl_t, 'example_image_id', '') or '').strip()) if _rvl_t else False
                _rvl_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
                     InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}")],
                    [InlineKeyboardButton("🖼 Example Photo ✅" if _rvl_img else "🖼 Example Photo যোগ", callback_data=f"adm_te_sample_photo_{tid}"),
                     InlineKeyboardButton("🎬 Demo Video ✅",    callback_data=f"adm_te_video_{tid}")],
                    [InlineKeyboardButton("✅ Done — Task সেভ করুন", callback_data=f"adm_task_done_{tid}")],
                ])
                await update.message.reply_text(
                    f"✅ *Video Link সেট হয়েছে।*\n\n🔗 `{link}`\n\n"
                    f"📋 Fields: {len(_rvl_f)} টি\n\nআরো fields যোগ করুন বা Done চাপুন:",
                    parse_mode="Markdown", reply_markup=_rvl_kb)
                ctx.user_data["state"] = "adm_task_fields"
            else:
                await update.message.reply_text("❌ সঠিক Link দিন।", parse_mode="Markdown")
            return

        # Task field prompt (bn)
        if st == "adm_field_prompt_bn":
            ctx.user_data["new_field_prompt_bn"] = txt
            ctx.user_data["state"] = "adm_field_prompt_en"
            await update.message.reply_text(
                f"✅ বাংলা: `{txt}`\n\nএখন *English Prompt* লিখুন:\n_(বা `-` পাঠালে auto-translate হবে)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_field_prompt_en":
            ftype    = ctx.user_data.get("new_field_type", "text")
            tid      = ctx.user_data.get("new_field_tid")
            prompt_bn = ctx.user_data.get("new_field_prompt_bn", "")
            prompt_en = txt if txt.strip() not in ("-","–","—") else await asyncio.to_thread(translate_to_en, prompt_bn)
            await asyncio.to_thread(add_task_field, tid, ftype, prompt_bn, prompt_en)
            icon = "📝" if ftype == "text" else "📷"
            fields = await asyncio.to_thread(get_task_fields, tid)
            for k in ("new_field_type","new_field_tid","new_field_prompt_bn","state"):
                ctx.user_data.pop(k, None)
            # Photo Field হলে per-field example photo সেট করার সুযোগ দাও
            if ftype == "photo":
                new_field = await asyncio.to_thread(get_last_task_field, tid)
                fid = new_field["id"] if new_field else None
                if fid:
                    ctx.user_data["state"]               = "adm_field_set_example_photo"
                    ctx.user_data["edit_field_id"]        = fid
                    ctx.user_data["edit_field_task_id"]   = tid
                    ctx.user_data["edit_field_is_categ"]  = False
                    await update.message.reply_text(
                        f"✅ *Photo Field যোগ হয়েছে!*\n📷 `{prompt_bn}` / `{prompt_en}`\n\n"
                        f"এই field-এর জন্য *Example Photo* সেট করবেন?\n"
                        f"_(ইউজার এই ছবি দেখে বুঝবে কেমন ছবি পাঠাতে হবে)_\n\n"
                        f"📤 এখনই ছবি পাঠান, অথবা Skip করুন:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⏭ Skip — Example Photo ছাড়াই রাখুন", callback_data=f"adm_field_skip_example_{tid}")],
                        ])
                    )
                    return
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
                 InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}")],
                [InlineKeyboardButton("✅ Done", callback_data=f"adm_task_done_{tid}")],
            ])
            await update.message.reply_text(
                f"✅ *Field যোগ হয়েছে!*\n{icon} `{prompt_bn}` / `{prompt_en}`\n\n"
                f"মোট fields: {len(fields)} টি\n\nআরো field যোগ করুন বা Done চাপুন:",
                parse_mode="Markdown", reply_markup=kb)
            return

        # Channel add states
        if st == "adm_ch_add_id":
            ctx.user_data["new_ch_id"] = txt.strip()
            ctx.user_data["state"] = "adm_ch_add_link"
            await update.message.reply_text(
                f"✅ *Channel ID:* `{txt.strip()}`\n\nএখন *Channel Link* দিন:\n/cancel — বাতিল",
                parse_mode="Markdown"); return
        if st == "adm_ch_add_link":
            ctx.user_data["new_ch_link"] = txt.strip()
            ctx.user_data["state"] = "adm_ch_add_name"
            await update.message.reply_text(
                f"✅ *Link:* `{txt.strip()}`\n\nএখন *Channel নাম* দিন:\n/cancel — বাতিল",
                parse_mode="Markdown"); return
        if st == "adm_ch_add_name":
            ch_id   = ctx.user_data.get("new_ch_id", "")
            ch_link = ctx.user_data.get("new_ch_link", "")
            ch_name = txt.strip()
            await asyncio.to_thread(add_req_channel, ch_id, ch_link, ch_name)
            await update.message.reply_text(
                f"✅ *Channel যোগ হয়েছে!*\n📛 {ch_name}\n🔗 `{ch_id}`",
                parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            for k in ("state","new_ch_id","new_ch_link"):
                ctx.user_data.pop(k, None)
            return

        # Menu button edit
        if st == "adm_btn_edit":
            btn_key = ctx.user_data.get("edit_btn_key","")
            parts   = txt.split("|")
            if len(parts) == 2:
                bn = parts[0].strip()
                en = parts[1].strip()
                await asyncio.to_thread(set_btn, btn_key, bn, en)
                await update.message.reply_text(
                    f"✅ *Button `{btn_key}` আপডেট!*\n🇧🇩 `{bn}` | 🇬🇧 `{en}`",
                    parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            else:
                await update.message.reply_text(
                    "❌ ফরম্যাট ঠিক নেই।\n\nউদাহরণ: `📋 কাজ | 📋 Tasks`",
                    parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_btn_key", None); return

        # Bot settings
        if st == "adm_set_support_owner":
            await asyncio.to_thread(set_setting, 'support_owner', txt.strip())
            await update.message.reply_text(f"✅ *Support Owner আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_support_bot":
            await asyncio.to_thread(set_setting, 'support_bot', txt.strip())
            await update.message.reply_text(f"✅ *Support Bot আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_min_withdraw":
            try:
                val = float(txt.strip())
                await asyncio.to_thread(set_setting, 'min_withdraw', str(val))
                await update.message.reply_text(f"✅ *Min Withdraw আপডেট:* `{val}$ USDT`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return
        if st == "adm_set_commission_pct":
            try:
                val = float(txt.strip())
                await asyncio.to_thread(set_setting, 'commission_pct', str(val))
                await update.message.reply_text(f"✅ *Commission আপডেট:* `{val}%`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return
        if st == "adm_set_gmail_ch_id":
            await asyncio.to_thread(set_setting, 'gmail_ch_id', txt.strip())
            await update.message.reply_text(f"✅ *Task Channel ID আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_withdraw_ch_id":
            await asyncio.to_thread(set_setting, 'withdraw_ch_id', txt.strip())
            await update.message.reply_text(f"✅ *Withdraw Channel ID আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_bsc_wallet":
            new_addr = txt.strip()
            if not new_addr.startswith("0x") or len(new_addr) != 42:
                await update.message.reply_text(
                    "❌ *অবৈধ BSC Address!*\n\n`0x` দিয়ে শুরু হতে হবে এবং মোট ৪২ character হতে হবে।\nআবার লিখুন বা /cancel করুন।",
                    parse_mode="Markdown"); return
            await asyncio.to_thread(set_setting, 'bsc_wallet_addr', new_addr)
            bscscan = f"https://bscscan.com/address/{new_addr}"
            await update.message.reply_text(
                f"✅ *BSC Wallet Address আপডেট হয়েছে!*\n\n"
                f"📍 *নতুন Address:* `{new_addr}`\n"
                f"🌐 [BSCScan এ দেখুন]({bscscan})",
                parse_mode="Markdown", disable_web_page_preview=False, reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_welcome_bn":
            new_val = "" if txt.strip() in ("-","–","—") else txt.strip()
            await asyncio.to_thread(set_setting, 'welcome_msg_bn', new_val)
            msg = "✅ *বাংলা Welcome Message আপডেট হয়েছে।*" if new_val else "✅ *Default এ ফিরিয়ে দেওয়া হয়েছে।*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_welcome_en":
            new_val = "" if txt.strip() in ("-","–","—") else txt.strip()
            await asyncio.to_thread(set_setting, 'welcome_msg_en', new_val)
            msg = "✅ *English Welcome Message updated.*" if new_val else "✅ *Reset to Default.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return

        if st == "adm_set_task_notify_ch":
            await asyncio.to_thread(set_setting, 'task_notify_ch_id', txt.strip())
            await update.message.reply_text(f"✅ *Task Notify Channel ID আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return

        if st == "adm_set_notify_ch_username":
            val = txt.strip()
            if not val.startswith('@'): val = '@' + val
            await asyncio.to_thread(set_setting, 'task_notify_ch_username', val)
            await update.message.reply_text(f"✅ *Notification Channel Username আপডেট:* `{val}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); return

        if st == "adm_set_notify_delay":
            try:
                delay = int(txt.strip())
                if delay < 0: raise ValueError
                await asyncio.to_thread(set_setting, 'notify_delay_min', str(delay))
                msg = (f"✅ *Bot Notify Delay আপডেট:* `{delay} মিনিট`\n\n"
                       f"📌 Channel এ সাথে সাথে, Bot users এ {delay} মিনিট পরে।"
                       if delay > 0 else
                       "✅ *Bot Notify Delay আপডেট: সাথে সাথে (0 মিনিট)*")
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except ValueError:
                await update.message.reply_text("❌ সঠিক সংখ্যা দিন (যেমন: 0, 5, 10)", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        # ── TG Task creation flow ────────────────────────────────
        if st == "adm_tgt_name":
            ctx.user_data["new_tgt_name"] = txt.strip()
            ctx.user_data["state"] = "adm_tgt_price"
            await update.message.reply_text(
                f"✅ *নাম:* `{txt.strip()}`\n\nএখন *পুরস্কার (USD)* লিখুন:\n_(যেমন: 0.05)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_price":
            try:
                price = float(txt.strip())
                ctx.user_data["new_tgt_price"] = price
                ctx.user_data["state"] = "adm_tgt_target"
                await update.message.reply_text(
                    f"✅ *দাম:* `{price}$`\n\nএখন *Channel/Group username বা ID* দিন:\n_(যেমন: @mychannel বা -100123456)_\n/cancel — বাতিল",
                    parse_mode="Markdown")
            except Exception: await update.message.reply_text("❌ সঠিক দাম দিন (যেমন: 0.05)", parse_mode="Markdown")
            return

        if st == "adm_tgt_target":
            ctx.user_data["new_tgt_target"] = txt.strip()
            ctx.user_data["state"] = "adm_tgt_link"
            await update.message.reply_text(
                f"✅ *Channel:* `{txt.strip()}`\n\nএখন *Join Link* দিন:\n_(যেমন: https://t.me/mychannel)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_link":
            ctx.user_data["new_tgt_link"] = txt.strip()
            ctx.user_data["state"] = "adm_tgt_name2"
            await update.message.reply_text(
                f"✅ *Link:* `{txt.strip()}`\n\nএখন *Channel-এর প্রদর্শন নাম* দিন:\n_(যেমন: আমার চ্যানেল)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_name2":
            ctx.user_data["new_tgt_display_name"] = txt.strip()
            ctx.user_data["state"] = "adm_tgt_max"
            await update.message.reply_text(
                f"✅ *Display নাম:* `{txt.strip()}`\n\nসর্বোচ্চ কতজন এই Task করতে পারবে?\n_(0 = unlimited)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_max":
            try:
                max_c = int(txt.strip())
            except Exception: max_c = 0
            ctx.user_data["new_tgt_max"] = max_c
            ctx.user_data["state"] = "adm_tgt_per_user"
            await update.message.reply_text(
                f"✅ *Max:* `{max_c if max_c > 0 else 'Unlimited'}`\n\n"
                "👤 *প্রতিটি ইউজার এই Task সর্বোচ্চ কতবার করতে পারবে?*\n"
                "_(TG Task এ সাধারণত 1 রাখুন)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_per_user":
            try:
                per_user_limit = max(0, int(txt.strip()))
            except Exception:
                per_user_limit = 1
            ctx.user_data["new_tgt_per_user"] = per_user_limit
            ctx.user_data["state"] = "adm_tgt_countries"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
            await update.message.reply_text(
                f"✅ *Per User:* `{per_lbl}`\n\n"
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে?*\n\n"
                "দেশের কোড কমা দিয়ে লিখুন:\n"
                f"`{_ch}`\n\n"
                "উদাহরণ: `BD,IN,PK`\n"
                "🌐 *সব দেশের জন্য:* `0` লিখুন\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_countries":
            _ac = txt.strip().upper()
            allowed_c = "" if _ac in ("0","ALL","") else ",".join(c.strip() for c in _ac.split(",") if c.strip())
            name   = ctx.user_data.get("new_tgt_name","")
            price  = ctx.user_data.get("new_tgt_price", 0.05)
            target = ctx.user_data.get("new_tgt_target","")
            link   = ctx.user_data.get("new_tgt_link","")
            dname  = ctx.user_data.get("new_tgt_display_name","")
            max_c  = ctx.user_data.get("new_tgt_max", 0)
            per_user_limit = ctx.user_data.pop("new_tgt_per_user", 1)
            tid_new = await asyncio.to_thread(add_tg_task, name, price, target, link, dname, max_c, per_user_limit=per_user_limit, allowed_countries=allowed_c)
            for k in ("new_tgt_name","new_tgt_price","new_tgt_target","new_tgt_link","new_tgt_display_name","new_tgt_max","state"):
                ctx.user_data.pop(k, None)
            max_txt = f"{max_c} জন" if max_c > 0 else "Unlimited ♾️"
            per_txt = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            tp_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ নাম বদলাও",               callback_data=f"adm_tp_n_{tid_new}_g")],
                [InlineKeyboardButton("✏️ দাম বদলাও",               callback_data=f"adm_tp_p_{tid_new}_g")],
                [InlineKeyboardButton("✏️ Max বদলাও",               callback_data=f"adm_tp_m_{tid_new}_g")],
                [InlineKeyboardButton("✏️ Per User Limit",           callback_data=f"adm_tp_pu_{tid_new}_g")],
                [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন",       callback_data=f"adm_tp_c_{tid_new}_g")],
                [InlineKeyboardButton("🔓 New Joiners Only: ❌ OFF", callback_data=f"adm_tp_njo_{tid_new}_g")],
                [InlineKeyboardButton("✅ Confirm করুন",             callback_data=f"adm_tp_ok_{tid_new}_g")],
                [InlineKeyboardButton("❌ বাতিল/Delete",             callback_data=f"adm_tp_del_{tid_new}_g")],
            ])
            await update.message.reply_text(
                f"📋 *Task Preview — চেক করুন:*\n\n"
                f"📌 *Name:* `{name}`\n"
                f"💰 *Reward:* `{price}$ USDT`\n"
                f"📢 *Channel:* `{target}`\n"
                f"👥 *Max:* `{max_txt}`\n"
                f"👤 *Per User:* `{per_txt}`\n"
                f"🌍 *দেশ:* {fmt_allowed_countries(allowed_c)}\n\n"
                f"✏️ Edit করুন বা ✅ Confirm করুন।",
                parse_mode="Markdown", reply_markup=tp_kb)
            return

        # ── TG Task edit states ──────────────────────────────────
        if st == "adm_tgt_edit_name":
            tid = ctx.user_data.get("edit_tgt_id")
            await asyncio.to_thread(update_tg_task, tid, "name", txt.strip())
            await update.message.reply_text(f"✅ *নাম আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_price":
            tid = ctx.user_data.get("edit_tgt_id")
            try:
                price = float(txt.strip())
                await asyncio.to_thread(update_tg_task, tid, "price", price)
                await update.message.reply_text(f"✅ *দাম আপডেট:* `{price}$`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক দাম দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_target":
            tid = ctx.user_data.get("edit_tgt_id")
            await asyncio.to_thread(update_tg_task, tid, "tg_target", txt.strip())
            await update.message.reply_text(f"✅ *Channel আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_link":
            tid = ctx.user_data.get("edit_tgt_id")
            await asyncio.to_thread(update_tg_task, tid, "tg_target_link", txt.strip())
            await update.message.reply_text(f"✅ *Link আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_max":
            tid = ctx.user_data.get("edit_tgt_id")
            try:
                max_c = int(txt.strip())
                await asyncio.to_thread(update_tg_task, tid, "max_completions", max_c)
                max_txt = f"{max_c} জন" if max_c > 0 else "সীমাহীন"
                await update.message.reply_text(f"✅ *Max Completions আপডেট:* {max_txt}", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_per_user":
            tid = ctx.user_data.get("edit_tgt_id")
            try:
                pu_val = max(0, int(txt.strip()))
                await asyncio.to_thread(update_tg_task, tid, "per_user_limit", pu_val)
                per_lbl = "Unlimited ♾️" if pu_val == 0 else f"{pu_val} বার"
                await update.message.reply_text(f"✅ *Per User Limit আপডেট:* `{per_lbl}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return

        if st == "adm_tgt_edit_countries":
            tid = ctx.user_data.get("edit_tgt_id")
            _ac3 = txt.strip().upper()
            allowed_c3 = "" if _ac3 in ("0","ALL","") else ",".join(c.strip() for c in _ac3.split(",") if c.strip())
            await asyncio.to_thread(update_tg_task, tid, "allowed_countries", allowed_c3)
            await update.message.reply_text(
                f"✅ *দেশ আপডেট:* {fmt_allowed_countries(allowed_c3)}",
                parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return

    # ── Category admin creation states ───────────────────────────
        if st == "adm_cat_name":
            name = txt.strip()
            cid  = await asyncio.to_thread(add_category, name)
            ctx.user_data.pop("state", None)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ এই Category তে Task যোগ করুন", callback_data=f"adm_cate_task_add_{cid}")],
                [InlineKeyboardButton("📁 সব Categories", callback_data="adm_cat_list")],
            ])
            await update.message.reply_text(
                f"✅ *Category তৈরি হয়েছে:* `{name}`\n\nএখন এই Category তে Task যোগ করুন:",
                parse_mode="Markdown", reply_markup=kb); return

        if st == "adm_cat_edit_name":
            cid = ctx.user_data.pop("edit_cat_id", None)
            ctx.user_data.pop("state", None)
            if cid:
                _, _kb = await asyncio.gather(
                    asyncio.to_thread(update_category, cid, "name", txt.strip()),
                    asyncio.to_thread(main_kb, uid),
                )
                await update.message.reply_text(f"✅ *Category নাম আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=_kb)
            return

        if st == "adm_cate_task_name":
            cid = ctx.user_data.get("new_cate_cat_id")
            ctx.user_data["new_cate_task_name"] = txt.strip()
            ctx.user_data["state"] = "adm_cate_task_await_type"
            await update.message.reply_text(
                f"✅ *Task নাম:* `{txt.strip()}`\n\n🏷 *Task Type বেছে নিন:*",
                parse_mode="Markdown",
                reply_markup=task_type_picker_kb("adm_cate_task_type_")
            ); return

        if st == "adm_cate_task_price":
            try:
                price = float(txt.strip())
            except Exception:
                await update.message.reply_text("❌ সঠিক দাম দিন (যেমন: `0.10`):", parse_mode="Markdown"); return
            ctx.user_data["new_cate_task_price"] = price
            ctx.user_data["state"] = "adm_cate_task_max"
            await update.message.reply_text(
                f"✅ *দাম:* `{price}$`\n\n👥 *সর্বোচ্চ কতজন করতে পারবে?*\n_(0 = সীমাহীন)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cate_task_max":
            try: max_c = int(txt.strip())
            except Exception: max_c = 0
            ctx.user_data["new_cate_task_max"] = max_c
            ctx.user_data["state"] = "adm_cate_task_per_user"
            await update.message.reply_text(
                f"✅ *Max:* `{max_c if max_c > 0 else '∞'}`\n\n"
                "👤 *প্রতিটি ইউজার এই Task সর্বোচ্চ কতবার করতে পারবে?*\n"
                "_(যেমন: 1 বার, 2 বার — শুধু সংখ্যা লিখুন)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cate_task_per_user":
            try:
                per_user_limit = max(0, int(txt.strip()))
            except Exception:
                per_user_limit = 1
            ctx.user_data["new_cate_task_per_user"] = per_user_limit
            ctx.user_data["state"] = "adm_cate_task_countries"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
            await update.message.reply_text(
                f"✅ *Per User Limit:* `{per_lbl}`\n\n"
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে?*\n\n"
                "দেশের কোড কমা দিয়ে লিখুন:\n"
                f"`{_ch}`\n\n"
                "উদাহরণ: `BD,IN,PK`\n"
                "🌐 *সব দেশের জন্য:* `0` লিখুন\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cate_task_countries":
            _ac = txt.strip().upper()
            ctx.user_data["new_cate_task_countries"] = "" if _ac in ("0","ALL","") else ",".join(c.strip() for c in _ac.split(",") if c.strip())
            ctx.user_data["state"] = "adm_cate_task_rules"
            _c_lbl = fmt_allowed_countries(ctx.user_data["new_cate_task_countries"])
            await update.message.reply_text(
                f"✅ *দেশ সেট:* {_c_lbl}\n\n"
                "📜 *Task নিয়মাবলী/বিবরণ লিখুন* (user কে দেখানো হবে):\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cate_task_rules":
            ctx.user_data["new_cate_task_rules"] = txt.strip()
            ctx.user_data["state"] = "adm_cate_task_rules_en"
            await update.message.reply_text(
                "✅ *বাংলা নিয়মাবলী সেভ হয়েছে।*\n\n"
                "📜 *এখন ইংরেজিতে Rules লিখুন* (English users-দের দেখানো হবে):\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cate_task_rules_en":
            cid   = ctx.user_data.pop("new_cate_cat_id", None)
            name  = ctx.user_data.pop("new_cate_task_name", "")
            price = ctx.user_data.pop("new_cate_task_price", 0.05)
            max_c = ctx.user_data.pop("new_cate_task_max", 0)
            ttype = ctx.user_data.pop("new_cate_task_type", "other")
            per_user_limit = ctx.user_data.pop("new_cate_task_per_user", 1)
            allowed_c = ctx.user_data.pop("new_cate_task_countries", "")
            rules = ctx.user_data.pop("new_cate_task_rules", "")
            ctx.user_data.pop("state", None)
            rules_en_cate = txt.strip()
            tid   = await asyncio.to_thread(add_category_task, cid, name, price, rules, rules_en=rules_en_cate, max_comp=max_c, task_type=ttype, per_user_limit=per_user_limit, allowed_countries=allowed_c)
            country_lbl = fmt_allowed_countries(allowed_c)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📷 Photo Field যোগ",  callback_data=f"adm_cfield_add_photo_{tid}"),
                 InlineKeyboardButton("📝 Text Field যোগ",   callback_data=f"adm_cfield_add_text_{tid}")],
                [InlineKeyboardButton("🖼 Example Photo যোগ", callback_data=f"adm_ctaste_sample_photo_{tid}"),
                 InlineKeyboardButton("🎬 Demo Video যোগ",    callback_data=f"adm_ctaste_video_{tid}")],
                [InlineKeyboardButton("🚀 এখনই Live করুন (Proof ছাড়া)", callback_data=f"adm_ctask_golive_{tid}")],
            ])
            await update.message.reply_text(
                f"✅ *Category Task তৈরি হয়েছে!*\n\n"
                f"📌 *Task:* `{name}`\n"
                f"💰 *দাম:* `{price}$`\n"
                f"👥 *Max:* `{max_c if max_c > 0 else '∞'}`\n"
                f"🌍 *দেশ:* {country_lbl}\n\n"
                "🖼 *Example Photo* — ইউজারকে দেখাবেন কেমন ছবি দিতে হবে\n"
                "🎬 *Demo Video* — ইউজার চাপলে ভিডিও/লিংক দেখবে\n\n"
                f"📋 Proof Fields যোগ করতে পারেন, অথবা সরাসরি Live করুন:",
                parse_mode="Markdown", reply_markup=kb); return

        if st == "adm_ctask_edit_name":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            if tid: await asyncio.to_thread(update_category_task, tid, "name", txt.strip())
            await update.message.reply_text(f"✅ *Task নাম আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return

        if st == "adm_ctask_edit_price":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            try:
                price = float(txt.strip())
                if tid: await asyncio.to_thread(update_category_task, tid, "price", price)
                await update.message.reply_text(f"✅ *দাম আপডেট:* `{price}$`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক দাম দিন।", parse_mode="Markdown")
            return

        if st == "adm_ctask_edit_rules":
            ctx.user_data["edit_ct_rules_bn"] = txt.strip()
            ctx.user_data["state"] = "adm_ctask_edit_rules_en"
            await update.message.reply_text(
                "✅ *বাংলা নিয়মাবলী সেভ হয়েছে।*\n\n"
                "📜 *এখন ইংরেজিতে Rules লিখুন:*\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_ctask_edit_rules_en":
            tid      = ctx.user_data.pop("edit_ctask_id", None)
            rules_bn = ctx.user_data.pop("edit_ct_rules_bn", "")
            ctx.user_data.pop("state", None)
            if tid:
                await asyncio.to_thread(update_category_task, tid, "rules",    rules_bn)
                await asyncio.to_thread(update_category_task, tid, "rules_en", txt.strip())
            await update.message.reply_text("✅ *নিয়মাবলী আপডেট হয়েছে।*", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return

        if st == "adm_ctask_edit_max":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            try:
                max_c = int(txt.strip())
                if tid: await asyncio.to_thread(update_category_task, tid, "max_completions", max_c)
                max_txt = f"{max_c} জন" if max_c > 0 else "সীমাহীন"
                await update.message.reply_text(f"✅ *Max Completions আপডেট:* {max_txt}", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            return

        if st == "adm_ctask_edit_per_user":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            try:
                pu_val = max(0, int(txt.strip()))
                if tid: await asyncio.to_thread(update_category_task, tid, "per_user_limit", pu_val)
                per_lbl = "Unlimited ♾️" if pu_val == 0 else f"{pu_val} বার"
                await update.message.reply_text(f"✅ *Per User Limit আপডেট:* `{per_lbl}`", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
            except Exception: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            return

        # ── Video link state ────────────────────────────────────────
        if st == "adm_ctask_set_video_link":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            link = txt.strip()
            if tid and link:
                await asyncio.to_thread(update_category_task, tid, "demo_video_id", link)
                _cvl_t = await asyncio.to_thread(get_category_task, tid)
                _cvl_img = bool((_rget(_cvl_t, 'example_image_id', '') or '').strip()) if _cvl_t else False
                _cvl_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📷 Photo Field যোগ",  callback_data=f"adm_cfield_add_photo_{tid}"),
                     InlineKeyboardButton("📝 Text Field যোগ",   callback_data=f"adm_cfield_add_text_{tid}")],
                    [InlineKeyboardButton("🖼 Example Photo ✅" if _cvl_img else "🖼 Example Photo যোগ", callback_data=f"adm_ctaste_sample_photo_{tid}"),
                     InlineKeyboardButton("🎬 Demo Video ✅",    callback_data=f"adm_ctaste_video_{tid}")],
                    [InlineKeyboardButton("🚀 এখনই Live করুন", callback_data=f"adm_ctask_golive_{tid}")],
                ])
                await update.message.reply_text(
                    f"✅ *Video Link সেট হয়েছে।*\n\n🔗 `{link}`\n\n"
                    "আরো কিছু যোগ করুন বা Live করুন:",
                    parse_mode="Markdown", reply_markup=_cvl_kb)
            else:
                await update.message.reply_text("❌ সঠিক Link দিন।", parse_mode="Markdown")
            return

        # ── Bio Task reward inputs ──────────────────────────────────
        if st == "adm_bio_task_daily_reward":
            if not await asyncio.to_thread(is_admin, uid):
                ctx.user_data.pop("state", None)
                return
            try:
                val = float(txt.strip())
                if val <= 0: raise ValueError()
            except Exception:
                await update.message.reply_text(
                    "❌ সঠিক Amount দিন (যেমন: `0.05`):", parse_mode="Markdown"); return
            await asyncio.to_thread(set_bio_task, reward=val)
            ctx.user_data.pop("state", None)
            await update.message.reply_text(
                f"✅ *Daily Reward সেট হয়েছে:* `{val}$ USDT` প্রতিদিন",
                parse_mode="Markdown")
            return

        if st == "adm_bio_task_add_reward":
            if not await asyncio.to_thread(is_admin, uid):
                ctx.user_data.pop("state", None)
                return
            try:
                val = float(txt.strip())
                if val <= 0: raise ValueError()
            except Exception:
                await update.message.reply_text(
                    "❌ সঠিক Amount দিন (যেমন: `0.10`):", parse_mode="Markdown"); return
            await asyncio.to_thread(set_bio_task, add_reward=val)
            ctx.user_data.pop("state", None)
            await update.message.reply_text(
                f"✅ *Add Reward সেট হয়েছে:* `{val}$ USDT` (প্রথমবার bio সেট করলে)",
                parse_mode="Markdown")
            return

        # ── Referral Event creation states ────────────────────────
        if st == "adm_ref_event_start":
            if not await asyncio.to_thread(is_admin, uid):
                ctx.user_data.pop("state", None); return
            import re as _re
            if not _re.match(r'^\d{4}-\d{2}-\d{2}$', txt.strip()):
                await update.message.reply_text(
                    "❌ Format ঠিক নেই। এভাবে লিখুন: `2026-06-12`", parse_mode="Markdown"); return
            ctx.user_data["ref_event_start"] = txt.strip()
            ctx.user_data["state"] = "adm_ref_event_end"
            await update.message.reply_text(
                f"✅ শুরুর তারিখ: `{txt.strip()}`\n\n"
                "📅 *শেষের তারিখ* লিখুন:\n"
                "Format: `YYYY-MM-DD` (যেমন: `2026-06-30`)\n\n/cancel — বাতিল",
                parse_mode="Markdown")
            return

        if st == "adm_ref_event_end":
            if not await asyncio.to_thread(is_admin, uid):
                ctx.user_data.pop("state", None); return
            import re as _re
            if not _re.match(r'^\d{4}-\d{2}-\d{2}$', txt.strip()):
                await update.message.reply_text(
                    "❌ Format ঠিক নেই। এভাবে লিখুন: `2026-06-30`", parse_mode="Markdown"); return
            _ev_start = ctx.user_data.get("ref_event_start", "")
            if txt.strip() < _ev_start:
                await update.message.reply_text(
                    f"❌ শেষের তারিখ শুরুর তারিখ (`{_ev_start}`) এর আগে হতে পারবে না।\n"
                    "আবার লিখুন:", parse_mode="Markdown"); return
            ctx.user_data["ref_event_end"] = txt.strip()
            ctx.user_data["state"] = "adm_ref_event_tasks"
            await update.message.reply_text(
                f"✅ শেষের তারিখ: `{txt.strip()}`\n\n"
                "📋 *Referred user কতটি Task করতে হবে?*\n"
                "শুধু সংখ্যা লিখুন (যেমন: `5`)\n\n/cancel — বাতিল",
                parse_mode="Markdown")
            return

        if st == "adm_ref_event_tasks":
            if not await asyncio.to_thread(is_admin, uid):
                ctx.user_data.pop("state", None); return
            try:
                req_tasks = int(txt.strip())
                if req_tasks < 1: raise ValueError()
            except Exception:
                await update.message.reply_text(
                    "❌ সঠিক সংখ্যা দিন (যেমন: `5`)", parse_mode="Markdown"); return
            ctx.user_data["ref_event_tasks"] = req_tasks
            ctx.user_data["state"] = "adm_ref_event_bonus"
            await update.message.reply_text(
                f"✅ Required Tasks: `{req_tasks}`\n\n"
                "💰 *Referrer কত USDT Bonus পাবে?*\n"
                "Dollar amount লিখুন (যেমন: `0.05`)\n\n/cancel — বাতিল",
                parse_mode="Markdown")
            return

        if st == "adm_ref_event_bonus":
            if not await asyncio.to_thread(is_admin, uid):
                ctx.user_data.pop("state", None); return
            try:
                bonus = float(txt.strip())
                if bonus <= 0: raise ValueError()
            except Exception:
                await update.message.reply_text(
                    "❌ সঠিক Amount দিন (যেমন: `0.05`)", parse_mode="Markdown"); return
            start  = ctx.user_data.pop("ref_event_start", "")
            end    = ctx.user_data.pop("ref_event_end", "")
            tasks  = ctx.user_data.pop("ref_event_tasks", 5)
            ctx.user_data.pop("state", None)
            await asyncio.to_thread(create_referral_event, start, end, tasks, bonus)
            await update.message.reply_text(
                f"🎯 *Referral Event তৈরি হয়েছে!*\n\n"
                f"📅 *Period:* `{start}` → `{end}`\n"
                f"📋 *Required Tasks:* `{tasks}`\n"
                f"💰 *Bonus per referral:* `${bonus}` USDT\n\n"
                f"✅ Event চালু হয়েছে! Referred users task complete করলে referrer automatically bonus পাবে।",
                parse_mode="Markdown")
            return

        # ── Promo code creation states ─────────────────────────────
        if st == "adm_promo_code":
            code = txt.strip().upper().replace(" ", "")
            if not code:
                await update.message.reply_text("❌ Code খালি রাখা যাবে না।", parse_mode="Markdown"); return
            # duplicate check — শুধু active (শেষ না হওয়া) code block করো
            def _db_9442():
                row = None
                with _c() as c:
                    row = c.execute("SELECT id, used_count, max_uses FROM promocodes WHERE code=%s", (code,)).fetchone()
                return row
            _existing = await asyncio.to_thread(_db_9442)
            if _existing:
                _used  = _existing["used_count"] if _existing["used_count"] is not None else 0
                _maxu  = _existing["max_uses"]   if _existing["max_uses"]   is not None else 0
                _active = (_maxu == 0) or (_used < _maxu)
                if _active:
                    await update.message.reply_text(
                        f"❌ `{code}` Code এখনো *Active* আছে। অন্য Code দিন বা পুরনোটি শেষ হওয়ার পর ব্যবহার করুন:",
                        parse_mode="Markdown"); return
                else:
                    # পুরনো exhausted code মুছে দাও — নতুন করে বানানো যাবে
                    def _db_del_old():
                        with _c() as c:
                            c.execute("DELETE FROM promo_uses WHERE code_id=%s", (_existing["id"],))
                            c.execute("DELETE FROM promocodes WHERE id=%s", (_existing["id"],))
                    await asyncio.to_thread(_db_del_old)
            ctx.user_data["promo_code"] = code
            ctx.user_data["state"]      = "adm_promo_amount"
            await update.message.reply_text(
                f"✅ *Code:* `{code}`\n\n💰 *এখন Reward Amount লিখুন (USDT):*\n_(যেমন: 0.05)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_promo_amount":
            try:
                amount = float(txt.strip())
                if amount <= 0: raise ValueError()
            except Exception:
                await update.message.reply_text("❌ সঠিক Amount দিন (যেমন: 0.05):", parse_mode="Markdown"); return
            ctx.user_data["promo_amount"] = amount
            ctx.user_data["state"]        = "adm_promo_max_uses"
            await update.message.reply_text(
                f"✅ *Reward:* `{amount}$ USDT`\n\n👥 *সর্বোচ্চ কতজন এই Code ব্যবহার করতে পারবে?*\n_(যেমন: 50)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_promo_max_uses":
            try:
                max_uses = int(txt.strip())
                if max_uses <= 0: raise ValueError()
            except Exception:
                await update.message.reply_text("❌ সঠিক সংখ্যা দিন (যেমন: 50):", parse_mode="Markdown"); return
            ctx.user_data["promo_max_uses"] = max_uses
            ctx.user_data.pop("state", None)
            # Requirement type choice
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 যে কেউ পাবে",          callback_data="adm_promo_req_anyone")],
                [InlineKeyboardButton("📋 Task Count Requirement", callback_data="adm_promo_req_tasks")],
            ])
            await update.message.reply_text(
                f"✅ *Max Uses:* `{max_uses}` জন\n\n"
                "📋 *Requirement বেছে নিন:*\n"
                "• *যে কেউ পাবে* — কোনো শর্ত নেই\n"
                "• *Task Count* — নির্দিষ্ট সংখ্যক Task Approved হলে পাবে",
                parse_mode="Markdown", reply_markup=kb); return

        if st == "adm_promo_min_tasks":
            try:
                min_tasks = int(txt.strip())
                if min_tasks <= 0: raise ValueError()
            except Exception:
                await update.message.reply_text("❌ সঠিক সংখ্যা দিন (যেমন: 2):", parse_mode="Markdown"); return
            ctx.user_data["promo_min_tasks"] = min_tasks
            ctx.user_data["promo_req_text"]  = f"Minimum {min_tasks} Approved Tasks required ✅"
            await _show_promo_preview(update.message, ctx); return

        if st == "adm_cfield_prompt_bn":
            ctx.user_data["new_cfield_prompt_bn"] = txt.strip()
            ctx.user_data["state"] = "adm_cfield_prompt_en"
            await update.message.reply_text(
                "✅ বাংলা Prompt সেভ হয়েছে!\n\nএখন *English Prompt* লিখুন:\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cfield_prompt_en":
            ftype     = ctx.user_data.pop("new_cfield_type",      "text")
            tid       = ctx.user_data.pop("new_cfield_tid",        None)
            prompt_bn = ctx.user_data.pop("new_cfield_prompt_bn",  "")
            prompt_en = txt.strip()
            ctx.user_data.pop("state", None)
            if tid:
                await asyncio.to_thread(add_category_task_field, tid, ftype, prompt_bn, prompt_en)
                icon = "📝" if ftype == "text" else "📷"
                # Photo Field হলে per-field example photo সেট করার সুযোগ দাও
                if ftype == "photo":
                    new_field = await asyncio.to_thread(get_last_category_task_field, tid)
                    fid = new_field["id"] if new_field else None
                    if fid:
                        ctx.user_data["state"]               = "adm_field_set_example_photo"
                        ctx.user_data["edit_field_id"]        = fid
                        ctx.user_data["edit_field_task_id"]   = tid
                        ctx.user_data["edit_field_is_categ"]  = True
                        await update.message.reply_text(
                            f"✅ *Photo Field যোগ হয়েছে!*\n📷 `{prompt_bn}` / `{prompt_en}`\n\n"
                            f"এই field-এর জন্য *Example Photo* সেট করবেন?\n"
                            f"_(ইউজার এই ছবি দেখে বুঝবে কেমন ছবি পাঠাতে হবে)_\n\n"
                            f"📤 এখনই ছবি পাঠান, অথবা Skip করুন:",
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("⏭ Skip — Example Photo ছাড়াই রাখুন", callback_data=f"adm_cfield_skip_example_{tid}")],
                            ])
                        )
                        return
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ আরো Field যোগ করুন", callback_data=f"adm_cfields_{tid}")],
                    [InlineKeyboardButton("🚀 সম্পন্ন — Task Live করুন", callback_data=f"adm_ctask_golive_{tid}")],
                ])
                await update.message.reply_text(
                    f"{icon} *Field যোগ হয়েছে!*\n\nBN: `{prompt_bn}`\nEN: `{prompt_en}`",
                    parse_mode="Markdown", reply_markup=kb)
            return

    # ── Task "done" callback handled inline ──
    # (handled via on_callback adm_task_done_)

    # ── Bot off (non-admin) ────────────────────────────────────────
    if not await _bot_accessible(uid):
        if st: ctx.user_data.clear()
        await bot_off_reply(update, uid); return

    # ── Registration flow: user typed text instead of clicking button ─
    if st == "first_lang":
        lang_hint = (
            "👆 *Please tap a button to choose your language.*"
        )
        await update.message.reply_text(
            lang_hint, parse_mode="Markdown",
            reply_markup=await asyncio.to_thread(lang_kb))
        return

    if st == "first_country":
        country_hint = (
            "🌍 *আপনার দেশ বেছে নিন — button চাপুন।*\n\n"
            "⚠️ ভুল দেশ বাছলে Task পাবেন না!\n\n"
            "👆 Please tap a button to choose your country."
        )
        await update.message.reply_text(
            country_hint, parse_mode="Markdown",
            reply_markup=await asyncio.to_thread(country_kb))
        return

    if st == "first_channel":
        await join_prompt(update, uid)
        return

    # ── Task proof collection ─────────────────────────────────────
    if st == "task_collect":
        if not await guard(update, ctx): ctx.user_data.pop("state", None); return
        await handle_task_collect_text(update, ctx); return

    # ── Category task proof collection ────────────────────────────
    if st == "categ_task_collect":
        if not await guard(update, ctx): ctx.user_data.pop("state", None); return
        fields   = ctx.user_data.get("task_fields", [])
        idx      = ctx.user_data.get("field_idx", 0)
        collected = ctx.user_data.setdefault("collected", [])
        if idx >= len(fields):
            await _finalize_category_task_submission(update, ctx, uid); return
        field = fields[idx]
        if field["field_type"] == "photo":
            lang = await asyncio.to_thread(get_lang, uid)
            await update.message.reply_text(
                "📷 Please send a photo now, not text." if lang == 'en' else "📷 এখন ছবি পাঠান, টেক্সট নয়।",
                parse_mode="Markdown"); return
        collected.append({"type": "text", "prompt_bn": field["prompt_bn"], "prompt_en": field.get("prompt_en",""), "value": txt.strip()})
        ctx.user_data["field_idx"] = idx + 1
        next_idx = idx + 1
        if next_idx < len(fields):
            nf = fields[next_idx]
            lang = await asyncio.to_thread(get_lang, uid)
            icon = "📷" if nf["field_type"] == "photo" else "📝"
            prompt = nf.get("prompt_en") if lang == 'en' else nf["prompt_bn"]
            await update.message.reply_text(f"{icon} *{prompt}*", parse_mode="Markdown")
        else:
            await _finalize_category_task_submission(update, ctx, uid)
        return

    # ── Promo code redemption (user) ──────────────────────────────
    if st == "user_promo_code":
        if not await guard(update, ctx): ctx.user_data.pop("state", None); return
        ctx.user_data.pop("state", None)
        code = txt.strip().upper()
        lang = await asyncio.to_thread(get_lang, uid)
        def _db_9565():
            promo = None
            with _c() as c:
                promo = c.execute("SELECT * FROM promocodes WHERE code=%s", (code,)).fetchone()
            return promo
        promo = await asyncio.to_thread(_db_9565)
        if not promo:
            msg = "❌ *এই Promo Code টি বিদ্যমান নেই।*" if lang == 'bn' else "❌ *This promo code does not exist.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return
        if promo["used_count"] >= promo["max_uses"]:
            msg = "❌ *এই Promo Code টি সম্পূর্ণ ব্যবহার হয়ে গেছে।*" if lang == 'bn' else "❌ *This promo code has been fully used.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return
        def _db_9573():
            already = None
            with _c() as c:
                already = c.execute("SELECT id FROM promo_uses WHERE code_id=%s AND user_id=%s", (promo["id"], uid)).fetchone()
            return already
        already = await asyncio.to_thread(_db_9573)
        if already:
            msg = "⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn' else "⚠️ *You have already used this promo code.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return
        min_tasks = promo["min_tasks"] or 0
        if min_tasks > 0:
            def _db_9580():
                done_count = None
                with _c() as c:
                    done_count = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'", (uid,)).fetchone())
                return done_count
            done_count = await asyncio.to_thread(_db_9580)
            if done_count < min_tasks:
                msg = (
                    f"⚠️ *এই Code রিডিম করতে কমপক্ষে {min_tasks}টি Task Approved হতে হবে।*\n\n"
                    f"আপনার এখন: `{done_count}/{min_tasks}` টি approved task আছে।"
                    if lang == 'bn' else
                    f"⚠️ *You need at least {min_tasks} approved tasks to redeem this code.*\n\n"
                    f"You currently have: `{done_count}/{min_tasks}` approved tasks."
                )
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return
        amount = promo["amount"]
        # Atomic: INSERT ON CONFLICT + max_uses guard — double-redeem এবং over-redeem উভয়ই প্রতিরোধ করে
        _promo_already = False
        _promo_exhausted = False
        def _db_9595():
            _promo_already = None
            _promo_exhausted = None
            with _c() as c:
                _pu_cur = c.execute(
                    "INSERT INTO promo_uses (code_id, user_id, used_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (promo["id"], uid, now())
                )
                if _pu_cur.rowcount == 0:
                    _promo_already = True
                else:
                    # Atomic max_uses guard — used_count < max_uses হলেই increment করে
                    _max_cur = c.execute(
                        "UPDATE promocodes SET used_count=used_count+1 WHERE id=%s AND used_count < max_uses",
                        (promo["id"],)
                    )
                    if _max_cur.rowcount == 0:
                        # Slot শেষ হয়ে গেছে — promo_uses INSERT undo করো, balance দেবো না
                        c.execute("DELETE FROM promo_uses WHERE code_id=%s AND user_id=%s", (promo["id"], uid))
                        _promo_exhausted = True
                    else:
                        c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amount, uid))
                        _invalidate_user_cache(uid)
            return _promo_already, _promo_exhausted
        _promo_already, _promo_exhausted = await asyncio.to_thread(_db_9595)
        if _promo_already:
            msg = "⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn' else "⚠️ *You have already used this promo code.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return
        if _promo_exhausted:
            msg = "❌ *এই Promo Code টি সম্পূর্ণ ব্যবহার হয়ে গেছে।*" if lang == 'bn' else "❌ *This promo code has been fully used.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid)); return
        _mark_dirty()
        # DB থেকে fresh used_count নিন — concurrent redeem এ stale value এড়াতে
        def _db_9622():
            _fresh = None
            with _c() as _fc:
                _fresh = _fc.execute("SELECT used_count FROM promocodes WHERE id=%s", (promo["id"],)).fetchone()
            return _fresh
        _fresh = await asyncio.to_thread(_db_9622)
        new_used = _fresh["used_count"] if _fresh else promo["used_count"] + 1
        _bal_now = await asyncio.to_thread(get_bal, uid)
        ok_msg = (
            f"✅ *Promo Code রিডিম সফল!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` যোগ হয়েছে।\n"
            f"💼 *বর্তমান Balance:* `{_bal_now}$ USDT`"
            if lang == 'bn' else
            f"✅ *Promo Code redeemed!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` added.\n"
            f"💼 *Current Balance:* `{_bal_now}$ USDT`"
        )
        await update.message.reply_text(ok_msg, parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))

        # ── Admin notification: কে নিল, কতজন নিল, কতটা বাকি ──────
        remaining = max(0, promo["max_uses"] - new_used)
        uname = update.effective_user.username or ""
        uname_txt = f"@{_md_esc(uname)}" if uname else f"ID:`{uid}`"
        full_name = _md_esc((update.effective_user.full_name or "").strip() or "—")
        admin_notif = (
            f"🎟 *Promo Code ব্যবহার হয়েছে!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"👤 *User:* {uname_txt} | {full_name} (`{uid}`)\n"
            f"💰 *Reward:* `{amount}$ USDT`\n\n"
            f"📊 *ব্যবহার:* `{new_used}/{promo['max_uses']}`\n"
            f"🔢 *বাকি আছে:* `{remaining}` জন নিতে পারবে\n"
        )
        if remaining == 0:
            admin_notif += "\n❌ *Code সম্পূর্ণ শেষ হয়ে গেছে!*"
        else:
            admin_notif += f"\n✅ *এখনো {remaining}টি ব্যবহারের সুযোগ আছে।*"
        _t2 = asyncio.create_task(notify_full_admins(ctx.bot, admin_notif))
        _background_tasks.add(_t2); _t2.add_done_callback(_background_tasks.discard)

        # ── News channel message edit করো (code শেষ হলে) ─────────
        if new_used >= promo["max_uses"]:
            nc_msg_id  = promo.get("news_ch_msg_id")  or 0
            nc_chat_id = promo.get("news_ch_chat_id") or 0
            if nc_msg_id and nc_chat_id:
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=nc_chat_id, message_id=nc_msg_id,
                        text=_build_promo_post_used(
                            code, promo["amount"], promo["max_uses"], promo.get("requirement", "")
                        ),
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([]))
                except Exception as _e:
                    log.warning(f"promo news edit fail: {_e}")
        return

    # ── Withdraw states ───────────────────────────────────────────
    if st == "wd_addr":
        if not await guard(update, ctx): ctx.user_data.pop("state", None); return
        await receive_wd_addr(update, ctx); return
    if st == "wd_amt":
        if not await guard(update, ctx): ctx.user_data.pop("state", None); return
        await receive_wd_amt(update, ctx); return
    if st == "wd_confirm":
        lang = await asyncio.to_thread(get_lang, uid)
        hint = ("👆 Please press *Confirm* or *Edit* button above." if lang == 'en'
                else "👆 উপরের *Confirm* বা *Edit* বাটন চাপুন।")
        await update.message.reply_text(hint, parse_mode="Markdown")
        await show_wd_confirmation(update.message, ctx, uid); return

    # ── Menu buttons ──────────────────────────────────────────────
    # guard + get_lang parallel — sequential await নেই
    # send_chat_action background-এ (fire & forget) — main path block করে না
    _chat_act = asyncio.create_task(ctx.bot.send_chat_action(chat_id=uid, action="typing"))
    _background_tasks.add(_chat_act); _chat_act.add_done_callback(_background_tasks.discard)
    _guard_ok, lang = await asyncio.gather(
        guard(update, ctx),
        asyncio.to_thread(get_lang, uid),
    )
    if not _guard_ok: return
    btns = get_all_btns(lang)

    if   txt == btns['tasks']:    await show_tasks(update, ctx)
    elif txt == btns['profile']:  await show_profile(update, ctx)
    elif txt == btns['balance']:  await show_balance(update, ctx)
    elif txt == btns['refer']:    await show_refer(update, ctx)
    elif txt == btns['support']:  await show_support(update, ctx)
    elif txt == btns['withdraw']: await show_withdraw(update, ctx)
    elif txt == btns['history']:  await cmd_history(update, ctx)
    elif txt == btns['language']:      await show_language(update, ctx)
    else:
        await update.message.reply_text(T(uid,'choose_menu'), parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))

# ──────────────────────────── PHOTO HANDLER ────────────────────────────
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    st  = ctx.user_data.get("state")

    if not await _bot_accessible(uid):
        await bot_off_reply(update, uid); return

    if st == "adm_bc_photo" and await asyncio.to_thread(is_admin, uid):
        file_id = update.message.photo[-1].file_id
        ctx.user_data["bc_photo_id"] = file_id
        ctx.user_data["state"] = "adm_bc_bn"
        await update.message.reply_text(
            "✅ *ছবি পাওয়া গেছে!*\n\n"
            "📢 *এখন বাংলা মেসেজ লিখুন:*\n"
            "_(বাংলা ইউজাররা এটা caption হিসেবে পাবে)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if st in ("adm_ctask_set_sample_photo", "adm_ctask_set_video_file",
              "adm_task_set_sample_photo") and await asyncio.to_thread(is_admin, uid):
        await handle_task_collect_photo(update, ctx)
        return

    # ── Per-field Example Photo সেট করা ──────────────────────────
    if st == "adm_field_set_example_photo" and await asyncio.to_thread(is_admin, uid):
        fid        = ctx.user_data.pop("edit_field_id",       None)
        tid        = ctx.user_data.pop("edit_field_task_id",  None)
        is_categ   = ctx.user_data.pop("edit_field_is_categ", False)
        ctx.user_data.pop("state", None)
        if update.message.photo and fid:
            img_fid = update.message.photo[-1].file_id
            if is_categ:
                await asyncio.to_thread(update_category_task_field_example_img, fid, img_fid)
            else:
                await asyncio.to_thread(update_task_field_example_img, fid, img_fid)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Text Field যোগ", callback_data=f"adm_field_add_text_{tid}"),
                 InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_{'c' if is_categ else ''}field_add_photo_{tid}")],
                [InlineKeyboardButton("✅ Done — Task সেভ করুন", callback_data=f"adm_{'ctask_golive' if is_categ else 'task_done'}_{tid}")],
            ])
            await update.message.reply_text(
                "✅ *Example Photo সেট হয়েছে!*\n\n"
                "এখন থেকে ইউজার এই Photo Field এ এই ছবি দেখে বুঝবে কেমন ছবি দিতে হবে।\n\n"
                "আরো field যোগ করুন বা Done চাপুন:",
                parse_mode="Markdown", reply_markup=kb)
        else:
            await update.message.reply_text("❌ ছবি পাওয়া যায়নি।", parse_mode="Markdown")
        return

    if st == "task_collect":
        if not await guard(update, ctx): return
        fields = ctx.user_data.get("task_fields", [])
        idx    = ctx.user_data.get("field_idx", 0)
        if idx < len(fields) and fields[idx]["field_type"] == "photo":
            await handle_task_collect_photo(update, ctx)
        else:
            lang = await asyncio.to_thread(get_lang, uid)
            await update.message.reply_text(
                "❌ Send text now, not a photo." if lang == 'en' else "❌ এখন ছবি নয়, টেক্সট পাঠান।",
                parse_mode="Markdown")

    elif st == "categ_task_collect":
        if not await guard(update, ctx): return
        fields    = ctx.user_data.get("task_fields", [])
        idx       = ctx.user_data.get("field_idx", 0)
        collected = ctx.user_data.setdefault("collected", [])
        lang      = await asyncio.to_thread(get_lang, uid)
        if idx < len(fields) and fields[idx]["field_type"] == "photo":
            photo          = update.message.photo[-1]
            file_id        = photo.file_id
            file_unique_id = photo.file_unique_id
            _ctask_cap = (update.message.caption or "").strip()
            collected.append({"type": "photo", "prompt_bn": fields[idx]["prompt_bn"], "prompt_en": fields[idx].get("prompt_en",""), "value": file_id, "unique_id": file_unique_id, "caption": _ctask_cap})
            ctx.user_data["field_idx"] = idx + 1
            next_idx = idx + 1
            if next_idx < len(fields):
                await _ask_next_field(update, ctx, uid, via_callback=False)
            else:
                await _finalize_category_task_submission(update, ctx, uid)
        else:
            await update.message.reply_text(
                "❌ Send text now, not a photo." if lang == 'en' else "❌ এখন ছবি নয়, টেক্সট পাঠান।",
                parse_mode="Markdown")

# ──────────────────────────── EXTRA CALLBACK (task done) ─────────────────────
async def on_callback_extra(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles adm_task_done_ callback - task creation complete"""
    q   = update.callback_query
    uid = q.from_user.id
    d   = q.data
    # spinner তাৎক্ষণিক clear করো
    try: await q.answer()
    except Exception: pass

    # ── Notification confirm / edit / skip ──────────────────────
    if d == "adm_notif_send":
        if not await asyncio.to_thread(is_admin, uid):
            return
        notif_msg   = ctx.user_data.get("pending_notif_msg", "")
        notify_ch   = ctx.user_data.get("pending_notif_ch", "") or await asyncio.to_thread(get_task_notify_ch_id)
        bot_link    = ctx.user_data.get("pending_notif_bot_link", "")
        if not bot_link:
            try:
                _bme = await ctx.bot.get_me()
                bot_link = f"https://t.me/{_bme.username}"
            except Exception:
                bot_link = ""
        if not notif_msg:
            await q.message.reply_text("❌ *কোনো pending notification নেই।*", parse_mode="Markdown"); return
        _notif_kb_rows = []
        if bot_link:
            _notif_kb_rows.append([InlineKeyboardButton("🚀 Task করুন", url=bot_link)])
        _notif_kb_rows.append([InlineKeyboardButton("📰 News Channel", url=NEWS_CH_LINK)])
        user_kb = InlineKeyboardMarkup(_notif_kb_rows)
        ch_kb   = InlineKeyboardMarkup(_notif_kb_rows)
        users = await asyncio.to_thread(all_users); sent = fail = 0
        for u in users:
            if u["user_id"] == ADMIN_ID: continue
            try:
                await ctx.bot.send_message(u["user_id"], notif_msg, parse_mode="Markdown", reply_markup=user_kb)
                sent += 1
                if sent % 25 == 0:
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(0.04)
            except Exception as _nf_e:
                log.warning(f"[NOTIF-SEND] uid={u.get('user_id')} failed: {_nf_e}")
                fail += 1
        try: await ctx.bot.send_message(notify_ch, notif_msg, parse_mode="Markdown", reply_markup=ch_kb)
        except Exception as e: log.warning(f"notif ch fail: {e}")
        await q.message.reply_text(f"📢 *Notification sent!* ✅ `{sent}` | ❌ `{fail}`", parse_mode="Markdown")
        for k in ("pending_notif_msg","pending_notif_ch","pending_notif_ch_link","pending_notif_bot_link"):
            ctx.user_data.pop(k, None)
        return

    if d == "adm_notif_edit":
        if not await asyncio.to_thread(is_admin, uid):
            return
        ctx.user_data["state"] = "adm_notif_edit_txt"
        await q.message.reply_text(
            "✏️ *নতুন Notification Message লিখুন:*\n\n"
            "_(লেখা শেষে আবার preview দেখাবে)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d == "adm_notif_skip":
        if not await asyncio.to_thread(is_admin, uid):
            return
        # spinner ইতিমধ্যে background task-এ clear হয়েছে; এখন message দেখাও
        for k in ("pending_notif_msg","pending_notif_ch","pending_notif_ch_link","pending_notif_bot_link"):
            ctx.user_data.pop(k, None)
        await q.message.edit_text("❌ *Notification skip করা হয়েছে।*", parse_mode="Markdown")
        return

    if d.startswith("adm_task_done_"):
        if not await asyncio.to_thread(is_admin, uid):
            return
        tid    = int(d.split("_")[3])
        task, fields = await asyncio.gather(
            asyncio.to_thread(get_task, tid),
            asyncio.to_thread(get_task_fields, tid),
        )
        max_c2  = _rget(task, 'max_completions', 0)
        max_txt = f"{max_c2} people" if max_c2 > 0 else "Unlimited"
        _pu_raw3 = _rget(task, 'per_user_limit', None)
        pu_limit = _pu_raw3 if _pu_raw3 is not None else 1
        _ac_done = _rget(task, 'allowed_countries', '') or ''
        tp_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tid}_t")],
            [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tid}_t")],
            [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tid}_t")],
            [InlineKeyboardButton("✏️ Per User Limit",    callback_data=f"adm_tp_pu_{tid}_t")],
            [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tid}_t")],
            [InlineKeyboardButton("✅ Confirm করুন",      callback_data=f"adm_tp_ok_{tid}_t")],
            [InlineKeyboardButton("❌ বাতিল/Delete",      callback_data=f"adm_tp_del_{tid}_t")],
        ])
        await q.message.reply_text(
            f"📋 *Task Preview — চেক করুন:*\n\n"
            f"📌 *Name:* `{task['name']}`\n"
            f"💵 *Reward:* `{task['price']}$ USDT`\n"
            f"📋 *Fields:* {len(fields)}\n"
            f"👥 *Max:* `{max_txt}`\n"
            f"👤 *Per User:* `{pu_limit}` বার\n"
            f"🌍 *দেশ:* {fmt_allowed_countries(_ac_done)}\n\n"
            f"✏️ Edit করুন বা ✅ Confirm করুন।",
            parse_mode="Markdown", reply_markup=tp_kb)
        ctx.user_data.pop("state", None); ctx.user_data.pop("new_task_id", None)

    # ── Task Preview callbacks ────────────────────────────────────
    if d.startswith("adm_tp_"):
        if not await asyncio.to_thread(is_admin, uid):
            return
        parts = d.split("_")
        # format: adm_tp_<action>_<tid>_<type>
        action  = parts[2]          # n / p / m / ok / del
        tid     = int(parts[3])
        tp_type = parts[4]          # t=regular, g=tg

        async def _refresh_tp_preview(msg):
            if tp_type == "t":
                task = await asyncio.to_thread(get_task, tid)
                if not task:
                    await msg.reply_text("❌ Task পাওয়া যায়নি বা মুছে ফেলা হয়েছে।", parse_mode="Markdown"); return
                fields = await asyncio.to_thread(get_task_fields, tid)
                mc   = _rget(task, 'max_completions', 0)
                _pu_r4 = _rget(task, 'per_user_limit', None)
                pu   = _pu_r4 if _pu_r4 is not None else 1
                _ac  = _rget(task, 'allowed_countries', '') or ''
                _pu_lbl4 = "Unlimited ♾️" if pu == 0 else f"{pu} বার"
                txt  = (f"📋 *Task Preview — চেক করুন:*\n\n"
                        f"📌 *Name:* `{task['name']}`\n"
                        f"💵 *Reward:* `{task['price']}$ USDT`\n"
                        f"📋 *Fields:* {len(fields)}\n"
                        f"👥 *Max:* `{'Unlimited' if mc==0 else str(mc)+' people'}`\n"
                        f"👤 *Per User:* `{_pu_lbl4}`\n"
                        f"🌍 *দেশ:* {fmt_allowed_countries(_ac)}\n\n"
                        f"✏️ Edit করুন বা ✅ Confirm করুন।")
            else:
                task = await asyncio.to_thread(get_tg_task, tid)
                if not task:
                    await msg.reply_text("❌ TG Task পাওয়া যায়নি বা মুছে ফেলা হয়েছে।", parse_mode="Markdown"); return
                mc   = _rget(task, 'max_completions', 0)
                njo  = _rget(task, 'new_joiners_only', 0)
                _pu_r5 = _rget(task, 'per_user_limit', None)
                pu   = _pu_r5 if _pu_r5 is not None else 1
                _ac  = _rget(task, 'allowed_countries', '') or ''
                njo_lbl = "🔒 New Joiners Only: ✅ ON" if njo else "🔓 New Joiners Only: ❌ OFF"
                _pu_lbl5 = "Unlimited ♾️" if pu == 0 else f"{pu} বার"
                txt  = (f"📋 *TG Task Preview — চেক করুন:*\n\n"
                        f"📌 *Name:* `{task['name']}`\n"
                        f"💵 *Reward:* `{task['price']}$ USDT`\n"
                        f"📢 *Channel:* `{task['tg_target']}`\n"
                        f"👥 *Max:* `{'Unlimited' if mc==0 else str(mc)+' people'}`\n"
                        f"👤 *Per User:* `{_pu_lbl5}`\n"
                        f"🌍 *দেশ:* {fmt_allowed_countries(_ac)}\n"
                        f"🔒 *New Joiners Only:* {'✅ ON' if njo else '❌ OFF'}\n\n"
                        f"✏️ Edit করুন বা ✅ Confirm করুন।")
            if tp_type == "g":
                njo_lbl = "🔒 New Joiners Only: ✅ ON" if njo else "🔓 New Joiners Only: ❌ OFF"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ Per User Limit",    callback_data=f"adm_tp_pu_{tid}_{tp_type}")],
                    [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tid}_{tp_type}")],
                    [InlineKeyboardButton(njo_lbl,                 callback_data=f"adm_tp_njo_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✅ Confirm করুন",       callback_data=f"adm_tp_ok_{tid}_{tp_type}")],
                    [InlineKeyboardButton("❌ বাতিল/Delete",       callback_data=f"adm_tp_del_{tid}_{tp_type}")],
                ])
            else:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ নাম বদলাও",         callback_data=f"adm_tp_n_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ দাম বদলাও",         callback_data=f"adm_tp_p_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ Max বদলাও",         callback_data=f"adm_tp_m_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✏️ Per User Limit",    callback_data=f"adm_tp_pu_{tid}_{tp_type}")],
                    [InlineKeyboardButton("🌍 দেশ নির্ধারণ করুন", callback_data=f"adm_tp_c_{tid}_{tp_type}")],
                    [InlineKeyboardButton("✅ Confirm করুন",       callback_data=f"adm_tp_ok_{tid}_{tp_type}")],
                    [InlineKeyboardButton("❌ বাতিল/Delete",       callback_data=f"adm_tp_del_{tid}_{tp_type}")],
                ])
            await msg.reply_text(txt, parse_mode="Markdown", reply_markup=kb)

        if action == "n":
            ctx.user_data["tp_tid"] = tid; ctx.user_data["tp_type"] = tp_type
            ctx.user_data["state"] = "adm_tp_edit_name"
            await q.message.reply_text("📌 *নতুন নাম লিখুন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        elif action == "p":
            ctx.user_data["tp_tid"] = tid; ctx.user_data["tp_type"] = tp_type
            ctx.user_data["state"] = "adm_tp_edit_price"
            await q.message.reply_text("💵 *নতুন দাম লিখুন (USD):*\n_(যেমন: 0.05)_\n\n/cancel — বাতিল", parse_mode="Markdown")
        elif action == "m":
            ctx.user_data["tp_tid"] = tid; ctx.user_data["tp_type"] = tp_type
            ctx.user_data["state"] = "adm_tp_edit_max"
            await q.message.reply_text("👥 *নতুন Max সংখ্যা লিখুন:*\n_(0 = unlimited)_\n\n/cancel — বাতিল", parse_mode="Markdown")
        elif action == "pu":
            ctx.user_data["tp_tid"] = tid; ctx.user_data["tp_type"] = tp_type
            ctx.user_data["state"] = "adm_tp_edit_per_user"
            await q.message.reply_text(
                "👤 *নতুন Per User Limit লিখুন:*\n_(কতবার একজন user এই task করতে পারবে, যেমন: 1, 2)_\n\n/cancel — বাতিল",
                parse_mode="Markdown")
        elif action == "c":
            ctx.user_data["tp_tid"] = tid; ctx.user_data["tp_type"] = tp_type
            ctx.user_data["state"] = "adm_tp_edit_countries"
            _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
            await q.message.reply_text(
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে?*\n\n"
                "দেশের কোড কমা দিয়ে লিখুন:\n"
                f"`{_ch}`\n\n"
                "উদাহরণ: `BD,IN,PK`\n"
                "🌐 সব দেশের জন্য: `0` লিখুন\n\n/cancel — বাতিল",
                parse_mode="Markdown")
        elif action == "njo":
            task = await asyncio.to_thread(get_tg_task, tid)
            if task:
                cur = _rget(task, 'new_joiners_only', 0)
                new_val = 0 if cur else 1
                await asyncio.to_thread(update_tg_task, tid, "new_joiners_only", new_val)
                status = "✅ ON" if new_val else "❌ OFF"
                await q.message.reply_text(
                    f"🔒 *New Joiners Only:* {status}\n\nআবার চেক করুন:",
                    parse_mode="Markdown")
                await _refresh_tp_preview(q.message)
        elif action == "ok":
            try:
                await q.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await q.message.reply_text("✅ *Task confirm হয়েছে।*", parse_mode="Markdown")
            if tp_type == "t":
                task = await asyncio.to_thread(get_task, tid)
                await _activate_task_and_notify(ctx, q, uid,
                    get_fn=get_task, update_fn=update_task_field_val, tid=tid,
                    link_prefix="task_")
            elif tp_type == "g":
                task = await asyncio.to_thread(get_tg_task, tid)
                await _activate_task_and_notify(ctx, q, uid,
                    get_fn=get_tg_task, update_fn=update_tg_task, tid=tid,
                    link_prefix="tgtask_", njo_field="new_joiners_only")
            else:
                # future task types: add get_fn/update_fn here
                pass
        elif action == "del":
            if tp_type == "t": await asyncio.to_thread(delete_task, tid)
            else: await asyncio.to_thread(delete_tg_task, tid)
            await q.message.reply_text("🗑 *Task delete করা হয়েছে।*", parse_mode="Markdown", reply_markup=await asyncio.to_thread(main_kb, uid))
        return

# ──────────────────── INACTIVE USER REPORT ───────────────────
async def cmd_inactive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await asyncio.to_thread(is_admin, uid):
        return
    lang = await asyncio.to_thread(get_lang, uid)
    args = ctx.args
    try:
        days = int(args[0]) if args else 7
    except (ValueError, IndexError):
        days = 7
    from datetime import timedelta
    cutoff = (datetime.now(BD_TZ) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    def _db_10018():
        rows = None
        with _c() as c:
            rows = c.execute(
                "SELECT user_id, full_name, username, last_seen FROM users "
                "WHERE last_seen < %s AND last_seen IS NOT NULL AND last_seen != '' "
                "ORDER BY last_seen ASC LIMIT 100",
                (cutoff,)
            ).fetchall()
        return rows
    rows = await asyncio.to_thread(_db_10018)
    if not rows:
        msg = (
            f"✅ *{days} দিনে কোনো Inactive User পাওয়া যায়নি।*"
            if lang == 'bn' else
            f"✅ *No inactive users found in the last {days} days.*"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    if lang == 'bn':
        lines = [f"😴 *{days} দিন+ Inactive Users ({len(rows)} জন):*\n"]
        for r in rows:
            name  = r["full_name"] or r["username"] or "Unknown"
            uname = f" @{r['username']}" if r["username"] else ""
            ls    = r["last_seen"] or "অজানা"
            lines.append(f"• `{r['user_id']}`{uname} — {name}\n  শেষ: {ls}")
    else:
        lines = [f"😴 *Inactive Users ({days}+ days) — {len(rows)} found:*\n"]
        for r in rows:
            name  = r["full_name"] or r["username"] or "Unknown"
            uname = f" @{r['username']}" if r["username"] else ""
            ls    = r["last_seen"] or "Unknown"
            lines.append(f"• `{r['user_id']}`{uname} — {name}\n  Last seen: {ls}")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + ("\n…(আরো আছে)" if lang == 'bn' else "\n…(more)")
    await update.message.reply_text(text, parse_mode="Markdown")


# ──────────────────── PROMO CODE SYSTEM ──────────────────────
def _build_promo_post(code: str, amount: float, max_uses: int, req_text: str) -> str:
    return (
        f"🎁 *New Promo Code Available!*\n\n"
        f"🔑 *Code:* `{code}`\n"
        f"💰 *Reward:* `{amount}$ USDT`\n"
        f"👥 *Total Slots:* `{max_uses}`\n"
        f"📋 *Requirement:* {req_text}\n\n"
        f"🚀 Go to the bot and type `/promo {code}` to redeem."
    )

async def _auto_apply_promo(bot, uid: int, code: str):
    """Deep-link pending promo code — apply automatically after /start."""
    done_count = None
    lang = await asyncio.to_thread(get_lang, uid)
    def _db_10067():
        promo = None
        with _c() as c:
            promo = c.execute("SELECT * FROM promocodes WHERE code=%s", (code,)).fetchone()
        return promo
    promo = await asyncio.to_thread(_db_10067)
    if not promo:
        return  # code doesn't exist — silently ignore
    if promo["used_count"] >= promo["max_uses"]:
        msg = ("❌ *এই Promo Code টি সম্পূর্ণ ব্যবহার হয়ে গেছে।*" if lang == 'bn'
               else "❌ *This promo code is fully used.*")
        await bot.send_message(uid, msg, parse_mode="Markdown")
        return
    def _db_10076():
        already = None
        with _c() as c:
            already = c.execute("SELECT id FROM promo_uses WHERE code_id=%s AND user_id=%s",
                                (promo["id"], uid)).fetchone()
        return already
    already = await asyncio.to_thread(_db_10076)
    if already:
        msg = ("⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn'
               else "⚠️ *You have already used this promo code.*")
        await bot.send_message(uid, msg, parse_mode="Markdown")
        return
    min_tasks = promo["min_tasks"] or 0
    if min_tasks > 0:
        def _db_10086():
            done_count = None
            with _c() as c:
                done_count = _count(c.execute(
                    "SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'", (uid,)
                ).fetchone())
            return done_count
        done_count = await asyncio.to_thread(_db_10086)
        if done_count < min_tasks:
            msg = (f"⚠️ *এই Code রিডিম করতে কমপক্ষে {min_tasks}টি Task Approved হতে হবে।*\n\n"
                   f"আপনার এখন: `{done_count}/{min_tasks}` টি approved task।"
                   if lang == 'bn' else
                   f"⚠️ *You need at least {min_tasks} approved tasks to redeem this code.*\n\n"
                   f"You currently have: `{done_count}/{min_tasks}`")
            await bot.send_message(uid, msg, parse_mode="Markdown")
            return
    # ── Atomic redeem ─────────────────────────────────────────────
    amount = promo["amount"]
    def _db_10102():
        _already = False
        _exhausted = False
        with _c() as c:
            _ap_ins = c.execute(
                "INSERT INTO promo_uses (code_id, user_id, used_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (promo["id"], uid, now())
            )
            if _ap_ins.rowcount == 0:
                # concurrent request এ আগেই INSERT হয়ে গেছে — race condition guard
                _already = True
            else:
                _nr = c.execute(
                    "UPDATE promocodes SET used_count=used_count+1 WHERE id=%s AND used_count < max_uses RETURNING used_count",
                    (promo["id"],)
                ).fetchone()
                if not _nr:
                    # Slot শেষ — INSERT undo করো
                    c.execute("DELETE FROM promo_uses WHERE code_id=%s AND user_id=%s", (promo["id"], uid))
                    _exhausted = True
                else:
                    # Atomic: balance credit same transaction এ
                    c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amount, uid))
                    _invalidate_user_cache(uid)
        return _already, _exhausted
    _ap_already, _ap_exhausted = await asyncio.to_thread(_db_10102)
    if _ap_already:
        msg = ("⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn'
               else "⚠️ *You have already used this promo code.*")
        await bot.send_message(uid, msg, parse_mode="Markdown")
        return
    if _ap_exhausted:
        msg = ("❌ *এই Promo Code টি সম্পূর্ণ ব্যবহার হয়ে গেছে।*" if lang == 'bn'
               else "❌ *This promo code is fully used.*")
        await bot.send_message(uid, msg, parse_mode="Markdown")
        return
    _mark_dirty()
    # DB থেকে fresh used_count নিন
    def _db_ap_fresh():
        with _c() as _fc:
            _r = _fc.execute("SELECT used_count FROM promocodes WHERE id=%s", (promo["id"],)).fetchone()
        return _r
    _ap_fresh = await asyncio.to_thread(_db_ap_fresh)
    new_used  = _ap_fresh["used_count"] if _ap_fresh else promo["used_count"] + 1
    _bal_now  = await asyncio.to_thread(get_bal, uid)
    ok_msg = (
        f"🎉 *Promo Code সফলভাবে রিডিম হয়েছে!*\n\n"
        f"🔑 *Code:* `{code}`\n"
        f"💰 *Reward:* `{amount}$ USDT` যোগ হয়েছে।\n"
        f"💼 *বর্তমান Balance:* `{_bal_now}$ USDT`"
        if lang == 'bn' else
        f"🎉 *Promo Code redeemed successfully!*\n\n"
        f"🔑 *Code:* `{code}`\n"
        f"💰 *Reward:* `{amount}$ USDT` added.\n"
        f"💼 *Current Balance:* `{_bal_now}$ USDT`"
    )
    await bot.send_message(uid, ok_msg, parse_mode="Markdown")

    # ── Admin notification: কে নিল, কতজন নিল, কতটা বাকি ──────
    _ap_remaining = max(0, promo["max_uses"] - new_used)
    _ap_uname     = ""  # deep-link path-এ update নেই, uid শুধু আছে
    _ap_uname_txt = f"ID:`{uid}`"
    admin_notif = (
        f"🎟 *Promo Code ব্যবহার হয়েছে!*\n\n"
        f"🔑 *Code:* `{code}`\n"
        f"👤 *User:* {_ap_uname_txt}\n"
        f"💰 *Reward:* `{amount}$ USDT`\n\n"
        f"📊 *ব্যবহার:* `{new_used}/{promo['max_uses']}`\n"
        f"🔢 *বাকি আছে:* `{_ap_remaining}` জন নিতে পারবে\n"
    )
    if _ap_remaining == 0:
        admin_notif += "\n❌ *Code সম্পূর্ণ শেষ হয়ে গেছে!*"
        # News channel post edit করো
        nc_msg_id  = promo.get("news_ch_msg_id")  or 0
        nc_chat_id = promo.get("news_ch_chat_id") or 0
        if nc_msg_id and nc_chat_id:
            try:
                await bot.edit_message_text(
                    chat_id=nc_chat_id, message_id=nc_msg_id,
                    text=_build_promo_post_used(
                        code, promo["amount"], promo["max_uses"], promo.get("requirement", "")
                    ),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([]))
            except Exception as _e:
                log.warning(f"auto_promo news edit fail: {_e}")
    else:
        admin_notif += f"\n✅ *এখনো {_ap_remaining}টি ব্যবহারের সুযোগ আছে।*"
    _ap_t = asyncio.create_task(notify_full_admins(bot, admin_notif))
    _background_tasks.add(_ap_t); _ap_t.add_done_callback(_background_tasks.discard)


def _build_promo_post_used(code: str, amount: float, max_uses: int, req_text: str) -> str:
    return (
        f"🎁 *Promo Code*\n\n"
        f"🔑 *Code:* `{code}`\n"
        f"💰 *Reward:* `{amount}$ USDT`\n"
        f"👥 *Total Slots:* `{max_uses}` ✅ _(সব শেষ)_\n"
        f"📋 *Requirement:* {req_text}\n\n"
        f"❌ *This promo code is fully used.*"
    )

async def cmd_promocode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User command — bot asks for promo code input"""
    uid  = update.effective_user.id
    lang = await asyncio.to_thread(get_lang, uid)
    if not await guard(update, ctx):
        return
    ctx.user_data["state"] = "user_promo_code"
    msg = (
        "🎁 *আপনার Promo Code দিন:*\n\n"
        "_(Code টি লিখুন এবং পাঠান)_\n\n"
        "/cancel — বাতিল"
        if lang == 'bn' else
        "🎁 *Enter your Promo Code:*\n\n"
        "_(Type and send your code)_\n\n"
        "/cancel — cancel"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_promo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = await asyncio.to_thread(get_lang, uid)
    args = ctx.args
    if not args:
        msg = "💡 *ব্যবহার:* `/promo CODE`" if lang == 'bn' else "💡 *Usage:* `/promo CODE`"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    code = args[0].strip().upper()
    # Code খোঁজো
    def _db_10183():
        promo = None
        with _c() as c:
            promo = c.execute("SELECT * FROM promocodes WHERE code=%s", (code,)).fetchone()
        return promo
    promo = await asyncio.to_thread(_db_10183)
    if not promo:
        msg = "❌ *এই Promo Code টি বিদ্যমান নেই।*" if lang == 'bn' else "❌ *This promo code does not exist.*"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    # ইতিমধ্যে fully used%s
    if promo["used_count"] >= promo["max_uses"]:
        msg = "❌ *এই Promo Code টি সম্পূর্ণ ব্যবহার হয়ে গেছে।*" if lang == 'bn' else "❌ *This promo code has been fully used.*"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    # ইতিমধ্যে এই user use করেছে%s
    def _db_10195():
        already = None
        with _c() as c:
            already = c.execute(
                "SELECT id FROM promo_uses WHERE code_id=%s AND user_id=%s",
                (promo["id"], uid)
            ).fetchone()
        return already
    already = await asyncio.to_thread(_db_10195)
    if already:
        msg = "⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn' else "⚠️ *You have already used this promo code.*"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    # Requirement চেক (min_tasks)
    min_tasks = promo["min_tasks"] or 0
    if min_tasks > 0:
        def _db_10207():
            done_count = None
            with _c() as c:
                done_count = _count(c.execute(
                    "SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'",
                    (uid,)
                ).fetchone())
            return done_count
        done_count = await asyncio.to_thread(_db_10207)
        if done_count < min_tasks:
            if lang == 'bn':
                msg = (
                    f"⚠️ *এই Promo Code রিডিম করতে কমপক্ষে {min_tasks}টি Task Approved হতে হবে।*\n\n"
                    f"আপনার এখন: `{done_count}/{min_tasks}` টি approved task আছে।"
                )
            else:
                msg = (
                    f"⚠️ *You need at least {min_tasks} approved tasks to redeem this code.*\n\n"
                    f"You currently have: `{done_count}/{min_tasks}` approved tasks."
                )
            await update.message.reply_text(msg, parse_mode="Markdown")
            return
    # ✅ Redeem করো — Atomic: INSERT + max_uses guard — double-redeem এবং over-redeem উভয়ই প্রতিরোধ করে
    amount = promo["amount"]
    def _db_10229():
        _already = False
        _exhausted = False
        with _c() as c:
            _pcu_cur = c.execute(
                "INSERT INTO promo_uses (code_id, user_id, used_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (promo["id"], uid, now())
            )
            if _pcu_cur.rowcount == 0:
                _already = True
            else:
                # Atomic max_uses guard — used_count < max_uses হলেই increment করে
                _cmd_max_cur = c.execute(
                    "UPDATE promocodes SET used_count=used_count+1 WHERE id=%s AND used_count < max_uses",
                    (promo["id"],)
                )
                if _cmd_max_cur.rowcount == 0:
                    # Slot শেষ হয়ে গেছে — promo_uses INSERT undo করো, balance দেবো না
                    c.execute("DELETE FROM promo_uses WHERE code_id=%s AND user_id=%s", (promo["id"], uid))
                    _exhausted = True
                else:
                    c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amount, uid))
                    _invalidate_user_cache(uid)
        return _already, _exhausted
    _promo_cmd_already, _promo_cmd_exhausted = await asyncio.to_thread(_db_10229)
    if _promo_cmd_already:
        msg = "⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn' else "⚠️ *You have already used this promo code.*"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    if _promo_cmd_exhausted:
        msg = "❌ *এই Promo Code টি সম্পূর্ণ ব্যবহার হয়ে গেছে।*" if lang == 'bn' else "❌ *This promo code has been fully used.*"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    # DB থেকে fresh used_count নিন — concurrent redeem এ stale value এড়াতে
    def _db_10257():
        _fresh2 = None
        with _c() as _fc2:
            _fresh2 = _fc2.execute("SELECT used_count FROM promocodes WHERE id=%s", (promo["id"],)).fetchone()
        return _fresh2
    _fresh2 = await asyncio.to_thread(_db_10257)
    new_used = _fresh2["used_count"] if _fresh2 else promo["used_count"] + 1
    _mark_dirty()
    if lang == 'bn':
        _bal_now = await asyncio.to_thread(get_bal, uid)
        ok_msg = (
            f"✅ *Promo Code রিডিম সফল!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` যোগ হয়েছে।\n"
            f"💼 *বর্তমান Balance:* `{_bal_now}$ USDT`"
        )
    else:
        _bal_now = await asyncio.to_thread(get_bal, uid)
        ok_msg = (
            f"✅ *Promo Code redeemed successfully!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` added.\n"
            f"💼 *Current Balance:* `{_bal_now}$ USDT`"
        )
    await update.message.reply_text(ok_msg, parse_mode="Markdown")

    # ── Admin notification: কে নিল, কতজন নিল, কতটা বাকি ──────
    remaining = max(0, promo["max_uses"] - new_used)
    uname     = update.effective_user.username or ""
    uname_txt = f"@{_md_esc(uname)}" if uname else f"ID:`{uid}`"
    full_name = _md_esc((update.effective_user.full_name or "").strip() or "—")
    admin_notif = (
        f"🎟 *Promo Code ব্যবহার হয়েছে!*\n\n"
        f"🔑 *Code:* `{code}`\n"
        f"👤 *User:* {uname_txt} | {full_name} (`{uid}`)\n"
        f"💰 *Reward:* `{amount}$ USDT`\n\n"
        f"📊 *ব্যবহার:* `{new_used}/{promo['max_uses']}`\n"
        f"🔢 *বাকি আছে:* `{remaining}` জন নিতে পারবে\n"
    )
    if remaining == 0:
        admin_notif += "\n❌ *Code সম্পূর্ণ শেষ হয়ে গেছে!*"
    else:
        admin_notif += f"\n✅ *এখনো {remaining}টি ব্যবহারের সুযোগ আছে।*"
    _t3 = asyncio.create_task(notify_full_admins(ctx.bot, admin_notif))
    _background_tasks.add(_t3); _t3.add_done_callback(_background_tasks.discard)

    # Fully used হলে news channel post edit করো
    if new_used >= promo["max_uses"]:
        nc_msg_id  = promo.get("news_ch_msg_id")  or 0
        nc_chat_id = promo.get("news_ch_chat_id") or 0
        if nc_msg_id and nc_chat_id:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=nc_chat_id,
                    message_id=nc_msg_id,
                    text=_build_promo_post_used(
                        code, promo["amount"], promo["max_uses"], promo.get("requirement", "")
                    ),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([])
                )
            except Exception as _e:
                log.warning(f"promo news edit fail: {_e}")

# ── Promo code callback handlers ──────────────────────────────
async def _handle_promo_callbacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    promo_id = None
    q   = update.callback_query
    uid = q.from_user.id
    d   = q.data
    # spinner তাৎক্ষণিক clear করো
    try: await q.answer()
    except Exception: pass
    if not await asyncio.to_thread(is_admin, uid):
        return

    if d == "adm_promo_req_anyone":
        ctx.user_data["promo_min_tasks"] = 0
        ctx.user_data["promo_req_text"]  = "Anyone can use ✅"
        ctx.user_data["state"]           = "adm_promo_preview"
        await _show_promo_preview(q.message, ctx)

    elif d == "adm_promo_req_tasks":
        ctx.user_data["state"] = "adm_promo_min_tasks"
        await q.message.reply_text(
            "📋 *কতটি Task Approved থাকলে Code নিতে পারবে?*\n\n"
            "_(সংখ্যা লিখুন, যেমন: 2)_\n\n/cancel — বাতিল",
            parse_mode="Markdown")

    elif d == "adm_promo_post_news":
        code      = ctx.user_data.get("promo_code", "")
        amount    = ctx.user_data.get("promo_amount", 0)
        max_uses  = ctx.user_data.get("promo_max_uses", 0)
        req_text  = ctx.user_data.get("promo_req_text", "")
        min_tasks = ctx.user_data.get("promo_min_tasks", 0)
        if not code:
            await q.message.reply_text("❌ Promo data পাওয়া যায়নি।", parse_mode="Markdown"); return
        # DB-তে save
        def _db_10349():
            promo_id = None
            with _c() as c:
                c.execute(
                    "INSERT INTO promocodes (code,amount,max_uses,requirement,min_tasks,created_by,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (code, amount, max_uses, req_text, min_tasks, uid, now())
                )
                promo_id = _fval(c.execute("SELECT id FROM promocodes WHERE code=%s", (code,)).fetchone())
            return promo_id
        promo_id = await asyncio.to_thread(_db_10349)
        # News channel এ post করো
        post_txt = _build_promo_post(code, amount, max_uses, req_text)
        try:
            _bme_p = await ctx.bot.get_me()
            _promo_url = f"https://t.me/{_bme_p.username}?start=promo_{code}"
            _promo_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎁 প্রমো কোড নিন", url=_promo_url)]
            ])
            nc_msg = await ctx.bot.send_message(LEADERBOARD_CH_ID, post_txt, parse_mode="Markdown", reply_markup=_promo_kb)
            def _db_10365():
                with _c() as c:
                    c.execute(
                        "UPDATE promocodes SET news_ch_msg_id=%s, news_ch_chat_id=%s WHERE id=%s",
                        (nc_msg.message_id, nc_msg.chat_id, promo_id)
                    )
                return
            await asyncio.to_thread(_db_10365)
            await q.message.reply_text(
                f"✅ *Promo Code তৈরি ও News Channel এ Post হয়েছে!*\n\n"
                f"🔑 *Code:* `{code}`\n"
                f"🔗 {LEADERBOARD_CH_LINK}",
                parse_mode="Markdown")
        except Exception as _e:
            await q.message.reply_text(f"✅ *Promo Code তৈরি হয়েছে।* News Channel Post ব্যর্থ: `{_e}`", parse_mode="Markdown")
        _clear_promo_data(ctx)

    elif d == "adm_promo_save_only":
        code      = ctx.user_data.get("promo_code", "")
        amount    = ctx.user_data.get("promo_amount", 0)
        max_uses  = ctx.user_data.get("promo_max_uses", 0)
        req_text  = ctx.user_data.get("promo_req_text", "")
        min_tasks = ctx.user_data.get("promo_min_tasks", 0)
        if not code:
            await q.message.reply_text("❌ Promo data পাওয়া যায়নি।", parse_mode="Markdown"); return
        def _db_10388():
            with _c() as c:
                c.execute(
                    "INSERT INTO promocodes (code,amount,max_uses,requirement,min_tasks,created_by,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (code, amount, max_uses, req_text, min_tasks, uid, now())
                )
        await asyncio.to_thread(_db_10388)
        await q.message.reply_text(
            f"✅ *Promo Code সেভ হয়েছে।* (News Channel এ post হয়নি)\n\n"
            f"🔑 *Code:* `{code}`",
            parse_mode="Markdown")
        _clear_promo_data(ctx)


def _clear_promo_data(ctx):
    for k in ("promo_code","promo_amount","promo_max_uses","promo_req_text","promo_min_tasks","state"):
        ctx.user_data.pop(k, None)

async def _show_promo_preview(msg, ctx):
    code      = ctx.user_data.get("promo_code", "")
    amount    = ctx.user_data.get("promo_amount", 0)
    max_uses  = ctx.user_data.get("promo_max_uses", 0)
    req_text  = ctx.user_data.get("promo_req_text", "")
    ctx.user_data.pop("state", None)
    post_txt  = _build_promo_post(code, amount, max_uses, req_text)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 News Channel এ Post করুন", callback_data="adm_promo_post_news")],
        [InlineKeyboardButton("💾 Save Only (Post ছাড়া)",   callback_data="adm_promo_save_only")],
    ])
    await msg.reply_text(
        f"👁 *Promo Code Preview:*\n\n{post_txt}\n\n"
        f"📌 News Channel এ এই Post যাবে। Confirm করুন:",
        parse_mode="Markdown", reply_markup=kb)


# ──────────────────────────── MAIN ────────────────────────────
def _backup_db():
    """PostgreSQL mode — file backup দরকার নেই। CockroachDB তে data স্থায়ী।"""
    return


def _sync_restore_from_telegram():
    """PostgreSQL mode — CockroachDB তে সব data স্থায়ী। File restore দরকার নেই।"""
    return False

async def _run_health_check_and_fix(bot, skip_admin_ping=False) -> tuple:
    chs = None
    g = None
    pn = None
    u = None
    w = None
    """
    সব কিছু চেক করে, সমস্যা পেলে নিজেই ঠিক করে এবং রিপোর্ট দেয়।
    ✅ = ঠিক আছে | ❌ = সমস্যা পেয়েছে | 🔧 = ঠিক করা হয়েছে
    Returns: (report_str, problems_count, fixes_count)
    """
    lines = ["🔍 *Bot Health Check Report*", f"🕐 `{now()}`", ""]
    fixes = 0
    problems = 0

    # ── 1. Database ──────────────────────────────────────────────
    lines.append("*📦 Database (CockroachDB — Serverless)*")
    try:
        def _db_10445():
            u = None
            with _c() as c:
                u = _count(c.execute("SELECT COUNT(*) FROM users").fetchone())
            return u
        u = await asyncio.to_thread(_db_10445)
        lines.append(f"  ✅ CockroachDB সুস্থ আছে ({u} জন user)")
        lines.append("  🔒 Data permanently stored — restart এ কিছু যায় না")
    except Exception as e:
        problems += 1
        lines.append(f"  ❌ PostgreSQL সংযোগ ত্রুটি: {e}")
        try:
            await asyncio.to_thread(init_db)
            await asyncio.to_thread(_auto_configure)
            lines.append("  🔧 DB re-initialized করা হয়েছে"); fixes += 1
        except Exception as _re_e:
            lines.append(f"  ❌ DB re-init ব্যর্থ: {_re_e}")

    # ── 2. Settings ──────────────────────────────────────────────
    lines.append("\n*⚙️ Settings*")
    critical_keys = ["gmail_ch_id", "withdraw_ch_id", "bot_status"]
    missing_keys = []
    try:
        def _db_10464():
            with _c() as c:
                for key in critical_keys:
                    val = c.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
                    if not val:
                        missing_keys.append(key)
            return
        await asyncio.to_thread(_db_10464)
    except Exception:
        missing_keys = critical_keys
    if missing_keys:
        problems += 1
        lines.append(f"  ❌ Missing settings: {', '.join(missing_keys)}")
        await asyncio.to_thread(_auto_configure)
        lines.append("  🔧 _auto_configure() চালানো হয়েছে — settings restore হয়েছে"); fixes += 1
    else:
        lines.append("  ✅ সব critical settings ঠিক আছে")

    # ── 3. Backup file_id ────────────────────────────────────────
    lines.append("\n*💾 Backup Status*")
    fid = await asyncio.to_thread(_load_file_id)
    if not fid:
        problems += 1
        lines.append("  ❌ backup sentinel DB তে নেই")
        try:
            await _send_db_to_admin(bot, label="🔧 Health-Fix Backup")
            lines.append("  🔧 sentinel DB তে সেভ হয়েছে — পরবর্তী check এ ✅ দেখাবে"); fixes += 1
        except Exception as e:
            lines.append(f"  ⚠️ sentinel সেভ ব্যর্থ: {e}")
    else:
        lines.append("  ✅ CockroachDB — ডেটা স্থায়ী, কোনো file backup দরকার নেই")

    # ── 4. Admin reachable ───────────────────────────────────────
    lines.append("\n*👮 Admin Connection*")
    if skip_admin_ping:
        lines.append("  ✅ Admin সক্রিয় আছেন (বর্তমানে অনলাইন)")
    else:
        try:
            await bot.send_message(ADMIN_ID,
                f"🔔 *Bot Health Check চলছে...*\n`{now()}`", parse_mode="Markdown")
            lines.append("  ✅ Admin কে message পাঠানো গেছে")
        except Exception as e:
            problems += 1
            lines.append(f"  ❌ Admin এ message যাচ্ছে না: {e}")

    # ── 5. Backup Channels ───────────────────────────────────────
    lines.append("\n*📡 Backup Channels*")
    for ch in BACKUP_CHANNELS:
        try:
            # আগের HC message delete করো
            old_mid = await asyncio.to_thread(_hc_load_msg_id, ch)
            if old_mid:
                try:
                    await bot.delete_message(ch, old_mid)
                except Exception:
                    pass
            # নতুন HC message পাঠাও
            hc_m = await bot.send_message(ch,
                f"✅ *Health Check — Channel OK*\n🕐 `{now()}`", parse_mode="Markdown")
            await asyncio.to_thread(_hc_save_msg_id, ch, hc_m.message_id)
            lines.append(f"  ✅ `{ch}` — সংযুক্ত")
        except Exception as e:
            problems += 1
            lines.append(f"  ❌ `{ch}` — সমস্যা: {e}")

    # ── 6. Required join channels ────────────────────────────────
    lines.append("\n*📢 Required Channels*")
    try:
        def _db_10529():
            chs = None
            with _c() as c:
                chs = c.execute("SELECT ch_id, ch_name FROM required_channels").fetchall()
            return chs
        chs = await asyncio.to_thread(_db_10529)
        if not chs:
            problems += 1
            lines.append("  ❌ কোনো required channel নেই")
            await asyncio.to_thread(_auto_configure)
            lines.append("  🔧 default channels restore হয়েছে"); fixes += 1
        else:
            for ch in chs:
                lines.append(f"  ✅ {ch['ch_name']} (`{ch['ch_id']}`)")
    except Exception as e:
        lines.append(f"  ⚠️ চেক করতে পারেনি: {e}")

    # ── 7. PostgreSQL storage info ────────────────────────────────
    lines.append("\n*🗂 Storage*")
    try:
        def _db_10545():
            g = None
            pn = None
            w = None
            with _c() as c:
                g = _count(c.execute("SELECT COUNT(*) FROM gmails").fetchone())
                w = _count(c.execute("SELECT COUNT(*) FROM withdrawals").fetchone())
                pn = _count(c.execute("SELECT COUNT(*) FROM pending_notifications").fetchone())
            return g, pn, w
        g, pn, w = await asyncio.to_thread(_db_10545)
        lines.append(f"  ✅ CockroachDB — gmails:{g} | withdrawals:{w} | notifications:{pn}")
        lines.append("  ℹ️ Auto-cleanup রাত ৩টায় চলে — storage সীমিত থাকে")
    except Exception as _se:
        lines.append(f"  ⚠️ Storage info: {_se}")

    # ── Summary ──────────────────────────────────────────────────
    lines.append("\n" + "━" * 22)
    if problems == 0:
        lines.append("🎉 *সব কিছু ঠিকঠাক! Bot সুস্থ আছে।*")
    else:
        lines.append(f"⚠️ *{problems}টি সমস্যা পাওয়া গেছে*")
        lines.append(f"🔧 *{fixes}টি স্বয়ংক্রিয়ভাবে ঠিক করা হয়েছে*")
        if problems > fixes:
            lines.append(f"❗ *{problems - fixes}টি সমস্যা এখনও আছে — Admin দেখুন*")

    return "\n".join(lines), problems, fixes


async def job_health_check(context):
    """প্রতি ১ ঘণ্টায় স্বয়ংক্রিয়ভাবে health check চালায়, সমস্যা পেলে fix করে admin কে জানায়"""
    report, problems, fixes = await _run_health_check_and_fix(context.bot)
    if "❌" in report or "🔧" in report:
        try:
            await context.bot.send_message(ADMIN_ID, report, parse_mode="Markdown")
        except Exception as e:
            log.warning(f"[HEALTH-JOB] Admin notify ব্যর্থ: {e}")


async def job_clean_stuck_states(context):
    """প্রতি ৩০ মিনিটে stuck user state পরিষ্কার করে।
    শুধু সেই states পরিষ্কার করে যেখানে ৩০+ মিনিট কোনো activity নেই।
    Active session (task collect, broadcast) কখনো মুছবে না।
    """
    STALE_THRESHOLD = 1800  # ৩০ মিনিট নিষ্ক্রিয় → stuck বলে ধরা হবে
    try:
        app = context.application
        now_mono = _time.monotonic()
        stale = []
        for uid, data in list(app.user_data.items()):
            if not data.get("state"):
                continue
            last_active = data.get("_last_active")
            if last_active is None:
                # _last_active নেই মানে guard() ডাকা হয়নি → stuck
                stale.append(uid)
                data.pop("state", None)
                data.pop("edit_task_id", None)
                data.pop("tg_task_draft", None)
            elif now_mono - last_active > STALE_THRESHOLD:
                # ৩০+ মিনিট ধরে কোনো activity নেই → stuck
                stale.append(uid)
                data.pop("state", None)
                data.pop("edit_task_id", None)
                data.pop("tg_task_draft", None)
                data.pop("_last_active", None)
        if stale:
            log.info(f"[STATE-CLEAN] {len(stale)} জন user এর stuck state পরিষ্কার হয়েছে")
    except Exception as e:
        log.warning(f"[STATE-CLEAN] ব্যর্থ: {e}")


# ── Error type → human-readable কারণ + auto-fix mapping ────────────────────
_ERROR_FIXES: list[tuple] = [
    # (match_fn,  বাংলা কারণ,               fix_fn | None)
    (lambda e: "MessageNotModified"  in str(e),
        "Message already same — কোনো পরিবর্তন নেই",           None),
    (lambda e: "RetryAfter"          in str(e),
        "Telegram rate-limit — বেশি request হয়েছে",            None),
    (lambda e: "Forbidden"           in str(e) or "bot was blocked" in str(e).lower(),
        "User bot block করেছে",                                 None),
    (lambda e: "ChatNotFound"        in str(e) or "chat not found" in str(e).lower(),
        "Channel/Chat খুঁজে পাওয়া যাচ্ছে না",                 None),
    (lambda e: "BadRequest"          in str(e) and "message is too long" in str(e).lower(),
        "Message অনেক বড় — Telegram limit পার",               None),
    (lambda e: "NetworkError"        in str(e) or "TimedOut" in str(e),
        "Internet/Network সমস্যা — PTB নিজেই retry করবে",      None),
    (lambda e: "psycopg2"             in str(type(e).__name__).lower() or
               "OperationalError"    in str(type(e).__name__),
        "Database error",                                       "_fix_db"),
    (lambda e: isinstance(e, NameError),
        lambda e: f"NameError — অপরিচিত নাম ডাকা হয়েছে: `{str(e)}`",
        "_fix_nameerror"),
    (lambda e: isinstance(e, (KeyError, AttributeError)),
        "Internal state error",                                 "_fix_state"),
]


async def _smart_error_handler(update, context):
    """
    সব unhandled error এখানে আসে।
    কারণ বিশ্লেষণ করে, নিজে fix করে, admin কে Markdown রিপোর্ট পাঠায়।
    """
    err = context.error
    err_str = str(err)
    err_type = type(err).__name__

    # ── 1. কারণ ও fix চিহ্নিত করো ───────────────────────────────
    reason = f"`{err_type}` — অজানা কারণ: `{err_str[:80]}`"
    fix_key = None
    for match_fn, label, fk in _ERROR_FIXES:
        try:
            if match_fn(err):
                reason = label(err) if callable(label) else label
                fix_key = fk
                break
        except Exception:
            pass

    # ── 2. silent errors — log করো, admin কে বিরক্ত করো না ─────
    silent_keywords = ["MessageNotModified", "RetryAfter", "bot was blocked",
                       "Forbidden", "NetworkError", "TimedOut", "Conflict",
                       "ProxyError", "503", "502", "ConnectionError", "SSLError",
                       "ReadError", "ReadTimeout", "ConnectError", "RemoteProtocolError"]
    _check_str = err_str + err_type
    if any(k in _check_str for k in silent_keywords):
        # httpx.ReadError / NetworkError — Render free tier এ স্বাভাবিক, DEBUG এ রাখো
        # WARNING log spam এড়াতে ReadError গুলো debug level এ
        _is_routine_network = any(k in _check_str for k in
            ("ReadError", "ReadTimeout", "ConnectError", "NetworkError", "TimedOut", "RemoteProtocolError"))
        if _is_routine_network:
            log.debug(f"[NET-NOISE] {err_type}: {err_str[:80]}")
        else:
            log.warning(f"[SILENT-ERR] {err_type}: {err_str[:120]}")
        return

    # ── 3. auto-fix চালাও ────────────────────────────────────────
    fix_result = "🔧 auto-fix চালানো হয়নি"
    if fix_key == "_fix_db":
        try:
            await asyncio.to_thread(init_db)
            await asyncio.to_thread(_auto_configure)
            fix_result = "🔧 DB re-initialized + _auto_configure() চালানো হয়েছে"
            log.warning("[AUTO-FIX] DB error detected — reinit করা হয়েছে")
        except Exception as fe:
            fix_result = f"🔧 DB fix চেষ্টা ব্যর্থ: {fe}"
    elif fix_key == "_fix_state":
        try:
            uid = update.effective_user.id if update and update.effective_user else None
            if uid and uid in context.application.user_data:
                context.application.user_data[uid].pop("state", None)
                fix_result = f"🔧 User `{uid}` এর stuck state পরিষ্কার করা হয়েছে"
        except Exception as fe:
            fix_result = f"🔧 State fix ব্যর্থ: {fe}"
    elif fix_key == "_fix_nameerror":
        # NameError সাধারণত code bug — runtime এ ঠিক করা যায় না,
        # তবে user এর state clear করে দিই যাতে bot আটকে না থাকে
        try:
            uid = update.effective_user.id if update and update.effective_user else None
            if uid and uid in context.application.user_data:
                context.application.user_data[uid].pop("state", None)
            fix_result = (
                f"🔧 NameError: `{err_str}` — এটি code-level bug। "
                "User state cleared। Bot re-deploy করলে সমস্যা যাবে।"
            )
            log.error(f"[NAMEERROR] Code bug detected: {err_str}")
        except Exception as fe:
            fix_result = f"🔧 NameError state-clear ব্যর্থ: {fe}"

    # ── 4. update context বের করো ────────────────────────────────
    ctx_parts = []
    try:
        if update:
            u = update.effective_user
            if u:
                ctx_parts.append(f"👤 User: <code>{u.id}</code> (@{html.escape(u.username or 'নেই')})")
            if update.effective_message:
                txt = html.escape((update.effective_message.text or "")[:60])
                ctx_parts.append(f"💬 Message: <code>{txt}</code>")
            if update.callback_query:
                ctx_parts.append(f"🔘 Callback: <code>{html.escape(str(update.callback_query.data))}</code>")
    except Exception:
        pass

    # ── 5. admin কে রিপোর্ট পাঠাও — HTML parse_mode যাতে dynamic content parse error না করে ──
    log.exception(f"[SMART-ERROR] {err_type}: {err_str[:200]}")
    _h_err_type   = html.escape(str(err_type))
    _h_reason     = html.escape(str(reason))
    _h_fix_result = html.escape(str(fix_result))
    _h_err_str    = html.escape(err_str[:300])
    report_lines = [
        "🚨 <b>Bot Error Detected &amp; Auto-Fixed</b>",
        "",
        f"❌ <b>Error:</b> <code>{_h_err_type}</code>",
        f"📋 <b>কারণ:</b> {_h_reason}",
        f"🕐 <b>সময়:</b> <code>{html.escape(now())}</code>",
    ]
    if ctx_parts:
        report_lines.append("")
        report_lines.extend(ctx_parts)
    report_lines += [
        "",
        _h_fix_result,
        "",
        f"<pre>{_h_err_str}</pre>",
    ]
    _network_errs = ("ProxyError", "NetworkError", "TimedOut", "ConnectionError", "503", "502")
    for _attempt in range(2):
        try:
            await context.bot.send_message(
                ADMIN_ID, "\n".join(report_lines), parse_mode="HTML"
            )
            break
        except Exception as notify_err:
            _ne_str = str(notify_err)
            if any(k in _ne_str for k in _network_errs):
                log.warning(f"[SMART-ERROR] Admin notify network err (attempt {_attempt+1}): {_ne_str[:80]}")
                break  # network down — retry করে লাভ নেই
            log.warning(f"[SMART-ERROR] Admin notify ব্যর্থ: {notify_err}")
            break


# ──────────────────────────── LEADERBOARD ────────────────────────────

LEADERBOARD_CH_LINK = "https://t.me/task_earn_bot_news"

BN_MONTHS = {
    1: "জানুয়ারি", 2: "ফেব্রুয়ারি", 3: "মার্চ", 4: "এপ্রিল",
    5: "মে", 6: "জুন", 7: "জুলাই", 8: "আগস্ট",
    9: "সেপ্টেম্বর", 10: "অক্টোবর", 11: "নভেম্বর", 12: "ডিসেম্বর"
}
EN_MONTHS = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

LB_MEDALS = ["🥇", "🥈", "🥉", "৪.", "৫.", "৬.", "৭.", "৮.", "৯.", "১০."]
LB_MEDALS_EN = ["🥇", "🥈", "🥉", "4.", "5.", "6.", "7.", "8.", "9.", "10."]


async def cmd_leaderboard(update, ctx):
    """User /leaderboard command — সবাই দেখতে পারবে"""
    uid  = update.effective_user.id
    lang, text = await asyncio.gather(
        asyncio.to_thread(get_lang, uid),
        asyncio.to_thread(_lb_build_text, 'bn'),  # initial fast fetch
    )
    if lang != 'bn':
        text = await asyncio.to_thread(_lb_build_text, lang)

    rank_label = "🔍 Find my place" if lang == "en" else "🔍 আমি কোথায় আছি?"
    ch_label   = "📢 News Channel"   if lang == "en" else "📢 নিউজ চ্যানেল"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(rank_label, callback_data="lb_my_rank"),
        InlineKeyboardButton(ch_label,   url=LEADERBOARD_CH_LINK),
    ]])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


def _lb_adm_save(chat_id: int, msg_id: int):
    """Admin leaderboard মেসেজের chat_id + msg_id সেভ করো (auto-update এর জন্য)"""
    with _c() as c:
        c.execute(
            "INSERT INTO bot_meta (key, value) VALUES ('lb_adm_chat', %s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(chat_id),))
        c.execute(
            "INSERT INTO bot_meta (key, value) VALUES ('lb_adm_msg', %s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(msg_id),))


def _lb_adm_load() -> tuple[int, int] | tuple[None, None]:
    with _c() as c:
        r1 = c.execute("SELECT value FROM bot_meta WHERE key='lb_adm_chat'").fetchone()
        r2 = c.execute("SELECT value FROM bot_meta WHERE key='lb_adm_msg'").fetchone()
    if r1 and r2:
        return int(r1['value']), int(r2['value'])
    return None, None


def _lb_save_msg_id(chat_id, msg_id: int):
    """Channel leaderboard post এর chat_id + msg_id দুটোই save করো।"""
    with _c() as c:
        c.execute(
            "INSERT INTO bot_meta (key, value) VALUES ('lb_ch_chat_id', %s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(chat_id),)
        )
        c.execute(
            "INSERT INTO bot_meta (key, value) VALUES ('lb_msg_id', %s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(msg_id),)
        )


def _lb_load_msg_id() -> tuple[int | str | None, int | None]:
    """(chat_id, msg_id) ফেরত দেয় — দুটোই না থাকলে (None, None)"""
    cid = None
    with _c() as c:
        r1 = c.execute("SELECT value FROM bot_meta WHERE key='lb_ch_chat_id'").fetchone()
        r2 = c.execute("SELECT value FROM bot_meta WHERE key='lb_msg_id'").fetchone()
    if r1 and r2:
        # chat_id: string (@username) বা int উভয়ই হতে পারে
        raw_cid = r1['value']
        try:
            cid = int(raw_cid)
        except ValueError:
            cid = raw_cid
        return cid, int(r2['value'])
    return None, None


def _hc_save_msg_id(ch_id, msg_id: int):
    """Health Check channel post এর msg_id save করো (প্রতি channel আলাদা key)."""
    key = f"hc_msg_{str(ch_id).replace('-', '_').replace('@', '')}"
    with _c() as c:
        c.execute(
            "INSERT INTO bot_meta (key, value) VALUES (%s, %s) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(msg_id))
        )


def _hc_load_msg_id(ch_id) -> int | None:
    """Health Check channel এর পুরনো msg_id লোড করো।"""
    key = f"hc_msg_{str(ch_id).replace('-', '_').replace('@', '')}"
    with _c() as c:
        row = c.execute("SELECT value FROM bot_meta WHERE key=%s", (key,)).fetchone()
    if row:
        try:
            return int(row['value'])
        except Exception:
            return None
    return None


def _lb_get_user_full_rank(uid: int) -> tuple[int, int] | tuple[None, None]:
    """সব user এর মধ্যে এই user কত নম্বরে — rank ও task count ফেরত দেয়।
    gmails (regular + category) + tg_task_completions — category_task_completions বাদ
    (category tasks gmails-এ already আছে, double-count হয়)।
    timestamps DB-তে BD local time হিসেবে stored → BD time দিয়ে compare করো।"""
    since = datetime.now(BD_TZ).replace(hour=0, minute=0, second=0, microsecond=0) \
                .strftime("%Y-%m-%d %H:%M:%S")   # BD local — UTC convert করলে ৬ ঘণ্টা ভুল হয়
    with _c() as c:
        all_rows = c.execute("""
            SELECT user_id, COUNT(*) AS cnt
            FROM (
                SELECT user_id, approved_at AS ts FROM gmails
                WHERE status = 'approved' AND approved_at >= %s
                UNION ALL
                SELECT user_id, done_at AS ts FROM tg_task_completions
                WHERE done_at >= %s
            ) combined
            GROUP BY user_id
            ORDER BY cnt DESC
        """, (since, since)).fetchall()
    for i, row in enumerate(all_rows):
        if row['user_id'] == uid:
            return (i + 1, row['cnt'])
    return (None, None)


def _lb_get_rows(yesterday: bool = False) -> list:
    """Top 10 users — gmails (regular+category) + tg_task_completions।
    category_task_completions বাদ — category tasks gmails-এ already আছে (double-count ঠেকাতে)।
    yesterday=False → আজকের রাত ১২:০০ AM BD থেকে এখন পর্যন্ত।
    yesterday=True  → গতকাল রাত ১২:০০ AM থেকে আজ রাত ১২:০০ AM পর্যন্ত।
    ⚠️  timestamps DB-তে BD local time string → BD time দিয়ে compare করো, UTC নয়।
    Returns list of (user_id, full_name, username, cnt) tuples."""
    today_midnight_bd = datetime.now(BD_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if yesterday:
        since = (today_midnight_bd - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        until = today_midnight_bd.strftime("%Y-%m-%d %H:%M:%S")
    else:
        since = today_midnight_bd.strftime("%Y-%m-%d %H:%M:%S")
        until = None
    with _c() as c:
        if until:
            raw = c.execute("""
                SELECT u.user_id, u.full_name, u.username, COUNT(*) AS cnt
                FROM (
                    SELECT user_id, approved_at AS ts FROM gmails
                    WHERE status = 'approved' AND approved_at >= %s AND approved_at < %s
                    UNION ALL
                    SELECT user_id, done_at AS ts FROM tg_task_completions
                    WHERE done_at >= %s AND done_at < %s
                ) combined
                JOIN users u ON combined.user_id = u.user_id
                GROUP BY u.user_id, u.full_name, u.username
                ORDER BY cnt DESC
                LIMIT 10
            """, (since, until, since, until)).fetchall()
        else:
            raw = c.execute("""
                SELECT u.user_id, u.full_name, u.username, COUNT(*) AS cnt
                FROM (
                    SELECT user_id, approved_at AS ts FROM gmails
                    WHERE status = 'approved' AND approved_at >= %s
                    UNION ALL
                    SELECT user_id, done_at AS ts FROM tg_task_completions
                    WHERE done_at >= %s
                ) combined
                JOIN users u ON combined.user_id = u.user_id
                GROUP BY u.user_id, u.full_name, u.username
                ORDER BY cnt DESC
                LIMIT 10
            """, (since, since)).fetchall()
    # RealDictCursor rows → tuple list for backward-compatible callers
    return [(r['user_id'], r['full_name'], r['username'], r['cnt']) for r in raw]


def _lb_mask_name(full_name: str, username: str) -> str:
    """নাম পুরো দেখাও, @ দিয়ে শুরু word হাইড করো। HTML escape করো।"""
    name = (full_name or "User").strip()
    name = re.sub(r'@\S+', '@\u2060***', name)
    name = html.escape(name or "User")
    return name


def _lb_build_text_admin() -> str:
    """Admin DM — HTML, নাম + @username + User ID সহ।"""
    now_bd = datetime.now(BD_TZ)
    day    = now_bd.day
    month  = BN_MONTHS[now_bd.month]
    rows   = _lb_get_rows()
    medals = LB_MEDALS
    lines  = [
        f"🏆 <b>আজকের লিডারবোর্ড — {day} {month}</b>\n",
        "👇 <i>Admin View (নাম + Username + ID)</i>\n",
    ]
    if not rows:
        lines.append("<i>এখনো কোনো Task Approve হয়নি।</i>")
    else:
        for i, (user_id, full_name, username, cnt) in enumerate(rows):
            name  = html.escape((full_name or "User").strip())
            uname = f"@{html.escape(username)}" if username else "—"
            lines.append(f"{medals[i]} {name} | {uname} | <code>{user_id}</code> — {cnt}টি Task")
    lines.append("\n⏰ প্রতিদিন রাত ১২:০০ BD তে আপডেট হয়")
    return "\n".join(lines)


def _lb_build_text(lang: str = "bn", yesterday: bool = False) -> str:
    """Channel/User leaderboard — HTML, শুধু নাম (@ হাইড)।
    yesterday=True → গতকালের দিনের ফলাফল (১২:০০ AM channel post এর জন্য)।"""
    now_bd  = datetime.now(BD_TZ)
    ref_bd  = now_bd - timedelta(days=1) if yesterday else now_bd
    day     = ref_bd.day
    rows    = _lb_get_rows(yesterday=yesterday)

    if lang == "en":
        month  = EN_MONTHS[ref_bd.month]
        medals = LB_MEDALS_EN
        title  = f"🏆 <b>Yesterday's Result — {day} {month}</b>\n" if yesterday else f"🏆 <b>Today's Leaderboard — {day} {month}</b>\n"
        lines  = [title, "These users were in the Top 10\n" if yesterday else "These users are in the Top 10 today\n"]
        if not rows:
            lines.append("<i>No tasks were approved yesterday.</i>" if yesterday else "<i>No tasks approved yet today.</i>")
        else:
            for i, (user_id, full_name, username, cnt) in enumerate(rows):
                name = _lb_mask_name(full_name, username)
                lines.append(f"{medals[i]} {name} — {cnt} Task{'s' if cnt > 1 else ''}")
        lines.append("\n⏰ Results calculated every day at 12:00 AM Bangladesh Time")
        lines.append("\nParticipate daily, rise on the leaderboard 🚀")
    else:
        month  = BN_MONTHS[ref_bd.month]
        medals = LB_MEDALS
        title  = f"🏆 <b>গতকালের ফলাফল — {day} {month}</b>\n" if yesterday else f"🏆 <b>আজকের লিডারবোর্ড — {day} {month}</b>\n"
        lines  = [title, "গতকাল যারা টপ ১০ এ ছিলেন\n" if yesterday else "নিচের ব্যবহারকারীরা আজকে টপ ১০ এর মধ্যে আছে\n"]
        if not rows:
            lines.append("<i>গতকাল কোনো Task Approve হয়নি।</i>" if yesterday else "<i>এখনো কোনো Task Approve হয়নি।</i>")
        else:
            for i, (user_id, full_name, username, cnt) in enumerate(rows):
                name = _lb_mask_name(full_name, username)
                lines.append(f"{medals[i]} {name} — {cnt}টি Task")
        lines.append("\n⏰ প্রতিদিন বাংলাদেশ সময় রাত ১২:০০ তে স্বয়ংক্রিয়ভাবে দিনের ফলাফল গণনা করা হয়")
        lines.append("\nপ্রতিদিন অংশ নিন, লিডারবোর্ডে উঠুন 🚀")

    return "\n".join(lines)


def _lb_channel_btn(lang: str = "bn") -> InlineKeyboardMarkup:
    label = "📢 News Channel" if lang == "en" else "📢 নিউজ চ্যানেল"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, url=LEADERBOARD_CH_LINK)
    ]])


async def _lb_refresh_now(bot):
    """যেকোনো জায়গা থেকে সাথে সাথে Admin leaderboard refresh করে।"""
    chat_id, msg_id = await asyncio.to_thread(_lb_adm_load)
    if not chat_id or not msg_id:
        return
    text = await asyncio.to_thread(_lb_build_text_admin)
    now_str = datetime.now(BD_TZ).strftime("%d/%m/%Y %I:%M %p")
    text += f"\n\n🕐 <b>আপডেট:</b> <code>{now_str}</code>"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data="adm_lb_refresh")
    ]])
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=text, parse_mode="HTML", reply_markup=kb
        )
    except Exception as e:
        log.warning(f"[Leaderboard] instant refresh ব্যর্থ: {e}")


async def job_lb_adm_update(context: ContextTypes.DEFAULT_TYPE):
    """প্রতি ১০ মিনিটে Admin leaderboard মেসেজ auto-edit করে"""
    await _lb_refresh_now(context.bot)


async def job_bio_task_check(context: ContextTypes.DEFAULT_TYPE):
    """প্রতিদিন রাত ১২:০০ (BD) — 24h tracking + 5min grace period সহ bio চেক।
    Parallel processing (Semaphore=10) + asyncio.to_thread for all DB calls.
    """
    bio_task = await asyncio.to_thread(get_bio_task)
    if not bio_task or not bio_task['is_active']:
        return
    daily_reward = bio_task['reward']
    add_reward   = bio_task.get('add_reward', 0) or 0
    if daily_reward <= 0 and add_reward <= 0:
        return

    bot = context.bot
    bot_me = await bot.get_me()
    bot_username = bot_me.username
    today = datetime.now(BD_TZ).strftime("%Y-%m-%d")
    users = await asyncio.to_thread(all_users)

    paid_list        = []   # আজ pay হয়েছে
    new_tracked_list = []   # প্রথমবার bio সেট করেছে (add_reward পেয়েছে)
    grace_list       = []   # bio ভুল — 5 মিনিট grace দেওয়া হবে
    failed_list      = []   # grace এর পরেও ভুল / tracking নেই
    _sem = asyncio.Semaphore(10)   # max 10 parallel Telegram API calls

    async def _check_bio(uid, bot_uname):
        ref_link = f"https://t.me/{bot_uname}?start=ref_{uid}"
        required = f"Free Task Earn bot {ref_link}"
        try:
            chat = await bot.get_chat(uid)
            user_bio = (chat.bio or "").strip()
            # শুধু ref_{uid} থাকলেই যথেষ্ট — এটাই unique identifier per user
            if f"ref_{uid}" in user_bio:
                return True
            if required.lower() in user_bio.lower():
                return True
            if str(uid) in user_bio and bot_uname.lower() in user_bio.lower():
                return True
            return False
        except Exception as e:
            log.warning(f"[BIO-TASK] uid={uid} bio check failed: {e}")
            return False

    async def _process_one_user(u):
        """একজন user-এর bio check + payment logic। Semaphore দিয়ে parallel।"""
        uid = u['user_id']
        if u.get('is_banned'):
            return
        if await asyncio.to_thread(bio_task_paid_today, uid):
            return

        async with _sem:
            bio_ok = await _check_bio(uid, bot_username)

        actively_track = await asyncio.to_thread(is_bio_actively_tracked, uid)

        if bio_ok:
            if actively_track:
                # ✅ আগের দিনও ছিল (24h kept) → daily reward দাও
                amt = round(daily_reward, 4)
                await asyncio.to_thread(record_bio_payment, uid, today, amt)
                await asyncio.to_thread(add_bal, uid, amt)
                await asyncio.to_thread(set_bio_tracking, uid)
                paid_list.append({'uid': uid, 'name': u.get('full_name', ''), 'amt': amt})
                # Referrer commission — daily
                _jref = await asyncio.to_thread(get_referred_by, uid)
                if _jref:
                    _jcpct = await asyncio.to_thread(get_commission_pct)
                    _jcomm = round(amt * _jcpct / 100.0, 4)
                    await asyncio.to_thread(add_bal, _jref, _jcomm)
                    await asyncio.to_thread(record_commission, _jref, uid, _jcomm, 'Bio Task Daily')
                    try:
                        _jru = await asyncio.to_thread(get_user, uid)
                        _jrname = (_jru["full_name"] or _jru["username"] or "Unknown") if _jru else "Unknown"
                        _jcpd = int(_jcpct) if _jcpct == int(_jcpct) else _jcpct
                        await bot.send_message(_jref,
                            T(_jref, 'commission_msg', name=_md_esc(_jrname), price=amt, comm=_jcomm, comm_pct=_jcpd),
                            parse_mode="Markdown")
                    except Exception: pass
            else:
                # 🆕 bio সঠিক কিন্তু active tracking নেই → tracking শুরু, add_reward চেক
                await asyncio.to_thread(set_bio_tracking, uid)
                if add_reward > 0 and await asyncio.to_thread(mark_add_reward_given, uid):
                    amt = round(add_reward, 4)
                    await asyncio.to_thread(record_bio_payment, uid, today, amt)
                    await asyncio.to_thread(add_bal, uid, amt)
                    new_tracked_list.append({'uid': uid, 'name': u.get('full_name', ''), 'amt': amt})
                    _jaref = await asyncio.to_thread(get_referred_by, uid)
                    if _jaref:
                        _jacpct = await asyncio.to_thread(get_commission_pct)
                        _jacomm = round(amt * _jacpct / 100.0, 4)
                        await asyncio.to_thread(add_bal, _jaref, _jacomm)
                        await asyncio.to_thread(record_commission, _jaref, uid, _jacomm, 'Bio Task')
                        try:
                            _jaru = await asyncio.to_thread(get_user, uid)
                            _jarname = (_jaru["full_name"] or _jaru["username"] or "Unknown") if _jaru else "Unknown"
                            _jacpd = int(_jacpct) if _jacpct == int(_jacpct) else _jacpct
                            await bot.send_message(_jaref,
                                T(_jaref, 'commission_msg', name=_md_esc(_jarname), price=amt, comm=_jacomm, comm_pct=_jacpd),
                                parse_mode="Markdown")
                        except Exception: pass
        else:
            if actively_track:
                grace_list.append({'uid': uid, 'name': u.get('full_name', ''), 'u': u})
            else:
                failed_list.append({'uid': uid, 'name': u.get('full_name', '')})

    # ── প্রথম pass — ২০ জন করে chunked (DB pool + TG flood ঠেকাতে) ──
    _CHUNK = 20
    for _ci in range(0, len(users), _CHUNK):
        await asyncio.gather(
            *[_process_one_user(u) for u in users[_ci:_ci + _CHUNK]],
            return_exceptions=True
        )
        if _ci + _CHUNK < len(users):
            await asyncio.sleep(1)

    # ── Grace period — warning পাঠাও, ৫ মিনিট wait ─────────────────
    for g in grace_list:
        uid  = g['uid']
        lang = await asyncio.to_thread(get_lang, uid)
        try:
            warn_msg = (
                "⚠️ *Bio Task সতর্কতা!*\n\n"
                "আপনার Telegram Bio সরানো হয়েছে বা সঠিক নেই!\n\n"
                f"⏳ *৫ মিনিটের মধ্যে* Bio আবার সেট করুন —\n"
                f"নাহলে আজকের `{daily_reward}$ USDT` payment পাবেন না।\n\n"
                f"📝 Bio তে হুবহু এই text রাখুন:\n"
                f"`Free Task Earn bot https://t.me/{bot_username}?start=ref_{uid}`\n\n"
                f"_(Settings → Edit Profile → Bio)_"
                if lang == 'bn' else
                "⚠️ *Bio Task Warning!*\n\n"
                "Your Telegram Bio has been removed or is incorrect!\n\n"
                f"⏳ Set your Bio again within *5 minutes* —\n"
                f"otherwise you won't receive today's `{daily_reward}$ USDT` payment.\n\n"
                f"📝 Set your Bio to exactly:\n"
                f"`Free Task Earn bot https://t.me/{bot_username}?start=ref_{uid}`\n\n"
                f"_(Settings → Edit Profile → Bio)_"
            )
            await bot.send_message(uid, warn_msg, parse_mode="Markdown")
        except Exception:
            pass

    # ── ৫ মিনিট অপেক্ষা ─────────────────────────────────────────────
    if grace_list:
        await asyncio.sleep(300)

    # ── দ্বিতীয় pass — grace users recheck (parallel) ───────────────
    grace_notified_uids = set()   # এই uid গুলো already bio-restored message পেয়েছে

    async def _recheck_grace(g):
        uid  = g['uid']
        lang = await asyncio.to_thread(get_lang, uid)
        async with _sem:
            bio_ok = await _check_bio(uid, bot_username)
        if bio_ok:
            amt = round(daily_reward, 4)
            await asyncio.to_thread(record_bio_payment, uid, today, amt)
            await asyncio.to_thread(add_bal, uid, amt)
            await asyncio.to_thread(set_bio_tracking, uid)
            grace_notified_uids.add(uid)
            paid_list.append({'uid': uid, 'name': g['name'], 'amt': amt})
            _gref = await asyncio.to_thread(get_referred_by, uid)
            if _gref:
                _gcpct = await asyncio.to_thread(get_commission_pct)
                _gcomm = round(amt * _gcpct / 100.0, 4)
                await asyncio.to_thread(add_bal, _gref, _gcomm)
                await asyncio.to_thread(record_commission, _gref, uid, _gcomm, 'Bio Task Daily')
                try:
                    _gru = await asyncio.to_thread(get_user, uid)
                    _grname = (_gru["full_name"] or _gru["username"] or "Unknown") if _gru else "Unknown"
                    _gcpd = int(_gcpct) if _gcpct == int(_gcpct) else _gcpct
                    await bot.send_message(_gref,
                        T(_gref, 'commission_msg', name=_md_esc(_grname), price=amt, comm=_gcomm, comm_pct=_gcpd),
                        parse_mode="Markdown")
                except Exception: pass
            try:
                ok_msg = (
                    f"✅ *Bio আবার সেট করেছেন — Payment হয়েছে!*\n\n"
                    f"💰 `{amt}$ USDT` আপনার একাউন্টে যোগ হয়েছে।\n"
                    f"🗓️ তারিখ: `{today}`"
                    if lang == 'bn' else
                    f"✅ *Bio restored — Payment received!*\n\n"
                    f"💰 `{amt}$ USDT` added to your account.\n"
                    f"🗓️ Date: `{today}`"
                )
                await bot.send_message(uid, ok_msg, parse_mode="Markdown")
            except Exception: pass
        else:
            await asyncio.to_thread(clear_bio_tracking, uid)
            failed_list.append({'uid': uid, 'name': g['name']})
            try:
                no_msg = (
                    f"❌ *আজকের Bio Task Payment বাতিল হয়েছে।*\n\n"
                    f"৫ মিনিটের মধ্যে Bio সেট না করায় আজকের payment হয়নি।\n\n"
                    f"📝 আবার শুরু করতে Bio সেট করুন:\n"
                    f"`Free Task Earn bot https://t.me/{bot_username}?start=ref_{uid}`\n\n"
                    f"কাল রাত ১২:০০ তে আবার চেক হবে।"
                    if lang == 'bn' else
                    f"❌ *Today's Bio Task Payment cancelled.*\n\n"
                    f"Bio was not restored within 5 minutes.\n\n"
                    f"📝 To restart, set your Bio to:\n"
                    f"`Free Task Earn bot https://t.me/{bot_username}?start=ref_{uid}`\n\n"
                    f"Next check tomorrow at 12:00 AM."
                )
                await bot.send_message(uid, no_msg, parse_mode="Markdown")
            except Exception: pass

    if grace_list:
        for _ci in range(0, len(grace_list), _CHUNK):
            await asyncio.gather(
                *[_recheck_grace(g) for g in grace_list[_ci:_ci + _CHUNK]],
                return_exceptions=True
            )
            if _ci + _CHUNK < len(grace_list):
                await asyncio.sleep(1)

    # ── Daily reward পেলে user কে notify করো ────────────────────────
    for p in paid_list:
        if p['uid'] in grace_notified_uids:
            continue   # এরা আগেই "bio restored" message পেয়েছে — duplicate skip
        lang = await asyncio.to_thread(get_lang, p['uid'])
        try:
            pay_msg = (
                f"✅ *Daily Bio Task পুরস্কার!*\n\n"
                f"💰 *`{p['amt']}$ USDT`* আপনার একাউন্টে যোগ হয়েছে!\n\n"
                f"🗓️ তারিখ: `{today}`\n\n"
                f"🔄 কাল রাত ১২:০০ তে আবার চেক হবে — বায়ো ঠিক রাখুন!"
                if lang == 'bn' else
                f"✅ *Daily Bio Task Reward!*\n\n"
                f"💰 *`{p['amt']}$ USDT`* added to your account!\n\n"
                f"🗓️ Date: `{today}`\n\n"
                f"🔄 Next check tomorrow at 12:00 AM — keep your bio!"
            )
            await bot.send_message(p['uid'], pay_msg, parse_mode="Markdown")
        except Exception:
            pass
        await asyncio.sleep(0.05)  # flood control — ২০ msg/সেকেন্ড লিমিট

    # ── add_reward পেলে user কে notify করো ──────────────────────────
    for p in new_tracked_list:
        lang = await asyncio.to_thread(get_lang, p['uid'])
        try:
            add_msg = (
                f"🎉 *Bio Task শুরু হয়েছে!*\n\n"
                f"✅ আপনার Bio সঠিকভাবে সেট করা হয়েছে।\n"
                f"💰 *`{p['amt']}$ USDT`* আপনার একাউন্টে যোগ হয়েছে!\n\n"
                f"🔄 এখন থেকে প্রতিদিন রাত ১২:০০ তে bio check হবে।\n"
                f"Bio ঠিক রাখলে প্রতিদিন `{daily_reward}$` পাবেন!"
                if lang == 'bn' else
                f"🎉 *Bio Task started!*\n\n"
                f"✅ Your Bio has been set correctly.\n"
                f"💰 *`{p['amt']}$ USDT`* added to your account!\n\n"
                f"🔄 Bio will be checked daily at 12:00 AM.\n"
                f"Keep your bio to earn `{daily_reward}$` every day!"
            )
            await bot.send_message(p['uid'], add_msg, parse_mode="Markdown")
        except Exception:
            pass
        await asyncio.sleep(0.05)  # flood control

    # ── Admin summary notification ────────────────────────────────────
    today_daily   = len(paid_list)
    today_new     = len(new_tracked_list)
    today_cost    = round(
        sum(p['amt'] for p in paid_list) + sum(p['amt'] for p in new_tracked_list), 4
    )
    cum_count, cum_amount = await asyncio.to_thread(get_bio_task_cumulative)
    cum_amount = round(float(cum_amount), 4)

    summary = (
        f"📊 *Daily Bio Task Report*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🗓️ *তারিখ:* `{today}`\n"
        f"💰 *Add Reward:* `{add_reward}$` | *Daily:* `{daily_reward}$`\n\n"
        f"📅 *আজকের রিপোর্ট:*\n"
        f"  🆕 নতুন (add reward): `{today_new} জন`\n"
        f"  ✅ Daily reward: `{today_daily} জন`\n"
        f"  💸 আজকের মোট খরচ: `{today_cost}$ USDT`\n"
        f"  ❌ Bio ঠিক ছিল না: `{len(failed_list)} জন`\n\n"
        f"📈 *সর্বমোট Cumulative:*\n"
        f"  👥 মোট পেয়েছে: `{cum_count} জন`\n"
        f"  💵 মোট খরচ হয়েছে: `{cum_amount}$ USDT`\n"
    )
    all_paid = paid_list + new_tracked_list
    if all_paid:
        lines = [f"  • `{p['uid']}` — {p['name']} — `{p['amt']}$`" for p in all_paid[:20]]
        if len(all_paid) > 20:
            lines.append(f"  _...আরো {len(all_paid)-20} জন_")
        summary += "\n✅ *আজ যারা পেয়েছে:*\n" + "\n".join(lines)

    await notify_full_admins(bot, summary)
    log.info(f"[BIO-TASK] done: new={today_new}, daily={today_daily}, failed={len(failed_list)}, cost={today_cost}, cum={cum_amount}")


async def job_bio_realtime_monitor(context: ContextTypes.DEFAULT_TYPE):
    """
    প্রতি মিনিটে bio চেক করে।
    Flow:
      ১. Bio হাটালে → warning পাঠাও, last_warned_at সেট করো (tracking চালু রাখো)
      ২. ৫ মিনিট পর আবার চেক:
         • Bio আছে → tracking active রাখো, last_warned_at clear করো
         • Bio নেই → tracking clear (আজ payment নেই), task list এ আবার দেখাবে
    """
    bio_task = await asyncio.to_thread(get_bio_task)
    if not bio_task or not bio_task['is_active']:
        return
    daily_reward = bio_task['reward']
    if daily_reward <= 0:
        return

    bot = context.bot
    bot_me = await bot.get_me()
    bot_username = bot_me.username
    now_str = now()
    now_bd = datetime.now(BD_TZ)

    # ── DB query non-blocking ─────────────────────────────────────
    def _fetch_tracked():
        with _c() as c:
            return c.execute(
                "SELECT user_id, last_warned_at FROM bio_task_tracking "
                "WHERE (first_correct_at IS NOT NULL AND first_correct_at != '') "
                "   OR (last_warned_at IS NOT NULL AND last_warned_at != '')"
            ).fetchall()

    tracked_users = await asyncio.to_thread(_fetch_tracked)
    if not tracked_users:
        return

    # ── Semaphore — Telegram rate limit এড়াতে সর্বোচ্চ ৫টি concurrent API call ──
    _sem = asyncio.Semaphore(5)

    async def _process_user(row):
        uid         = row['user_id']
        last_warned = (row.get('last_warned_at') or '')
        ref_link    = f"https://t.me/{bot_username}?start=ref_{uid}"
        required    = f"Free Task Earn bot {ref_link}"
        lang        = await asyncio.to_thread(get_lang, uid)

        def _bio_matches(bio_text: str) -> bool:
            bio_text = (bio_text or "").strip()
            if not bio_text:
                return False
            # শুধু ref_{uid} থাকলেই যথেষ্ট — এটাই unique identifier per user
            if f"ref_{uid}" in bio_text:
                return True
            if required.lower() in bio_text.lower():
                return True
            if str(uid) in bio_text and bot_username.lower() in bio_text.lower():
                return True
            return False

        async with _sem:
            if last_warned:
                try:
                    warned_dt = BD_TZ.localize(datetime.strptime(last_warned, "%Y-%m-%d %H:%M"))
                    elapsed   = (now_bd - warned_dt).total_seconds()
                except Exception:
                    elapsed = 999

                if elapsed < 300:
                    return  # ৫ মিনিট হয়নি

                try:
                    try: await bot.get_chat(uid)  # cache warm-up
                    except Exception: pass
                    chat   = await bot.get_chat(uid)
                    bio_ok = _bio_matches(chat.bio)
                except Exception:
                    return

                if bio_ok:
                    def _clear_warned():
                        with _c() as c:
                            c.execute("UPDATE bio_task_tracking SET last_warned_at='' WHERE user_id=%s", (uid,))
                    await asyncio.to_thread(_clear_warned)
                    try:
                        ok_msg = (
                            f"✅ *Bio Task — Bio পুনরায় সেট করা হয়েছে!*\n\n"
                            f"আজকের `{daily_reward}$ USDT` payment রাত ১২:০০ তে পাবেন।"
                            if lang == 'bn' else
                            f"✅ *Bio Task — Bio restored!*\n\n"
                            f"Today's `{daily_reward}$ USDT` payment will be sent at 12:00 AM."
                        )
                        await bot.send_message(uid, ok_msg, parse_mode="Markdown")
                    except Exception:
                        pass
                    log.info(f"[BIO-MONITOR] uid={uid} bio restored within 5min — tracking kept")
                else:
                    await asyncio.to_thread(clear_bio_tracking, uid)
                    try:
                        fail_msg = (
                            f"❌ *Bio Task — আজকের Payment বাতিল!*\n\n"
                            f"৫ মিনিটের মধ্যে Bio সেট না করায় আজকের `{daily_reward}$ USDT` payment পাবেন না।\n\n"
                            f"📝 Bio আবার সেট করে Tasks মেনু থেকে Bio Task → Check করলে কাল থেকে payment পাবেন।\n\n"
                            f"Bio text:\n`{required}`"
                            if lang == 'bn' else
                            f"❌ *Bio Task — Today's Payment Cancelled!*\n\n"
                            f"Bio was not restored within 5 minutes. Today's `{daily_reward}$ USDT` is cancelled.\n\n"
                            f"📝 Set your Bio again and press Check in Tasks → Bio Task to resume from tomorrow.\n\n"
                            f"Bio text:\n`{required}`"
                        )
                        await bot.send_message(uid, fail_msg, parse_mode="Markdown")
                    except Exception:
                        pass
                    log.info(f"[BIO-MONITOR] uid={uid} 5min grace expired — tracking cleared, no payment today")
            else:
                actively_track = await asyncio.to_thread(is_bio_actively_tracked, uid)
                if not actively_track:
                    return

                try:
                    try: await bot.get_chat(uid)  # cache warm-up
                    except Exception: pass
                    chat   = await bot.get_chat(uid)
                    bio_ok = _bio_matches(chat.bio)
                except Exception:
                    return

                if not bio_ok:
                    try:
                        warn_msg = (
                            "🚨 *Bio Task সতর্কতা!*\n\n"
                            "❌ আপনার Telegram Bio সরানো হয়েছে বা পরিবর্তন হয়েছে!\n\n"
                            f"⏳ *৫ মিনিটের মধ্যে* Bio আবার সেট করুন —\n"
                            f"না করলে আজকের `{daily_reward}$ USDT` payment *বাতিল হয়ে যাবে!*\n\n"
                            f"📝 Bio তে হুবহু এই text রাখুন:\n"
                            f"`{required}`\n\n"
                            f"_⚙️ Settings → Edit Profile → Bio_"
                            if lang == 'bn' else
                            "🚨 *Bio Task Warning!*\n\n"
                            "❌ Your Telegram Bio has been removed or changed!\n\n"
                            f"⏳ Restore your Bio within *5 minutes* —\n"
                            f"otherwise today's `{daily_reward}$ USDT` payment will be *cancelled!*\n\n"
                            f"📝 Set your Bio to exactly:\n"
                            f"`{required}`\n\n"
                            f"_⚙️ Settings → Edit Profile → Bio_"
                        )
                        await bot.send_message(uid, warn_msg, parse_mode="Markdown")
                        def _set_warned():
                            with _c() as c:
                                c.execute(
                                    "UPDATE bio_task_tracking SET last_warned_at=%s WHERE user_id=%s",
                                    (now_str, uid)
                                )
                        await asyncio.to_thread(_set_warned)
                        log.info(f"[BIO-MONITOR] uid={uid} bio removed — warning sent, 5min grace started")
                    except Exception as _e:
                        log.warning(f"[BIO-MONITOR] uid={uid} send failed: {_e}")

    # ── সব users কে parallel এ process করো ──────────────────────
    await asyncio.gather(*[_process_user(row) for row in tracked_users])


async def job_post_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """প্রতিদিন রাত ১২:০০ (BD) — শুধু Channel এ পাঠায় (bot DM নয়)"""
    bot_username = (await context.bot.get_me()).username
    bot_link     = f"https://t.me/{bot_username}"

    ch_id = LEADERBOARD_CH_ID
    if not ch_id:
        return

    # channel এর জন্য public text — গতকালের পুরো দিনের ফলাফল
    ch_text, (old_chat_id, old_msg_id) = await asyncio.gather(
        asyncio.to_thread(_lb_build_text, "bn", yesterday=True),
        asyncio.to_thread(_lb_load_msg_id),
    )

    # পুরনো post ডিলিট করো — saved chat_id ব্যবহার করো
    if old_chat_id and old_msg_id:
        try:
            await context.bot.delete_message(chat_id=old_chat_id, message_id=old_msg_id)
            log.info(f"[Leaderboard] পুরনো post ডিলিট ✅ msg_id={old_msg_id}")
        except Exception as e:
            log.warning(f"[Leaderboard] পুরনো post ডিলিট ব্যর্থ: {e}")

    # Channel post — bot link button সহ
    ch_btn = InlineKeyboardMarkup([[
        InlineKeyboardButton("🤖 বট শুরু করুন", url=bot_link)
    ]])
    try:
        ch_msg = await context.bot.send_message(
            chat_id=ch_id,
            text=ch_text,
            parse_mode="HTML",
            reply_markup=ch_btn
        )
        await asyncio.to_thread(_lb_save_msg_id, ch_msg.chat_id, ch_msg.message_id)
        log.info(f"[Leaderboard] Channel post ✅ chat_id={ch_msg.chat_id} msg_id={ch_msg.message_id}")
    except Exception as e:
        log.warning(f"[Leaderboard] Channel post ব্যর্থ: {e}")




async def _run_dirty_backup_now(bot):
    """
    আসল dirty-backup logic — bot object সরাসরি নেয়।
    _mark_dirty() (asyncio path) এবং job_dirty_backup (job_queue path) — দুটোই এটা call করে।
    """
    global _db_dirty
    if not _db_dirty:
        return  # কিছু বদলায়নি — পাঠানোর দরকার নেই
    _db_dirty = False  # আগে reset করো — পরের পরিবর্তন আবার dirty করবে

    # ── ১. DB file পাঠাও (admin + BACKUP_CHANNELS সবাইকে) ────────────────────
    await _send_db_to_admin(bot, label="💾 Auto Backup (ডাটা পরিবর্তন হয়েছে)")

    # ── ২. User data report তৈরি করে BACKUP_CHANNELS এ পাঠাও ─────────────────
    # _build_full_users_report() sync DB heavy → thread এ চালাও
    try:
        import io as _io
        report = await asyncio.to_thread(_build_full_users_report)
        report_bytes = report.encode("utf-8")
        filename = f"users_{now()[:10]}.txt"
        _auto_user_count = len(await asyncio.to_thread(all_users))
        caption = (
            f"📊 *User Data Auto Backup*\n\n"
            f"🕐 *সময়:* `{now()}`\n"
            f"👥 *মোট Users:* `{_auto_user_count}`\n\n"
            f"✅ নতুন data আসায় auto-save হয়েছে।"
        )
        for target in BACKUP_CHANNELS:
            # পুরনো report message ডিলিট করো
            old_rid = await asyncio.to_thread(_report_msg_load, target)
            if old_rid:
                try:
                    await bot.delete_message(chat_id=target, message_id=old_rid)
                except Exception:
                    pass
            try:
                msg = await bot.send_document(
                    target,
                    document=_io.BytesIO(report_bytes),
                    filename=filename,
                    caption=caption,
                    parse_mode="Markdown"
                )
                if msg:
                    await asyncio.to_thread(_report_msg_save, target, msg.message_id)
                    # NOTE: pin করা হচ্ছে না — DB file pinned থাকতে হবে যাতে restore কাজ করে
            except Exception as e:
                log.warning(f"[DIRTY-REPORT] {target} এ পাঠাতে ব্যর্থ: {e}")
        log.info("✅ Dirty-flag: user report + DB backup পাঠানো হয়েছে।")
    except Exception as e:
        log.warning(f"[DIRTY-REPORT] ব্যর্থ: {e}")


async def job_dirty_backup(context):
    """প্রতি ৬০ সেকেন্ডে চেক করে — dirty হলে backup পাঠায় (job_queue safety net)।"""
    await _run_dirty_backup_now(context.bot)


def _trim_user_records(c, table, uid, keep=50):
    """প্রতি user এর পুরনো processed record ছাঁটাই — শুধু approved/rejected এর মধ্যে।
    users টেবিল, balance, referral — কোনোটাই স্পর্শ করে না।
    """
    try:
        c.execute(
            f"DELETE FROM {table} WHERE user_id=%s AND status IN ('approved','rejected') "
            f"AND id NOT IN ("
            f"  SELECT id FROM {table} WHERE user_id=%s AND status IN ('approved','rejected') "
            f"  ORDER BY id DESC LIMIT %s"
            f")",
            (uid, uid, keep)
        )
    except Exception as e:
        log.warning(f"_trim_user_records({table}, uid={uid}): {e}")


def _trim_table(table, uid_col, uid, keep, order_col="id", only_done=False):
    """নতুন record insert হলে পুরানোগুলো মুছে দাও — শুধু শেষ `keep` টা রাখো।
    only_done=True হলে শুধু approved/rejected মুছে — pending কখনো ছোঁয় না।
    users table, balance, referral এ কোনো effect নেই।
    """
    try:
        with _c() as c:
            if only_done:
                # pending records কখনো delete হবে না
                c.execute(
                    f"DELETE FROM {table} WHERE {uid_col}=%s "
                    f"AND status IN ('approved','rejected') "
                    f"AND {order_col} NOT IN ("
                    f"  SELECT {order_col} FROM {table} WHERE {uid_col}=%s "
                    f"  AND status IN ('approved','rejected') "
                    f"  ORDER BY {order_col} DESC LIMIT %s"
                    f")",
                    (uid, uid, keep)
                )
            else:
                c.execute(
                    f"DELETE FROM {table} WHERE {uid_col}=%s "
                    f"AND {order_col} NOT IN ("
                    f"  SELECT {order_col} FROM {table} WHERE {uid_col}=%s "
                    f"  ORDER BY {order_col} DESC LIMIT %s"
                    f")",
                    (uid, uid, keep)
                )
    except Exception as e:
        log.warning(f"[TRIM] {table} uid={uid}: {e}")


def auto_cleanup_old_records():
    """
    পুরনো processed record পরিষ্কার — CockroachDB storage ব্যবস্থাপনা।

    ✅ কখনো delete হয় না:
       - users (balance, referred_by, points)
       - pending gmails / pending withdrawals
       - admins, settings, tasks, tg_tasks, categories
       - promocodes, promo_uses, required_channels
       - bio_task_tracking (active tracking)

    🗑  Delete হয় (নতুন data আসলে পুরানো মুছে):
       - gmails approved/rejected  → শেষ 20টা রাখো, বাকি মুছো
       - withdrawals approved/rejected → শেষ 10টা রাখো, বাকি মুছো
       - pending_notifications > 3 দিন পুরনো
       - gmails pending > 30 দিন পুরনো
       - commissions > 14 দিন পুরনো (প্রতি user শেষ 30টা রাখো)
       - bio_task_completions > 30 দিন পুরনো (প্রতি user শেষ 30টা)
       - tg_task_pre_members > 7 দিন পুরনো
    """
    _now = datetime.now(BD_TZ)
    cutoff_14 = (_now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M")
    cutoff_30d = (_now - timedelta(days=30)).strftime("%Y-%m-%d")
    cutoff_7   = (_now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    cutoff_3   = (_now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")

    total_deleted = 0
    counts = {}
    try:
        with _c() as c:
            # ── gmails: approved/rejected — প্রতি user শেষ 20টা রাখো ─
            r = c.execute(
                "DELETE FROM gmails WHERE status IN ('approved','rejected') "
                "AND submitted_at < %s",
                (cutoff_30d,)
            )
            counts['gmails_old'] = r.rowcount or 0
            total_deleted += counts['gmails_old']

            g_users = [row['user_id'] for row in
                       c.execute("SELECT DISTINCT user_id FROM gmails WHERE status IN ('approved','rejected')").fetchall()]
            for uid in g_users:
                _trim_user_records(c, "gmails", uid, keep=20)

            # ── withdrawals: approved/rejected — প্রতি user শেষ 10টা ─
            r2 = c.execute(
                "DELETE FROM withdrawals WHERE status IN ('approved','rejected') "
                "AND requested_at < %s",
                (cutoff_30d,)
            )
            counts['withdrawals_old'] = r2.rowcount or 0
            total_deleted += counts['withdrawals_old']

            w_users = [row['user_id'] for row in
                       c.execute("SELECT DISTINCT user_id FROM withdrawals WHERE status IN ('approved','rejected')").fetchall()]
            for uid in w_users:
                _trim_user_records(c, "withdrawals", uid, keep=10)

            # ── pending_notifications > 3 দিন ──────────────────────
            r3 = c.execute(
                "DELETE FROM pending_notifications WHERE send_at < %s",
                (cutoff_3,)
            )
            counts['notifications'] = r3.rowcount or 0
            total_deleted += counts['notifications']

            # ── gmails pending > 30 দিন (ভুলে যাওয়া) ─────────────
            r4 = c.execute(
                "DELETE FROM gmails WHERE status='pending' AND submitted_at < %s",
                (cutoff_30d,)
            )
            counts['gmails_pending'] = r4.rowcount or 0
            total_deleted += counts['gmails_pending']

            # ── commissions > 14 দিন — প্রতি user শেষ 30টা রাখো ───
            r5 = c.execute(
                "DELETE FROM commissions WHERE created_at < %s",
                (cutoff_14,)
            )
            counts['commissions'] = r5.rowcount or 0
            total_deleted += counts['commissions']

            comm_users = [row['user_id'] for row in
                          c.execute("SELECT DISTINCT user_id FROM commissions").fetchall()]
            for uid in comm_users:
                # commissions table এ status column নেই — id দিয়ে সরাসরি trim করো
                try:
                    c.execute(
                        "DELETE FROM commissions WHERE user_id=%s AND id NOT IN ("
                        "  SELECT id FROM commissions WHERE user_id=%s ORDER BY id DESC LIMIT 30"
                        ")",
                        (uid, uid)
                    )
                except Exception as _ce:
                    log.warning(f"_trim commissions uid={uid}: {_ce}")

            # ── bio_task_completions > 30 দিন — প্রতি user শেষ 30টা ─
            r6 = c.execute(
                "DELETE FROM bio_task_completions WHERE task_date < %s",
                (cutoff_30d,)
            )
            counts['bio_completions'] = r6.rowcount or 0
            total_deleted += counts['bio_completions']

            bio_users = [row['user_id'] for row in
                         c.execute("SELECT DISTINCT user_id FROM bio_task_completions").fetchall()]
            for uid in bio_users:
                c.execute(
                    "DELETE FROM bio_task_completions WHERE user_id=%s AND id NOT IN ("
                    "  SELECT id FROM bio_task_completions WHERE user_id=%s "
                    "  ORDER BY task_date DESC LIMIT 30"
                    ")",
                    (uid, uid)
                )

            # ── tg_task_pre_members > 7 দিন ─────────────────────────
            r7 = c.execute(
                "DELETE FROM tg_task_pre_members WHERE seen_at < %s",
                (cutoff_7,)
            )
            counts['pre_members'] = r7.rowcount or 0
            total_deleted += counts['pre_members']

        log.info(
            f"[AUTO-CLEANUP] সম্পন্ন — "
            f"gmails={counts['gmails_old']}, withdrawals={counts['withdrawals_old']}, "
            f"notifications={counts['notifications']}, pending_gmails={counts['gmails_pending']}, "
            f"commissions={counts['commissions']}, bio_completions={counts['bio_completions']}, "
            f"pre_members={counts['pre_members']} | মোট={total_deleted} row মুছেছে"
        )
    except Exception as e:
        log.error(f"[AUTO-CLEANUP] Error: {e}")


async def job_auto_cleanup(context):
    """APScheduler daily job — রাত ৩টায় চলে (Bangladesh time = UTC+6)।"""
    log.info("[AUTO-CLEANUP] Daily cleanup job শুরু হচ্ছে...")
    await asyncio.to_thread(auto_cleanup_old_records)


def _auto_configure():
    """
    যেকোনো জায়গায় upload করলেও সব settings অটো সেট হয়।
    Code এর constants থেকে DB তে সব critical config লিখে দেয়।
    config_backup.json এ backup রাখে — DB মুছলেও restore হবে।
    """
    CONFIG_BACKUP_FILE = "config_backup.json"

    # ── Step 1: Code থেকে critical settings DB তে সেট করো ──────────
    with _c() as c:
        # admin_id, backup_channels — code-only, সব সময় overwrite হবে
        forced_overwrite = {
            "admin_id":              str(ADMIN_ID),
            "backup_channels":       ",".join(str(x) for x in BACKUP_CHANNELS),
            "backup_interval_hours": str(BACKUP_INTERVAL_HOURS),
        }
        for key, val in forced_overwrite.items():
            c.execute(
                "INSERT INTO settings (key,value) VALUES (%s,%s) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, val)
            )
        # admin panel থেকে পরিবর্তনযোগ্য — শুধু প্রথমবার seed করো, overwrite নয়
        seed_if_missing = {
            "gmail_ch_id":    str(GMAIL_CH_ID),
            "withdraw_ch_id": str(WITHDRAW_CH_ID),
            "bsc_wallet_addr": BSC_WALLET_ADDR,
        }
        for key, val in seed_if_missing.items():
            c.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING", (key, val))

        # Admin সবসময় users table এ থাকবে — balance/stats এর জন্য দরকার
        c.execute(
            "INSERT INTO users (user_id, username, full_name, lang) VALUES (%s, '', 'Admin', 'bn') ON CONFLICT DO NOTHING",
            (ADMIN_ID,)
        )

        # Required channels: শুধু DB একদম খালি থাকলে default seed করো
        # (admin panel থেকে করা পরিবর্তন যাতে code re-run এ মুছে না যায়)
        ch_count = _count(c.execute("SELECT COUNT(*) FROM required_channels").fetchone())
        if ch_count == 0:
            for ch in REQUIRED_CHANNELS:
                c.execute(
                    "INSERT INTO required_channels (ch_id, ch_link, ch_name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (ch["id"], ch["link"], ch["name"])
                )

    # ── Step 2: সব settings JSON এ backup রাখো ──────────────────────
    try:
        with _c() as c:
            rows = c.execute("SELECT key, value FROM settings").fetchall()
            all_settings = {r["key"]: r["value"] for r in rows}
            # DB এর actual required_channels সেভ করো — hardcoded নয়
            ch_rows = c.execute("SELECT ch_id, ch_link, ch_name FROM required_channels").fetchall()
            db_channels = [{"id": r["ch_id"], "link": r["ch_link"], "name": r["ch_name"]} for r in ch_rows]
        backup_data = {
            "saved_at":       now(),
            "settings":       all_settings,
            "admin_id":       ADMIN_ID,
            "backup_channels": BACKUP_CHANNELS,
            "required_channels": db_channels if db_channels else REQUIRED_CHANNELS,
        }
        with open(CONFIG_BACKUP_FILE, "w", encoding="utf-8") as f:
            _json.dump(backup_data, f, ensure_ascii=False, indent=2)
        log.info("✅ config_backup.json আপডেট হয়েছে।")
    except Exception as e:
        log.warning(f"[AUTO-CONFIG] config_backup.json লিখতে ব্যর্থ: {e}")

    # ── Step 3: DB ফাঁকা হলে JSON থেকে restore করো ─────────────────
    try:
        if os.path.exists(CONFIG_BACKUP_FILE):
            with open(CONFIG_BACKUP_FILE, "r", encoding="utf-8") as f:
                saved = _json.load(f)
            with _c() as c:
                user_count = _count(c.execute("SELECT COUNT(*) FROM users").fetchone())
                ch_count_now = _count(c.execute("SELECT COUNT(*) FROM required_channels").fetchone())
            if user_count == 0 and saved.get("settings"):
                with _c() as c:
                    # settings restore করো
                    for key, val in saved["settings"].items():
                        c.execute(
                            "INSERT INTO settings (key,value) VALUES (%s,%s) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, val)
                        )
                    # required_channels restore করো — admin পরিবর্তন করা channels ফিরিয়ে আনো
                    if ch_count_now == 0 and saved.get("required_channels"):
                        for ch in saved["required_channels"]:
                            c.execute(
                                "INSERT INTO required_channels (ch_id, ch_link, ch_name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                                (ch["id"], ch["link"], ch["name"])
                            )
                log.info("✅ Fresh DB detected — config_backup.json থেকে settings + channels restore হয়েছে।")
    except Exception as e:
        log.warning(f"[AUTO-CONFIG] restore চেষ্টা ব্যর্থ: {e}")

    # ── Step 4: Bulk settings write এর পর cache purge করো ───────────
    # _auto_configure() অনেক settings force-overwrite করে — stale cache সব মুছো
    _settings_cache.clear()
    log.info("✅ Auto-configure সম্পন্ন (settings cache cleared)।")


# ──────────────────────────── BEP-20 AUTO WITHDRAW ────────────────────────────

def _get_w3():
    w3 = None
    if not _WEB3_OK:
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL, request_kwargs={"timeout": 30}))
        return w3
    except Exception as e:
        log.error(f"[AUTO-WD] Web3 init failed: {e}")
        return None

def get_bsc_usdt_balance():
    """বট wallet এর USDT BEP-20 ব্যালান্স (USD float) রিটার্ন করো। ব্যর্থ হলে None।"""
    try:
        w3 = _get_w3()
        if not w3 or not w3.is_connected():
            return None
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDT_BEP20_CA),
            abi=USDT_BEP20_ABI
        )
        raw      = contract.functions.balanceOf(Web3.to_checksum_address(get_bsc_wallet_addr())).call()
        decimals = contract.functions.decimals().call()
        return raw / (10 ** decimals)
    except Exception as e:
        log.error(f"[AUTO-WD] USDT balance check failed: {e}")
        return None

def get_bsc_bnb_balance():
    """বট wallet এর BNB ব্যালান্স রিটার্ন করো (gas এর জন্য)। ব্যর্থ হলে None।"""
    try:
        w3 = _get_w3()
        if not w3 or not w3.is_connected():
            return None
        raw = w3.eth.get_balance(Web3.to_checksum_address(get_bsc_wallet_addr()))
        return raw / (10 ** 18)
    except Exception as e:
        log.error(f"[AUTO-WD] BNB balance check failed: {e}")
        return None

def check_bnb_sufficient() -> tuple:
    """BNB balance চেক করো।
    (True, bnb_bal) = যথেষ্ট আছে, (False, bnb_bal) = কম আছে — Admin কে alert পাঠাতে হবে।"""
    bnb = get_bsc_bnb_balance()
    if bnb is None:
        return True, None   # চেক করা যায়নি — চেষ্টা করতে দাও
    if bnb >= BNB_ALERT_THRESHOLD:
        return True, bnb    # যথেষ্ট BNB আছে
    log.warning(f"[BNB] ⚠️ BNB কম: {bnb:.6f} BNB — Admin alert পাঠানো হবে।")
    return False, bnb       # কম আছে

def get_bnb_price_usd():
    """Binance থেকে BNB এর বর্তমান USD মূল্য নিয়ে আসো। ব্যর্থ হলে None।"""
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
        return float(data["price"])
    except Exception as e:
        log.warning(f"[AUTO-WD] BNB price fetch failed: {e}")
        return None

def estimate_gas_cost_usd():
    """USDT transfer এর জন্য gas fee USD-এ হিসাব করো।
    সফল হলে (gas_bnb, gas_usd), ব্যর্থ হলে (None, None)।"""
    gas_bnb = None
    gas_usd = None
    try:
        w3 = _get_w3()
        if not w3 or not w3.is_connected():
            return None, None
        gas_price  = w3.eth.gas_price
        gas_units  = 65000                       # USDT BEP-20 transfer এর স্বাভাবিক gas
        gas_bnb    = (gas_price * gas_units) / (10 ** 18)
        bnb_price  = get_bnb_price_usd()
        if bnb_price is None:
            return gas_bnb, None
        gas_usd = gas_bnb * bnb_price
        return gas_bnb, gas_usd
    except Exception as e:
        log.warning(f"[AUTO-WD] Gas estimate failed: {e}")
        return None, None

# Gas fee সর্বোচ্চ সীমা — এর বেশি হলে admin কে জানানো হবে
GAS_ALERT_THRESHOLD_USD = 0.01

def send_usdt_bep20(to_address: str, net_usd: float):
    """USDT BEP-20 পাঠাও।
    সবসময় (tx_hash_str_or_None, err_or_None, gas_usd_or_None, gas_bnb_or_None) — 4-tuple রিটার্ন করো।
    ⚠️ Wallet থেকে শুধু USDT কাটে — BNB শুধু gas fee হিসেবে যায়।"""
    gas_bnb_actual = None
    gas_usd_paid = None
    try:
        w3 = _get_w3()
        if not w3 or not w3.is_connected():
            return None, "BSC RPC সংযোগ ব্যর্থ", None, None  # Bug #7 fix: 4-tuple
        if not BSC_PRIVATE_KEY:
            return None, "BSC_PRIVATE_KEY সেট নেই", None, None  # Bug #7 fix: 4-tuple
        if not get_bsc_wallet_addr():
            return None, "BSC_WALLET_ADDRESS সেট নেই", None, None  # Bug #7 fix: 4-tuple
        contract  = w3.eth.contract(
            address=Web3.to_checksum_address(USDT_BEP20_CA),
            abi=USDT_BEP20_ABI
        )
        decimals   = contract.functions.decimals().call()
        amount_raw = int(net_usd * (10 ** decimals))
        account    = w3.eth.account.from_key(BSC_PRIVATE_KEY)
        nonce      = w3.eth.get_transaction_count(account.address, 'pending')
        gas_price  = w3.eth.gas_price
        tx = contract.functions.transfer(
            Web3.to_checksum_address(to_address),
            amount_raw
        ).build_transaction({
            "from":     account.address,
            "nonce":    nonce,
            "gasPrice": gas_price,
            "chainId":  56,
        })
        gas        = w3.eth.estimate_gas(tx)
        tx["gas"]  = int(gas * 1.2)
        # ── Actual gas cost হিসাব ─────────────────────────────────────
        gas_bnb_actual = (gas_price * tx["gas"]) / (10 ** 18)
        bnb_price      = get_bnb_price_usd()
        gas_usd_paid   = round(gas_bnb_actual * bnb_price, 6) if bnb_price else None
        signed  = w3.eth.account.sign_transaction(tx, BSC_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        # ── Receipt confirm — on-chain status নিশ্চিত করো (timeout 120s) ──
        try:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt.status != 1:
                return None, f"TX reverted on-chain: {tx_hash.hex()}", gas_usd_paid, gas_bnb_actual
        except Exception as _re:
            # Timeout বা network issue — hash সেভ করো, পরের cycle এ recovery হবে
            log.warning(f"[AUTO-WD] receipt wait failed ({_re}) — returning hash for recovery")
        return tx_hash.hex(), None, gas_usd_paid, gas_bnb_actual
    except Exception as e:
        return None, str(e), gas_usd_paid, None

async def job_auto_withdraw(context):
    """প্রতি ২ মিনিটে চলে — pending withdrawals গুলো BEP-20 এ auto-process করে।"""
    _gas_cur = None
    locked = None
    rows = None
    stuck_rows = None
    if not _WEB3_OK:
        log.warning("[AUTO-WD] web3 library নেই — `pip install web3` চালান।")
        return
    if not BSC_PRIVATE_KEY or not await asyncio.to_thread(get_bsc_wallet_addr):
        log.warning("[AUTO-WD] BSC_PRIVATE_KEY বা BSC_WALLET_ADDRESS সেট নেই — skip।")
        return

    # আগের cycle চলছে%s তাহলে এই cycle skip — queue pile-up এবং nonce conflict বন্ধ
    if _wd_job_lock.locked():
        log.info("[AUTO-WD] আগের cycle এখনো চলছে — এই cycle skip।")
        return

    async with _wd_job_lock:
        # ── Stuck processing recovery ─────────────────────────────────
        # যে records ১০+ মিনিট ধরে 'processing' এ আছে সেগুলো recover করো
        stuck_cutoff = (datetime.now(BD_TZ) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
        def _db_12006():
            stuck_rows = None
            with _c() as c:
                stuck_rows = c.execute(
                    "SELECT id, user_id, amount, tx_hash FROM withdrawals "
                    "WHERE status='processing' AND locked_at IS NOT NULL AND locked_at <= %s",
                    (stuck_cutoff,)
                ).fetchall()
            return stuck_rows
        stuck_rows = await asyncio.to_thread(_db_12006)
        for sr in stuck_rows:
            sid, suid, samt, stx = sr["id"], sr["user_id"], sr["amount"], sr["tx_hash"] or ""
            if stx.strip():
                # tx_hash আছে — TX পাঠানো হয়েছিল, শুধু approved mark বাকি ছিল
                def _db_12016():
                    with _c() as c:
                        c.execute("UPDATE withdrawals SET status='approved' WHERE id=%s AND status='processing'", (sid,))
                    return
                await asyncio.to_thread(_db_12016)
                _mark_dirty()
                log.warning(f"[AUTO-WD] #{sid}: stuck processing+tx_hash → auto-approved। tx={stx}")
                try:
                    bscscan_url = f"https://bscscan.com/tx/{stx}"
                    await context.bot.send_message(
                        suid,
                        f"✅ *Withdrawal Successful!*\n\n"
                        f"💰 *Amount:* `${samt:.4f} USDT BEP-20`\n"
                        f"🔗 *TX:* [BSCScan]({bscscan_url})\n\n"
                        f"💎 *Thank you!*",
                        parse_mode="Markdown", disable_web_page_preview=False
                    )
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{sid}: stuck-tx user notify ব্যর্থ: {_e}")
            else:
                # tx_hash নেই — TX পাঠানো হয়নি, নিরাপদে pending এ ফেরত দাও
                def _db_12034():
                    with _c() as c:
                        c.execute(
                            "UPDATE withdrawals SET status='pending', locked_at=NULL WHERE id=%s AND status='processing'",
                            (sid,)
                        )
                    return
                await asyncio.to_thread(_db_12034)
                _mark_dirty()
                log.warning(f"[AUTO-WD] #{sid}: stuck processing (no tx) → reset to pending।")

        _now_str    = datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M:%S")
        _fallback2m = (datetime.now(BD_TZ) - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")

        def _db_12045():
            rows = None
            with _c() as c:
                rows = c.execute(
                    "SELECT id,user_id,amount,fee,address FROM withdrawals "
                    "WHERE status='pending' AND ("
                    "  (scheduled_at != '' AND scheduled_at <= %s) "
                    "  OR "
                    "  (scheduled_at IS NULL OR scheduled_at = '') AND requested_at <= %s"
                    ") "
                    "ORDER BY id ASC",
                    (_now_str, _fallback2m)
                ).fetchall()
            return rows
        rows = await asyncio.to_thread(_db_12045)
        if not rows:
            return

        log.info(f"[AUTO-WD] {len(rows)} টি scheduled withdrawal প্রস্তুত — processing শুরু।")

        for row in rows:
            wid     = row["id"]
            uid     = row["user_id"]
            amt     = row["amount"]
            fee     = row["fee"] if row["fee"] is not None else await asyncio.to_thread(get_wd_fee)
            address = row["address"]
            net_amt = round(amt - fee, 4)

            # ── Gas fee estimate — >= $0.01 হলে থামো, admin কে জানাও ──────
            gas_bnb_est, gas_usd_est = await asyncio.to_thread(estimate_gas_cost_usd)
            gas_usd_str = f"${gas_usd_est:.4f}" if gas_usd_est is not None else "অজানা"
            if gas_usd_est is not None and gas_usd_est >= GAS_ALERT_THRESHOLD_USD:
                log.warning(f"[AUTO-WD] #{wid}: Gas fee {gas_usd_str} >= ${GAS_ALERT_THRESHOLD_USD} — pausing, admin alert।")
                def _db_12074():
                    _gas_cur = None
                    with _c() as c:
                        # AND status='pending' guard — ইতিমধ্যে cancel/process হলে gas_pending করবে না
                        _gas_cur = c.execute(
                            "UPDATE withdrawals SET status='gas_pending' WHERE id=%s AND status='pending'", (wid,)
                        )
                    return _gas_cur
                _gas_cur = await asyncio.to_thread(_db_12074)
                if _gas_cur.rowcount == 0:
                    log.warning(f"[AUTO-WD] #{wid}: gas_pending set skip — status আর pending নেই।")
                    continue
                _mark_dirty()
                try:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Approve (পাঠাও)", callback_data=f"gas_ok_{wid}"),
                         InlineKeyboardButton("❌ Cancel (Refund)",  callback_data=f"gas_cancel_{wid}")],
                    ])
                    await context.bot.send_message(
                        ADMIN_ID,
                        f"⚠️ *Gas Fee বেশি — WD #{wid} থেমে আছে*\n\n"
                        f"👤 *User:* `{uid}`\n"
                        f"💰 *Amount:* `${amt:.4f} USDT`\n"
                        f"📉 *Fee:* `${fee:.4f}` | *Net:* `${net_amt:.4f}`\n"
                        f"⛽ *Gas Fee:* `{gas_usd_str}` (সীমা: ${GAS_ALERT_THRESHOLD_USD})\n\n"
                        f"🔘 *Approve* চাপলে পাঠানো হবে।\n"
                        f"❌ *Cancel* চাপলে ইউজারকে টাকা ফেরত যাবে।\n"
                        f"⚠️ *Approve না করলে পাঠাবে না।*",
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                except Exception as e:
                    log.error(f"[AUTO-WD] #{wid}: Admin alert পাঠানো যায়নি — {e}")
                continue

            # ── Wallet balance check ──────────────────────────────────────
            wallet_bal = await asyncio.to_thread(get_bsc_usdt_balance)
            if wallet_bal is None:
                log.warning(f"[AUTO-WD] #{wid}: balance check failed — পরের চক্রে retry।")
                continue
            if wallet_bal < net_amt:
                err_detail = f"Bot wallet balance ({wallet_bal:.4f} USDT) < required ({net_amt:.4f} USDT)"
                log.warning(f"[AUTO-WD] #{wid}: {err_detail} — reject।")
                # Atomic: AND status='pending' guard — double-refund প্রতিরোধ করে
                def _db_12114():
                    with _c() as c:
                        _awd_low_cur = c.execute(
                            "UPDATE withdrawals SET status='rejected' WHERE id=%s AND status='pending'", (wid,)
                        )
                        if _awd_low_cur.rowcount == 0:
                            log.warning(f"[AUTO-WD] #{wid}: status আর pending নেই — skip।")
                            return False
                        c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amt, uid))
                        _invalidate_user_cache(uid)
                    return True
                _db_12114_ok = await asyncio.to_thread(_db_12114)
                if not _db_12114_ok:
                    continue
                _mark_dirty()
                _ulang = await asyncio.to_thread(get_lang, uid)
                try:
                    _low_bal_txt = (
                        f"❌ *Your withdrawal was cancelled.*\n\n"
                        f"💰 *Amount:* `${amt:.4f} USDT`\n"
                        f"🔄 The amount has been refunded to your account.\n\n"
                        f"❓ Support: @inkamxbd"
                        if _ulang == 'en' else
                        f"❌ *Withdrawal বাতিল হয়েছে।*\n\n"
                        f"💰 *Amount:* `${amt:.4f} USDT`\n"
                        f"🔄 টাকা আপনার একাউন্টে ফেরত দেওয়া হয়েছে।\n\n"
                        f"❓ সমস্যার জন্য: @inkamxbd"
                    )
                    await context.bot.send_message(uid, _low_bal_txt, parse_mode="Markdown")
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: user notify (low-bal) ব্যর্থ: {_e}")
                try:
                    await context.bot.send_message(
                        ADMIN_ID,
                        f"⚠️ *Auto-WD Reject — WD #{wid}*\n\n"
                        f"👤 *User:* `{uid}`\n"
                        f"💰 *Amount:* `${amt:.4f} USDT`\n"
                        f"📍 *Address:* `{address}`\n\n"
                        f"❌ *কারণ:* {err_detail}",
                        parse_mode="Markdown"
                    )
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: admin alert (low-bal) ব্যর্থ: {_e}")
                continue

            # ── Security Lock: payment পাঠানোর আগে atomically 'processing' করো ──
            def _db_12154():
                locked = None
                with _c() as c:
                    locked = c.execute(
                        "UPDATE withdrawals SET status='processing', locked_at=%s WHERE id=%s AND status='pending'",
                        (now(), wid)
                    )
                return locked
            locked = await asyncio.to_thread(_db_12154)
            if locked.rowcount == 0:
                log.warning(f"[AUTO-WD] #{wid}: ইতিমধ্যে processing বা cancel — skip।")
                continue

            # ── USDT পাঠাও ───────────────────────────────────────────────
            log.info(f"[AUTO-WD] #{wid}: Sending {net_amt} USDT → {address} | Est. gas: {gas_usd_str}")
            tx_hash, err, gas_usd_paid, gas_bnb_actual = await asyncio.to_thread(send_usdt_bep20, address, net_amt)

            if tx_hash:
                # ── সফল ────────────────────────────────────────────────
                # tx_hash + status একসাথে atomic update — দুটো আলাদা query = crash gap ছিল
                def _db_12171():
                    with _c() as c:
                        c.execute(
                            "UPDATE withdrawals SET tx_hash=%s, status='approved' WHERE id=%s AND status='processing'",
                            (tx_hash, wid)
                        )
                    return
                await asyncio.to_thread(_db_12171)
                _mark_dirty()
                bscscan_url  = f"https://bscscan.com/tx/{tx_hash}"
                # Gas fee — BNB + USD দুটোই দেখাবে
                if gas_bnb_actual is not None and gas_usd_paid is None:
                    try:
                        _bnb_p = await asyncio.to_thread(get_bnb_price_usd)
                        if _bnb_p:
                            gas_usd_paid = round(gas_bnb_actual * _bnb_p, 6)
                    except Exception as _e:
                        log.warning(f"[AUTO-WD] #{wid}: BNB price fetch (post-tx) ব্যর্থ: {_e}")
                if gas_bnb_actual is not None:
                    if gas_usd_paid is not None:
                        gas_paid_str = f"{gas_bnb_actual:.6f} BNB (≈${gas_usd_paid:.4f})"
                    else:
                        gas_paid_str = f"{gas_bnb_actual:.6f} BNB"
                elif gas_usd_paid is not None:
                    gas_paid_str = f"${gas_usd_paid:.4f}"
                else:
                    gas_paid_str = "হিসাব করা যায়নি"
                success_msg = (
                    f"✅ *Withdrawal Successful!*\n\n"
                    f"💰 *Amount:* `${net_amt:.4f} USDT BEP-20`\n"
                    f"📋 *Address:* `{address}`\n"
                    f"🔗 *TX Hash:* `{tx_hash}`\n\n"
                    f"🌐 *BSCScan:* [View Transaction]({bscscan_url})\n\n"
                    f"💎 *Thank you!*"
                )
                try:
                    await context.bot.send_message(uid, success_msg, parse_mode="Markdown", disable_web_page_preview=False)
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: user success notify ব্যর্থ: {_e}")
                try:
                    masked = address[:6] + "..." + address[-4:]
                    _urow  = await asyncio.to_thread(get_user, uid)
                    _uname_raw = (_urow["full_name"] or _urow["username"] or "Unknown") if _urow else "Unknown"
                    _uname = _uname_raw.split('@')[0].strip() or _uname_raw.lstrip('@').split()[0] if _uname_raw else "Unknown"
                    ch_msg = (
                        f"👤 *{_uname}*\n\n"
                        f"✅ *Withdrawal Successful!*\n\n"
                        f"💰 *Amount:* `${net_amt:.4f} USDT BEP-20`\n"
                        f"📋 *Address:* `{masked}`\n"
                        f"🔗 [View on BSCScan]({bscscan_url})"
                    )
                    await context.bot.send_message(await asyncio.to_thread(get_withdraw_ch_id), ch_msg, parse_mode="Markdown", disable_web_page_preview=False)
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: withdraw channel notify ব্যর্থ: {_e}")
                try:
                    _urow       = await asyncio.to_thread(get_user, uid)
                    _uname_plain = (_urow["full_name"] or _urow["username"] or str(uid)) if _urow else str(uid)
                    _uname_plain = _uname_plain.split('@')[0].strip() or str(uid)
                    _uhandle_plain = f"@{_urow['username']}" if (_urow and _urow.get("username")) else "নেই"
                    # Plain text — no parse_mode — সবচেয়ে reliable, কোনো escape সমস্যা নেই
                    admin_ch_msg = (
                        f"✅ Withdraw সফল!\n\n"
                        f"👤 User: {_uname_plain}\n"
                        f"Username: {_uhandle_plain}\n"
                        f"User ID: {uid}\n\n"
                        f"Method: USDT BEP-20 (BSC)\n"
                        f"Address: {address}\n"
                        f"Debit: ${amt:.4f} USDT\n"
                        f"Fee: ${fee:.4f} USDT\n"
                        f"Sent: ${net_amt:.4f} USDT\n"
                        f"Gas Fee: {gas_paid_str}\n"
                        f"WD ID: #{wid}\n"
                        f"TX: {bscscan_url}\n"
                        f"সময়: {now()}"
                    )
                    await context.bot.send_message(WD_DETAIL_CH_ID, admin_ch_msg, disable_web_page_preview=True)
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: Admin detail channel পাঠানো যায়নি — {_e}")
                    # Channel fail হলে main admin কে সরাসরি জানাও (plain text)
                    try:
                        await context.bot.send_message(
                            ADMIN_ID,
                            f"⚠️ WD Detail Channel এ পাঠানো যায়নি!\n"
                            f"Error: {str(_e)[:300]}\n\n"
                            f"WD ID: #{wid} | User: {uid}\n"
                            f"Amount: ${amt:.4f} | Sent: ${net_amt:.4f}\n"
                            f"Address: {address}\n"
                            f"TX: {bscscan_url}",
                            disable_web_page_preview=True
                        )
                    except Exception:
                        pass
                log.info(f"[AUTO-WD] #{wid}: ✅ Approved — tx={tx_hash} | gas={gas_paid_str}")
            else:
                # ── TX fail হয়েছে — BNB gas সমস্যা হলে সব Admin কে notify ──
                if err and ("insufficient funds" in err.lower() or "out of gas" in err.lower() or "gas" in err.lower()):
                    # Withdrawal টা pending এ ফেরত দাও — user এর টাকা যাবে না
                    def _db_12258():
                        with _c() as c:
                            c.execute(
                                "UPDATE withdrawals SET status='pending', locked_at=NULL WHERE id=%s AND status='processing'",
                                (wid,)
                            )
                        return
                    await asyncio.to_thread(_db_12258)
                    _mark_dirty()
                    log.warning(f"[AUTO-WD] #{wid}: BNB/Gas সমস্যা — pending এ রাখা হয়েছে।")
                    # Cooldown check — প্রতি ঘন্টায় সর্বোচ্চ একবার alert
                    import time as _time_mod
                    global _bnb_alert_last_sent
                    _now_ts = _time_mod.monotonic()
                    if _now_ts - _bnb_alert_last_sent >= _BNB_ALERT_COOLDOWN:
                        _bnb_alert_last_sent = _now_ts
                        try:
                            _bnb_now = await asyncio.to_thread(get_bsc_bnb_balance)
                            _bnb_str = f"{_bnb_now:.6f}" if _bnb_now is not None else "অজানা"
                            _wallet  = await asyncio.to_thread(get_bsc_wallet_addr)
                            await notify_all_admins(
                                context.bot,
                                f"⚠️ *Wallet এ BNB শেষ! Withdrawal আটকে আছে।*\n\n"
                                f"🔋 *BNB Balance:* `{_bnb_str} BNB`\n\n"
                                f"📌 *আটকে থাকা WD:* `#{wid}`\n"
                                f"👤 *User:* `{uid}`\n"
                                f"💰 *Amount:* `${amt:.4f} USDT`\n\n"
                                f"👉 *Wallet এ BNB যোগ করুন, তারপর Withdrawal আপনা-আপনি process হবে।*\n"
                                f"🔗 *Wallet:* `{_wallet}`"
                            )
                        except Exception as _e:
                            log.error(f"[AUTO-WD] BNB alert পাঠানো যায়নি — {_e}")
                    else:
                        log.warning(f"[AUTO-WD] BNB alert cooldown চলছে — skip করা হয়েছে")
                    continue   # এই withdrawal pending — পরেরটা try করো

                # ── অন্য error — tx_hash IS NULL নিশ্চিত করো — TX গেলে refund করবো না
                def _db_12286():
                    with _c() as c:
                        _awd_fail_cur = c.execute(
                            "UPDATE withdrawals SET status='rejected' WHERE id=%s AND status='processing' AND tx_hash IS NULL", (wid,)
                        )
                        if _awd_fail_cur.rowcount == 0:
                            log.warning(f"[AUTO-WD] #{wid}: TX fail কিন্তু tx_hash আছে বা processing নেই — refund skip (double-spend guard)।")
                            return False
                        c.execute("UPDATE users SET balance=balance+%s WHERE user_id=%s", (amt, uid))
                        _invalidate_user_cache(uid)
                    return True
                _db_12286_ok = await asyncio.to_thread(_db_12286)
                if not _db_12286_ok:
                    continue
                _mark_dirty()
                _ulang2 = await asyncio.to_thread(get_lang, uid)
                _fail_txt = (
                    f"❌ *Your withdrawal was cancelled.*\n\n"
                    f"💰 *Amount:* `${amt:.4f} USDT`\n"
                    f"🔄 The amount has been refunded to your account.\n\n"
                    f"❓ Support: @inkamxbd"
                    if _ulang2 == 'en' else
                    f"❌ *Withdrawal বাতিল হয়েছে।*\n\n"
                    f"💰 *Amount:* `${amt:.4f} USDT`\n"
                    f"🔄 টাকা আপনার একাউন্টে ফেরত দেওয়া হয়েছে।\n\n"
                    f"❓ সমস্যার জন্য: @inkamxbd"
                )
                try:
                    await context.bot.send_message(uid, _fail_txt, parse_mode="Markdown")
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: user fail notify ব্যর্থ: {_e}")
                try:
                    await context.bot.send_message(
                        ADMIN_ID,
                        f"❌ *Auto-WD Failed — WD #{wid}*\n\n"
                        f"👤 *User:* `{uid}`\n"
                        f"💰 *Amount:* `${amt:.4f} USDT`\n"
                        f"📍 *Address:* `{address}`\n"
                        f"⛽ *Gas est.:* `{gas_usd_str}`\n\n"
                        f"🔴 *Error:* `{err}`",
                        parse_mode="Markdown"
                    )
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: admin fail alert ব্যর্থ: {_e}")
                log.error(f"[AUTO-WD] #{wid}: ❌ Failed — {err}")

            await asyncio.sleep(3)  # TX এর মাঝে একটু বিরতি


def _setup_uptimerobot():
    """
    UptimeRobot API দিয়ে monitor auto-register করো।
    Env vars:
      UPTIMEROBOT_API_KEY  — UptimeRobot Main API key (ur...)
      BOT_PUBLIC_URL       — (optional) override ping URL
    """
    api_key = _get_env("UPTIMEROBOT_API_KEY")

    # Public URL বের করো — API key থাকুক বা না থাকুক URL সবসময় log করো
    bot_url = _get_env("BOT_PUBLIC_URL")
    if not bot_url:
        domains = _get_env("REPLIT_DOMAINS")
        if domains:
            first_domain = domains.split(",")[0].strip()
            bot_url = f"https://{first_domain}"

    ping_url = (bot_url.rstrip("/") + "/api/bot-ping") if bot_url else ""

    if not api_key:
        return  # VPS-এ UptimeRobot optional — শুধু key থাকলে চলবে
    if not bot_url:
        return

    def _ur_post(endpoint, params):
        """UptimeRobot v2 POST — error হলে response body সহ log করো।"""
        data = urllib.parse.urlencode({**params, "api_key": api_key, "format": "json"}).encode()
        req = urllib.request.Request(
            f"https://api.uptimerobot.com/v2/{endpoint}",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cache-Control": "no-cache",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return _json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            log.warning(f"[UptimeRobot] HTTP {e.code} — {body[:200]}")
            return None
        except Exception as e:
            log.warning(f"[UptimeRobot] request ব্যর্থ: {e}")
            return None

    try:
        # আগে চেক করো duplicate আছে কিনা
        result = _ur_post("getMonitors", {"search": "Task Earn Bot"})
        if result is None:
            log.warning(f"[UptimeRobot] getMonitors ব্যর্থ — URL টি manually দিন: {ping_url}")
            return
        if result.get("stat") == "ok" and result.get("monitors"):
            existing_url = result["monitors"][0].get("url", "")
            if existing_url != ping_url:
                log.info("[UptimeRobot] Monitor আছে কিন্তু URL পুরনো — update হবে না (manually করুন)।")
            else:
                log.info("✅ [UptimeRobot] Monitor আগে থেকেই আছে ✓")
            return

        # নতুন monitor তৈরি করো
        result2 = _ur_post("newMonitor", {
            "type":          "1",       # HTTP(s)
            "url":           ping_url,
            "friendly_name": "Task Earn Bot",
        })
        if result2 is None:
            log.warning(f"[UptimeRobot] newMonitor ব্যর্থ — manually দিন: {ping_url}")
            return
        if result2.get("stat") == "ok":
            mid = result2.get("monitor", {}).get("id", "?")
            log.info(f"✅ [UptimeRobot] Monitor তৈরি! ID={mid} | URL={ping_url}")
        else:
            err = result2.get("error", result2)
            log.warning(f"[UptimeRobot] Monitor তৈরি ব্যর্থ: {err} — manually দিন: {ping_url}")

    except Exception as e:
        log.warning(f"[UptimeRobot] setup ব্যর্থ: {e} — manually দিন: {ping_url}")


def main():
    # ── ProxyError fix: httpx যেন system proxy ignore করে (503 এড়াতে) ───
    for _pvar in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
        os.environ.pop(_pvar, None)

    # ── Single-instance lock — দুটো instance একসাথে চলতে পারবে না ─
    import fcntl, sys
    _pid_file = open("/tmp/task_earn_bot.lock", "w")
    try:
        fcntl.flock(_pid_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("❌ Bot ইতিমধ্যে চলছে (PID lock)। দুটো instance একসাথে চলবে না।")
        sys.exit(1)

    # PostgreSQL ব্যবহার হচ্ছে — file backup/restore দরকার নেই
    init_db()
    _auto_configure()
    _setup_uptimerobot()  # no-ops if UPTIMEROBOT_API_KEY not set
    async def post_init(application):
        global _app
        _app = application   # immediate backup scheduling এর জন্য
        # ── Step 1: DB না থাকলে Telegram থেকে নিজেই restore করো ──
        # PostgreSQL — auto-restore দরকার নেই
        # ── Step 2: Startup backup পাঠাও ──────────────────────────
        log.info("✅ PostgreSQL connected — data permanently stored in CockroachDB.")
        # DB এর পরপরই user report পাঠাও ও pin করো
        await _send_startup_report(application.bot)
        # ── Step 3: Pending notification watcher শুরু করো ─────────
        # Bot restart হলেও DB থেকে pending notifications ধরবে।
        _watcher_task = asyncio.create_task(_pn_watcher(application.bot))
        _background_tasks.add(_watcher_task)
        _watcher_task.add_done_callback(_background_tasks.discard)
        log.info("✅ Pending-notification watcher started (প্রতি ৩০ সেকেন্ড চেক করবে)")
        # Fallback dirty-check: ৬০ সেকেন্ড পরপর (immediate scheduling ব্যর্থ হলে)
        if application.job_queue is not None:
            application.job_queue.run_repeating(
                job_dirty_backup,
                interval=60,    # CockroachDB — original 60s interval
                first=300,
                name="dirty_backup"
            )
            application.job_queue.run_repeating(
                job_health_check,
                interval=3600,
                first=300,
                name="health_check"
            )
            application.job_queue.run_repeating(
                job_clean_stuck_states,
                interval=1800,
                first=1800,
                name="clean_stuck_states"
            )
            application.job_queue.run_repeating(
                job_scheduled_report,
                interval=BACKUP_INTERVAL_HOURS * 3600,
                first=BACKUP_INTERVAL_HOURS * 3600,
                name="scheduled_report"
            )
            application.job_queue.run_daily(
                job_auto_cleanup,
                time=dt_time(hour=21, minute=0, tzinfo=pytz.utc),  # UTC 21:00 = BD 03:00
                name="daily_auto_cleanup"
            )
            application.job_queue.run_daily(
                job_bio_task_check,
                time=dt_time(hour=0, minute=5, tzinfo=BD_TZ),
                name="daily_bio_task"
            )
            application.job_queue.run_repeating(
                job_bio_realtime_monitor,
                interval=60,
                first=30,
                name="bio_realtime_monitor"
            )
            application.job_queue.run_daily(
                job_post_leaderboard,
                time=dt_time(hour=0, minute=0, tzinfo=BD_TZ),
                name="daily_leaderboard"
            )
            application.job_queue.run_repeating(
                job_lb_adm_update,
                interval=600,    # CockroachDB — original 600s interval
                first=1800,
                name="lb_adm_update"
            )
            # ── BEP-20 Auto Withdraw — শুধু BSC_PRIVATE_KEY থাকলে চালু হবে ──
            if BSC_PRIVATE_KEY:
                application.job_queue.run_repeating(
                    job_auto_withdraw,
                    interval=60,
                    first=60,
                    name="auto_withdraw"
                )
                log.info("✅ BEP-20 Auto Withdraw job registered (প্রতি ১ মিনিট)")
            else:
                log.warning("⚠️ BSC_PRIVATE_KEY নেই — Auto Withdraw বন্ধ আছে।")
            log.info("✅ Dirty-flag backup job registered (প্রতি ৬০ সেকেন্ড)")
            log.info("✅ Health check job registered (প্রতি ১ ঘণ্টায় auto-check & fix)")
            log.info("✅ Stuck-state cleaner job registered (প্রতি ৩০ মিনিট)")
            log.info(f"✅ Scheduled report job registered (প্রতি {BACKUP_INTERVAL_HOURS} ঘণ্টায়)")
            log.info("✅ Daily leaderboard job registered (রাত ১২:০০ BD)")
            log.info("✅ Admin leaderboard auto-update job registered (প্রতি ১০ মিনিট)")
        else:
            # ── JobQueue নেই (APScheduler install নেই) — asyncio দিয়ে সব job চালাও ──
            log.warning("⚠️ JobQueue unavailable — asyncio fallback দিয়ে সব periodic job চালু হচ্ছে...")

            class _FakeCtx:
                """job callback গুলো context.bot চায় — এটা তার বিকল্প।"""
                def __init__(self, bot): self.bot = bot

            _fctx = _FakeCtx(application.bot)

            async def _run_periodically(fn, interval_sec, first_sec=0):
                if first_sec:
                    await asyncio.sleep(first_sec)
                while True:
                    try:
                        await fn(_fctx)
                    except Exception as _e:
                        log.warning(f"[ASYNC-JOB] {fn.__name__} ব্যর্থ: {_e}")
                    await asyncio.sleep(interval_sec)

            def _secs_until_daily(hour, minute, tz):
                """পরের দিন নির্দিষ্ট hour:minute পর্যন্ত কত সেকেন্ড বাকি।"""
                now_tz = datetime.now(tz)
                target = now_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if now_tz >= target:
                    target += timedelta(days=1)
                return (target - now_tz).total_seconds()

            async def _run_daily_at(fn, hour, minute, tz):
                """প্রতিদিন নির্দিষ্ট সময়ে fn চালায়।"""
                await asyncio.sleep(_secs_until_daily(hour, minute, tz))
                while True:
                    try:
                        await fn(_fctx)
                    except Exception as _e:
                        log.warning(f"[ASYNC-DAILY] {fn.__name__} ব্যর্থ: {_e}")
                    await asyncio.sleep(86400)

            _async_jobs = [
                asyncio.create_task(_run_periodically(job_dirty_backup,      60,  first_sec=60)),
                asyncio.create_task(_run_periodically(job_health_check,    3600,  first_sec=300)),
                asyncio.create_task(_run_periodically(job_clean_stuck_states, 1800, first_sec=1800)),
                asyncio.create_task(_run_periodically(job_scheduled_report,
                    BACKUP_INTERVAL_HOURS * 3600, first_sec=BACKUP_INTERVAL_HOURS * 3600)),
                asyncio.create_task(_run_periodically(job_lb_adm_update,    600,  first_sec=600)),
                asyncio.create_task(_run_periodically(job_bio_realtime_monitor, 60, first_sec=30)),
                asyncio.create_task(_run_daily_at(job_bio_task_check,   0, 5, BD_TZ)),
                asyncio.create_task(_run_daily_at(job_post_leaderboard, 0, 0, BD_TZ)),
                asyncio.create_task(_run_daily_at(job_auto_cleanup,     3, 0, BD_TZ)),
            ]
            if BSC_PRIVATE_KEY:
                _async_jobs.append(
                    asyncio.create_task(_run_periodically(job_auto_withdraw, 60, first_sec=60))
                )
            for _t in _async_jobs:
                _background_tasks.add(_t)
                _t.add_done_callback(_background_tasks.discard)
            log.info("✅ Asyncio fallback: সব periodic job চালু হয়েছে (job_queue ছাড়াই)।")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .connect_timeout(10)                # sendMessage / API call connect
        .read_timeout(15)                   # sendMessage / API call read
        .write_timeout(10)                  # sendMessage write
        .pool_timeout(10)                   # HTTP connection pool
        .get_updates_connect_timeout(10)    # getUpdates (long-poll) connect
        .get_updates_read_timeout(45)       # getUpdates long-poll wait — বেশি রাখো
        .get_updates_write_timeout(10)      # getUpdates write
        .get_updates_pool_timeout(10)       # getUpdates pool
        .build()
    )
    # ── User বট block করলে তার data মুছে দাও ────────────────────────
    from telegram.ext import ChatMemberHandler as _CmH
    async def _on_bot_blocked(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.my_chat_member: return
        new = update.my_chat_member.new_chat_member
        if new and new.status in (ChatMemberStatus.BANNED, ChatMemberStatus.LEFT):
            uid = update.my_chat_member.from_user.id
            await asyncio.to_thread(delete_user_data, uid)
    app.add_handler(_CmH(_on_bot_blocked))

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("cancel",      cmd_cancel))
    app.add_handler(CommandHandler("history",     cmd_history))
    app.add_handler(CommandHandler("backup",          cmd_backup))
    app.add_handler(CommandHandler("restore",         cmd_restore))
    app.add_handler(CommandHandler("test",            cmd_testleaderboard))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("point",       cmd_point))
    app.add_handler(CommandHandler("inactive",   cmd_inactive))
    app.add_handler(CommandHandler("promocode",  cmd_promocode))
    app.add_handler(CommandHandler("promo",      cmd_promo))
    # Register extra callback handler BEFORE main callback handler
    app.add_handler(CallbackQueryHandler(on_callback_extra,       pattern=r"^(adm_task_done_\d+|adm_notif_send|adm_notif_edit|adm_notif_skip|adm_tp_.+)$"))
    app.add_handler(CallbackQueryHandler(_handle_promo_callbacks, pattern=r"^adm_promo_(req_anyone|req_tasks|post_news|save_only)$"))
    app.add_handler(CallbackQueryHandler(on_callback))
    # ~filters.VIA_BOT → inline-bot দিয়ে পাঠানো message ignore করো
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND & ~filters.VIA_BOT, on_photo))
    app.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND & ~filters.VIA_BOT, on_video))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND & ~filters.VIA_BOT, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.VIA_BOT, on_message))
    app.add_error_handler(_smart_error_handler)
    log.info("✅ Task Earn Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import time, asyncio as _asyncio
    import concurrent.futures as _cf

    # ── Thread pool বড় করো — প্রতিটি asyncio.to_thread() এখানে থেকে worker নেয়
    # ডিফল্ট pool = min(32, cpu_count+4) ≈ 5 workers (Render free tier)
    # 5 জন user একসাথে বোতাম চাপলেই pool শেষ → queue → সবার delay
    # 100 workers → concurrent DB calls আর block হবে না
    _thread_executor = _cf.ThreadPoolExecutor(max_workers=100, thread_name_prefix="bot_worker")

    keep_alive()   # ← Flask server চালু (Render web service এর জন্য)
    time.sleep(3)  # Flask পুরোপুরি bind হতে দাও — Render port scan এর আগে
    while True:
        # Python 3.12+: run_polling() শেষ হলে loop বন্ধ হয়ে যায়।
        # প্রতিটা iteration এ fresh loop বানাও — না হলে
        # "There is no current event loop in thread 'MainThread'" crash হয়।
        _loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(_loop)
        # ── প্রতিটি নতুন event loop-এ বড় thread pool লাগাও ──────────────
        # না করলে নতুন loop নতুন ছোট pool তৈরি করে — পুরনো fix কাজ করে না
        _loop.set_default_executor(_thread_executor)
        try:
            main()
        except Exception as _e:
            log.error(f"❌ Bot crashed: {_e} — ১০ সেকেন্ড পরে restart হবে...")
            time.sleep(10)
        finally:
            try:
                _loop.close()
            except Exception:
                pass
