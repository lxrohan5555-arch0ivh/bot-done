#!/usr/bin/env python3
"""Task Earn Bot — Full Featured Telegram Bot"""

import os, logging, asyncio, urllib.request, urllib.parse, urllib.error, json as _json, shutil, random
# pip install psycopg2-binary
import psycopg2
import psycopg2.extras

# ── .env ফাইল automatic load ─────────────────────────────────────
# VPS/Server → tgbot/.env ফাইলে config লিখুন
# Replit     → Secrets এ দিন (env var হিসেবেই কাজ করে)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"), override=False)
except ImportError:
    pass  # python-dotenv নেই হলেও চলবে — Replit Secrets / system env থেকে পড়বে

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
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError

# ──────────────────────────── CONFIG ────────────────────────────
# প্রথমে Environment Variable থেকে নেওয়ার চেষ্টা করো,
# না পেলে টার্মিনালে input() দিয়ে চাও — কোনো .env ফাইল লাগবে না।


def _fval(row, default=None):
    """fetchone() → dict হলে প্রথম value বের করো (single-column query এর জন্য)."""
    if row is None:
        return default
    if isinstance(row, dict):
        vals = list(row.values())
        return vals[0] if vals else default
    return default

  
def _get_env(key, default=""):
    val = os.environ.get(key, "").strip()
    return val if val else default

def _require_env(key):
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"[ERROR] '{key}' সেট করা নেই! Replit Secrets-এ {key} যোগ করুন।")
        raise SystemExit(1)
    return val

BOT_TOKEN         = _require_env("BOT_TOKEN")
ADMIN_ID          = 7423929548
GMAIL_CH_ID       = -1003947318907
WITHDRAW_CH_ID    = -1003765360862
LEADERBOARD_CH_ID = "@task_earn_bot_news"
TASK_DONE_NOTIFY_CH = -1004251213683

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
# BNB এই পরিমাণের নিচে নামলে USDT থেকে অটো-কিনবে
BNB_REFILL_THRESHOLD = 0.001   # এর নিচে হলে swap হবে
BNB_REFILL_TARGET    = 0.003   # এতটুকু BNB কিনবে (~$0.9 worth)
BNB_MAX_BUY_USD      = 0.20    # সর্বোচ্চ ২০ সেন্ট — এর বেশি USDT দিয়ে BNB কিনবে না

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
            "আপনার পয়েন্ট ৩০ বা তার নিচে নেমে গেছে।\n\n"
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
            "• পয়েন্ট `30` বা নিচে গেলে: `{block_days}` দিন Task বন্ধ\n"
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
            "Your trust points dropped to 30 or below.\n\n"
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
        except: pass
    return text

def translate_to_en(text):
    try:
        url = ("https://translate.googleapis.com/translate_a/single"
               "?client=gtx&sl=bn&tl=en&dt=t&q=" + urllib.parse.quote(text))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
        return "".join(seg[0] for seg in data[0] if seg[0]).strip()
    except:
        return text

def md_escape(text):
    for ch in ('*', '_', '`', '['):
        text = text.replace(ch, f'\\{ch}')
    return text

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
            loop.create_task(_delayed_backup())
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
          self._cur.execute(sql, params)
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


class _ConnCtx:
    """with _c() as c: → commit on exit, rollback on error."""
    __slots__ = ("_conn", "_wrapper")

    def __enter__(self):
        self._conn = psycopg2.connect(DATABASE_URL)
        self._conn.autocommit = False
        self._wrapper = _CursorWrapper(self._conn)
        return self._wrapper

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception:
                try: self._conn.rollback()
                except Exception: pass
        else:
            try: self._conn.rollback()
            except Exception: pass
        try: self._conn.close()
        except Exception: pass
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
                user_id     BIGINT PRIMARY KEY,
                username    TEXT    DEFAULT '',
                full_name   TEXT    DEFAULT '',
                balance     REAL    DEFAULT 0,
                referred_by BIGINT  DEFAULT NULL,
                joined_at   TEXT    DEFAULT '',
                is_banned   INTEGER DEFAULT 0,
                lang        TEXT    DEFAULT '',
                country     TEXT    DEFAULT 'BD',
                points      INTEGER DEFAULT 100,
                blocked_until TEXT  DEFAULT NULL,
                last_seen   TEXT    DEFAULT ''
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
                photo_unique_ids TEXT  DEFAULT ''
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
                demo_video_id    TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS task_fields (
                id          BIGSERIAL PRIMARY KEY,
                task_id     BIGINT NOT NULL,
                field_order INTEGER DEFAULT 0,
                field_type  TEXT DEFAULT 'text',
                prompt_bn   TEXT DEFAULT '',
                prompt_en   TEXT DEFAULT ''
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
                allowed_countries TEXT DEFAULT ''
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
                demo_video_id    TEXT DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS category_task_fields (
                id          BIGSERIAL PRIMARY KEY,
                task_id     BIGINT NOT NULL,
                field_order INTEGER DEFAULT 0,
                field_type  TEXT DEFAULT 'text',
                prompt_bn   TEXT DEFAULT '',
                prompt_en   TEXT DEFAULT ''
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
                id          BIGSERIAL PRIMARY KEY,
                code        TEXT    NOT NULL UNIQUE,
                amount      REAL    NOT NULL DEFAULT 0,
                max_uses    INTEGER NOT NULL DEFAULT 0,
                used_count  INTEGER NOT NULL DEFAULT 0,
                requirement TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL DEFAULT '',
                expires_at  TEXT    NOT NULL DEFAULT '',
                is_active   INTEGER NOT NULL DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS promo_uses (
                id        BIGSERIAL PRIMARY KEY,
                code_id   BIGINT NOT NULL,
                user_id   BIGINT NOT NULL,
                used_at   TEXT DEFAULT '',
                UNIQUE(code_id, user_id)
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
                "ON CONFLICT (key) DO UPDATE SET label_bn=EXCLUDED.label_bn,label_en=EXCLUDED.label_en",
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
            import random as _rnd
            _chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            for tbl in ("tasks", "tg_tasks", "category_tasks"):
                empty_ids = [list(r.values())[0] for r in c.execute(
                    f"SELECT id FROM {tbl} WHERE task_code='' OR task_code IS NULL"
                ).fetchall()]
                for eid in empty_ids:
                    while True:
                        _code = "TK-" + "".join(_rnd.choices(_chars, k=5))
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
        log.info("✅ PostgreSQL schema ready.")


def now(): return datetime.now().strftime("%Y-%m-%d %H:%M")

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
def get_user(uid):
    with _c() as c: return c.execute("SELECT * FROM users WHERE user_id=%s", (uid,)).fetchone()

def ensure_user(uid, username, full_name, ref_by=None):
    with _c() as c:
        cur = c.execute(
            "INSERT INTO users (user_id,username,full_name,referred_by,joined_at,lang,country) VALUES (%s,%s,%s,%s,%s,'','BD') ON CONFLICT DO NOTHING",
            (uid, username or "", full_name or "", ref_by, now())
        )
        if cur.rowcount:  # নতুন user যোগ হলে
            _mark_dirty()

def get_lang(uid):
    with _c() as c:
        r = c.execute("SELECT lang FROM users WHERE user_id=%s", (uid,)).fetchone()
        return (r["lang"] if r and r["lang"] else "bn")

def user_has_lang(uid):
    with _c() as c:
        r = c.execute("SELECT lang FROM users WHERE user_id=%s", (uid,)).fetchone()
        return bool(r and r["lang"])

def set_lang(uid, lang):
    with _c() as c:
        c.execute("UPDATE users SET lang=%s WHERE user_id=%s", (lang, uid))

def get_user_country(uid):
    with _c() as c:
        r = c.execute("SELECT country FROM users WHERE user_id=%s", (uid,)).fetchone()
        return (r["country"] or "BD") if r else "BD"

def set_user_country(uid, country):
    with _c() as c:
        c.execute("UPDATE users SET country=%s WHERE user_id=%s", (country, uid))

# ── Point / Trust system ─────────────────────────────────────────
POINT_START       = 100
POINT_PER_REJECT  = 10   # reject হলে কমবে
POINT_PER_APPROVE = 3    # approve হলে বাড়বে (ধীরে ধীরে)
POINT_BLOCK_AT    = 30   # এর সমান বা নিচে গেলে block
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
    """Points বাড়াও/কমাও। 0–100 clamp। ≤ POINT_BLOCK_AT হলে block করো।"""
    with _c() as c:
        r = c.execute("SELECT points FROM users WHERE user_id=%s", (uid,)).fetchone()
        current  = (r["points"] if r and r["points"] is not None else POINT_START)
        new_pts  = max(0, min(POINT_START, current + delta))
        if delta < 0 and new_pts <= POINT_BLOCK_AT:
            block_dt     = datetime.now(timezone.utc) + timedelta(days=POINT_BLOCK_DAYS)
            blocked_until = block_dt.isoformat()
            c.execute("UPDATE users SET points=%s, blocked_until=%s WHERE user_id=%s",
                      (new_pts, blocked_until, uid))
        elif new_pts > POINT_BLOCK_AT:
            c.execute("UPDATE users SET points=%s, blocked_until=NULL WHERE user_id=%s",
                      (new_pts, uid))
        else:
            c.execute("UPDATE users SET points=%s WHERE user_id=%s", (new_pts, uid))
    _mark_dirty()   # points পরিবর্তন হলে সাথে সাথে backup নেও
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
    _mark_dirty()
    return True

def get_referred_by(uid):
    u = get_user(uid)
    return u["referred_by"] if u else None

def ban_user(uid, val):
    with _c() as c: c.execute("UPDATE users SET is_banned=%s WHERE user_id=%s", (val, uid))

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
def is_admin(uid):
    if uid == ADMIN_ID: return True
    with _c() as c:
        return c.execute("SELECT 1 FROM admins WHERE user_id=%s", (uid,)).fetchone() is not None

def add_admin(uid, added_by, permissions="full"):
    with _c() as c:
        c.execute(
            "INSERT INTO admins (user_id,added_by,added_at,permissions) VALUES (%s,%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET added_by=EXCLUDED.added_by,added_at=EXCLUDED.added_at,permissions=EXCLUDED.permissions",
            (uid, added_by, now(), permissions)
        )

def remove_admin(uid):
    with _c() as c:
        c.execute("DELETE FROM admins WHERE user_id=%s", (uid,))

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
    for adm in get_full_access_admins():
        try:
            await bot.send_message(adm["user_id"], msg, **kwargs)
        except Exception as e:
            log.warning(f"notify_full_admins sub-admin {adm['user_id']} fail: {e}")

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

# ── Settings helpers ──────────────────────────────────────────────
def get_setting(key):
    with _c() as c:
        r = c.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
        return r["value"] if r else None

def set_setting(key, val):
    with _c() as c: c.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, val))

def bot_is_on():
    return get_setting("bot_status") != "off"

def get_gmail_ch_id():
    v = get_setting('gmail_ch_id')
    try: return int(v)
    except: return GMAIL_CH_ID

def get_withdraw_ch_id():
    v = get_setting('withdraw_ch_id')
    try: return int(v)
    except: return WITHDRAW_CH_ID

def get_bsc_wallet_addr():
    v = get_setting('bsc_wallet_addr')
    return v.strip() if v and v.strip() else BSC_WALLET_ADDR

def get_min_withdraw():
    v = get_setting('min_withdraw')
    try: return float(v)
    except: return 0.05

def get_wd_fee():
    v = get_setting('wd_fee')
    try: return float(v)
    except: return 0.01

def get_commission_pct():
    v = get_setting('commission_pct')
    try: return float(v)
    except: return 8.0

def task_notify_on():
    return (get_setting('task_notify') or 'on') == 'on'

def get_task_notify_ch_username():
    v = get_setting('task_notify_ch_username') or '@task_earn_bot_notification'
    if v and not v.startswith('@'): v = '@' + v
    return v

def get_task_notify_ch_id():
    v = get_setting('task_notify_ch_id')
    try: return int(v)
    except: return -1003866731221

def get_notify_delay_min():
    v = get_setting('notify_delay_min')
    try: return int(v)
    except: return 0

# ── Menu button helpers ────────────────────────────────────────────
def get_btn(key, lang='bn'):
    with _c() as c:
        r = c.execute("SELECT label_bn, label_en FROM menu_buttons WHERE key=%s", (key,)).fetchone()
        if r:
            return r['label_bn'] if lang == 'bn' else r['label_en']
    return DEFAULT_BTNS.get(key, ('?','?'))[0 if lang == 'bn' else 1]

def get_all_btns(lang='bn'):
    with _c() as c:
        rows = c.execute("SELECT key, label_bn, label_en FROM menu_buttons").fetchall()
    result = {}
    for row in rows:
        result[row['key']] = row['label_bn'] if lang == 'bn' else row['label_en']
    for key, (bn, en) in DEFAULT_BTNS.items():
        if key not in result:
            result[key] = bn if lang == 'bn' else en
    return result

def set_btn(key, label_bn, label_en):
    with _c() as c:
        c.execute("INSERT INTO menu_buttons (key,label_bn,label_en) VALUES (%s,%s,%s) ON CONFLICT (key) DO UPDATE SET label_bn=EXCLUDED.label_bn,label_en=EXCLUDED.label_en",
                  (key, label_bn, label_en))

# ── Required Channel helpers ───────────────────────────────────────
def get_req_channels():
    with _c() as c:
        return c.execute("SELECT * FROM required_channels ORDER BY id").fetchall()

def add_req_channel(ch_id, ch_link, ch_name):
    with _c() as c:
        c.execute("INSERT INTO required_channels (ch_id, ch_link, ch_name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                  (ch_id, ch_link, ch_name))

def remove_req_channel(rid):
    with _c() as c:
        c.execute("DELETE FROM required_channels WHERE id=%s", (rid,))

# ── Task helpers ──────────────────────────────────────────────────
def get_tasks():
    with _c() as c: return c.execute("SELECT * FROM tasks WHERE is_active=1 ORDER BY id").fetchall()

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
    with _c() as c: return c.execute("SELECT * FROM tasks WHERE id=%s", (tid,)).fetchone()

def generate_task_code():
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
        return cur.lastrowid

def delete_task(tid):
    with _c() as c:
        c.execute("DELETE FROM tasks WHERE id=%s", (tid,))
        c.execute("DELETE FROM task_fields WHERE task_id=%s", (tid,))

def update_task_field_val(tid, field, value):
    if field not in {"name","price","rules","rules_en","is_active","max_completions","task_type","per_user_limit","allowed_countries","example_image_id","demo_video_id"}: return
    with _c() as c: c.execute(f"UPDATE tasks SET {field}=%s WHERE id=%s", (value, tid))

def get_all_tasks():
    with _c() as c: return c.execute("SELECT * FROM tasks ORDER BY id").fetchall()

# ── Task Fields helpers ────────────────────────────────────────────
def get_task_fields(task_id):
    with _c() as c:
        return c.execute(
            "SELECT * FROM task_fields WHERE task_id=%s ORDER BY field_order", (task_id,)
        ).fetchall()

def add_task_field(task_id, field_type, prompt_bn, prompt_en, order=None):
    with _c() as c:
        if order is None:
            r = c.execute("SELECT COUNT(*) FROM task_fields WHERE task_id=%s", (task_id,)).fetchone()
            order = _count(r)
        c.execute(
            "INSERT INTO task_fields (task_id,field_order,field_type,prompt_bn,prompt_en) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (task_id, order, field_type, prompt_bn, prompt_en)
        )

def delete_task_field(fid):
    with _c() as c:
        c.execute("DELETE FROM task_fields WHERE id=%s", (fid,))

def delete_all_task_fields(task_id):
    with _c() as c:
        c.execute("DELETE FROM task_fields WHERE task_id=%s", (task_id,))

# ── TG Task helpers ────────────────────────────────────────────────
def get_tg_tasks():
    with _c() as c:
        return c.execute("SELECT * FROM tg_tasks WHERE is_active=1 ORDER BY id").fetchall()

def get_tg_task(tid):
    with _c() as c:
        return c.execute("SELECT * FROM tg_tasks WHERE id=%s", (tid,)).fetchone()

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
        return cur.lastrowid

def delete_tg_task(tid):
    with _c() as c:
        c.execute("DELETE FROM tg_tasks WHERE id=%s", (tid,))
        c.execute("DELETE FROM tg_task_completions WHERE task_id=%s", (tid,))

def update_tg_task(tid, field, value):
    allowed = {"name","price","tg_target","tg_target_link","tg_target_name","max_completions","is_active","new_joiners_only","per_user_limit","allowed_countries"}
    if field not in allowed: return
    with _c() as c:
        c.execute(f"UPDATE tg_tasks SET {field}=%s WHERE id=%s", (value, tid))

def tg_task_user_done(task_id, user_id):
    with _c() as c:
        r = c.execute("SELECT 1 FROM tg_task_completions WHERE task_id=%s AND user_id=%s", (task_id, user_id)).fetchone()
        return r is not None

def tg_task_mark_done(task_id, user_id):
    with _c() as c:
        try:
            c.execute("INSERT INTO tg_task_completions (task_id,user_id,done_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (task_id, user_id, now()))
        except: pass
        c.execute("UPDATE tg_tasks SET completed_count=completed_count+1 WHERE id=%s", (task_id,))
        t = c.execute("SELECT max_completions,completed_count FROM tg_tasks WHERE id=%s", (task_id,)).fetchone()
        if t and t["max_completions"] > 0 and t["completed_count"] >= t["max_completions"]:
            c.execute("UPDATE tg_tasks SET is_active=0 WHERE id=%s", (task_id,))

def tg_task_slots_left(task_id):
    t = get_tg_task(task_id)
    if not t: return 0
    if t["max_completions"] == 0: return -1
    return max(0, t["max_completions"] - t["completed_count"])

# ── Category Task helpers ────────────────────────────────────────────
def get_categories():
    with _c() as c:
        return c.execute("SELECT * FROM task_categories WHERE is_active=1 ORDER BY id").fetchall()

def get_all_categories():
    with _c() as c:
        return c.execute("SELECT * FROM task_categories ORDER BY id").fetchall()

def get_category(cid):
    with _c() as c:
        return c.execute("SELECT * FROM task_categories WHERE id=%s", (cid,)).fetchone()

def add_category(name):
    with _c() as c:
        cur = c.execute("INSERT INTO task_categories (name) VALUES (%s) RETURNING id", (name,))
        return cur.lastrowid

def delete_category(cid):
    with _c() as c:
        tasks = c.execute("SELECT id FROM category_tasks WHERE category_id=%s", (cid,)).fetchall()
        for t in tasks:
            c.execute("DELETE FROM category_task_fields WHERE task_id=%s", (t[0],))
            c.execute("DELETE FROM category_task_completions WHERE task_id=%s", (t[0],))
        c.execute("DELETE FROM category_tasks WHERE category_id=%s", (cid,))
        c.execute("DELETE FROM task_categories WHERE id=%s", (cid,))

def update_category(cid, field, value):
    if field not in {"name", "is_active"}: return
    with _c() as c:
        c.execute(f"UPDATE task_categories SET {field}=%s WHERE id=%s", (value, cid))

def get_category_tasks(cid, active_only=True):
    with _c() as c:
        if active_only:
            return c.execute("SELECT * FROM category_tasks WHERE category_id=%s AND is_active=1 ORDER BY id", (cid,)).fetchall()
        return c.execute("SELECT * FROM category_tasks WHERE category_id=%s ORDER BY id", (cid,)).fetchall()

def get_category_task(tid):
    with _c() as c:
        return c.execute("SELECT * FROM category_tasks WHERE id=%s", (tid,)).fetchone()

def add_category_task(cid, name, price, rules, rules_en="", max_comp=0, task_type="other", per_user_limit=1, allowed_countries=""):
    code = generate_task_code()
    with _c() as c:
        cur = c.execute(
            "INSERT INTO category_tasks (category_id,name,price,rules,rules_en,max_completions,task_type,per_user_limit,is_active,task_code,allowed_countries) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s) RETURNING id",
            (cid, name, price, rules, rules_en, max_comp, task_type, per_user_limit, code, allowed_countries or "")
        )
        return cur.lastrowid

def delete_category_task(tid):
    with _c() as c:
        c.execute("DELETE FROM category_tasks WHERE id=%s", (tid,))
        c.execute("DELETE FROM category_task_fields WHERE task_id=%s", (tid,))
        c.execute("DELETE FROM category_task_completions WHERE task_id=%s", (tid,))

def update_category_task(tid, field, value):
    allowed = {"name","price","rules","rules_en","max_completions","is_active","completed_count","task_type","per_user_limit","allowed_countries","example_image_id","demo_video_id"}
    if field not in allowed: return
    with _c() as c:
        c.execute(f"UPDATE category_tasks SET {field}=%s WHERE id=%s", (value, tid))

def get_category_task_fields(task_id):
    with _c() as c:
        return c.execute("SELECT * FROM category_task_fields WHERE task_id=%s ORDER BY field_order", (task_id,)).fetchall()

def add_category_task_field(task_id, field_type, prompt_bn, prompt_en, order=None):
    with _c() as c:
        if order is None:
            r = c.execute("SELECT COUNT(*) FROM category_task_fields WHERE task_id=%s", (task_id,)).fetchone()
            order = _count(r)
        c.execute(
            "INSERT INTO category_task_fields (task_id,field_order,field_type,prompt_bn,prompt_en) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (task_id, order, field_type, prompt_bn, prompt_en)
        )

def delete_category_task_field(fid):
    with _c() as c:
        c.execute("DELETE FROM category_task_fields WHERE id=%s", (fid,))

def delete_all_category_task_fields(task_id):
    with _c() as c:
        c.execute("DELETE FROM category_task_fields WHERE task_id=%s", (task_id,))

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
    with _c() as c:
        try:
            c.execute("INSERT INTO category_task_completions (task_id,user_id,done_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (task_id, user_id, now()))
        except: pass
        c.execute("UPDATE category_tasks SET completed_count=completed_count+1 WHERE id=%s", (task_id,))
        t = c.execute("SELECT max_completions,completed_count FROM category_tasks WHERE id=%s", (task_id,)).fetchone()
        if t and t["max_completions"] > 0 and t["completed_count"] >= t["max_completions"]:
            c.execute("UPDATE category_tasks SET is_active=0 WHERE id=%s", (task_id,))

# ── Stats helper ──────────────────────────────────────────────────
def get_stats():
    with _c() as c:
        total   = _count(c.execute("SELECT COUNT(*) FROM users").fetchone())
        tot_bal = _count(c.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone())
        pg = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE status='pending'").fetchone())
        pw = _count(c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone())
        ag = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE status='approved'").fetchone())
        aw = _count(c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='approved'").fetchone())
    return total, tot_bal, pg, pw, ag, aw

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

def lang_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇧🇩 বাংলা", callback_data="lang_bn"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
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
async def check_ch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return []
    uid = update.effective_user.id
    channels = get_req_channels()
    missing = []
    for ch in channels:
        try:
            m = await ctx.bot.get_chat_member(ch["ch_id"], uid)
            if m.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                missing.append(ch)
        except TelegramError:
            missing.append(ch)
    return missing

async def join_prompt(update: Update, uid=None):
    lang_uid = uid or (update.effective_user.id if update.effective_user else 0)
    channels = get_req_channels()
    btns = [[InlineKeyboardButton(f"👉 {ch['ch_name']}", url=ch["ch_link"])] for ch in channels]
    btns.append([InlineKeyboardButton("✅ জয়েন করেছি — চেক করুন", callback_data="check_join")])
    await update.effective_message.reply_text(
        T(lang_uid, 'join_prompt'),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns)
    )

# ──────────────────────────── REFERRAL NOTIFY ────────────────────────────
async def fire_new_user_notify(ctx, uid, full_name, username, ref_by):
    uname = f"@{username}" if username else full_name
    ref_txt = f"\n🔗 *রেফার:* {ref_by}" if ref_by else ""
    await notify_full_admins(
        ctx.bot,
        f"🆕 *নতুন ইউজার জয়েন!*\n\n"
        f"👤 *নাম:* {full_name}\n"
        f"🆔 *ID:* `{uid}`\n"
        f"📛 *Username:* {uname}{ref_txt}"
    )
    if ref_by:
        try:
            comm_pct = get_commission_pct()
            await ctx.bot.send_message(ref_by,
                T(ref_by, 'new_user_ref', name=full_name, uname=uname, comm_pct=int(comm_pct) if comm_pct == int(comm_pct) else comm_pct),
                parse_mode="Markdown")
        except: pass

# ──────────────────────────── /start ────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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

    is_new = get_user(user.id) is None
    ensure_user(user.id, user.username, user.full_name, ref_by)

    u = get_user(user.id)
    if u["is_banned"]:
        await update.message.reply_text(T(user.id, 'banned'), parse_mode="Markdown")
        return

    missing = await check_ch(update, ctx)
    if missing:
        if is_new:
            ctx.user_data["pending_new"]   = True
            ctx.user_data["pending_uid"]   = user.id
            ctx.user_data["pending_name"]  = user.full_name
            ctx.user_data["pending_uname"] = user.username or ""
            ctx.user_data["pending_ref"]   = ref_by
        if deep_task_type:
            ctx.user_data["pending_deep_task_type"] = deep_task_type
            ctx.user_data["pending_deep_task_id"]   = deep_task_id
        await join_prompt(update, user.id)
        return

    if is_new:
        await fire_new_user_notify(ctx, user.id, user.full_name, user.username, ref_by)

    # New user or no language set → ask language first
    if not user_has_lang(user.id):
        ctx.user_data["state"] = "first_lang"
        if ref_by: ctx.user_data["start_ref"] = ref_by
        if deep_task_type:
            ctx.user_data["pending_deep_task_type"] = deep_task_type
            ctx.user_data["pending_deep_task_id"]   = deep_task_id
        await update.message.reply_text(
            "🌍 *ভাষা বেছে নিন / Choose your language:*",
            parse_mode="Markdown",
            reply_markup=lang_kb()
        )
        return

    # Language selected but country not yet picked → resume country selection
    if ctx.user_data.get("state") in ("first_lang", "first_country"):
        lang = get_lang(user.id)
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
        await _send_welcome(update, ctx, user.id)
        if deep_task_type == "ct":
            t = get_category_task(deep_task_id)
            uid2 = user.id; lang = get_lang(uid2)
            if t and t['is_active']:
                done  = categ_task_user_done(deep_task_id, uid2)
                slots = categ_task_slots_left(deep_task_id)
                if done:
                    await update.message.reply_text("✅ আপনি এই Task ইতিমধ্যে করেছেন!" if lang=='bn' else "✅ You already completed this task!", parse_mode="Markdown")
                elif slots == 0:
                    await update.message.reply_text("❌ এই Task পূর্ণ হয়ে গেছে!" if lang=='bn' else "❌ All slots are filled!", parse_mode="Markdown")
                else:
                    # ── Country restriction check (deep link) ──────────
                    _dl_ct_ac = (_rget(t, 'allowed_countries', '') or '').strip().upper()
                    if _dl_ct_ac and _dl_ct_ac not in ("0","ALL"):
                        _dl_ct_list = [x.strip() for x in _dl_ct_ac.split(",") if x.strip()]
                        _dl_u_cntry = get_user_country(uid2).upper()
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
                    msg   = (f"{ticon} *{t['name']}*\n\n💰 *পুরস্কার:* `{t['price']}$ USDT`\n{slots_txt}\n\n📜 *নিয়মাবলী:*\n{t['rules']}" if lang=='bn'
                             else f"{ticon} *{t['name']}*\n\n💰 *Reward:* `{t['price']}$ USDT`\n{slots_txt}\n\n📜 *Rules:*\n{t['rules']}")
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Task শুরু করুন" if lang=='bn' else "✅ Start Task", callback_data=f"ctask_submit_{deep_task_id}")],
                        [InlineKeyboardButton("❌ বাতিল" if lang=='bn' else "❌ Cancel", callback_data="task_cancel")],
                    ])
                    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
            else:
                await update.message.reply_text("❌ এই Task পাওয়া যায়নি বা inactive।" if lang=='bn' else "❌ Task not found or inactive.", parse_mode="Markdown")
            return
        if deep_task_type == "g":
            task = get_tg_task(deep_task_id)
            if task and task['is_active']:
                uid2 = user.id
                lang = get_lang(uid2)
                tid2 = task['id']
                _dl_tg_ac = (_rget(task, 'allowed_countries', '') or '').strip().upper()
                _dl_tg_deny = (
                    bool(_dl_tg_ac) and _dl_tg_ac not in ("0","ALL") and
                    get_user_country(uid2).upper() not in [x.strip() for x in _dl_tg_ac.split(",") if x.strip()]
                )
                if tg_task_user_done(tid2, uid2):
                    await update.message.reply_text("✅ আপনি এই Task ইতিমধ্যে সম্পন্ন করেছেন!" if lang=='bn' else "✅ You already completed this task!", parse_mode="Markdown")
                elif tg_task_slots_left(tid2) == 0:
                    await update.message.reply_text("❌ এই Task এর সব জায়গা পূর্ণ!" if lang=='bn' else "❌ All slots are filled!", parse_mode="Markdown")
                elif _dl_tg_deny:
                    await update.message.reply_text(
                        (f"❌ *এই Task আপনার দেশে উপলব্ধ নয়।*\n\n✅ অনুমোদিত দেশ: {fmt_allowed_countries(_dl_tg_ac)}" if lang=='bn'
                         else f"❌ *This task is not available in your country.*\n\n✅ Allowed countries: {fmt_allowed_countries(_dl_tg_ac)}"),
                        parse_mode="Markdown")
                elif _rget(task, 'new_joiners_only', 0):
                    try:
                        mbr = await ctx.bot.get_chat_member(task['tg_target'], uid2)
                        already = mbr.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
                    except Exception:
                        already = False
                    if already:
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
                    if lang == 'bn':
                        msg = (f"📡 *{task['name']}*\n\n💰 *পুরস্কার:* `{task['price']}$ USDT`\n"
                               f"📢 *Channel/Group:* {tg_name}\n{slots_txt}\n\n"
                               f"*কীভাবে করবেন:*\n1️⃣ নিচের বাটনে চেপে Channel/Group জয়েন করুন\n"
                               f"2️⃣ জয়েন হলে ✅ Check বাটনে চাপুন\n3️⃣ বট চেক করবে — জয়েন থাকলে টাকা পাবেন!")
                    else:
                        msg = (f"📡 *{task['name']}*\n\n💰 *Reward:* `{task['price']}$ USDT`\n"
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
                    tg_link = task['tg_target_link'] or f"https://t.me/{task['tg_target'].lstrip('@')}"
                    tg_name = task['tg_target_name'] or task['tg_target']
                    max_c = _rget(task, 'max_completions', 0)
                    done_c = _rget(task, 'completed_count', 0)
                    if max_c > 0:
                        slots_txt = (f"👥 *মোট:* `{max_c} জন` | *বাকি:* `{max(0,max_c-done_c)} জন`" if lang=='bn'
                                     else f"👥 *Total:* `{max_c}` | *Remaining:* `{max(0,max_c-done_c)}`")
                    else:
                        slots_txt = "👥 *মোট:* `Unlimited`" if lang=='bn' else "👥 *Total:* `Unlimited`"
                    if lang == 'bn':
                        msg = (f"📡 *{task['name']}*\n\n💰 *পুরস্কার:* `{task['price']}$ USDT`\n"
                               f"📢 *Channel/Group:* {tg_name}\n{slots_txt}\n\n"
                               f"*কীভাবে করবেন:*\n1️⃣ নিচের বাটনে চেপে Channel/Group জয়েন করুন\n"
                               f"2️⃣ জয়েন হলে ✅ Check বাটনে চাপুন\n3️⃣ বট চেক করবে — জয়েন থাকলে টাকা পাবেন!")
                    else:
                        msg = (f"📡 *{task['name']}*\n\n💰 *Reward:* `{task['price']}$ USDT`\n"
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
            task = get_task(deep_task_id)
            uid2 = user.id
            lang = get_lang(uid2)
            if task and task['is_active']:
                # ── Per-user limit check ──────────────────────────────
                per_user_limit = _rget(task, 'per_user_limit', 1)
                _dl_tcode      = _rget(task, 'task_code', '') or ''
                done_count     = get_user_task_submit_count(task['name'], uid2, _dl_tcode)
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
                    _dl_t_country = get_user_country(uid2).upper()
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
                if lang == 'bn':
                    rules_text = task["rules"] if task["rules"] else "কোনো বিশেষ নিয়ম নেই।"
                    msg = (
                        f"{ticon} *{task['name']}*{code_line}\n\n"
                        f"💰 *পুরস্কার:* `{task['price']}$ USDT`\n"
                        f"{slots_txt}\n\n"
                        f"📜 *নিয়মাবলী:*\n{rules_text}"
                    )
                else:
                    rules_text = (_rget(task, "rules_en") or task["rules"]) or "No special rules."
                    msg = (
                        f"{ticon} *{task['name']}*{code_line}\n\n"
                        f"💰 *Reward:* `{task['price']}$ USDT`\n"
                        f"{slots_txt}\n\n"
                        f"📜 *Rules:*\n{rules_text}"
                    )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Task শুরু করুন" if lang=='bn' else "✅ Start Task",
                                         callback_data=f"task_submit_{deep_task_id}")],
                    [InlineKeyboardButton("❌ বাতিল" if lang=='bn' else "❌ Cancel",
                                         callback_data="task_cancel")],
                ])
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
            else:
                await update.message.reply_text(
                    "❌ এই Task পাওয়া যায়নি বা inactive।" if lang=='bn' else "❌ Task not found or inactive.",
                    parse_mode="Markdown"
                )
        return

    await _send_welcome(update, ctx, user.id)

