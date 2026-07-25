import os
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
