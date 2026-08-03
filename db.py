import os
import math
import random
import datetime
import psycopg2
import psycopg2.extras
import psycopg2.pool

DATABASE_URL = os.environ.get("DATABASE_URL")

# به‌جای باز و بسته کردن یه اتصال تازه برای هر عملیات (که خیلی کند بود)،
# یه استخر کوچیک از اتصالات از قبل باز نگه می‌داریم و فقط قرضشون می‌گیریم.
_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)


def get_conn():
    return _pool.getconn()


def put_conn(conn):
    _pool.putconn(conn)


COOLDOWN_SECONDS = 300  # فاصله ثابت بین هر میو: ۵ دقیقه

# سطح‌های ۱ تا ۵: برای جذب بازیکن‌های جدید، فقط ۸ میو لازمه.
# از سطح ۶ به بعد فرمول رشد می‌کنه ولی به EXP_CAP که برسه دیگه بیشتر نمی‌شه؛
# قبلاً این عدد سقف نداشت و مثلاً سطح ۳۰ به تنهایی ۱۷۵ میو لازم داشت.
EARLY_LEVEL_THRESHOLD = 5
EARLY_LEVEL_EXP_NEEDED = 8

EXP_BASE_NEEDED = 15
EXP_STEP_PER_LEVEL = 2
EXP_CAP = 40
MAX_LEVEL = 120

MIN_TRANSFER = 500
MAX_TRANSFER = 10000

# چند روزی که پیام‌های دیده‌شده رو نگه می‌داریم تا بشه رویشون ریپلای زد
SEEN_MESSAGES_RETENTION_DAYS = 3


def get_or_create_user(user_id, username=None):
    # اگه دو دستور هم‌زمان برای یه کاربر تازه اجرا بشن و هر دو بخوان
    # همزمان یه ردیف جدید بسازن، دومیش با خطای تکراری روبه‌رو می‌شه؛
    # به‌جای کرش کردن، همون لحظه دوباره ردیف واقعی (که اولی ساخته) رو
    # می‌خونیم. این نسخه، چه قید یکتا رو دیتابیس فعال باشه چه نباشه،
    # هیچ‌وقت بدتر از قبل عمل نمی‌کنه.
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM meowie_users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        if user:
            cur.close()
            return user

        try:
            cur.execute(
                "INSERT INTO meowie_users (user_id, username) VALUES (%s, %s) RETURNING *",
                (user_id, username),
            )
            user = cur.fetchone()
            conn.commit()
        except psycopg2.Error:
            conn.rollback()
            cur.execute("SELECT * FROM meowie_users WHERE user_id = %s", (user_id,))
            user = cur.fetchone()

        cur.close()
        return user
    finally:
        put_conn(conn)


def get_username(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT username FROM meowie_users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        if row and row["username"]:
            return row["username"]
        return None
    finally:
        put_conn(conn)


def set_username(user_id, username):
    get_or_create_user(user_id, username)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE meowie_users SET username = %s WHERE user_id = %s",
            (username, user_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def exp_needed_for_next_level(level):
    if level >= MAX_LEVEL:
        return None  # دیگه سطح بعدی‌ای وجود نداره
    if level <= EARLY_LEVEL_THRESHOLD:
        return EARLY_LEVEL_EXP_NEEDED
    needed = EXP_BASE_NEEDED + (level - 1 - EARLY_LEVEL_THRESHOLD) * EXP_STEP_PER_LEVEL
    return min(needed, EXP_CAP)


def level_up_bonus_coins(new_level):
    # هر بار که سطح میره بالا، یه پاداش کوچیک و رو به رشد کوین هم می‌گیره
    # تا لول رفتن فقط یه عدد نباشه، حس ملموس‌تری داشته باشه.
    return 200 + new_level * 20


# هر ۲۰ سطح، ۵۰ کوین بیشتر به ازای هر میو اضافه می‌شه (سطح ۲۰-۳۹: +۵۰، ۴۰-۵۹: +۱۰۰ و...)
MEOW_BONUS_PER_TIER = 50
MEOW_BONUS_LEVEL_INTERVAL = 20


def meow_level_bonus(level):
    tier = level // MEOW_BONUS_LEVEL_INTERVAL
    return tier * MEOW_BONUS_PER_TIER


# هر ۱۰ سطح، ۵ ثانیه از کولداون کم می‌شه، ولی هیچ‌وقت از این کمتر نمی‌شه
COOLDOWN_REDUCTION_PER_TIER = 5
COOLDOWN_REDUCTION_LEVEL_INTERVAL = 10
MIN_COOLDOWN_SECONDS = 120


def cooldown_for_level(level):
    tiers = level // COOLDOWN_REDUCTION_LEVEL_INTERVAL
    reduced = COOLDOWN_SECONDS - tiers * COOLDOWN_REDUCTION_PER_TIER
    return max(MIN_COOLDOWN_SECONDS, reduced)


def ensure_extra_columns():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE meowie_users ADD COLUMN IF NOT EXISTS total_meows INTEGER DEFAULT 0"
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def do_meow(user_id, username):
    import random

    user = get_or_create_user(user_id, username)

    now = datetime.datetime.utcnow()
    last = user["last_meow_at"]
    current_cooldown = cooldown_for_level(user["level"])

    if last:
        elapsed = (now - last).total_seconds()
        if elapsed < current_cooldown:
            remaining = round(current_cooldown - elapsed)
            return False, {"reason": "cooldown", "remaining": remaining}

    points_earned = random.randint(1, 100) + meow_level_bonus(user["level"])
    new_exp = user["exp"] + 1
    new_level = user["level"]
    new_total_meows = (user.get("total_meows") or 0) + 1

    leveled_up = False
    levels_gained = 0
    while new_level < MAX_LEVEL:
        needed = exp_needed_for_next_level(new_level)
        if new_exp < needed:
            break
        new_exp -= needed
        new_level += 1
        leveled_up = True
        levels_gained += 1

    if new_level >= MAX_LEVEL:
        new_level = MAX_LEVEL
        new_exp = 0  # سطح ماکسیموم، دیگه پیشرفتی برای نمایش نیست

    bonus_coins = 0
    if leveled_up:
        # اگه چند سطح یهو رد بشه (نادره ولی ممکنه)، برای هر سطح جدا پاداش می‌گیره
        for lvl in range(new_level - levels_gained + 1, new_level + 1):
            bonus_coins += level_up_bonus_coins(lvl)

    conn = get_conn()
    try:
        cur = conn.cursor()
        # نکته‌ی مهم: اینجا points رو با یه مقدار «مطلقِ از قبل محاسبه‌شده»
        # جایگزین نمی‌کنیم (که قبلاً همین‌کارو می‌کرد)، چون بین لحظه‌ی
        # خوندن موجودی (بالای همین تابع) تا لحظه‌ی نوشتن، ممکنه یه انتقال،
        # شارژ ادمین، یا برد کازینو موجودی رو عوض کرده باشه — و نوشتن یه
        # مقدار مطلق قدیمی، اون تغییر رو کامل پاک می‌کنه (دقیقاً همون باگی
        # که باعث می‌شد دارایی بعضی کاربرا یهو صفر/کم بشه). به‌جاش فقط
        # مقدار افزایش رو اضافه می‌کنیم که با هر عملیات دیگه‌ای سازگاره.
        points_delta = points_earned + bonus_coins
        cur.execute(
            """
            UPDATE meowie_users
            SET points = points + %s, exp = %s, level = %s, last_meow_at = %s, total_meows = %s
            WHERE user_id = %s
            RETURNING points
            """,
            (points_delta, new_exp, new_level, now, new_total_meows, user_id),
        )
        final_points = cur.fetchone()[0]
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)

    return True, {
        "points_earned": points_earned,
        "total_points": final_points,
        "level": new_level,
        "leveled_up": leveled_up,
        "bonus_coins": bonus_coins,
        "exp": new_exp,
        "exp_needed": exp_needed_for_next_level(new_level),
        "cooldown_seconds": cooldown_for_level(new_level),
    }


def get_leaderboard_global(order_by="points", limit=10):
    column = "points" if order_by == "points" else "total_meows"
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""
            SELECT username, points, level, total_meows
            FROM meowie_users
            WHERE username IS NOT NULL
            ORDER BY {column} DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        put_conn(conn)


def ensure_group_members_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS group_members (
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def record_group_membership(chat_id, user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO group_members (chat_id, user_id) VALUES (%s, %s)
            ON CONFLICT (chat_id, user_id) DO NOTHING
            """,
            (chat_id, user_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_all_group_chat_ids():
    """
    لیست همه‌ی chat_idهایی که بات حداقل یه پیام توشون دیده (برای پیام
    خودکار دوره‌ای به همه‌ی گروه‌ها).
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT chat_id FROM group_members")
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]
    finally:
        put_conn(conn)


def record_message_context(chat_id, sender_id, message_id):
    """
    نسخه‌ی ترکیبی record_group_membership + record_seen_message که تو یه
    اتصال دیتابیس (نه دوتا) انجامش می‌ده تا هر پیام یه رفت‌وبرگشت شبکه‌ی
    کمتر با دیتابیس داشته باشه و ربات سریع‌تر جواب بده.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO group_members (chat_id, user_id) VALUES (%s, %s)
            ON CONFLICT (chat_id, user_id) DO NOTHING
            """,
            (chat_id, sender_id),
        )
        if message_id:
            cur.execute(
                """
                INSERT INTO seen_messages (message_id, chat_id, sender_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (message_id) DO NOTHING
                """,
                (str(message_id), str(chat_id), str(sender_id)),
            )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_leaderboard_group(chat_id, order_by="points", limit=10):
    column = "u.points" if order_by == "points" else "u.total_meows"
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""
            SELECT u.username, u.points, u.level, u.total_meows
            FROM meowie_users u
            JOIN group_members gm ON gm.user_id = u.user_id
            WHERE gm.chat_id = %s AND u.username IS NOT NULL
            ORDER BY {column} DESC
            LIMIT %s
            """,
            (chat_id, limit),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        put_conn(conn)


def get_rank_group(chat_id, user_id, order_by="points"):
    column = "u.points" if order_by == "points" else "u.total_meows"
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT rank FROM (
                SELECT u.user_id, RANK() OVER (ORDER BY {column} DESC) as rank
                FROM meowie_users u
                JOIN group_members gm ON gm.user_id = u.user_id
                WHERE gm.chat_id = %s AND u.username IS NOT NULL
            ) ranked WHERE user_id = %s
            """,
            (chat_id, user_id),
        )
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    finally:
        put_conn(conn)


