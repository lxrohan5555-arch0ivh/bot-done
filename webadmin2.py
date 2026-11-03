"""Admin Panel বাকি পাতা — devices, ips, countries, alerts, tasks, earnings,
withdrawals, referrals, logs, settings। একই blueprint-এ যোগ হয় (wadm)।
"""
import os
import time

from flask import request, redirect, Response

from webadmin import (
    wadm, page, guard, table, badge, kpi, money, num, flag, esc, icon, now,
    one, many, scal, sfloat, _x, log_admin, client_ip, spark, bars, donut,
    admin_key, unread_alerts, alerts_changed, many_cached,
    _fire_settings_changed, _fire_user_changed,
)


def _toast():
    return request.args.get("ok") or None


def _err():
    return request.args.get("e") or None


def settings_get(key, default=""):
    r = one("SELECT value FROM settings WHERE key=%s", (key,))
    return (r["value"] if r and r["value"] is not None else default)


def settings_all(keys):
    """সব key এক query-তে — প্রতি key-তে আলাদা round-trip (settings পেজ ~১৭ query) বাঁচে।"""
    keys = [str(k) for k in keys]
    out = {k: "" for k in keys}
    if not keys:
        return out
    try:
        ph = ",".join(["%s"] * len(keys))
        for r in many("SELECT key, value FROM settings WHERE key IN (" + ph + ")", tuple(keys)):
            k, v = r.get("key"), r.get("value")
            if k in out and v is not None:
                out[k] = v
    except Exception:
        pass
    return out


def settings_set(key, value):
    try:
        _x("INSERT INTO settings (key, value) VALUES (%s,%s) "
           "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, str(value)))
        try:
            _fire_settings_changed([key])   # বটের ৬০s ক্যাশে বাধা — সাথে সাথে কার্যকর
        except Exception:
            pass
        return True
    except Exception:
        return False


# ───────────────────────── devices ─────────────────────────
@wadm.route("/admin/devices", methods=["GET"])
def devices():
    g = guard()
    if g:
        return g
    rows = many(
        "SELECT d.device_hash, COUNT(DISTINCT d.user_id) AS accounts, "
        "MIN(d.first_seen) AS first_seen, MAX(d.last_seen) AS last_seen, "
        "MAX(d.platform) AS platform "
        "FROM devices d WHERE COALESCE(d.device_hash,'') <> '' "
        "GROUP BY d.device_hash ORDER BY accounts DESC, last_seen DESC LIMIT 100")
    trs = []
    for r in rows:
        accts = int(r["accounts"] or 0)
        users = many("SELECT user_id, full_name, username FROM devices d "
                     "LEFT JOIN users u ON u.user_id=d.user_id "
                     "WHERE d.device_hash=%s GROUP BY user_id, full_name, username LIMIT 8",
                     (r["device_hash"],))
        links = " ".join(
            f"<a class='mono' href='/admin/user/{int(u['user_id'])}'>#{int(u['user_id'])}</a>"
            for u in users if u["user_id"])
        status = badge("shared device", "err") if accts > 1 else badge("unique", "ok")
        trs.append([
            f"<span class='mono'>{esc(str(r['device_hash'])[:24])}</span>",
            f"<b>{num(accts)}</b>", status, links or "<span class='mut'>-</span>",
            esc(str(r["platform"] or "-")),
            esc(str(r["first_seen"] or "")[:16]),
            esc(str(r["last_seen"] or "")[:16]),
        ])
    dup_alerts = many(
        "SELECT user_id, detail, created_at, status FROM security_alerts "
        "WHERE kind='multi_account' ORDER BY id DESC LIMIT 12")
    cards = "".join(
        f"<div class='card' style='border-color:#ef444455;margin-bottom:10px'>"
        f"<div class='row'><b>⚠️ Duplicate device</b>{badge(str(a['status']), 'warn' if str(a['status']) == 'new' else 'ok')}"
        f"<a href='/admin/user/{int(a['user_id'] or 0)}' style='margin-left:auto'>User #{int(a['user_id'] or 0)} →</a></div>"
        f"<div class='mut' style='margin-top:4px'>{esc(str(a['detail'] or ''))}</div>"
        f"<div class='mut' style='font-size:11px'>{esc(str(a['created_at'] or '')[:19])}</div></div>"
        for a in dup_alerts) or "<div class='empty'>কোনো duplicate device alert নেই</div>"
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('Devices', num(len(rows)), 'ইউনিক ডিভাইস', 'device', '#a855f7'),
            kpi('Shared Devices', num(sum(1 for r in rows if int(r['accounts'] or 0) > 1)),
                'একাধিক অ্যাকাউন্ট', 'alert', '#ef4444'),
            kpi('Alerts', num(unread_alerts()), 'নতুন মাল্টি-অ্যাকাউন্ট', 'shield', '#f59e0b'),
        ])}</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("alert") + " Duplicate Device Alerts</h3>"
        + cards + "</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("device") + " Device Management</h3>"
        + table(["Device (hash)", "Accounts", "Status", "Linked users", "Platform", "First seen", "Last seen"], trs)
        + "</div>"
    )
    return page("Device Management", body, "devices")


# ───────────────────────── ips ─────────────────────────
def _ensure_ip_bans():
    try:
        _x("CREATE TABLE IF NOT EXISTS ip_bans (ip TEXT PRIMARY KEY, reason TEXT DEFAULT '', "
           "added_by BIGINT DEFAULT 0, created_at TEXT DEFAULT '')")
    except Exception:
        pass


@wadm.route("/admin/ips", methods=["GET"])
def ips():
    g = guard()
    if g:
        return g
    _ensure_ip_bans()
    rows = many(
        "SELECT ip, COUNT(DISTINCT user_id) AS accounts, MAX(country) AS country, "
        "MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen, SUM(hits) AS hits "
        "FROM user_ips WHERE COALESCE(ip,'') <> '' GROUP BY ip "
        "ORDER BY accounts DESC, last_seen DESC LIMIT 100")
    bans = {r["ip"]: r for r in many("SELECT ip, reason, created_at FROM ip_bans")}
    trs = []
    for r in rows:
        ip = r["ip"]
        accts = int(r["accounts"] or 0)
        warn = badge("many accounts", "err") if accts >= 3 else badge("ok", "mut")
        if ip in bans:
            warn += " " + badge("IP banned", "err")
        trs.append([
            f"<span class='mono'>{esc(ip)}</span>",
            f"{flag(r['country'])} {esc(r['country'] or '-')}",
            f"<b>{num(accts)}</b>", warn,
            num(r["hits"]),
            esc(str(r["first_seen"] or "")[:16]),
            esc(str(r["last_seen"] or "")[:16]),
            (f"<form method='post' action='/admin/do/ipunban' style='display:inline'>"
             f"<input type='hidden' name='ip' value='{esc(ip)}'>"
             "<button class='gray sm' type='submit'>Unban</button></form>"
             if ip in bans else
             f"<form method='post' action='/admin/do/ipban' style='display:inline' "
             "data-confirm='এই IP ব্যান করবেন?'>"
             f"<input type='hidden' name='ip' value='{esc(ip)}'>"
             "<button class='red sm' type='submit'>Ban IP</button></form>"),
        ])
    ban_rows = [[f"<span class='mono'>{esc(b['ip'])}</span>", esc(b["reason"] or "-"),
                 esc(str(b["created_at"] or "")[:19]),
                 f"<form method='post' action='/admin/do/ipunban'><input type='hidden' name='ip' "
                 f"value='{esc(b['ip'])}'><button class='gray sm' type='submit'>Remove</button></form>"]
                for b in bans.values()]
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('Tracked IPs', num(len(rows)), 'ডেটাবেসে', 'globe', '#4f7cff'),
            kpi('Multi-account IP', num(sum(1 for r in rows if int(r['accounts'] or 0) >= 3)),
                '৩+ অ্যাকাউন্ট', 'alert', '#f59e0b'),
            kpi('Banned IPs', num(len(bans)), 'ব্লক করা', 'ban', '#ef4444'),
        ])}</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("ban") + " IP Ban Management</h3>"
        "<form class='row' method='post' action='/admin/do/ipban'>"
        "<input name='ip' placeholder='1.2.3.4' class='f1' required>"
        "<input name='reason' placeholder='কারণ' class='f1'>"
        "<button class='red' type='submit'>Ban IP</button></form>"
        + table(["IP", "Reason", "Added", ""], ban_rows, "কোনো IP ব্যান করা নেই")
        + "</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("globe") + " IP Tracking</h3>"
        "<p class='mut' style='font-size:11.5px'>শুধু একই IP হওয়ার কারণে কাউকে অটো-ব্যান করা হয় না — "
        "শুধু সতর্কতা দেখানো হয়।</p>"
        + table(["IP", "Country", "Accounts", "Status", "Hits", "First seen", "Last seen", "Action"], trs)
        + "</div>"
    )
    return page("IP Tracking", body, "ips")


