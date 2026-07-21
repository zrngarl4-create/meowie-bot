import os
import datetime
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


BASE_COOLDOWN_SECONDS = 10
COOLDOWN_REDUCTION_PER_LEVEL = 1
MIN_COOLDOWN_SECONDS = 3

EXP_NEEDED_PER_LEVEL = 5


def get_or_create_user(user_id, username):
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
    conn.close()
    return user


def get_cooldown_seconds(level):
    cd = BASE_COOLDOWN_SECONDS - (level - 1) * COOLDOWN_REDUCTION_PER_LEVEL
    return max(cd, MIN_COOLDOWN_SECONDS)


def exp_needed_for_next_level(level):
    return level * EXP_NEEDED_PER_LEVEL


def do_meow(user_id, username):
    import random

    user = get_or_create_user(user_id, username)

    now = datetime.datetime.utcnow()
    last = user["last_meow_at"]

    cooldown = get_cooldown_seconds(user["level"])
    if last:
        elapsed = (now - last).total_seconds()
        if elapsed < cooldown:
            remaining = round(cooldown - elapsed, 1)
            return False, {"reason": "cooldown", "remaining": remaining}

    points_earned = random.randint(1, 100)
    new_points = user["points"] + points_earned
    new_exp = user["exp"] + 1
    new_level = user["level"]

    leveled_up = False
    while new_exp >= exp_needed_for_next_level(new_level):
        new_exp -= exp_needed_for_next_level(new_level)
        new_level += 1
        leveled_up = True

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE meowie_users
        SET points = %s, exp = %s, level = %s, last_meow_at = %s, username = %s
        WHERE user_id = %s
        """,
        (new_points, new_exp, new_level, now, username, user_id),
    )
    conn.commit()
    cur.close()
    conn.close()

    return True, {
        "points_earned": points_earned,
        "total_points": new_points,
        "level": new_level,
        "leveled_up": leveled_up,
        "exp": new_exp,
        "exp_needed": exp_needed_for_next_level(new_level),
    }


def get_profile(user_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM meowie_users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user
