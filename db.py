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
EXP_NEEDED_PER_LEVEL = 5


def get_or_create_user(user_id, username=None):
    conn = get_conn()
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
    put_conn(conn)
    return user


def get_username(user_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT username FROM meowie_users WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    put_conn(conn)
    if row and row["username"]:
        return row["username"]
    return None


def set_username(user_id, username):
    get_or_create_user(user_id, username)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE meowie_users SET username = %s WHERE user_id = %s",
        (username, user_id),
    )
    conn.commit()
    cur.close()
    put_conn(conn)


def exp_needed_for_next_level(level):
    return level * EXP_NEEDED_PER_LEVEL


def ensure_extra_columns():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "ALTER TABLE meowie_users ADD COLUMN IF NOT EXISTS total_meows INTEGER DEFAULT 0"
    )
    conn.commit()
    cur.close()
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
    while new_exp >= exp_needed_for_next_level(new_level):
        new_exp -= exp_needed_for_next_level(new_level)
        new_level += 1
        leveled_up = True

    conn = get_conn()
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
    put_conn(conn)

    return True, {
        "points_earned": points_earned,
        "total_points": new_points,
        "level": new_level,
        "leveled_up": leveled_up,
        "exp": new_exp,
        "exp_needed": exp_needed_for_next_level(new_level),
    }


def get_leaderboard_global(order_by="points", limit=10):
    column = "points" if order_by == "points" else "total_meows"
    conn = get_conn()
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
    put_conn(conn)
    return rows


def ensure_group_members_table():
    conn = get_conn()
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
    put_conn(conn)


def record_group_membership(chat_id, user_id):
    conn = get_conn()
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
    put_conn(conn)


def get_leaderboard_group(chat_id, order_by="points", limit=10):
    column = "u.points" if order_by == "points" else "u.total_meows"
    conn = get_conn()
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
    put_conn(conn)
    return rows


def get_profile(user_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM meowie_users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    put_conn(conn)
    return user

def ensure_offset_table():
    conn = get_conn()
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
    put_conn(conn)


def get_offset():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT last_offset_id FROM bot_state WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    put_conn(conn)
    return row[0] if row else None


def set_offset(offset_id):
    conn = get_conn()
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
    put_conn(conn)