@wadm.route("/admin/do/ipban", methods=["POST"])
def ipban():
    g = guard()
    if g:
        return g
    _ensure_ip_bans()
    ip = (request.form.get("ip") or "").strip()
    if ip:
        try:
            _x("INSERT INTO ip_bans (ip, reason, created_at) VALUES (%s,%s,%s) "
               "ON CONFLICT (ip) DO UPDATE SET reason=EXCLUDED.reason",
               (ip, (request.form.get("reason") or "admin panel")[:200], now()))
            log_admin("ipban", 0, ip)
        except Exception:
            pass
    return redirect("/admin/ips?ok=IP+banned")


@wadm.route("/admin/do/ipunban", methods=["POST"])
def ipunban():
    g = guard()
    if g:
        return g
    ip = (request.form.get("ip") or "").strip()
    if ip:
        try:
            _x("DELETE FROM ip_bans WHERE ip=%s", (ip,))
            log_admin("ipunban", 0, ip)
        except Exception:
            pass
    return redirect("/admin/ips?ok=IP+unbanned")


@wadm.route("/admin/do/ipban/<int:uid>", methods=["POST"])
def ipban_user(uid):
    g = guard()
    if g:
        return g
    _ensure_ip_bans()
    u = one("SELECT COALESCE(last_ip,'') AS ip, COALESCE(reg_ip,'') AS rip FROM users WHERE user_id=%s", (uid,))
    ip = ((u["ip"] or u["rip"]) if u else "").strip()
    if ip:
        try:
            _x("INSERT INTO ip_bans (ip, reason, created_at) VALUES (%s,%s,%s) "
               "ON CONFLICT (ip) DO UPDATE SET reason=EXCLUDED.reason",
               (ip, f"user {uid}", now()))
            log_admin("ipban", uid, ip)
            _fire_user_changed(uid)
        except Exception:
            pass
    return redirect(f"/admin/user/{uid}?ok=IP+banned")


# ───────────────────────── countries ─────────────────────────
@wadm.route("/admin/countries", methods=["GET"])
def countries():
    g = guard()
    if g:
        return g
    rows = many(
        "SELECT COALESCE(NULLIF(country,''),'??') AS c, COUNT(*) AS total, "
        "SUM(CASE WHEN COALESCE(vrf_pass,0)=1 THEN 1 ELSE 0 END) AS ver, "
        "SUM(CASE WHEN COALESCE(is_banned,0)=1 THEN 1 ELSE 0 END) AS banned, "
        "COALESCE(SUM(balance),0) AS bal FROM users GROUP BY c ORDER BY total DESC")
    total_users = sum(int(r["total"] or 0) for r in rows) or 1
    trs = [[f"{flag(r['c'])} <b>{esc(r['c'])}</b>", num(r["total"]),
            f"{round(int(r['total'] or 0) / total_users * 100, 1)}%",
            num(r["ver"]), num(max(0, int(r["total"] or 0) - int(r["ver"] or 0))),
            num(r["banned"]), money(r["bal"])] for r in rows[:40]]
    chart = bars([(f"{flag(r['c'])} {r['c']}", r["total"]) for r in rows[:8]]) if rows else ""
    body = (
        f"<div class='grid g2'>"
        f"<div class='card'><h3>{icon('globe')} Country Ranking</h3>"
        + donut([(f"{flag(r['c'])} {r['c']}", r["total"]) for r in rows[:6]], num(total_users))
        + "</div>"
        f"<div class='card'><h3>{icon('trend')} Top Countries</h3>{chart}</div></div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("globe") + " Country Statistics</h3>"
        + table(["Country", "Users", "Share", "Verified", "Unverified", "Banned", "Balance"], trs)
        + "</div>"
    )
    return page("Country Statistics", body, "countries")


# ───────────────────────── security alerts ─────────────────────────
SEV = {"multi_account": ("High", "err"), "duplicate_device": ("Critical", "err"),
       "suspicious_ip": ("Medium", "warn"), "device_changed": ("Medium", "warn"),
       "suspicious_referral": ("High", "warn"), "verify_retry": ("Low", "blue")}


@wadm.route("/admin/alerts", methods=["GET"])
def alerts():
    g = guard()
    if g:
        return g
    f = (request.args.get("f") or "new").strip()
    where, args = "", ()
    if f in ("new", "reviewed", "ignored"):
        where, args = "WHERE status=%s", (f,)
    rows = many("SELECT id, kind, user_id, ip, detail, status, created_at FROM security_alerts "
                + where + " ORDER BY id DESC LIMIT 150", args)
    trs = []
    for r in rows:
        sev, cls = SEV.get(str(r["kind"]), ("Medium", "warn"))
        uid = int(r["user_id"] or 0)
        trs.append([
            f"<a class='mono' href='/admin/user/{uid}'>#{uid}</a>",
            esc(r["kind"] or "-"), badge(sev, cls),
            esc(str(r["detail"] or "")[:110]),
            f"<span class='mono'>{esc(r['ip'] or '-')}</span>",
            badge(str(r["status"]), "ok" if str(r["status"]) == "reviewed"
                  else "mut" if str(r["status"]) == "ignored" else "warn"),
            f"<span class='mut'>{esc(str(r['created_at'] or '')[:19])}</span>",
            (f"<form method='post' action='/admin/do/alert/{int(r['id'])}/review' style='display:inline'>"
             "<button class='ok sm' type='submit'>Review</button></form> "
             f"<form method='post' action='/admin/do/alert/{int(r['id'])}/ignore' style='display:inline'>"
             "<button class='gray sm' type='submit'>Ignore</button></form>"),
        ])
    tabs = ("<div class='seg'>" + "".join(
        f"<a class='{'on' if f == k else ''}' href='/admin/alerts?f={k}'>{lbl}</a>"
        for k, lbl in (("new", "New"), ("reviewed", "Reviewed"), ("ignored", "Ignored"), ("all", "All"))
    ) + "</div>")
    counts = {k: scal("SELECT COUNT(*) FROM security_alerts WHERE status=%s", (k,))
              for k in ("new", "reviewed", "ignored")}
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('New Alerts', num(counts['new']), 'দেখা হয়নি', 'alert', '#ef4444'),
            kpi('Reviewed', num(counts['reviewed']), 'সমাধান হয়েছে', 'check', '#22c55e'),
            kpi('Ignored', num(counts['ignored']), 'বাদ দেওয়া', 'x', '#8b8ba3'),
            kpi('Duplicate Device', num(scal("SELECT COUNT(*) FROM security_alerts WHERE kind='multi_account'")),
                'মাল্টি-অ্যাকাউন্ট', 'device', '#a855f7'),
        ])}</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("alert") + " Security Center</h3>"
        + tabs + table(["User", "Category", "Severity", "Detail", "IP", "Status", "Time", "Action"], trs,
                       "কোনো অ্যালার্ট নেই")
        + "</div>"
    )
    return page("Security Alerts", body, "alerts")