def get_rank_global(user_id, order_by="points"):
    column = "points" if order_by == "points" else "total_meows"
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT rank FROM (
                SELECT user_id, RANK() OVER (ORDER BY {column} DESC) as rank
                FROM meowie_users WHERE username IS NOT NULL
            ) ranked WHERE user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    finally:
        put_conn(conn)


def get_profile(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM meowie_users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        cur.close()
        return user
    finally:
        put_conn(conn)


def get_points(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT points FROM meowie_users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    finally:
        put_conn(conn)


def transfer_points(sender_id, receiver_id, amount):
    """
    انتقال میوپوینت از sender_id به receiver_id.
    قبل از صدا زدن این تابع باید مطمئن بشی هر دو کاربر تو دیتابیس وجود دارن
    (با get_or_create_user).
    """
    if str(sender_id) == str(receiver_id):
        return False, {"reason": "self"}
    if amount < MIN_TRANSFER:
        return False, {"reason": "below_min", "min": MIN_TRANSFER}
    if amount > MAX_TRANSFER:
        return False, {"reason": "above_max", "max": MAX_TRANSFER}

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # هر دو ردیف رو با ترتیب ثابت قفل می‌کنیم تا دو انتقال همزمان دِدلاک نشن
        ids_sorted = sorted([str(sender_id), str(receiver_id)])
        cur.execute(
            "SELECT user_id, points FROM meowie_users WHERE user_id = ANY(%s) FOR UPDATE",
            (ids_sorted,),
        )
        rows = {r["user_id"]: r["points"] for r in cur.fetchall()}

        if str(sender_id) not in rows:
            conn.rollback()
            cur.close()
            return False, {"reason": "sender_not_found"}
        if str(receiver_id) not in rows:
            conn.rollback()
            cur.close()
            return False, {"reason": "receiver_not_found"}

        sender_points = rows[str(sender_id)]
        if sender_points < amount:
            conn.rollback()
            cur.close()
            return False, {"reason": "insufficient", "have": sender_points}

        new_sender_points = sender_points - amount
        new_receiver_points = rows[str(receiver_id)] + amount

        cur.execute(
            "UPDATE meowie_users SET points = %s WHERE user_id = %s",
            (new_sender_points, sender_id),
        )
        cur.execute(
            "UPDATE meowie_users SET points = %s WHERE user_id = %s",
            (new_receiver_points, receiver_id),
        )
        conn.commit()
        cur.close()
        return True, {
            "sender_new_points": new_sender_points,
            "receiver_new_points": new_receiver_points,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def ensure_offset_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY,
                last_offset_id TEXT
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_offset():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT last_offset_id FROM bot_state WHERE id = 1")
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    finally:
        put_conn(conn)


def set_offset(offset_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bot_state (id, last_offset_id) VALUES (1, %s)
            ON CONFLICT (id) DO UPDATE SET last_offset_id = EXCLUDED.last_offset_id
            """,
            (offset_id,),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def ensure_broadcast_column():
    """
    یه ستون رو جدول bot_state اضافه می‌کنه که زمان آخرین پیام دوره‌ای رو
    نگه می‌داره. چون تو دیتابیسه (نه حافظه‌ی برنامه)، با ری‌استارت سرویس
    (دیپلوی جدید، خواب رفتن Render و بیدار شدن) گم نمی‌شه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS last_broadcast_at TIMESTAMP")
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_last_broadcast_time():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT last_broadcast_at FROM bot_state WHERE id = 1")
        row = cur.fetchone()
        cur.close()
        return row[0] if row and row[0] else None
    finally:
        put_conn(conn)


def set_last_broadcast_time():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bot_state (id, last_broadcast_at) VALUES (1, NOW())
            ON CONFLICT (id) DO UPDATE SET last_broadcast_at = EXCLUDED.last_broadcast_at
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def reset_user_points(user_id):
    """موجودی کاربر رو صفر می‌کنه (برای دستور ادمینِ «ریست کوین»)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE meowie_users SET points = 0 WHERE user_id = %s", (user_id,))
        updated = cur.rowcount > 0
        conn.commit()
        cur.close()
        return updated
    finally:
        put_conn(conn)


def ensure_seen_messages_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_messages (
                message_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                seen_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def record_seen_message(message_id, chat_id, sender_id):
    if not message_id:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO seen_messages (message_id, chat_id, sender_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (message_id) DO NOTHING
            """,
            (str(message_id), str(chat_id), str(sender_id)),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_sender_of_message(message_id):
    if not message_id:
        return None
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT sender_id FROM seen_messages WHERE message_id = %s",
            (str(message_id),),
        )
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    finally:
        put_conn(conn)


def cleanup_old_seen_messages(days=SEEN_MESSAGES_RETENTION_DAYS):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM seen_messages WHERE seen_at < NOW() - (%s || ' days')::interval",
            (days,),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def add_points(user_id, amount):
    """
    مستقیم به موجودی یه کاربر اضافه می‌کنه (بدون کم کردن از کسی).
    فقط باید از یه مسیر ادمین‌محور صدا زده بشه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "UPDATE meowie_users SET points = points + %s WHERE user_id = %s RETURNING points",
            (amount, user_id),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return row["points"] if row else None
    finally:
        put_conn(conn)


def ensure_admin_actions_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_actions (
                id SERIAL PRIMARY KEY,
                admin_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def record_admin_action(admin_id, target_id, amount, action_type):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO admin_actions (admin_id, target_id, amount, action_type)
            VALUES (%s, %s, %s, %s)
            """,
            (str(admin_id), str(target_id), amount, action_type),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# سیستم پیشی (Cats)
# ---------------------------------------------------------------------------

CAT_PRICE = 500
CAT_MIN_LEVEL = 2

CAT_MAX_RANK = 20
CAT_BASE_RANK_CAP = 10       # سقف لولِ رتبه‌ی ۱
CAT_RANK_CAP_STEP = 5        # هر رتبه، ۵ لول بیشتر از رتبه‌ی قبل نیاز داره

CAT_PRODUCTION_PER_HOUR_PER_POWER = 40   # تولید در ساعت به ازای هر واحد «پاور لول»
CAT_CAPACITY_HOURS = 5                   # صندوقچه حداکثر معادل ۵ ساعت تولید جا داره

# قبلاً هزینه‌ی ارتقا همیشه دقیقاً ۵ ساعت تولید بود (چه رتبه ۱ چه رتبه ۲۰)،
# یعنی هیچ‌وقت نه سریع‌تر می‌شد نه سخت‌تر. حالا رتبه‌های اول ارزون‌ترن (۳ ساعت،
# برای جذب سریع کاربر تازه‌وارد) و هرچی رتبه بالاتر می‌ره کم‌کم گرون‌تر می‌شه
# تا سقف ۶ ساعت (دیگه از اون گرون‌تر نمی‌شه).
CAT_UPGRADE_COST_MULTIPLIER_MIN = 3
CAT_UPGRADE_COST_MULTIPLIER_MAX = 6
CAT_UPGRADE_COST_GROWTH_PER_RANK = 0.3

# هر رتبه از شرکت، به یه سطح حساب مشخص هم نیاز داره (نه فقط پول)؛
# این‌جوری صرفاً صبر کردن و پول جمع کردن کافی نیست، باید واقعاً میو هم زده باشه.
CAT_LEVEL_REQUIREMENT_PER_RANK = 2


def cat_rank_cap(rank):
    """سقف لول یه رتبه‌ی مشخص (چند لول باید بگیره تا بره رتبه‌ی بعد)."""
    return CAT_BASE_RANK_CAP + CAT_RANK_CAP_STEP * (rank - 1)


def cat_rank_offset(rank):
    """جمع سقفِ لول همه‌ی رتبه‌های قبل از این رتبه."""
    total = 0
    for r in range(1, rank):
        total += cat_rank_cap(r)
    return total


def cat_power_level(rank, level):
    """یه عدد پیوسته که هرچی پیشی قوی‌تر میشه (چه با لول‌گرفتن چه با ارتقای رتبه) بزرگ‌تر میشه."""
    return cat_rank_offset(rank) + level


def cat_production_per_hour(rank, level):
    power = cat_power_level(rank, level)
    return max(1, power * CAT_PRODUCTION_PER_HOUR_PER_POWER)


def cat_capacity(rank, level):
    return cat_production_per_hour(rank, level) * CAT_CAPACITY_HOURS


def cat_upgrade_cost_multiplier(rank):
    growth = (rank - 1) * CAT_UPGRADE_COST_GROWTH_PER_RANK
    return min(CAT_UPGRADE_COST_MULTIPLIER_MIN + growth, CAT_UPGRADE_COST_MULTIPLIER_MAX)


def cat_upgrade_cost(rank, level):
    per_hour = cat_production_per_hour(rank, level)
    return round(per_hour * cat_upgrade_cost_multiplier(rank))


def cat_required_account_level(rank):
    return rank * CAT_LEVEL_REQUIREMENT_PER_RANK


def cat_is_maxed(rank, level):
    return rank >= CAT_MAX_RANK and level >= cat_rank_cap(CAT_MAX_RANK)


def ensure_cats_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cats (
                owner_id TEXT PRIMARY KEY,
                name TEXT,
                rank INTEGER NOT NULL DEFAULT 1,
                level INTEGER NOT NULL DEFAULT 1,
                total_collected BIGINT NOT NULL DEFAULT 0,
                last_collect_at TIMESTAMP NOT NULL DEFAULT NOW(),
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_cat(owner_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM cats WHERE owner_id = %s", (owner_id,))
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        put_conn(conn)


def buy_cat(owner_id, amount=CAT_PRICE, name=None):
    """
    خرید پیشی: هزینه رو از موجودی کاربر کم می‌کنه و یه ردیف تو cats می‌سازه.
    هر کاربر فقط یه پیشی می‌تونه داشته باشه (owner_id کلید اصلیه).
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT 1 FROM cats WHERE owner_id = %s", (owner_id,))
        if cur.fetchone():
            cur.close()
            return False, {"reason": "already_has_cat"}

        cur.execute(
            "UPDATE meowie_users SET points = points - %s WHERE user_id = %s AND points >= %s RETURNING points",
            (amount, owner_id, amount),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return False, {"reason": "insufficient"}

        cur.execute(
            """
            INSERT INTO cats (owner_id, name, rank, level, last_collect_at)
            VALUES (%s, %s, 1, 1, NOW())
            """,
            (owner_id, name),
        )
        conn.commit()
        cur.close()
        return True, {"remaining_points": row["points"]}
    finally:
        put_conn(conn)


def collect_cat_points(owner_id):
    """
    میوپوینت‌های جمع‌شده تو صندوقچه‌ی پیشی رو به موجودی کاربر اضافه می‌کنه
    و صندوقچه رو صفر می‌کنه (تولید از الان دوباره شروع می‌شه).
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM cats WHERE owner_id = %s FOR UPDATE", (owner_id,))
        cat = cur.fetchone()
        if not cat:
            cur.close()
            return False, {"reason": "no_cat"}

        now = datetime.datetime.utcnow()
        elapsed_hours = (now - cat["last_collect_at"]).total_seconds() / 3600
        per_hour = cat_production_per_hour(cat["rank"], cat["level"])
        capacity = cat_capacity(cat["rank"], cat["level"])
        pending = min(capacity, per_hour * elapsed_hours)
        pending = int(pending)

        if pending <= 0:
            cur.close()
            return False, {"reason": "nothing_to_collect"}

        cur.execute(
            "UPDATE meowie_users SET points = points + %s WHERE user_id = %s RETURNING points",
            (pending, owner_id),
        )
        user_row = cur.fetchone()

        cur.execute(
            """
            UPDATE cats
            SET total_collected = total_collected + %s, last_collect_at = %s
            WHERE owner_id = %s
            """,
            (pending, now, owner_id),
        )
        conn.commit()
        cur.close()
        return True, {"collected": pending, "new_points": user_row["points"]}
    finally:
        put_conn(conn)


def upgrade_cat(owner_id):
    """
    یه لول به پیشی اضافه می‌کنه (هزینه‌ش از موجودی کاربر کم می‌شه).
    اگه به سقف لول رتبه‌ی فعلی برسه، رتبه +۱ می‌شه و لول از ۱ همون
    رتبه‌ی جدید از نو شروع می‌شه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM cats WHERE owner_id = %s FOR UPDATE", (owner_id,))
        cat = cur.fetchone()
        if not cat:
            cur.close()
            return False, {"reason": "no_cat"}

        rank = cat["rank"]
        level = cat["level"]

        if cat_is_maxed(rank, level):
            cur.close()
            return False, {"reason": "maxed"}

        would_rank_up = (level + 1) > cat_rank_cap(rank) and rank < CAT_MAX_RANK
        if would_rank_up:
            required_level = cat_required_account_level(rank + 1)
            cur.execute("SELECT level FROM meowie_users WHERE user_id = %s", (owner_id,))
            account_row = cur.fetchone()
            account_level = account_row["level"] if account_row else 0
            if account_level < required_level:
                cur.close()
                return False, {
                    "reason": "level_too_low",
                    "required_level": required_level,
                    "account_level": account_level,
                }

        cost = cat_upgrade_cost(rank, level)

        cur.execute(
            "UPDATE meowie_users SET points = points - %s WHERE user_id = %s AND points >= %s RETURNING points",
            (cost, owner_id, cost),
        )
        user_row = cur.fetchone()
        if not user_row:
            conn.rollback()
            cur.close()
            return False, {"reason": "insufficient", "cost": cost}

        new_level = level + 1
        rank_up = False
        if new_level > cat_rank_cap(rank) and rank < CAT_MAX_RANK:
            rank += 1
            new_level = 1
            rank_up = True

        cur.execute(
            "UPDATE cats SET rank = %s, level = %s WHERE owner_id = %s",
            (rank, new_level, owner_id),
        )
        conn.commit()
        cur.close()
        return True, {
            "cost": cost,
            "new_rank": rank,
            "new_level": new_level,
            "rank_up": rank_up,
            "remaining_points": user_row["points"],
        }
    finally:
        put_conn(conn)


def set_cat_name(owner_id, name):
    """اسم پیشی رو تنظیم می‌کنه. اگه اصلاً پیشی نداشت، False برمی‌گردونه."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE cats SET name = %s WHERE owner_id = %s", (name, owner_id))
        updated = cur.rowcount > 0
        conn.commit()
        cur.close()
        return updated
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# کازینو میویی
# ---------------------------------------------------------------------------

CASINO_MIN_BET = 100
CASINO_MAX_BET = 20000

COINFLIP_PAYOUT_MULTIPLIER = 1.9   # ~۵٪ مزیت بانک
DICE_PAYOUT_MULTIPLIER = 5.7       # ~۵٪ مزیت بانک (شانس واقعی ۱ از ۶ = ۶ برابر منصفانه)
HIGHLOW_PAYOUT_MULTIPLIER = 1.9    # ~۵٪ مزیت بانک


def _resolve_bet(user_id, bet, winnings):
    """
    مبلغ شرط رو از موجودی کم می‌کنه (اتمیک، اگه کافی نباشه None برمی‌گردونه).
    اگه برد بود (winnings > 0)، همون مبلغ برد رو اضافه می‌کنه.
    موجودی نهایی کاربر رو برمی‌گردونه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "UPDATE meowie_users SET points = points - %s WHERE user_id = %s AND points >= %s RETURNING points",
            (bet, user_id, bet),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return None

        new_points = row["points"]
        if winnings > 0:
            cur.execute(
                "UPDATE meowie_users SET points = points + %s WHERE user_id = %s RETURNING points",
                (winnings, user_id),
            )
            row2 = cur.fetchone()
            new_points = row2["points"]

        conn.commit()
        cur.close()
        return new_points
    finally:
        put_conn(conn)


def _check_bet_range(bet):
    if bet < CASINO_MIN_BET:
        return {"reason": "below_min", "min": CASINO_MIN_BET}
    if bet > CASINO_MAX_BET:
        return {"reason": "above_max", "max": CASINO_MAX_BET}
    return None


def coin_flip(user_id, bet, choice):
    """choice باید 'شیر' یا 'خط' باشه."""
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error
    if choice not in ("شیر", "خط"):
        return False, {"reason": "invalid_choice"}

    result = random.choice(["شیر", "خط"])
    won = result == choice
    winnings = int(bet * COINFLIP_PAYOUT_MULTIPLIER) if won else 0

    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    return True, {
        "result": result,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


def dice_roll(user_id, bet, guess):
    """guess باید عددی بین ۱ تا ۶ باشه."""
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error
    if guess not in range(1, 7):
        return False, {"reason": "invalid_guess"}

    result = random.randint(1, 6)
    won = result == guess
    winnings = int(bet * DICE_PAYOUT_MULTIPLIER) if won else 0

    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    return True, {
        "result": result,
        "guess": guess,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


def highlow_play(user_id, bet, direction):
    """direction باید 'بالا' یا 'پایین' باشه."""
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error
    if direction not in ("بالا", "پایین"):
        return False, {"reason": "invalid_direction"}

    current = random.randint(1, 100)
    next_number = random.randint(1, 100)

    if next_number == current:
        won = False  # مساوی به نفع بانکه
    elif direction == "بالا":
        won = next_number > current
    else:
        won = next_number < current

    winnings = int(bet * HIGHLOW_PAYOUT_MULTIPLIER) if won else 0

    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    return True, {
        "current": current,
        "next": next_number,
        "direction": direction,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


# ---------------------------------------------------------------------------
# زیرساخت مشترک بازی‌های چندمرحله‌ای (بلک‌جک، کرش، مین‌یاب)
# هر کاربر فقط یه دور فعال می‌تونه داشته باشه. هر دور یه مهلت ۶۵ ثانیه‌ای
# داره؛ اگه کاربر تو این مدت جواب نده، دور خودکار به ضرر خودش بسته می‌شه
# (bet از قبل کم شده، پس فقط ردیف پاک می‌شه و پیام باخت فرستاده می‌شه).
# این باعث می‌شه هیچ ردیف نیمه‌کاره‌ای برای همیشه تو دیتابیس نمونه.
# ---------------------------------------------------------------------------

GAME_TIMEOUT_SECONDS = 65


def ensure_active_games_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS active_games (
                user_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                game_type TEXT NOT NULL,
                bet INTEGER NOT NULL,
                state JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_active_game(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM active_games WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        put_conn(conn)


def start_active_game(user_id, chat_id, game_type, bet, state, timeout_seconds=None):
    if timeout_seconds is None:
        timeout_seconds = GAME_TIMEOUT_SECONDS
    timeout_seconds = max(1, round(timeout_seconds))
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO active_games (user_id, chat_id, game_type, bet, state, expires_at)
            VALUES (%s, %s, %s, %s, %s, NOW() + (%s || ' seconds')::interval)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, chat_id, game_type, bet, psycopg2.extras.Json(state), timeout_seconds),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def update_active_game_state(user_id, state):
    """ذخیره‌ی وضعیت جدید بازی + تمدید مهلت به ۶۵ ثانیه‌ی دیگه از همین لحظه."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE active_games
            SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval
            WHERE user_id = %s
            """,
            (psycopg2.extras.Json(state), GAME_TIMEOUT_SECONDS, user_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def end_active_game(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_expired_games():
    """دورهایی که مهلت ۶۵ ثانیه‌شون گذشته و کاربر جواب نداده."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM active_games WHERE expires_at < NOW()")
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        put_conn(conn)


def _deduct_bet(user_id, bet):
    """فقط کم کردن شرط، بدون تسویه‌ی برد (برای بازی‌های چندمرحله‌ای)."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "UPDATE meowie_users SET points = points - %s WHERE user_id = %s AND points >= %s RETURNING points",
            (bet, user_id, bet),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return None
        conn.commit()
        cur.close()
        return row["points"]
    finally:
        put_conn(conn)


def _credit(user_id, amount):
    """فقط اضافه کردن جایزه (برای بازی‌های چندمرحله‌ای)."""
    if amount <= 0:
        return get_points(user_id)
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "UPDATE meowie_users SET points = points + %s WHERE user_id = %s RETURNING points",
            (amount, user_id),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return row["points"]
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# 🎰 اسلات
# ---------------------------------------------------------------------------

SLOT_SYMBOL_POOL = ["🍒", "🍋", "🍇", "7️⃣", "💎"]

# (اسم، احتمال، ضریب جایزه) — جمع احتمال‌ها با احتمال باخت باید ۱ بشه
SLOT_OUTCOMES = [
    ("diamond", 0.005, 20, ("💎", "💎", "💎")),
    ("seven", 0.01, 10, ("7️⃣", "7️⃣", "7️⃣")),
    ("grape", 0.03, 5, ("🍇", "🍇", "🍇")),
    ("lemon", 0.05, 3, ("🍋", "🍋", "🍋")),
    ("cherry", 0.08, 2, ("🍒", "🍒", "🍒")),
    ("pair", 0.15, 1, None),
]


def _random_pair_reel():
    symbol = random.choice(SLOT_SYMBOL_POOL)
    others = [s for s in SLOT_SYMBOL_POOL if s != symbol]
    third = random.choice(others)
    positions = [symbol, symbol, third]
    random.shuffle(positions)
    return tuple(positions)


def _random_losing_reel():
    while True:
        reel = tuple(random.choice(SLOT_SYMBOL_POOL) for _ in range(3))
        if len(set(reel)) > 1:  # مطمئن می‌شیم سه‌تا یکسان (که جداگونه مدیریت می‌شه) در نیاد
            # و مطمئن می‌شیم دوتاش هم یکسان نباشه (چون اون حالت «pair» جدا حساب شده)
            if len(set(reel)) == 3:
                return reel


def slot_spin(user_id, bet):
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error

    roll = random.random()
    cumulative = 0.0
    multiplier = 0
    reel = None
    for name, prob, mult, fixed_reel in SLOT_OUTCOMES:
        cumulative += prob
        if roll <= cumulative:
            multiplier = mult
            reel = _random_pair_reel() if name == "pair" else fixed_reel
            break

    if reel is None:
        multiplier = 0
        reel = _random_losing_reel()

    winnings = int(bet * multiplier) if multiplier > 0 else 0
    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    return True, {
        "reel": reel,
        "multiplier": multiplier,
        "won": multiplier > 0,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


# ---------------------------------------------------------------------------
# 🎡 رولت مینی
# ---------------------------------------------------------------------------

ROULETTE_RED_NUMBERS = {
    1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
}


def roulette_spin(user_id, bet, bet_type, bet_value):
    """
    bet_type: 'color' یا 'number'
    bet_value: 'قرمز'/'مشکی' برای color، یا عدد ۰ تا ۳۶ برای number
    """
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error

    result = random.randint(0, 36)
    if result == 0:
        color = "سبز"
    elif result in ROULETTE_RED_NUMBERS:
        color = "قرمز"
    else:
        color = "مشکی"

    won = False
    multiplier = 0
    if bet_type == "color" and bet_value in ("قرمز", "مشکی"):
        if bet_value == color:
            won = True
            multiplier = 2
    elif bet_type == "number":
        if bet_value == result:
            won = True
            multiplier = 35

    winnings = int(bet * multiplier) if won else 0
    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    return True, {
        "result": result,
        "color": color,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


# ---------------------------------------------------------------------------
# 🎯 دارت (جایگزین کرش — چون یه پرتاب آنیه، هیچ وابستگی‌ای به گذر زمان نداره
# و مشکل تأخیر شبکه‌ای که تو کرش پیش میومد رو کلاً نداره)
# ---------------------------------------------------------------------------

DART_OUTCOMES = [
    ("bullseye", 0.05, 7),
    ("red", 0.15, 2),
    ("blue", 0.30, 1),
]


def dart_throw(user_id, bet):
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error

    roll = random.random()
    cumulative = 0.0
    multiplier = 0
    zone = "miss"
    for name, prob, mult in DART_OUTCOMES:
        cumulative += prob
        if roll <= cumulative:
            multiplier = mult
            zone = name
            break

    if multiplier > 1:
        status = "win"
    elif multiplier == 1:
        status = "push"
    else:
        status = "loss"

    winnings = int(bet * multiplier) if multiplier > 0 else 0
    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    return True, {
        "zone": zone,
        "multiplier": multiplier,
        "status": status,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


# ---------------------------------------------------------------------------
# 🃏 بلک‌جک (چندمرحله‌ای، از زیرساخت active_games استفاده می‌کنه)
# قانون: مساوی (Push) به نفع خونه‌ست — این همون مزیت بانکیه که برای این بازی انتخاب کردیم.
# بلک‌جک طبیعی (۲۱ با دو کارت) ۲.۵ برابر می‌ده (یعنی ۱.۵ برابر سود + خود شرط).
# ---------------------------------------------------------------------------

CARD_RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def _draw_card():
    return random.choice(CARD_RANKS)


def _card_value(rank):
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def _hand_value(cards):
    total = sum(_card_value(c) for c in cards)
    aces = cards.count("A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def start_blackjack(user_id, chat_id, bet):
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error
    if get_active_game(user_id):
        return False, {"reason": "already_in_game"}

    new_points = _deduct_bet(user_id, bet)
    if new_points is None:
        return False, {"reason": "insufficient"}

    player = [_draw_card(), _draw_card()]
    dealer = [_draw_card(), _draw_card()]
    state = {"player": player, "dealer": dealer}

    player_value = _hand_value(player)
    if player_value == 21:
        # بلک‌جک طبیعی -> بازی همون لحظه تموم می‌شه، نیازی به active_games نیست
        dealer_value = _hand_value(dealer)
        if dealer_value == 21:
            return True, {
                "status": "push_loss",
                "player": player,
                "dealer": dealer,
                "bet": bet,
                "winnings": 0,
                "new_points": new_points,
            }
        winnings = int(bet * 2.5)
        final_points = _credit(user_id, winnings)
        return True, {
            "status": "natural_win",
            "player": player,
            "dealer": dealer,
            "bet": bet,
            "winnings": winnings,
            "new_points": final_points,
        }

    start_active_game(user_id, chat_id, "blackjack", bet, state)
    return True, {
        "status": "in_progress",
        "player": player,
        "dealer_shown": [dealer[0]],
        "bet": bet,
        "new_points": new_points,
    }


def blackjack_hit(user_id):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "blackjack":
        return False, {"reason": "no_game"}

    state = game["state"]
    state["player"].append(_draw_card())
    player_value = _hand_value(state["player"])

    if player_value > 21:
        end_active_game(user_id)
        return True, {
            "status": "bust",
            "player": state["player"],
            "dealer": state["dealer"],
            "bet": game["bet"],
            "winnings": 0,
            "new_points": get_points(user_id),
        }

    update_active_game_state(user_id, state)
    return True, {
        "status": "in_progress",
        "player": state["player"],
        "dealer_shown": [state["dealer"][0]],
        "bet": game["bet"],
        "new_points": get_points(user_id),
    }


def blackjack_stand(user_id):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "blackjack":
        return False, {"reason": "no_game"}

    state = game["state"]
    dealer = state["dealer"]
    while _hand_value(dealer) < 17:
        dealer.append(_draw_card())

    player_value = _hand_value(state["player"])
    dealer_value = _hand_value(dealer)
    bet = game["bet"]

    if dealer_value > 21 or player_value > dealer_value:
        status = "win"
        winnings = bet * 2
    elif player_value == dealer_value:
        status = "push_loss"  # مساوی به نفع خونه
        winnings = 0
    else:
        status = "loss"
        winnings = 0

    end_active_game(user_id)
    new_points = _credit(user_id, winnings) if winnings > 0 else get_points(user_id)

    return True, {
        "status": status,
        "player": state["player"],
        "dealer": dealer,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


# ---------------------------------------------------------------------------
# 💥 کرش (چندمرحله‌ای)
# نقطه‌ی ترکیدن با یه فرمول ریاضی استاندارد (همون که بازی‌های واقعی کرش
# استفاده می‌کنن) حساب می‌شه که خودش تضمین می‌کنه مزیت بانک دقیقاً ۵٪ باشه،
# فارغ از اینکه کاربر کِی برداشت کنه.
# ضریب با گذر زمان (نه با پیام) بالا می‌ره، پس «مهلت» این بازی به‌جای ۶۵
# ثانیه‌ی ثابت، دقیقاً همون لحظه‌ی ترکیدنشه.
# ---------------------------------------------------------------------------

CRASH_HOUSE_EDGE = 0.05
CRASH_GROWTH_RATE = 0.15   # افزایش ضریب به ازای هر ثانیه
CRASH_MAX_MULTIPLIER = 10.0


def _generate_crash_point():
    u = random.random()
    if u <= 0:
        u = 1e-9
    point = (1 - CRASH_HOUSE_EDGE) / u
    point = max(1.0, min(point, CRASH_MAX_MULTIPLIER))
    return round(point, 2)


def _crash_time_to_point(crash_point):
    return (crash_point - 1) / CRASH_GROWTH_RATE


def start_crash(user_id, chat_id, bet):
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error
    if get_active_game(user_id):
        return False, {"reason": "already_in_game"}

    new_points = _deduct_bet(user_id, bet)
    if new_points is None:
        return False, {"reason": "insufficient"}

    crash_point = _generate_crash_point()
    now = datetime.datetime.utcnow()
    state = {"crash_point": crash_point, "start_time": now.isoformat()}
    t_crash = _crash_time_to_point(crash_point)
    start_active_game(user_id, chat_id, "crash", bet, state, timeout_seconds=t_crash)

    return True, {"bet": bet, "new_points": new_points}


def get_crash_status(user_id):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "crash":
        return False, {"reason": "no_game"}

    state = game["state"]
    start_time = datetime.datetime.fromisoformat(state["start_time"])
    elapsed = (datetime.datetime.utcnow() - start_time).total_seconds()
    crash_point = state["crash_point"]
    t_crash = _crash_time_to_point(crash_point)

    if elapsed >= t_crash:
        return True, {"multiplier": crash_point, "crashed": True, "bet": game["bet"]}

    current_multiplier = round(1 + CRASH_GROWTH_RATE * elapsed, 2)
    return True, {"multiplier": current_multiplier, "crashed": False, "bet": game["bet"]}


def crash_cashout(user_id):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "crash":
        return False, {"reason": "no_game"}

    state = game["state"]
    start_time = datetime.datetime.fromisoformat(state["start_time"])
    elapsed = (datetime.datetime.utcnow() - start_time).total_seconds()
    crash_point = state["crash_point"]
    t_crash = _crash_time_to_point(crash_point)
    bet = game["bet"]

    if elapsed >= t_crash:
        end_active_game(user_id)
        return True, {
            "status": "too_late",
            "multiplier": crash_point,
            "bet": bet,
            "winnings": 0,
            "new_points": get_points(user_id),
        }

    current_multiplier = round(1 + CRASH_GROWTH_RATE * elapsed, 2)
    winnings = int(bet * current_multiplier)
    end_active_game(user_id)
    new_points = _credit(user_id, winnings)

    return True, {
        "status": "cashed_out",
        "multiplier": current_multiplier,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
    }


# ---------------------------------------------------------------------------
# 💣 مین‌یاب (چندمرحله‌ای)
# ضریب هر مرحله از روی احتمال واقعیِ زنده موندن (فرمول ترکیبیاتی) حساب
# می‌شه، بعد ۵٪ ازش کم می‌کنیم تا مزیت بانک با بقیه‌ی بازی‌ها یکی باشه.
# ---------------------------------------------------------------------------

MINES_TOTAL_CELLS = 25
MINES_HOUSE_EDGE = 0.05
MINES_MIN_COUNT = 1
MINES_MAX_COUNT = 10


def _mines_multiplier(total_cells, mine_count, opened_count):
    if opened_count == 0:
        return 1.0
    safe_cells = total_cells - mine_count
    fair = math.comb(total_cells, opened_count) / math.comb(safe_cells, opened_count)
    return round(fair * (1 - MINES_HOUSE_EDGE), 2)


def start_mines(user_id, chat_id, bet, mine_count):
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error
    if mine_count < MINES_MIN_COUNT or mine_count > MINES_MAX_COUNT:
        return False, {"reason": "invalid_mine_count", "min": MINES_MIN_COUNT, "max": MINES_MAX_COUNT}
    if get_active_game(user_id):
        return False, {"reason": "already_in_game"}

    new_points = _deduct_bet(user_id, bet)
    if new_points is None:
        return False, {"reason": "insufficient"}

    mine_positions = random.sample(range(MINES_TOTAL_CELLS), mine_count)
    state = {"mine_positions": mine_positions, "opened": [], "mine_count": mine_count}
    start_active_game(user_id, chat_id, "mines", bet, state)

    return True, {"bet": bet, "mine_count": mine_count, "new_points": new_points}


def mines_open(user_id, cell_index):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # قفل کردن ردیف بازی تا آخر تراکنش: اگه یه «باز کن» دیگه هم‌زمان
        # برسه، اینجا منتظر می‌مونه تا این یکی کامل تموم بشه و بعد با
        # وضعیت واقعی و به‌روز کار کنه (نه یه نسخه‌ی قدیمی و ازدست‌رفته).
        cur.execute(
            "SELECT * FROM active_games WHERE user_id = %s FOR UPDATE",
            (user_id,),
        )
        game = cur.fetchone()
        if not game or game["game_type"] != "mines":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}
        if cell_index < 0 or cell_index >= MINES_TOTAL_CELLS:
            conn.rollback()
            cur.close()
            return False, {"reason": "invalid_cell"}

        state = game["state"]
        if cell_index in state["opened"]:
            conn.rollback()
            cur.close()
            return False, {"reason": "already_open"}

        if cell_index in state["mine_positions"]:
            cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
            conn.commit()
            cur.close()
            return True, {
                "status": "hit_mine",
                "mine_positions": state["mine_positions"],
                "opened": state["opened"],
                "bet": game["bet"],
                "winnings": 0,
                "new_points": get_points(user_id),
            }

        state["opened"].append(cell_index)
        opened_count = len(state["opened"])
        multiplier = _mines_multiplier(MINES_TOTAL_CELLS, state["mine_count"], opened_count)
        safe_total = MINES_TOTAL_CELLS - state["mine_count"]

        if opened_count >= safe_total:
            # همه‌ی خونه‌های امن باز شدن -> برد کامل، خودکار برداشت می‌شه
            winnings = int(game["bet"] * multiplier)
            cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
            conn.commit()
            cur.close()
            new_points = _credit(user_id, winnings)
            return True, {
                "status": "cleared_all",
                "opened": state["opened"],
                "mine_positions": state["mine_positions"],
                "multiplier": multiplier,
                "bet": game["bet"],
                "winnings": winnings,
                "new_points": new_points,
            }

        cur.execute(
            """
            UPDATE active_games
            SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval
            WHERE user_id = %s
            """,
            (psycopg2.extras.Json(state), GAME_TIMEOUT_SECONDS, user_id),
        )
        conn.commit()
        cur.close()
        return True, {
            "status": "safe",
            "opened": state["opened"],
            "multiplier": multiplier,
            "bet": game["bet"],
            "new_points": get_points(user_id),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def mines_cashout(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM active_games WHERE user_id = %s FOR UPDATE",
            (user_id,),
        )
        game = cur.fetchone()
        if not game or game["game_type"] != "mines":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}

        state = game["state"]
        opened_count = len(state["opened"])
        if opened_count == 0:
            conn.rollback()
            cur.close()
            return False, {"reason": "no_cells_opened"}

        multiplier = _mines_multiplier(MINES_TOTAL_CELLS, state["mine_count"], opened_count)
        winnings = int(game["bet"] * multiplier)
        cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
        new_points = _credit(user_id, winnings)

        return True, {
            "status": "cashed_out",
            "opened": state["opened"],
            "mine_positions": state["mine_positions"],
            "multiplier": multiplier,
            "bet": game["bet"],
            "winnings": winnings,
            "new_points": new_points,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# 🆚 بازی‌های دونفره (دوز، گل یا پوچ)
# جریان کار: یه نفر با ریپلای + مبلغ شرط دعوت می‌فرسته (مثلاً «درخواست دوز 5000»)،
# طرف مقابل تا ۶۵ ثانیه وقت داره «قبول» یا «رد» کنه. اگه قبول کرد، شرط از هر دو
# نفر کم می‌شه و بازی شروع می‌شه؛ برنده کل مبلغ (۲ برابر شرط) رو می‌گیره.
# هر حرکت هم ۶۵ ثانیه مهلت داره؛ دیر جنبیدن = باخت خودکار به نفع طرف مقابل.
# ---------------------------------------------------------------------------

DUEL_MIN_BET = 100
DUEL_MAX_BET = 20000
DUEL_INVITE_TIMEOUT_SECONDS = 65
DUEL_TURN_TIMEOUT_SECONDS = 65


def ensure_duel_tables():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS duel_invites (
                id SERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL,
                challenger_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                game_type TEXT NOT NULL,
                bet INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS duel_games (
                id SERIAL PRIMARY KEY,
                chat_id TEXT NOT NULL,
                player1_id TEXT NOT NULL,
                player2_id TEXT NOT NULL,
                game_type TEXT NOT NULL,
                bet INTEGER NOT NULL,
                state JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def _user_in_any_duel(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM duel_games WHERE player1_id = %s OR player2_id = %s",
            (str(user_id), str(user_id)),
        )
        row = cur.fetchone()
        cur.close()
        return row is not None
    finally:
        put_conn(conn)


def _user_has_pending_invite(chat_id, user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM duel_invites WHERE chat_id = %s AND (challenger_id = %s OR target_id = %s)",
            (chat_id, str(user_id), str(user_id)),
        )
        row = cur.fetchone()
        cur.close()
        return row is not None
    finally:
        put_conn(conn)


def create_duel_invite(chat_id, challenger_id, target_id, game_type, bet):
    if str(challenger_id) == str(target_id):
        return False, {"reason": "self"}
    if bet < DUEL_MIN_BET:
        return False, {"reason": "below_min", "min": DUEL_MIN_BET}
    if bet > DUEL_MAX_BET:
        return False, {"reason": "above_max", "max": DUEL_MAX_BET}
    if _user_in_any_duel(challenger_id) or _user_in_any_duel(target_id):
        return False, {"reason": "already_in_game"}
    if _user_has_pending_invite(chat_id, challenger_id) or _user_has_pending_invite(chat_id, target_id):
        return False, {"reason": "pending_invite_exists"}

    challenger_points = get_points(challenger_id)
    if challenger_points is None or challenger_points < bet:
        return False, {"reason": "insufficient"}

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO duel_invites (chat_id, challenger_id, target_id, game_type, bet, expires_at)
            VALUES (%s, %s, %s, %s, %s, NOW() + (%s || ' seconds')::interval)
            """,
            (chat_id, str(challenger_id), str(target_id), game_type, bet, DUEL_INVITE_TIMEOUT_SECONDS),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)

    return True, {"bet": bet, "game_type": game_type}


def get_pending_invite(chat_id, target_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM duel_invites WHERE chat_id = %s AND target_id = %s ORDER BY created_at DESC LIMIT 1",
            (chat_id, str(target_id)),
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        put_conn(conn)


def _delete_duel_invite(invite_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM duel_invites WHERE id = %s", (invite_id,))
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def decline_duel_invite(chat_id, target_id):
    invite = get_pending_invite(chat_id, target_id)
    if not invite:
        return False, {"reason": "no_invite"}
    _delete_duel_invite(invite["id"])
    return True, {"challenger_id": invite["challenger_id"], "game_type": invite["game_type"]}


TICTACTOE_WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def _new_tictactoe_state(player1_id, player2_id):
    return {
        "board": [None] * 9,
        "turn": str(player1_id),
        "symbols": {str(player1_id): "❌", str(player2_id): "⭕"},
    }


def _new_guessflower_state(hider_id, guesser_id):
    return {
        "hider_id": str(hider_id),
        "guesser_id": str(guesser_id),
        "round": 1,
        "score_hider": 0,
        "score_guesser": 0,
        "hidden": None,
    }


def accept_duel_invite(chat_id, target_id):
    invite = get_pending_invite(chat_id, target_id)
    if not invite:
        return False, {"reason": "no_invite"}

    challenger_id = invite["challenger_id"]
    bet = invite["bet"]
    game_type = invite["game_type"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        ids_sorted = sorted([str(challenger_id), str(target_id)])
        cur.execute(
            "SELECT user_id, points FROM meowie_users WHERE user_id = ANY(%s) FOR UPDATE",
            (ids_sorted,),
        )
        rows = {r["user_id"]: r["points"] for r in cur.fetchall()}

        if str(challenger_id) not in rows or rows[str(challenger_id)] < bet:
            conn.rollback()
            cur.close()
            _delete_duel_invite(invite["id"])
            return False, {"reason": "challenger_insufficient"}
        if str(target_id) not in rows or rows[str(target_id)] < bet:
            conn.rollback()
            cur.close()
            return False, {"reason": "insufficient"}

        cur.execute("UPDATE meowie_users SET points = points - %s WHERE user_id = %s", (bet, challenger_id))
        cur.execute("UPDATE meowie_users SET points = points - %s WHERE user_id = %s", (bet, target_id))

        if game_type == "tictactoe":
            state = _new_tictactoe_state(challenger_id, target_id)
        elif game_type == "guessflower":
            state = _new_guessflower_state(challenger_id, target_id)
        else:
            state = {}

        cur.execute(
            """
            INSERT INTO duel_games (chat_id, player1_id, player2_id, game_type, bet, state, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW() + (%s || ' seconds')::interval)
            """,
            (chat_id, str(challenger_id), str(target_id), game_type, bet,
             psycopg2.extras.Json(state), DUEL_TURN_TIMEOUT_SECONDS),
        )
        cur.execute("DELETE FROM duel_invites WHERE id = %s", (invite["id"],))

        conn.commit()
        cur.close()
        return True, {
            "challenger_id": challenger_id,
            "target_id": target_id,
            "game_type": game_type,
            "bet": bet,
            "state": state,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def get_duel_game(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM duel_games WHERE player1_id = %s OR player2_id = %s",
            (str(user_id), str(user_id)),
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        put_conn(conn)


def _update_duel_state(game_id, state):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE duel_games SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval WHERE id = %s",
            (psycopg2.extras.Json(state), DUEL_TURN_TIMEOUT_SECONDS, game_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def _end_duel_game(game_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM duel_games WHERE id = %s", (game_id,))
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_expired_duel_invites():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM duel_invites WHERE expires_at < NOW()")
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        put_conn(conn)


def get_expired_duel_games():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM duel_games WHERE expires_at < NOW()")
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        put_conn(conn)


def expire_duel_invite(invite):
    _delete_duel_invite(invite["id"])
    return {
        "challenger_id": invite["challenger_id"],
        "target_id": invite["target_id"],
        "game_type": invite["game_type"],
        "chat_id": invite["chat_id"],
    }


def expire_duel_game(game):
    """
    وقتی نوبت یکی از بازیکن‌ها بوده و مهلتش تموم شده، بازی به نفع طرف مقابل بسته می‌شه.
    """
    game_type = game["game_type"]
    state = game["state"]
    bet = game["bet"]
    pot = bet * 2

    if game_type == "tictactoe":
        loser_id = state["turn"]
        winner_id = game["player2_id"] if str(loser_id) == str(game["player1_id"]) else game["player1_id"]
    elif game_type == "guessflower":
        if state["hidden"] is None:
            loser_id = state["hider_id"]
            winner_id = state["guesser_id"]
        else:
            loser_id = state["guesser_id"]
            winner_id = state["hider_id"]
    else:
        loser_id = None
        winner_id = None

    _end_duel_game(game["id"])
    new_points = _credit(winner_id, pot) if winner_id else None

    return {
        "winner_id": winner_id,
        "loser_id": loser_id,
        "pot": pot,
        "new_points": new_points,
        "chat_id": game["chat_id"],
        "game_type": game_type,
    }


def _tictactoe_winner(board):
    for a, b, c in TICTACTOE_WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def duel_tictactoe_move(user_id, cell_index):
    game = get_duel_game(user_id)
    if not game or game["game_type"] != "tictactoe":
        return False, {"reason": "no_game"}

    state = game["state"]
    if state["turn"] != str(user_id):
        return False, {"reason": "not_your_turn"}
    if cell_index < 0 or cell_index > 8:
        return False, {"reason": "invalid_cell"}
    if state["board"][cell_index] is not None:
        return False, {"reason": "cell_taken"}

    symbol = state["symbols"][str(user_id)]
    state["board"][cell_index] = symbol

    other_id = game["player2_id"] if str(user_id) == str(game["player1_id"]) else game["player1_id"]
    pot = game["bet"] * 2

    winner_symbol = _tictactoe_winner(state["board"])
    board_full = all(c is not None for c in state["board"])

    if winner_symbol:
        _end_duel_game(game["id"])
        new_points = _credit(user_id, pot)
        return True, {
            "status": "win",
            "board": state["board"],
            "winner_id": user_id,
            "pot": pot,
            "new_points": new_points,
            "chat_id": game["chat_id"],
        }

    if board_full:
        _end_duel_game(game["id"])
        winner_id = random.choice([game["player1_id"], game["player2_id"]])
        new_points = _credit(winner_id, pot)
        return True, {
            "status": "coin_of_fate",
            "board": state["board"],
            "winner_id": winner_id,
            "pot": pot,
            "new_points": new_points,
            "chat_id": game["chat_id"],
        }

    state["turn"] = other_id
    _update_duel_state(game["id"], state)
    return True, {
        "status": "in_progress",
        "board": state["board"],
        "next_turn": other_id,
        "chat_id": game["chat_id"],
    }


def duel_flower_hide(user_id, hand):
    """hand: 'چپ' یا 'راست'. باید فقط تو پی‌وی بات صدا زده بشه (چک این‌کار تو bot.py انجام می‌شه)."""
    game = get_duel_game(user_id)
    if not game or game["game_type"] != "guessflower":
        return False, {"reason": "no_game"}

    state = game["state"]
    if state["hider_id"] != str(user_id):
        return False, {"reason": "not_hider"}
    if state["hidden"] is not None:
        return False, {"reason": "already_hidden"}
    if hand not in ("چپ", "راست"):
        return False, {"reason": "invalid_hand"}

    state["hidden"] = hand
    _update_duel_state(game["id"], state)
    return True, {"chat_id": game["chat_id"], "round": state["round"]}


def duel_flower_guess(user_id, guess):
    game = get_duel_game(user_id)
    if not game or game["game_type"] != "guessflower":
        return False, {"reason": "no_game"}

    state = game["state"]
    if state["guesser_id"] != str(user_id):
        return False, {"reason": "not_guesser"}
    if state["hidden"] is None:
        return False, {"reason": "not_hidden_yet"}
    if guess not in ("چپ", "راست"):
        return False, {"reason": "invalid_guess"}

    revealed_hand = state["hidden"]
    correct = guess == revealed_hand

    if correct:
        state["score_guesser"] += 1
    else:
        state["score_hider"] += 1

    pot = game["bet"] * 2
    hider_id = state["hider_id"]
    guesser_id = state["guesser_id"]
    score_hider = state["score_hider"]
    score_guesser = state["score_guesser"]

    if score_guesser >= 2:
        _end_duel_game(game["id"])
        new_points = _credit(guesser_id, pot)
        return True, {
            "status": "guesser_win", "correct": correct, "revealed_hand": revealed_hand,
            "score_hider": score_hider, "score_guesser": score_guesser,
            "pot": pot, "new_points": new_points, "chat_id": game["chat_id"],
            "hider_id": hider_id, "guesser_id": guesser_id,
        }

    if score_hider >= 2:
        _end_duel_game(game["id"])
        new_points = _credit(hider_id, pot)
        return True, {
            "status": "hider_win", "correct": correct, "revealed_hand": revealed_hand,
            "score_hider": score_hider, "score_guesser": score_guesser,
            "pot": pot, "new_points": new_points, "chat_id": game["chat_id"],
            "hider_id": hider_id, "guesser_id": guesser_id,
        }

    state["round"] += 1
    state["hidden"] = None
    _update_duel_state(game["id"], state)
    return True, {
        "status": "next_round", "correct": correct, "revealed_hand": revealed_hand,
        "score_hider": score_hider, "score_guesser": score_guesser,
        "round": state["round"], "chat_id": game["chat_id"],
        "hider_id": hider_id, "guesser_id": guesser_id,
    }