async def _send_welcome(update, ctx, uid):
    lang_key = 'welcome_msg_bn' if get_lang(uid) == 'bn' else 'welcome_msg_en'
    custom_w = get_setting(lang_key)
    welcome_text = custom_w if custom_w else T(uid, 'welcome')
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=main_kb(uid))
    else:
        await ctx.bot.send_message(uid, welcome_text, parse_mode="Markdown", reply_markup=main_kb(uid))

# ──────────────────────────── GUARD ────────────────────────────
async def guard(update, ctx):
    uid = update.effective_user.id if update.effective_user else None
    if not uid: return False
    u = get_user(uid)
    if u and u["is_banned"]:
        if update.message:
            await update.message.reply_text(T(uid, 'banned'), parse_mode="Markdown")
        elif update.callback_query:
            await update.callback_query.answer("🚫 Banned!", show_alert=True)
        return False
    missing = await check_ch(update, ctx)
    if missing:
        await join_prompt(update, uid)
        return False
    # ── last_seen আপডেট ───────────────────────────────────────────
    try:
        with _c() as c:
            c.execute("UPDATE users SET last_seen=%s WHERE user_id=%s", (now(), uid))
    except: pass
    return True

async def bot_off_reply(update, uid=0):
    msg = T(uid, 'bot_off')
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.answer("🔴 Bot is offline!", show_alert=True)

# ──────────────────────────── /cancel ────────────────────────────
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    ctx.user_data.clear()
    await update.message.reply_text(T(uid, 'cancelled'), parse_mode="Markdown", reply_markup=main_kb(uid))

# ──────────────────────────── MENU HANDLERS ────────────────────────────
async def show_profile(update, ctx):
    user = update.effective_user
    uid  = user.id
    bal  = get_bal(uid)
    uname = f"@{user.username}" if user.username else user.full_name
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
    bal   = get_bal(uid)
    owner = get_setting('support_owner') or '@inkamxbd'
    owner_e = owner.replace('_', '\\_')
    lang  = get_lang(uid)
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
    owner = get_setting('support_owner') or '@inkamxbd'
    bot_u = get_setting('support_bot') or '@gmail_sell_help_bot'
    owner_e = owner.replace('_', '\\_')
    bot_e   = bot_u.replace('_', '\\_')
    lang = get_lang(uid)
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

async def _notify_task_maxed(bot, name: str, code: str, type_label: str, done: int, max_c: int):
    """Admin-কে জানাও যে task-এর সব slot পূর্ণ হয়েছে এবং task বন্ধ হয়েছে।"""
    code_line = f"\n🔑 *Code:* `{code}`" if code else ""
    msg = (
        f"🏁 *Task সম্পূর্ণ হয়েছে — Slot পূর্ণ!*\n\n"
        f"📌 *Task:* {name}{code_line}\n"
        f"🏷️ *Type:* {type_label}\n"
        f"👥 *Completed:* `{done}/{max_c}` জন\n\n"
        f"✅ Task স্বয়ংক্রিয়ভাবে বন্ধ করে দেওয়া হয়েছে।"
    )
    await notify_full_admins(bot, msg)

async def _notify_task_done_ch(bot, user_id: int, task_name: str, task_code: str, price: float, task_type_label: str = ""):
    """যেকোনো task complete/approve হলে TASK_DONE_NOTIFY_CH চ্যানেলে notification পাঠাও।"""
    try:
        u = get_user(user_id)
        full_name = (u["full_name"] or "Unknown") if u else "Unknown"
        type_line = f"\n🏷️ *ধরন:* {task_type_label}" if task_type_label else ""
        code_line = f"\n🔑 *Task Code:* `{task_code}`" if task_code else ""
        msg = (
            f"✅ *Task সম্পন্ন হয়েছে!*\n\n"
            f"👤 *ইউজার:* {full_name}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📌 *Task:* {task_name}{code_line}{type_line}\n"
            f"💰 *আয়:* `{price}$ USDT`\n"
            f"🕐 *সময়:* {now()}"
        )
        await bot.send_message(TASK_DONE_NOTIFY_CH, msg, parse_mode="Markdown")
    except Exception as _e:
        log.warning(f"[TASK_DONE_NOTIFY] চ্যানেলে পাঠাতে ব্যর্থ: {_e}")

async def show_tasks(update, ctx):
    uid          = update.effective_user.id
    lang         = get_lang(uid)
    tasks        = get_tasks()
    tg_tasks     = get_tg_tasks()
    categories   = get_categories()

    if not tasks and not tg_tasks and not categories:
        await update.message.reply_text(T(uid, 'no_tasks'), parse_mode="Markdown",
                                        reply_markup=main_kb(uid))
        return

    btns = []

    # Regular tasks — per task one button
    if tasks:
        user_country = (get_user_country(uid) or "").upper()
        for task in tasks:
            # Per-user limit check
            per_user_limit = _rget(task, 'per_user_limit', 1)
            _st_tcode      = _rget(task, 'task_code', '') or ''
            done_count     = get_user_task_submit_count(task['name'], uid, _st_tcode)
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

    # Categories (one button per category, filter tasks user can still do)
    if categories:
        for cat in categories:
            cat_tasks = [t for t in get_category_tasks(cat['id'])
                         if not categ_task_user_done(t['id'], uid)]
            if cat_tasks:
                total_earn = sum(t['price'] for t in cat_tasks)
                task_count = len(cat_tasks)
                btns.append([InlineKeyboardButton(
                    f"🗂️ {cat['name']}  •  {task_count} Task  •  ${round(total_earn,2)}",
                    callback_data=f"categ_{cat['id']}"
                )])

    # Telegram tasks — ONE grouped button
    if tg_tasks:
        available_tgt = sum(
            1 for t in tg_tasks
            if not tg_task_user_done(t['id'], uid) and tg_task_slots_left(t['id']) != 0
        )
        tgt_label = (
            f"📲 Telegram Tasks ({available_tgt} available)"
            if get_lang(uid) == 'en'
            else f"📲 Telegram Tasks ({available_tgt}টি available)"
        )
        btns.append([InlineKeyboardButton(tgt_label, callback_data="tgt_list")])

    if not btns:
        await update.message.reply_text(
            "✅ আপনি সব available Task সম্পন্ন করেছেন!" if get_lang(uid) == 'bn'
            else "✅ You have completed all available tasks!",
            parse_mode="Markdown", reply_markup=main_kb(uid))
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
    lang = get_lang(uid)
    tg_tasks = get_tg_tasks()
    if not tg_tasks:
        await q.answer("❌ No Telegram tasks available!" if lang=='en' else "❌ কোনো Telegram Task নেই!", show_alert=True)
        return
    await q.answer()
    btns = []
    for t in tg_tasks:
        slots = tg_task_slots_left(t['id'])
        done  = tg_task_user_done(t['id'], uid)
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
    lang = get_lang(uid)
    cat  = get_category(cat_id)
    if not cat:
        await q.answer("❌ Category not found!", show_alert=True); return
    tasks = get_category_tasks(cat_id)
    if not tasks:
        await q.answer("❌ No tasks in this category!" if lang=='en' else "❌ এই Category তে কোনো Task নেই!", show_alert=True)
        return
    await q.answer()
    btns = []
    for t in tasks:
        slots = categ_task_slots_left(t['id'])
        done  = categ_task_user_done(t['id'], uid)
        fields = get_category_task_fields(t['id'])
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
    avail = sum(1 for t in tasks if not categ_task_user_done(t['id'], uid) and categ_task_slots_left(t['id']) != 0)
    if lang == 'en':
        title = f"🗂️ *{cat['name']}*\n\n📋 Total: `{total_tasks}` tasks  •  ✅ Available: `{avail}`\n\n👇 Choose a task below:"
    else:
        title = f"🗂️ *{cat['name']}*\n\n📋 মোট: `{total_tasks}` টি Task  •  ✅ পাওয়া যাচ্ছে: `{avail}` টি\n\n👇 নিচে থেকে একটি Task বেছে নিন:"
    await q.message.reply_text(title, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


async def show_category_task_rules(update, ctx, task):
    """Show rules/proof info for a category task"""
    uid  = update.callback_query.from_user.id
    lang = get_lang(uid)
    tid  = task['id']
    fields = get_category_task_fields(tid)
    slots  = categ_task_slots_left(tid)
    done   = categ_task_user_done(tid, uid)

    if done:
        per_user_limit = _rget(task, 'per_user_limit', 1)
        user_count     = get_user_categ_task_submit_count(task['name'], uid)
        lim_lbl = "∞" if per_user_limit == 0 else str(per_user_limit)
        if lang == 'en':
            done_msg = f"✅ You have already completed this task {user_count}/{lim_lbl} time(s). No more submissions allowed."
        else:
            done_msg = f"✅ আপনি এই Task ইতিমধ্যে {user_count}/{lim_lbl} বার সম্পন্ন করেছেন। আর জমা দেওয়া যাবে না।"
        await update.callback_query.answer(done_msg, show_alert=True); return
    if slots == 0:
        await update.callback_query.answer(
            "❌ All slots for this task are filled!" if lang=='en' else "❌ এই Task এর সব জায়গা পূর্ণ!",
            show_alert=True
        ); return

    # ── Country restriction check ─────────────────────────────────
    _cat_allowed = (_rget(task, 'allowed_countries', '') or '').strip().upper()
    if _cat_allowed and _cat_allowed not in ("0","ALL"):
        _cat_allowed_list = [x.strip() for x in _cat_allowed.split(",") if x.strip()]
        _cat_u_country = get_user_country(uid).upper()
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
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(_ask, parse_mode="Markdown", reply_markup=country_kb())
            return
        if _cat_u_country not in _cat_allowed_list:
            _deny = (
                f"❌ *এই Task আপনার দেশে উপলব্ধ নয়।*\n\n✅ অনুমোদিত দেশ: {fmt_allowed_countries(_cat_allowed)}"
                if lang == 'bn' else
                f"❌ *This task is not available in your country.*\n\n✅ Allowed countries: {fmt_allowed_countries(_cat_allowed)}"
            )
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(_deny, parse_mode="Markdown")
            return

    await update.callback_query.answer()

    if lang == 'en':
        rules_text  = (task["rules_en"] if task["rules_en"] else translate_to_en(task["rules"])) if task["rules"] else "No special rules."
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
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(sub_btn,  callback_data=f"ctask_submit_{tid}"),
        InlineKeyboardButton(cancel_b, callback_data="task_cancel"),
    ]])
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
        f"{ticon} *{task['name']}*\n"
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
    # ── Task Media (example image / demo video) ────────────────────
    _ct_ex_img   = _rget(task, 'example_image_id', '') or ''
    _ct_demo_vid = _rget(task, 'demo_video_id',   '') or ''
    if _ct_ex_img:
        try:
            _cap = "📷 Example Image" if lang == 'en' else "📷 উদাহরণ ছবি"
            await update.effective_message.reply_photo(_ct_ex_img, caption=_cap)
        except: pass
    if _ct_demo_vid:
        try:
            _cap = "🎬 Demo Video" if lang == 'en' else "🎬 ডেমো ভিডিও"
            await update.effective_message.reply_video(_ct_demo_vid, caption=_cap)
        except: pass

async def start_category_task_collect(update, ctx, task_id):
    """Start collecting proof for a category task"""
    uid    = update.callback_query.from_user.id
    task   = get_category_task(task_id)
    fields = get_category_task_fields(task_id)
    q      = update.callback_query
    # ── Point block check ─────────────────────────────────────────
    _blk, _blk_dt = is_task_blocked(uid)
    if _blk and _blk_dt:
        _until_str = _blk_dt.astimezone(pytz.timezone("Asia/Dhaka")).strftime("%d %b %Y, %I:%M %p")
        await q.answer(T(uid, 'task_blocked_msg', until=_until_str)[:200], show_alert=True)
        return
    await q.answer()

    ctx.user_data["state"]          = "categ_task_collect"
    ctx.user_data["categ_task_id"]  = task_id
    ctx.user_data["task_name"]      = task["name"]
    ctx.user_data["task_price"]     = task["price"]
    ctx.user_data["task_fields"]    = [dict(f) for f in fields]
    ctx.user_data["field_idx"]      = 0
    ctx.user_data["collected"]      = []

    if not fields:
        lang = get_lang(uid)
        prompt = "📝 *Send your info:*\n\n❌ To cancel, type /cancel" if lang == 'en' else "📝 *আপনার তথ্য পাঠান:*\n\n❌ বাতিল করতে /cancel লিখুন"
        await q.message.reply_text(prompt, parse_mode="Markdown",
                                   reply_markup=ReplyKeyboardMarkup([[T(uid,'cancel_btn')]], resize_keyboard=True))
        return
    await _ask_next_field(update, ctx, uid, via_callback=True)


async def _finalize_category_task_submission(update, ctx, uid, via_callback=False):
    """Finalize and send category task submission to admin"""
    user      = update.effective_user
    uname     = f"@{user.username}" if user.username else user.full_name
    task_name = ctx.user_data.get("task_name", "Task")
    price     = float(ctx.user_data.get("task_price", 0.10))
    collected = ctx.user_data.get("collected", [])
    task_id   = ctx.user_data.get("categ_task_id", 0)

    lang = get_lang(uid)
    summary_lines  = []
    photo_file_ids = []
    _cat_submitted_uids = []
    for i, item in enumerate(collected, 1):
        if item["type"] == "photo":
            if item["value"]:
                photo_file_ids.append(item["value"])
                if item.get("unique_id"):
                    _cat_submitted_uids.append(item["unique_id"])
                summary_lines.append(f"📷 *{item['prompt_bn']}:* [Photo #{len(photo_file_ids)}]")
            else:
                summary_lines.append(f"📷 *{item['prompt_bn']}:* (Skipped)")
        else:
            val = item["value"] or ""
            summary_lines.append(f"📝 *{item['prompt_bn']}:*\n`{val}`")

    # ── Duplicate photo detection ──────────────────────────────────
    if _cat_submitted_uids:
        with _c() as c:
            _cat_existing_rows = c.execute(
                "SELECT photo_unique_ids FROM gmails WHERE photo_unique_ids IS NOT NULL AND photo_unique_ids != ''"
            ).fetchall()
        _cat_all_existing = set()
        for _r in _cat_existing_rows:
            for _s in (_r[0] or "").split(","):
                _s = _s.strip()
                if _s: _cat_all_existing.add(_s)
        if any(_u in _cat_all_existing for _u in _cat_submitted_uids):
            _new_pts = add_points(uid, -15)
            _cat_lang = get_lang(uid)
            _dup_notice = (
                "⚠️ *Duplicate Photo Detected!*\n\n"
                "🔴 This photo has been used in a previous submission.\n"
                f"📉 *15 points deducted.* Current: `{_new_pts}/100`"
                if _cat_lang == 'en' else
                "⚠️ *ডুপ্লিকেট ছবি শনাক্ত হয়েছে!*\n\n"
                "🔴 এই ছবি আগে অন্য Submission এ ব্যবহার করা হয়েছে।\n"
                f"📉 *১৫ পয়েন্ট কাটা হয়েছে।* বর্তমান: `{_new_pts}/100`"
            )
            ctx.user_data.clear()
            if update.message:
                await update.message.reply_text(_dup_notice, parse_mode="Markdown", reply_markup=main_kb(uid))
            else:
                await ctx.bot.send_message(uid, _dup_notice, parse_mode="Markdown", reply_markup=main_kb(uid))
            return

    summary_txt = "\n".join(summary_lines) if summary_lines else "_No data_"
    gmail_text  = _json.dumps(collected, ensure_ascii=False)

    # Mark as done (one-per-user) and save to gmails table for admin approve/reject
    ttype_obj = get_category_task(task_id)
    ttype_ins = _rget(ttype_obj, 'task_type', 'other') if ttype_obj else 'other'
    ttype_icon = task_type_icon(ttype_ins)
    _cat_tcode = _rget(ttype_obj, 'task_code', '') if ttype_obj else ''
    with _c() as c:
        cur = c.execute(
            "INSERT INTO gmails (user_id,gmail_text,task_name,amount,submitted_at,task_type,task_code) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (uid, gmail_text, f"[CAT] {task_name}", price, now(), ttype_ins, _cat_tcode)
        )
        gid = cur.lastrowid
    # ── photo unique_ids সেভ করো ──────────────────────────────────
    if _cat_submitted_uids:
        try:
            with _c() as c:
                c.execute("UPDATE gmails SET photo_unique_ids=%s WHERE id=%s",
                          (",".join(_cat_submitted_uids), gid))
        except: pass

    # Mark user as done for this category task
    categ_task_mark_done(task_id, uid)
    # Max completions check — notify admin if slot পূর্ণ হয়েছে
    _ct_after = get_category_task(task_id)
    if _ct_after and _ct_after["max_completions"] > 0 and not _ct_after["is_active"]:
        asyncio.create_task(_notify_task_maxed(
            ctx.bot, _ct_after["name"], _rget(_ct_after,"task_code","") or "",
            "Category Task", _ct_after["completed_count"], _ct_after["max_completions"]
        ))
    _u_ctry_raw = (get_user_country(uid) or "").upper().strip()
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

    a_mid, c_mid = 0, 0
    try:
        am = await ctx.bot.send_message(ADMIN_ID, msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
        a_mid = am.message_id
        for fid in photo_file_ids:
            await ctx.bot.send_photo(ADMIN_ID, fid, caption=f"📷 Sub #{gid}")
    except Exception as e: log.warning(f"Admin fwd fail: {e}")

    # Sub-admins
    for adm_row in get_all_admins():
        try:
            await ctx.bot.send_message(adm_row["user_id"], msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
            for fid in photo_file_ids:
                await ctx.bot.send_photo(adm_row["user_id"], fid, caption=f"📷 Sub #{gid}")
        except Exception as e: log.warning(f"Sub-admin fwd fail {adm_row['user_id']}: {e}")

    try:
        cm = await ctx.bot.send_message(get_gmail_ch_id(), msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
        c_mid = cm.message_id
        for fid in photo_file_ids:
            await ctx.bot.send_photo(get_gmail_ch_id(), fid, caption=f"📷 Sub #{gid}")
    except Exception as e: log.warning(f"Channel fwd fail: {e}")

    with _c() as c:
        c.execute("UPDATE gmails SET admin_msg_id=%s,channel_msg_id=%s WHERE id=%s", (a_mid, c_mid, gid))

    for k in ("state","task_price","task_name","categ_task_id","task_fields","field_idx","collected"):
        ctx.user_data.pop(k, None)

    if update.message and not via_callback:
        await update.message.reply_text(T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=main_kb(uid))
    else:
        await ctx.bot.send_message(uid, T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=main_kb(uid))


async def show_tg_task(update, ctx, task):
    """Show TG Join task info with Join + Check buttons"""
    q   = update.callback_query
    uid = q.from_user.id
    lang = get_lang(uid)

    tid   = task['id']
    slots = tg_task_slots_left(tid)

    # Already done%s
    if tg_task_user_done(tid, uid):
        await q.answer("✅ আপনি এই Task ইতিমধ্যে সম্পন্ন করেছেন!" if lang=='bn' else "✅ You already completed this task!", show_alert=True)
        return

    # Slots finished?
    if slots == 0:
        await q.answer("❌ এই Task এর সব জায়গা পূর্ণ হয়ে গেছে!" if lang=='bn' else "❌ All slots for this task are filled!", show_alert=True)
        return

    # New joiners only check
    if _rget(task, 'new_joiners_only', 0):
        try:
            member = await ctx.bot.get_chat_member(task['tg_target'], uid)
            already = member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        except Exception:
            already = False
        if already:
            await q.answer()
            await q.message.reply_text(T(uid, 'new_joiners_only_reject'), parse_mode="Markdown")
            return

    await q.answer()

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
    if lang == 'bn':
        msg = (
            f"📡 *{task['name']}*\n"
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
            f"📡 *{task['name']}*\n"
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
    lang = get_lang(uid)
    task = get_tg_task(tid)

    if not task or not task['is_active']:
        await q.answer("❌ Task not found or inactive!" if lang=='en' else "❌ Task পাওয়া যায়নি বা বন্ধ আছে!", show_alert=True); return

    # Already done?
    if tg_task_user_done(tid, uid):
        await q.answer("✅ ইতিমধ্যে সম্পন্ন করেছেন!" if lang=='bn' else "✅ Already completed!", show_alert=True); return

    # Slots finished?
    slots = tg_task_slots_left(tid)
    if slots == 0:
        await q.answer("❌ সব জায়গা পূর্ণ!" if lang=='bn' else "❌ All slots filled!", show_alert=True); return

    # ── Country restriction check ────────────────────────────────
    _allowed_c = (_rget(task, 'allowed_countries', '') or '').strip().upper()
    if _allowed_c and _allowed_c not in ("0", "ALL"):
        _allowed_list = [x.strip() for x in _allowed_c.split(",") if x.strip()]
        _user_country = get_user_country(uid).upper()
        if not _user_country:
            # User hasn't set country yet — ask them
            await q.answer()
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
            await q.answer()
            await q.message.reply_text(_deny, parse_mode="Markdown")
            return

    await q.answer("⏳ চেক করা হচ্ছে..." if lang=='bn' else "⏳ Checking...", show_alert=False)

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

    # ✅ Joined! Auto-pay
    price = task['price']
    tg_task_mark_done(tid, uid)
    add_bal(uid, price)
    # Max completions check — notify admin if slot পূর্ণ হয়েছে
    _tg_after = get_tg_task(tid)
    if _tg_after and _tg_after["max_completions"] > 0 and not _tg_after["is_active"]:
        asyncio.create_task(_notify_task_maxed(
            ctx.bot, _tg_after["name"], _rget(_tg_after,"task_code","") or "",
            "TG Join Task", _tg_after["completed_count"], _tg_after["max_completions"]
        ))

    # Commission to referrer
    ref = get_referred_by(uid)
    if ref:
        comm_pct = get_commission_pct()
        comm = round(price * comm_pct / 100.0, 4)
        add_bal(ref, comm)
        try:
            ru = get_user(uid)
            rname = (ru["full_name"] or ru["username"] or "Unknown") if ru else "Unknown"
            comm_pct_d = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
            await ctx.bot.send_message(ref,
                T(ref, 'commission_msg', name=rname, price=price, comm=comm, comm_pct=comm_pct_d),
                parse_mode="Markdown")
        except: pass

    # Notify user
    success_msg = (
        f"✅ *Task সম্পন্ন! অভিনন্দন!*\n\n"
        f"📡 *Task:* {task['name']}\n"
        f"💰 *`{price}$ USDT` আপনার একাউন্টে যোগ হয়েছে!*\n\n"
        f"💎 ধন্যবাদ!"
        if lang=='bn' else
        f"✅ *Task completed! Congratulations!*\n\n"
        f"📡 *Task:* {task['name']}\n"
        f"💰 *`{price}$ USDT` added to your account!*\n\n"
        f"💎 Thank you!"
    )
    await q.message.reply_text(success_msg, parse_mode="Markdown", reply_markup=main_kb(uid))
    asyncio.create_task(_lb_refresh_now(ctx.bot))

    asyncio.create_task(_notify_task_done_ch(
        ctx.bot, uid, task['name'],
        _rget(task, 'task_code', '') or '', price, "📡 TG Join Task"
    ))

async def show_refer(update, ctx):
    uid     = update.effective_user.id
    bot_info = await ctx.bot.get_me()
    link    = f"https://t.me/{bot_info.username}%sstart=ref_{uid}"
    comm_pct = get_commission_pct()
    comm_pct_display = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
    with _c() as c:
        referrals  = _count(c.execute("SELECT COUNT(*) FROM users WHERE referred_by=%s", (uid,)).fetchone())
        gmail_earn = _fval(c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM gmails WHERE status='approved' AND user_id IN "
            "(SELECT user_id FROM users WHERE referred_by=%s)", (uid,)
        ).fetchone(), 0) or 0
    commission = round(gmail_earn * comm_pct / 100.0, 4)
    owner = get_setting('support_owner') or '@inkamxbd'
    owner_e = owner.replace('_', '\\_')
    lang = get_lang(uid)

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
    bal   = get_bal(uid)
    min_w = get_min_withdraw()
    fee   = get_wd_fee()
    lang  = get_lang(uid)
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
        reply_markup=lang_kb()
    )

# ──────────────────────────── TASK SUBMIT FLOW ────────────────────────────
async def show_task_rules(update, ctx, task):
    uid      = update.effective_user.id
    lang     = get_lang(uid)
    # ── Point block check ─────────────────────────────────────────
    _blk, _blk_dt = is_task_blocked(uid)
    if _blk and _blk_dt:
        _until_str = _blk_dt.astimezone(pytz.timezone("Asia/Dhaka")).strftime("%d %b %Y, %I:%M %p")
        _msg = T(uid, 'task_blocked_msg', until=_until_str)
        if update.callback_query:
            await update.callback_query.answer(_msg[:200], show_alert=True)
        else:
            await update.effective_message.reply_text(_msg, parse_mode="Markdown")
        return
    # ── Per-user limit check ─────────────────────────────────────
    per_user_limit = _rget(task, 'per_user_limit', 1)
    _str_tcode     = _rget(task, 'task_code', '') or ''
    done_count     = get_user_task_submit_count(task['name'], uid, _str_tcode)
    lim_lbl        = "∞" if per_user_limit == 0 else str(per_user_limit)
    if per_user_limit != 0 and done_count >= per_user_limit:
        if lang == 'en':
            msg = f"✅ You have already completed this task {done_count}/{lim_lbl} time(s). No more submissions allowed."
        else:
            msg = f"✅ আপনি এই Task ইতিমধ্যে {done_count}/{lim_lbl} বার সম্পন্ন করেছেন। আর জমা দেওয়া যাবে না।"
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.effective_message.reply_text(msg, parse_mode="Markdown")
        return
    fields   = get_task_fields(task['id'])
    sub_btn  = T(uid, 'submit_btn')
    cancel_b = T(uid, 'cancel_inline')
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(sub_btn,    callback_data=f"task_submit_{task['id']}"),
        InlineKeyboardButton(cancel_b,   callback_data="task_cancel"),
    ]])
    if lang == 'en':
        rules_text  = (task["rules_en"] if task["rules_en"] else translate_to_en(task["rules"])) if task["rules"] else "No special rules."
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
    # ── Task Media (example image / demo video) ────────────────────
    _ex_img   = _rget(task, 'example_image_id', '') or ''
    _demo_vid = _rget(task, 'demo_video_id',   '') or ''
    if _ex_img:
        try:
            _cap = "📷 Example Image" if lang == 'en' else "📷 উদাহরণ ছবি"
            await update.effective_message.reply_photo(_ex_img, caption=_cap)
        except: pass
    if _demo_vid:
        try:
            _cap = "🎬 Demo Video" if lang == 'en' else "🎬 ডেমো ভিডিও"
            await update.effective_message.reply_video(_demo_vid, caption=_cap)
        except: pass

async def start_task_collect(update, ctx, task_id):
    uid    = update.callback_query.from_user.id
    task   = get_task(task_id)
    fields = get_task_fields(task_id)
    q      = update.callback_query
    await q.answer()

    ctx.user_data["state"]         = "task_collect"
    ctx.user_data["task_id"]       = task_id
    ctx.user_data["task_name"]     = task["name"]
    ctx.user_data["task_price"]    = task["price"]
    ctx.user_data["task_fields"]   = [dict(f) for f in fields]
    ctx.user_data["field_idx"]     = 0
    ctx.user_data["collected"]     = []

    if not fields:
        # No custom fields — ask generic info
        lang = get_lang(uid)
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
    lang  = get_lang(uid)
    prompt = field["prompt_en"] if lang == 'en' else field["prompt_bn"]
    step_txt = f"({idx+1}/{total})" if total > 1 else ""

    if field["field_type"] == "photo":
        msg = T(uid, 'field_photo_ask', prompt=f"{prompt} {step_txt}")
        skip_btn = T(uid, 'skip_btn')
        kb = ReplyKeyboardMarkup([[skip_btn], [T(uid,'cancel_btn')]], resize_keyboard=True)
    else:
        msg = T(uid, 'field_ask', prompt=f"{prompt} {step_txt}")
        kb = ReplyKeyboardMarkup([[T(uid,'cancel_btn')]], resize_keyboard=True)

    if via_callback:
        await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)

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

    field = fields[idx]

    # Check skip for photo field
    skip_bn = T(uid, 'skip_btn')
    if txt in (skip_bn, "⏭ Skip করুন", "⏭ Skip") and field["field_type"] == "photo":
        ctx.user_data["collected"].append({
            "type": "photo", "prompt_bn": field["prompt_bn"],
            "prompt_en": field["prompt_en"], "value": None
        })
    else:
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
    fields = ctx.user_data.get("task_fields", [])
    idx    = ctx.user_data.get("field_idx", 0)
    total  = len(fields)

    if not fields: return

    field = fields[idx]
    if field["field_type"] != "photo":
        lang = get_lang(uid)
        await update.message.reply_text(
            "❌ Send text here, not a photo." if lang == 'en' else "❌ এখানে ছবি নয়, টেক্সট পাঠান।",
            parse_mode="Markdown")
        return

    file_id        = update.message.photo[-1].file_id
    file_unique_id = update.message.photo[-1].file_unique_id
    ctx.user_data["collected"].append({
        "type": "photo", "prompt_bn": field["prompt_bn"],
        "prompt_en": field["prompt_en"], "value": file_id,
        "unique_id": file_unique_id
    })
    ctx.user_data["field_idx"] = idx + 1

    if idx + 1 < total:
        await _ask_next_field(update, ctx, uid)
    else:
        await _finalize_task_submission(update, ctx, uid)