@wadm.route("/admin/do/alert/<int:aid>/<act>", methods=["POST"])
def alert_action(aid, act):
    g = guard()
    if g:
        return g
    status = "reviewed" if act == "review" else "ignored"
    try:
        _x("UPDATE security_alerts SET status=%s, handled_at=%s WHERE id=%s", (status, now(), aid))
        alerts_changed()      # সাইডবারের কাউন্ট ক্যাশে পরিষ্কার
        try:
            _x("UPDATE users SET is_suspicious=0 WHERE user_id=(SELECT user_id FROM security_alerts WHERE id=%s)",
               (aid,))
        except Exception:
            pass
        log_admin(f"alert_{act}", aid)
    except Exception:
        pass
    return redirect("/admin/alerts?ok=Alert+updated")


# ───────────────────────── tasks ─────────────────────────
@wadm.route("/admin/tasks", methods=["GET"])
def tasks():
    g = guard()
    if g:
        return g
    cat_t = many_cached("tasks_cat",
                 "SELECT id, name, price, is_active, completed_count, max_completions, created_at "
                 "FROM category_tasks ORDER BY id DESC LIMIT 30")
    tg_t = many_cached("tasks_tg",
                "SELECT id, name, price, is_active, completed_count, max_completions, created_at "
                "FROM tg_tasks ORDER BY id DESC LIMIT 30")
    bio_t = many_cached("tasks_bio",
                 "SELECT id, name, price, is_active, completed_count, max_completions "
                 "FROM tasks ORDER BY id DESC LIMIT 30")
    comp_cat = many_cached("tasks_comp_cat",
                    "SELECT c.user_id, u.full_name, u.username, ct.name AS task, ct.price, c.done_at "
                    "FROM category_task_completions c LEFT JOIN category_tasks ct ON ct.id=c.task_id "
                    "LEFT JOIN users u ON u.user_id=c.user_id ORDER BY c.id DESC LIMIT 40")
    comp_tg = many_cached("tasks_comp_tg",
                   "SELECT c.user_id, u.full_name, u.username, tt.name AS task, tt.price, c.done_at "
                   "FROM tg_task_completions c LEFT JOIN tg_tasks tt ON tt.id=c.task_id "
                   "LEFT JOIN users u ON u.user_id=c.user_id ORDER BY c.id DESC LIMIT 40")
    comp_bio = many_cached("tasks_comp_bio",
                    "SELECT b.user_id, u.full_name, u.username, 'Bio Task' AS task, b.amount AS price, "
                    "b.paid_at AS done_at FROM bio_task_completions b "
                    "LEFT JOIN users u ON u.user_id=b.user_id ORDER BY b.id DESC LIMIT 40")
    rows = []
    for src, label in ((comp_cat, "Category"), (comp_tg, "Telegram"), (comp_bio, "Bio")):
        for r in src:
            rows.append((str(r["done_at"] or "")[:19], int(r["user_id"] or 0),
                         r["full_name"] or "User", r["username"] or "", label,
                         r["task"] or "-", r["price"] or 0))
    rows.sort(reverse=True)
    trs = [[f"<span class='mut'>{esc(t)}</span>",
            f"<a class='mono' href='/admin/user/{uid}'>#{uid}</a> {esc(fn)}"
            + (f"<div class='mut' style='font-size:11px'>@{esc(un)}</div>" if un else ""),
            badge(lbl, "blue"), esc(task), money(pr)] for t, uid, fn, un, lbl, task, pr in rows[:120]]
    task_rows = [[esc(r["name"]), money(r["price"]),
                  badge("active", "ok") if int(r["is_active"] or 0) else badge("inactive", "mut"),
                  num(r["completed_count"]),
                  num(r["max_completions"]) if int(r["max_completions"] or 0) else "∞"]
                 for r in (cat_t + tg_t + bio_t)]
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('Category Tasks', num(len(cat_t)), 'মোট টাস্ক', 'task', '#7c5cff'),
            kpi('Telegram Tasks', num(len(tg_t)), 'মোট টাস্ক', 'task', '#4f7cff'),
            kpi('Bio Tasks', num(len(bio_t)), 'মোট টাস্ক', 'task', '#22d3ee'),
            kpi('Completed', num(len(comp_cat) + len(comp_tg) + len(comp_bio)),
                'সর্বশেষ ১২০-এর মধ্যে', 'check', '#22c55e'),
        ])}</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("task") + " Task Management</h3>"
        + table(["Task", "Reward", "Status", "Completed", "Limit"], task_rows, "কোনো টাস্ক নেই")
        + "</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("users")
        + " Recent Completions (কে কবে কোন টাস্ক করল)</h3>"
        + table(["Time", "User", "Type", "Task", "Reward"], trs, "এখনো কোনো টাস্ক সম্পন্ন হয়নি")
        + "</div>"
    )
    return page("Task Management", body, "tasks")


@wadm.route("/admin/task-stats", methods=["GET"])
def task_stats():
    g = guard()
    if g:
        return g
    top_cat = many("SELECT ct.name, ct.price, COUNT(*) AS n FROM category_task_completions c "
                   "LEFT JOIN category_tasks ct ON ct.id=c.task_id GROUP BY ct.name, ct.price "
                   "ORDER BY n DESC LIMIT 12")
    top_tg = many("SELECT tt.name, tt.price, COUNT(*) AS n FROM tg_task_completions c "
                  "LEFT JOIN tg_tasks tt ON tt.id=c.task_id GROUP BY tt.name, tt.price "
                  "ORDER BY n DESC LIMIT 12")
    daily = many("SELECT d, COUNT(*) AS n FROM ("
                 " SELECT SUBSTRING(done_at,1,10) AS d FROM category_task_completions"
                 " UNION ALL SELECT SUBSTRING(done_at,1,10) FROM tg_task_completions"
                 " UNION ALL SELECT SUBSTRING(paid_at,1,10) FROM bio_task_completions"
                 ") x WHERE d IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 14")
    daily = list(reversed(daily))
    nvals = [r["n"] for r in daily]
    total = scal("SELECT COUNT(*) FROM category_task_completions") + \
        scal("SELECT COUNT(*) FROM tg_task_completions") + scal("SELECT COUNT(*) FROM bio_task_completions")
    earn = sfloat("SELECT COALESCE(SUM(p),0) FROM ("
                  " SELECT ct.price AS p FROM category_task_completions c LEFT JOIN category_tasks ct ON ct.id=c.task_id"
                  " UNION ALL SELECT tt.price FROM tg_task_completions c LEFT JOIN tg_tasks tt ON tt.id=c.task_id"
                  " UNION ALL SELECT amount FROM bio_task_completions) x")
    trs = [[esc(r["name"] or "-"), money(r["price"]), num(r["n"]), money(float(r["price"] or 0) * int(r["n"] or 0))]
           for r in (top_cat + top_tg)]
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('Total Completions', num(total), 'সব ধরন', 'task', '#7c5cff'),
            kpi('Total Paid', money(earn), 'টাস্ক থেকে আয়', 'money', '#22c55e'),
            kpi('Today', num(nvals[-1] if nvals else 0), 'আজকের সম্পন্ন', 'clock', '#4f7cff'),
        ])}</div>"
        f"<div class='card' style='margin-top:14px'><h3>{icon('trend')} Daily Completions (14 days)</h3>"
        + spark(nvals, "#4f7cff") + "</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("task") + " Top Tasks</h3>"
        + table(["Task", "Reward", "Completions", "Total payout"], trs) + "</div>"
    )
    return page("Task Statistics", body, "taskstats")


