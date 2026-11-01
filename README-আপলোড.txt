=====================================================================
  TASK EARN BOT — সব আপডেট ফাইল একসাথে (একবারে আপলোড)
=====================================================================
এই zip এ যা যা আছে:
  bot.py          → মেইন বট (সব নতুন ফিচার + স্পিড ফিক্স)
  panel.py        → verification panel
  webadmin.py     → অ্যাডমিন panel (base)
  webadmin2.py    → devices/ips/alerts/settings panel
  keep_alive.py   → keep alive
  vtoken.py       → verification token
  requirements.txt, runtime.txt → Render কনফিগ

কীভাবে আপলোড করবেন:
  1. সব ফাইল zip থেকে বের করুন
  2. GitHub repo-তে একই নামে replace করে commit+push
     (অথবা Render Dashboard → Advanced → Deploy)
  3. Render auto-restart করবে

গুরুত্বপূর্ণ:
  • BOT_BUILD এবারও অপরিবর্তিত (2026-11-01.4) — আপডেটে আবার
    কাউকে re-verify করতে হবে না।
  • টাকা/ব্যালান্স/পয়েন্ট/রেফারেল — কিছুই মোছা হবে না।

নতুন সেটিং (প্যানেল → Settings):
  • Point system          — পয়েন্ট ON/OFF
  • Point 0 → Task block  — পয়েন্ট ০ হলে task ব্লক হবে কি না (ডিফল্ট বন্ধ)
  • Update reset          — নতুন build-ে সবাই re-verify (ডিফল্ট চালু)
  • Security alerts (DM)  — এখন সত্যিই কাজ করে
  • IP data retention     — এখন সত্যিই কাজ করে (১২ ঘণ্টা পর পর পরিষ্কার)

নতুন কমান্ড:
  /btest     → নতুন ইউজার কেমন দেখবে + সিস্টেম চেক
  /resetall  → এখনই সবাইকে re-verify (confirm বাটনসহ)
=====================================================================