async def _finalize_task_submission(update, ctx, uid, via_callback=False):
    user      = update.effective_user
    uname     = f"@{user.username}" if user.username else user.full_name
    task_name = ctx.user_data.get("task_name", "Task")
    price     = float(ctx.user_data.get("task_price", get_setting("gmail_price") or "0.10"))
    collected = ctx.user_data.get("collected", [])

    # Build text summary
    lang          = get_lang(uid)
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
                summary_lines.append(f"📷 *{prompt_lbl}:* [Photo #{len(photo_file_ids)}]" if lang == 'en' else f"📷 *{prompt_lbl}:* [ছবি #{len(photo_file_ids)}]")
            else:
                summary_lines.append(f"📷 *{prompt_lbl}:* (Skipped)" if lang == 'en' else f"📷 *{prompt_lbl}:* (Skip করা হয়েছে)")
        else:
            val = item["value"] or ""
            summary_lines.append(f"📝 *{prompt_lbl}:*\n`{val}`")

    # ── Duplicate photo detection ──────────────────────────────────
    if _submitted_unique_ids:
        with _c() as c:
            _existing_rows = c.execute(
                "SELECT photo_unique_ids FROM gmails WHERE photo_unique_ids IS NOT NULL AND photo_unique_ids != ''"
            ).fetchall()
        _all_existing = set()
        for _row in _existing_rows:
            for _uid_str in (_row.get("photo_unique_ids") or "").split(","):
                _uid_str = _uid_str.strip()
                if _uid_str:
                    _all_existing.add(_uid_str)
        if any(_u in _all_existing for _u in _submitted_unique_ids):
            _new_pts = add_points(uid, -15)
            _dup_notice = (
                "⚠️ *Duplicate Photo Detected!*\n\n"
                "🔴 This photo has been used in a previous submission.\n"
                f"📉 *15 points deducted.* Current: `{_new_pts}/100`"
                if lang == 'en' else
                "⚠️ *ডুপ্লিকেট ছবি শনাক্ত হয়েছে!*\n\n"
                "🔴 এই ছবি আগে অন্য Submission এ ব্যবহার করা হয়েছে।\n"
                f"📉 *১৫ পয়েন্ট কাটা হয়েছে।* বর্তমান: `{_new_pts}/100`"
            )
            ctx.user_data.clear()
            if update.message:
                await update.message.reply_text(_dup_notice, parse_mode="Markdown", reply_markup=main_kb(uid))
            else:
                await ctx.bot.send_message(uid, _dup_notice, parse_mode="Markdown", reply_markup=main_kb(uid))
            return

    summary_txt = "\n".join(summary_lines) if summary_lines else ("_No data_" if lang == 'en' else "_কোনো তথ্য নেই_")
    gmail_text  = _json.dumps(collected, ensure_ascii=False)

    _task_id_ins = ctx.user_data.get("task_id", 0)
    _t_obj       = get_task(_task_id_ins)
    _ttype_ins   = _rget(_t_obj, 'task_type', 'other') if _t_obj else 'other'
    _t_icon      = task_type_icon(_ttype_ins)
    _reg_tcode   = _rget(_t_obj, 'task_code', '') if _t_obj else ''
    with _c() as c:
        cur = c.execute(
            "INSERT INTO gmails (user_id,gmail_text,task_name,amount,submitted_at,task_type,task_code) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
            (uid, gmail_text, task_name, price, now(), _ttype_ins, _reg_tcode)
        )
        gid = cur.lastrowid
        # conflict হলে lastrowid=None — existing row-এর id আনো
        if gid is None:
            row = c.execute(
                "SELECT id FROM gmails WHERE user_id=%s AND task_code=%s ORDER BY submitted_at DESC LIMIT 1",
                (uid, _reg_tcode)
            ).fetchone()
            gid = row['id'] if row else None
    # ── photo unique_ids সেভ করো ──────────────────────────────────
    if _submitted_unique_ids and gid:
        try:
            with _c() as c:
                c.execute("UPDATE gmails SET photo_unique_ids=%s WHERE id=%s",
                          (",".join(_submitted_unique_ids), gid))
        except: pass
    _u_ctry_raw2 = (get_user_country(uid) or "").upper().strip()
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

    a_mid, c_mid = 0, 0
    # Send to main admin
    try:
        am = await ctx.bot.send_message(ADMIN_ID, msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
        a_mid = am.message_id
        for fid in photo_file_ids:
            await ctx.bot.send_photo(ADMIN_ID, fid, caption=f"📷 Sub #{gid} এর ছবি")
    except Exception as e: log.warning(f"Admin fwd fail: {e}")

    # Send to sub-admins
    for adm_row in get_all_admins():
        try:
            await ctx.bot.send_message(adm_row["user_id"], msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
            for fid in photo_file_ids:
                await ctx.bot.send_photo(adm_row["user_id"], fid, caption=f"📷 Sub #{gid} এর ছবি")
        except Exception as e: log.warning(f"Sub-admin fwd fail {adm_row['user_id']}: {e}")

    # Send to channel
    try:
        cm = await ctx.bot.send_message(get_gmail_ch_id(), msg_txt, parse_mode="Markdown", reply_markup=g_kb(gid))
        c_mid = cm.message_id
        for fid in photo_file_ids:
            await ctx.bot.send_photo(get_gmail_ch_id(), fid, caption=f"📷 Sub #{gid} এর ছবি")
    except Exception as e: log.warning(f"Channel fwd fail: {e}")

    with _c() as c:
        c.execute("UPDATE gmails SET admin_msg_id=%s,channel_msg_id=%s WHERE id=%s", (a_mid, c_mid, gid))

    for k in ("state","task_price","task_name","task_id","task_fields","field_idx","collected"):
        ctx.user_data.pop(k, None)

    send_fn = update.message.reply_text if (update.message and not via_callback) else None
    if send_fn:
        await send_fn(T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=main_kb(uid))
    else:
        await ctx.bot.send_message(uid, T(uid, 'submit_ok'), parse_mode="Markdown", reply_markup=main_kb(uid))

# ──────────────────────────── WITHDRAW FLOW ────────────────────────────
async def wd_ask_addr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    await q.answer()
    await q.message.reply_text(
        T(uid, 'wd_enter_addr'), parse_mode="Markdown", reply_markup=cancel_kb(uid)
    )
    ctx.user_data["state"] = "wd_addr"

async def receive_wd_addr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    address = update.message.text.strip()
    lang    = get_lang(uid)

    # ── Duplicate address check: সেম address অন্য user আগে use করেছে কিনা ──
    with _c() as c:
        addr_owner = c.execute(
            "SELECT DISTINCT user_id FROM withdrawals WHERE address=%s AND user_id!=%s",
            (address, uid)
        ).fetchone()
    if addr_owner:
        owner_id = addr_owner['user_id']
        # ── শুধু নতুন user কে ব্যান করো ──
        ban_user(uid, 1)
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
        uname_str = f"@{user.username}" if user.username else user.full_name
        admin_msg = (
            f"🚨 *Duplicate Wallet — Auto Ban*\n\n"
            f"👤 *Banned User:* [{uname_str}](tg://user%sid={uid}) (`{uid}`)\n"
            f"🔗 *Address:* `{address}`\n"
            f"⚠️ *এই Address আগে ব্যবহার করেছে:* `{owner_id}`\n\n"
            f"✅ নতুন User auto-ban হয়েছে।"
        )
        await notify_full_admins(ctx.bot, admin_msg)
        return

    ctx.user_data["wd_addr"] = address
    ctx.user_data["state"]   = "wd_amt"
    bal   = get_bal(uid)
    fee   = get_wd_fee()
    min_w = get_min_withdraw()
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
    min_w = get_min_withdraw()
    fee   = get_wd_fee()
    lang  = get_lang(uid)
    if amt < min_w:
        min_msg = (f"❌ *Minimum withdrawal:* `{min_w}$ USDT`" if lang=='en'
                   else f"❌ *মিনিমাম উইথড্র:* `{min_w}$ USDT`")
        await update.message.reply_text(min_msg, parse_mode="Markdown", reply_markup=cancel_kb(uid))
        return
    bal = get_bal(uid)
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
    fee     = get_wd_fee()
    net_amt = round(amt - fee, 4)
    lang    = get_lang(uid)
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
    address = ctx.user_data.get("wd_addr", "")
    amt     = ctx.user_data.get("wd_amt", 0.0)
    fee     = get_wd_fee()
    net_amt = round(amt - fee, 4)
    lang    = get_lang(uid)

    # ── Security Check 1: pending/processing withdrawal আছে কিনা দেখো ──
    with _c() as c:
        existing = c.execute(
            "SELECT id FROM withdrawals WHERE user_id=%s AND status IN ('pending','processing','gas_pending')",
            (uid,)
        ).fetchone()
    if existing:
        err_msg = (
            "⚠️ *You already have a pending withdrawal.*\n\nPlease wait for it to be processed before submitting a new one."
            if lang == 'en' else
            "⚠️ *আপনার একটি Withdraw ইতিমধ্যে প্রক্রিয়াধীন আছে।*\n\nনতুন Withdraw করার আগে এটি সম্পন্ন হতে দিন।"
        )
        for k in ("state", "wd_addr", "wd_amt"):
            ctx.user_data.pop(k, None)
        await ctx.bot.send_message(uid, err_msg, parse_mode="Markdown", reply_markup=main_kb(uid))
        return

    # ── Security Check 2: Atomic balance deduction — balance না থাকলে block ──
    if not safe_ded_bal(uid, amt):
        err_msg = (
            f"❌ *Insufficient balance!*\n\n💰 *Your balance:* `${get_bal(uid):.4f} USDT`\n💸 *Requested:* `${amt:.4f} USDT`"
            if lang == 'en' else
            f"❌ *পর্যাপ্ত ব্যালান্স নেই!*\n\n💰 *আপনার ব্যালান্স:* `${get_bal(uid):.4f} USDT`\n💸 *চাইছেন:* `${amt:.4f} USDT`"
        )
        for k in ("state", "wd_addr", "wd_amt"):
            ctx.user_data.pop(k, None)
        await ctx.bot.send_message(uid, err_msg, parse_mode="Markdown", reply_markup=main_kb(uid))
        return

    import random as _random
    _sched_sec = _random.randint(120, 300)   # ২ থেকে ৫ মিনিট (120–300 সেকেন্ড)
    _sched_at  = (datetime.now() + timedelta(seconds=_sched_sec)).strftime("%Y-%m-%d %H:%M:%S")
    with _c() as c:
        cur = c.execute(
            "INSERT INTO withdrawals (user_id,amount,fee,address,requested_at,scheduled_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (uid, amt, fee, address, now(), _sched_at)
        )
        wid = cur.lastrowid

    with _c() as c:
        c.execute("UPDATE withdrawals SET admin_msg_id=0,channel_msg_id=0 WHERE id=%s", (wid,))

    for k in ("state","wd_addr","wd_amt"):
        ctx.user_data.pop(k, None)

    lang = get_lang(uid)
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
    await ctx.bot.send_message(uid, confirm_msg, parse_mode="Markdown", reply_markup=main_kb(uid))

# ──────────────────────────── HISTORY ────────────────────────────
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await guard(update, ctx): return
    with _c() as c:
        gmails = c.execute(
            "SELECT task_name,amount,status,submitted_at FROM gmails WHERE user_id=%s ORDER BY id DESC LIMIT 10", (uid,)
        ).fetchall()
        wds = c.execute(
            "SELECT amount,status,requested_at FROM withdrawals WHERE user_id=%s ORDER BY id DESC LIMIT 10", (uid,)
        ).fetchall()
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
    pts              = get_points(uid)
    blk, blk_dt     = is_task_blocked(uid)
    lang             = get_lang(uid)
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
    """PostgreSQL mode — Neon এ সব data স্থায়ীভাবে থাকে, restore দরকার নেই।"""
    log.info("[AUTO-RESTORE] PostgreSQL mode — Neon DB তে সব data আছে। Restore skip।")
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
    """PostgreSQL — file backup disabled. Stats only."""
    return  # PostgreSQL — no SQLite file to send
    try:
        size_kb = round(os.path.getsize(DATABASE_URL) / 1024, 1)
        caption = (
            f"🗄 *{label}*\n\n"
            f"📁 *ফাইল:* `data.db`\n"
            f"📦 *সাইজ:* `{size_kb} KB`\n"
            f"🕐 *সময়:* `{now()}`\n\n"
            f"✅ সব user এর balance, referral, history এখানে আছে।\n"
            f"♻️ Bot নিজেই auto-restore করবে — manual কিছু লাগবে না।"
        )
        targets = [ADMIN_ID] + BACKUP_CHANNELS
        saved_fid = False
        for target in targets:
            # ── পুরনো backup message সরাও ──────────────────────────────
            old_mid = _backup_msg_load(target)
            if old_mid:
                deleted = False
                try:
                    await bot.delete_message(chat_id=target, message_id=old_mid)
                    deleted = True
                except Exception:
                    pass
                # Delete permission না থাকলে caption edit করে জানাও
                if not deleted:
                    try:
                        await bot.edit_message_caption(
                            chat_id=target, message_id=old_mid,
                            caption="🗑 *(পুরানো backup — নতুনটি নিচে দেখুন)*",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
            try:
                with open(DATABASE_URL, "rb") as f:
                    msg = await bot.send_document(
                        target, document=f,
                        filename=f"data_{now()[:10]}.db",
                        caption=caption, parse_mode="Markdown"
                    )
                # নতুন message_id সেভ করো
                if msg:
                    _backup_msg_save(target, msg.message_id)
                    # সব জায়গায় pin করো — channel + admin — restart এ auto-restore এর জন্য
                    try:
                        await bot.unpin_all_chat_messages(chat_id=target)
                    except Exception:
                        pass
                    try:
                        await bot.pin_chat_message(target, msg.message_id, disable_notification=True)
                    except Exception:
                        pass
                # প্রথম সফল send এর file_id সেভ করো
                if not saved_fid and msg and msg.document:
                    _save_file_id(msg.document.file_id)
                    saved_fid = True
            except Exception as e:
                log.warning(f"[AUTO-BACKUP] {target} এ পাঠাতে ব্যর্থ: {e}")
        log.info("✅ Auto-backup সব channel এ পাঠানো হয়েছে।")
    except Exception as e:
        log.warning(f"[AUTO-BACKUP] ব্যর্থ: {e}")


async def _send_startup_report(bot):
    """
    Bot চালু হলে user report BACKUP_CHANNELS এ পাঠায় ও pin করে।
    post_init এ _send_db_to_admin এর পরে call করতে হবে।
    """
    try:
        import io as _io
        report = _build_full_users_report()
        report_bytes = report.encode("utf-8")
        filename = f"users_{now()[:10]}.txt"
        caption = (
            f"📊 *User Data — Startup Report*\n\n"
            f"🕐 *সময়:* `{now()}`\n"
            f"👥 *মোট Users:* `{len(all_users())}`\n\n"
            f"🔄 Bot নতুন করে চালু হয়েছে — সর্বশেষ user data।"
        )
        for target in BACKUP_CHANNELS:
            # পুরনো report message ডিলিট করো
            old_rid = _report_msg_load(target)
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
                    _report_msg_save(target, msg.message_id)
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


async def _delayed_notify_runner(bot, kwargs_dict: dict, delay_sec: int, pn_id: int | None = None):
    """Top-level coroutine — delay_sec সেকেন্ড অপেক্ষা করে তারপর
    সব bot user কে notification পাঠায়। Bot restart হলেও _pn_watcher আবার
    schedule করবে — তাই এখন 100% restart-safe।"""
    try:
        if delay_sec > 0:
            log.info(f"[NOTIFY] {delay_sec}s ({delay_sec//60}min) অপেক্ষা শুরু…")
            await asyncio.sleep(delay_sec)
            log.info("[NOTIFY] অপেক্ষা শেষ — এখন পাঠাচ্ছি।")
        # _pn_watcher ইতিমধ্যে পাঠিয়ে থাকলে skip করো (race condition fix)
        if pn_id is not None:
            if not _pn_exists(pn_id):
                log.info(f"[NOTIFY] #{pn_id} ইতিমধ্যে _pn_watcher পাঠিয়েছে — skip।")
                return
            _pn_delete(pn_id)   # ← send এর আগে delete — watcher আর পাঠাবে না
            log.info(f"[NOTIFY] pending_notifications #{pn_id} claimed — পাঠাচ্ছি।")
        await _send_task_notify_to_users(**kwargs_dict)
    except Exception as _e:
        log.error(f"[NOTIFY-RUNNER] Notification পাঠাতে ব্যর্থ (pn_id={pn_id}): {_e}", exc_info=True)


async def _pn_watcher(bot):
    """প্রতি ৩০ সেকেন্ডে due pending notifications পাঠায়।
    Bot restart হলে এখান থেকেই missed notifications ধরা পড়বে।"""
    log.info("[PNW] Pending-notification watcher শুরু হয়েছে।")
    while True:
        try:
            due = _pn_get_due()
            for row in due:
                nid = row["id"]
                # ← delete আগে: _delayed_notify_runner এর সাথে race condition এড়াতে
                if not _pn_exists(nid):
                    log.info(f"[PNW] #{nid} ইতিমধ্যে asyncio task পাঠিয়েছে — skip।")
                    continue
                _pn_delete(nid)
                log.info(f"[PNW] #{nid} claimed — task: {row['t_name']} — পাঠাচ্ছি।")
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
        await asyncio.sleep(30)


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
    users_list = all_users()
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
    if not task_notify_on():
        return 0, 0
    notify_ch       = get_task_notify_ch_id()
    notify_ch_uname = get_task_notify_ch_username()
    notify_ch_link  = f"https://t.me/{notify_ch_uname.lstrip('@')}"
    delay_min       = get_notify_delay_min()
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
        _send_at_dt = datetime.now() + timedelta(minutes=delay_min)
        _send_at_str = _send_at_dt.strftime("%Y-%m-%d %H:%M")
        _pn_id = _pn_save_full(
            send_at=_send_at_str,
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
    t = get_fn(tid)
    if not t:
        await q.answer("❌ Task পাওয়া যায়নি!", show_alert=True)
        return False
    new_val = 0 if t['is_active'] else 1
    update_fn(tid, "is_active", new_val)
    mark_db_dirty()
    await q.answer("✅ চালু!" if new_val else "❌ বন্ধ!", show_alert=False)
    try: await q.message.edit_reply_markup(reply_markup=None)
    except: pass
    status_lbl = "✅ *Task Activated!*" if new_val else "❌ *Task Deactivated.*"
    await q.message.reply_text(status_lbl, parse_mode="Markdown")
    if new_val:
        bot_info  = await ctx.bot.get_me()
        task_link = (f"https://t.me/{bot_info.username}%sstart={link_prefix}{tid}"
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
        f"📊 USER DATA REPORT",
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
        report = _build_full_users_report()
        filename = f"users_report_{now()[:10]}.txt"
        report_bytes = report.encode("utf-8")
        caption = (
            f"📊 *Scheduled User Data Report*\n\n"
            f"🕐 *সময়:* `{now()}`\n"
            f"👥 *মোট Users:* `{len(all_users())}`\n\n"
            f"✅ সব user এর balance, referral, commission, task history।"
        )
        import io
        for target in BACKUP_CHANNELS:
            # পুরনো report message ডিলিট করো
            old_rid = _report_msg_load(target)
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
                    _report_msg_save(target, msg.message_id)
            except Exception as e:
                log.warning(f"[REPORT] {target} এ পাঠাতে ব্যর্থ: {e}")
        log.info("✅ Scheduled report পাঠানো হয়েছে।")
    except Exception as e:
        log.warning(f"[REPORT] তৈরি করতে ব্যর্থ: {e}")

async def cmd_testleaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin only — এখনই leaderboard post করো (test)"""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ *এই command শুধু Admin ব্যবহার করতে পারবে।*", parse_mode="Markdown")
        return
    await update.message.reply_text("⏳ *Leaderboard পাঠানো হচ্ছে...*", parse_mode="Markdown")
    await job_post_leaderboard(ctx)
    await update.message.reply_text("✅ *Leaderboard পাঠানো হয়েছে।*", parse_mode="Markdown")


async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ *এই command শুধু Admin ব্যবহার করতে পারবে।*", parse_mode="Markdown")
        return
    await update.message.reply_text("⏳ *User Report তৈরি হচ্ছে...*", parse_mode="Markdown")
    try:
        import io as _io
        report = _build_full_users_report()
        await update.message.reply_document(
            document=_io.BytesIO(report.encode("utf-8")),
            filename=f"users_{now()[:10]}.txt",
            caption=(
                f"📊 *User Data Report (PostgreSQL)*\n\n"
                f"🕐 সময়: `{now()}`\n"
                f"👥 মোট Users: `{len(all_users())}`\n\n"
                "✅ Data Neon PostgreSQL এ permanently stored।\n"
                "🔒 Bot restart হলেও কোনো data যাবে না।"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Report তৈরিতে সমস্যা: `{e}`", parse_mode="Markdown")

async def cmd_restore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ *এই command শুধু Admin ব্যবহার করতে পারবে।*", parse_mode="Markdown")
        return
    await update.message.reply_text(
        "ℹ️ *PostgreSQL Mode চালু আছে।*\n\n"
        "✅ সব data Neon PostgreSQL এ *permanently stored*।\n"
        "🔒 Bot restart হলেও কোনো data মুছে যায় না।\n\n"
        "❌ SQLite file restore এর দরকার নেই।",
        parse_mode="Markdown"
    )

async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    st  = ctx.user_data.get("state")
    if st == "adm_restore_db" and is_admin(uid):
        # PostgreSQL mode — file restore দরকার নেই
        ctx.user_data.pop("state", None)
        await update.message.reply_text(
            "ℹ️ *PostgreSQL Mode চালু — file restore দরকার নেই।*\n\n"
            "✅ Data Neon এ permanently stored। Bot restart হলেও সব data থাকে।",
            parse_mode="Markdown"
        )
    # non-restore document — ignore

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ *আপনি এডমিন না!*", parse_mode="Markdown"); return
    await send_admin_panel(update.message, uid)

async def _show_perm_picker(msg, tgt_uid: int, ctx):
    """Show permission checkbox picker for tgt_uid."""
    with _c() as c:
        r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tgt_uid,)).fetchone()
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
    total, tot_bal, pg, pw, ag, aw = get_stats()
    status   = get_setting("bot_status") or "on"
    bot_btn  = "🔴 Bot বন্ধ করুন" if status == "on" else "🟢 Bot চালু করুন"
    bot_s    = "🟢 চালু" if status == "on" else "🔴 বন্ধ"
    notify_s = "🔔 চালু" if task_notify_on() else "🔕 বন্ধ"

    is_main = (uid == ADMIN_ID)
    has_full         = admin_has_perm(uid, PERM_FULL)
    has_tasks        = admin_has_perm(uid, PERM_TASKS)
    has_withdraw     = admin_has_perm(uid, PERM_WITHDRAW)
    has_users        = admin_has_perm(uid, PERM_USERS)
    has_broadcast    = admin_has_perm(uid, PERM_BROADCAST)
    has_finance      = admin_has_perm(uid, PERM_FINANCE)
    has_settings     = admin_has_perm(uid, PERM_SETTINGS)
    has_admin_manage = is_main or admin_has_perm(uid, PERM_ADMIN_MANAGE)

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
        perms = get_admin_perms(uid)
        perm_line = f"\n🔑 *আপনার পারমিশন:* {_perms_display(','.join(perms))}"

    kb = InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([[]])
    admin_count = len(get_all_admins()) + 1  # +1 for main admin
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
        await _on_callback_inner(update, ctx, q, uid, d)
    except Exception as e:
        log.exception(f"[on_callback ERROR] data={d} uid={uid} error={e}")
        try:
            await q.answer("❌ Error! Admin check logs.", show_alert=True)
        except: pass
    return

async def _on_callback_inner(update, ctx, q, uid, d):

    # ── Language selection (inline) ──────────────────────────────
    if d in ("lang_bn", "lang_en"):
        lang = "bn" if d == "lang_bn" else "en"
        set_lang(uid, lang)
        await q.answer()
        try: await q.message.delete()
        except: pass
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
            await ctx.bot.send_message(uid, msg, parse_mode="Markdown", reply_markup=main_kb(uid))
        return

    # ── Country selection (first-time + change) ─────────────────
    if d.startswith("set_country_"):
        code = d[len("set_country_"):]
        valid_codes = {c[0] for c in COUNTRY_LIST}
        if code not in valid_codes:
            await q.answer("❌ অজানা দেশ!", show_alert=True); return
        _lk = get_lang(uid)
        # ── One-time lock: already set + not first-time flow → block ──
        _existing_country = get_user_country(uid)
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
            await q.answer("🔒 দেশ পরিবর্তন করা যাবে না!", show_alert=True)
            try: await q.message.edit_reply_markup(reply_markup=None)
            except: pass
            await ctx.bot.send_message(uid, _lock_msg, parse_mode="Markdown", reply_markup=main_kb(uid))
            return
        set_user_country(uid, code)
        flag, cname = COUNTRY_MAP.get(code, ("🌍", code))
        await q.answer(f"✅ {flag} {cname} সেট হয়েছে!", show_alert=False)
        try: await q.message.edit_reply_markup(reply_markup=None)
        except: pass
        # First-time registration flow → send welcome
        if ctx.user_data.get("state") == "first_country":
            ctx.user_data.pop("state", None)
            await _send_welcome(update, ctx, uid)
        else:
            success_msg = (
                f"✅ *আপনার দেশ সেট হয়েছে:* {flag} *{cname}*"
                if _lk == 'bn' else
                f"✅ *Country set:* {flag} *{cname}*"
            )
            await ctx.bot.send_message(uid, success_msg, parse_mode="Markdown", reply_markup=main_kb(uid))
            # Resume pending restricted task after country set
            _pending = ctx.user_data.pop("pending_tg_task_after_country", None)
            if _pending:
                _ps = str(_pending)
                if _ps.startswith("ctask_"):
                    _ctid = int(_ps.split("_")[1])
                    _ctask = get_category_task(_ctid)
                    if _ctask and _ctask["is_active"]:
                        await show_category_task_rules(update, ctx, _ctask)
                else:
                    _ptask = get_tg_task(int(_ps))
                    if _ptask and _ptask["is_active"]:
                        await show_tg_task(update, ctx, _ptask)
        return

    # ── Check join ──────────────────────────────────────────────
    if d == "check_join":
        missing = await check_ch(update, ctx)
        if missing:
            await q.answer(T(uid,'not_joined'), show_alert=True)
        else:
            lang_cj = get_lang(uid)
            await q.answer("✅ Verified!" if lang_cj=='en' else "✅ যাচাই সফল!", show_alert=True)
            try: await q.message.delete()
            except: pass
            if ctx.user_data.get("pending_new"):
                await fire_new_user_notify(ctx,
                    ctx.user_data.get("pending_uid"),
                    ctx.user_data.get("pending_name"),
                    ctx.user_data.get("pending_uname"),
                    ctx.user_data.get("pending_ref"))
                for k in ("pending_new","pending_uid","pending_name","pending_uname","pending_ref"):
                    ctx.user_data.pop(k, None)
            # Check if language is set
            if not user_has_lang(uid):
                ctx.user_data["state"] = "first_lang"
                await ctx.bot.send_message(uid,
                    "🌍 *ভাষা বেছে নিন / Choose your language:*",
                    parse_mode="Markdown", reply_markup=lang_kb())
            else:
                await ctx.bot.send_message(uid, T(uid,'joined_ok'), parse_mode="Markdown", reply_markup=main_kb(uid))
        return

    # ── Leaderboard: find my place ────────────────────────────────
    if d == "lb_my_rank":
        await q.answer()
        lang = get_lang(uid)
        rank, cnt = _lb_get_user_full_rank(uid)
        if rank is None:
            msg = ("💡 _You have no approved tasks today. Complete tasks to appear on the leaderboard!_"
                   if lang == "en" else
                   "💡 _আজকে তোমার কোনো Task Approve হয়নি। Task করলে Leaderboard এ আসবে!_")
        else:
            suffix = f"task{'s' if cnt > 1 else ''}" if lang == "en" else f"টি Task"
            msg = (f"📍 *Your rank today:* #{rank} — {cnt} {suffix}"
                   if lang == "en" else
                   f"📍 *আজকে তোমার অবস্থান:* #{rank} — {cnt}{suffix}")
        await q.message.reply_text(msg, parse_mode="Markdown")
        return

    # ── My Referrals ──────────────────────────────────────────────
    if d.startswith("my_refs_"):
        rid = int(d.split("_")[2])
        await q.answer()
        with _c() as c:
            refs = c.execute(
                "SELECT user_id,username,full_name,joined_at FROM users WHERE referred_by=%s", (rid,)
            ).fetchall()
        if not refs:
            await q.message.reply_text(T(uid,'no_referrals'), parse_mode="Markdown"); return
        lines = []
        for i, r in enumerate(refs, 1):
            n = r["full_name"] or r["username"] or "Unknown"
            lines.append(f"`{i}.` {n} — {r['joined_at']}")
        text = f"{T(uid,'my_referrals_title')}\n\n" + "\n".join(lines)
        await q.message.reply_text(text, parse_mode="Markdown"); return

    # ── Task cancel ──────────────────────────────────────────────
    if d == "task_cancel":
        await q.answer()
        try: await q.message.delete()
        except: pass
        return

    if d.startswith("adm_ctask_golive_"):
        if not is_admin(uid): return
        tid = int(d.split("_")[3])
        t   = get_category_task(tid)
        if not t:
            await q.answer("❌ Task পাওয়া যায়নি!", show_alert=True); return
        update_category_task(tid, "is_active", 1)
        fields = get_category_task_fields(tid)
        max_c  = t["max_completions"]
        await q.answer("🚀 Task Live হয়েছে!", show_alert=True)
        try: await q.message.delete()
        except: pass
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
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        await show_tg_tasks_list(update, ctx)
        return

    # ── Category selected → show tasks ───────────────────────────
    if d.startswith("categ_"):
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        try:
            cid = int(d.split("_")[1])
            await show_category_tasks(update, ctx, cid)
        except Exception as e:
            log.warning(f"categ error: {e}")
            await q.answer("❌ সমস্যা হয়েছে!", show_alert=True)
        return

    # ── Category task selected → show rules ──────────────────────
    if d.startswith("ctask_") and not d.startswith("ctask_submit_"):
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        try:
            tid  = int(d.split("_")[1])
            task = get_category_task(tid)
            if not task or not task['is_active']:
                await q.answer("❌ Task পাওয়া যায়নি!", show_alert=True); return
            await show_category_task_rules(update, ctx, task)
        except Exception as e:
            log.warning(f"ctask error: {e}")
            await q.answer("❌ সমস্যা হয়েছে!", show_alert=True)
        return

    # ── Category task submit ──────────────────────────────────────
    if d.startswith("ctask_submit_"):
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        try:
            tid = int(d.split("_")[2])
            await start_category_task_collect(update, ctx, tid)
        except Exception as e:
            log.warning(f"ctask_submit error: {e}")
            await q.answer("❌ সমস্যা হয়েছে!", show_alert=True)
        return

    # ── TG Task separator (dummy) ─────────────────────────────────
    if d == "tgt_sep":
        await q.answer(); return

    # ── TG Task selected → show info ─────────────────────────────
    if d.startswith("tgt_") and not d.startswith("tgt_check_"):
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        try:
            tid  = int(d.split("_")[1])
            task = get_tg_task(tid)
            if not task or not task['is_active']:
                await q.answer("❌ Task পাওয়া যায়নি!", show_alert=True); return
            await show_tg_task(update, ctx, task)
        except Exception as e:
            log.warning(f"tgt task error: {e}")
            await q.answer("❌ সমস্যা হয়েছে!", show_alert=True)
        return

    # ── TG Task check membership ──────────────────────────────────
    if d.startswith("tgt_check_"):
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _blk_tg, _blk_tg_dt = is_task_blocked(uid)
        if _blk_tg and _blk_tg_dt:
            _until_tg = _blk_tg_dt.astimezone(pytz.timezone("Asia/Dhaka")).strftime("%d %b %Y, %I:%M %p")
            await q.answer(T(uid, 'task_blocked_msg', until=_until_tg)[:200], show_alert=True)
            return
        try:
            tid = int(d.split("_")[2])
            await handle_tg_task_check(update, ctx, tid)
        except Exception as e:
            log.warning(f"tgt check error: {e}")
            await q.answer("❌ সমস্যা হয়েছে!", show_alert=True)
        return

    # ── Task selected → show rules ────────────────────────────────
    if d.startswith("task_") and not d.startswith("task_submit_"):
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        _answered = False
        try:
            tid  = int(d.split("_")[1])
            task = get_task(tid)
            if not task:
                await q.answer("❌ Task পাওয়া যায়নি!", show_alert=True); return
            await q.answer()
            _answered = True
            await show_task_rules(update, ctx, task)
        except Exception as _te:
            log.warning(f"[TASK_CLICK] tid={d} error: {_te}")
            if not _answered:
                try: await q.answer("❌ সমস্যা হয়েছে!", show_alert=True)
                except: pass
            else:
                try: await q.message.reply_text("❌ Task দেখাতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")
                except: pass
        return

    # ── Task submit ──────────────────────────────────────────────
    if d.startswith("task_submit_"):
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        try:
            tid = int(d.split("_")[2])
            await start_task_collect(update, ctx, tid)
        except Exception as e:
            log.warning(f"task_submit error: {e}")
            await q.answer("❌ সমস্যা হয়েছে!", show_alert=True)
        return

    # ── Withdraw ──────────────────────────────────────────────────
    if d == "wd_go":
        if not is_admin(uid) and not bot_is_on():
            await bot_off_reply(update, uid); return
        if not await guard(update, ctx): return
        await wd_ask_addr(update, ctx); return

    if d == "wd_confirm":
        if ctx.user_data.get("state") != "wd_confirm":
            lang = get_lang(uid)
            msg = ("⚠️ Session expired. Please start withdrawal again."
                   if lang=='en' else "⚠️ Session শেষ। আবার Withdraw শুরু করুন।")
            await q.answer(msg, show_alert=True); return
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        await do_wd_submit(update, ctx); return

    if d == "wd_edit_addr":
        await q.answer()
        ctx.user_data["state"] = "wd_addr"
        lang = get_lang(uid)
        fee   = get_wd_fee()
        min_w = get_min_withdraw()
        bal   = get_bal(uid)
        await q.message.reply_text(
            T(uid, 'wd_enter_addr'), parse_mode="Markdown", reply_markup=cancel_kb(uid)
        ); return

    if d == "wd_edit_amt":
        await q.answer()
        ctx.user_data["state"] = "wd_amt"
        fee   = get_wd_fee()
        min_w = get_min_withdraw()
        bal   = get_bal(uid)
        await q.message.reply_text(
            T(uid, 'wd_enter_amt', bal=bal, fee=fee, min_w=min_w),
            parse_mode="Markdown", reply_markup=cancel_kb(uid)
        ); return

    # ── Gmail message to user ─────────────────────────────────────
    if d.startswith("gmsg_"):
        if not is_admin(uid):
            await q.answer("❌ আপনি এডমিন না!", show_alert=True); return
        gid = int(d.split("_")[1])
        with _c() as c: row = c.execute("SELECT user_id FROM gmails WHERE id=%s", (gid,)).fetchone()
        if not row: await q.answer("❌ পাওয়া যায়নি!", show_alert=True); return
        await q.answer()
        ctx.user_data["state"]         = "adm_direct_msg"
        ctx.user_data["direct_msg_to"] = row["user_id"]
        await q.message.reply_text(
            f"📨 *Sub #{gid} এর User কে মেসেজ লিখুন:*\n\n/cancel — বাতিল",
            parse_mode="Markdown")
        return

    # ── Gmail approve ─────────────────────────────────────────────
    if d.startswith("ga_"):
        if not is_admin(uid):
            await q.answer("❌ আপনি এডমিন না!", show_alert=True); return
        gid = int(d.split("_")[1])
        with _c() as c: row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
        if not row: return await q.answer("❌ পাওয়া যায়নি!", show_alert=True)
        if row["status"] != "pending":
            return await q.answer(f"⚠️ ইতিমধ্যে {row['status']}!", show_alert=True)
        with _c() as c: c.execute("UPDATE gmails SET status='approved', approved_at=%s WHERE id=%s", (now(), gid))
        guser, price = row["user_id"], row["amount"]
        _row_ttype = _rget(row, 'task_type', 'other')
        _row_ticon = task_type_icon(_row_ttype)
        _task_display = f"{_row_ticon} {row['task_name']}"
        bal_ok = add_bal(guser, price)
        _q_answered = False
        if not bal_ok:
            log.warning(f"[APPROVE] user_id={guser} DB তে নেই — balance add হয়নি! gmails id={gid}")
            await q.answer(f"⚠️ Approved কিন্তু user {guser} DB তে নেই — balance যোগ হয়নি!", show_alert=True)
            _q_answered = True
        new_pts = add_points(guser, POINT_PER_APPROVE)
        ref = get_referred_by(guser)
        if ref:
            comm_pct = get_commission_pct()
            comm = round(price * comm_pct / 100.0, 4)
            comm_pct_display = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
            add_bal(ref, comm)
            try:
                ru    = get_user(guser)
                rname = (ru["full_name"] or ru["username"] or "Unknown") if ru else "Unknown"
                await ctx.bot.send_message(ref,
                    T(ref, 'commission_msg', name=rname, price=price, comm=comm, comm_pct=comm_pct_display),
                    parse_mode="Markdown")
            except: pass
        try:
            await ctx.bot.send_message(guser,
                T(guser, 'approved_msg', task=_task_display, price=price),
                parse_mode="Markdown")
        except: pass
        asyncio.create_task(_lb_refresh_now(ctx.bot))
        _rw_code = _rget(row, 'task_code', '') or ''
        _rw_name = row['task_name']
        _rt, _maxed = _regular_task_mark_done(_rw_code, _rw_name)
        if _maxed and _rt:
            asyncio.create_task(_notify_task_maxed(
                ctx.bot, _rt["name"], _rt["task_code"] or "",
                "Regular Task", _rt["completed_count"], _rt["max_completions"]
            ))
        _task_type_lbl = task_type_label(_rget(row, 'task_type', 'other'))
        asyncio.create_task(_notify_task_done_ch(
            ctx.bot, guser, _rw_name, _rw_code, price, _task_type_lbl
        ))
        status_line = f"\n\n✅ *APPROVED by Admin*"
        orig = q.message.text or q.message.caption or ""
        new_txt = orig + status_line
        try:
            if q.message.photo:
                await q.message.edit_caption(new_txt, parse_mode="Markdown", reply_markup=None)
            else:
                await q.message.edit_text(new_txt, parse_mode="Markdown", reply_markup=None)
        except: pass
        with _c() as c: row2 = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
        other_mid = row2["channel_msg_id"] if uid == ADMIN_ID else row2["admin_msg_id"]
        other_cid = get_gmail_ch_id() if uid == ADMIN_ID else ADMIN_ID
        if other_mid:
            try:
                if q.message.photo:
                    await ctx.bot.edit_message_caption(new_txt, chat_id=other_cid, message_id=other_mid, parse_mode="Markdown", reply_markup=None)
                else:
                    await ctx.bot.edit_message_text(new_txt, chat_id=other_cid, message_id=other_mid, parse_mode="Markdown", reply_markup=None)
            except: pass
        if not _q_answered:
            await q.answer("✅ Approved!")
        return

    # ── Gmail reject — কারণ জিজ্ঞেস করো আগে ────────────────────────
    if d.startswith("gr_"):
        if not is_admin(uid):
            await q.answer("❌ আপনি এডমিন না!", show_alert=True); return
        gid = int(d.split("_")[1])
        with _c() as c: row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
        if not row: return await q.answer("❌ পাওয়া যায়নি!", show_alert=True)
        if row["status"] != "pending":
            return await q.answer(f"⚠️ ইতিমধ্যে {row['status']}!", show_alert=True)
        await q.answer()
        ctx.user_data["state"]              = "adm_reject_reason"
        ctx.user_data["adm_reject_gid"]     = gid
        ctx.user_data["adm_reject_msg_id"]  = q.message.message_id
        ctx.user_data["adm_reject_chat_id"] = q.message.chat_id
        ctx.user_data["adm_reject_has_photo"] = bool(q.message.photo)
        ctx.user_data["adm_reject_orig_txt"]  = q.message.text or q.message.caption or ""
        await q.message.reply_text(
            f"❌ *Sub #{gid} Reject করছেন।*\n\n"
            f"📌 *Task:* {row['task_name']}\n"
            f"👤 *User ID:* `{row['user_id']}`\n\n"
            f"📝 *Reject এর কারণ লিখুন* (ইউজারকে পাঠানো হবে):\n\n"
            f"_/cancel — বাতিল করতে_",
            parse_mode="Markdown"
        )
        return

    # ── Show full address ─────────────────────────────────────────
    if d.startswith("show_addr_"):
        wid = int(d.split("_")[2])
        if not is_admin(uid):
            await q.answer("❌ শুধু Admin দেখতে পারবে!", show_alert=True); return
        with _c() as c:
            row = c.execute("SELECT address FROM withdrawals WHERE id=%s", (wid,)).fetchone()
        if not row:
            await q.answer("❌ পাওয়া যায়নি!", show_alert=True); return
        await q.answer(f"📍 Full Address:\n{row['address']}", show_alert=True)
        return

    # ── Gas Fee Alert: Admin approve (পাঠাও) বা cancel (ফেরত দাও) ──
    if d.startswith("gas_ok_") or d.startswith("gas_cancel_"):
        if not is_admin(uid):
            await q.answer("❌ আপনি এডমিন না!", show_alert=True); return
        wid = int(d.split("_")[2])
        with _c() as c:
            row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
        if not row:
            await q.answer("❌ WD পাওয়া যায়নি!", show_alert=True); return
        if row["status"] != "gas_pending":
            await q.answer(f"⚠️ ইতিমধ্যে {row['status']}!", show_alert=True); return

        wuser = row["user_id"]
        wamt  = row["amount"]
        waddr = row["address"]
        fee   = row["fee"] if row["fee"] else get_wd_fee()
        net_amt = round(wamt - fee, 4)

        if d.startswith("gas_ok_"):
            # ── পাঠাও ───────────────────────────────────────────────
            with _c() as c:
                c.execute("UPDATE withdrawals SET status='pending' WHERE id=%s", (wid,))
            mark_db_dirty()
            try: await q.message.edit_text(q.message.text + "\n\n✅ *Admin approved — পাঠানো হবে।*", parse_mode="Markdown", reply_markup=None)
            except: pass
            await q.answer("✅ Withdrawal pending এ রাখা হয়েছে — পরের cycle এ পাঠাবে।")
        else:
            # ── বাতিল করো — refund ──────────────────────────────────
            with _c() as c:
                c.execute("UPDATE withdrawals SET status='rejected' WHERE id=%s", (wid,))
            add_bal(wuser, wamt)
            mark_db_dirty()
            _ulang = get_lang(wuser)
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
            except: pass
            try: await q.message.edit_text(q.message.text + "\n\n❌ *Admin cancelled — balance refunded.*", parse_mode="Markdown", reply_markup=None)
            except: pass
            await q.answer("❌ Withdrawal cancelled — balance refunded.")
        return

    # ── Withdraw approve/reject ──────────────────────────────────
    if d.startswith("wa_") or d.startswith("wr_"):
        if not is_admin(uid):
            await q.answer("❌ আপনি এডমিন না!", show_alert=True); return
        wid    = int(d.split("_")[1])
        action = "approved" if d.startswith("wa_") else "rejected"
        with _c() as c: row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
        if not row: return await q.answer("❌ পাওয়া যায়নি!", show_alert=True)
        if row["status"] != "pending":
            return await q.answer(f"⚠️ ইতিমধ্যে {row['status']}!", show_alert=True)
        with _c() as c: c.execute("UPDATE withdrawals SET status=%s WHERE id=%s", (action, wid))

        wuser, wamt, waddr = row["user_id"], row["amount"], row["address"]
        masked = mask_addr_short(waddr)
        if action == "approved":
            try:
                await ctx.bot.send_message(wuser,
                    T(wuser, 'wd_approved_msg', amt=wamt, addr=masked),
                    parse_mode="Markdown")
            except: pass
        else:
            add_bal(wuser, wamt)
            try:
                await ctx.bot.send_message(wuser,
                    T(wuser, 'wd_rejected_msg', amt=wamt),
                    parse_mode="Markdown")
            except: pass

        status_line = "\n\n✅ *APPROVED by Admin*" if action == "approved" else "\n\n❌ *REJECTED + Refunded*"
        raw_txt = (q.message.text or "")
        new_txt = raw_txt + status_line
        try: await q.message.edit_text(new_txt, parse_mode="Markdown", reply_markup=None)
        except: pass
        with _c() as c: row2 = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
        other_mid = row2["channel_msg_id"] if uid == ADMIN_ID else row2["admin_msg_id"]
        other_cid = get_withdraw_ch_id() if uid == ADMIN_ID else ADMIN_ID
        if other_mid:
            try:
                await ctx.bot.edit_message_text(new_txt, chat_id=other_cid, message_id=other_mid, parse_mode="Markdown", reply_markup=None)
            except: pass
        await q.answer("✅ Done!")
        return

    # ── Admin-only callbacks ──────────────────────────────────────
    if not is_admin(uid):
        await q.answer("❌ আপনি এডমিন না!", show_alert=True); return
    await q.answer()

    if d == "adm_bot_toggle":
        if not admin_has_perm(uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        current = get_setting("bot_status") or "on"
        new_s   = "off" if current == "on" else "on"
        set_setting("bot_status", new_s)
        msg = "🔴 *Bot বন্ধ করা হয়েছে।*" if new_s == "off" else "🟢 *Bot চালু করা হয়েছে।*"
        await q.message.reply_text(msg, parse_mode="Markdown"); return

    if d == "adm_toggle_notify":
        new_n = "off" if task_notify_on() else "on"
        set_setting("task_notify", new_n)
        icon  = "🔔" if new_n == "on" else "🔕"
        await q.message.reply_text(
            f"{icon} *Task Notification {'চালু' if new_n=='on' else 'বন্ধ'} করা হয়েছে।*",
            parse_mode="Markdown"); return

    # ── Menu Buttons Edit ─────────────────────────────────────────
    if d == "adm_menu_btns":
        with _c() as c:
            rows = c.execute("SELECT key, label_bn, label_en FROM menu_buttons").fetchall()
        text = "🎛 *Menu Buttons সম্পাদনা*\n\n"
        btns = []
        for row in rows:
            text += f"• *{row['key']}*: BN=`{row['label_bn']}` | EN=`{row['label_en']}`\n"
            btns.append([InlineKeyboardButton(
                f"✏️ {row['key']}", callback_data=f"adm_btn_edit_{row['key']}"
            )])
        btns.append([InlineKeyboardButton("🔄 সব Default এ ফিরিয়ে দিন", callback_data="adm_btn_reset")])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_btn_reset":
        for key, (bn, en) in DEFAULT_BTNS.items():
            set_btn(key, bn, en)
        await q.message.reply_text("✅ *সব বাটন Default এ ফিরিয়ে দেওয়া হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_btn_edit_"):
        btn_key = d.replace("adm_btn_edit_", "")
        ctx.user_data["state"]       = "adm_btn_edit"
        ctx.user_data["edit_btn_key"] = btn_key
        with _c() as c:
            row = c.execute("SELECT label_bn, label_en FROM menu_buttons WHERE key=%s", (btn_key,)).fetchone()
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
        if not admin_has_perm(uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        owner      = get_setting('support_owner') or '@inkamxbd'
        bot_u      = get_setting('support_bot')   or '@gmail_sell_help_bot'
        min_w      = get_setting('min_withdraw')  or '0.05'
        comm       = get_setting('commission_pct') or '8'
        g_ch       = get_setting('gmail_ch_id')   or str(GMAIL_CH_ID)
        w_ch       = get_setting('withdraw_ch_id') or str(WITHDRAW_CH_ID)
        g_price    = get_setting('gmail_price')   or '0.10'
        notify_ch  = get_setting('task_notify_ch_id') or '-1003866731221'
        has_wbn    = "✅ Custom" if get_setting('welcome_msg_bn') else "⬜ Default"
        has_wen    = "✅ Custom" if get_setting('welcome_msg_en') else "⬜ Default"
        bsc_w = get_bsc_wallet_addr()
        bsc_short = bsc_w[:6] + "..." + bsc_w[-4:] if bsc_w else "N/A"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👨‍💼 Support Owner পরিবর্তন",          callback_data="adm_set_support_owner")],
            [InlineKeyboardButton("🤖 Support Bot পরিবর্তন",              callback_data="adm_set_support_bot")],
            [InlineKeyboardButton("💸 Min Withdraw পরিবর্তন",             callback_data="adm_set_min_withdraw")],
            [InlineKeyboardButton("💹 Commission % পরিবর্তন",             callback_data="adm_set_commission_pct")],
            [InlineKeyboardButton("📢 Task Channel ID পরিবর্তন",          callback_data="adm_set_gmail_ch_id")],
            [InlineKeyboardButton("💳 Withdraw Channel ID পরিবর্তন",      callback_data="adm_set_withdraw_ch_id")],
            [InlineKeyboardButton("💰 Default Task Price পরিবর্তন",       callback_data="adm_set_gmail_price")],
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
            f"💰 *Default Task Price:* `{g_price}$ USDT`\n"
            f"📡 *Task Notify Channel:* `{notify_ch}`\n"
            f"⏱ *Bot Notify Delay:* `{get_notify_delay_min()} মিনিট`\n"
            f"📝 *Welcome BN:* {has_wbn}\n"
            f"📝 *Welcome EN:* {has_wen}\n"
            f"🔑 *BSC Wallet:* `{bsc_short}`\n\n"
            f"👇 পরিবর্তন করতে নিচের বাটন চাপুন:",
            parse_mode="Markdown", reply_markup=kb
        ); return

    if d == "adm_db_backup":
        if not admin_has_perm(uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.answer("⏳ Backup পাঠানো হচ্ছে...")
        await _send_db_to_admin(ctx.bot, label="📥 Manual Backup (Admin Panel)")
        await q.message.reply_text("✅ *Backup আপনার Telegram এ পাঠানো হয়েছে।*", parse_mode="Markdown"); return

    if d == "adm_db_restore":
        if not admin_has_perm(uid, PERM_SETTINGS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.answer()
        ctx.user_data["state"] = "adm_restore_db"
        await q.message.reply_text(
            "📤 *Restore করতে data.db ফাইল পাঠান।*\n\n"
            "⚠️ *সতর্কতা:* এটা বর্তমান সব data replace করবে!\n"
            "_(Backup file টা পাঠান যেটা /backup বা auto-backup এ পেয়েছিলেন)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown"
        ); return

    if d == "adm_health_check":
        await q.answer("🔍 চেক করা হচ্ছে...")
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
        await q.answer("🔧 Fix চলছে...")
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
        await q.answer("⏳ Balance চেক করা হচ্ছে...")
        usdt = get_bsc_usdt_balance()
        bnb  = get_bsc_bnb_balance()
        usdt_txt = f"`{usdt:.4f} USDT`" if usdt is not None else "❌ চেক করা যায়নি"
        bnb_txt  = f"`{bnb:.6f} BNB`"   if bnb  is not None else "❌ চেক করা যায়নি"
        _bsc_w   = get_bsc_wallet_addr()
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
        cur_w = get_bsc_wallet_addr()
        ctx.user_data["state"] = "adm_set_bsc_wallet"
        await q.message.reply_text(
            f"🔑 *BSC Wallet Address পরিবর্তন করুন*\n\n"
            f"_বর্তমান:_ `{cur_w}`\n\n"
            "নতুন BSC (BEP-20) wallet address দিন:\n/cancel — বাতিল",
            parse_mode="Markdown"
        ); return
    if d == "adm_set_gmail_price":
        ctx.user_data["state"] = "adm_set_gmail_price"
        await q.message.reply_text("💰 *Default Task Price লিখুন:*\n_(যেমন: 0.10)_\n/cancel — বাতিল", parse_mode="Markdown"); return
    if d == "adm_set_task_notify_ch":
        ctx.user_data["state"] = "adm_set_task_notify_ch"
        cur = get_setting('task_notify_ch_id') or '-1003866731221'
        await q.message.reply_text(
            f"📡 *Task Notify Channel ID পরিবর্তন করুন:*\n\n_বর্তমান:_ `{cur}`\n\n"
            "Channel/Group ID দিন (যেমন: `-1001234567890`)\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_set_notify_ch_username":
        ctx.user_data["state"] = "adm_set_notify_ch_username"
        cur = get_task_notify_ch_username()
        await q.message.reply_text(
            f"🔔 *Notification Channel Username পরিবর্তন করুন:*\n\n_বর্তমান:_ `{cur}`\n\n"
            "Channel username দিন (যেমন: `@task_earn_bot_notification`)\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_set_notify_delay":
        ctx.user_data["state"] = "adm_set_notify_delay"
        cur = get_notify_delay_min()
        await q.message.reply_text(
            f"⏱ *Bot Notify Delay পরিবর্তন করুন:*\n\n_বর্তমান:_ `{cur} মিনিট`\n\n"
            "কত মিনিট পরে Bot users এ notification যাবে?\n"
            "_(0 = সাথে সাথে, 5 = ৫ মিনিট পরে)_\n\n"
            "📌 Channel এ সবসময় সাথে সাথে যাবে।\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_set_welcome_bn":
        ctx.user_data["state"] = "adm_set_welcome_bn"
        cur = get_setting('welcome_msg_bn') or "(Default ব্যবহার হচ্ছে)"
        await q.message.reply_text(
            f"📝 *বাংলা Welcome Message লিখুন:*\n\n_বর্তমান:_\n{cur[:300]}\n\n"
            "⚠️ শুধু `-` পাঠালে Default এ ফিরে যাবে।\n/cancel — বাতিল",
            parse_mode="Markdown"); return
    if d == "adm_set_welcome_en":
        ctx.user_data["state"] = "adm_set_welcome_en"
        cur = get_setting('welcome_msg_en') or "(Default in use)"
        await q.message.reply_text(
            f"📝 *English Welcome Message লিখুন:*\n\n_Current:_\n{cur[:300]}\n\n"
            "⚠️ Send `-` to reset to Default.\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d in ("adm_leaderboard", "adm_lb_refresh"):
        # Admin panel → full info: নাম + @username + ID (@ হাইড হয় না)
        text = _lb_build_text_admin()
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
                _lb_adm_save(sent.chat_id, sent.message_id)
                return
            _lb_adm_save(q.message.chat_id, q.message.message_id)
        else:
            sent = await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            _lb_adm_save(sent.chat_id, sent.message_id)
        return

    if d == "adm_lb_post_ch":
        # Admin panel থেকে এখনই channel এ leaderboard পাঠাও
        await q.answer("⏳ Channel এ পাঠানো হচ্ছে...")
        await job_post_leaderboard(ctx)
        await q.message.reply_text("✅ Leaderboard channel এ পাঠানো হয়েছে।")
        return

    if d == "adm_refer_stats":
        if not admin_has_perm(uid, PERM_FINANCE):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.answer()
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        d7_ago      = (now_utc - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        d30_ago     = (now_utc - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        def _top10(since: str) -> list:
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

        top_today = _top10(today_start)
        top_7d    = _top10(d7_ago)
        top_30d   = _top10(d30_ago)

        with _c() as c:
            total_ref = _count(c.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL").fetchone())
            total_usr = _count(c.execute("SELECT COUNT(*) FROM users").fetchone())

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
        if not admin_has_perm(uid, PERM_FINANCE):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        total, tot_bal, pg, pw, ag, aw = get_stats()
        await q.message.reply_text(
            f"📊 *STATISTICS*\n\n"
            f"👥 *মোট ইউজার:* `{total}`\n"
            f"💰 *মোট ব্যালান্স:* `{round(tot_bal,4)}$ USDT`\n\n"
            f"📋 *Task Pending:* `{pg}` | Approved: `{ag}`\n"
            f"💸 *Withdraw Pending:* `{pw}` | Approved: `{aw}`\n"
            f"👮 *Sub Admins:* `{len(get_all_admins())}`\n"
            f"📋 *মোট Task:* `{len(get_all_tasks())}`",
            parse_mode="Markdown"); return

    if d == "adm_pending_g":
        if not admin_has_perm(uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        with _c() as c:
            rows = c.execute("SELECT id,user_id,task_name,amount,submitted_at FROM gmails WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
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
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_pending_w":
        if not admin_has_perm(uid, PERM_WITHDRAW):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        with _c() as c:
            rows = c.execute(
                "SELECT id,user_id,amount,address,requested_at,status,locked_at,tx_hash "
                "FROM withdrawals WHERE status IN ('pending','processing','gas_pending') "
                "ORDER BY id DESC LIMIT 30"
            ).fetchall()
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
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_task_code_search":
        if not admin_has_perm(uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_task_code_input"
        await q.message.reply_text(
            "🔍 *Task Code দিয়ে খুঁজুন*\n\n"
            "Task Code লিখুন (যেমন: `TK-AB3X7`):\n\n/cancel — বাতিল",
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
        await q.answer()
        with _c() as c:
            rows = c.execute(
                "SELECT user_id,username,full_name,balance,is_banned,joined_at FROM users ORDER BY user_id DESC LIMIT 20"
            ).fetchall()
        if not rows:
            await q.message.reply_text("কোনো user নেই।"); return
        lines = ["📋 *সর্বশেষ ২০ জন User:*\n"]
        for r in rows:
            icon = "🚫" if r["is_banned"] else "✅"
            name = f"@{r['username']}" if r["username"] else (r["full_name"] or "Unknown")
            lines.append(f"{icon} `{r['user_id']}` — {name} | 💰`{round(r['balance'],4)}`")
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown"); return

    if d.startswith("adm_uban_"):
        tid = int(d[len("adm_uban_"):])
        ban_user(tid, 1)
        await q.answer("🚫 Banned!", show_alert=True)
        txt2, kb2 = _build_user_profile(tid)
        try: await q.message.edit_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        except: await q.message.reply_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        return

    if d.startswith("adm_uunban_"):
        tid = int(d[len("adm_uunban_"):])
        ban_user(tid, 0)
        await q.answer("✅ Unbanned!", show_alert=True)
        txt2, kb2 = _build_user_profile(tid)
        try: await q.message.edit_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        except: await q.message.reply_text(txt2, parse_mode="Markdown", reply_markup=kb2)
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
        await q.answer()
        with _c() as c:
            refs = c.execute(
                "SELECT user_id,username,full_name,balance,joined_at FROM users WHERE referred_by=%s ORDER BY user_id DESC LIMIT 30",
                (tid,)
            ).fetchall()
        if not refs:
            await q.message.reply_text(f"👥 `{tid}` এর কোনো referral নেই।", parse_mode="Markdown"); return
        lines = [f"👥 *`{tid}` এর Referrals ({len(refs)}জন):*\n"]
        for r in refs:
            name = f"@{r['username']}" if r["username"] else (r["full_name"] or "Unknown")
            lines.append(f"• `{r['user_id']}` — {name} | 💰`{round(r['balance'],4)}` | 📅{r['joined_at']}")
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown"); return

    if d.startswith("adm_usubs_"):
        tid = int(d[len("adm_usubs_"):])
        await q.answer()
        with _c() as c:
            subs = c.execute(
                "SELECT id,gmail_text,task_name,status,amount,submitted_at FROM gmails WHERE user_id=%s ORDER BY id DESC LIMIT 15",
                (tid,)
            ).fetchall()
        if not subs:
            await q.message.reply_text(f"📋 `{tid}` এর কোনো submission নেই।", parse_mode="Markdown"); return
        lines = [f"📋 *`{tid}` এর Submissions ({len(subs)}টি):*\n"]
        for r in subs:
            icon = "✅" if r["status"]=="approved" else ("❌" if r["status"]=="rejected" else "⏳")
            lines.append(f"{icon} `#{r['id']}` | {r['task_name']} | 💰`{r['amount']}` | {r['submitted_at']}")
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown"); return

    if d.startswith("adm_uwds_"):
        tid = int(d[len("adm_uwds_"):])
        await q.answer()
        with _c() as c:
            wds = c.execute(
                "SELECT id,amount,address,status,requested_at FROM withdrawals WHERE user_id=%s ORDER BY id DESC LIMIT 15",
                (tid,)
            ).fetchall()
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
        await q.answer()
        txt2, kb2 = _build_user_profile(tid)
        try: await q.message.edit_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        except: await q.message.reply_text(txt2, parse_mode="Markdown", reply_markup=kb2)
        return

    # ── Admin: দেশ পরিবর্তন করুন (country keyboard দেখাও) ──────────
    if d.startswith("adm_uchcountry_"):
        if not admin_has_perm(uid, PERM_USERS):
            await q.answer("❌ পারমিশন নেই!", show_alert=True); return
        tid = int(d[len("adm_uchcountry_"):])
        await q.answer()
        _cur = get_user_country(tid)
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
        if not admin_has_perm(uid, PERM_USERS):
            await q.answer("❌ পারমিশন নেই!", show_alert=True); return
        # format: adm_uset_ctry_{tid}_{code}
        rest = d[len("adm_uset_ctry_"):]          # e.g. "7423929548_BD"
        last_under = rest.rfind("_")
        tid  = int(rest[:last_under])
        code = rest[last_under+1:].upper()
        if code not in COUNTRY_MAP:
            await q.answer("❌ অজানা দেশ!", show_alert=True); return
        old_raw = get_user_country(tid)
        old_flag, old_name = COUNTRY_MAP.get((old_raw or "").upper(), ("🌍", old_raw or "সেট হয়নি"))
        set_user_country(tid, code)
        flag, cname = COUNTRY_MAP[code]
        await q.answer(f"✅ {flag} {cname} সেট হয়েছে!", show_alert=True)
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
        except: pass
        # User কে notify করো
        _u_lang = get_lang(tid)
        _notify = (
            f"🌍 *আপনার দেশ পরিবর্তন করা হয়েছে।*\n\n"
            f"নতুন দেশ: {flag} *{cname}*\n\n"
            f"এখন আপনি এই দেশের Task করতে পারবেন।"
            if _u_lang == 'bn' else
            f"🌍 *Your country has been updated by Admin.*\n\n"
            f"New country: {flag} *{cname}*\n\n"
            f"You can now access tasks available for this country."
        )
        try: await context.bot.send_message(tid, _notify, parse_mode="Markdown")
        except: pass
        return

    # ── Admin: Bot Logs দেখুন ─────────────────────────────────────────
    if d == "adm_view_logs" or d.startswith("adm_logs_filter_"):
        if not (uid == ADMIN_ID or admin_has_perm(uid, PERM_SETTINGS)):
            await q.answer("❌ পারমিশন নেই!", show_alert=True); return
        await q.answer()
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
            with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as _lf:
                _all_lines = _lf.readlines()
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
            except: await q.message.reply_text(_msg, parse_mode="Markdown", reply_markup=filter_kb)
        except Exception as _le:
            await q.message.reply_text(f"❌ Log পড়তে সমস্যা: {_le}", parse_mode="Markdown")
        return

    if d == "adm_bc":
        if not admin_has_perm(uid, PERM_BROADCAST):
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
        if not is_admin(uid): return
        await q.answer()
        ctx.user_data.pop("bc_photo_id", None)
        ctx.user_data["state"] = "adm_bc_bn"
        await q.message.reply_text(
            "📢 *Broadcast — বাংলা মেসেজ লিখুন:*\n\n"
            "_(বাংলা ইউজাররা এটা পাবে)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d in ("adm_bc_send_pin", "adm_bc_send_nopin"):
        if not is_admin(uid): return
        await q.answer()
        bc_bn     = ctx.user_data.get("bc_msg_bn", "")
        bc_en     = ctx.user_data.get("bc_msg_en", "")
        bc_photo  = ctx.user_data.get("bc_photo_id", "")
        do_pin    = (d == "adm_bc_send_pin")
        if not bc_bn and not bc_en:
            await q.message.reply_text("❌ *কোনো মেসেজ পাওয়া যায়নি।*", parse_mode="Markdown"); return
        users = all_users(); sent = fail = pin_fail = 0
        for u in users:
            try:
                u_lang  = get_lang(u["user_id"])
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
                    except: pin_fail += 1
                await asyncio.sleep(0.05)
            except: fail += 1
        pin_status = f"\n📌 *পিন:* ✅ `{sent - pin_fail}` | ❌ `{pin_fail}`" if do_pin else "\n📌 *পিন:* বন্ধ ছিল"
        await q.message.reply_text(
            f"📢 *Broadcast সম্পন্ন!*\n✅ `{sent}` | ❌ `{fail}`{pin_status}",
            parse_mode="Markdown")
        for k in ("bc_msg_bn", "bc_msg_en", "bc_photo_id"):
            ctx.user_data.pop(k, None)
        return

    if d == "adm_bc_edit":
        if not is_admin(uid): return
        await q.answer()
        for k in ("bc_msg_bn", "bc_msg_en", "bc_photo_id"): ctx.user_data.pop(k, None)
        skip_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip — ছবি ছাড়া", callback_data="adm_bc_skip_photo")]])
        ctx.user_data["state"] = "adm_bc_photo"
        await q.message.reply_text(
            "📢 *আবার শুরু করুন — ছবি পাঠান বা Skip করুন:*\n\n/cancel — বাতিল",
            parse_mode="Markdown", reply_markup=skip_kb)
        return

    if d == "adm_bc_cancel":
        if not is_admin(uid): return
        await q.answer("বাতিল করা হয়েছে।")
        for k in ("bc_msg_bn", "bc_msg_en", "bc_photo_id"): ctx.user_data.pop(k, None)
        await q.message.edit_text("❌ *Broadcast বাতিল করা হয়েছে।*", parse_mode="Markdown"); return

    if d == "adm_promo_create":
        if not is_admin(uid): return
        await q.answer()
        ctx.user_data["state"] = "adm_promo_code"
        await q.message.reply_text(
            "🎁 *নতুন Promo Code তৈরি করুন*\n\n"
            "প্রথমে *Code* লিখুন (যেমন: `EIDMUBARAK`):\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d == "adm_bc_post_news":
        if not is_admin(uid): return
        await q.answer()
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
        if not admin_has_perm(uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("📨 *কোন User কে মেসেজ করবেন? User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_msg_id"; return
    if d == "adm_add":
        if not admin_has_perm(uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("➕ *Balance যোগ করতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_add_id"; return
    if d == "adm_ded":
        if not admin_has_perm(uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("➖ *Balance কাটতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_ded_id"; return
    if d == "adm_ban":
        if not admin_has_perm(uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("🚫 *Ban করতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_ban_id"; return
    if d == "adm_unban":
        if not admin_has_perm(uid, PERM_USERS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text("✅ *Unban করতে User ID দিন:*\n\n/cancel — বাতিল", parse_mode="Markdown")
        ctx.user_data["state"] = "adm_unban_id"; return

    if d == "adm_add_admin":
        if not (uid == ADMIN_ID or admin_has_perm(uid, PERM_ADMIN_MANAGE)):
            await q.message.reply_text("❌ *আপনার এই কাজের অনুমতি নেই!*", parse_mode="Markdown"); return
        await q.message.reply_text(
            "👮 *নতুন Admin যোগ করুন*\n\nপ্রথমে *User ID* দিন:\n\n/cancel — বাতিল",
            parse_mode="Markdown")
        ctx.user_data["state"] = "adm_add_admin_id"; return

    if d == "adm_list_admins":
        if not is_admin(uid): await q.answer("⛔ Access Denied", show_alert=True); return
        await q.answer()
        admins = get_all_admins()
        can_manage = (uid == ADMIN_ID or admin_has_perm(uid, PERM_ADMIN_MANAGE))
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
            if can_manage:
                row = []
                if uid == ADMIN_ID:
                    row.append(InlineKeyboardButton(f"✏️ এডিট {a['user_id']}", callback_data=f"adm_edit_admin_perm_{a['user_id']}"))
                row.append(InlineKeyboardButton(f"❌ সরাও {a['user_id']}", callback_data=f"adm_rm_admin_{a['user_id']}"))
                btns.append(row)
        if not admins:
            lines.append("\n_এখনো কোনো Sub Admin যোগ করা হয়নি।_")
        await q.message.reply_text(
            f"📋 *Admin তালিকা ({len(admins)+1} জন):*\n\n" + "\n\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns) if btns else None); return

    if d.startswith("adm_rm_admin_") and not d.startswith("adm_rm_admin_confirm_") and d != "adm_rm_admin_cancel":
        if not (uid == ADMIN_ID or admin_has_perm(uid, PERM_ADMIN_MANAGE)):
            await q.message.reply_text("❌ *আপনার এই কাজের অনুমতি নেই!*", parse_mode="Markdown"); return
        await q.answer()
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
        if not (uid == ADMIN_ID or admin_has_perm(uid, PERM_ADMIN_MANAGE)):
            await q.answer("⛔ Access Denied", show_alert=True); return
        await q.answer()
        tid = int(d.split("_")[4])
        if tid == ADMIN_ID:
            await q.message.reply_text("❌ *Main Admin কে সরানো যাবে না!*", parse_mode="Markdown"); return
        remove_admin(tid)
        ctx.user_data.pop("adm_rm_confirm_tid", None)
        try:
            await ctx.bot.send_message(tid, "⛔ *আপনাকে Admin তালিকা থেকে সরানো হয়েছে।*", parse_mode="Markdown")
        except: pass
        await q.message.reply_text(f"✅ *`{tid}` কে Admin তালিকা থেকে সরানো হয়েছে।*", parse_mode="Markdown"); return

    if d == "adm_rm_admin_cancel":
        await q.answer()
        ctx.user_data.pop("adm_rm_confirm_tid", None)
        await q.message.reply_text("❌ বাতিল করা হয়েছে।", parse_mode="Markdown"); return

    if d.startswith("adm_new_adm_yes_"):
        if not (uid == ADMIN_ID or admin_has_perm(uid, PERM_ADMIN_MANAGE)):
            await q.answer("⛔ Access Denied", show_alert=True); return
        await q.answer()
        tid = int(d.split("_")[4])
        with _c() as c:
            r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tid,)).fetchone()
        cur_perms = set((r["permissions"] if r else "full").split(","))
        cur_perms.add(PERM_ADMIN_MANAGE)
        new_str = ",".join(sorted(cur_perms))
        with _c() as c:
            c.execute("UPDATE admins SET permissions=%s WHERE user_id=%s", (new_str, tid))
        pdisp = _perms_display(new_str)
        try:
            await ctx.bot.send_message(
                tid,
                f"👮 *আপনাকে Bot Admin হিসেবে যোগ করা হয়েছে!*\n\n"
                f"🔑 *পারমিশন:* {pdisp}\n"
                f"👮 *Admin যোগ/সরানো:* ✅ হ্যাঁ\n\n"
                f"/admin দিয়ে Admin Panel খুলুন।",
                parse_mode="Markdown")
        except: pass
        await q.message.reply_text(
            f"✅ *`{tid}` Admin হিসেবে যোগ হয়েছে।*\n\n🔑 {pdisp}\n👮 Admin যোগ/সরানো: ✅ হ্যাঁ",
            parse_mode="Markdown"); return

    if d.startswith("adm_new_adm_no_"):
        if not (uid == ADMIN_ID or admin_has_perm(uid, PERM_ADMIN_MANAGE)):
            await q.answer("⛔ Access Denied", show_alert=True); return
        await q.answer()
        tid = int(d.split("_")[4])
        with _c() as c:
            r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tid,)).fetchone()
        cur_perms = set((r["permissions"] if r else "full").split(","))
        cur_perms.discard(PERM_ADMIN_MANAGE)
        new_str = ",".join(sorted(cur_perms))
        with _c() as c:
            c.execute("UPDATE admins SET permissions=%s WHERE user_id=%s", (new_str, tid))
        pdisp = _perms_display(new_str)
        try:
            await ctx.bot.send_message(
                tid,
                f"👮 *আপনাকে Bot Admin হিসেবে যোগ করা হয়েছে!*\n\n"
                f"🔑 *পারমিশন:* {pdisp}\n"
                f"👮 *Admin যোগ/সরানো:* ❌ না\n\n"
                f"/admin দিয়ে Admin Panel খুলুন।",
                parse_mode="Markdown")
        except: pass
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
        with _c() as c:
            r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tgt_uid,)).fetchone()
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
        with _c() as c:
            c.execute("UPDATE admins SET permissions=%s WHERE user_id=%s", (new_str, tgt_uid))
        await _show_perm_picker(q.message, tgt_uid, ctx); return

    if d.startswith("adm_perm_save_"):
        if uid != ADMIN_ID: return
        tgt_uid = int(d.split("_")[3])
        with _c() as c:
            r = c.execute("SELECT permissions FROM admins WHERE user_id=%s", (tgt_uid,)).fetchone()
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
        except: pass
        return

    # ── Task Management ───────────────────────────────────────────
    if d == "adm_task_add":
        if not admin_has_perm(uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        await q.message.reply_text(
            "➕ *নতুন Task যোগ করুন*\n\nপ্রথমে *Task নাম* লিখুন:\n\n/cancel — বাতিল",
            parse_mode="Markdown")
        ctx.user_data["state"] = "adm_task_name"; return

    # ── Task type picker (regular task) ───────────────────────────
    if d.startswith("adm_task_type_"):
        if not is_admin(uid): return
        await q.answer()
        ttype = d[len("adm_task_type_"):]
        ctx.user_data["new_task_type"] = ttype
        ctx.user_data["state"] = "adm_task_price"
        icon, label = TASK_TYPE_MAP.get(ttype, ("📋", "Other"))
        name = ctx.user_data.get("new_task_name", "")
        await q.message.reply_text(
            f"✅ *নাম:* `{name}`\n🏷 *Type:* {icon} {label}\n\nএখন *মূল্য* লিখুন (যেমন: `0.10`):\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_task_list":
        tasks = get_all_tasks()
        if not tasks:
            await q.message.reply_text("📋 *কোনো Task নেই।*", parse_mode="Markdown"); return
        text = "📋 *Task তালিকা:*\n\n"
        btns = []
        for t in tasks:
            s  = "✅" if t["is_active"] else "❌"
            fields = get_task_fields(t['id'])
            field_count = len(fields)
            text += f"{s} *{t['name']}* — `{t['price']}$ USDT` | {field_count} field(s)\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {t['name']}", callback_data=f"adm_task_edit_{t['id']}"),
                InlineKeyboardButton("🗑 Delete",        callback_data=f"adm_task_del_{t['id']}"),
            ])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_task_del_"):
        tid  = int(d.split("_")[3])
        task = get_task(tid)
        if task:
            delete_task(tid)
            await q.message.reply_text(f"🗑 *'{task['name']}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_task_edit_"):
        tid  = int(d.split("_")[3])
        task = get_task(tid)
        if not task:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown"); return
        fields = get_task_fields(tid)
        status_s = "✅ Active" if task["is_active"] else "❌ Inactive"
        toggle_lbl = "🔴 Deactivate" if task["is_active"] else "🟢 Activate & Notify"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম",      callback_data=f"adm_te_name_{tid}"),
             InlineKeyboardButton("💰 দাম",       callback_data=f"adm_te_price_{tid}")],
            [InlineKeyboardButton("📜 নিয়মাবলী", callback_data=f"adm_te_rules_{tid}"),
             InlineKeyboardButton(toggle_lbl, callback_data=f"adm_te_toggle_{tid}")],
            [InlineKeyboardButton(f"📋 Proof Fields ({len(fields)})", callback_data=f"adm_fields_{tid}")],
            [InlineKeyboardButton("❌ বাতিল",     callback_data="task_cancel")],
        ])
        await q.message.reply_text(
            f"✏️ *Task Edit:* `{task['name']}`\n\n"
            f"💵 *দাম:* `{task['price']}$ USDT`\n"
            f"👁 *Status:* {status_s}\n"
            f"📋 *Proof Fields:* {len(fields)} টি\n"
            f"📜 *নিয়মাবলী:*\n{task['rules']}\n\nকী পরিবর্তন করবেন?",
            parse_mode="Markdown", reply_markup=kb); return

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
        task   = get_task(tid)
        fields = get_task_fields(tid)
        text   = f"📋 *Task Proof Fields: `{task['name']}`*\n\n"
        btns   = []
        if fields:
            for i, f in enumerate(fields, 1):
                icon = "📷" if f["field_type"] == "photo" else "📝"
                text += f"{i}. {icon} {f['prompt_bn']}\n"
                btns.append([InlineKeyboardButton(
                    f"🗑 {i}. {f['prompt_bn'][:20]}", callback_data=f"adm_field_del_{f['id']}_{tid}"
                )])
        else:
            text += "_কোনো field নেই — ইউজার থেকে generic তথ্য চাওয়া হবে।_\n"

        btns.append([
            InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
            InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}"),
        ])
        btns.append([InlineKeyboardButton("🔄 সব Fields মুছুন", callback_data=f"adm_fields_clear_{tid}")])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_fields_clear_"):
        tid = int(d.split("_")[2])
        delete_all_task_fields(tid)
        await q.message.reply_text("✅ *সব Fields মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_field_del_"):
        parts = d.split("_")
        fid   = int(parts[3])
        tid   = int(parts[4])
        delete_task_field(fid)
        await q.message.reply_text("✅ *Field মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

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
        channels = get_req_channels()
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
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_ch_add":
        ctx.user_data["state"] = "adm_ch_add_id"
        await q.message.reply_text(
            "📢 *নতুন Channel যোগ করুন*\n\n*Channel ID বা Username* দিন:\n_(যেমন: @mychannel)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_ch_del_"):
        rid = int(d.split("_")[3])
        remove_req_channel(rid)
        await q.message.reply_text("✅ *Channel সরানো হয়েছে।*", parse_mode="Markdown"); return

    # ── Category Task Management ─────────────────────────────────
    if d == "adm_cat_add":
        if not admin_has_perm(uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_cat_name"
        await q.message.reply_text(
            "📁 *নতুন Category যোগ করুন*\n\n*Category নাম* লিখুন:\n_(যেমন: Social Media Tasks)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d == "adm_cat_list":
        cats = get_all_categories()
        if not cats:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ নতুন Category যোগ করুন", callback_data="adm_cat_add")]])
            await q.message.reply_text("📁 *কোনো Category নেই।*", parse_mode="Markdown", reply_markup=kb); return
        text = "📁 *Category তালিকা:*\n\n"
        btns = []
        for cat in cats:
            s = "✅" if cat["is_active"] else "❌"
            tasks = get_category_tasks(cat['id'], active_only=False)
            text += f"{s} *{cat['name']}* — {len(tasks)} task(s)\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {cat['name']}", callback_data=f"adm_cat_edit_{cat['id']}"),
                InlineKeyboardButton("🗑 Delete",          callback_data=f"adm_cat_del_{cat['id']}"),
            ])
        btns.append([InlineKeyboardButton("➕ নতুন Category যোগ করুন", callback_data="adm_cat_add")])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_cat_del_"):
        cid = int(d.split("_")[3])
        cat = get_category(cid)
        if cat:
            delete_category(cid)
            await q.message.reply_text(f"🗑 *'{cat['name']}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Category পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_cat_edit_"):
        cid = int(d.split("_")[3])
        cat = get_category(cid)
        if not cat:
            await q.message.reply_text("❌ Category পাওয়া যায়নি!", parse_mode="Markdown"); return
        tasks = get_category_tasks(cid, active_only=False)
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
        cat = get_category(cid)
        if not cat: return
        new_val = 0 if cat["is_active"] else 1
        update_category(cid, "is_active", new_val)
        s = "✅ Active" if new_val else "❌ Inactive"
        await q.message.reply_text(f"👁 *'{cat['name']}' Status: {s}*", parse_mode="Markdown"); return

    if d.startswith("adm_cate_name_"):
        cid = int(d.split("_")[3])
        ctx.user_data["state"] = "adm_cat_edit_name"; ctx.user_data["edit_cat_id"] = cid
        await q.message.reply_text("✏️ *নতুন Category নাম লিখুন:*\n/cancel — বাতিল", parse_mode="Markdown"); return

    if d.startswith("adm_cate_task_add_"):
        cid = int(d.split("_")[4])
        ctx.user_data["state"] = "adm_cate_task_name"
        ctx.user_data["new_cate_cat_id"] = cid
        cat = get_category(cid)
        await q.message.reply_text(
            f"➕ *'{cat['name']}' — নতুন Task যোগ করুন*\n\nTask নাম লিখুন:\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    # ── Task type picker (category task) ──────────────────────────
    if d.startswith("adm_cate_task_type_"):
        if not is_admin(uid): return
        await q.answer()
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
        cat   = get_category(cid)
        tasks = get_category_tasks(cid, active_only=False)
        if not tasks:
            await q.message.reply_text(f"📋 *'{cat['name']}' তে কোনো Task নেই।*", parse_mode="Markdown"); return
        text = f"📋 *'{cat['name']}' Tasks:*\n\n"
        btns = []
        for t in tasks:
            s = "✅" if t["is_active"] else "❌"
            fields = get_category_task_fields(t['id'])
            text += f"{s} *{t['name']}* — `{t['price']}$` | {len(fields)} field(s)\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {t['name']}", callback_data=f"adm_ctask_edit_{t['id']}"),
                InlineKeyboardButton("🗑 Delete",         callback_data=f"adm_ctask_del_{t['id']}"),
            ])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d.startswith("adm_ctask_del_"):
        tid = int(d.split("_")[3])
        t = get_category_task(tid)
        if t:
            delete_category_task(tid)
            await q.message.reply_text(f"🗑 *'{t['name']}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_ctask_edit_"):
        tid = int(d.split("_")[3])
        t   = get_category_task(tid)
        if not t:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown"); return
        fields = get_category_task_fields(tid)
        s = "✅ Active" if t["is_active"] else "❌ Inactive"
        ctaste_toggle_lbl = "🔴 Deactivate" if t["is_active"] else "🟢 Activate & Notify"
        slots = f"{t['completed_count']}/{t['max_completions']}" if t['max_completions'] > 0 else f"{t['completed_count']}/∞"
        pu_limit = _rget(t, 'per_user_limit', 1) or 1
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম",       callback_data=f"adm_ctaste_name_{tid}"),
             InlineKeyboardButton("💰 দাম",        callback_data=f"adm_ctaste_price_{tid}")],
            [InlineKeyboardButton("📜 নিয়মাবলী",  callback_data=f"adm_ctaste_rules_{tid}"),
             InlineKeyboardButton(ctaste_toggle_lbl, callback_data=f"adm_ctaste_toggle_{tid}")],
            [InlineKeyboardButton("👥 Max জন",     callback_data=f"adm_ctaste_max_{tid}"),
             InlineKeyboardButton(f"📋 Proof Fields ({len(fields)})", callback_data=f"adm_cfields_{tid}")],
            [InlineKeyboardButton(f"👤 Per User Limit ({pu_limit}x)", callback_data=f"adm_ctaste_per_user_{tid}")],
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
        t = get_category_task(tid)
        cur = t['max_completions'] if t else 0
        ctx.user_data["state"] = "adm_ctask_edit_max"; ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text(
            f"👥 *সর্বোচ্চ কতজন করতে পারবে?*\n\n_বর্তমান: `{cur}` (0 = সীমাহীন)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_ctaste_per_user_"):
        tid = int(d.split("_")[4])
        t = get_category_task(tid)
        cur = _rget(t, 'per_user_limit', 1) or 1 if t else 1
        ctx.user_data["state"] = "adm_ctask_edit_per_user"; ctx.user_data["edit_ctask_id"] = tid
        await q.message.reply_text(
            f"👤 *প্রতি ইউজার কতবার এই Task করতে পারবে?*\n\n_বর্তমান: `{cur}` বার_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_cfields_"):
        tid    = int(d.split("_")[2])
        task   = get_category_task(tid)
        fields = get_category_task_fields(tid)
        text   = f"📋 *Category Task Proof Fields: `{task['name']}`*\n\n"
        btns   = []
        if fields:
            for i, f in enumerate(fields, 1):
                icon = "📷" if f["field_type"] == "photo" else "📝"
                text += f"{i}. {icon} {f['prompt_bn']}\n"
                btns.append([InlineKeyboardButton(
                    f"🗑 {i}. {f['prompt_bn'][:20]}", callback_data=f"adm_cfield_del_{f['id']}_{tid}"
                )])
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
        delete_all_category_task_fields(tid)
        await q.message.reply_text("✅ *সব Fields মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

    if d.startswith("adm_cfield_del_"):
        parts = d.split("_")
        fid   = int(parts[3])
        delete_category_task_field(fid)
        await q.message.reply_text("✅ *Field মুছে ফেলা হয়েছে।*", parse_mode="Markdown"); return

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
        tasks = get_all_tg_tasks()
        if not tasks:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ নতুন TG Task যোগ করুন", callback_data="adm_tgt_add")]])
            await q.message.reply_text("📡 *কোনো TG Task নেই।*", parse_mode="Markdown", reply_markup=kb); return
        text = "📡 *Telegram Task তালিকা:*\n\n"
        btns = []
        for t in tasks:
            s = "✅" if t["is_active"] else "❌"
            slots = f"{t['completed_count']}/{t['max_completions']}" if t['max_completions'] > 0 else f"{t['completed_count']}/∞"
            text += f"{s} *{t['name']}* — `{t['price']}$` | {slots} done\n📢 `{t['tg_target']}`\n\n"
            btns.append([
                InlineKeyboardButton(f"✏️ {t['name']}", callback_data=f"adm_tgt_edit_{t['id']}"),
                InlineKeyboardButton("🗑 Delete",         callback_data=f"adm_tgt_del_{t['id']}"),
            ])
        btns.append([InlineKeyboardButton("➕ নতুন TG Task যোগ করুন", callback_data="adm_tgt_add")])
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)); return

    if d == "adm_tgt_add":
        if not admin_has_perm(uid, PERM_TASKS):
            await q.message.reply_text("❌ *এই কাজের পারমিশন নেই।*", parse_mode="Markdown"); return
        ctx.user_data["state"] = "adm_tgt_name"
        await q.message.reply_text(
            "📡 *নতুন Telegram Task*\n\n"
            "প্রথমে *Task নাম* লিখুন:\n_(যেমন: XYZ Channel Join)_\n\n/cancel — বাতিল",
            parse_mode="Markdown"); return

    if d.startswith("adm_tgt_del_"):
        tid = int(d.split("_")[3])
        t = get_tg_task(tid)
        if t:
            delete_tg_task(tid)
            await q.message.reply_text(f"🗑 *'{t['name']}' মুছে ফেলা হয়েছে।*", parse_mode="Markdown")
        else:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown")
        return

    if d.startswith("adm_tgt_edit_"):
        tid = int(d.split("_")[3])
        t = get_tg_task(tid)
        if not t:
            await q.message.reply_text("❌ Task পাওয়া যায়নি!", parse_mode="Markdown"); return
        s = "✅ Active" if t["is_active"] else "❌ Inactive"
        tgte_toggle_lbl = "🔴 Deactivate" if t["is_active"] else "🟢 Activate & Notify"
        slots = f"{t['completed_count']}/{t['max_completions']}" if t['max_completions'] > 0 else f"{t['completed_count']}/∞"
        _tgte_ac = (_rget(t, 'allowed_countries', '') or '')
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ নাম",          callback_data=f"adm_tgte_name_{tid}"),
             InlineKeyboardButton("💰 দাম",           callback_data=f"adm_tgte_price_{tid}")],
            [InlineKeyboardButton("📢 Channel",       callback_data=f"adm_tgte_target_{tid}"),
             InlineKeyboardButton("🔗 Link",          callback_data=f"adm_tgte_link_{tid}")],
            [InlineKeyboardButton("👥 Max জন",       callback_data=f"adm_tgte_max_{tid}"),
             InlineKeyboardButton(tgte_toggle_lbl,    callback_data=f"adm_tgte_toggle_{tid}")],
            [InlineKeyboardButton("🌍 দেশ নির্ধারণ", callback_data=f"adm_tgte_country_{tid}")],
            [InlineKeyboardButton("❌ বাতিল",         callback_data="task_cancel")],
        ])
        await q.message.reply_text(
            f"✏️ *TG Task Edit:* `{t['name']}`\n\n"
            f"💰 *দাম:* `{t['price']}$ USDT`\n"
            f"📢 *Channel:* `{t['tg_target']}`\n"
            f"🔗 *Link:* `{t['tg_target_link'] or 'সেট নেই'}`\n"
            f"👥 *Completions:* {slots}\n"
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
        t = get_tg_task(tid)
        cur = t['max_completions'] if t else 0
        await q.message.reply_text(
            f"👥 *সর্বোচ্চ কতজন করতে পারবে?*\n\n_বর্তমান: `{cur}` (0 = সীমাহীন)_\n\nসংখ্যা লিখুন (0 = unlimited):\n/cancel — বাতিল",
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
        await update.message.reply_text(T(uid,'cancelled'), parse_mode="Markdown", reply_markup=main_kb(uid))
        return

    # ── Admin states ──────────────────────────────────────────────
    if is_admin(uid) and st and (st.startswith("adm_") or st == "adm_direct_msg"):

        if st == "adm_reject_reason":
            gid    = ctx.user_data.get("adm_reject_gid")
            reason = txt.strip()
            if not gid:
                ctx.user_data.pop("state", None); return
            with _c() as c:
                row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
            if not row or row["status"] != "pending":
                await update.message.reply_text("⚠️ Sub আর pending নেই।", parse_mode="Markdown")
                for k in ("state","adm_reject_gid","adm_reject_msg_id","adm_reject_chat_id",
                          "adm_reject_has_photo","adm_reject_orig_txt"):
                    ctx.user_data.pop(k, None)
                return
            with _c() as c:
                c.execute("UPDATE gmails SET status='rejected', reject_reason=%s WHERE id=%s", (reason, gid))
            guser = row["user_id"]
            _row_ttype    = _rget(row, 'task_type', 'other')
            _row_ticon    = task_type_icon(_row_ttype)
            _task_display = f"{_row_ticon} {row['task_name']}"
            # ── Point deduction ─────────────────────────────────────
            new_pts  = add_points(guser, -POINT_PER_REJECT)
            blocked  = new_pts <= POINT_BLOCK_AT
            # ── Notify user ─────────────────────────────────────────
            u_lang = get_lang(guser)
            if u_lang == 'bn':
                pt_line  = f"🔴 *পয়েন্ট:* `{new_pts}/100` *(-{POINT_PER_REJECT})*"
                blk_line = f"\n⛔ *পয়েন্ট ৩০ বা নিচে! {POINT_BLOCK_DAYS} দিনের জন্য Task বন্ধ।*" if blocked else "\nℹ️ নিয়মগুলো পড়ে পুনরায় চেষ্টা করুন।"
                rej_msg  = (
                    f"❌ *আপনার Submission Reject হয়েছে।*\n\n"
                    f"📌 *Task:* {_task_display}\n"
                    f"📝 *কারণ:* {reason}\n\n"
                    f"{pt_line}{blk_line}"
                )
            else:
                pt_line  = f"🔴 *Points:* `{new_pts}/100` *(-{POINT_PER_REJECT})*"
                blk_line = f"\n⛔ *Points at 30 or below! Tasks blocked for {POINT_BLOCK_DAYS} days.*" if blocked else "\nℹ️ Please re-read the rules and try again."
                rej_msg  = (
                    f"❌ *Your Submission has been Rejected.*\n\n"
                    f"📌 *Task:* {_task_display}\n"
                    f"📝 *Reason:* {reason}\n\n"
                    f"{pt_line}{blk_line}"
                )
            try:
                await ctx.bot.send_message(guser, rej_msg, parse_mode="Markdown")
            except: pass
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
                except: pass
            # ── Edit channel/other-side message ──────────────────────
            with _c() as c: row2 = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
            if row2:
                other_mid = row2["channel_msg_id"] if uid == ADMIN_ID else row2["admin_msg_id"]
                other_cid = get_gmail_ch_id() if uid == ADMIN_ID else ADMIN_ID
                if other_mid:
                    try:
                        if has_photo:
                            await ctx.bot.edit_message_caption(new_txt, chat_id=other_cid,
                                                               message_id=other_mid, parse_mode="Markdown", reply_markup=None)
                        else:
                            await ctx.bot.edit_message_text(new_txt, chat_id=other_cid,
                                                            message_id=other_mid, parse_mode="Markdown", reply_markup=None)
                    except: pass
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

        if st == "adm_direct_msg":
            target = ctx.user_data.get("direct_msg_to")
            if target:
                try:
                    await ctx.bot.send_message(target, txt, parse_mode="Markdown")
                    await update.message.reply_text(f"✅ *User `{target}` কে মেসেজ পাঠানো হয়েছে।*", parse_mode="Markdown")
                except Exception as e:
                    await update.message.reply_text(f"❌ ব্যর্থ: {e}", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("direct_msg_to", None); return

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
            if tp_type == "t": update_task_field_val(tp_tid, "name", txt.strip())
            else: update_tg_task(tp_tid, "name", txt.strip())
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
                if tp_type == "t": update_task_field_val(tp_tid, "price", price)
                else: update_tg_task(tp_tid, "price", price)
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
            except: await update.message.reply_text("❌ সঠিক দাম দিন (যেমন: 0.05)", parse_mode="Markdown")
            return

        if st == "adm_tp_edit_max":
            tp_tid  = ctx.user_data.get("tp_tid")
            tp_type = ctx.user_data.get("tp_type", "t")
            try: max_v = int(txt.strip())
            except: max_v = 0
            if tp_type == "t": update_task_field_val(tp_tid, "max_completions", max_v)
            else: update_tg_task(tp_tid, "max_completions", max_v)
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
            except: pu_val = 1
            if tp_type == "t": update_task_field_val(tp_tid, "per_user_limit", pu_val)
            else: update_tg_task(tp_tid, "per_user_limit", pu_val)
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
            await update.message.reply_text(f"✅ *Per User Limit আপডেট:* `{pu_val}` বার\n\nআবার চেক করুন:", parse_mode="Markdown", reply_markup=kb)
            return

        if st == "adm_tp_edit_countries":
            tp_tid  = ctx.user_data.get("tp_tid")
            tp_type = ctx.user_data.get("tp_type", "t")
            _ac2 = txt.strip().upper()
            allowed_c2 = "" if _ac2 in ("0","ALL","") else ",".join(c.strip() for c in _ac2.split(",") if c.strip())
            if tp_type == "t": update_task_field_val(tp_tid, "allowed_countries", allowed_c2)
            else: update_tg_task(tp_tid, "allowed_countries", allowed_c2)
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
            except: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            return
        if st == "adm_add_amt":
            try:
                amt = float(txt); tid = ctx.user_data["adm_tid"]
                add_bal(tid, amt)
                await update.message.reply_text(f"✅ *`{tid}` তে `{amt}$` যোগ হয়েছে।*", parse_mode="Markdown")
                try: await ctx.bot.send_message(tid, T(tid,'bal_added', amt=amt, bal=get_bal(tid)), parse_mode="Markdown")
                except: pass
            except: await update.message.reply_text("❌ সঠিক পরিমাণ দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_tid", None); return

        if st == "adm_ded_id":
            try:
                ctx.user_data["adm_tid"] = int(txt); ctx.user_data["state"] = "adm_ded_amt"
                await update.message.reply_text(f"💵 *কত ডলার কাটবেন?* (User: `{txt}`)", parse_mode="Markdown")
            except: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            return
        if st == "adm_ded_amt":
            try:
                amt = float(txt); tid = ctx.user_data["adm_tid"]
                ded_bal(tid, amt)
                await update.message.reply_text(f"✅ *`{tid}` থেকে `{amt}$` কাটা হয়েছে।*", parse_mode="Markdown")
            except: await update.message.reply_text("❌ সঠিক পরিমাণ দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_tid", None); return

        if st == "adm_ban_id":
            try:
                tid = int(txt); ban_user(tid, 1)
                await update.message.reply_text(f"🚫 *`{tid}` ব্যান হয়েছে।*", parse_mode="Markdown")
                try: await ctx.bot.send_message(tid, T(tid,'banned'), parse_mode="Markdown")
                except: pass
            except: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return
        if st == "adm_unban_id":
            try:
                tid = int(txt); ban_user(tid, 0)
                await update.message.reply_text(f"✅ *`{tid}` Unban হয়েছে।*", parse_mode="Markdown")
                try: await ctx.bot.send_message(tid, T(tid,'unban_notify'), parse_mode="Markdown")
                except: pass
            except: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        if st == "adm_msg_id":
            try:
                ctx.user_data["adm_tid"] = int(txt); ctx.user_data["state"] = "adm_msg_txt"
                await update.message.reply_text(f"📝 *User `{txt}` কে কি মেসেজ পাঠাবেন?*", parse_mode="Markdown")
            except: await update.message.reply_text("❌ সঠিক ID দিন।", parse_mode="Markdown")
            return
        if st == "adm_msg_txt":
            try:
                tid = ctx.user_data["adm_tid"]
                await ctx.bot.send_message(tid, txt, parse_mode="Markdown")
                await update.message.reply_text(f"✅ *`{tid}` কে মেসেজ পাঠানো হয়েছে।*", parse_mode="Markdown")
            except Exception as e: await update.message.reply_text(f"❌ ব্যর্থ: {e}", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_tid", None); return

        if st == "adm_task_code_input":
            code = txt.strip().upper()
            with _c() as c:
                # Regular task
                t_row = c.execute("SELECT * FROM tasks WHERE task_code=%s", (code,)).fetchone()
                # TG task
                tg_row = c.execute("SELECT * FROM tg_tasks WHERE task_code=%s", (code,)).fetchone()
                # Category task
                ct_row = c.execute("SELECT * FROM category_tasks WHERE task_code=%s", (code,)).fetchone()

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
                with _c() as c:
                    total   = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s", (code,)).fetchone())
                    pending = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='pending'", (code,)).fetchone())
                    approved= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='approved'", (code,)).fetchone())
                    rejected= _count(c.execute("SELECT COUNT(*) FROM gmails WHERE task_code=%s AND status='rejected'", (code,)).fetchone())
                    subs    = c.execute(
                        "SELECT id,user_id,status,submitted_at FROM gmails WHERE task_code=%s ORDER BY id DESC LIMIT 15",
                        (code,)
                    ).fetchall()
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
                with _c() as c:
                    total   = _count(c.execute("SELECT COUNT(*) FROM tg_task_completions WHERE task_id=%s", (tid,)).fetchone())
                    subs    = c.execute(
                        "SELECT user_id,joined_at FROM tg_task_completions WHERE task_id=%s ORDER BY id DESC LIMIT 15",
                        (tid,)
                    ).fetchall()
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

            await update.message.reply_text(text2, parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        if st == "adm_find_user_id":
            try:
                tid = int(txt.strip())
                u = get_user(tid)
                if not u:
                    await update.message.reply_text(f"❌ `{tid}` ID দিয়ে কোনো user পাওয়া যায়নি।", parse_mode="Markdown")
                else:
                    text2, kb2 = _build_user_profile(tid, u)
                    await update.message.reply_text(text2, parse_mode="Markdown", reply_markup=kb2)
            except:
                await update.message.reply_text("❌ সঠিক Telegram User ID দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        if st == "adm_uaddbal_amt":
            tid = ctx.user_data.get("adm_target")
            try:
                amt = float(txt.strip())
                if amt <= 0:
                    raise ValueError("amount must be positive")
                if get_user(tid) is None:
                    await update.message.reply_text(
                        f"❌ *User `{tid}` DB তে নেই!*\n\nUser প্রথমে bot-এ /start করুক, তারপর balance add করুন।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                ok = add_bal(tid, amt)
                if not ok:
                    await update.message.reply_text(
                        f"❌ *Balance update ব্যর্থ!* User `{tid}` DB তে পাওয়া যায়নি।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                new_bal = get_bal(tid)
                await update.message.reply_text(
                    f"✅ *`{tid}` কে `{amt}$` যোগ হয়েছে।*\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                try: await ctx.bot.send_message(
                    tid,
                    f"✅ আপনার Account-এ *{amt}$ USDT* যোগ করা হয়েছে।\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                except: pass
            except Exception as _e:
                await update.message.reply_text(f"❌ সঠিক পরিমাণ দিন। (Error: {_e})", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return

        if st == "adm_udedbal_amt":
            tid = ctx.user_data.get("adm_target")
            try:
                amt = float(txt.strip())
                if amt <= 0:
                    raise ValueError("amount must be positive")
                if get_user(tid) is None:
                    await update.message.reply_text(
                        f"❌ *User `{tid}` DB তে নেই!*\n\nUser প্রথমে bot-এ /start করুক।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                ok = ded_bal(tid, amt)
                if not ok:
                    await update.message.reply_text(
                        f"❌ *Balance update ব্যর্থ!* User `{tid}` DB তে পাওয়া যায়নি।",
                        parse_mode="Markdown")
                    ctx.user_data.pop("state", None); ctx.user_data.pop("adm_target", None); return
                new_bal = get_bal(tid)
                await update.message.reply_text(
                    f"✅ *`{tid}` থেকে `{amt}$` কাটা হয়েছে।*\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                try: await ctx.bot.send_message(
                    tid,
                    f"⚠️ আপনার Account থেকে *{amt}$ USDT* কাটা হয়েছে।\n💰 নতুন Balance: `{new_bal}$`",
                    parse_mode="Markdown")
                except: pass
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
            try:
                tid = int(txt.strip())
                if tid == ADMIN_ID:
                    await update.message.reply_text("ℹ️ এই ID ইতিমধ্যে Main Admin!", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                # Full access দিয়ে admin যোগ করো
                add_admin(tid, uid, permissions="full")
                ctx.user_data.pop("state", None)
                # YES/NO জিজ্ঞেস করো admin_manage সম্পর্কে
                await update.message.reply_text(
                    f"✅ *`{tid}` কে Full Access দিয়ে Admin যোগ করা হয়েছে।*\n\n"
                    f"👑 Full Access মানে:\n"
                    f"• আপনার কাছে যা যা আসে সব তার কাছেও যাবে\n"
                    f"• সব কিছু করতে পারবে (Task, Withdraw, Users, Broadcast ইত্যাদি)\n\n"
                    f"👮 *এই Admin কি অন্য Admin যোগ করতে ও সরাতে পারবে%s*",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ হ্যাঁ, পারবে", callback_data=f"adm_new_adm_yes_{tid}"),
                        InlineKeyboardButton("❌ না, পারবে না", callback_data=f"adm_new_adm_no_{tid}"),
                    ]])
                )
            except:
                await update.message.reply_text("❌ সঠিক Telegram User ID দিন।", parse_mode="Markdown")
                ctx.user_data.pop("state", None)
            return

        # ── ID দিয়ে Submission Approve ─────────────────────────────
        if st == "adm_approve_id_num":
            try:
                gid = int(txt.strip())
                with _c() as c:
                    row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
                if not row:
                    await update.message.reply_text(f"❌ *Sub `#{gid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *Sub `#{gid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                with _c() as c:
                    c.execute("UPDATE gmails SET status='approved', approved_at=%s WHERE id=%s", (now(), gid))
                guser, price = row["user_id"], row["amount"]
                _aid_ttype = _rget(row, 'task_type', 'other')
                _aid_ticon = task_type_icon(_aid_ttype)
                _aid_task_display = f"{_aid_ticon} {row['task_name']}"
                add_bal(guser, price)
                ref = get_referred_by(guser)
                if ref:
                    comm_pct = get_commission_pct()
                    comm = round(price * comm_pct / 100.0, 4)
                    add_bal(ref, comm)
                    try:
                        ru = get_user(guser); rname = (ru["full_name"] or ru["username"] or "Unknown") if ru else "Unknown"
                        comm_d = int(comm_pct) if comm_pct == int(comm_pct) else comm_pct
                        await ctx.bot.send_message(ref, T(ref,'commission_msg',name=rname,price=price,comm=comm,comm_pct=comm_d), parse_mode="Markdown")
                    except: pass
                try:
                    await ctx.bot.send_message(guser, T(guser,'approved_msg',task=_aid_task_display,price=price), parse_mode="Markdown")
                except: pass
                asyncio.create_task(_lb_refresh_now(ctx.bot))
                # Regular task completed_count + max check
                _mid_code = _rget(row, 'task_code', '') or ''
                _mid_name = row['task_name']
                _mt, _mmaxed = _regular_task_mark_done(_mid_code, _mid_name)
                if _mmaxed and _mt:
                    asyncio.create_task(_notify_task_maxed(
                        ctx.bot, _mt["name"], _mt["task_code"] or "",
                        "Regular Task", _mt["completed_count"], _mt["max_completions"]
                    ))
                await update.message.reply_text(
                    f"✅ *Sub `#{gid}` Approved!*\n\n"
                    f"📌 *Task:* {row['task_name']}\n"
                    f"👤 *User:* `{guser}`\n"
                    f"💰 *{price}$ USDT* balance-এ যোগ হয়েছে।",
                    parse_mode="Markdown")
                a_mid = row["admin_msg_id"]; c_mid = row["channel_msg_id"]
                status_line = "\n\n✅ *APPROVED by Admin*"
                for chat_id, msg_id in [(ADMIN_ID, a_mid), (get_gmail_ch_id(), c_mid)]:
                    if msg_id:
                        try: await ctx.bot.edit_message_text(("(Submission)\n") + status_line, chat_id=chat_id, message_id=msg_id, parse_mode="Markdown", reply_markup=None)
                        except: pass
            except ValueError:
                await update.message.reply_text("❌ সঠিক ID দিন (শুধু সংখ্যা, যেমন: `15`)।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return

        # ── ID দিয়ে Submission Reject ──────────────────────────────
        if st == "adm_reject_id_num":
            try:
                gid = int(txt.strip())
                with _c() as c:
                    row = c.execute("SELECT * FROM gmails WHERE id=%s", (gid,)).fetchone()
                if not row:
                    await update.message.reply_text(f"❌ *Sub `#{gid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *Sub `#{gid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                with _c() as c:
                    c.execute("UPDATE gmails SET status='rejected' WHERE id=%s", (gid,))
                guser = row["user_id"]
                _rid_ttype = _rget(row, 'task_type', 'other')
                _rid_ticon = task_type_icon(_rid_ttype)
                _rid_task_display = f"{_rid_ticon} {row['task_name']}"
                _rid_new_pts = add_points(guser, -POINT_PER_REJECT)
                _rid_blocked = _rid_new_pts <= POINT_BLOCK_AT
                u_lang = get_lang(guser)
                if u_lang == 'bn':
                    _rid_msg = (
                        f"❌ *আপনার Submission Reject হয়েছে।*\n\n"
                        f"📌 *Task:* {_rid_task_display}\n\n"
                        f"🔴 *পয়েন্ট:* `{_rid_new_pts}/100` *(-{POINT_PER_REJECT})*"
                        + (f"\n⛔ *পয়েন্ট ৩০ বা নিচে! {POINT_BLOCK_DAYS} দিনের জন্য Task বন্ধ।*" if _rid_blocked
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
                except: pass
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
                with _c() as c:
                    row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
                if not row:
                    await update.message.reply_text(f"❌ *WD `#{wid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *WD `#{wid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                with _c() as c:
                    c.execute("UPDATE withdrawals SET status='approved' WHERE id=%s", (wid,))
                wuser, wamt, waddr = row["user_id"], row["amount"], row["address"]
                masked = mask_addr_short(waddr)
                try:
                    await ctx.bot.send_message(wuser, T(wuser,'wd_approved_msg',amt=wamt,addr=masked), parse_mode="Markdown")
                except: pass
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
                with _c() as c:
                    row = c.execute("SELECT * FROM withdrawals WHERE id=%s", (wid,)).fetchone()
                if not row:
                    await update.message.reply_text(f"❌ *WD `#{wid}` পাওয়া যায়নি।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                if row["status"] != "pending":
                    await update.message.reply_text(f"⚠️ *WD `#{wid}` ইতিমধ্যে `{row['status']}`।*", parse_mode="Markdown")
                    ctx.user_data.pop("state", None); return
                with _c() as c:
                    c.execute("UPDATE withdrawals SET status='rejected' WHERE id=%s", (wid,))
                wuser, wamt = row["user_id"], row["amount"]
                add_bal(wuser, wamt)
                try:
                    await ctx.bot.send_message(wuser, T(wuser,'wd_rejected_msg',amt=wamt), parse_mode="Markdown")
                except: pass
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
            except: await update.message.reply_text("❌ সঠিক মূল্য দিন (যেমন: 0.10)", parse_mode="Markdown")
            return

        if st == "adm_task_rules":
            ctx.user_data["new_task_rules"] = txt
            ctx.user_data["state"] = "adm_task_max"
            await update.message.reply_text(
                f"✅ *নিয়মাবলী সেভ হয়েছে।*\n\n"
                "👥 *সর্বোচ্চ কতজন এই Task করতে পারবে%s*\n"
                "_(0 লিখলে Unlimited — যেকোনো সংখ্যা দিন)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_task_max":
            try: max_c = max(0, int(txt.strip()))
            except: max_c = 0
            ctx.user_data["new_task_max"] = max_c
            ctx.user_data["state"] = "adm_task_per_user"
            max_lbl = "Unlimited ♾️" if max_c == 0 else f"{max_c} জন"
            await update.message.reply_text(
                f"✅ *Max:* `{max_lbl}`\n\n"
                "👤 *প্রতিটি ইউজার সর্বোচ্চ কতবার এই Task করতে পারবে%s*\n"
                "_(0 লিখলে Unlimited — যেমন: 1, 2, 3...)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_task_per_user":
            try:
                per_user_limit = max(0, int(txt.strip()))
            except:
                per_user_limit = 1
            ctx.user_data["new_task_per_user"] = per_user_limit
            ctx.user_data["state"] = "adm_task_countries"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
            await update.message.reply_text(
                f"✅ *Per User:* `{per_lbl}`\n\n"
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে%s*\n\n"
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
            max_c     = ctx.user_data.pop("new_task_max", 0)
            per_user_limit = ctx.user_data.pop("new_task_per_user", 1)
            rules_en_val = translate_to_en(rules)
            task_id = add_task(name, price, rules, rules_en_val, task_type=ttype, per_user_limit=per_user_limit, max_completions=max_c, allowed_countries=allowed_c)
            ctx.user_data["new_task_id"] = task_id
            ctx.user_data["state"] = "adm_task_fields"
            ctx.user_data.pop("new_task_name", None); ctx.user_data.pop("new_task_price", None)
            max_lbl = "Unlimited ♾️" if max_c == 0 else f"{max_c} জন"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{task_id}"),
                 InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{task_id}")],
                [InlineKeyboardButton("✅ Done — Task সেভ করুন", callback_data=f"adm_task_done_{task_id}")],
            ])
            await update.message.reply_text(
                f"✅ *Task প্রায় তৈরি!*\n📌 `{name}` | 💵 `{price}$`\n"
                f"👥 *Max:* `{max_lbl}` | 👤 *Per User:* `{per_lbl}`\n"
                f"🌍 *দেশ:* {fmt_allowed_countries(allowed_c)}\n\n"
                "এখন *Proof Fields* যোগ করুন — ইউজার থেকে কী কী নেওয়া হবে%s\n\n"
                "_(Field না যোগ করলে generic তথ্য চাওয়া হবে)_",
                parse_mode="Markdown", reply_markup=kb); return

        if st == "adm_task_edit_name_done":
            pass  # handled below

        if st == "adm_edit_name":
            tid = ctx.user_data.get("edit_tid"); update_task_field_val(tid, "name", txt)
            await update.message.reply_text(f"✅ *নাম আপডেট:* `{txt}`", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tid", None); return
        if st == "adm_edit_price":
            tid = ctx.user_data.get("edit_tid")
            try:
                price = float(txt); update_task_field_val(tid, "price", price)
                await update.message.reply_text(f"✅ *দাম আপডেট:* `{price}$`", parse_mode="Markdown")
            except: await update.message.reply_text("❌ সঠিক দাম দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tid", None); return
        if st == "adm_edit_rules":
            tid = ctx.user_data.get("edit_tid")
            update_task_field_val(tid, "rules", txt)
            rules_en_val = translate_to_en(txt)
            update_task_field_val(tid, "rules_en", rules_en_val)
            await update.message.reply_text("✅ *নিয়মাবলী আপডেট হয়েছে।*", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tid", None); return

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
            prompt_en = txt if txt.strip() not in ("-","–","—") else translate_to_en(prompt_bn)
            add_task_field(tid, ftype, prompt_bn, prompt_en)
            icon = "📝" if ftype == "text" else "📷"
            task = get_task(tid)
            fields = get_task_fields(tid)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Text Field যোগ",  callback_data=f"adm_field_add_text_{tid}"),
                 InlineKeyboardButton("📷 Photo Field যোগ", callback_data=f"adm_field_add_photo_{tid}")],
                [InlineKeyboardButton("✅ Done", callback_data=f"adm_task_done_{tid}")],
            ])
            await update.message.reply_text(
                f"✅ *Field যোগ হয়েছে!*\n{icon} `{prompt_bn}` / `{prompt_en}`\n\n"
                f"মোট fields: {len(fields)} টি\n\nআরো field যোগ করুন বা Done চাপুন:",
                parse_mode="Markdown", reply_markup=kb)
            for k in ("new_field_type","new_field_tid","new_field_prompt_bn","state"):
                ctx.user_data.pop(k, None)
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
            add_req_channel(ch_id, ch_link, ch_name)
            await update.message.reply_text(
                f"✅ *Channel যোগ হয়েছে!*\n📛 {ch_name}\n🔗 `{ch_id}`",
                parse_mode="Markdown", reply_markup=main_kb(uid))
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
                set_btn(btn_key, bn, en)
                await update.message.reply_text(
                    f"✅ *Button `{btn_key}` আপডেট!*\n🇧🇩 `{bn}` | 🇬🇧 `{en}`",
                    parse_mode="Markdown", reply_markup=main_kb(uid))
            else:
                await update.message.reply_text(
                    "❌ ফরম্যাট ঠিক নেই।\n\nউদাহরণ: `📋 কাজ | 📋 Tasks`",
                    parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_btn_key", None); return

        # Bot settings
        if st == "adm_set_support_owner":
            set_setting('support_owner', txt.strip())
            await update.message.reply_text(f"✅ *Support Owner আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_support_bot":
            set_setting('support_bot', txt.strip())
            await update.message.reply_text(f"✅ *Support Bot আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_min_withdraw":
            try:
                val = float(txt.strip())
                set_setting('min_withdraw', str(val))
                await update.message.reply_text(f"✅ *Min Withdraw আপডেট:* `{val}$ USDT`", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return
        if st == "adm_set_commission_pct":
            try:
                val = float(txt.strip())
                set_setting('commission_pct', str(val))
                await update.message.reply_text(f"✅ *Commission আপডেট:* `{val}%`", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return
        if st == "adm_set_gmail_ch_id":
            set_setting('gmail_ch_id', txt.strip())
            await update.message.reply_text(f"✅ *Task Channel ID আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_withdraw_ch_id":
            set_setting('withdraw_ch_id', txt.strip())
            await update.message.reply_text(f"✅ *Withdraw Channel ID আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_bsc_wallet":
            new_addr = txt.strip()
            if not new_addr.startswith("0x") or len(new_addr) != 42:
                await update.message.reply_text(
                    "❌ *অবৈধ BSC Address!*\n\n`0x` দিয়ে শুরু হতে হবে এবং মোট ৪২ character হতে হবে।\nআবার লিখুন বা /cancel করুন।",
                    parse_mode="Markdown"); return
            set_setting('bsc_wallet_addr', new_addr)
            bscscan = f"https://bscscan.com/address/{new_addr}"
            await update.message.reply_text(
                f"✅ *BSC Wallet Address আপডেট হয়েছে!*\n\n"
                f"📍 *নতুন Address:* `{new_addr}`\n"
                f"🌐 [BSCScan এ দেখুন]({bscscan})",
                parse_mode="Markdown", disable_web_page_preview=False, reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_gmail_price":
            try:
                val = float(txt.strip())
                set_setting('gmail_price', str(val))
                await update.message.reply_text(f"✅ *Default Task Price আপডেট:* `{val}$ USDT`", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); return
        if st == "adm_set_welcome_bn":
            new_val = "" if txt.strip() in ("-","–","—") else txt.strip()
            set_setting('welcome_msg_bn', new_val)
            msg = "✅ *বাংলা Welcome Message আপডেট হয়েছে।*" if new_val else "✅ *Default এ ফিরিয়ে দেওয়া হয়েছে।*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return
        if st == "adm_set_welcome_en":
            new_val = "" if txt.strip() in ("-","–","—") else txt.strip()
            set_setting('welcome_msg_en', new_val)
            msg = "✅ *English Welcome Message updated.*" if new_val else "✅ *Reset to Default.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return

        if st == "adm_set_task_notify_ch":
            set_setting('task_notify_ch_id', txt.strip())
            await update.message.reply_text(f"✅ *Task Notify Channel ID আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return

        if st == "adm_set_notify_ch_username":
            val = txt.strip()
            if not val.startswith('@'): val = '@' + val
            set_setting('task_notify_ch_username', val)
            await update.message.reply_text(f"✅ *Notification Channel Username আপডেট:* `{val}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); return

        if st == "adm_set_notify_delay":
            try:
                delay = int(txt.strip())
                if delay < 0: raise ValueError
                set_setting('notify_delay_min', str(delay))
                msg = (f"✅ *Bot Notify Delay আপডেট:* `{delay} মিনিট`\n\n"
                       f"📌 Channel এ সাথে সাথে, Bot users এ {delay} মিনিট পরে।"
                       if delay > 0 else
                       "✅ *Bot Notify Delay আপডেট: সাথে সাথে (0 মিনিট)*")
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(uid))
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
            except: await update.message.reply_text("❌ সঠিক দাম দিন (যেমন: 0.05)", parse_mode="Markdown")
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
                f"✅ *Display নাম:* `{txt.strip()}`\n\nসর্বোচ্চ কতজন এই Task করতে পারবে%s\n_(0 = unlimited)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_max":
            try:
                max_c = int(txt.strip())
            except: max_c = 0
            ctx.user_data["new_tgt_max"] = max_c
            ctx.user_data["state"] = "adm_tgt_per_user"
            await update.message.reply_text(
                f"✅ *Max:* `{max_c if max_c > 0 else 'Unlimited'}`\n\n"
                "👤 *প্রতিটি ইউজার এই Task সর্বোচ্চ কতবার করতে পারবে%s*\n"
                "_(TG Task এ সাধারণত 1 রাখুন)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_tgt_per_user":
            try:
                per_user_limit = max(0, int(txt.strip()))
            except:
                per_user_limit = 1
            ctx.user_data["new_tgt_per_user"] = per_user_limit
            ctx.user_data["state"] = "adm_tgt_countries"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
            await update.message.reply_text(
                f"✅ *Per User:* `{per_lbl}`\n\n"
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে%s*\n\n"
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
            tid_new = add_tg_task(name, price, target, link, dname, max_c, per_user_limit=per_user_limit, allowed_countries=allowed_c)
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
            update_tg_task(tid, "name", txt.strip())
            await update.message.reply_text(f"✅ *নাম আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_price":
            tid = ctx.user_data.get("edit_tgt_id")
            try:
                price = float(txt.strip())
                update_tg_task(tid, "price", price)
                await update.message.reply_text(f"✅ *দাম আপডেট:* `{price}$`", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক দাম দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_target":
            tid = ctx.user_data.get("edit_tgt_id")
            update_tg_task(tid, "tg_target", txt.strip())
            await update.message.reply_text(f"✅ *Channel আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_link":
            tid = ctx.user_data.get("edit_tgt_id")
            update_tg_task(tid, "tg_target_link", txt.strip())
            await update.message.reply_text(f"✅ *Link আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_max":
            tid = ctx.user_data.get("edit_tgt_id")
            try:
                max_c = int(txt.strip())
                update_tg_task(tid, "max_completions", max_c)
                max_txt = f"{max_c} জন" if max_c > 0 else "সীমাহীন"
                await update.message.reply_text(f"✅ *Max Completions আপডেট:* {max_txt}", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return
        if st == "adm_tgt_edit_per_user":
            tid = ctx.user_data.get("edit_tgt_id")
            try:
                pu_val = max(1, int(txt.strip()))
                update_tg_task(tid, "per_user_limit", pu_val)
                await update.message.reply_text(f"✅ *Per User Limit আপডেট:* `{pu_val}` বার", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return

        if st == "adm_tgt_edit_countries":
            tid = ctx.user_data.get("edit_tgt_id")
            _ac3 = txt.strip().upper()
            allowed_c3 = "" if _ac3 in ("0","ALL","") else ",".join(c.strip() for c in _ac3.split(",") if c.strip())
            update_tg_task(tid, "allowed_countries", allowed_c3)
            await update.message.reply_text(
                f"✅ *দেশ আপডেট:* {fmt_allowed_countries(allowed_c3)}",
                parse_mode="Markdown", reply_markup=main_kb(uid))
            ctx.user_data.pop("state", None); ctx.user_data.pop("edit_tgt_id", None); return

    # ── Category admin creation states ───────────────────────────
        if st == "adm_cat_name":
            name = txt.strip()
            cid  = add_category(name)
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
                update_category(cid, "name", txt.strip())
                await update.message.reply_text(f"✅ *Category নাম আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid))
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
            except:
                await update.message.reply_text("❌ সঠিক দাম দিন (যেমন: `0.10`):", parse_mode="Markdown"); return
            ctx.user_data["new_cate_task_price"] = price
            ctx.user_data["state"] = "adm_cate_task_max"
            await update.message.reply_text(
                f"✅ *দাম:* `{price}$`\n\n👥 *সর্বোচ্চ কতজন করতে পারবে%s*\n_(0 = সীমাহীন)_\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cate_task_max":
            try: max_c = int(txt.strip())
            except: max_c = 0
            ctx.user_data["new_cate_task_max"] = max_c
            ctx.user_data["state"] = "adm_cate_task_per_user"
            await update.message.reply_text(
                f"✅ *Max:* `{max_c if max_c > 0 else '∞'}`\n\n"
                "👤 *প্রতিটি ইউজার এই Task সর্বোচ্চ কতবার করতে পারবে%s*\n"
                "_(যেমন: 1 বার, 2 বার — শুধু সংখ্যা লিখুন)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_cate_task_per_user":
            try:
                per_user_limit = max(0, int(txt.strip()))
            except:
                per_user_limit = 1
            ctx.user_data["new_cate_task_per_user"] = per_user_limit
            ctx.user_data["state"] = "adm_cate_task_countries"
            per_lbl = "Unlimited ♾️" if per_user_limit == 0 else f"{per_user_limit} বার"
            _ch = "BD IN PK NP LK MY PH ID TH MM VN NG GH KE US GB AU AE SA OT"
            await update.message.reply_text(
                f"✅ *Per User Limit:* `{per_lbl}`\n\n"
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে%s*\n\n"
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
            cid   = ctx.user_data.pop("new_cate_cat_id", None)
            name  = ctx.user_data.pop("new_cate_task_name", "")
            price = ctx.user_data.pop("new_cate_task_price", 0.05)
            max_c = ctx.user_data.pop("new_cate_task_max", 0)
            ttype = ctx.user_data.pop("new_cate_task_type", "other")
            per_user_limit = ctx.user_data.pop("new_cate_task_per_user", 1)
            allowed_c = ctx.user_data.pop("new_cate_task_countries", "")
            ctx.user_data.pop("state", None)
            rules = txt.strip()
            tid   = add_category_task(cid, name, price, rules, max_comp=max_c, task_type=ttype, per_user_limit=per_user_limit, allowed_countries=allowed_c)
            country_lbl = fmt_allowed_countries(allowed_c)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📷 Photo Field যোগ",  callback_data=f"adm_cfield_add_photo_{tid}"),
                 InlineKeyboardButton("📝 Text Field যোগ",   callback_data=f"adm_cfield_add_text_{tid}")],
                [InlineKeyboardButton("🚀 এখনই Live করুন (Proof ছাড়া)", callback_data=f"adm_ctask_golive_{tid}")],
            ])
            await update.message.reply_text(
                f"✅ *Category Task তৈরি হয়েছে!*\n\n"
                f"📌 *Task:* `{name}`\n"
                f"💰 *দাম:* `{price}$`\n"
                f"👥 *Max:* `{max_c if max_c > 0 else '∞'}`\n"
                f"🌍 *দেশ:* {country_lbl}\n\n"
                f"📋 Proof Fields যোগ করতে পারেন, অথবা সরাসরি Live করুন:",
                parse_mode="Markdown", reply_markup=kb); return

        if st == "adm_ctask_edit_name":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            if tid: update_category_task(tid, "name", txt.strip())
            await update.message.reply_text(f"✅ *Task নাম আপডেট:* `{txt.strip()}`", parse_mode="Markdown", reply_markup=main_kb(uid)); return

        if st == "adm_ctask_edit_price":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            try:
                price = float(txt.strip())
                if tid: update_category_task(tid, "price", price)
                await update.message.reply_text(f"✅ *দাম আপডেট:* `{price}$`", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক দাম দিন।", parse_mode="Markdown")
            return

        if st == "adm_ctask_edit_rules":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            if tid: update_category_task(tid, "rules", txt.strip())
            await update.message.reply_text("✅ *নিয়মাবলী আপডেট হয়েছে।*", parse_mode="Markdown", reply_markup=main_kb(uid)); return

        if st == "adm_ctask_edit_max":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            try:
                max_c = int(txt.strip())
                if tid: update_category_task(tid, "max_completions", max_c)
                max_txt = f"{max_c} জন" if max_c > 0 else "সীমাহীন"
                await update.message.reply_text(f"✅ *Max Completions আপডেট:* {max_txt}", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            return

        if st == "adm_ctask_edit_per_user":
            tid = ctx.user_data.pop("edit_ctask_id", None)
            ctx.user_data.pop("state", None)
            try:
                pu_val = max(0, int(txt.strip()))
                if tid: update_category_task(tid, "per_user_limit", pu_val)
                per_lbl = "Unlimited ♾️" if pu_val == 0 else f"{pu_val} বার"
                await update.message.reply_text(f"✅ *Per User Limit আপডেট:* `{per_lbl}`", parse_mode="Markdown", reply_markup=main_kb(uid))
            except: await update.message.reply_text("❌ সঠিক সংখ্যা দিন।", parse_mode="Markdown")
            return

        # ── Promo code creation states ─────────────────────────────
        if st == "adm_promo_code":
            code = txt.strip().upper().replace(" ", "")
            if not code:
                await update.message.reply_text("❌ Code খালি রাখা যাবে না।", parse_mode="Markdown"); return
            # duplicate check
            with _c() as c:
                exists = c.execute("SELECT id FROM promocodes WHERE code=%s", (code,)).fetchone()
            if exists:
                await update.message.reply_text(f"❌ `{code}` Code আগেই আছে। অন্য Code দিন:", parse_mode="Markdown"); return
            ctx.user_data["promo_code"] = code
            ctx.user_data["state"]      = "adm_promo_amount"
            await update.message.reply_text(
                f"✅ *Code:* `{code}`\n\n💰 *এখন Reward Amount লিখুন (USDT):*\n_(যেমন: 0.05)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_promo_amount":
            try:
                amount = float(txt.strip())
                if amount <= 0: raise ValueError()
            except:
                await update.message.reply_text("❌ সঠিক Amount দিন (যেমন: 0.05):", parse_mode="Markdown"); return
            ctx.user_data["promo_amount"] = amount
            ctx.user_data["state"]        = "adm_promo_max_uses"
            await update.message.reply_text(
                f"✅ *Reward:* `{amount}$ USDT`\n\n👥 *সর্বোচ্চ কতজন এই Code ব্যবহার করতে পারবে%s*\n_(যেমন: 50)_\n\n/cancel — বাতিল",
                parse_mode="Markdown"); return

        if st == "adm_promo_max_uses":
            try:
                max_uses = int(txt.strip())
                if max_uses <= 0: raise ValueError()
            except:
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
            except:
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
                add_category_task_field(tid, ftype, prompt_bn, prompt_en)
                icon = "📝" if ftype == "text" else "📷"
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
    if not is_admin(uid) and not bot_is_on():
        if st: ctx.user_data.clear()
        await bot_off_reply(update, uid); return

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
            lang = get_lang(uid)
            await update.message.reply_text(
                "📷 Please send a photo now, not text." if lang == 'en' else "📷 এখন ছবি পাঠান, টেক্সট নয়।",
                parse_mode="Markdown"); return
        collected.append({"type": "text", "prompt_bn": field["prompt_bn"], "prompt_en": field.get("prompt_en",""), "value": txt.strip()})
        ctx.user_data["field_idx"] = idx + 1
        next_idx = idx + 1
        if next_idx < len(fields):
            nf = fields[next_idx]
            lang = get_lang(uid)
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
        lang = get_lang(uid)
        with _c() as c:
            promo = c.execute("SELECT * FROM promocodes WHERE code=%s", (code,)).fetchone()
        if not promo:
            msg = "❌ *এই Promo Code টি বিদ্যমান নেই।*" if lang == 'bn' else "❌ *This promo code does not exist.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(uid)); return
        if promo["used_count"] >= promo["max_uses"]:
            msg = "❌ *এই Promo Code টি সম্পূর্ণ ব্যবহার হয়ে গেছে।*" if lang == 'bn' else "❌ *This promo code has been fully used.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(uid)); return
        with _c() as c:
            already = c.execute("SELECT id FROM promo_uses WHERE code_id=%s AND user_id=%s", (promo["id"], uid)).fetchone()
        if already:
            msg = "⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn' else "⚠️ *You have already used this promo code.*"
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(uid)); return
        min_tasks = promo["min_tasks"] or 0
        if min_tasks > 0:
            with _c() as c:
                done_count = _count(c.execute("SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'", (uid,)).fetchone())
            if done_count < min_tasks:
                msg = (
                    f"⚠️ *এই Code রিডিম করতে কমপক্ষে {min_tasks}টি Task Approved হতে হবে।*\n\n"
                    f"আপনার এখন: `{done_count}/{min_tasks}` টি approved task আছে।"
                    if lang == 'bn' else
                    f"⚠️ *You need at least {min_tasks} approved tasks to redeem this code.*\n\n"
                    f"You currently have: `{done_count}/{min_tasks}` approved tasks."
                )
                await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(uid)); return
        amount = promo["amount"]
        add_bal(uid, amount)
        with _c() as c:
            c.execute("INSERT INTO promo_uses (code_id, user_id, used_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (promo["id"], uid, now()))
            c.execute("UPDATE promocodes SET used_count=used_count+1 WHERE id=%s", (promo["id"],))
            new_used = _fval(c.execute("SELECT used_count FROM promocodes WHERE id=%s", (promo["id"],)).fetchone())
        ok_msg = (
            f"✅ *Promo Code রিডিম সফল!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` যোগ হয়েছে।\n"
            f"💼 *বর্তমান Balance:* `{get_bal(uid)}$ USDT`"
            if lang == 'bn' else
            f"✅ *Promo Code redeemed!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` added.\n"
            f"💼 *Current Balance:* `{get_bal(uid)}$ USDT`"
        )
        await update.message.reply_text(ok_msg, parse_mode="Markdown", reply_markup=main_kb(uid))

        # ── Admin notification: কে নিল, কতজন নিল, কতটা বাকি ──────
        remaining = max(0, promo["max_uses"] - new_used)
        uname = update.effective_user.username or ""
        uname_txt = f"@{uname}" if uname else f"ID:`{uid}`"
        full_name = (update.effective_user.full_name or "").strip() or "—"
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
        asyncio.create_task(notify_full_admins(ctx.bot, admin_notif))

        # ── News channel message edit করো (code শেষ হলে) ─────────
        if new_used >= promo["max_uses"]:
            nc_msg_id  = promo.get("news_ch_msg_id")  or 0
            nc_chat_id = promo.get("news_ch_chat_id") or 0
            if nc_msg_id and nc_chat_id:
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=nc_chat_id, message_id=nc_msg_id,
                        text=(
                            f"🔑 *Code:* `{code}`\n\n"
                            f"❌ *এই Promo Code সম্পূর্ণ ব্যবহার হয়ে গেছে।*\n"
                            f"👥 *মোট ব্যবহার:* `{new_used}/{promo['max_uses']}`"
                        ),
                        parse_mode="Markdown")
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
        lang = get_lang(uid)
        hint = ("👆 Please press *Confirm* or *Edit* button above." if lang == 'en'
                else "👆 উপরের *Confirm* বা *Edit* বাটন চাপুন।")
        await update.message.reply_text(hint, parse_mode="Markdown")
        await show_wd_confirmation(update.message, ctx, uid); return

    # ── Menu buttons ──────────────────────────────────────────────
    if not await guard(update, ctx): return

    lang = get_lang(uid)
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
        await update.message.reply_text(T(uid,'choose_menu'), parse_mode="Markdown", reply_markup=main_kb(uid))

# ──────────────────────────── PHOTO HANDLER ────────────────────────────
async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    uid = update.effective_user.id
    st  = ctx.user_data.get("state")

    if not is_admin(uid) and not bot_is_on():
        await bot_off_reply(update, uid); return

    if st == "adm_bc_photo" and is_admin(uid):
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

    if st == "task_collect":
        if not await guard(update, ctx): return
        fields = ctx.user_data.get("task_fields", [])
        idx    = ctx.user_data.get("field_idx", 0)
        if idx < len(fields) and fields[idx]["field_type"] == "photo":
            await handle_task_collect_photo(update, ctx)
        else:
            lang = get_lang(uid)
            await update.message.reply_text(
                "❌ Send text now, not a photo." if lang == 'en' else "❌ এখন ছবি নয়, টেক্সট পাঠান।",
                parse_mode="Markdown")

    elif st == "categ_task_collect":
        if not await guard(update, ctx): return
        fields    = ctx.user_data.get("task_fields", [])
        idx       = ctx.user_data.get("field_idx", 0)
        collected = ctx.user_data.setdefault("collected", [])
        lang      = get_lang(uid)
        if idx < len(fields) and fields[idx]["field_type"] == "photo":
            photo          = update.message.photo[-1]
            file_id        = photo.file_id
            file_unique_id = photo.file_unique_id
            collected.append({"type": "photo", "prompt_bn": fields[idx]["prompt_bn"], "prompt_en": fields[idx].get("prompt_en",""), "value": file_id, "unique_id": file_unique_id})
            ctx.user_data["field_idx"] = idx + 1
            next_idx = idx + 1
            if next_idx < len(fields):
                nf   = fields[next_idx]
                icon = "📷" if nf["field_type"] == "photo" else "📝"
                prompt = nf.get("prompt_en") if lang == 'en' else nf["prompt_bn"]
                await update.message.reply_text(f"{icon} *{prompt}*", parse_mode="Markdown")
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

    # ── Notification confirm / edit / skip ──────────────────────
    if d == "adm_notif_send":
        if not is_admin(uid): return
        await q.answer()
        notif_msg   = ctx.user_data.get("pending_notif_msg", "")
        notify_ch   = ctx.user_data.get("pending_notif_ch", "")
        ch_link     = ctx.user_data.get("pending_notif_ch_link", "")
        bot_link    = ctx.user_data.get("pending_notif_bot_link", "")
        if not notif_msg:
            await q.message.reply_text("❌ *কোনো pending notification নেই।*", parse_mode="Markdown"); return
        user_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Task করুন", url=bot_link)],
            [InlineKeyboardButton("📰 News Channel", url=NEWS_CH_LINK)],
        ])
        ch_kb   = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Task করুন", url=bot_link)],
            [InlineKeyboardButton("📰 News Channel", url=NEWS_CH_LINK)],
        ])
        users = all_users(); sent = fail = 0
        for u in users:
            if u["user_id"] == ADMIN_ID: continue
            try:
                await ctx.bot.send_message(u["user_id"], notif_msg, parse_mode="Markdown", reply_markup=user_kb)
                sent += 1; await asyncio.sleep(0.05)
            except: fail += 1
        try: await ctx.bot.send_message(notify_ch, notif_msg, parse_mode="Markdown", reply_markup=ch_kb)
        except Exception as e: log.warning(f"notif ch fail: {e}")
        await q.message.reply_text(f"📢 *Notification sent!* ✅ `{sent}` | ❌ `{fail}`", parse_mode="Markdown")
        for k in ("pending_notif_msg","pending_notif_ch","pending_notif_ch_link","pending_notif_bot_link"):
            ctx.user_data.pop(k, None)
        return

    if d == "adm_notif_edit":
        if not is_admin(uid): return
        await q.answer()
        ctx.user_data["state"] = "adm_notif_edit_txt"
        await q.message.reply_text(
            "✏️ *নতুন Notification Message লিখুন:*\n\n"
            "_(লেখা শেষে আবার preview দেখাবে)_\n\n"
            "/cancel — বাতিল",
            parse_mode="Markdown")
        return

    if d == "adm_notif_skip":
        if not is_admin(uid): return
        await q.answer("Notification skip করা হয়েছে।")
        for k in ("pending_notif_msg","pending_notif_ch","pending_notif_ch_link","pending_notif_bot_link"):
            ctx.user_data.pop(k, None)
        await q.message.edit_text("❌ *Notification skip করা হয়েছে।*", parse_mode="Markdown")
        return

    if d.startswith("adm_task_done_"):
        if not is_admin(uid): return
        await q.answer()
        tid    = int(d.split("_")[3])
        task   = get_task(tid)
        fields = get_task_fields(tid)
        max_c2  = _rget(task, 'max_completions', 0)
        max_txt = f"{max_c2} people" if max_c2 > 0 else "Unlimited"
        pu_limit = _rget(task, 'per_user_limit', 1) or 1
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
        if not is_admin(uid): return
        await q.answer()
        parts = d.split("_")
        # format: adm_tp_<action>_<tid>_<type>
        action  = parts[2]          # n / p / m / ok / del
        tid     = int(parts[3])
        tp_type = parts[4]          # t=regular, g=tg

        async def _refresh_tp_preview(msg):
            if tp_type == "t":
                task = get_task(tid); fields = get_task_fields(tid)
                mc   = _rget(task, 'max_completions', 0)
                pu   = _rget(task, 'per_user_limit', 1) or 1
                _ac  = _rget(task, 'allowed_countries', '') or ''
                txt  = (f"📋 *Task Preview — চেক করুন:*\n\n"
                        f"📌 *Name:* `{task['name']}`\n"
                        f"💵 *Reward:* `{task['price']}$ USDT`\n"
                        f"📋 *Fields:* {len(fields)}\n"
                        f"👥 *Max:* `{'Unlimited' if mc==0 else str(mc)+' people'}`\n"
                        f"👤 *Per User:* `{pu}` বার\n"
                        f"🌍 *দেশ:* {fmt_allowed_countries(_ac)}\n\n"
                        f"✏️ Edit করুন বা ✅ Confirm করুন।")
            else:
                task = get_tg_task(tid)
                mc   = _rget(task, 'max_completions', 0)
                njo  = _rget(task, 'new_joiners_only', 0)
                pu   = _rget(task, 'per_user_limit', 1) or 1
                _ac  = _rget(task, 'allowed_countries', '') or ''
                njo_lbl = "🔒 New Joiners Only: ✅ ON" if njo else "🔓 New Joiners Only: ❌ OFF"
                txt  = (f"📋 *TG Task Preview — চেক করুন:*\n\n"
                        f"📌 *Name:* `{task['name']}`\n"
                        f"💵 *Reward:* `{task['price']}$ USDT`\n"
                        f"📢 *Channel:* `{task['tg_target']}`\n"
                        f"👥 *Max:* `{'Unlimited' if mc==0 else str(mc)+' people'}`\n"
                        f"👤 *Per User:* `{pu}` বার\n"
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
                "🌍 *কোন দেশের ইউজাররা এই Task করতে পারবে%s*\n\n"
                "দেশের কোড কমা দিয়ে লিখুন:\n"
                f"`{_ch}`\n\n"
                "উদাহরণ: `BD,IN,PK`\n"
                "🌐 সব দেশের জন্য: `0` লিখুন\n\n/cancel — বাতিল",
                parse_mode="Markdown")
        elif action == "njo":
            task = get_tg_task(tid)
            if task:
                cur = _rget(task, 'new_joiners_only', 0)
                new_val = 0 if cur else 1
                update_tg_task(tid, "new_joiners_only", new_val)
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
                task = get_task(tid)
                await _activate_task_and_notify(ctx, q, uid,
                    get_fn=get_task, update_fn=update_task_field_val, tid=tid,
                    link_prefix="task_")
            elif tp_type == "g":
                task = get_tg_task(tid)
                await _activate_task_and_notify(ctx, q, uid,
                    get_fn=get_tg_task, update_fn=update_tg_task, tid=tid,
                    link_prefix="tgtask_", njo_field="new_joiners_only")
            else:
                # future task types: add get_fn/update_fn here
                pass
        elif action == "del":
            if tp_type == "t": delete_task(tid)
            else: delete_tg_task(tid)
            await q.message.reply_text("🗑 *Task delete করা হয়েছে।*", parse_mode="Markdown", reply_markup=main_kb(uid))
        return

# ──────────────────── INACTIVE USER REPORT ───────────────────
async def cmd_inactive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    lang = get_lang(uid)
    args = ctx.args
    try:
        days = int(args[0]) if args else 7
    except (ValueError, IndexError):
        days = 7
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    with _c() as c:
        rows = c.execute(
            "SELECT user_id, full_name, username, last_seen FROM users "
            "WHERE last_seen < %s AND last_seen IS NOT NULL AND last_seen != '' "
            "ORDER BY last_seen ASC LIMIT 100",
            (cutoff,)
        ).fetchall()
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

async def cmd_promocode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User command — bot asks for promo code input"""
    uid  = update.effective_user.id
    lang = get_lang(uid)
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
    lang = get_lang(uid)
    args = ctx.args
    if not args:
        msg = "💡 *ব্যবহার:* `/promo CODE`" if lang == 'bn' else "💡 *Usage:* `/promo CODE`"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    code = args[0].strip().upper()
    # Code খোঁজো
    with _c() as c:
        promo = c.execute("SELECT * FROM promocodes WHERE code=%s", (code,)).fetchone()
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
    with _c() as c:
        already = c.execute(
            "SELECT id FROM promo_uses WHERE code_id=%s AND user_id=%s",
            (promo["id"], uid)
        ).fetchone()
    if already:
        msg = "⚠️ *আপনি এই Promo Code আগেই ব্যবহার করেছেন।*" if lang == 'bn' else "⚠️ *You have already used this promo code.*"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    # Requirement চেক (min_tasks)
    min_tasks = promo["min_tasks"] or 0
    if min_tasks > 0:
        with _c() as c:
            done_count = _fval(c.execute(
                "SELECT COUNT(*) FROM gmails WHERE user_id=%s AND status='approved'",
                (uid,)
            ).fetchone())
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
    # ✅ Redeem করো
    amount = promo["amount"]
    add_bal(uid, amount)
    with _c() as c:
        c.execute(
            "INSERT INTO promo_uses (code_id, user_id, used_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (promo["id"], uid, now())
        )
        c.execute(
            "UPDATE promocodes SET used_count=used_count+1 WHERE id=%s",
            (promo["id"],)
        )
        new_used = _fval(c.execute("SELECT used_count FROM promocodes WHERE id=%s", (promo["id"],)).fetchone())
    if lang == 'bn':
        ok_msg = (
            f"✅ *Promo Code রিডিম সফল!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` যোগ হয়েছে।\n"
            f"💼 *বর্তমান Balance:* `{get_bal(uid)}$ USDT`"
        )
    else:
        ok_msg = (
            f"✅ *Promo Code redeemed successfully!*\n\n"
            f"🔑 *Code:* `{code}`\n"
            f"💰 *Reward:* `{amount}$ USDT` added.\n"
            f"💼 *Current Balance:* `{get_bal(uid)}$ USDT`"
        )
    await update.message.reply_text(ok_msg, parse_mode="Markdown")

    # ── Admin notification: কে নিল, কতজন নিল, কতটা বাকি ──────
    remaining = max(0, promo["max_uses"] - new_used)
    uname     = update.effective_user.username or ""
    uname_txt = f"@{uname}" if uname else f"ID:`{uid}`"
    full_name = (update.effective_user.full_name or "").strip() or "—"
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
    asyncio.create_task(notify_full_admins(ctx.bot, admin_notif))

    # Fully used হলে news channel post edit করো
    if new_used >= promo["max_uses"]:
        nc_msg_id  = promo["news_ch_msg_id"]  or 0
        nc_chat_id = promo["news_ch_chat_id"] or 0
        if nc_msg_id and nc_chat_id:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=nc_chat_id,
                    message_id=nc_msg_id,
                    text=(
                        f"🔑 *Code:* `{code}`\n\n"
                        f"❌ *This promo code has been fully used.*\n"
                        f"👥 *Total Used:* `{new_used}/{promo['max_uses']}`"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as _e:
                log.warning(f"promo news edit fail: {_e}")

# ── Promo code callback handlers ──────────────────────────────
async def _handle_promo_callbacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    d   = q.data
    if not is_admin(uid): return

    if d == "adm_promo_req_anyone":
        await q.answer()
        ctx.user_data["promo_min_tasks"] = 0
        ctx.user_data["promo_req_text"]  = "Anyone can use ✅"
        ctx.user_data["state"]           = "adm_promo_preview"
        await _show_promo_preview(q.message, ctx)

    elif d == "adm_promo_req_tasks":
        await q.answer()
        ctx.user_data["state"] = "adm_promo_min_tasks"
        await q.message.reply_text(
            "📋 *কতটি Task Approved থাকলে Code নিতে পারবে%s*\n\n"
            "_(সংখ্যা লিখুন, যেমন: 2)_\n\n/cancel — বাতিল",
            parse_mode="Markdown")

    elif d == "adm_promo_post_news":
        await q.answer()
        code      = ctx.user_data.get("promo_code", "")
        amount    = ctx.user_data.get("promo_amount", 0)
        max_uses  = ctx.user_data.get("promo_max_uses", 0)
        req_text  = ctx.user_data.get("promo_req_text", "")
        min_tasks = ctx.user_data.get("promo_min_tasks", 0)
        if not code:
            await q.message.reply_text("❌ Promo data পাওয়া যায়নি।", parse_mode="Markdown"); return
        # DB-তে save
        with _c() as c:
            c.execute(
                "INSERT INTO promocodes (code,amount,max_uses,requirement,min_tasks,created_by,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (code, amount, max_uses, req_text, min_tasks, uid, now())
            )
            promo_id = _fval(c.execute("SELECT id FROM promocodes WHERE code=%s", (code,)).fetchone())
        # News channel এ post করো
        post_txt = _build_promo_post(code, amount, max_uses, req_text)
        try:
            nc_msg = await ctx.bot.send_message(LEADERBOARD_CH_ID, post_txt, parse_mode="Markdown")
            with _c() as c:
                c.execute(
                    "UPDATE promocodes SET news_ch_msg_id=%s, news_ch_chat_id=%s WHERE id=%s",
                    (nc_msg.message_id, nc_msg.chat_id, promo_id)
                )
            await q.message.reply_text(
                f"✅ *Promo Code তৈরি ও News Channel এ Post হয়েছে!*\n\n"
                f"🔑 *Code:* `{code}`\n"
                f"🔗 {LEADERBOARD_CH_LINK}",
                parse_mode="Markdown")
        except Exception as _e:
            await q.message.reply_text(f"✅ *Promo Code তৈরি হয়েছে।* News Channel Post ব্যর্থ: `{_e}`", parse_mode="Markdown")
        _clear_promo_data(ctx)

    elif d == "adm_promo_save_only":
        await q.answer()
        code      = ctx.user_data.get("promo_code", "")
        amount    = ctx.user_data.get("promo_amount", 0)
        max_uses  = ctx.user_data.get("promo_max_uses", 0)
        req_text  = ctx.user_data.get("promo_req_text", "")
        min_tasks = ctx.user_data.get("promo_min_tasks", 0)
        if not code:
            await q.message.reply_text("❌ Promo data পাওয়া যায়নি।", parse_mode="Markdown"); return
        with _c() as c:
            c.execute(
                "INSERT INTO promocodes (code,amount,max_uses,requirement,min_tasks,created_by,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (code, amount, max_uses, req_text, min_tasks, uid, now())
            )
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
    """PostgreSQL mode — file backup দরকার নেই। Neon এ data স্থায়ী।"""
    return


def _sync_restore_from_telegram():
    """PostgreSQL mode — Neon এ সব data স্থায়ী। File restore দরকার নেই।"""
    return False

async def _run_health_check_and_fix(bot, skip_admin_ping=False) -> tuple:
    """
    সব কিছু চেক করে, সমস্যা পেলে নিজেই ঠিক করে এবং রিপোর্ট দেয়।
    ✅ = ঠিক আছে | ❌ = সমস্যা পেয়েছে | 🔧 = ঠিক করা হয়েছে
    Returns: (report_str, problems_count, fixes_count)
    """
    lines = [f"🔍 *Bot Health Check Report*", f"🕐 `{now()}`", ""]
    fixes = 0
    problems = 0

    # ── 1. Database ──────────────────────────────────────────────
    lines.append("*📦 Database (PostgreSQL — Neon)*")
    try:
        with _c() as c:
            u = _count(c.execute("SELECT COUNT(*) FROM users").fetchone())
        lines.append(f"  ✅ Neon DB সুস্থ আছে ({u} জন user)")
        lines.append("  🔒 Data permanently stored — restart এ কিছু যায় না")
    except Exception as e:
        problems += 1
        lines.append(f"  ❌ PostgreSQL সংযোগ ত্রুটি: {e}")
        try:
            init_db(); _auto_configure()
            lines.append("  🔧 DB re-initialized করা হয়েছে"); fixes += 1
        except: pass

    # ── 2. Settings ──────────────────────────────────────────────
    lines.append("\n*⚙️ Settings*")
    critical_keys = ["gmail_ch_id", "withdraw_ch_id", "bot_status"]
    missing_keys = []
    try:
        with _c() as c:
            for key in critical_keys:
                val = c.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
                if not val:
                    missing_keys.append(key)
    except:
        missing_keys = critical_keys
    if missing_keys:
        problems += 1
        lines.append(f"  ❌ Missing settings: {', '.join(missing_keys)}")
        _auto_configure()
        lines.append("  🔧 _auto_configure() চালানো হয়েছে — settings restore হয়েছে"); fixes += 1
    else:
        lines.append("  ✅ সব critical settings ঠিক আছে")

    # ── 3. Backup file_id ────────────────────────────────────────
    lines.append("\n*💾 Backup File ID*")
    fid = _load_file_id()
    if not fid:
        problems += 1
        lines.append("  ❌ last_backup_file_id.txt নেই")
        try:
            await _send_db_to_admin(bot, label="🔧 Health-Fix Backup")
            lines.append("  🔧 নতুন backup পাঠানো হয়েছে এবং file_id সেভ হয়েছে"); fixes += 1
        except Exception as e:
            lines.append(f"  ⚠️ backup পাঠাতে ব্যর্থ: {e}")
    else:
        lines.append(f"  ✅ file_id আছে (`{fid[:18]}...`)")

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
            old_mid = _hc_load_msg_id(ch)
            if old_mid:
                try:
                    await bot.delete_message(ch, old_mid)
                except Exception:
                    pass
            # নতুন HC message পাঠাও
            hc_m = await bot.send_message(ch,
                f"✅ *Health Check — Channel OK*\n🕐 `{now()}`", parse_mode="Markdown")
            _hc_save_msg_id(ch, hc_m.message_id)
            lines.append(f"  ✅ `{ch}` — সংযুক্ত")
        except Exception as e:
            problems += 1
            lines.append(f"  ❌ `{ch}` — সমস্যা: {e}")

    # ── 6. Required join channels ────────────────────────────────
    lines.append("\n*📢 Required Channels*")
    try:
        with _c() as c:
            chs = c.execute("SELECT ch_id, ch_name FROM required_channels").fetchall()
        if not chs:
            problems += 1
            lines.append("  ❌ কোনো required channel নেই")
            _auto_configure()
            lines.append("  🔧 default channels restore হয়েছে"); fixes += 1
        else:
            for ch in chs:
                lines.append(f"  ✅ {ch['ch_name']} (`{ch['ch_id']}`)")
    except Exception as e:
        lines.append(f"  ⚠️ চেক করতে পারেনি: {e}")

    # ── 7. PostgreSQL storage info ────────────────────────────────
    lines.append("\n*🗂 Storage*")
    try:
        with _c() as c:
            g = _count(c.execute("SELECT COUNT(*) FROM gmails").fetchone())
            w = _count(c.execute("SELECT COUNT(*) FROM withdrawals").fetchone())
            pn = _count(c.execute("SELECT COUNT(*) FROM pending_notifications").fetchone())
        lines.append(f"  ✅ Neon PostgreSQL — gmails:{g} | withdrawals:{w} | notifications:{pn}")
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
    """প্রতি ৩০ মিনিটে stuck user state পরিষ্কার করে"""
    try:
        app = context.application
        stale = []
        for uid, data in list(app.user_data.items()):
            if data.get("state"):
                stale.append(uid)
                data.pop("state", None)
                data.pop("edit_task_id", None)
                data.pop("tg_task_draft", None)
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
                       "ProxyError", "503", "502", "ConnectionError", "SSLError"]
    _check_str = err_str + err_type
    if any(k in _check_str for k in silent_keywords):
        log.warning(f"[SILENT-ERR] {err_type}: {err_str[:120]}")
        return

    # ── 3. auto-fix চালাও ────────────────────────────────────────
    fix_result = "🔧 auto-fix চালানো হয়নি"
    if fix_key == "_fix_db":
        try:
            init_db()
            _auto_configure()
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
                ctx_parts.append(f"👤 User: `{u.id}` (@{u.username or 'নেই'})")
            if update.effective_message:
                txt = (update.effective_message.text or "")[:60]
                ctx_parts.append(f"💬 Message: `{txt}`")
            if update.callback_query:
                ctx_parts.append(f"🔘 Callback: `{update.callback_query.data}`")
    except Exception:
        pass

    # ── 5. admin কে রিপোর্ট পাঠাও ────────────────────────────────
    log.exception(f"[SMART-ERROR] {err_type}: {err_str[:200]}")
    report_lines = [
        "🚨 *Bot Error Detected & Auto-Fixed*",
        "",
        f"❌ *Error:* `{err_type}`",
        f"📋 *কারণ:* {reason}",
        f"🕐 *সময়:* `{now()}`",
    ]
    if ctx_parts:
        report_lines.append("")
        report_lines.extend(ctx_parts)
    report_lines += [
        "",
        f"{fix_result}",
        "",
        f"```\n{err_str[:300]}\n```",
    ]
    _network_errs = ("ProxyError", "NetworkError", "TimedOut", "ConnectionError", "503", "502")
    for _attempt in range(2):
        try:
            await context.bot.send_message(
                ADMIN_ID, "\n".join(report_lines), parse_mode="Markdown"
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

BD_TZ              = pytz.timezone("Asia/Dhaka")
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
    lang = get_lang(uid)
    text = _lb_build_text(lang)

    rank_label = "🔍 Find my place" if lang == "en" else "🔍 আমি কোথায় আছি%s"
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
    সব task type গণনা হয়: Gmail, TG, Regular, Category।"""
    since = (datetime.now(BD_TZ) - timedelta(hours=24)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn  = psycopg2.connect(DATABASE_URL)
    cur   = conn.cursor()
    cur.execute("""
        SELECT user_id, COUNT(*) AS cnt
        FROM (
            SELECT user_id, approved_at AS ts FROM gmails
            WHERE status = 'approved' AND approved_at >= %s
            UNION ALL
            SELECT user_id, done_at AS ts FROM tg_task_completions
            WHERE done_at >= %s
            UNION ALL
            SELECT ctc.user_id, ctc.done_at AS ts FROM category_task_completions ctc
            JOIN category_tasks ct ON ctc.task_id = ct.id
            WHERE ctc.done_at >= %s AND ct.is_active = 1
        ) combined
        GROUP BY user_id
        ORDER BY cnt DESC
    """, (since, since, since))
    all_rows = cur.fetchall()
    conn.close()
    for i, row in enumerate(all_rows):
        if row[0] == uid:
            return (i + 1, row[1])
    return (None, None)


def _lb_get_rows(yesterday: bool = False) -> list:
    """Top 10 users — সব task type মিলিয়ে: Gmail, TG, Regular, Category।
    yesterday=False → আজকের রাত ১২:০০ AM BD থেকে এখন পর্যন্ত।
    yesterday=True  → গতকাল রাত ১২:০০ AM থেকে আজ রাত ১২:০০ AM পর্যন্ত।"""
    today_midnight_bd = datetime.now(BD_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if yesterday:
        since_dt = today_midnight_bd - timedelta(days=1)
        until_dt = today_midnight_bd
        since = since_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        until = until_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    else:
        since = today_midnight_bd.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        until = None
    conn  = psycopg2.connect(DATABASE_URL)
    cur   = conn.cursor()
    if until:
        cur.execute("""
            SELECT u.user_id, u.full_name, u.username, COUNT(*) AS cnt
            FROM (
                SELECT user_id, approved_at AS ts FROM gmails
                WHERE status = 'approved' AND approved_at >= %s AND approved_at < %s
                UNION ALL
                SELECT user_id, done_at AS ts FROM tg_task_completions
                WHERE done_at >= %s AND done_at < %s
                UNION ALL
                SELECT ctc.user_id, ctc.done_at AS ts FROM category_task_completions ctc
                JOIN category_tasks ct ON ctc.task_id = ct.id
                WHERE ctc.done_at >= %s AND ctc.done_at < %s AND ct.is_active = 1
            ) combined
            JOIN users u ON combined.user_id = u.user_id
            GROUP BY combined.user_id
            ORDER BY cnt DESC
            LIMIT 10
        """, (since, until, since, until, since, until))
    else:
        cur.execute("""
            SELECT u.user_id, u.full_name, u.username, COUNT(*) AS cnt
            FROM (
                SELECT user_id, approved_at AS ts FROM gmails
                WHERE status = 'approved' AND approved_at >= %s
                UNION ALL
                SELECT user_id, done_at AS ts FROM tg_task_completions
                WHERE done_at >= %s
                UNION ALL
                SELECT ctc.user_id, ctc.done_at AS ts FROM category_task_completions ctc
                JOIN category_tasks ct ON ctc.task_id = ct.id
                WHERE ctc.done_at >= %s AND ct.is_active = 1
            ) combined
            JOIN users u ON combined.user_id = u.user_id
            GROUP BY combined.user_id
            ORDER BY cnt DESC
            LIMIT 10
        """, (since, since, since))
    rows = cur.fetchall()
    conn.close()
    return rows


def _lb_mask_name(full_name: str, username: str) -> str:
    """নাম পুরো দেখাও, @ দিয়ে শুরু word হাইড করো। HTML escape করো।"""
    import re, html as _html
    name = (full_name or "User").strip()
    name = re.sub(r'@\S+', '@\u2060***', name)
    name = _html.escape(name or "User")
    return name


def _lb_build_text_admin() -> str:
    """Admin DM — HTML, নাম + @username + User ID সহ।"""
    import html as _html
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
            name  = _html.escape((full_name or "User").strip())
            uname = f"@{_html.escape(username)}" if username else "—"
            lines.append(f"{medals[i]} {name} | {uname} | <code>{user_id}</code> — {cnt}টি Task")
    lines.append("\n⏰ প্রতিদিন রাত ১২:০৫ BD তে আপডেট হয়")
    return "\n".join(lines)


def _lb_build_text(lang: str = "bn", yesterday: bool = False) -> str:
    """Channel/User leaderboard — HTML, শুধু নাম (@ হাইড)।
    yesterday=True → গতকালের দিনের ফলাফল (১২:০৫ AM channel post এর জন্য)।"""
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
        lines.append("\n⏰ Results calculated every day at 12:05 AM Bangladesh Time")
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
        lines.append("\n⏰ প্রতিদিন বাংলাদেশ সময় রাত ১২টা ৫ মিনিটে স্বয়ংক্রিয়ভাবে দিনের ফলাফল গণনা করা হয়")
        lines.append("\nপ্রতিদিন অংশ নিন, লিডারবোর্ডে উঠুন 🚀")

    return "\n".join(lines)


def _lb_channel_btn(lang: str = "bn") -> InlineKeyboardMarkup:
    label = "📢 News Channel" if lang == "en" else "📢 নিউজ চ্যানেল"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, url=LEADERBOARD_CH_LINK)
    ]])


async def _lb_refresh_now(bot):
    """যেকোনো জায়গা থেকে সাথে সাথে Admin leaderboard refresh করে।"""
    chat_id, msg_id = _lb_adm_load()
    if not chat_id or not msg_id:
        return
    text = _lb_build_text_admin()
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


async def job_post_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """প্রতিদিন রাত ১২:০৫ (BD) — শুধু Channel এ পাঠায় (bot DM নয়)"""
    bot_username = (await context.bot.get_me()).username
    bot_link     = f"https://t.me/{bot_username}"

    ch_id = LEADERBOARD_CH_ID
    if not ch_id:
        return

    # channel এর জন্য public text — গতকালের পুরো দিনের ফলাফল
    ch_text = _lb_build_text("bn", yesterday=True)

    # পুরনো post ডিলিট করো — saved chat_id ব্যবহার করো
    old_chat_id, old_msg_id = _lb_load_msg_id()
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
        _lb_save_msg_id(ch_msg.chat_id, ch_msg.message_id)
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
    try:
        import io as _io
        report = _build_full_users_report()
        report_bytes = report.encode("utf-8")
        filename = f"users_{now()[:10]}.txt"
        caption = (
            f"📊 *User Data Auto Backup*\n\n"
            f"🕐 *সময়:* `{now()}`\n"
            f"👥 *মোট Users:* `{len(all_users())}`\n\n"
            f"✅ নতুন data আসায় auto-save হয়েছে।"
        )
        for target in BACKUP_CHANNELS:
            # পুরনো report message ডিলিট করো
            old_rid = _report_msg_load(target)
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
                    _report_msg_save(target, msg.message_id)
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
        # শেষ 'keep' টা রেখে বাকি delete করো
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


def auto_cleanup_old_records():
    """
    পুরনো processed record পরিষ্কার — storage ৫০০ MB এর মধ্যে রাখে।

    ✅ কখনো delete হয় না:
       - users (সব user, balance, referred_by, points)
       - pending gmails / pending withdrawals
       - admins, settings, tasks, tg_tasks, categories
       - promocodes, promo_uses, required_channels

    🗑  Delete হয়:
       - gmails approved/rejected > 30 দিন পুরনো
         (প্রতি user এর শেষ 50টা সর্বদা রক্ষিত)
       - withdrawals approved/rejected > 30 দিন পুরনো
         (প্রতি user এর শেষ 20টা সর্বদা রক্ষিত)
       - pending_notifications > 7 দিন পুরনো
       - gmails pending > 90 দিন পুরনো (ভুলে যাওয়া submission)
    """
    import datetime as _dt
    now = _dt.datetime.utcnow()
    cutoff_30 = (now - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
    cutoff_90 = (now - _dt.timedelta(days=90)).strftime("%Y-%m-%d")
    cutoff_7  = (now - _dt.timedelta(days=7)).strftime("%Y-%m-%d")

    total_deleted = 0
    try:
        with _c() as c:
            # ── gmails: approved/rejected > 30 দিন ─────────────────
            r = c.execute(
                "DELETE FROM gmails WHERE status IN ('approved','rejected') "
                "AND submitted_at < %s",
                (cutoff_30,)
            )
            n1 = r.rowcount or 0
            total_deleted += n1

            # ── প্রতি user এর জন্য শেষ 50টা রাখো ──────────────────
            users = [row['user_id'] for row in
                     c.execute("SELECT DISTINCT user_id FROM gmails WHERE status IN ('approved','rejected')").fetchall()]
            for uid in users:
                _trim_user_records(c, "gmails", uid, keep=50)

            # ── withdrawals: approved/rejected > 30 দিন ────────────
            r2 = c.execute(
                "DELETE FROM withdrawals WHERE status IN ('approved','rejected') "
                "AND requested_at < %s",
                (cutoff_30,)
            )
            n2 = r2.rowcount or 0
            total_deleted += n2

            # ── প্রতি user এর শেষ 20টা withdrawal রাখো ────────────
            w_users = [row['user_id'] for row in
                       c.execute("SELECT DISTINCT user_id FROM withdrawals WHERE status IN ('approved','rejected')").fetchall()]
            for uid in w_users:
                _trim_user_records(c, "withdrawals", uid, keep=20)

            # ── pending_notifications > 7 দিন ──────────────────────
            r3 = c.execute(
                "DELETE FROM pending_notifications WHERE send_at < %s",
                (cutoff_7,)
            )
            n3 = r3.rowcount or 0
            total_deleted += n3

            # ── gmails pending > 90 দিন (ভুলে যাওয়া) ───────────────
            r4 = c.execute(
                "DELETE FROM gmails WHERE status='pending' AND submitted_at < %s",
                (cutoff_90,)
            )
            n4 = r4.rowcount or 0
            total_deleted += n4

        log.info(
            f"[AUTO-CLEANUP] সম্পন্ন — gmails={n1}, withdrawals={n2}, "
            f"notifications={n3}, old_pending_gmails={n4} | মোট={total_deleted} row"
        )
    except Exception as e:
        log.error(f"[AUTO-CLEANUP] Error: {e}")


async def job_auto_cleanup(context):
    """APScheduler daily job — রাত ৩টায় চলে (Bangladesh time = UTC+6)।"""
    log.info("[AUTO-CLEANUP] Daily cleanup job শুরু হচ্ছে...")
    auto_cleanup_old_records()


def _auto_configure():
    """
    যেকোনো জায়গায় upload করলেও সব settings অটো সেট হয়।
    Code এর constants থেকে DB তে সব critical config লিখে দেয়।
    config_backup.json এ backup রাখে — DB মুছলেও restore হবে।
    """
    import json as _json_mod

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
            _json_mod.dump(backup_data, f, ensure_ascii=False, indent=2)
        log.info("✅ config_backup.json আপডেট হয়েছে।")
    except Exception as e:
        log.warning(f"[AUTO-CONFIG] config_backup.json লিখতে ব্যর্থ: {e}")

    # ── Step 3: DB ফাঁকা হলে JSON থেকে restore করো ─────────────────
    try:
        if os.path.exists(CONFIG_BACKUP_FILE):
            with open(CONFIG_BACKUP_FILE, "r", encoding="utf-8") as f:
                saved = _json_mod.load(f)
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

    # ── Step 4: শুধু system constants সব সময় code থেকে নেওয়া হবে ────
    # admin_id, backup_channels — এগুলো code-only, পরিবর্তনযোগ্য নয়
    # gmail_ch_id, withdraw_ch_id, bsc_wallet_addr — admin panel থেকে পরিবর্তনযোগ্য,
    # তাই force-overwrite করা হবে না — শুধু প্রথমবার seed হবে
    try:
        with _c() as c:
            # সব সময় code থেকে নেওয়া হবে (admin পরিবর্তন করতে পারবে না)
            always_from_code = {
                "admin_id":              str(ADMIN_ID),
                "backup_channels":       ",".join(str(x) for x in BACKUP_CHANNELS),
                "backup_interval_hours": str(BACKUP_INTERVAL_HOURS),
            }
            for key, val in always_from_code.items():
                c.execute(
                    "INSERT INTO settings (key,value) VALUES (%s,%s) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, val)
                )
            # admin panel থেকে পরিবর্তনযোগ্য — শুধু missing হলে seed করো
            admin_configurable = {
                "gmail_ch_id":    str(GMAIL_CH_ID),
                "withdraw_ch_id": str(WITHDRAW_CH_ID),
                "bsc_wallet_addr": BSC_WALLET_ADDR,
            }
            for key, val in admin_configurable.items():
                c.execute("INSERT INTO settings (key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING", (key, val))
        log.info("✅ Code constants DB তে enforce হয়েছে।")
    except Exception as e:
        log.warning(f"[AUTO-CONFIG] code-enforce ব্যর্থ: {e}")

    log.info("✅ Auto-configure সম্পন্ন।")


# ──────────────────────────── BEP-20 AUTO WITHDRAW ────────────────────────────

def _get_w3():
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

def swap_usdt_to_bnb(usdt_amount: float) -> tuple:
    """PancakeSwap দিয়ে USDT → BNB swap করো (gas কেনার জন্য)।
    সফল হলে (True, bnb_received), ব্যর্থ হলে (False, error_str)।"""
    try:
        w3 = _get_w3()
        if not w3 or not w3.is_connected():
            return False, "BSC সংযোগ ব্যর্থ"
        account  = w3.eth.account.from_key(BSC_PRIVATE_KEY)
        usdt_c   = w3.eth.contract(address=Web3.to_checksum_address(USDT_BEP20_CA), abi=USDT_BEP20_ABI)
        router_c = w3.eth.contract(address=Web3.to_checksum_address(PANCAKE_ROUTER), abi=PANCAKE_ROUTER_ABI)
        decimals = usdt_c.functions.decimals().call()
        amt_in   = int(usdt_amount * (10 ** decimals))

        # ── কত BNB পাবে estimate করো ──────────────────────────────
        path        = [Web3.to_checksum_address(USDT_BEP20_CA), Web3.to_checksum_address(WBNB_ADDR)]
        amounts_out = router_c.functions.getAmountsOut(amt_in, path).call()
        bnb_min     = int(amounts_out[1] * 0.97)   # 3% slippage

        # ── Step 1: Approve PancakeSwap ───────────────────────────
        nonce    = w3.eth.get_transaction_count(account.address, 'pending')
        gas_price = w3.eth.gas_price
        approve_tx = usdt_c.functions.approve(
            Web3.to_checksum_address(PANCAKE_ROUTER), amt_in
        ).build_transaction({"from": account.address, "nonce": nonce, "gasPrice": gas_price, "chainId": 56})
        approve_tx["gas"] = int(w3.eth.estimate_gas(approve_tx) * 1.2)
        signed = w3.eth.account.sign_transaction(approve_tx, BSC_PRIVATE_KEY)
        w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(signed.raw_transaction, timeout=60)

        # ── Step 2: Swap USDT → BNB ───────────────────────────────
        nonce    += 1
        deadline  = w3.eth.get_block('latest')['timestamp'] + 300
        swap_tx   = router_c.functions.swapExactTokensForETH(
            amt_in, bnb_min, path, account.address, deadline
        ).build_transaction({"from": account.address, "nonce": nonce, "gasPrice": gas_price, "chainId": 56})
        swap_tx["gas"] = int(w3.eth.estimate_gas(swap_tx) * 1.2)
        signed2  = w3.eth.account.sign_transaction(swap_tx, BSC_PRIVATE_KEY)
        tx_hash  = w3.eth.send_raw_transaction(signed2.raw_transaction)
        receipt  = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] == 1:
            bnb_got = amounts_out[1] / (10 ** 18)
            log.info(f"[SWAP] ✅ {usdt_amount} USDT → {bnb_got:.6f} BNB")
            return True, bnb_got
        return False, "Swap transaction reverted"
    except Exception as e:
        log.error(f"[SWAP] ❌ {e}")
        return False, str(e)

def ensure_bnb_for_gas() -> str:
    """BNB কম থাকলে USDT থেকে অটো-কিনে নেয়।
    'ok' = BNB যথেষ্ট, 'swapped' = swap করা হয়েছে, 'failed' = swap ব্যর্থ।"""
    bnb = get_bsc_bnb_balance()
    if bnb is None:
        return "ok"   # চেক করা যায়নি — চেষ্টা করতে দাও
    if bnb >= BNB_REFILL_THRESHOLD:
        return "ok"   # যথেষ্ট BNB আছে
    # কত USDT দিয়ে BNB কিনতে হবে হিসাব করো
    bnb_price = get_bnb_price_usd()
    if bnb_price is None:
        return "ok"   # price জানা নেই, চেষ্টা করতে দাও
    usdt_needed = round(BNB_REFILL_TARGET * bnb_price * 1.05, 4)   # 5% buffer
    # সর্বোচ্চ $0.20 limit — এর বেশি লাগলে BNB কিনবে না
    if usdt_needed > BNB_MAX_BUY_USD:
        log.warning(f"[SWAP] BNB কিনতে {usdt_needed}$ লাগবে — সীমা ${BNB_MAX_BUY_USD} ছাড়িয়ে গেছে, কেনা হবে না।")
        return "failed"
    usdt_bal    = get_bsc_usdt_balance() or 0
    if usdt_bal < usdt_needed:
        log.warning(f"[SWAP] USDT কম ({usdt_bal:.4f}) — swap করা সম্ভব না।")
        return "failed"
    log.info(f"[SWAP] BNB কম ({bnb:.6f}) — {usdt_needed}$ USDT দিয়ে BNB কিনছি।")
    ok, result = swap_usdt_to_bnb(usdt_needed)
    return "swapped" if ok else "failed"

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
    সফল হলে (tx_hash_str, None, gas_usd), ব্যর্থ হলে (None, error_str, gas_usd) রিটার্ন করো।
    ⚠️ Wallet থেকে শুধু USDT কাটে — BNB শুধু gas fee হিসেবে যায়।"""
    gas_usd_paid = None
    try:
        w3 = _get_w3()
        if not w3 or not w3.is_connected():
            return None, "BSC RPC সংযোগ ব্যর্থ", None
        if not BSC_PRIVATE_KEY:
            return None, "BSC_PRIVATE_KEY সেট নেই", None
        if not get_bsc_wallet_addr():
            return None, "BSC_WALLET_ADDRESS সেট নেই", None
        # ── BNB কম থাকলে USDT থেকে অটো swap করো ─────────────────────
        swap_status = ensure_bnb_for_gas()
        if swap_status == "failed":
            bnb_bal = get_bsc_bnb_balance() or 0
            if bnb_bal < 0.00022:
                return None, f"BNB অপর্যাপ্ত ({bnb_bal:.5f}) এবং USDT→BNB swap ব্যর্থ।", None
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
        # ⚡ receipt-এর জন্য অপেক্ষা করো না — tx_hash সঙ্গে সঙ্গে return করো
        return tx_hash.hex(), None, gas_usd_paid, gas_bnb_actual
    except Exception as e:
        return None, str(e), gas_usd_paid, None

async def job_auto_withdraw(context):
    """প্রতি ২ মিনিটে চলে — pending withdrawals গুলো BEP-20 এ auto-process করে।"""
    if not _WEB3_OK:
        log.warning("[AUTO-WD] web3 library নেই — `pip install web3` চালান।")
        return
    if not BSC_PRIVATE_KEY or not get_bsc_wallet_addr():
        log.warning("[AUTO-WD] BSC_PRIVATE_KEY বা BSC_WALLET_ADDRESS সেট নেই — skip।")
        return

    # আগের cycle চলছে%s তাহলে এই cycle skip — queue pile-up এবং nonce conflict বন্ধ
    if _wd_job_lock.locked():
        log.info("[AUTO-WD] আগের cycle এখনো চলছে — এই cycle skip।")
        return

    async with _wd_job_lock:
        # ── Stuck processing recovery ─────────────────────────────────
        # যে records ১০+ মিনিট ধরে 'processing' এ আছে সেগুলো recover করো
        stuck_cutoff = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
        with _c() as c:
            stuck_rows = c.execute(
                "SELECT id, user_id, amount, tx_hash FROM withdrawals "
                "WHERE status='processing' AND locked_at != '' AND locked_at <= %s",
                (stuck_cutoff,)
            ).fetchall()
        for sr in stuck_rows:
            sid, suid, samt, stx = sr["id"], sr["user_id"], sr["amount"], sr["tx_hash"] or ""
            if stx.strip():
                # tx_hash আছে — TX পাঠানো হয়েছিল, শুধু approved mark বাকি ছিল
                with _c() as c:
                    c.execute("UPDATE withdrawals SET status='approved' WHERE id=%s AND status='processing'", (sid,))
                mark_db_dirty()
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
                except: pass
            else:
                # tx_hash নেই — TX পাঠানো হয়নি, নিরাপদে pending এ ফেরত দাও
                with _c() as c:
                    c.execute(
                        "UPDATE withdrawals SET status='pending', locked_at='' WHERE id=%s AND status='processing'",
                        (sid,)
                    )
                mark_db_dirty()
                log.warning(f"[AUTO-WD] #{sid}: stuck processing (no tx) → reset to pending।")

        _now_str    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _fallback2m = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")

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
        if not rows:
            return

        log.info(f"[AUTO-WD] {len(rows)} টি scheduled withdrawal প্রস্তুত — processing শুরু।")

        for row in rows:
            wid     = row["id"]
            uid     = row["user_id"]
            amt     = row["amount"]
            fee     = row["fee"] if row["fee"] else get_wd_fee()
            address = row["address"]
            net_amt = round(amt - fee, 4)

            # ── Gas fee estimate — >= $0.01 হলে থামো, admin কে জানাও ──────
            gas_bnb_est, gas_usd_est = estimate_gas_cost_usd()
            gas_usd_str = f"${gas_usd_est:.4f}" if gas_usd_est is not None else "অজানা"
            if gas_usd_est is not None and gas_usd_est >= GAS_ALERT_THRESHOLD_USD:
                log.warning(f"[AUTO-WD] #{wid}: Gas fee {gas_usd_str} >= ${GAS_ALERT_THRESHOLD_USD} — pausing, admin alert।")
                with _c() as c:
                    c.execute("UPDATE withdrawals SET status='gas_pending' WHERE id=%s", (wid,))
                mark_db_dirty()
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
            wallet_bal = get_bsc_usdt_balance()
            if wallet_bal is None:
                log.warning(f"[AUTO-WD] #{wid}: balance check failed — পরের চক্রে retry।")
                continue
            if wallet_bal < net_amt:
                err_detail = f"Bot wallet balance ({wallet_bal:.4f} USDT) < required ({net_amt:.4f} USDT)"
                log.warning(f"[AUTO-WD] #{wid}: {err_detail} — reject।")
                with _c() as c:
                    c.execute("UPDATE withdrawals SET status='rejected' WHERE id=%s", (wid,))
                add_bal(uid, amt)
                mark_db_dirty()
                _ulang = get_lang(uid)
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
                except: pass
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
                except: pass
                continue

            # ── Security Lock: payment পাঠানোর আগে atomically 'processing' করো ──
            with _c() as c:
                locked = c.execute(
                    "UPDATE withdrawals SET status='processing', locked_at=%s WHERE id=%s AND status='pending'",
                    (now(), wid)
                )
            if locked.rowcount == 0:
                log.warning(f"[AUTO-WD] #{wid}: ইতিমধ্যে processing বা cancel — skip।")
                continue

            # ── USDT পাঠাও ───────────────────────────────────────────────
            log.info(f"[AUTO-WD] #{wid}: Sending {net_amt} USDT → {address} | Est. gas: {gas_usd_str}")
            tx_hash, err, gas_usd_paid, gas_bnb_actual = send_usdt_bep20(address, net_amt)

            if tx_hash:
                # ── সফল ────────────────────────────────────────────────
                with _c() as c:
                    c.execute(
                        "UPDATE withdrawals SET status='approved', tx_hash=%s WHERE id=%s",
                        (tx_hash, wid)
                    )
                mark_db_dirty()
                bscscan_url  = f"https://bscscan.com/tx/{tx_hash}"
                # Gas fee সবসময় দেখাবে — BNB সবসময়, USD পাওয়া গেলে সাথে দেখাবে
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
                except: pass
                try:
                    masked = address[:6] + "..." + address[-4:]
                    _urow  = get_user(uid)
                    _uname = (_urow["full_name"] or _urow["username"] or str(uid)) if _urow else str(uid)
                    ch_msg = (
                        f"👤 *{_uname}*\n\n"
                        f"✅ *Withdrawal Successful!*\n\n"
                        f"💰 *Amount:* `${net_amt:.4f} USDT BEP-20`\n"
                        f"📋 *Address:* `{masked}`\n"
                        f"🔗 [View on BSCScan]({bscscan_url})"
                    )
                    await context.bot.send_message(get_withdraw_ch_id(), ch_msg, parse_mode="Markdown", disable_web_page_preview=False)
                except: pass
                try:
                    _urow  = get_user(uid)
                    _uname = (_urow["full_name"] or _urow["username"] or str(uid)) if _urow else str(uid)
                    _uhandle = (f"@{_urow['username']}" if _urow and _urow["username"] else "_(username নেই)_")
                    admin_ch_msg = (
                        f"👤 *{_uname}*\n"
                        f"📛 *Username:* {_uhandle}\n"
                        f"🆔 *User ID:* `{uid}`\n\n"
                        f"✅ *Withdraw সফল!*\n\n"
                        f"💳 *Method:* USDT BEP-20 (BSC)\n"
                        f"📋 *Address:* `{address}`\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"💵 *Debit:* `${amt:.4f} USDT`\n"
                        f"📉 *Fee:* `${fee:.4f} USDT`\n"
                        f"💰 *Sent:* `${net_amt:.4f} USDT`\n"
                        f"⛽ *Gas Fee:* `{gas_paid_str}`\n"
                        f"━━━━━━━━━━━━━━━━\n"
                        f"🔖 *WD ID:* `#{wid}`\n"
                        f"🔗 *TX:* [BSCScan]({bscscan_url})\n"
                        f"🕐 *সময়:* {now()}"
                    )
                    await context.bot.send_message(-1003963267867, admin_ch_msg, parse_mode="Markdown", disable_web_page_preview=True)
                except Exception as _e:
                    log.warning(f"[AUTO-WD] #{wid}: Admin detail channel পাঠানো যায়নি — {_e}")
                log.info(f"[AUTO-WD] #{wid}: ✅ Approved — tx={tx_hash} | gas={gas_paid_str}")
            else:
                # ── ব্যর্থ — refund ─────────────────────────────────────
                with _c() as c:
                    c.execute("UPDATE withdrawals SET status='rejected' WHERE id=%s", (wid,))
                add_bal(uid, amt)
                mark_db_dirty()
                _ulang2 = get_lang(uid)
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
                except: pass
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
                except: pass
                log.error(f"[AUTO-WD] #{wid}: ❌ Failed — {err}")

            await asyncio.sleep(3)  # TX এর মাঝে একটু বিরতি


def _setup_uptimerobot():
    """
    UptimeRobot API দিয়ে monitor auto-register করো।
    Env vars:
      UPTIMEROBOT_API_KEY  — UptimeRobot Main API key (ur...)
      BOT_PUBLIC_URL       — (optional) override ping URL
    """
    import urllib.request, urllib.parse, urllib.error

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
                log.info(f"[UptimeRobot] Monitor আছে কিন্তু URL পুরনো — update হবে না (manually করুন)।")
            else:
                log.info(f"✅ [UptimeRobot] Monitor আগে থেকেই আছে ✓")
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
    # _setup_uptimerobot() — disabled (UptimeRobot manually সেট করুন)
    async def post_init(application):
        global _app
        _app = application   # immediate backup scheduling এর জন্য
        # ── Step 1: DB না থাকলে Telegram থেকে নিজেই restore করো ──
        # PostgreSQL — auto-restore দরকার নেই
        restored = False
        # ── Step 2: Startup backup পাঠাও ──────────────────────────
        log.info("✅ PostgreSQL connected — data permanently stored in Neon.")
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
                interval=60,
                first=60,
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
                job_post_leaderboard,
                time=dt_time(hour=0, minute=5, tzinfo=BD_TZ),
                name="daily_leaderboard"
            )
            application.job_queue.run_repeating(
                job_lb_adm_update,
                interval=600,
                first=600,
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
            log.info("✅ Dirty-flag backup job registered (প্রতি ৩০ সেকেন্ড)")
            log.info("✅ Health check job registered (প্রতি ১ ঘণ্টায় auto-check & fix)")
            log.info("✅ Stuck-state cleaner job registered (প্রতি ৩০ মিনিট)")
            log.info(f"✅ Scheduled report job registered (প্রতি {BACKUP_INTERVAL_HOURS} ঘণ্টায়)")
            log.info("✅ Daily leaderboard job registered (রাত ১২:০৫ BD)")
            log.info("✅ Admin leaderboard auto-update job registered (প্রতি ১০ মিনিট)")
            log.info("✅ BEP-20 Auto Withdraw job registered (প্রতি ২ মিনিট)")
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
                asyncio.create_task(_run_daily_at(job_post_leaderboard, 0, 5, BD_TZ)),
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
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )
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
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(_smart_error_handler)
    log.info("✅ Task Earn Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    import time, asyncio as _asyncio
    keep_alive()   # ← Flask server চালু (Render web service এর জন্য)
    time.sleep(3)  # Flask পুরোপুরি bind হতে দাও — Render port scan এর আগে
    while True:
        # Python 3.12+: run_polling() শেষ হলে loop বন্ধ হয়ে যায়।
        # প্রতিটা iteration এ fresh loop বানাও — না হলে
        # "There is no current event loop in thread 'MainThread'" crash হয়।
        _loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(_loop)
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