# ───────────────────────── earnings & withdrawals ─────────────────────────
@wadm.route("/admin/earnings", methods=["GET"])
def earnings():
    g = guard()
    if g:
        return g
    bal = sfloat("SELECT COALESCE(SUM(balance),0) FROM users")
    pts = scal("SELECT COALESCE(SUM(points),0) FROM users")
    task_pay = sfloat("SELECT COALESCE(SUM(p),0) FROM ("
                      " SELECT ct.price AS p FROM category_task_completions c LEFT JOIN category_tasks ct ON ct.id=c.task_id"
                      " UNION ALL SELECT tt.price FROM tg_task_completions c LEFT JOIN tg_tasks tt ON tt.id=c.task_id"
                      " UNION ALL SELECT amount FROM bio_task_completions) x")
    paid = sfloat("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='approved'")
    pend = sfloat("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='pending'")
    daily = many("SELECT d, SUM(p) AS s FROM ("
                 " SELECT SUBSTRING(c.done_at,1,10) AS d, ct.price AS p FROM category_task_completions c"
                 "  LEFT JOIN category_tasks ct ON ct.id=c.task_id"
                 " UNION ALL SELECT SUBSTRING(c.done_at,1,10), tt.price FROM tg_task_completions c"
                 "  LEFT JOIN tg_tasks tt ON tt.id=c.task_id"
                 " UNION ALL SELECT SUBSTRING(b.paid_at,1,10), b.amount FROM bio_task_completions b"
                 ") x WHERE d IS NOT NULL GROUP BY d ORDER BY d DESC LIMIT 14")
    daily = list(reversed(daily))
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('Total User Earnings', money(task_pay), 'টাস্ক থেকে মোট', 'money', '#22c55e'),
            kpi('Current Balance', money(bal), 'ইউজারদের হাতে', 'wallet', '#4f7cff'),
            kpi('Total Paid', money(paid), 'উইথড্র approved', 'check', '#22c55e'),
            kpi('Pending', money(pend), 'অপেক্ষমাণ', 'clock', '#f59e0b'),
            kpi('Total Points', num(pts), 'সব ইউজার', 'trend', '#a855f7'),
        ])}</div>"
        f"<div class='card' style='margin-top:14px'><h3>{icon('trend')} Earnings (14 days)</h3>"
        + spark([r["s"] or 0 for r in daily], "#22c55e") + "</div>"
    )
    return page("Earnings", body, "earnings")


@wadm.route("/admin/withdrawals", methods=["GET"])
def withdrawals():
    g = guard()
    if g:
        return g
    st = (request.args.get("st") or "").strip()
    where, args = ("WHERE w.status=%s", (st,)) if st in ("pending", "approved", "rejected") else ("", ())
    rows = many("SELECT w.id, w.user_id, u.full_name, u.username, w.amount, w.status, w.address, "
                "w.requested_at, w.tx_hash FROM withdrawals w LEFT JOIN users u ON u.user_id=w.user_id "
                + where + " ORDER BY w.id DESC LIMIT 120", args)
    counts = {k: scal("SELECT COUNT(*) FROM withdrawals WHERE status=%s", (k,))
              for k in ("pending", "approved", "rejected")}
    # "All" ট্যাবের কাউন্ট (counts[''] না থাকায় পেজটা KeyError-এ 500 হতো)
    counts[""] = scal("SELECT COUNT(*) FROM withdrawals")
    sums = {k: sfloat("SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status=%s", (k,))
            for k in ("pending", "approved", "rejected")}
    trs = [[f"#{int(r['id'])}",
            f"<a class='mono' href='/admin/user/{int(r['user_id'] or 0)}'>#{int(r['user_id'] or 0)}</a> "
            f"{esc(r['full_name'] or '')}",
            money(r["amount"]),
            badge(str(r["status"]), "ok" if str(r["status"]) == "approved"
                  else "err" if str(r["status"]) == "rejected" else "warn"),
            esc(str(r["address"] or "-")[:22]),
            f"<span class='mut'>{esc(str(r['requested_at'] or '')[:19])}</span>",
            f"<span class='mono'>{esc(str(r['tx_hash'] or '-')[:12])}</span>"] for r in rows]
    tabs = ("<div class='seg'>" + "".join(
        f"<a class='{'on' if st == k else ''}' href='/admin/withdrawals?st={k}'>{lbl} ({counts[k]})</a>"
        for k, lbl in (("", "All"), ("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"))
    ) + "</div>")
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('Pending', f"{counts['pending']} · {money(sums['pending'])}", 'অনুমোদনের অপেক্ষায়', 'clock', '#f59e0b'),
            kpi('Approved', f"{counts['approved']} · {money(sums['approved'])}", 'পেইড', 'check', '#22c55e'),
            kpi('Rejected', f"{counts['rejected']} · {money(sums['rejected'])}", 'বাতিল', 'x', '#ef4444'),
        ])}</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("wallet") + " Withdrawals</h3>"
        "<p class='mut' style='font-size:11.5px'>পেমেন্ট বটের নিজস্ব ফ্লো (অ্যাডমিন অনুমোদন + চ্যানেল মেসেজ) "
        "দিয়েই হয় — এখান থেকে শুধু মনিটর করা যায়, যাতে ডাবল পেমেন্ট না হয়।</p>"
        + tabs + table(["ID", "User", "Amount", "Status", "Wallet", "Requested", "TX"], trs, "কোনো উইথড্র নেই")
        + "</div>"
    )
    return page("Withdrawals", body, "withdrawals")


# ───────────────────────── referrals ─────────────────────────
@wadm.route("/admin/referrals", methods=["GET"])
def referrals():
    g = guard()
    if g:
        return g
    total = scal("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")
    top = many("SELECT u.user_id, u.full_name, u.username, COUNT(r.user_id) AS n "
               "FROM users r JOIN users u ON u.user_id=r.referred_by "
               "GROUP BY u.user_id, u.full_name, u.username ORDER BY n DESC LIMIT 15")
    recent = many("SELECT r.user_id, r.full_name, r.username, r.referred_by, r.joined_at "
                  "FROM users r WHERE r.referred_by IS NOT NULL ORDER BY r.user_id DESC LIMIT 60")
    rew = sfloat("SELECT COALESCE(SUM(amount),0) FROM commissions")
    board = [[f"<b>#{i+1}</b>", f"<a class='mono' href='/admin/user/{int(r['user_id'])}'>#{int(r['user_id'])}</a> "
              f"{esc(r['full_name'] or '')}", f"<span class='mut'>@{esc(r['username'] or '-')}</span>",
              f"<b>{num(r['n'])}</b>"] for i, r in enumerate(top)]
    body = (
        f"<div class='grid g-kpi'>{''.join([
            kpi('Total Referrals', num(total), 'রেফার থেকে আসা ইউজার', 'refer', '#7c5cff'),
            kpi('Top Referrers', num(len(top)), 'লিডারবোর্ডে', 'trend', '#4f7cff'),
            kpi('Commission Paid', money(rew), 'মোট কমিশন', 'money', '#22c55e'),
        ])}</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("refer") + " Top Referrer Leaderboard</h3>"
        + table(["#", "User", "Username", "Referrals"], board, "কোনো রেফার নেই") + "</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("users") + " Referral History</h3>"
        + table(["User", "Referred by", "Joined"],
                [[f"<a class='mono' href='/admin/user/{int(r['user_id'])}'>#{int(r['user_id'])}</a> "
                  f"{esc(r['full_name'] or '')}",
                  f"<a class='mono' href='/admin/user/{int(r['referred_by'])}'>#{int(r['referred_by'])}</a>",
                  f"<span class='mut'>{esc(str(r['joined_at'] or '')[:19])}</span>"] for r in recent],
                "কোনো রেফার হিস্ট্রি নেই") + "</div>"
    )
    return page("Referrals", body, "referrals")


