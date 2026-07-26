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
_pool = psycopg2.pool.ThreadedConnectionPool(1, 5, DATABASE_URL)


def get_conn():
    return _pool.getconn()


def put_conn(conn):
    _pool.putconn(conn)


COOLDOWN_SECONDS = 300  # فاصله ثابت بین هر میو: ۵ دقیقه

# سطح‌های ۱ تا ۵: برای جذب بازیکن‌های جدید، فقط ۱۰ میو لازمه.
# از سطح ۶ به بعد برمی‌گرده به فرمول اصلی (۳۰ + ۵ به ازای هر سطح).
EARLY_LEVEL_THRESHOLD = 5
EARLY_LEVEL_EXP_NEEDED = 10

EXP_BASE_NEEDED = 30
EXP_STEP_PER_LEVEL = 5
MAX_LEVEL = 120

MIN_TRANSFER = 500
MAX_TRANSFER = 10000

# چند روزی که پیام‌های دیده‌شده رو نگه می‌داریم تا بشه رویشون ریپلای زد
SEEN_MESSAGES_RETENTION_DAYS = 3


def get_or_create_user(user_id, username=None):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM meowie_users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            cur.execute(
                "INSERT INTO meowie_users (user_id, username) VALUES (%s, %s) RETURNING *",
                (user_id, username),
            )
            user = cur.fetchone()
            conn.commit()
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
    return EXP_BASE_NEEDED + (level - 1) * EXP_STEP_PER_LEVEL


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

    if last:
        elapsed = (now - last).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            remaining = round(COOLDOWN_SECONDS - elapsed)
            return False, {"reason": "cooldown", "remaining": remaining}

    points_earned = random.randint(1, 100)
    new_points = user["points"] + points_earned
    new_exp = user["exp"] + 1
    new_level = user["level"]
    new_total_meows = (user.get("total_meows") or 0) + 1

    leveled_up = False
    while new_level < MAX_LEVEL:
        needed = exp_needed_for_next_level(new_level)
        if new_exp < needed:
            break
        new_exp -= needed
        new_level += 1
        leveled_up = True

    if new_level >= MAX_LEVEL:
        new_level = MAX_LEVEL
        new_exp = 0  # سطح ماکسیموم، دیگه پیشرفتی برای نمایش نیست

    conn = get_conn()
    try:
        cur = conn.cursor()
        # توجه: دیگه username رو اینجا آپدیت نمی‌کنیم تا اسمی که کاربر
        # خودش با "تنظیم میویی" ثبت کرده پاک نشه.
        cur.execute(
            """
            UPDATE meowie_users
            SET points = %s, exp = %s, level = %s, last_meow_at = %s, total_meows = %s
            WHERE user_id = %s
            """,
            (new_points, new_exp, new_level, now, new_total_meows, user_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)

    return True, {
        "points_earned": points_earned,
        "total_points": new_points,
        "level": new_level,
        "leveled_up": leveled_up,
        "exp": new_exp,
        "exp_needed": exp_needed_for_next_level(new_level),
        "cooldown_seconds": COOLDOWN_SECONDS,
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
CAT_UPGRADE_COST_MULTIPLIER = 5          # هزینه‌ی ارتقا = این عدد × تولید ساعتی فعلی


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


def cat_upgrade_cost(rank, level):
    return cat_production_per_hour(rank, level) * CAT_UPGRADE_COST_MULTIPLIER


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
    game = get_active_game(user_id)
    if not game or game["game_type"] != "mines":
        return False, {"reason": "no_game"}
    if cell_index < 0 or cell_index >= MINES_TOTAL_CELLS:
        return False, {"reason": "invalid_cell"}

    state = game["state"]
    if cell_index in state["opened"]:
        return False, {"reason": "already_open"}

    if cell_index in state["mine_positions"]:
        end_active_game(user_id)
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
        end_active_game(user_id)
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

    update_active_game_state(user_id, state)
    return True, {
        "status": "safe",
        "opened": state["opened"],
        "multiplier": multiplier,
        "bet": game["bet"],
        "new_points": get_points(user_id),
    }


def mines_cashout(user_id):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "mines":
        return False, {"reason": "no_game"}

    state = game["state"]
    opened_count = len(state["opened"])
    if opened_count == 0:
        return False, {"reason": "no_cells_opened"}

    multiplier = _mines_multiplier(MINES_TOTAL_CELLS, state["mine_count"], opened_count)
    winnings = int(game["bet"] * multiplier)
    end_active_game(user_id)
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