# ───────────────────────── admin logs ─────────────────────────
@wadm.route("/admin/logs", methods=["GET"])
def logs():
    g = guard()
    if g:
        return g
    rows = many("SELECT id, action, target_uid, detail, ip, created_at FROM admin_actions "
                "ORDER BY id DESC LIMIT 200")
    trs = [[f"<span class='mut mono'>#{int(r['id'])}</span>", esc(r["action"] or "-"),
            f"<a class='mono' href='/admin/user/{int(r['target_uid'] or 0)}'>#{int(r['target_uid'] or 0)}</a>"
            if int(r["target_uid"] or 0) else "<span class='mut'>-</span>",
            esc(str(r["detail"] or "")[:120]),
            f"<span class='mono'>{esc(r['ip'] or '-')}</span>",
            f"<span class='mut'>{esc(str(r['created_at'] or '')[:19])}</span>"] for r in rows]
    body = ("<div class='card'><h3>" + icon("logs") + " Admin Activity Logs</h3>"
            "<p class='mut' style='font-size:11.5px'>বট ও ওয়েব প্যানেল থেকে করা প্রতিটি অ্যাকশন এখানে।</p>"
            + table(["#", "Action", "Target", "Detail", "IP", "Time"], trs, "কোনো লগ নেই") + "</div>")
    return page("Admin Logs", body, "logs")


# ───────────────────────── settings ─────────────────────────
def _secret_link():
    """প্যানেলের গোপন এন্ট্রি-লিংক (সেটিংস পেজে দেখানোর জন্য)।"""
    try:
        from webadmin import panel_secret
        sec = (panel_secret() or "").strip()
        if not sec:
            return request.host_url.rstrip("/") + "/admin"
        return request.host_url.rstrip("/") + "/admin?k=" + sec
    except Exception:
        return request.host_url.rstrip("/") + "/admin"
@wadm.route("/admin/settings", methods=["GET"])
def settings():
    g = guard()
    if g:
        return g
    keys = ["verify_enabled", "sec_device_limit", "sec_ip_limit", "sec_ip_block", "sec_auto_ban",
            "sec_ip_retention_days", "notify_security", "bot_status",
            "points_on", "points_block", "reset_on_update", "sec_device_strict",
            "pt_max", "pt_start", "pt_approve", "pt_reject", "pt_dup",
            "task_notify_mode", "task_notify_channel", "task_notify_daily_max"]
    vals = settings_all(keys)
    body = (
        f"<div class='grid g2'>"
        "<div class='card'><h3>" + icon("lock") + " Admin Access</h3>"
        "<form method='post' action='/admin/do/settings'>"
        "<div class='form'><label>নতুন Admin Password (key)</label>"
        f"<input name='admin_web_key' placeholder='খালি রাখলে অপরিবর্তিত' minlength='6'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 12px'>এটা বদলালে বটের /webadmin-ও নতুন key দেবে।</p>"
        "</div>"
        "<div class='form'><label>Username</label>"
        f"<input value='{esc(__import__('os').environ.get('ADMIN_WEB_USER') or 'admin')}' disabled></div>"
        "<div class='form'><label>🔒 গোপন লিংক (এটা ছাড়া প্যানেল খোলে না)</label>"
        f"<input value='{esc(_secret_link())}' readonly onclick='this.select()'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>এই লিংক কারো সাথে শেয়ার করবেন না। "
        "ভুলে গেলে বটে <b>/webadmin</b> পাঠালে আবার পাবেন।</p></div>"
        "<button type='submit' style='margin-top:10px'>Save Access</button></form></div>"
        "<div class='card'><h3>" + icon("shield") + " Verification & Security</h3>"
        "<form method='post' action='/admin/do/settings'>"
        "<div class='form'><label>Verification</label><select name='verify_enabled'>"
        f"<option value='on' {'selected' if vals['verify_enabled'] != 'off' else ''}>চালু (ON)</option>"
        f"<option value='off' {'selected' if vals['verify_enabled'] == 'off' else ''}>বন্ধ (OFF)</option>"
        "</select></div>"
        "<div class='form'><label>Auto-ban on shared device</label><select name='sec_auto_ban'>"
        f"<option value='on' {'selected' if vals['sec_auto_ban'] != 'off' else ''}>চালু</option>"
        f"<option value='off' {'selected' if vals['sec_auto_ban'] == 'off' else ''}>বন্ধ</option>"
        "</select></div>"
        "<div class='form'><label>Device limit — একই ফোনে সর্বোচ্চ অ্যাকাউন্ট (1 = এক ফোন এক অ্যাকাউন্ট)</label>"
        f"<input name='sec_device_limit' type='number' min='1' value='{esc(vals['sec_device_limit'] or 1)}'></div>"
        "<div class='form'><label>Strict one-phone rule — একই ফোন প্রোফাইল+ব্রাউজার হলে IP বদলালেও ব্লক</label><select name='sec_device_strict'>"
        f"<option value='on' {'selected' if vals.get('sec_device_strict', 'on') != 'off' else ''}>চালু (ডিফল্ট)</option>"
        f"<option value='off' {'selected' if vals.get('sec_device_strict') == 'off' else ''}>বন্ধ</option>"
        "</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>চালু থাকলে মোবাইল ডেটা/ওয়াইফাই বদলে IP পাল্টালেও একই ফোনে দ্বিতীয় অ্যাকাউন্ট verify হবে না।</p></div>"
        "<div class='form'><label>IP limit — একই IP-তে সর্বোচ্চ অ্যাকাউন্ট (ছাড়ালে শুধু অ্যালার্ট)</label>"
        f"<input name='sec_ip_limit' type='number' min='2' value='{esc(vals['sec_ip_limit'] or 2)}'></div>"
        "<div class='form'><label>IP দিয়ে ব্লক</label><select name='sec_ip_block'>"
        f"<option value='off' {'selected' if (vals.get('sec_ip_block') or 'off') != 'on' else ''}>বন্ধ (ডিফল্ট — শুধু অ্যালার্ট)</option>"
        f"<option value='on' {'selected' if (vals.get('sec_ip_block') or 'off') == 'on' else ''}>চালু (verify আটকাবে)</option>"
        "</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>মোবাইল ইন্টারনেটে (CGNAT) এক IP-তে শত শত ইউজার থাকে — তাই ডিফল্টে IP কাউকে আটকায় না, শুধু অ্যালার্ট দেয়।</p></div>"
        "<div class='form'><label>IP data retention (দিন)</label>"
        f"<input name='sec_ip_retention_days' type='number' min='1' value='{esc(vals['sec_ip_retention_days'] or 90)}'></div>"
        "<button type='submit' style='margin-top:10px'>Save Security</button></form></div>"
        "<div class='card'><h3>" + icon("bell") + " Notifications & System</h3>"
        "<form method='post' action='/admin/do/settings'>"
        "<div class='form'><label>Security alerts (Telegram DM)</label><select name='notify_security'>"
        f"<option value='on' {'selected' if vals['notify_security'] != 'off' else ''}>চালু</option>"
        f"<option value='off' {'selected' if vals['notify_security'] == 'off' else ''}>বন্ধ</option>"
        "</select></div>"
        "<div class='form'><label>Bot status</label><select name='bot_status'>"
        f"<option value='on' {'selected' if vals['bot_status'] != 'off' else ''}>চালু</option>"
        f"<option value='off' {'selected' if vals['bot_status'] == 'off' else ''}>বন্ধ</option>"
        "</select></div>"
        "<div class='form'><label>Point system (trust points)</label><select name='points_on'>"
        f"<option value='on' {'selected' if vals['points_on'] != 'off' else ''}>চালু (ON)</option>"
        f"<option value='off' {'selected' if vals['points_on'] == 'off' else ''}>বন্ধ (OFF)</option>"
        "</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>বন্ধ থাকলে approve/reject-এ পয়েন্ট বাড়বে/কমবে না, "
        "পয়েন্ট ০ হয়ে ব্লকও হবে না।</p></div>"
        "<div class='form'><label>Point 0 → Task block (পয়েন্ট ০ হলে task ২ দিন ব্লক)</label><select name='points_block'>"
        f"<option value='on' {'selected' if vals['points_block'] == 'on' else ''}>চালু</option>"
        f"<option value='off' {'selected' if vals['points_block'] != 'on' else ''}>বন্ধ (ডিফল্ট)</option>"
        "</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>বন্ধ থাকলে পয়েন্ট ০ হলেও Task করা যাবে। "
        "চালু করলে আগের মতো ২ দিন ব্লক হবে।</p></div>"
        "<div class='form'><label>Update reset — নতুন build-এ সবাই আবার verify করবে (অটো)</label>"
        "<select name='reset_on_update'>"
        f"<option value='off' {'selected' if vals.get('reset_on_update') != 'on' else ''}>বন্ধ (ডিফল্ট)</option>"
        f"<option value='on' {'selected' if vals.get('reset_on_update') == 'on' else ''}>চালু</option>"
        "</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>ব্যালান্স/পয়েন্ট মোছা হয় না — শুধু verify আবার করতে হয়। "
        "⚠️ নোটিশ কখনো অটো যায় না: বট /resetall (বা বিল্ড আপডেটের মেসেজে বাটন) চাপলে তখনই সবাইকে যায়, এক বিল্ডে একবার।</p></div>"
        "<button type='submit' style='margin-top:10px'>Save System</button></form></div>"
        "<div class='card'><h3>" + icon("trend") + " পয়েন্ট সেটিংস</h3>"
        "<form method='post' action='/admin/do/settings'>"
        "<div class='form'><label>সর্বোচ্চ পয়েন্ট (স্কোর)</label>"
        f"<input name='pt_max' type='number' min='1' max='100000' value='{esc(vals.get('pt_max') or 50)}'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>এটাই পয়েন্টের ছাদ — এর বেশি বাড়বে না। ডিফল্ট ৫০।</p></div>"
        "<div class='form'><label>নতুন ইউজারের শুরুর পয়েন্ট</label>"
        f"<input name='pt_start' type='number' min='0' max='100000' value='{esc(vals.get('pt_start') or 50)}'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>সর্বোচ্চের বেশি দিলে সর্বোচ্চই ধরা হবে। ডিফল্ট ৫০।</p></div>"
        "<div class='form'><label>Task approve হলে কত পয়েন্ট বাড়বে (+)</label>"
        f"<input name='pt_approve' type='number' min='0' max='100000' value='{esc(vals.get('pt_approve') or 5)}'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>ডিফল্ট +৫।</p></div>"
        "<div class='form'><label>Task reject হলে কত পয়েন্ট কমবে (−)</label>"
        f"<input name='pt_reject' type='number' min='0' max='100000' value='{esc(vals.get('pt_reject') or 10)}'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>ডিফল্ট −১০।</p></div>"
        "<div class='form'><label>একই ছবি আবার দিলে কত কমবে (−)</label>"
        f"<input name='pt_dup' type='number' min='0' max='100000' value='{esc(vals.get('pt_dup') or 3)}'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>ডিফল্ট −৩। বদলালে সাথে সাথে বটে কার্যকর (ক্যাশ ১০ মিনিট)।</p></div>"
        "<button type='submit' style='margin-top:10px'>Save Points</button></form></div>"
        "<div class='card'><h3>" + icon("bell") + " Task Notification</h3>"
        "<form method='post' action='/admin/do/settings'>"
        "<div class='form'><label>Task সম্পন্ন / approve হলে নোটিফিকেশন কোথায় যাবে</label>"
        "<select name='task_notify_mode'>"
        "<option value='channel' "
        + ("selected" if (vals.get("task_notify_mode") or "channel") == "channel" else "") + ">শুধু চ্যানেলে</option>"
        "<option value='dm' "
        + ("selected" if vals.get("task_notify_mode") == "dm" else "") + ">শুধু বটে (Admin DM)</option>"
        "<option value='both' "
        + ("selected" if vals.get("task_notify_mode") == "both" else "") + ">চ্যানেল + বটে (দুটোই)</option>"
        "<option value='off' "
        + ("selected" if vals.get("task_notify_mode") == "off" else "") + ">বন্ধ — কোথাও যাবে না</option>"
        "</select>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>ডিফল্ট: শুধু চ্যানেল। "
        "বদলালে সাথে সাথেই বটে কার্যকর।</p></div>"
        "<div class='form'><label>চ্যানেল (আইডি বা @username)</label>"
        f"<input name='task_notify_channel' value='{esc(vals.get('task_notify_channel') or '')}' "
        "placeholder='-1004251213683 বা @mychannel'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>খালি রাখলে আগের ডিফল্ট চ্যানেলেই যাবে। "
        "বটকে ওই চ্যানেলের অ্যাডমিন করতে হবে।</p></div>"
        "<div class='form'><label>দিনে সর্বোচ্চ কতটা নোটিফিকেশন (0 = সীমা নেই)</label>"
        f"<input name='task_notify_daily_max' type='number' min='0' max='100000' "
        f"value='{esc(vals.get('task_notify_daily_max') or 0)}'>"
        "<p class='mut' style='font-size:11px;margin:6px 0 0'>সীমা শেষ হলে ওই দিন আর পাঠানো হবে না — "
        "টাস্ক/টাকা কিছুই আটকাবে না, শুধু নোটিফিকেশন।</p></div>"
        "<button type='submit' style='margin-top:10px'>Save Notification</button></form></div>"
        "</div>"
    )
    return page("Settings", body, "settings", toast=_toast())


@wadm.route("/admin/do/settings", methods=["POST"])
def settings_save():
    g = guard()
    if g:
        return g
    saved = []
    key = (request.form.get("admin_web_key") or "").strip()
    if len(key) >= 6:
        settings_set("admin_web_key", key)
        saved.append("password")
    for k in ("verify_enabled", "sec_auto_ban", "notify_security", "bot_status",
              "points_on", "points_block", "reset_on_update",
              "sec_device_strict", "sec_ip_block"):
        v = (request.form.get(k) or "").strip()
        if v in ("on", "off"):
            settings_set(k, v)
            saved.append(k)
    for k in ("sec_device_limit", "sec_ip_limit", "sec_ip_retention_days"):
        v = (request.form.get(k) or "").strip()
        if v.isdigit():
            settings_set(k, v)
            saved.append(k)
    for k in ("pt_max", "pt_start", "pt_approve", "pt_reject", "pt_dup"):
        v = (request.form.get(k) or "").strip()
        if v.isdigit() and int(v) <= 100000 and (int(v) >= 1 if k == "pt_max" else True):
            settings_set(k, v)
            saved.append(k)
    for k in ("task_notify_mode",):
        v = (request.form.get(k) or "").strip().lower()
        if v in ("channel", "dm", "both", "off"):
            settings_set(k, v)
            saved.append(k)
    v = (request.form.get("task_notify_channel") or "").strip()[:80]
    if v:
        settings_set("task_notify_channel", v)
        saved.append("task_notify_channel")
    v = (request.form.get("task_notify_daily_max") or "").strip()
    if v.isdigit() and int(v) <= 100000:
        settings_set("task_notify_daily_max", v)
        saved.append("task_notify_daily_max")
    log_admin("settings_save", 0, ",".join(saved))
    return redirect("/admin/settings?ok=Settings+saved")
# ───────────────── Task Logs — কে কখন কী জমা দিয়েছে + বাল্ক ডিলিট ─────────────────
# • approve/reject হওয়ার সাথে সাথে জমার ভেতরের ডেটা (পাসওয়ার্ড/কুকি/ইমেইল/ফটো) মুছে যায়
# • এখানে থেকে যায়: কে, কবে/কখন জমা, কখন approve, কোন টাস্ক, কোনটা approved/rejected
# • ☑️ দিয়ে বেছে একবারে ৫০টা ডিলিট করা যায় → ডেটাবেস ছোট থাকে
# • পুরো পেজে মাত্র ২টা DB query (list + per-task summary) — DB/RAM চাপ কম
_UNION = (
    "SELECT * FROM ("
    "SELECT 'gmail' AS k, id, user_id, COALESCE(task_name,'Gmail Task') AS task, "
    "COALESCE(amount,0) AS amount, COALESCE(status,'pending') AS status, "
    "COALESCE(submitted_at,'') AS ts, COALESCE(NULLIF(approved_at,''),'') AS rev, '' AS ident "
    "FROM gmails "
    "UNION ALL "
    "SELECT 'fb', s.id, s.user_id, COALESCE(t.name,'Facebook Task'), COALESCE(t.price,0), "
    "COALESCE(s.status,'pending'), COALESCE(s.created_at,''), "
    "COALESCE(NULLIF(s.reviewed_at,''),''), COALESCE(s.fb_uid,'') "
    "FROM fb_submissions s LEFT JOIN fb_tasks t ON t.id=s.task_id "
    "UNION ALL "
    "SELECT 'ig', s.id, s.user_id, COALESCE(t.name,'Instagram Task'), COALESCE(t.price,0), "
    "COALESCE(s.status,'pending'), COALESCE(s.created_at,''), "
    "COALESCE(NULLIF(s.reviewed_at,''),''), COALESCE(s.ig_username,'') "
    "FROM ig_submissions s LEFT JOIN ig_tasks t ON t.id=s.task_id "
    "UNION ALL "
    "SELECT 'tg', c.id, c.user_id, COALESCE(t.name,'TG Task'), COALESCE(t.price,0), "
    "'approved', COALESCE(c.done_at,''), COALESCE(c.done_at,''), '' "
    "FROM tg_task_completions c LEFT JOIN tg_tasks t ON t.id=c.task_id"
    ") u"
)
_LOG_TBLS = {"gmail": "gmails", "fb": "fb_submissions",
             "ig": "ig_submissions", "tg": "tg_task_completions"}
_LOG_LABEL = {"gmail": "📧 Gmail", "fb": "📘 Facebook",
              "ig": "📸 Instagram", "tg": "✈️ Telegram"}
_LOG_MAX_DEL = 50        # একবারে সর্বোচ্চ ৫০টা ডিলিট
_LOG_ROWS    = 120       # এক পাতায় সর্বোচ্চ কত সারি (মেমরি কম রাখতে)
_LOG_TASKS   = 40        # per-task summary-তে সর্বোচ্চ কত টাস্ক


def _log_rows(kind, st):
    """সব টাইপ একসাথে এক query-তে — kind/status ফিল্টারও DB-তেই।"""
    sql = ("SELECT * FROM (" + _UNION + ") w "
           "WHERE (%s='all' OR w.k=%s) AND (%s='all' OR w.status=%s) "
           "ORDER BY w.ts DESC LIMIT %s")
    return many(sql, (kind, kind, st, st, _LOG_ROWS))


def _log_summary():
    """(per-status counts, per-type counts, per-task rows) — এক query-তেই।"""
    rows = many_cached("tasklogs_sum",
                       "SELECT k, task, status, COUNT(*) AS n FROM (" + _UNION + ") w "
                       "GROUP BY k, task, status")
    cnt = {"pending": 0, "approved": 0, "rejected": 0}
    per = {}
    tasks = {}
    for r in rows:
        k = str(r.get("k") or "")
        stt = str(r.get("status") or "pending").strip().lower()
        n = int(r.get("n") or 0)
        cnt[stt] = cnt.get(stt, 0) + n
        per[k] = per.get(k, 0) + n
        key = (k, str(r.get("task") or "-"))
        t = tasks.setdefault(key, {"total": 0, "pending": 0, "approved": 0, "rejected": 0})
        t["total"] += n
        t[stt] = t.get(stt, 0) + n
    trows = sorted(tasks.items(), key=lambda x: x[1]["total"], reverse=True)[:_LOG_TASKS]
    return cnt, per, trows


_LOG_TABLES = {"gmail": ("gmails", "submitted_at"),
               "fb": ("fb_submissions", "created_at"),
               "ig": ("ig_submissions", "created_at"),
               "tg": ("tg_task_completions", "done_at")}
_LOG_ARCH = {
    "gmail": ("CASE WHEN COALESCE(status,'')='approved' THEN 1 ELSE 0 END",
              "CASE WHEN COALESCE(status,'')='rejected' THEN 1 ELSE 0 END"),
    "fb": ("CASE WHEN COALESCE(status,'')='approved' THEN 1 ELSE 0 END",
           "CASE WHEN COALESCE(status,'')='rejected' THEN 1 ELSE 0 END"),
    "ig": ("CASE WHEN COALESCE(status,'')='approved' THEN 1 ELSE 0 END",
           "CASE WHEN COALESCE(status,'')='rejected' THEN 1 ELSE 0 END"),
    "tg": ("1", "0"),
}


def _archive_selected(k, ids):
    """ডিলিটের *আগে* কাউন্ট archive — লিডারবোর্ড/রেফার/প্রোমোর হিসাব কমবে না।"""
    tbl, tsc = _LOG_TABLES[k]
    day = "LEFT(COALESCE(" + tsc + ",''),10)"
    ok, rj = _LOG_ARCH[k]
    ph = ",".join(["%s"] * len(ids))
    _x("INSERT INTO task_archive_counts (user_id,day,kind,n_approved,n_rejected,updated_at) "
       "SELECT user_id, " + day + ", %s, SUM(" + ok + "), SUM(" + rj + "), %s FROM " + tbl +
       " WHERE id IN (" + ph + ") AND COALESCE(user_id,0) <> 0 "
       "GROUP BY user_id, " + day +
       " HAVING SUM(" + ok + ") + SUM(" + rj + ") > 0 "
       "ON CONFLICT (user_id,day,kind) DO UPDATE SET "
       "n_approved = task_archive_counts.n_approved + EXCLUDED.n_approved, "
       "n_rejected = task_archive_counts.n_rejected + EXCLUDED.n_rejected, "
       "updated_at = EXCLUDED.updated_at", (k, now()) + tuple(ids))


def _tg_notify(text):
    """অ্যাডমিনদের টেলিগ্রাম DM — সরাসরি Bot API (নতুন dependency নেই)।"""
    import json as _json
    import urllib.request as _ur
    tok = (os.environ.get("BOT_TOKEN") or "").strip()
    if not tok:
        return 0
    ids = set()
    try:
        for r in many("SELECT user_id FROM admins"):
            ids.add(int(r["user_id"]))
    except Exception:
        pass
    try:
        import bot as _b          # একই প্রসেসে লোড থাকে — owner id
        ids.add(int(_b.ADMIN_ID))
    except Exception:
        pass
    sent = 0
    for uid in ids:
        try:
            body = _json.dumps({"chat_id": uid, "text": text}).encode("utf-8")
            req = _ur.Request("https://api.telegram.org/bot" + tok + "/sendMessage",
                              data=body, headers={"Content-Type": "application/json"})
            _ur.urlopen(req, timeout=8).read()
            sent += 1
        except Exception:
            continue
    return sent


@wadm.route("/admin/task-logs", methods=["GET"])
def task_logs():
    g = guard()
    if g:
        return g
    kind = (request.args.get("t") or "all").strip().lower()
    if kind not in ("all", "gmail", "fb", "ig", "tg"):
        kind = "all"
    st = (request.args.get("st") or "all").strip().lower()
    if st not in ("all", "pending", "approved", "rejected"):
        st = "all"
    cnt, per, trows = _log_summary()
    rows = _log_rows(kind, st)

    st_map = {"approved": ("ok", "Approved"), "rejected": ("err", "Rejected"),
              "pending": ("warn", "Pending")}
    trs = []
    for r in rows:
        _st = str(r.get("status") or "").lower()
        cls, lbl = st_map.get(_st, ("mut", _st or "-"))
        uid = int(r.get("user_id") or 0)
        ident = str(r.get("ident") or "")
        task_cell = esc(str(r.get("task") or "-"))
        if ident:
            task_cell += ("<br><span class='mono mut' style='font-size:11px'>"
                          + esc(ident[:28]) + "</span>")
        trs.append([
            ("<input type='checkbox' name='sel' value='"
             + str(r.get("k") or "") + ":" + str(int(r.get("id") or 0)) + "'>"),
            _LOG_LABEL.get(str(r.get("k") or ""), str(r.get("k") or "")),
            task_cell,
            "<a class='mono' href='/admin/user/" + str(uid) + "'>#" + str(uid) + "</a>",
            "<span class='mut'>" + esc(str(r.get("ts") or "-")[:16]) + "</span>",
            "<span class='mut'>" + esc(str(r.get("rev") or "-")[:16]) + "</span>",
            badge(lbl, cls),
            money(r.get("amount")),
        ])

    ttrs = []
    for (k, tname), c in trows:
        ttrs.append([
            _LOG_LABEL.get(k, k),
            esc(tname[:40]),
            num(c["total"]),
            num(c.get("pending", 0)),
            num(c.get("approved", 0)),
            num(c.get("rejected", 0)),
        ])

    total_all = sum(per.values()) or sum(cnt.values())
    tab_kind = "".join(
        "<a class='" + ("on" if kind == k else "") + "' href='/admin/task-logs?t=" + k + "&st=" + st + "'>"
        + (_LOG_LABEL.get(k, k) if k != "all" else "📋 All")
        + " (" + num(total_all if k == "all" else per.get(k, 0)) + ")</a>"
        for k in ("all", "gmail", "fb", "ig", "tg"))
    tab_st = "".join(
        "<a class='" + ("on" if st == k else "") + "' href='/admin/task-logs?t=" + kind + "&st=" + k + "'>"
        + lbl + " (" + num(sum(cnt.values()) if k == "all" else cnt.get(k, 0)) + ")</a>"
        for k, lbl in (("all", "All"), ("pending", "Pending"),
                       ("approved", "Approved"), ("rejected", "Rejected")))

    kpis = "".join([
        kpi("Total", num(sum(cnt.values())), "সব টাস্ক মিলিয়ে", "logs", "#7c5cff"),
        kpi("Pending", num(cnt.get("pending", 0)), "রিভিউ বাকি", "clock", "#f59e0b"),
        kpi("Approved", num(cnt.get("approved", 0)), "পেইড", "check", "#22c55e"),
        kpi("Rejected", num(cnt.get("rejected", 0)), "বাতিল", "x", "#ef4444"),
    ])
    js = ("<script>function tlAll(el){var n=document.querySelectorAll(\"input[name='sel']\");"
          "for(var i=0;i<n.length;i++){n[i].checked=el.checked;}}</script>")
    note = ("approve/reject হওয়ার সাথে সাথেই জমার ভেতরের ডেটা (পাসওয়ার্ড, কুকি, 2FA, ইমেইল, ফটো) "
            "মুছে যায় — শুধু এই লগ থাকে: কে, কবে কখন জমা দিল, কখন approve হলো, কোন টাস্ক, "
            "কোনটা approved/rejected। ☑️ দিয়ে বেছে <b>Delete selected</b> চাপলে এই লগও ডিলিট হবে "
            "(একবারে সর্বোচ্চ " + str(_LOG_MAX_DEL) + "টা) — ডেটাবেস ছোট থাকবে।")
    body = (
        "<div class='grid g-kpi'>" + kpis + "</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("trend") + " কোন টাস্ক কত জন করলো</h3>"
        + table(["Type", "Task", "Total", "Pending", "Approved", "Rejected"], ttrs,
                "কোনো টাস্ক ডেটা নেই")
        + "</div>"
        "<div class='card' style='margin-top:14px'><h3>" + icon("logs") + " Task Logs</h3>"
        "<p class='mut' style='font-size:11.5px'>" + note + "</p>"
        "<div class='seg'>" + tab_kind + "</div>"
        "<div class='seg' style='margin-top:8px'>" + tab_st + "</div>"
        + js +
        "<form method='post' action='/admin/task-logs/delete' style='margin-top:10px'>"
        "<label class='chk' style='display:inline-block;margin-bottom:8px'>"
        "<input type='checkbox' onclick='tlAll(this)'> সব সিলেক্ট</label>"
        + table(["", "Type", "Task", "User", "Submitted", "Approved", "Status", "Amount"], trs,
                "কোনো জমার লগ নেই")
        + "<div style='margin-top:10px'>"
          "<button class='err' type='submit'>🗑 Delete selected</button></div>"
          "</form></div>"
    )
    return page("Task Logs", body, "tasklogs")


@wadm.route("/admin/task-logs/delete", methods=["POST"])
def task_logs_delete():
    g = guard()
    if g:
        return g
    sels = request.form.getlist("sel")
    if not sels:
        return redirect("/admin/task-logs?e=কিছু+সিলেক্ট+করেননি")
    sels = sels[:_LOG_MAX_DEL]
    groups = {}
    for it in sels:
        k, _, i = str(it).partition(":")
        if k in _LOG_TBLS and i.isdigit():
            groups.setdefault(k, []).append(int(i))
    deleted = {}
    for k, ids in groups.items():
        tbl = _LOG_TBLS[k]
        ph = ",".join(["%s"] * len(ids))
        try:
            _archive_selected(k, ids)      # কাউন্ট জমা রাখো (হিসাব অটুট)
            n = scal("SELECT COUNT(*) FROM " + tbl + " WHERE id IN (" + ph + ")", tuple(ids))
            _x("DELETE FROM " + tbl + " WHERE id IN (" + ph + ")", tuple(ids))
            deleted[k] = int(n or 0)
        except Exception as _e:
            print("[task-logs] delete " + k + ": " + str(_e))
    total = sum(deleted.values())
    try:
        from webadmin import agg_clear
        agg_clear()
    except Exception:
        pass
    log_admin("task_logs_delete", 0, ", ".join(k + "=" + str(v) for k, v in deleted.items()) or "0")
    if total:
        txt = "🗑 Task Logs ডিলিট\n\n" + "\n".join(
            "• " + _LOG_LABEL.get(k, k) + ": " + str(v) + "টা" for k, v in deleted.items())
        txt += "\n\n✅ মোট " + str(total) + "টা রেকর্ড ডিলিট হয়েছে (প্যানেলও আপডেট হয়েছে)।"
        try:
            _tg_notify(txt)
        except Exception:
            pass
    msg = (str(total) + "+রেকর্ড+ডিলিট+হয়েছে") if total else "কিছু+ডিলিট+হয়নি"
    return redirect("/admin/task-logs?ok=" + msg)


# ── মেনুতে যোগ (Task Statistics এর পরে) ──
try:
    from webadmin import MENU as _MENU_TL
    if not any(isinstance(r, tuple) and len(r) == 5 and r[0] == "tasklogs" for r in _MENU_TL):
        _i = next((i for i, r in enumerate(_MENU_TL)
                   if isinstance(r, tuple) and r[0] == "taskstats"), len(_MENU_TL))
        _MENU_TL.insert(_i + 1, ("tasklogs", "/admin/task-logs", "Task Logs", "logs", ""))
except Exception:
    pass
