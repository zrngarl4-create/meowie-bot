import os
import re
import time
import math
import random
import string
import datetime
import psycopg2
import psycopg2.extras
import psycopg2.pool

DATABASE_URL = os.environ.get("DATABASE_URL")

# به‌جای باز و بسته کردن یه اتصال تازه برای هر عملیات (که خیلی کند بود)،
# یه استخر کوچیک از اتصالات از قبل باز نگه می‌داریم و فقط قرضشون می‌گیریم.
#
# مهم: این عدد باید هماهنگ با «Connection pool size» تنظیم‌شده تو پنل
# Supabase (Database Settings → Connection Pooling) باشه. اونجا از ۱۵
# (پیش‌فرض پلن Nano) به ۳۰ بردیمش بالا؛ این‌جا هم کمی زیرش نگه می‌داریم
# (نه دقیقاً همون عدد) تا هم بات جا داشته باشه، هم برای دسترسی‌های دیگه
# (مثلاً SQL Editor خودِ Supabase) جا بمونه. اگه توی پنل Supabase دوباره
# این عدد رو عوض کردی، این خط رو هم هماهنگ باهاش به‌روز کن.
_pool = psycopg2.pool.ThreadedConnectionPool(1, 25, DATABASE_URL)


def get_conn():
    """
    یه اتصال از Pool قرض می‌گیره. ThreadedConnectionPool وقتی همه‌ی
    اتصالات در حال استفاده‌ان، بلافاصله خطا می‌ده (صبر نمی‌کنه) — یعنی
    اگه دقیقاً همون لحظه چندنفر هم‌زمان پیام بفرستن و هر ۱۰ تا اتصال
    مشغول باشن، همون یه پیامِ اضافه فوری fail می‌شد، حتی اگه یه‌دهم
    ثانیه بعد یه اتصال آزاد می‌شد. برای همین، به‌جای بلافاصله شکست
    خوردن، چندبار با یه فاصله‌ی کوتاه دوباره امتحان می‌کنیم — این‌جوری
    فقط یه ترافیک لحظه‌ای رو صاف می‌کنه، نه اینکه واقعاً صف طولانی
    درست کنه.
    """
    last_error = None
    for _ in range(8):
        try:
            return _pool.getconn()
        except psycopg2.pool.PoolError as e:
            last_error = e
            time.sleep(0.25)
    raise last_error


def put_conn(conn):
    # اگه یه تابع (هرکدوم) وسط یه تراکنش با خطا مواجه بشه و rollback
    # نکنه، اتصال «آلوده» به Pool برمی‌گرده و هر درخواست بعدی که همین
    # اتصال رو قرض بگیره — حتی برای یه کاربر/دستور کاملاً بی‌ربط —
    # فوراً خطا می‌گیره. این rollback دفاعی، اگه تراکنشی از قبل درست
    # commit شده باشه، کاملاً بی‌اثر و امنه؛ ولی اگه یه‌جایی فراموش شده
    # باشه rollback بشه، اینجا جلوی آلوده شدن Pool رو می‌گیره.
    try:
        conn.rollback()
    except Exception:
        pass
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


_username_cache = {}  # user_id -> (username_or_None, زمان_کش‌شدن)
_USERNAME_CACHE_TTL = 300  # ۵ دقیقه


def get_username(user_id):
    """
    این تابع تقریباً رو هر پیامی صدا زده می‌شه (برای نمایش اسم فرستنده،
    اسم حریف تو بازی‌ها، و...) — یعنی پرتکرارترین کوئری دیتابیس تو کل
    برنامه‌ست. چون اسم کاربرها به‌ندرت عوض می‌شه، نتیجه رو چند دقیقه تو
    حافظه نگه می‌داریم تا این حجم عظیم کوئری تکراری، فشار غیرضروری رو
    استخر اتصال دیتابیس نذاره.
    """
    cached = _username_cache.get(user_id)
    if cached is not None:
        username, cached_at = cached
        if time.time() - cached_at < _USERNAME_CACHE_TTL:
            return username

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT username FROM meowie_users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.close()
        username = row["username"] if row and row["username"] else None
        _username_cache[user_id] = (username, time.time())
        return username
    finally:
        put_conn(conn)


def get_group_count():
    """تعداد گروه‌های متمایزی که بات توشون فعالیت داشته."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT chat_id) FROM group_members")
        count = cur.fetchone()[0]
        cur.close()
        return count
    finally:
        put_conn(conn)


def get_player_count():
    """تعداد کل کاربرهای ثبت‌شده تو بات."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM meowie_users")
        count = cur.fetchone()[0]
        cur.close()
        return count
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
        _username_cache[user_id] = (username, time.time())
    finally:
        put_conn(conn)


# فهرست کلمات غیرمجاز برای اسم — هم برای رد کردن اسم‌های جدید استفاده
# می‌شه، هم برای پیدا کردن اکانت‌های قدیمی‌ای که از قبل این اسم‌ها رو
# داشتن. نسخه‌ی گسترش‌یافته: علاوه بر کلمات قبلی، الفاظ رکیک/جنسی رایج
# فارسی هم اضافه شدن تا کسی نتونه با اسم/اسم‌شرکت همچین چیزی بذاره.
BLOCKED_NAME_WORDS = (
    "بکن", "کص", "کیر", "کون", "کونی", "ممه", "پستون",
    "کیری", "کصکش", "کسکش", "جنده", "جقی", "جق",
    "کیرم", "کصم", "کونت", "کیرت", "کصت",
    "لاشی",
    "زنا", "زناکار", "فاحشه", "هرزه",
    "کاندوم", "داشاق",
    "abjoni", "kir", "kos", "koon", "jende", "kiri",
)


def contains_blocked_word(text):
    if not text:
        return False
    return any(word in text for word in BLOCKED_NAME_WORDS)


# الگوهای رایج تبلیغ خرید/فروش کوین بین بازیکنا (نه فروش رسمی از طرف
# ادمین). این فقط برای تشخیص و هشدار/حذف پیامه — مکانیزم فروش رسمی
# کوین اینجا نیست و قرار نیست باشه.
COIN_TRADE_PATTERNS = (
    "کوین میفروشم", "کوین می‌فروشم", "کوین میخرم", "کوین می‌خرم",
    "فروش کوین", "خرید کوین",
    "خریدار کوین", "فروشنده کوین", "فروشنده‌ی کوین", "خریدار کویین",
    "کوین به قیمت", "قیمت هر کوین", "قیمت کوین",
    "کوین در ازای", "کوین بدم", "کوین بدید", "کوین بدین",
    "تبدیل کوین به تومن", "کوین به تومن", "تومن به کوین",
)


def contains_coin_trade_pattern(text):
    if not text:
        return False
    normalized = text.replace(" ", "").replace("‌", "")
    for pattern in COIN_TRADE_PATTERNS:
        if pattern.replace(" ", "").replace("‌", "") in normalized:
            return True
    return False


def reset_username(user_id):
    """اسم انتخابی کاربر رو پاک می‌کنه (برای دستور ادمینِ «ریست نام»)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE meowie_users SET username = NULL WHERE user_id = %s", (user_id,))
        updated = cur.rowcount > 0
        conn.commit()
        cur.close()
        return updated
    finally:
        put_conn(conn)


def reset_cat_name(owner_id):
    """اسم شرکتِ کاربر رو پاک می‌کنه (برای پاکسازی اسم شرکت‌هایی که نماد رزروشده داشتن)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE cats SET name = NULL WHERE owner_id = %s", (owner_id,))
        updated = cur.rowcount > 0
        conn.commit()
        cur.close()
        return updated
    finally:
        put_conn(conn)


def find_user_ids_by_username(username):
    """برای «ریست کوین <اسم>» / «ریست نام <اسم>» — پیدا کردن کاربر(ها) با این اسم.

    قبلاً اینجا با «=» دقیق مقایسه می‌شد؛ مشکل این بود که خیلی وقت‌ها
    اسمی که ادمین کپی/تایپ می‌کنه یه فاصله‌ی اضافه در ابتدا/انتها داره یا
    حروف بزرگ/کوچیک (برای اسم‌های انگلیسی) فرق داره، و همون اسم که تو
    لیدربرد کامل نشون داده می‌شه، این‌جا هیچ‌وقت پیدا نمی‌شد. با TRIM +
    مقایسه‌ی بدون‌حساسیت به بزرگی/کوچکی حروف، این مورد رفع می‌شه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id FROM meowie_users WHERE TRIM(LOWER(username)) = TRIM(LOWER(%s))",
            (username,),
        )
        rows = cur.fetchall()
        cur.close()
        return [row[0] for row in rows]
    finally:
        put_conn(conn)


def find_users_with_reserved_symbols():
    """
    اسکن اسم‌ کاربرها و اسم شرکت‌هاشون برای پیدا کردن کسایی که قبل از این
    فیلتر، با ایموجی یا نمادهای فروشگاهی (تاج، 💎...) یا کلمات تبلیغ
    خرید/فروش کوین یا آیدی/هندل (@...) برای خودشون اسم گذاشته بودن.
    برخلاف find_users_with_blocked_names، این فقط برای پاکسازی «اسم»‌هاست،
    نه یه تخلف اخلاقی — پس نباید کل اکانت ریست بشه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username FROM meowie_users WHERE username IS NOT NULL")
        user_rows = cur.fetchall()
        cur.execute(
            """
            SELECT c.owner_id, c.name
            FROM cats c
            WHERE c.name IS NOT NULL
            """
        )
        cat_rows = cur.fetchall()
        cur.close()
    finally:
        put_conn(conn)
    bad_users = [
        (uid, name) for uid, name in user_rows
        if contains_reserved_cosmetic_symbol(name) or contains_username_spam(name)
    ]
    bad_companies = [
        (uid, name) for uid, name in cat_rows
        if contains_reserved_cosmetic_symbol(name) or contains_username_spam(name)
    ]
    return bad_users, bad_companies


def find_users_with_blocked_names():
    """اسکن همه‌ی کاربرا برای پیدا کردن اکانت‌هایی که اسمشون کلمه‌ی
    غیرمجاز داره (برای پاکسازی اسم‌های قدیمی که قبل از فیلتر ثبت شدن)."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username FROM meowie_users WHERE username IS NOT NULL")
        rows = cur.fetchall()
        cur.close()
    finally:
        put_conn(conn)
    return [(user_id, username) for user_id, username in rows if contains_blocked_word(username)]


def full_reset_user_account(user_id):
    """ریست کامل یه اکانت: کوین، سطح، تجربه، شرکت، و کیف آیتم‌ها —
    برای پاکسازی اکانت‌هایی که اسم نامناسب داشتن."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE meowie_users
            SET points = 0, exp = 0, level = 1, total_meows = 0,
                last_meow_at = NULL, username = NULL
            WHERE user_id = %s
            """,
            (user_id,),
        )
        cur.execute("DELETE FROM cats WHERE owner_id = %s", (user_id,))
        cur.execute("DELETE FROM user_inventory WHERE user_id = %s", (user_id,))
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
        # chat_id واقعیِ پیویِ این کاربر با بات — فقط وقتی معتبره که از
        # یه پیامی که خودِ کاربر مستقیم تو پیوی بات فرستاده گرفته شده
        # باشه، نه حدس‌زده از sender_id یه پیام گروهی (که باعث خطای
        # INVALID_INPUT تو ارسال پیام مافیا شد).
        cur.execute(
            "ALTER TABLE meowie_users ADD COLUMN IF NOT EXISTS pv_chat_id TEXT"
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def set_pv_chat_id(user_id, chat_id):
    get_or_create_user(user_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE meowie_users SET pv_chat_id = %s WHERE user_id = %s",
            (chat_id, user_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_pv_chat_id(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pv_chat_id FROM meowie_users WHERE user_id = %s", (str(user_id),))
        row = cur.fetchone()
        cur.close()
        return row[0] if row and row[0] else None
    finally:
        put_conn(conn)


def do_meow(user_id, username):
    import random

    get_or_create_user(user_id, username)  # فقط برای اطمینان از وجود ردیف کاربر

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # نکته‌ی مهم: قبلاً last_meow_at با یه SELECT ساده (بدون قفل) خونده
        # می‌شد و فقط در پایان تابع نوشته می‌شد. اگه چند «میو» تقریباً
        # هم‌زمان می‌رسیدن، همه‌شون می‌تونستن قبل از ثبت شدن اولی، همون
        # مقدار قدیمیِ last_meow_at رو ببینن و همه فکر کنن کولداون تموم
        # شده — دقیقاً همون باگی که باعث می‌شد اسپم «میو» نادیده گرفته
        # نشه. حالا با قفل کردن ردیف تا آخر تراکنش، اگه دو تا میو هم‌زمان
        # برسن، دومی صبر می‌کنه اولی کامل تموم بشه و بعد با تاریخ واقعی
        # و به‌روز چک می‌کنه.
        cur.execute("SELECT * FROM meowie_users WHERE user_id = %s FOR UPDATE", (user_id,))
        user = cur.fetchone()

        now = datetime.datetime.utcnow()
        last = user["last_meow_at"]
        current_cooldown = cooldown_for_level(user["level"])

        if last:
            elapsed = (now - last).total_seconds()
            if elapsed < current_cooldown:
                remaining = round(current_cooldown - elapsed)
                conn.rollback()
                cur.close()
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
        final_points = cur.fetchone()["points"]
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


def get_group_chat_ids_by_size(limit=None):
    """گروه‌ها رو از بزرگ‌ترین (بیشترین عضو دیده‌شده) به کوچیک‌ترین مرتب می‌کنه."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        query = """
            SELECT chat_id, COUNT(DISTINCT user_id) AS member_count
            FROM group_members
            GROUP BY chat_id
            ORDER BY member_count DESC
        """
        if limit:
            query += " LIMIT %s"
            cur.execute(query, (limit,))
        else:
            cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]
    finally:
        put_conn(conn)


def ensure_ad_broadcast_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ad_broadcasts (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ad_broadcast_messages (
                broadcast_id INTEGER NOT NULL REFERENCES ad_broadcasts(id),
                chat_id TEXT NOT NULL,
                message_id TEXT
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def start_ad_broadcast():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO ad_broadcasts DEFAULT VALUES RETURNING id")
        broadcast_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return broadcast_id
    finally:
        put_conn(conn)


def record_ad_broadcast_message(broadcast_id, chat_id, message_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ad_broadcast_messages (broadcast_id, chat_id, message_id) VALUES (%s, %s, %s)",
            (broadcast_id, chat_id, message_id),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_latest_ad_broadcast_messages():
    """فقط پیام‌های آخرین دسته‌ی تبلیغاتی که واقعاً message_id داشتن."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM ad_broadcasts ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            cur.close()
            return []
        broadcast_id = row[0]
        cur.execute(
            "SELECT chat_id, message_id FROM ad_broadcast_messages WHERE broadcast_id = %s AND message_id IS NOT NULL",
            (broadcast_id,),
        )
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        put_conn(conn)


def record_message_context(chat_id, sender_id, message_id):
    """
    نسخه‌ی ترکیبی record_group_membership + record_seen_message که تو یه
    اتصال دیتابیس (نه دوتا) انجامش می‌ده تا هر پیام یه رفت‌وبرگشت شبکه‌ی
    کمتر با دیتابیس داشته باشه و ربات سریع‌تر جواب بده.

    خروجی: True یعنی این message_id قبلاً دیده نشده بود (پردازش کن).
    False یعنی این پیام قبلاً یه‌بار پردازش شده — احتمالاً روبیکا یا
    شبکه همون وبهوک رو دوباره فرستاده؛ این‌جوری از اجرای دوباره‌ی یه
    دستور مالی (مثل انتقال کوین یا برد بازی) به‌خاطر تحویل تکراری
    جلوگیری می‌شه.
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
        is_new = True
        if message_id:
            cur.execute(
                """
                INSERT INTO seen_messages (message_id, chat_id, sender_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (message_id) DO NOTHING
                """,
                (str(message_id), str(chat_id), str(sender_id)),
            )
            is_new = cur.rowcount > 0
        conn.commit()
        cur.close()
        return is_new
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


def _company_upgrade_bonuses(owner_id):
    """
    درصد بونس دائمی‌ای که کاربر از فروشگاه خریده رو برمی‌گردونه:
    (درصد افزایش سود, درصد افزایش ظرفیت خزانه).
    عمومی‌شده: به‌جای اینکه فقط سه کد ثابت (accountant/ads/cfo) رو
    بشناسه، هر آیتمی که owner داره و effect_type ش با الگوی
    permanent_income_<عدد> یا permanent_capacity_<عدد> باشه رو خودکار
    می‌شناسه و جمع می‌زنه. یعنی برای چرخه‌های بعدی فروشگاه، کافیه یه
    ردیف جدید تو shop_items اضافه بشه؛ نیازی به تغییر این تابع نیست.
    یه بوست موقت هم (از «بازار سیاه») اگه فعال باشه به سود اضافه می‌شه —
    چون سقف صندوق هم از همین سود محاسبه می‌شه، اگه پلیر تو بازه‌ی
    فعال‌بودنش برداشت نکنه، همون اضافه‌سودی که نگرفته از دست می‌ره؛
    این خودش همون «ریسک»ه، بدون نیاز به منطق جریمه‌ی جداگونه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.effect_type
            FROM user_inventory i
            JOIN shop_items s ON s.code = i.item_code
            WHERE i.user_id = %s
              AND i.quantity > 0
              AND (s.effect_type LIKE 'permanent_income_%%' OR s.effect_type LIKE 'permanent_capacity_%%')
            """,
            (owner_id,),
        )
        rows = cur.fetchall()

        cur.execute(
            "SELECT boost_pct FROM user_temp_income_boost WHERE user_id = %s AND expires_at > NOW()",
            (owner_id,),
        )
        temp_row = cur.fetchone()
        cur.close()
    finally:
        put_conn(conn)

    income_pct = 0
    capacity_pct = 0
    for (effect_type,) in rows:
        try:
            prefix, value = effect_type.rsplit("_", 1)
            value = int(value)
        except (ValueError, AttributeError):
            continue
        if prefix == "permanent_income":
            income_pct += value
        elif prefix == "permanent_capacity":
            capacity_pct += value
    if temp_row:
        income_pct += temp_row[0]
    return income_pct, capacity_pct


def cat_production_per_hour(rank, level, owner_id=None):
    power = cat_power_level(rank, level)
    base = max(1, power * CAT_PRODUCTION_PER_HOUR_PER_POWER)
    if owner_id:
        income_pct, _ = _company_upgrade_bonuses(owner_id)
        base = base * (100 + income_pct) / 100
    return round(base)


def cat_capacity(rank, level, owner_id=None):
    per_hour = cat_production_per_hour(rank, level, owner_id)
    capacity = per_hour * CAT_CAPACITY_HOURS
    if owner_id:
        _, capacity_pct = _company_upgrade_bonuses(owner_id)
        capacity = capacity * (100 + capacity_pct) / 100
    return round(capacity)


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
        per_hour = cat_production_per_hour(cat["rank"], cat["level"], owner_id)
        capacity = cat_capacity(cat["rank"], cat["level"], owner_id)
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


# 🎲 اثرهای مصرفی فروشگاه رو کازینو (طلسم شانس / بیمه سرمایه)
# فقط رو بازی‌های تک‌مرحله‌ای (سکه، تاس، بالا/پایین، اسلات، رولت،
# دارت) اعمال می‌شه — نه بلک‌جک/کرش/مین‌یاب/دوئل، چون اون‌ها چندمرحله‌این
# و «برد/باخت» تو یه لحظه‌ی مشخص تصمیم نمی‌گیرن.
LUCK_BOOST_FLIP_CHANCE = 0.10
INSURANCE_REFUND_RATE = 0.5


def ensure_shop_effects_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_pending_effects (
                user_id TEXT NOT NULL,
                effect_type TEXT NOT NULL,
                PRIMARY KEY (user_id, effect_type)
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


# 🎁 کد هدیه — هر بار که ادمین تو گروه درخواستش کنه، یه کد تازه ساخته
# می‌شه؛ حداکثر ۱۰ نفر می‌تونن استفاده‌ش کنن و مبلغش هر بار رندوم بین
# ۱٬۰۰۰ تا ۱۰٬۰۰۰ کوینه (گام‌های هزارتایی).
IRAN_UTC_OFFSET = datetime.timedelta(hours=3, minutes=30)
GIFT_CODE_MAX_USES = 10
GIFT_CODE_COIN_MIN = 1000
GIFT_CODE_COIN_MAX = 10000


def iran_now():
    return datetime.datetime.utcnow() + IRAN_UTC_OFFSET


def ensure_gift_code_tables():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_gift_codes (
                id SERIAL PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                coin_value INTEGER NOT NULL,
                max_uses INTEGER NOT NULL,
                uses_count INTEGER NOT NULL DEFAULT 0,
                created_date DATE NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_gift_code_redemptions (
                code_id INTEGER NOT NULL REFERENCES daily_gift_codes(id),
                user_id TEXT NOT NULL,
                redeemed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (code_id, user_id)
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def _generate_code_text():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def generate_gift_code():
    """
    یه کد هدیه‌ی تازه می‌سازه (هر بار که صدا زده بشه، فارغ از تاریخ یا
    اینکه قبلاً کدی ساخته شده یا نه) و کد + مبلغش رو برمی‌گردونه.
    مبلغ هر کد رندوم بین GIFT_CODE_COIN_MIN و GIFT_CODE_COIN_MAX
    (با گام‌های ۱۰۰۰تایی) انتخاب می‌شه.
    """
    coin_value = random.randint(
        GIFT_CODE_COIN_MIN // 1000, GIFT_CODE_COIN_MAX // 1000
    ) * 1000
    today = iran_now().date()
    conn = get_conn()
    try:
        cur = conn.cursor()
        # احتمال برخورد کد تصادفی خیلی کمه ولی چون این تابع می‌تونه چندبار
        # پشت‌سرهم صدا زده بشه، چند بار تلاش می‌کنیم تا کد یکتا پیدا بشه.
        for _ in range(5):
            code = _generate_code_text()
            try:
                cur.execute(
                    """
                    INSERT INTO daily_gift_codes (code, coin_value, max_uses, uses_count, created_date)
                    VALUES (%s, %s, %s, 0, %s)
                    """,
                    (code, coin_value, GIFT_CODE_MAX_USES, today),
                )
                conn.commit()
                cur.close()
                return code, coin_value
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                continue
        cur.close()
        raise RuntimeError("نتونستم کد هدیه‌ی یکتا بسازم")
    finally:
        put_conn(conn)


def redeem_gift_code(user_id, code_text):
    code_text = code_text.strip().upper()
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(
            "SELECT id, is_active FROM daily_gift_codes WHERE code = %s",
            (code_text,),
        )
        gift = cur.fetchone()
        if not gift or not gift["is_active"]:
            conn.rollback()
            cur.close()
            return False, {"reason": "invalid_code"}

        # اول عضویت این کاربر رو ثبت می‌کنیم؛ چون (code_id, user_id) کلید
        # یکتاست، اگه قبلاً همین کد رو گرفته باشه همین‌جا خطا می‌خوریم و
        # لازم نیست قبلش یه SELECT جدا برای چک تکراری بزنیم.
        try:
            cur.execute(
                "INSERT INTO daily_gift_code_redemptions (code_id, user_id) VALUES (%s, %s)",
                (gift["id"], user_id),
            )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            cur.close()
            return False, {"reason": "already_redeemed"}

        # سهمیه رو با یه UPDATE اتمیک و شرطی کم می‌کنیم؛ قفل ردیف فقط
        # برای طول همین یه کوئری نگه داشته می‌شه، نه کل تراکنش — این‌جوری
        # وقتی چندنفر هم‌زمان همین کد رو می‌فرستن، صف خیلی سریع‌تر خالی می‌شه.
        cur.execute(
            """
            UPDATE daily_gift_codes
            SET uses_count = uses_count + 1,
                is_active = (uses_count + 1 < max_uses)
            WHERE id = %s AND uses_count < max_uses
            RETURNING coin_value, max_uses, uses_count
            """,
            (gift["id"],),
        )
        updated = cur.fetchone()
        if not updated:
            conn.rollback()
            cur.close()
            return False, {"reason": "fully_used"}

        cur.execute(
            "UPDATE meowie_users SET points = points + %s WHERE user_id = %s RETURNING points",
            (updated["coin_value"], user_id),
        )
        new_points = cur.fetchone()["points"]
        conn.commit()
        cur.close()
        return True, {
            "coins": updated["coin_value"],
            "new_points": new_points,
            "remaining_uses": updated["max_uses"] - updated["uses_count"],
        }
    finally:
        put_conn(conn)


def _add_pending_effect(user_id, effect_type):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO user_pending_effects (user_id, effect_type)
            VALUES (%s, %s)
            ON CONFLICT (user_id, effect_type) DO NOTHING
            """,
            (user_id, effect_type),
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def _consume_pending_effect(user_id, effect_type):
    """اتمیک: اگه این اثر فعال بود، مصرفش می‌کنه و True برمی‌گردونه."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM user_pending_effects WHERE user_id = %s AND effect_type = %s RETURNING effect_type",
            (user_id, effect_type),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return row is not None
    finally:
        put_conn(conn)


def _apply_luck_boost(user_id, won):
    """اگه طلسم شانس فعال بود، مصرفش می‌کنه؛ اگه نتیجه طبیعی باخت بود،
    ۱۰٪ شانس داره که به برد تبدیلش کنه. خروجی سوم (flipped) یعنی
    واقعاً همین اتفاق افتاد، نه فقط اینکه طلسم مصرف شد."""
    used = _consume_pending_effect(user_id, "luck_boost")
    flipped = False
    if not won and used and random.random() < LUCK_BOOST_FLIP_CHANCE:
        won = True
        flipped = True
    return won, used, flipped


def _apply_insurance(user_id, won, bet):
    """اگه بیمه سرمایه فعال بود، مصرفش می‌کنه؛ اگه نتیجه‌ی نهایی باخت
    بود، نصف شرط برمی‌گرده."""
    used = _consume_pending_effect(user_id, "bet_insurance")
    refund = int(bet * INSURANCE_REFUND_RATE) if (not won and used) else 0
    return used, refund


def coin_flip(user_id, bet, choice):
    """choice باید 'شیر' یا 'خط' باشه."""
    range_error = _check_bet_range(bet)
    if range_error:
        return False, range_error
    if choice not in ("شیر", "خط"):
        return False, {"reason": "invalid_choice"}

    result = random.choice(["شیر", "خط"])
    won = result == choice
    won, luck_used, luck_flipped = _apply_luck_boost(user_id, won)
    winnings = int(bet * COINFLIP_PAYOUT_MULTIPLIER) if won else 0

    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    insurance_used, refund = _apply_insurance(user_id, won, bet)
    if refund:
        new_points = _credit(user_id, refund)

    return True, {
        "result": result,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
        "luck_used": luck_used,
        "luck_flipped": luck_flipped,
        "insurance_refund": refund,
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
    won, luck_used, luck_flipped = _apply_luck_boost(user_id, won)
    winnings = int(bet * DICE_PAYOUT_MULTIPLIER) if won else 0

    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    insurance_used, refund = _apply_insurance(user_id, won, bet)
    if refund:
        new_points = _credit(user_id, refund)

    return True, {
        "result": result,
        "guess": guess,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
        "luck_used": luck_used,
        "luck_flipped": luck_flipped,
        "insurance_refund": refund,
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

    won, luck_used, luck_flipped = _apply_luck_boost(user_id, won)
    winnings = int(bet * HIGHLOW_PAYOUT_MULTIPLIER) if won else 0

    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    insurance_used, refund = _apply_insurance(user_id, won, bet)
    if refund:
        new_points = _credit(user_id, refund)

    return True, {
        "current": current,
        "next": next_number,
        "direction": direction,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
        "luck_used": luck_used,
        "luck_flipped": luck_flipped,
        "insurance_refund": refund,
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


def refund_bet(user_id, bet):
    """
    برگردوندن یه شرط به کاربر، برای وقتی که بازی با موفقیت تو دیتابیس
    شروع شده (یعنی _deduct_bet قبلاً اجرا شده) ولی پیام شروعش هیچ‌وقت
    به دست بازیکن نرسیده (مثلاً ارسال پیام رو روبیکا شکست خورده). بدون
    این تابع، پول کم می‌شد ولی بازیکن هیچ‌وقت نمی‌فهمید بازی شروع شده،
    و بعد از GAME_TIMEOUT_SECONDS خودکار می‌باخت. برخلاف add_points که
    مسیر ادمینه، این فقط باید از مسیرهای لغوِ خودکارِ بازی صدا زده بشه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "UPDATE meowie_users SET points = points + %s WHERE user_id = %s RETURNING points",
            (bet, user_id),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return row["points"] if row else None
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# حدس کلمه — تک‌نفره، رایگان (شرطی نداره). هر دور یه کلمه‌ی رندوم از بانک
# انتخاب می‌شه؛ تعداد تلاش مجاز به تعداد حروف همون کلمه‌ست. هر حدس غلط،
# یه حرف دیگه (به‌ترتیب از اول کلمه) به‌عنوان راهنما آشکار می‌شه.
# از همون زیرساخت active_games استفاده می‌کنه (game_type="word_guess")
# پس با بقیه‌ی بازی‌های چندمرحله‌ای هم مهلت خودکار و هم انحصار «یه بازی
# فعال در لحظه» رو مشترکاً به ارث می‌بره.
# ---------------------------------------------------------------------------

WORD_GUESS_TIMEOUT_SECONDS = 120


def ensure_word_bank_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS word_bank (
                id SERIAL PRIMARY KEY,
                word TEXT UNIQUE NOT NULL
            )
            """
        )
        # ستون راهنما بعداً اضافه شد؛ برای دیتابیس‌هایی که از قبل این
        # جدول رو بدون این ستون ساخته بودن، این‌جوری بدون از‌دست‌رفتن
        # داده بهش اضافه می‌شه.
        cur.execute("ALTER TABLE word_bank ADD COLUMN IF NOT EXISTS hint TEXT")
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def add_word_hints(pairs):
    """
    هر عضو: (word, hint). راهنمای یه کلمه‌ی از قبل موجود تو بانک رو
    ست می‌کنه. مثل بقیه، دسته‌ای (۱۰۰تایی) کار می‌کنه تا اتصال کمی باز کنه.
    """
    clean_pairs = [(w.strip(), h) for w, h in pairs if w and w.strip()]
    chunk_size = 100
    for i in range(0, len(clean_pairs), chunk_size):
        chunk = clean_pairs[i:i + chunk_size]
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE word_bank SET hint = %s WHERE word = %s",
                [(h, w) for w, h in chunk],
            )
            conn.commit()
            cur.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)
    _invalidate_word_bank_cache()


def add_words_to_bank(words):
    """
    یه لیست از کلمه‌ها رو به بانک اضافه می‌کنه؛ تکراری‌ها بی‌سروصدا رد می‌شن.
    به‌جای یه اتصال جدا برای هر کلمه (که رو دیتابیس‌های با سقف اتصال کم،
    مثل پلن رایگان Supabase، می‌تونه سقف رو بزنه)، کلمه‌ها رو تو دسته‌های
    ۱۰۰تایی و با یه INSERT چندردیفی می‌فرسته — هم اتصال بمراتب کمتری باز
    می‌شه، هم اگه یه دسته مشکل داشت، دسته‌های قبلی از دست نمی‌رن.
    """
    clean_words = [w.strip() for w in words if w and w.strip()]
    added = 0
    chunk_size = 100
    for i in range(0, len(clean_words), chunk_size):
        chunk = clean_words[i:i + chunk_size]
        conn = get_conn()
        try:
            cur = conn.cursor()
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO word_bank (word) VALUES %s ON CONFLICT (word) DO NOTHING",
                [(w,) for w in chunk],
            )
            added += cur.rowcount
            conn.commit()
            cur.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)
    _invalidate_word_bank_cache()
    return added


def get_word_bank_count():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM word_bank")
        count = cur.fetchone()[0]
        cur.close()
        return count
    finally:
        put_conn(conn)


# هر دور بازی «حدس کلمه» و «اطلاعات عمومی» (تک‌نفره و دوئل) قبلاً یه
# SELECT ... ORDER BY RANDOM() مستقیم به دیتابیس می‌زد — یعنی هر بار
# یکی بازی رو شروع می‌کرد، یه اتصال جدا از استخر می‌گرفت. چون این
# بانک‌ها به‌ندرت تغییر می‌کنن (فقط وقتی ادمین کلمه/سؤال اضافه یا حذف
# می‌کنه)، کل لیست رو یه‌بار تو حافظه نگه می‌داریم و انتخاب رندوم رو
# خودِ پایتون انجام می‌ده — صفر اتصال دیتابیس برای هر بار شروع بازی.
_word_bank_cache = None
_word_bank_cache_loaded_at = 0
_WORD_BANK_CACHE_TTL = 600  # ۱۰ دقیقه

_trivia_cache = None
_trivia_cache_loaded_at = 0
_TRIVIA_CACHE_TTL = 600  # ۱۰ دقیقه


def _get_cached_word_bank():
    global _word_bank_cache, _word_bank_cache_loaded_at
    if _word_bank_cache is None or time.time() - _word_bank_cache_loaded_at > _WORD_BANK_CACHE_TTL:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT word, hint FROM word_bank WHERE hint IS NOT NULL")
            _word_bank_cache = cur.fetchall()
            cur.close()
        finally:
            put_conn(conn)
        _word_bank_cache_loaded_at = time.time()
    return _word_bank_cache


def _invalidate_word_bank_cache():
    global _word_bank_cache
    _word_bank_cache = None


def _get_cached_trivia_questions():
    global _trivia_cache, _trivia_cache_loaded_at
    if _trivia_cache is None or time.time() - _trivia_cache_loaded_at > _TRIVIA_CACHE_TTL:
        conn = get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM trivia_questions")
            _trivia_cache = [dict(r) for r in cur.fetchall()]
            cur.close()
        finally:
            put_conn(conn)
        _trivia_cache_loaded_at = time.time()
    return _trivia_cache


def _invalidate_trivia_cache():
    global _trivia_cache
    _trivia_cache = None


def start_word_guess_game(user_id, chat_id):
    existing = get_active_game(user_id)
    if existing:
        return False, {"reason": "already_active"}

    bank = _get_cached_word_bank()
    if not bank:
        return False, {"reason": "empty_bank"}
    word, hint = random.choice(bank)
    max_attempts = len(word)
    state = {"word": word, "hint": hint, "attempts_used": 0, "max_attempts": max_attempts}
    start_active_game(user_id, chat_id, "word_guess", 0, state, timeout_seconds=WORD_GUESS_TIMEOUT_SECONDS)
    max_reward = 200 + max_attempts * 150
    return True, {"length": max_attempts, "max_attempts": max_attempts, "hint": hint, "max_reward": max_reward}


def submit_word_guess(user_id, guess_word):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "word_guess":
        return False, {"reason": "no_game"}

    state = game["state"]
    word = state["word"]
    guess_word = guess_word.strip()

    if guess_word == word:
        end_active_game(user_id)
        attempts_used = state["attempts_used"]
        max_attempts = state["max_attempts"]
        remaining = max_attempts - attempts_used
        # هرچی با تلاش کمتری درست حدس بزنه، جایزه‌ی بیشتری می‌گیره
        reward = 200 + remaining * 150
        new_points = _credit(user_id, reward)
        return True, {
            "solved": True,
            "word": word,
            "reward": reward,
            "new_points": new_points,
            "attempts_used": attempts_used + 1,
        }

    attempts_used = state["attempts_used"] + 1
    max_attempts = state["max_attempts"]

    if attempts_used >= max_attempts:
        end_active_game(user_id)
        return True, {
            "solved": False,
            "finished": True,
            "word": word,
            "attempts_used": attempts_used,
            "max_attempts": max_attempts,
        }

    revealed = word[:attempts_used]
    state["attempts_used"] = attempts_used
    update_active_game_state(user_id, state)
    return True, {
        "solved": False,
        "finished": False,
        "revealed": revealed,
        "word_length": len(word),
        "attempts_used": attempts_used,
        "max_attempts": max_attempts,
    }


# ---------------------------------------------------------------------------
# 🧮 سرعت محاسبه — تک‌نفره، رایگان، مهارتی (نه شانسی). یه معادله‌ی ساده
# (جمع/تفریق/ضرب) نشون داده می‌شه؛ هرچی سریع‌تر جواب درست بدی، جایزه‌ی
# بیشتری می‌گیری. از همون زیرساخت active_games استفاده می‌کنه.
# ---------------------------------------------------------------------------

MATH_SPRINT_TIMEOUT_SECONDS = 20
MATH_SPRINT_MIN_REWARD = 80
MATH_SPRINT_MAX_REWARD = 300


def start_math_sprint(user_id, chat_id):
    existing = get_active_game(user_id)
    if existing:
        return False, {"reason": "already_active"}

    op = random.choice(["+", "-", "×"])
    if op == "×":
        # برای ضرب اعداد کوچیک‌تر تا محاسبه‌ی ذهنی واقع‌بینانه بمونه
        a = random.randint(2, 12)
        b = random.randint(2, 12)
        answer = a * b
    elif op == "+":
        a = random.randint(2, 60)
        b = random.randint(2, 60)
        answer = a + b
    else:
        a = random.randint(2, 60)
        b = random.randint(2, 60)
        if a < b:
            a, b = b, a
        answer = a - b

    state = {"answer": answer, "started_at": time.time()}
    start_active_game(user_id, chat_id, "math_sprint", 0, state, timeout_seconds=MATH_SPRINT_TIMEOUT_SECONDS)
    return True, {"a": a, "b": b, "op": op, "timeout": MATH_SPRINT_TIMEOUT_SECONDS}


def submit_math_sprint(user_id, guess_num):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "math_sprint":
        return False, {"reason": "no_game"}

    state = game["state"]
    answer = state["answer"]
    started_at = state.get("started_at", time.time())
    elapsed = max(0.0, time.time() - started_at)
    end_active_game(user_id)

    if guess_num != answer:
        return True, {"correct": False, "answer": answer}

    # جایزه‌ی خطی: هرچی به مهلت نزدیک‌تر جواب بدی، جایزه به حداقل نزدیک‌تر می‌شه
    remaining_ratio = max(0.0, 1 - (elapsed / MATH_SPRINT_TIMEOUT_SECONDS))
    reward_range = MATH_SPRINT_MAX_REWARD - MATH_SPRINT_MIN_REWARD
    reward = int(MATH_SPRINT_MIN_REWARD + remaining_ratio * reward_range)
    new_points = _credit(user_id, reward)
    return True, {
        "correct": True,
        "reward": reward,
        "elapsed": round(elapsed, 1),
        "new_points": new_points,
    }


def ban_word_from_bank(word):
    """
    یه کلمه‌ی مشکل‌دار رو کامل از بانک حذف می‌کنه. اگه همین لحظه کسی
    داشت دقیقاً رو همین کلمه بازی می‌کرد، بازیش لغو می‌شه و به‌عنوان
    جبران، کوینِ کامل (انگار همون اول درستش حدس زده بود) بهش داده
    می‌شه. برمی‌گردونه: (deleted: bool, affected: لیست کاربرهای جبران‌شده)
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("DELETE FROM word_bank WHERE word = %s", (word,))
        deleted = cur.rowcount > 0

        affected = []
        cur.execute(
            "SELECT user_id, chat_id, state FROM active_games WHERE game_type = 'word_guess'"
        )
        for row in cur.fetchall():
            if row["state"].get("word") != word:
                continue
            max_attempts = row["state"].get("max_attempts", len(word))
            compensation = 200 + max_attempts * 150
            cur.execute("DELETE FROM active_games WHERE user_id = %s", (row["user_id"],))
            cur.execute(
                "UPDATE meowie_users SET points = points + %s WHERE user_id = %s RETURNING points",
                (compensation, row["user_id"]),
            )
            new_points_row = cur.fetchone()
            affected.append(
                {
                    "user_id": row["user_id"],
                    "chat_id": row["chat_id"],
                    "compensation": compensation,
                    "new_points": new_points_row["points"] if new_points_row else None,
                }
            )

        conn.commit()
        cur.close()
        _invalidate_word_bank_cache()
        return deleted, affected
    finally:
        put_conn(conn)


# ---------------------------------------------------------------------------
# اطلاعات عمومی — تک‌نفره، رایگان. هر دور شامل ۵ سؤال چهارگزینه‌ای رندومه؛
# هر جواب درست کوین می‌ده، جواب کامل (۵ از ۵) یه پاداش تکمیلی هم داره.
# از همون زیرساخت active_games استفاده می‌کنه (game_type="trivia").
# ---------------------------------------------------------------------------

TRIVIA_QUESTIONS_PER_ROUND = 5
TRIVIA_TIMEOUT_SECONDS = 60
TRIVIA_CORRECT_REWARD = 100
TRIVIA_PERFECT_BONUS = 300


def ensure_trivia_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trivia_questions (
                id SERIAL PRIMARY KEY,
                question TEXT UNIQUE NOT NULL,
                option_a TEXT NOT NULL,
                option_b TEXT NOT NULL,
                option_c TEXT NOT NULL,
                option_d TEXT NOT NULL,
                correct_option CHAR(1) NOT NULL,
                category TEXT
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def add_trivia_questions(rows):
    """
    هر ردیف: (question, option_a, option_b, option_c, option_d, correct_option, category).
    تکراری‌ها (بر اساس متن سؤال) بی‌سروصدا رد می‌شن. مثل add_words_to_bank،
    تو دسته‌های ۱۰۰تایی و با یه INSERT چندردیفی فرستاده می‌شه تا اتصال
    کمتری باز بشه.
    """
    clean_rows = [
        (q.strip(), a, b, c, d, correct, category)
        for q, a, b, c, d, correct, category in rows
    ]
    added = 0
    chunk_size = 100
    for i in range(0, len(clean_rows), chunk_size):
        chunk = clean_rows[i:i + chunk_size]
        conn = get_conn()
        try:
            cur = conn.cursor()
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO trivia_questions
                    (question, option_a, option_b, option_c, option_d, correct_option, category)
                VALUES %s
                ON CONFLICT (question) DO NOTHING
                """,
                chunk,
            )
            added += cur.rowcount
            conn.commit()
            cur.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)
    _invalidate_trivia_cache()
    return added


def get_trivia_question_count():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trivia_questions")
        count = cur.fetchone()[0]
        cur.close()
        return count
    finally:
        put_conn(conn)


def start_trivia_game(user_id, chat_id):
    existing = get_active_game(user_id)
    if existing:
        return False, {"reason": "already_active"}

    pool_questions = _get_cached_trivia_questions()
    if len(pool_questions) < TRIVIA_QUESTIONS_PER_ROUND:
        return False, {"reason": "not_enough_questions"}

    questions = random.sample(pool_questions, TRIVIA_QUESTIONS_PER_ROUND)
    state = {"questions": questions, "current_index": 0, "correct_count": 0}
    start_active_game(user_id, chat_id, "trivia", 0, state, timeout_seconds=TRIVIA_TIMEOUT_SECONDS)
    q = questions[0]
    return True, {
        "question_number": 1,
        "total_questions": TRIVIA_QUESTIONS_PER_ROUND,
        "question": q["question"],
        "options": {"A": q["option_a"], "B": q["option_b"], "C": q["option_c"], "D": q["option_d"]},
        "category": q["category"],
    }


def submit_trivia_answer(user_id, letter_choice):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "trivia":
        return False, {"reason": "no_game"}

    letter_choice = letter_choice.strip().upper()
    if letter_choice not in ("A", "B", "C", "D"):
        return False, {"reason": "invalid_choice"}

    state = game["state"]
    idx = state["current_index"]
    questions = state["questions"]
    current_q = questions[idx]
    is_correct = letter_choice == current_q["correct_option"]

    if is_correct:
        state["correct_count"] += 1

    next_idx = idx + 1
    total = len(questions)

    if next_idx >= total:
        end_active_game(user_id)
        correct_count = state["correct_count"]
        reward = correct_count * TRIVIA_CORRECT_REWARD
        perfect = correct_count == total
        if perfect:
            reward += TRIVIA_PERFECT_BONUS
        new_points = _credit(user_id, reward)
        return True, {
            "finished": True,
            "was_correct": is_correct,
            "correct_option": current_q["correct_option"],
            "correct_count": correct_count,
            "total": total,
            "reward": reward,
            "perfect": perfect,
            "new_points": new_points,
        }

    state["current_index"] = next_idx
    update_active_game_state(user_id, state)
    next_q = questions[next_idx]
    return True, {
        "finished": False,
        "was_correct": is_correct,
        "correct_option": current_q["correct_option"],
        "question_number": next_idx + 1,
        "total_questions": total,
        "question": next_q["question"],
        "options": {"A": next_q["option_a"], "B": next_q["option_b"], "C": next_q["option_c"], "D": next_q["option_d"]},
        "category": next_q["category"],
    }


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

    won = multiplier > 0
    luck_flipped = False
    if not won:
        luck_used = _consume_pending_effect(user_id, "luck_boost")
        if luck_used and random.random() < LUCK_BOOST_FLIP_CHANCE:
            # وقتی طلسم باعث تبدیل باخت به برد می‌شه، حداقلیِ حالت برد
            # (همون ترکیب pair) رو می‌گیره، نه لزوماً بزرگ‌ترین جایزه.
            won = True
            luck_flipped = True
            multiplier = 1
            reel = _random_pair_reel()
    else:
        luck_used = _consume_pending_effect(user_id, "luck_boost")

    winnings = int(bet * multiplier) if won else 0
    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    insurance_used, refund = _apply_insurance(user_id, won, bet)
    if refund:
        new_points = _credit(user_id, refund)

    return True, {
        "reel": reel,
        "multiplier": multiplier,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
        "luck_used": luck_used,
        "luck_flipped": luck_flipped,
        "insurance_refund": refund,
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
    potential_multiplier = 2 if bet_type == "color" else 35
    if bet_type == "color" and bet_value in ("قرمز", "مشکی"):
        if bet_value == color:
            won = True
            multiplier = 2
    elif bet_type == "number":
        if bet_value == result:
            won = True
            multiplier = 35

    won, luck_used, luck_flipped = _apply_luck_boost(user_id, won)
    if luck_flipped:
        multiplier = potential_multiplier  # طلسم باخت رو به برد تبدیل کرد

    winnings = int(bet * multiplier) if won else 0
    new_points = _resolve_bet(user_id, bet, winnings)
    if new_points is None:
        return False, {"reason": "insufficient"}

    insurance_used, refund = _apply_insurance(user_id, won, bet)
    if refund:
        new_points = _credit(user_id, refund)

    return True, {
        "result": result,
        "color": color,
        "won": won,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
        "luck_used": luck_used,
        "luck_flipped": luck_flipped,
        "insurance_refund": refund,
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

    luck_flipped = False
    if multiplier == 0:
        luck_used = _consume_pending_effect(user_id, "luck_boost")
        if luck_used and random.random() < LUCK_BOOST_FLIP_CHANCE:
            # کوچیک‌ترین سطح واقعیِ برد (نه push) رو بهش می‌ده
            multiplier = 2
            zone = "red"
            luck_flipped = True
    else:
        luck_used = _consume_pending_effect(user_id, "luck_boost")

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

    insurance_used, refund = _apply_insurance(user_id, status == "loss", bet)
    if refund:
        new_points = _credit(user_id, refund)

    return True, {
        "zone": zone,
        "multiplier": multiplier,
        "status": status,
        "bet": bet,
        "winnings": winnings,
        "new_points": new_points,
        "luck_used": luck_used,
        "luck_flipped": luck_flipped,
        "insurance_refund": refund,
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
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # همون محافظت مهمی که برای مین‌یاب و دوئل گذاشتیم: اگه «بکش» دوبار
        # سریع بیاد، یا حلقه‌ی نگهداری هم‌زمان بخواد بازی رو به‌خاطر
        # تمومشدن مهلت ببنده، این قفل تضمین می‌کنه فقط یکی از این دو
        # مسیر واقعاً اعمال بشه، نه هر دو (که یعنی جایزه دوبار حساب بشه).
        cur.execute("SELECT * FROM active_games WHERE user_id = %s FOR UPDATE", (user_id,))
        game = cur.fetchone()
        if not game or game["game_type"] != "blackjack":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}

        state = game["state"]
        state["player"].append(_draw_card())
        player_value = _hand_value(state["player"])

        if player_value > 21:
            cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
            conn.commit()
            cur.close()
            return True, {
                "status": "bust",
                "player": state["player"],
                "dealer": state["dealer"],
                "bet": game["bet"],
                "winnings": 0,
                "new_points": get_points(user_id),
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
            "status": "in_progress",
            "player": state["player"],
            "dealer_shown": [state["dealer"][0]],
            "bet": game["bet"],
            "new_points": get_points(user_id),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def blackjack_stand(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM active_games WHERE user_id = %s FOR UPDATE", (user_id,))
        game = cur.fetchone()
        if not game or game["game_type"] != "blackjack":
            conn.rollback()
            cur.close()
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

        cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
        new_points = _credit(user_id, winnings) if winnings > 0 else get_points(user_id)

        return True, {
            "status": status,
            "player": state["player"],
            "dealer": dealer,
            "bet": bet,
            "winnings": winnings,
            "new_points": new_points,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


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
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM active_games WHERE user_id = %s FOR UPDATE", (user_id,))
        game = cur.fetchone()
        if not game or game["game_type"] != "crash":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}

        state = game["state"]
        start_time = datetime.datetime.fromisoformat(state["start_time"])
        elapsed = (datetime.datetime.utcnow() - start_time).total_seconds()
        crash_point = state["crash_point"]
        t_crash = _crash_time_to_point(crash_point)
        bet = game["bet"]

        if elapsed >= t_crash:
            cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
            conn.commit()
            cur.close()
            return True, {
                "status": "too_late",
                "multiplier": crash_point,
                "bet": bet,
                "winnings": 0,
                "new_points": get_points(user_id),
            }

        current_multiplier = round(1 + CRASH_GROWTH_RATE * elapsed, 2)
        winnings = int(bet * current_multiplier)
        cur.execute("DELETE FROM active_games WHERE user_id = %s", (user_id,))
        conn.commit()
        cur.close()
        new_points = _credit(user_id, winnings)

        return True, {
            "status": "cashed_out",
            "multiplier": current_multiplier,
            "bet": bet,
            "winnings": winnings,
            "new_points": new_points,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


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


# 🏪 فروشگاه (فاز ۱: زیرساخت — کاتالوگ آیتم‌ها + کیف کاربر)
# چرخه‌هایی که نباید با کوین خریداری بشن (مثلاً چرخه‌ی VIP که فقط با
# دستور ادمین «فعال کن» بعد از فروش دستی فعال می‌شه).
NON_PURCHASABLE_CYCLE_CODES = {"cycle_6"}

# نمادهایی که فقط باید از راه خرید/فعال‌سازی واقعی به دست بیان (تاج، نشان
# VIP، لقب بنیان‌گذار، افکت پروفایل...) — اگه این‌جا نبودن، هرکسی می‌تونست
# با «تنظیم نام 👑 علی» یا اسم شرکتش همون نماد رو مجانی برای خودش جعل کنه.
# این تنظیمات، مثل NON_PURCHASABLE_CYCLE_CODES بالا، سراسری‌ان: هر چرخه‌ی
# تازه‌ای که یه نماد پرستیژی جدید اضافه کرد، همون نماد رو باید همین‌جا هم
# اضافه کنی. علاوه بر این چندتا نماد مشخص، هر ایموجی دیگه‌ای هم به‌طور کلی
# تو اسم/اسم‌شرکت رد می‌شه، که کسی نتونه با یه ایموجی مشابه (نه لزوماً دقیقاً
# همون) خودش رو VIP یا تاج‌دار جا بزنه.
RESERVED_COSMETIC_SYMBOLS = ("👑", "💎", "🏆", "✨", "🕶️")

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # نمادها و پیکتوگرام‌ها
    "\U00002600-\U000027BF"   # نمادهای متفرقه + دینگ‌بت
    "\U0001F1E6-\U0001F1FF"   # پرچم‌ها
    "\U00002190-\U000021FF"   # فلش‌ها
    "\U00002B00-\U00002BFF"   # نمادهای متفرقه‌ی دیگه (مثل ⭐️)
    "\uFE0F"                  # variation selector (رنگی‌کردن ایموجی)
    "]+",
    flags=re.UNICODE,
)


def contains_reserved_cosmetic_symbol(text):
    """
    True یعنی این متن (اسم کاربر، اسم شرکت و...) شامل یه نماد پرستیژیه که
    فقط باید از راه فروشگاه به دست بیاد، یا اصلاً شامل هر ایموجی دیگه‌ایه.
    """
    if not text:
        return False
    if any(symbol in text for symbol in RESERVED_COSMETIC_SYMBOLS):
        return True
    return bool(_EMOJI_PATTERN.search(text))


# کلماتی که فقط تو «اسم» (نه هر پیام دیگه‌ای تو گروه) نباید باشن — برخلاف
# BLOCKED_NAME_WORDS بالا که هم برای اسم و هم به‌عنوان فیلتر سراسری پیام
# استفاده می‌شه، این‌ها اگه تو فیلتر سراسری هم می‌رفتن، دستورات واقعی مثل
# «انتقال کوین» یا «ریست کوین» رو کاملاً می‌بستن. برای همین یه لیست کاملاً
# جدا و مخصوص خودِ اسمه: جلوی این رو می‌گیره که کسی با اسمش (که همه‌جا
# نمایش داده می‌شه) بازار سیاه خرید/فروش کوین راه بندازه.
NAME_SPAM_WORDS = ("کوین", "میخرم", "می‌خرم", "میفروشم", "می‌فروشم")


def contains_username_spam(text):
    """
    True یعنی این اسم (کاربر یا شرکت) یا شامل کلمات تبلیغ خرید/فروش
    کوینه، یا شامل یه آیدی/هندل (با علامت @) — که یعنی کسی داره از
    طریق اسمش خودش رو تبلیغ یا معرفی می‌کنه، نه فقط اسم انتخاب می‌کنه.
    """
    if not text:
        return False
    if any(word in text for word in NAME_SPAM_WORDS):
        return True
    return "@" in text


def ensure_black_market_tables():
    """
    زیرساخت چرخه‌ی ۲ (بازار سیاه): بوست موقت سود + قراردادهای شانسیِ
    تأخیری. جدا از ensure_shop_tables چون این‌ها مکانیزم تازه‌ان، نه
    آیتم‌های ساده‌ی همیشگی.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_temp_income_boost (
                user_id TEXT PRIMARY KEY,
                boost_pct INTEGER NOT NULL,
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_lucky_contracts (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                stake INTEGER NOT NULL,
                multiplier NUMERIC NOT NULL,
                resolve_at TIMESTAMP NOT NULL,
                delivered BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


# نتیجه‌ی قرارداد شانسی: هر ردیف (احتمال، ضریب). جمع احتمال‌ها باید ۱ بشه.
# میانگین وزنی این توزیع عمداً کمی زیر ۱ (حدود ۰.۹۸)ه — یعنی رو کاغذ
# میانگین کمی ضرره، مثل هر بازی شانسی واقعی؛ وگرنه با خرید و صبرکردن
# می‌شد مطمئن سود کرد که اقتصاد بازی رو خراب می‌کنه.
LUCKY_CONTRACT_OUTCOMES = [
    (0.25, 0.5),
    (0.35, 0.8),
    (0.20, 1.0),
    (0.15, 1.5),
    (0.05, 3.0),
]


def _roll_lucky_contract_multiplier():
    roll = random.random()
    cumulative = 0.0
    for prob, multiplier in LUCKY_CONTRACT_OUTCOMES:
        cumulative += prob
        if roll <= cumulative:
            return multiplier
    return LUCKY_CONTRACT_OUTCOMES[-1][1]


def deliver_ready_lucky_contracts():
    """
    قراردادهایی که زمان‌شون رسیده رو تسویه می‌کنه: کوین حاصل از ضریب رو
    به کاربر می‌ده و لیستی از (user_id, stake, multiplier, payout) برمی‌گردونه
    تا صدازننده (بات) بتونه به هر کاربر پیوی بده که نتیجه‌ش چی شد.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM user_lucky_contracts WHERE resolve_at <= NOW() AND delivered = FALSE"
        )
        ready = cur.fetchall()
        results = []
        for row in ready:
            payout = round(row["stake"] * float(row["multiplier"]))
            cur.execute(
                "UPDATE meowie_users SET points = points + %s WHERE user_id = %s",
                (payout, row["user_id"]),
            )
            cur.execute(
                "UPDATE user_lucky_contracts SET delivered = TRUE WHERE id = %s",
                (row["id"],),
            )
            results.append({
                "user_id": row["user_id"],
                "stake": row["stake"],
                "multiplier": float(row["multiplier"]),
                "payout": payout,
            })
        conn.commit()
        cur.close()
        return results
    finally:
        put_conn(conn)


def cleanup_old_lucky_contracts():
    """قراردادهایی که مدت‌ها پیش تسویه شدن رو پاک می‌کنه که جدول همیشه بزرگ‌تر نشه."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM user_lucky_contracts WHERE delivered = TRUE AND resolve_at < NOW() - INTERVAL '7 days'"
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


# 🛡️⚔️ چرخه‌ی ۵ (جنگ مدیرعامل‌ها): سپر + حمله/بمب که «خزانه‌ی جمع‌نشده»‌ی
# شرکت هدف رو هدف می‌گیرن (همون منطق collect_cat_points)، به‌جای اینکه یه
# سیستم دوئل کاملاً جدا بسازیم.
COMPANY_RAID_STEAL_PCT = {
    "ad_attack": 0.35,      # VIP/پولی، قوی‌تر
    "economic_bomb": 0.15,  # کوینی، ضعیف‌تر و ریسکی
}
ECONOMIC_BOMB_SUCCESS_CHANCE = 0.6
CEO_DUEL_STAKE_MULTIPLIER = 2  # پاتِ نبرد نسبت به قیمت «مجوز مسابقه»


def ensure_company_shield_table():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_company_shield (
                user_id TEXT PRIMARY KEY,
                expires_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def is_company_shielded(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM user_company_shield WHERE user_id = %s AND expires_at > NOW()",
            (user_id,),
        )
        row = cur.fetchone()
        cur.close()
        return row is not None
    finally:
        put_conn(conn)


def _raid_company(attacker_id, target_id, keyword, expected_effect_type, steal_pct, check_shield=True):
    """
    هسته‌ی مشترک حمله‌تبلیغاتی/بمب‌اقتصادی: بخشی از خزانه‌ی جمع‌نشده‌ی
    (هنوز برداشت‌نشده‌ی) شرکتِ هدف رو می‌دزده. همه‌چیز تو یه تراکنش
    اتمیکه: اگه هدف سپر داشته باشه یا خزانه‌ش خالی باشه، آیتم اصلاً از
    کیف مهاجم کم نمی‌شه (پول واقعی/کوینِ ارزشمند هدر نره).
    """
    item = get_shop_item_by_keyword(keyword)
    if not item or item["effect_type"] != expected_effect_type:
        return False, {"reason": "not_found"}
    if str(target_id) == str(attacker_id):
        return False, {"reason": "self_target"}
    if check_shield and is_company_shielded(target_id):
        return False, {"reason": "target_shielded"}

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM cats WHERE owner_id = %s FOR UPDATE", (target_id,))
        cat = cur.fetchone()
        if not cat:
            conn.rollback()
            cur.close()
            return False, {"reason": "target_no_company"}

        now = datetime.datetime.utcnow()
        elapsed_hours = (now - cat["last_collect_at"]).total_seconds() / 3600
        per_hour = cat_production_per_hour(cat["rank"], cat["level"], target_id)
        capacity = cat_capacity(cat["rank"], cat["level"], target_id)
        pending = int(min(capacity, per_hour * elapsed_hours))

        if pending <= 0:
            conn.rollback()
            cur.close()
            return False, {"reason": "nothing_to_steal"}

        cur.execute(
            """
            UPDATE user_inventory
            SET quantity = quantity - 1
            WHERE user_id = %s AND item_code = %s AND quantity > 0
            RETURNING quantity
            """,
            (attacker_id, item["code"]),
        )
        if not cur.fetchone():
            conn.rollback()
            cur.close()
            return False, {"reason": "not_owned"}

        stolen = max(1, round(pending * steal_pct))

        # خزانه‌ی هدف کامل صفر می‌شه — بخشیش می‌ره پیش مهاجم، بقیه‌ش تو
        # حمله از بین می‌ره؛ همین باعث می‌شه یه «حمله» واقعاً حس ضرر بده،
        # نه یه انتقال بی‌درد.
        cur.execute("UPDATE cats SET last_collect_at = %s WHERE owner_id = %s", (now, target_id))
        cur.execute("UPDATE meowie_users SET points = points + %s WHERE user_id = %s", (stolen, attacker_id))
        conn.commit()
        cur.close()
        return True, {
            "item": item, "stolen": stolen, "pending_was": pending,
            "new_points": get_points(attacker_id),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def attempt_ad_attack(attacker_id, target_id, keyword):
    return _raid_company(attacker_id, target_id, keyword, "ad_attack", COMPANY_RAID_STEAL_PCT["ad_attack"])


def attempt_economic_bomb(attacker_id, target_id, keyword):
    item = get_shop_item_by_keyword(keyword)
    if not item or item["effect_type"] != "economic_bomb":
        return False, {"reason": "not_found"}
    if str(target_id) == str(attacker_id):
        return False, {"reason": "self_target"}
    if is_company_shielded(target_id):
        return False, {"reason": "target_shielded"}

    if random.random() > ECONOMIC_BOMB_SUCCESS_CHANCE:
        if not _consume_one_inventory_unit(attacker_id, item["code"]):
            return False, {"reason": "not_owned"}
        return True, {"item": item, "success": False, "stolen": 0}

    ok, result = _raid_company(
        attacker_id, target_id, keyword, "economic_bomb",
        COMPANY_RAID_STEAL_PCT["economic_bomb"], check_shield=False,
    )
    if ok:
        result["success"] = True
    return ok, result


def attempt_ceo_duel(challenger_id, target_id, keyword):
    item = get_shop_item_by_keyword(keyword)
    if not item or item["effect_type"] != "ceo_duel_license":
        return False, {"reason": "not_found"}
    if str(target_id) == str(challenger_id):
        return False, {"reason": "self_target"}

    challenger_cat = get_cat(challenger_id)
    target_cat = get_cat(target_id)
    if not challenger_cat or not target_cat:
        return False, {"reason": "missing_company"}

    if not _consume_one_inventory_unit(challenger_id, item["code"]):
        return False, {"reason": "not_owned"}

    challenger_power = cat_power_level(challenger_cat["rank"], challenger_cat["level"])
    target_power = cat_power_level(target_cat["rank"], target_cat["level"])
    total_power = max(1, challenger_power + target_power)
    # قدرت بیشتر شانس بیشتر می‌ده، ولی بین ۱۰٪ تا ۹۰٪ کلمپ می‌شه — یعنی
    # هیچ‌وقت نتیجه صد در صد از پیش‌معلوم نیست.
    challenger_win_chance = max(0.10, min(0.90, challenger_power / total_power))
    challenger_won = random.random() < challenger_win_chance
    winner_id = challenger_id if challenger_won else target_id
    loser_id = target_id if challenger_won else challenger_id

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT points FROM meowie_users WHERE user_id = %s FOR UPDATE", (loser_id,))
        loser_row = cur.fetchone()
        loser_points = loser_row[0] if loser_row else 0
        actual_pot = min(item["price"] * CEO_DUEL_STAKE_MULTIPLIER, loser_points)
        if actual_pot > 0:
            cur.execute("UPDATE meowie_users SET points = points - %s WHERE user_id = %s", (actual_pot, loser_id))
            cur.execute("UPDATE meowie_users SET points = points + %s WHERE user_id = %s", (actual_pot, winner_id))
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return True, {
        "item": item, "winner_id": winner_id, "loser_id": loser_id,
        "challenger_won": challenger_won, "pot": actual_pot,
    }


def get_spy_info(target_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT username, points, level FROM meowie_users WHERE user_id = %s",
            (target_id,),
        )
        user_row = cur.fetchone()
        if not user_row:
            cur.close()
            return None
        cur.execute(
            "SELECT rank, level FROM cats WHERE owner_id = %s",
            (target_id,),
        )
        cat_row = cur.fetchone()
        cur.close()
    finally:
        put_conn(conn)

    points = user_row["points"] or 0
    # دارایی «تقریبی» نشون می‌دیم نه دقیق — رند به نزدیک‌ترین ۱۰٪
    approx_points = max(0, round(points, -max(1, len(str(points)) - 2)))
    return {
        "username": user_row["username"],
        "account_level": user_row["level"],
        "approx_points": approx_points,
        "cat_rank": cat_row["rank"] if cat_row else None,
        "cat_level": cat_row["level"] if cat_row else None,
    }


def ensure_cosmetics_columns():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("ALTER TABLE meowie_users ADD COLUMN IF NOT EXISTS active_title TEXT")
        cur.execute("ALTER TABLE meowie_users ADD COLUMN IF NOT EXISTS active_theme TEXT")
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def ensure_shop_tables():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shop_items (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price INTEGER NOT NULL,
                category TEXT NOT NULL,
                effect_type TEXT NOT NULL,
                keyword TEXT,
                max_per_user INTEGER,
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
        # ستون‌های جدیدی که به نسخه‌های قبلی این جدول اضافه شدن
        cur.execute("ALTER TABLE shop_items ADD COLUMN IF NOT EXISTS keyword TEXT")
        cur.execute("ALTER TABLE shop_items ADD COLUMN IF NOT EXISTS max_per_user INTEGER")
        cur.execute("ALTER TABLE shop_items ADD COLUMN IF NOT EXISTS cycle_id INTEGER")
        cur.execute("ALTER TABLE shop_items ADD COLUMN IF NOT EXISTS is_vip_only BOOLEAN NOT NULL DEFAULT FALSE")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_inventory (
                user_id TEXT NOT NULL,
                item_code TEXT NOT NULL REFERENCES shop_items(code),
                quantity INTEGER NOT NULL DEFAULT 0,
                total_purchased INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, item_code)
            )
            """
        )
        cur.execute("ALTER TABLE user_inventory ADD COLUMN IF NOT EXISTS total_purchased INTEGER NOT NULL DEFAULT 0")

        # چرخه‌های فروشگاه: هر چرخه یه دسته‌ی مجزا از آیتم‌هاست. هر لحظه
        # فقط یه چرخه فعاله (تو shop_state)، بقیه از دید get_shop_items
        # مخفی می‌مونن، ولی موجودی خریدهای قبلی کاربرا دست‌نخورده می‌مونه.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shop_cycles (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT
            )
            """
        )
        cur.execute("ALTER TABLE shop_cycles ADD COLUMN IF NOT EXISTS description TEXT")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shop_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                current_cycle_id INTEGER REFERENCES shop_cycles(id),
                next_rotation_at TIMESTAMP,
                CHECK (id = 1)
            )
            """
        )

        shop_cycle_seed = [
            (
                "cycle_1", "💰 چرخه یک — سرمایه‌گذاری و شانس",
                "تمرکز: پایه و همیشگی 🐾\nارتقای دائمی شرکت، صندوقچه‌های شانس، و طلسم/بیمه‌ی کازینو — همون چیزایی که همیشه به‌کارت میان.",
            ),
            (
                "cycle_2", "🕶️ چرخه دو — بازار سیاه",
                "تمرکز: ریسک و تصمیم‌گیری 🎲\nاینجا باید بین سود بیشتر و ریسک بیشتر انتخاب کنی. یه معجون پرریسک، یه قرارداد امن‌تر، یه قرارداد شانسیِ تأخیری، و یه کارت جاسوسی برای زیرنظرگرفتن رقیب‌ها.",
            ),
            (
                "cycle_3", "🎪 چرخه سه — شهر بازی PawKing",
                "تمرکز: سرگرمی و مینی‌گیم 🎯\nبه‌زودی: بلیت‌های مینی‌گیمِ مستقل از اقتصاد شرکت — هدف‌گیری، مشت‌زنی، جعبه‌رمز و کارت شانس.",
            ),
            (
                "cycle_4", "👑 چرخه چهار — اشراف‌زادگان PawKing",
                "تمرکز: پرستیژ و شخصی‌سازی ✨\nتاج، لقب و تم اختصاصی برای پروفایلت — هیچ‌کدوم اقتصاد شرکتت رو تغییر نمی‌دن، فقط خاصت می‌کنن.",
            ),
            (
                "cycle_5", "⚔️ چرخه پنج — جنگ مدیرعامل‌ها",
                "تمرکز: رقابت بین بازیکن‌ها ⚔️\nآیتم‌هایی که فقط تو نبرد و دوئل به کارت میان — سپر شرکت، حمله‌ی تبلیغاتی و موارد مشابه.",
            ),
            (
                "cycle_6", "🚀 چرخه شش — فناوری آینده (VIP)",
                "تمرکز: کمیاب و Endgame 💎\nاین چرخه با کوین خریداری نمی‌شه — آیتم‌های خیلی کمیابش فقط با هماهنگی مستقیم با ادمین در دسترسن.",
            ),
        ]
        # چرخه‌ی شیش با کوین خریداری نمی‌شه — فقط با دستور ادمین
        # («فعال کن <کلیدواژه>» روی ریپلای) بعد از فروش دستی/پیوی فعال می‌شه.
        cycle_ids = {}
        for code, name, description in shop_cycle_seed:
            cur.execute(
                """
                INSERT INTO shop_cycles (code, name, description) VALUES (%s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description
                RETURNING id
                """,
                (code, name, description),
            )
            cycle_ids[code] = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO shop_state (id, current_cycle_id) VALUES (1, %s) ON CONFLICT (id) DO NOTHING",
            (cycle_ids["cycle_1"],),
        )

        # آیتم‌های فروشگاه — با ON CONFLICT DO UPDATE هر بار قیمت/توضیحشون
        # با آخرین مقداری که تو کد تعریف کردیم یکی می‌شه (حتی اگه قبلاً
        # با مقادیر قدیمی ساخته شده باشن).
        # فعلاً فقط چرخه‌ی ۱ آیتم داره؛ چرخه‌های ۲ تا ۶ عمداً خالی موندن
        # چون هرکدوم به مکانیزم تازه‌ای نیاز دارن (مینی‌گیم، قرارداد
        # زمان‌دار، شخصی‌سازی پروفایل، آیتم رقابتی، فروش VIP) که هنوز
        # ساخته نشدن — به‌مرور که هرکدوم آماده شد، آیتم‌هاشون اضافه می‌شن.
        shop_seed_items = [
            (
                "coffee", "☕ قهوه مدیرعامل",
                "یک‌بار مصرف: کولداون میوی بعدی رو صفر می‌کنه و هم‌زمان صندوق شرکتت رو (بدون در نظر گرفتن زمان) تا سقف پر می‌کنه.",
                3000, "مصرفی", "coffee_boost", "قهوه", 1, "cycle_1",
            ),
            (
                "accountant", "⚡ حسابدار حرفه‌ای",
                "ارتقای دائمی: سود شرکتت برای همیشه ۵٪ بیشتر می‌شه.",
                15000, "ارتقای شرکت", "permanent_income_5", "حسابدار", 1, "cycle_1",
            ),
            (
                "cfo", "💼 مدیر مالی",
                "ارتقای دائمی: ظرفیت خزانه‌ی شرکتت برای همیشه ۱۰٪ بیشتر می‌شه.",
                12000, "ارتقای شرکت", "permanent_capacity_10", "مدیرمالی", 1, "cycle_1",
            ),
            (
                "ads", "📈 تبلیغات",
                "ارتقای دائمی: سود شرکتت برای همیشه ۸٪ بیشتر می‌شه (با حسابدار جمع می‌شه).",
                20000, "ارتقای شرکت", "permanent_income_8", "تبلیغات", 1, "cycle_1",
            ),
            (
                "luck_charm", "🎲 طلسم شانس",
                "یک‌بار مصرف: تو بازی بعدی کازینو (سکه، تاس، بالا/پایین، اسلات، رولت، دارت)، ۱۰٪ شانس داره باخت رو به برد تبدیل کنه.",
                8000, "مصرفی", "luck_boost", "طلسم", None, "cycle_1",
            ),
            (
                "bet_insurance", "💣 بیمه سرمایه",
                "یک‌بار مصرف: اگه بازی بعدی کازینو رو باختی، نصف شرطت برمی‌گرده.",
                6000, "مصرفی", "bet_insurance", "بیمه", None, "cycle_1",
            ),
            (
                "box_common", "📦 صندوق معمولی",
                "یک‌بار مصرف: بازش کن ببین چی می‌گیری — کوین (یا شاید هیچی 😹).",
                2000, "صندوقچه", "lootbox_common", "صندوق معمولی", None, "cycle_1",
            ),
            (
                "box_silver", "📦 صندوق نقره‌ای",
                "یک‌بار مصرف: شانس بیشتر برای کوین خوب، حتی یه آیتم رایگان.",
                6000, "صندوقچه", "lootbox_silver", "صندوق نقره ای", None, "cycle_1",
            ),
            (
                "box_gold", "📦 صندوق طلایی",
                "یک‌بار مصرف: بهترین صندوقچه — کوین زیاد، آیتم، و شانس جکپات!",
                15000, "صندوقچه", "lootbox_gold", "صندوق طلایی", None, "cycle_1",
            ),
            (
                "wheel_ticket", "🎟️ بلیت گردونه شانس",
                "یک‌بار مصرف: چرخ رو بچرخون، جایزه‌ی تصادفی (تا جکپات) بگیر.",
                2500, "بلیت", "lucky_wheel", "گردونه", None, "cycle_1",
            ),

            # --- چرخه‌ی ۲ (بازار سیاه): ریسک و تصمیم‌گیری ---
            (
                "risk_potion", "🧪 معجون ریسک",
                "یک‌بار مصرف: سود شرکتت ۳ ساعت ۳۰٪ بیشتر می‌شه. باید تو همین ۳ ساعت برداشت بزنی وگرنه اثرش از دست می‌ره — ریسکشه!",
                24000, "بازار سیاه", "temp_income_boost_30_3", "معجون", None, "cycle_2",
            ),
            (
                "golden_contract", "💰 قرارداد طلایی",
                "یک‌بار مصرف: سود شرکتت ۱۲ ساعت ۸٪ بیشتر می‌شه. ریسک کمتر از معجون ریسک، ولی سودش هم کمتره.",
                27000, "بازار سیاه", "temp_income_boost_8_12", "قرارداد طلایی", None, "cycle_2",
            ),
            (
                "lucky_contract", "🎲 قرارداد شانسی",
                "یک‌بار مصرف: الان کوینشو می‌دی، ۶ ساعت دیگه معلوم می‌شه با چه ضریبی (۰.۵× تا ۳×) برات برمی‌گرده. تا اون‌موقع نمی‌دونی چی گیرت میاد!",
                21000, "بازار سیاه", "lucky_contract_6", "قرارداد شانسی", None, "cycle_2",
            ),
            (
                "spy_card", "🕵️ کارت جاسوسی",
                "یک‌بار مصرف: روی پیام یه بازیکن ریپلای بزن و استفاده کن — سطح، رتبه‌ی شرکت و دارایی تقریبی‌شو می‌بینی.",
                13000, "بازار سیاه", "spy_card", "جاسوسی", None, "cycle_2",
            ),

            # --- چرخه‌ی ۳ (شهر بازی PawKing): سرگرمی و مینی‌گیم، جدا از اقتصاد شرکت ---
            (
                "shooting_ticket", "🎯 بلیت تیراندازی",
                "یک‌بار مصرف: ۳ هدف، فقط یکیش جایزه داره. با «استفاده از تیراندازی <۱ تا ۳>» یکی رو انتخاب کن.",
                6000, "شهر بازی", "shooting_game_14000", "تیراندازی", None, "cycle_3",
            ),
            (
                "boxing_ticket", "🥊 بلیت مشت‌زنی",
                "یک‌بار مصرف: قدرت ضربه‌ت تصادفیه — ضعیف، معمولی، عالی یا PERFECT. جایزه‌ت بستگی به همون داره.",
                6000, "شهر بازی", "boxing_game", "مشت", None, "cycle_3",
            ),
            (
                "luck_card_ticket", "🃏 کارت شانس",
                "یک‌بار مصرف: ۳ کارت (قرمز، آبی، زرد) — یکی جایزه‌ی بزرگ داره، یکی متوسط، یکی پوچه. با «استفاده از کارت شانس <۱ تا ۳>» یکی رو باز کن.",
                6000, "شهر بازی", "luck_card_game", "کارت شانس", None, "cycle_3",
            ),
            (
                "codebreak_ticket", "🧩 جعبه رمز",
                "یک‌بار مصرف: یه رمز سه‌رقمی بین ۱۰۰ تا ۹۹۹ حدس بزن — بعد از هر حدس می‌گیم کدوم رقم‌ها درستن. ۴ تلاش داری، هر تلاش ۶۵ ثانیه وقت داری. با «استفاده از جعبه رمز» شروع کن و با «حدس <عدد سه‌رقمی>» حدس بزن.",
                15000, "شهر بازی", "codebreak_game", "جعبه رمز", None, "cycle_3",
            ),

            # --- چرخه‌ی ۴ (اشراف‌زادگان PawKing): پرستیژ و شخصی‌سازی، بدون اثر اقتصادی ---
            (
                "crown", "👑 تاج اختصاصی",
                "دائمی: یه تاج کنار اسمت تو پروفایل ظاهر می‌شه. فقط برای نشون دادن، اقتصاد شرکتت رو تغییر نمی‌ده.",
                40000, "پرستیژ", "cosmetic_crown", "تاج", 1, "cycle_4",
            ),
            (
                "profile_effect", "✨ افکت پروفایل",
                "دائمی: یه قاب تزئینی دور پروفایلت اضافه می‌شه.",
                35000, "پرستیژ", "cosmetic_effect", "افکت", 1, "cycle_4",
            ),
            (
                "title_wolf", "🏷️ لقب: گرگ بازار",
                "دائمی: این لقب زیر اسمت تو پروفایل نشون داده می‌شه (می‌تونی چند لقب بخری و با «لقبم» جابه‌جا کنی).",
                25000, "پرستیژ", "cosmetic_title", "گرگ بازار", 1, "cycle_4",
            ),
            (
                "title_coin_king", "🏷️ لقب: سلطان کوین",
                "دائمی: این لقب زیر اسمت تو پروفایل نشون داده می‌شه.",
                25000, "پرستیژ", "cosmetic_title", "سلطان کوین", 1, "cycle_4",
            ),
            (
                "title_gambler", "🏷️ لقب: قمارباز اعظم",
                "دائمی: این لقب زیر اسمت تو پروفایل نشون داده می‌شه.",
                25000, "پرستیژ", "cosmetic_title", "قمارباز اعظم", 1, "cycle_4",
            ),
            (
                "theme_diamond", "🎨 تم پروفایل: الماسی",
                "دائمی: ظاهر کارت پروفایلت عوض می‌شه (می‌تونی چند تم بخری و با «تمم» جابه‌جا کنی).",
                30000, "پرستیژ", "cosmetic_theme", "الماسی", 1, "cycle_4",
            ),
            (
                "theme_moon", "🎨 تم پروفایل: مهتابی",
                "دائمی: ظاهر کارت پروفایلت عوض می‌شه.",
                30000, "پرستیژ", "cosmetic_theme", "مهتابی", 1, "cycle_4",
            ),

            # --- چرخه‌ی ۵ (جنگ مدیرعامل‌ها): رقابت بین بازیکن‌ها ---
            # سپر و حمله قبلاً فقط با پول واقعی/هماهنگی ادمین فعال می‌شدن؛
            # چون این کنار کازینوی بات ریسک قانونی داشت، برگشتن به خرید با
            # خودِ کوینِ بازی (مثل بقیه‌ی آیتم‌های همین چرخه) — فقط با قیمت
            # بالاتر، چون تأثیرشون رو رقابت زیاده.
            (
                "company_shield", "🛡️ سپر شرکت",
                "برای ۴۸ ساعت، شرکتت در برابر «حمله تبلیغاتی» و «بمب اقتصادی» بقیه محافظت می‌شه.",
                45000, "جنگ مدیرعامل‌ها", "company_shield_48", "سپر شرکت", None, "cycle_5",
            ),
            (
                "ad_attack", "⚡ حمله تبلیغاتی",
                "یک‌بار مصرف، رو یه بازیکن دیگه (با ریپلای بزن «استفاده از حمله تبلیغاتی»). اگه هدف سپر نداشته باشه، بخش بزرگی از خزانه‌ی جمع‌نشده‌ی شرکتش رو می‌دزدی.",
                55000, "جنگ مدیرعامل‌ها", "ad_attack", "حمله تبلیغاتی", None, "cycle_5",
            ),
            (
                "economic_bomb", "💣 بمب اقتصادی",
                "یک‌بار مصرف، رو یه بازیکن دیگه (با ریپلای). ۶۰٪ شانس: بخش کوچیک‌تری از خزانه‌ی جمع‌نشده‌ی هدف رو می‌دزدی. ۴۰٪ شانس: بی‌نتیجه می‌ترکه و فقط خودِ آیتم از دست می‌ره.",
                38000, "جنگ مدیرعامل‌ها", "economic_bomb", "بمب اقتصادی", None, "cycle_5",
            ),
            (
                "ceo_duel_license", "🎟️ مجوز مسابقه",
                "یک‌بار مصرف، رو یه بازیکن دیگه (با ریپلای). یه نبرد اقتصادی فوری راه می‌اندازه؛ هرچی شرکتت قوی‌تر باشه شانس بردت بیشتره، ولی هیچ‌وقت تضمینی نیست. برنده یه پاتِ کوین از بازنده می‌بره.",
                30000, "جنگ مدیرعامل‌ها", "ceo_duel_license", "مجوز مسابقه", None, "cycle_5",
            ),

            # --- چرخه‌ی ۶ (فناوری آینده): VIP — دوتا از سه آیتم (نشان و لقب)
            # کاملاً کازمتیک‌ان و بدون اثر رو اقتصاد بازی، پس همچنان فقط با
            # پرداخت واقعی/هماهنگی ادمین فعال می‌شن. «سرمایه‌گذار سلطنتی» چون
            # سود دائمی (کوین قابل‌شرط‌بندی) تولید می‌کنه، برگشت به خرید با
            # خودِ کوین — با قیمت بالا، چون با ارتقای مشابه‌ی چرخه‌ی ۱ هم جمع می‌شه.
            (
                "vip_membership", "💎 عضویت VIP",
                "بعد از پرداخت واقعی و هماهنگی با ادمین: نشان 💎 دائمی تو پروفایلت فعال می‌شه و به مدت یک ماه به آپدیت‌های جدید و آیتم‌های ویژه هم دسترسی داری (تا وقتی همین آیتم تو کیفته).",
                0, "فروشگاه VIP", "cosmetic_vip_badge", "وی‌آی‌پی", 1, "cycle_6", True,
            ),
            (
                "vip_investor", "🏦 سرمایه‌گذار سلطنتی",
                "ارتقای دائمی — سود شرکتت برای همیشه ۲۵٪ بیشتر می‌شه (با ارتقاهای کوینی چرخه‌ی ۱ هم جمع می‌شه).",
                250000, "فروشگاه VIP", "permanent_income_25", "سرمایه‌گذار", 1, "cycle_6",
            ),
            (
                "founder_badge", "🏆 لقب: بنیان‌گذار PawKing",
                "بعد از پرداخت واقعی و هماهنگی با ادمین: یه لقب کمیاب و محدود زیر اسمت (فقط با صلاحدید مستقیم ادمین واگذار می‌شه).",
                0, "فروشگاه VIP", "cosmetic_title", "🏆 بنیان‌گذار PawKing", 1, "cycle_6", True,
            ),
        ]
        for entry in shop_seed_items:
            # آیتم‌های قدیمی ۹ فیلدی‌ان (بدون is_vip_only)؛ آیتم‌های تازه‌ی
            # چرخه‌ی ۵ که فقط با «فعال کن» در دسترسن، یه فیلد دهم اضافه دارن.
            if len(entry) == 9:
                code, name, description, price, category, effect_type, keyword, max_per_user, cycle_code = entry
                is_vip_only = False
            else:
                code, name, description, price, category, effect_type, keyword, max_per_user, cycle_code, is_vip_only = entry
            cur.execute(
                """
                INSERT INTO shop_items (code, name, description, price, category, effect_type, keyword, max_per_user, cycle_id, is_vip_only)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    price = EXCLUDED.price,
                    category = EXCLUDED.category,
                    effect_type = EXCLUDED.effect_type,
                    keyword = EXCLUDED.keyword,
                    max_per_user = EXCLUDED.max_per_user,
                    cycle_id = EXCLUDED.cycle_id,
                    is_vip_only = EXCLUDED.is_vip_only
                """,
                (code, name, description, price, category, effect_type, keyword, max_per_user, cycle_ids[cycle_code], is_vip_only),
            )

        # این سه‌تا از فروشگاه حذف شدن (فروش مستقیم کوین در برابر پول واقعی
        # کنار کازینو ریسک قانونی داشت) — چون از لیست بالا حذفشون کردیم،
        # دیگه با ON CONFLICT آپدیت نمی‌شن؛ این‌جا صریح غیرفعالشون می‌کنیم
        # که رو دیتابیسی که از قبل اجرا شده هم بلافاصله ناپدید بشن.
        cur.execute(
            "UPDATE shop_items SET is_active = FALSE WHERE code IN ('coin_pack_s', 'coin_pack_m', 'coin_pack_l')"
        )

        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


# فاصله‌های ممکن بین چرخش‌ها — هر بار یکی‌شون تصادفی انتخاب می‌شه، تا
# پلیر ندونه دقیقاً کِی چرخه عوض می‌شه (همون حس «ریسکِ» موردنظر).
SHOP_ROTATION_DURATIONS_SECONDS = [
    1 * 3600,       # ۱ ساعت
    2 * 3600,       # ۲ ساعت
    3 * 3600,       # ۳ ساعت
    24 * 3600,      # ۱ روز
    7 * 24 * 3600,  # ۱ هفته
]


def get_current_shop_cycle():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT c.id, c.code, c.name, c.description, s.next_rotation_at
            FROM shop_state s
            JOIN shop_cycles c ON c.id = s.current_cycle_id
            WHERE s.id = 1
            """
        )
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        put_conn(conn)


def force_rotate_shop_cycle():
    """مثل rotate_shop_cycle_if_needed ولی بدون توجه به زمان‌بندی — برای تست دستی ادمین."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT current_cycle_id FROM shop_state WHERE id = 1")
        state = cur.fetchone()
        if not state:
            cur.close()
            return None

        # فقط بین چرخه‌هایی که واقعاً آیتم فعال دارن می‌چرخیم — وگرنه ممکنه
        # پلیر با یه چرخه‌ی کاملاً خالی روبه‌رو بشه.
        cur.execute(
            """
            SELECT DISTINCT c.id, c.name
            FROM shop_cycles c
            JOIN shop_items s ON s.cycle_id = c.id AND s.is_active = TRUE
            """
        )
        all_cycles = cur.fetchall()
        if not all_cycles:
            cur.close()
            return None
        candidates = [c for c in all_cycles if c["id"] != state["current_cycle_id"]] or all_cycles
        new_cycle = random.choice(candidates)

        next_duration = random.choice(SHOP_ROTATION_DURATIONS_SECONDS)
        next_rotation_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=next_duration)

        cur.execute(
            "UPDATE shop_state SET current_cycle_id = %s, next_rotation_at = %s WHERE id = 1",
            (new_cycle["id"], next_rotation_at),
        )
        conn.commit()
        cur.close()
        return new_cycle
    finally:
        put_conn(conn)


def set_shop_cycle_by_number(cycle_number):
    """
    برخلاف force_rotate_shop_cycle (که یه چرخه‌ی تصادفی انتخاب می‌کنه)،
    این تابع دقیقاً همون چرخه‌ای که ادمین با شماره (۱ تا ۶) مشخص کرده
    رو فعال می‌کنه — بدون شانس و بدون اینکه سر از یه چرخه‌ی نامربوط دربیاره.
    خروجی: (True, cycle_dict) اگه موفق بود، (False, reason) اگه نه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        code = f"cycle_{cycle_number}"
        cur.execute("SELECT id, code, name FROM shop_cycles WHERE code = %s", (code,))
        cycle = cur.fetchone()
        if not cycle:
            cur.close()
            return False, "not_found"

        next_duration = random.choice(SHOP_ROTATION_DURATIONS_SECONDS)
        next_rotation_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=next_duration)
        cur.execute(
            "UPDATE shop_state SET current_cycle_id = %s, next_rotation_at = %s WHERE id = 1",
            (cycle["id"], next_rotation_at),
        )
        conn.commit()
        cur.close()
        return True, cycle
    finally:
        put_conn(conn)


def list_shop_cycles():
    """لیست همه‌ی چرخه‌ها به‌ترتیب شماره، برای نمایش به ادمین."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, code, name FROM shop_cycles ORDER BY id")
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        put_conn(conn)


def rotate_shop_cycle_if_needed():
    """
    اگه زمان چرخش رسیده باشه (یا هنوز هیچ‌وقت تنظیم نشده)، یه چرخه‌ی
    تصادفیِ متفاوت از چرخه‌ی فعلی رو انتخاب می‌کنه و یه بازه‌ی تصادفی
    از SHOP_ROTATION_DURATIONS_SECONDS برای چرخش بعدی می‌ذاره.
    فقط بین چرخه‌هایی که آیتم فعال دارن انتخاب می‌کنه، تا فروشگاه هیچ‌وقت
    خالی به پلیر نشون داده نشه.
    خروجی: چرخه‌ی جدید اگه چرخش انجام شد، وگرنه None.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT current_cycle_id, next_rotation_at FROM shop_state WHERE id = 1"
        )
        state = cur.fetchone()
        if not state:
            cur.close()
            return None

        now = datetime.datetime.utcnow()
        if state["next_rotation_at"] is not None and now < state["next_rotation_at"]:
            cur.close()
            return None

        cur.execute(
            """
            SELECT DISTINCT c.id, c.name
            FROM shop_cycles c
            JOIN shop_items s ON s.cycle_id = c.id AND s.is_active = TRUE
            """
        )
        all_cycles = cur.fetchall()
        if not all_cycles:
            cur.close()
            return None
        candidates = [c for c in all_cycles if c["id"] != state["current_cycle_id"]] or all_cycles
        new_cycle = random.choice(candidates)

        next_duration = random.choice(SHOP_ROTATION_DURATIONS_SECONDS)
        next_rotation_at = now + datetime.timedelta(seconds=next_duration)

        cur.execute(
            "UPDATE shop_state SET current_cycle_id = %s, next_rotation_at = %s WHERE id = 1",
            (new_cycle["id"], next_rotation_at),
        )
        conn.commit()
        cur.close()
        return new_cycle
    finally:
        put_conn(conn)


def get_shop_items():
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT s.*
            FROM shop_items s
            LEFT JOIN shop_state st ON st.id = 1
            WHERE s.is_active = TRUE
              AND (s.cycle_id IS NULL OR s.cycle_id = st.current_cycle_id)
            ORDER BY s.category, s.price
            """
        )
        items = cur.fetchall()
        cur.close()
        return items
    finally:
        put_conn(conn)


def get_all_shop_keywords():
    """همه‌ی کلیدواژه‌های فروشگاه، از همه‌ی چرخه‌ها (نه فقط چرخه‌ی فعلی) — برای دستورات ادمینی مثل «فعال کن» که باید هر آیتمی رو پیدا کنن."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT keyword FROM shop_items WHERE keyword IS NOT NULL")
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        put_conn(conn)


def get_shop_item_by_keyword(keyword):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM shop_items WHERE keyword = %s AND is_active = TRUE",
            (keyword.strip(),),
        )
        item = cur.fetchone()
        cur.close()
        return item
    finally:
        put_conn(conn)


def get_shop_item_by_code(code):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM shop_items WHERE code = %s", (code,))
        item = cur.fetchone()
        cur.close()
        return item
    finally:
        put_conn(conn)


def grant_item(user_id, keyword):
    """
    مثل buy_item ولی بدون کم کردن کوین — برای وقتی که ادمین می‌خواد
    یه آیتم رو دستی (مثلاً بعد از فروش پیوی برای چرخه‌ی VIP) به یه
    کاربر بده. سقف max_per_user همچنان رعایت می‌شه.
    """
    item = get_shop_item_by_keyword(keyword)
    if not item:
        return False, {"reason": "not_found"}
    code = item["code"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            INSERT INTO user_inventory (user_id, item_code, quantity, total_purchased)
            VALUES (%s, %s, 1, 1)
            ON CONFLICT (user_id, item_code) DO UPDATE
            SET quantity = user_inventory.quantity + 1,
                total_purchased = user_inventory.total_purchased + 1
            WHERE %s::int IS NULL OR user_inventory.total_purchased < %s
            RETURNING total_purchased
            """,
            (user_id, code, item["max_per_user"], item["max_per_user"]),
        )
        inv_row = cur.fetchone()
        if not inv_row:
            conn.rollback()
            cur.close()
            return False, {"reason": "limit_reached", "max_per_user": item["max_per_user"]}
        conn.commit()
        cur.close()

        # لقب/تم/نشان VIP هم مثل خریدهای عادی خودکار فعال بشه
        if item["effect_type"] == "cosmetic_title":
            _set_active_cosmetic(user_id, "active_title", item["keyword"])
        elif item["effect_type"] == "cosmetic_theme":
            _set_active_cosmetic(user_id, "active_theme", item["keyword"])

        return True, {"item": item}
    finally:
        put_conn(conn)


def buy_item(user_id, keyword):
    item = get_shop_item_by_keyword(keyword)
    if not item:
        return False, {"reason": "not_found"}
    code = item["code"]

    # قفل VIP سطح تک‌آیتم — مستقل از چرخه، هر آیتمی تو هر چرخه‌ای می‌تونه
    # با این فلگ فقط دستی (با «فعال کن») قابل‌دریافت باشه، نه با کوین.
    if item.get("is_vip_only"):
        return False, {"reason": "vip_only"}

    if item.get("cycle_id"):
        conn_check = get_conn()
        try:
            cur_check = conn_check.cursor()
            cur_check.execute(
                """
                SELECT c.code
                FROM shop_cycles c
                JOIN shop_state st ON st.current_cycle_id = c.id
                WHERE c.id = %s AND st.id = 1
                """,
                (item["cycle_id"],),
            )
            active_row = cur_check.fetchone()
            cur_check.close()
        finally:
            put_conn(conn_check)
        if not active_row:
            return False, {"reason": "cycle_inactive"}
        if active_row[0] in NON_PURCHASABLE_CYCLE_CODES:
            return False, {"reason": "vip_only"}

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # این یه UPSERT اتمیکه: اگه max_per_user محدودیتی داشته باشه و
        # کاربر قبلاً به سقفش رسیده باشه، شرط WHERE برقرار نمی‌شه و هیچ
        # ردیفی برنمی‌گرده — یعنی هنوز هیچ پولی از کاربر کم نشده.
        cur.execute(
            """
            INSERT INTO user_inventory (user_id, item_code, quantity, total_purchased)
            VALUES (%s, %s, 1, 1)
            ON CONFLICT (user_id, item_code) DO UPDATE
            SET quantity = user_inventory.quantity + 1,
                total_purchased = user_inventory.total_purchased + 1
            WHERE %s::int IS NULL OR user_inventory.total_purchased < %s
            RETURNING total_purchased
            """,
            (user_id, code, item["max_per_user"], item["max_per_user"]),
        )
        inv_row = cur.fetchone()
        if not inv_row:
            conn.rollback()
            cur.close()
            return False, {"reason": "limit_reached", "max_per_user": item["max_per_user"]}

        cur.execute(
            """
            UPDATE meowie_users
            SET points = points - %s
            WHERE user_id = %s AND points >= %s
            RETURNING points
            """,
            (item["price"], user_id, item["price"]),
        )
        prow = cur.fetchone()
        if not prow:
            conn.rollback()
            cur.close()
            return False, {"reason": "insufficient", "price": item["price"]}

        conn.commit()
        cur.close()

        # لقب یا تمی که تازه خریده شد، خودکار فعال می‌شه (کاربر بعداً هم
        # می‌تونه با «لقبم»/«تمم» بین چیزایی که داره جابه‌جا بشه).
        if item["effect_type"] == "cosmetic_title":
            _set_active_cosmetic(user_id, "active_title", item["keyword"])
        elif item["effect_type"] == "cosmetic_theme":
            _set_active_cosmetic(user_id, "active_theme", item["keyword"])

        return True, {"item": item, "new_points": prow["points"]}
    finally:
        put_conn(conn)


def _set_active_cosmetic(user_id, column, value):
    if column not in ("active_title", "active_theme"):
        raise ValueError("invalid cosmetic column")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE meowie_users SET {column} = %s WHERE user_id = %s", (value, user_id))
        conn.commit()
        cur.close()
    finally:
        put_conn(conn)


def get_owned_cosmetic_keywords(user_id, effect_type):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.keyword
            FROM user_inventory i
            JOIN shop_items s ON s.code = i.item_code
            WHERE i.user_id = %s AND i.quantity > 0 AND s.effect_type = %s
            """,
            (user_id, effect_type),
        )
        rows = [r[0] for r in cur.fetchall()]
        cur.close()
        return rows
    finally:
        put_conn(conn)


def set_active_title(user_id, keyword):
    if keyword not in get_owned_cosmetic_keywords(user_id, "cosmetic_title"):
        return False
    _set_active_cosmetic(user_id, "active_title", keyword)
    return True


def set_active_theme(user_id, keyword):
    if keyword not in get_owned_cosmetic_keywords(user_id, "cosmetic_theme"):
        return False
    _set_active_cosmetic(user_id, "active_theme", keyword)
    return True


def get_profile_cosmetics(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT active_title, active_theme FROM meowie_users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        cur.execute(
            """
            SELECT DISTINCT s.effect_type
            FROM user_inventory i
            JOIN shop_items s ON s.code = i.item_code
            WHERE i.user_id = %s AND i.quantity > 0
              AND s.effect_type IN ('cosmetic_crown', 'cosmetic_effect', 'cosmetic_vip_badge')
            """,
            (user_id,),
        )
        flags = {r[0] for r in cur.fetchall()}
        cur.close()
    finally:
        put_conn(conn)
    return {
        "active_title": row["active_title"] if row else None,
        "active_theme": row["active_theme"] if row else None,
        "has_crown": "cosmetic_crown" in flags,
        "has_effect": "cosmetic_effect" in flags,
        "has_vip_badge": "cosmetic_vip_badge" in flags,
    }


def get_inventory(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT s.code, s.name, s.description, s.keyword, s.category, i.quantity
            FROM user_inventory i
            JOIN shop_items s ON s.code = i.item_code
            WHERE i.user_id = %s AND i.quantity > 0
            ORDER BY s.category, s.name
            """,
            (user_id,),
        )
        items = cur.fetchall()
        cur.close()
        return items
    finally:
        put_conn(conn)


# 📦 صندوقچه‌ها و 🎟️ گردونه‌ی شانس (فاز ۴ فروشگاه)
LOOTBOX_TABLES = {
    "lootbox_common": {
        "nothing_chance": 0.15,
        "coin_range": (500, 3000),
        "item_chance": 0.0,
        "items": [],
    },
    "lootbox_silver": {
        "nothing_chance": 0.05,
        "coin_range": (2000, 8000),
        "item_chance": 0.10,
        "items": ["coffee"],
    },
    "lootbox_gold": {
        # نکته: item_chance علاوه بر coin_range عمل می‌کنه، نه به‌جاش (تو
        # _roll_lootbox_reward کوین همیشه رول می‌شه، آیتم فقط اضافه‌ست).
        # نسخه‌ی قبلی این جدول رو کاغذ حدود ۶٪ به نفع بازیکن بود (یعنی
        # فروشگاه ضرر می‌کرد)؛ این نسخه حدود ۲۵٪ به نفع خونه‌ست — هنوز
        # بهترین صندوقه، ولی دیگه ضررده نیست.
        "nothing_chance": 0.0,
        "coin_range": (4000, 14000),
        "item_chance": 0.15,
        "items": ["coffee", "luck_charm", "bet_insurance"],
        "jackpot_chance": 0.04,
        "jackpot_amount": 45000,
    },
}


def _roll_lootbox_reward(effect_type):
    table = LOOTBOX_TABLES[effect_type]
    roll = random.random()

    if roll < table["nothing_chance"]:
        return {"kind": "nothing", "coins": 0, "bonus_item_code": None}

    if table.get("jackpot_chance") and random.random() < table["jackpot_chance"]:
        return {"kind": "jackpot", "coins": table["jackpot_amount"], "bonus_item_code": None}

    bonus_item_code = None
    if table["item_chance"] and random.random() < table["item_chance"]:
        bonus_item_code = random.choice(table["items"])

    low, high = table["coin_range"]
    coins = random.randint(low, high)
    kind = "item" if bonus_item_code else "coins"
    return {"kind": kind, "coins": coins, "bonus_item_code": bonus_item_code}


# هر ردیف: (احتمال، حداقل جایزه، حداکثر جایزه) — جمع احتمال‌ها باید ۱ بشه.
# نسخه‌ی قبلی این جدول، رو کاغذ، به‌طور میانگین ۲.۱ برابر قیمت بلیت (۲۵۰۰)
# پرداخت می‌کرد — یعنی خودِ فروشگاه داشت رو هر چرخش ضرر می‌کرد و آروم‌آروم
# اقتصاد کوین رو باد می‌کرد. این نسخه یه احتمال «پوچ» واقعی داره و
# بازه‌ها/جکپاتش طوری تنظیم شده که ارزش امیدی حدود ۷۶٪ قیمت بلیت بشه
# (تقریباً هم‌رده‌ی بقیه‌ی بازی‌های شانسی فروشگاه، نه رایگان و نه ضررده).
LUCKY_WHEEL_OUTCOMES = [
    (0.20, 0, 0),           # پوچ
    (0.35, 300, 1000),
    (0.25, 1000, 2500),
    (0.13, 2500, 5000),
    (0.05, 5000, 9000),
    (0.02, 20000, 20000),   # جکپات
]


LUCKY_WHEEL_JACKPOT_AMOUNT = 20000


def _spin_lucky_wheel():
    roll = random.random()
    cumulative = 0.0
    for prob, low, high in LUCKY_WHEEL_OUTCOMES:
        cumulative += prob
        if roll <= cumulative:
            coins = random.randint(low, high)
            if coins == 0:
                kind = "nothing"
            elif low == LUCKY_WHEEL_JACKPOT_AMOUNT:
                kind = "jackpot"
            else:
                kind = "coins"
            return {"kind": kind, "coins": coins}
    # ایمنی: اگه به‌خاطر گرد‌کردن اعشار به هیچ‌کدوم نخورد، آخرین حالت رو بده
    low, high = LUCKY_WHEEL_OUTCOMES[-1][1], LUCKY_WHEEL_OUTCOMES[-1][2]
    return {"kind": "coins", "coins": random.randint(low, high)}


def _consume_one_inventory_unit(user_id, item_code):
    """یه واحد از این آیتم رو از کیف کاربر کم می‌کنه. موفقیت/شکست برمی‌گردونه."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE user_inventory
            SET quantity = quantity - 1
            WHERE user_id = %s AND item_code = %s AND quantity > 0
            RETURNING quantity
            """,
            (user_id, item_code),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return False
        conn.commit()
        cur.close()
        return True
    finally:
        put_conn(conn)


def use_spy_card(user_id, target_id, keyword):
    item = get_shop_item_by_keyword(keyword)
    if not item or item["effect_type"] != "spy_card":
        return False, {"reason": "not_found"}
    if target_id == user_id:
        return False, {"reason": "self_target"}

    if not _consume_one_inventory_unit(user_id, item["code"]):
        return False, {"reason": "not_owned"}

    info = get_spy_info(target_id)
    if not info:
        return False, {"reason": "target_not_found"}
    return True, {"item": item, "info": info}


def play_shooting_game(user_id, keyword, choice):
    item = get_shop_item_by_keyword(keyword)
    if not item or not item["effect_type"].startswith("shooting_game_"):
        return False, {"reason": "not_found"}
    if choice not in (1, 2, 3):
        return False, {"reason": "bad_choice"}
    if not _consume_one_inventory_unit(user_id, item["code"]):
        return False, {"reason": "not_owned"}

    reward = int(item["effect_type"].rsplit("_", 1)[1])
    winning_slot = random.randint(1, 3)
    won = choice == winning_slot
    if won:
        add_points(user_id, reward)
    return True, {
        "item": item,
        "won": won,
        "winning_slot": winning_slot,
        "reward": reward if won else 0,
        "new_points": get_points(user_id),
    }


# تیر (احتمال، برچسب فارسی، ضریب نسبت به قیمت بلیت)
BOXING_TIERS = [
    (0.40, "😿 ضعیف", 0.0),
    (0.35, "👊 معمولی", 0.7),
    (0.20, "💪 عالی", 1.5),
    (0.05, "🔥 PERFECT", 3.3),
]


def play_boxing_game(user_id, keyword):
    item = get_shop_item_by_keyword(keyword)
    if not item or item["effect_type"] != "boxing_game":
        return False, {"reason": "not_found"}
    if not _consume_one_inventory_unit(user_id, item["code"]):
        return False, {"reason": "not_owned"}

    roll = random.random()
    cumulative = 0.0
    label, multiplier = BOXING_TIERS[-1][1], BOXING_TIERS[-1][2]
    for prob, tier_label, tier_multiplier in BOXING_TIERS:
        cumulative += prob
        if roll <= cumulative:
            label, multiplier = tier_label, tier_multiplier
            break

    reward = round(item["price"] * multiplier)
    if reward:
        add_points(user_id, reward)
    return True, {"item": item, "label": label, "reward": reward, "new_points": get_points(user_id)}


# کارت شانس: سه تا خونه با این سه تا نتیجه، به‌صورت تصادفی قاطی می‌شن
LUCK_CARD_OUTCOMES = [("💎 جایزه‌ی بزرگ", 12000), ("🪙 جایزه‌ی متوسط", 3000), ("😿 پوچ", 0)]


def play_luck_card_game(user_id, keyword, choice):
    item = get_shop_item_by_keyword(keyword)
    if not item or item["effect_type"] != "luck_card_game":
        return False, {"reason": "not_found"}
    if choice not in (1, 2, 3):
        return False, {"reason": "bad_choice"}
    if not _consume_one_inventory_unit(user_id, item["code"]):
        return False, {"reason": "not_owned"}

    shuffled = LUCK_CARD_OUTCOMES.copy()
    random.shuffle(shuffled)
    label, reward = shuffled[choice - 1]
    if reward:
        add_points(user_id, reward)
    return True, {"item": item, "label": label, "reward": reward, "new_points": get_points(user_id)}


# این بازی فیدبک‌محوره (شبیه مسترمایند)، پس یه بازیکن معمولی تقریباً همیشه
# می‌بردش — یعنی نباید مثل بازی‌های شانسی باهاش رفتار کرد. تعداد تلاش کمتر
# و جایزه‌ی پایین‌تر (همراه با قیمت بلیت بالاتر تو shop_seed_items) باعث
# می‌شه بردِ تقریباً-تضمینی هم سود منطقی بده، نه پول مجانی.
CODEBREAK_ATTEMPTS = 4
CODEBREAK_REWARD = 28000


def start_codebreak_game(user_id, chat_id, keyword):
    item = get_shop_item_by_keyword(keyword)
    if not item or item["effect_type"] != "codebreak_game":
        return False, {"reason": "not_found"}
    if get_active_game(user_id):
        return False, {"reason": "game_in_progress"}
    if not _consume_one_inventory_unit(user_id, item["code"]):
        return False, {"reason": "not_owned"}

    secret = f"{random.randint(100, 999)}"
    start_active_game(
        user_id, chat_id, "codebreak", 0,
        {"secret": secret, "attempts_left": CODEBREAK_ATTEMPTS},
    )
    return True, {"item": item, "attempts_left": CODEBREAK_ATTEMPTS}


def submit_codebreak_guess(user_id, guess):
    game = get_active_game(user_id)
    if not game or game["game_type"] != "codebreak":
        return False, {"reason": "no_game"}
    if len(guess) != 3 or not guess.isdigit():
        return False, {"reason": "bad_guess"}

    state = game["state"]
    secret = state["secret"]
    feedback = ["✅" if guess[i] == secret[i] else "❌" for i in range(3)]
    solved = guess == secret
    attempts_left = state["attempts_left"] - 1

    if solved:
        end_active_game(user_id)
        add_points(user_id, CODEBREAK_REWARD)
        return True, {"solved": True, "feedback": feedback, "reward": CODEBREAK_REWARD, "new_points": get_points(user_id)}

    if attempts_left <= 0:
        end_active_game(user_id)
        return True, {"solved": False, "feedback": feedback, "attempts_left": 0, "secret": secret}

    update_active_game_state(user_id, {"secret": secret, "attempts_left": attempts_left})
    return True, {"solved": False, "feedback": feedback, "attempts_left": attempts_left}


def use_item(user_id, keyword):
    item = get_shop_item_by_keyword(keyword)
    if not item:
        return False, {"reason": "not_found"}
    if item["category"] == "ارتقای شرکت":
        # ارتقاهای دائمی شرکت مصرفی نیستن؛ همیشه فعالن، پس نباید با
        # «استفاده از» کم بشن و بونسشون از بین بره.
        return False, {"reason": "permanent_active"}
    code = item["code"]

    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            UPDATE user_inventory
            SET quantity = quantity - 1
            WHERE user_id = %s AND item_code = %s AND quantity > 0
            RETURNING quantity
            """,
            (user_id, code),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return False, {"reason": "not_owned"}

        if item["effect_type"] == "coffee_boost":
            # ۱. کولداون میو صفر می‌شه
            cur.execute(
                "UPDATE meowie_users SET last_meow_at = NULL WHERE user_id = %s",
                (user_id,),
            )
            # ۲. صندوق شرکت (اگه داشته باشه) بدون در نظر گرفتن زمان، تا
            # سقف ظرفیت پر می‌شه — با عقب کشیدن last_collect_at، دفعه‌ی
            # بعد که برداشت سود بزنه انگار کامل از زمان پر شدنش گذشته.
            cur.execute(
                """
                UPDATE cats
                SET last_collect_at = NOW() - (%s || ' hours')::interval
                WHERE owner_id = %s
                """,
                (CAT_CAPACITY_HOURS, user_id),
            )
        elif item["effect_type"] in ("luck_boost", "bet_insurance"):
            # این دوتا فوری اثر نمی‌ذارن؛ فقط برای «بازی بعدی کازینو»
            # فعال می‌مونن تا خودِ تابع بازی مصرفش کنه.
            cur.execute(
                """
                INSERT INTO user_pending_effects (user_id, effect_type)
                VALUES (%s, %s)
                ON CONFLICT (user_id, effect_type) DO NOTHING
                """,
                (user_id, item["effect_type"]),
            )
        elif item["effect_type"] in LOOTBOX_TABLES:
            reward = _roll_lootbox_reward(item["effect_type"])
            if reward["coins"] > 0:
                cur.execute(
                    "UPDATE meowie_users SET points = points + %s WHERE user_id = %s",
                    (reward["coins"], user_id),
                )
            if reward["bonus_item_code"]:
                cur.execute(
                    """
                    INSERT INTO user_inventory (user_id, item_code, quantity, total_purchased)
                    VALUES (%s, %s, 1, 0)
                    ON CONFLICT (user_id, item_code) DO UPDATE
                    SET quantity = user_inventory.quantity + 1
                    """,
                    (user_id, reward["bonus_item_code"]),
                )
            conn.commit()
            cur.close()
            reward["new_points"] = get_points(user_id)
            return True, {"item": item, "reward": reward}
        elif item["effect_type"] == "lucky_wheel":
            reward = _spin_lucky_wheel()
            cur.execute(
                "UPDATE meowie_users SET points = points + %s WHERE user_id = %s",
                (reward["coins"], user_id),
            )
            conn.commit()
            cur.close()
            reward["new_points"] = get_points(user_id)
            return True, {"item": item, "reward": reward}
        elif item["effect_type"].startswith("temp_income_boost_"):
            # effect_type مثلاً: temp_income_boost_30_3 یعنی ۳۰٪ برای ۳ ساعت
            try:
                _, _, _, pct_str, hours_str = item["effect_type"].split("_")
                pct, hours = int(pct_str), int(hours_str)
            except ValueError:
                conn.rollback()
                cur.close()
                return False, {"reason": "bad_effect"}
            cur.execute(
                """
                INSERT INTO user_temp_income_boost (user_id, boost_pct, expires_at)
                VALUES (%s, %s, NOW() + (%s || ' hours')::interval)
                ON CONFLICT (user_id) DO UPDATE
                SET boost_pct = EXCLUDED.boost_pct, expires_at = EXCLUDED.expires_at
                """,
                (user_id, pct, hours),
            )
            conn.commit()
            cur.close()
            return True, {"item": item, "boost_pct": pct, "hours": hours}
        elif item["effect_type"].startswith("company_shield_"):
            # effect_type مثلاً: company_shield_48 یعنی ۴۸ ساعت محافظت
            try:
                hours = int(item["effect_type"].rsplit("_", 1)[1])
            except ValueError:
                conn.rollback()
                cur.close()
                return False, {"reason": "bad_effect"}
            cur.execute(
                """
                INSERT INTO user_company_shield (user_id, expires_at)
                VALUES (%s, NOW() + (%s || ' hours')::interval)
                ON CONFLICT (user_id) DO UPDATE SET expires_at = EXCLUDED.expires_at
                """,
                (user_id, hours),
            )
            conn.commit()
            cur.close()
            return True, {"item": item, "hours": hours}
        elif item["effect_type"].startswith("lucky_contract_"):
            # effect_type مثلاً: lucky_contract_5 یعنی نتیجه ۵ ساعت دیگه معلوم می‌شه
            try:
                hours = int(item["effect_type"].rsplit("_", 1)[1])
            except ValueError:
                conn.rollback()
                cur.close()
                return False, {"reason": "bad_effect"}
            multiplier = _roll_lucky_contract_multiplier()
            cur.execute(
                """
                INSERT INTO user_lucky_contracts (user_id, stake, multiplier, resolve_at)
                VALUES (%s, %s, %s, NOW() + (%s || ' hours')::interval)
                """,
                (user_id, item["price"], multiplier, hours),
            )
            conn.commit()
            cur.close()
            return True, {"item": item, "hours": hours}

        conn.commit()
        cur.close()
        return True, {"item": item}
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
        elif game_type == "trivia":
            pool_questions = _get_cached_trivia_questions()
            if len(pool_questions) < TRIVIA_QUESTIONS_PER_ROUND:
                conn.rollback()
                cur.close()
                return False, {"reason": "not_enough_questions"}
            state = {
                "questions": random.sample(pool_questions, TRIVIA_QUESTIONS_PER_ROUND),
                "current_index": 0,
                "answered_this_round": {},
                "score": {str(challenger_id): 0, str(target_id): 0},
            }
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


def expire_duel_game(game_id):
    """
    به‌جای اعتماد به دیتای قدیمی‌ای که get_expired_duel_games() قبلاً
    خونده بود، اینجا دوباره با قفل می‌خونیم و دوباره چک می‌کنیم که واقعاً
    هنوز منقضیه یا نه — چون ممکنه دقیقاً همون لحظه بازیکن خودش حرکتش رو
    زده باشه و مهلتش تازه شده باشه. بدون این چک دوباره، دقیقاً همین‌جا
    بود که یه پات می‌توسنت هم از مسیر حرکت بازیکن هم از مسیر انقضا،
    دوبار پرداخت بشه.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM duel_games WHERE id = %s FOR UPDATE", (game_id,))
        game = cur.fetchone()
        if not game:
            conn.rollback()
            cur.close()
            return None  # یه مسیر دیگه (مثلاً خودِ بازیکن) قبل از ما تمومش کرده

        if game["expires_at"] > datetime.datetime.utcnow():
            conn.rollback()
            cur.close()
            return None  # درست همین الان تازه شده؛ دیگه منقضی نیست

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
        elif game_type == "trivia":
            p1, p2 = str(game["player1_id"]), str(game["player2_id"])
            score = state.get("score", {})
            p1_score = score.get(p1, 0)
            p2_score = score.get(p2, 0)
            if p1_score == p2_score:
                winner_id = random.choice([p1, p2])
            else:
                winner_id = p1 if p1_score > p2_score else p2
            loser_id = p2 if winner_id == p1 else p1
        else:
            loser_id = None
            winner_id = None

        cur.execute("DELETE FROM duel_games WHERE id = %s", (game["id"],))
        conn.commit()
        cur.close()
        new_points = _credit(winner_id, pot) if winner_id else None

        return {
            "winner_id": winner_id,
            "loser_id": loser_id,
            "pot": pot,
            "new_points": new_points,
            "chat_id": game["chat_id"],
            "game_type": game_type,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def _tictactoe_winner(board):
    for a, b, c in TICTACTOE_WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def duel_tictactoe_move(user_id, cell_index):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # نکته‌ی مهم و حیاتی: اینجا باید قفل بگیریم، چون همین لحظه که
        # بازیکن حرکت می‌زنه، ممکنه حلقه‌ی نگهداری (که هر ~۱ ثانیه تو یه
        # ترد کاملاً جدا اجرا می‌شه) دقیقاً همین بازی رو به‌خاطر تمومشدن
        # مهلت، «منقضی» تشخیص بده و جایزه رو به یکی دیگه بده — بدون قفل،
        # هر دو مسیر (حرکت واقعی بازیکن + انقضای هم‌زمان) می‌تونستن
        # مستقل از هم جایزه رو واریز کنن، یعنی یه پات، دوبار (یا بیشتر)
        # پرداخت بشه. دقیقاً همین باعث شده بود یکی با سوءاستفاده از این
        # هم‌زمانی، کوین نامتناسب و زیادی به دست بیاره.
        cur.execute(
            "SELECT * FROM duel_games WHERE (player1_id = %s OR player2_id = %s) FOR UPDATE",
            (str(user_id), str(user_id)),
        )
        game = cur.fetchone()
        if not game or game["game_type"] != "tictactoe":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}

        state = game["state"]
        if state["turn"] != str(user_id):
            conn.rollback()
            cur.close()
            return False, {"reason": "not_your_turn", "current_turn": state["turn"]}
        if cell_index < 0 or cell_index > 8:
            conn.rollback()
            cur.close()
            return False, {"reason": "invalid_cell"}
        if state["board"][cell_index] is not None:
            conn.rollback()
            cur.close()
            return False, {"reason": "cell_taken"}

        symbol = state["symbols"][str(user_id)]
        state["board"][cell_index] = symbol

        other_id = game["player2_id"] if str(user_id) == str(game["player1_id"]) else game["player1_id"]
        pot = game["bet"] * 2

        winner_symbol = _tictactoe_winner(state["board"])
        board_full = all(c is not None for c in state["board"])

        if winner_symbol:
            cur.execute("DELETE FROM duel_games WHERE id = %s", (game["id"],))
            conn.commit()
            cur.close()
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
            cur.execute("DELETE FROM duel_games WHERE id = %s", (game["id"],))
            conn.commit()
            cur.close()
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
        cur.execute(
            "UPDATE duel_games SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval WHERE id = %s",
            (psycopg2.extras.Json(state), DUEL_TURN_TIMEOUT_SECONDS, game["id"]),
        )
        conn.commit()
        cur.close()
        return True, {
            "status": "in_progress",
            "board": state["board"],
            "next_turn": other_id,
            "chat_id": game["chat_id"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def duel_trivia_answer(user_id, letter_choice):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM duel_games WHERE (player1_id = %s OR player2_id = %s) FOR UPDATE",
            (str(user_id), str(user_id)),
        )
        game = cur.fetchone()
        if not game or game["game_type"] != "trivia":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}

        state = game["state"]
        idx = state["current_index"]
        questions = state["questions"]
        total = len(questions)
        q = questions[idx]
        answered = state.get("answered_this_round", {})
        uid_str = str(user_id)

        if uid_str in answered:
            conn.rollback()
            cur.close()
            return False, {"reason": "already_answered"}

        p1, p2 = str(game["player1_id"]), str(game["player2_id"])
        other_id = p2 if uid_str == p1 else p1
        pot = game["bet"] * 2
        score = state.get("score", {p1: 0, p2: 0})

        is_correct = letter_choice == q["correct_option"]
        round_winner = None

        if is_correct:
            score[uid_str] = score.get(uid_str, 0) + 1
            state["score"] = score
            round_winner = uid_str
            advance = True
        else:
            answered[uid_str] = letter_choice
            state["answered_this_round"] = answered
            advance = other_id in answered

        if not advance:
            # هنوز حریف جواب نداده؛ سؤال عوض نمی‌شه، فقط منتظرش می‌مونیم
            cur.execute(
                "UPDATE duel_games SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval WHERE id = %s",
                (psycopg2.extras.Json(state), DUEL_TURN_TIMEOUT_SECONDS, game["id"]),
            )
            conn.commit()
            cur.close()
            return True, {
                "finished": False,
                "was_correct": False,
                "round_advanced": False,
                "chat_id": game["chat_id"],
            }

        next_index = idx + 1
        state["current_index"] = next_index
        state["answered_this_round"] = {}

        if next_index >= total:
            cur.execute("DELETE FROM duel_games WHERE id = %s", (game["id"],))
            conn.commit()
            cur.close()
            p1_score = score.get(p1, 0)
            p2_score = score.get(p2, 0)
            tie = p1_score == p2_score
            winner_id = random.choice([p1, p2]) if tie else (p1 if p1_score > p2_score else p2)
            new_points = _credit(winner_id, pot)
            return True, {
                "finished": True,
                "was_correct": is_correct,
                "correct_option": q["correct_option"],
                "round_winner": round_winner,
                "p1_id": p1,
                "p2_id": p2,
                "p1_score": p1_score,
                "p2_score": p2_score,
                "winner_id": winner_id,
                "tie": tie,
                "pot": pot,
                "new_points": new_points,
                "chat_id": game["chat_id"],
            }

        next_q = questions[next_index]
        cur.execute(
            "UPDATE duel_games SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval WHERE id = %s",
            (psycopg2.extras.Json(state), DUEL_TURN_TIMEOUT_SECONDS, game["id"]),
        )
        conn.commit()
        cur.close()
        return True, {
            "finished": False,
            "was_correct": is_correct,
            "correct_option": q["correct_option"],
            "round_winner": round_winner,
            "round_advanced": True,
            "question_number": next_index + 1,
            "total_questions": total,
            "question": next_q["question"],
            "options": {
                "A": next_q["option_a"],
                "B": next_q["option_b"],
                "C": next_q["option_c"],
                "D": next_q["option_d"],
            },
            "category": next_q["category"],
            "score": {p1: score.get(p1, 0), p2: score.get(p2, 0)},
            "chat_id": game["chat_id"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def duel_flower_hide(user_id, hand):
    """hand: 'چپ' یا 'راست'. باید فقط تو پی‌وی بات صدا زده بشه (چک این‌کار تو bot.py انجام می‌شه)."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM duel_games WHERE (player1_id = %s OR player2_id = %s) FOR UPDATE",
            (str(user_id), str(user_id)),
        )
        game = cur.fetchone()
        if not game or game["game_type"] != "guessflower":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}

        state = game["state"]
        if state["hider_id"] != str(user_id):
            conn.rollback()
            cur.close()
            return False, {"reason": "not_hider"}
        if state["hidden"] is not None:
            conn.rollback()
            cur.close()
            return False, {"reason": "already_hidden"}
        if hand not in ("چپ", "راست"):
            conn.rollback()
            cur.close()
            return False, {"reason": "invalid_hand"}

        state["hidden"] = hand
        cur.execute(
            "UPDATE duel_games SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval WHERE id = %s",
            (psycopg2.extras.Json(state), DUEL_TURN_TIMEOUT_SECONDS, game["id"]),
        )
        conn.commit()
        cur.close()
        return True, {"chat_id": game["chat_id"], "round": state["round"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def duel_flower_guess(user_id, guess):
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # همون محافظت مهمی که تو دوز گذاشتیم: قفل کردن ردیف تا آخر
        # تراکنش، تا حلقه‌ی نگهداری (بررسی انقضای مهلت) نتونه هم‌زمان
        # با این حرکت واقعی بازیکن، جایزه رو دوباره واریز کنه.
        cur.execute(
            "SELECT * FROM duel_games WHERE (player1_id = %s OR player2_id = %s) FOR UPDATE",
            (str(user_id), str(user_id)),
        )
        game = cur.fetchone()
        if not game or game["game_type"] != "guessflower":
            conn.rollback()
            cur.close()
            return False, {"reason": "no_game"}

        state = game["state"]
        if state["guesser_id"] != str(user_id):
            conn.rollback()
            cur.close()
            return False, {"reason": "not_guesser"}
        if state["hidden"] is None:
            conn.rollback()
            cur.close()
            return False, {"reason": "not_hidden_yet"}
        if guess not in ("چپ", "راست"):
            conn.rollback()
            cur.close()
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
            cur.execute("DELETE FROM duel_games WHERE id = %s", (game["id"],))
            conn.commit()
            cur.close()
            new_points = _credit(guesser_id, pot)
            return True, {
                "status": "guesser_win", "correct": correct, "revealed_hand": revealed_hand,
                "score_hider": score_hider, "score_guesser": score_guesser,
                "pot": pot, "new_points": new_points, "chat_id": game["chat_id"],
                "hider_id": hider_id, "guesser_id": guesser_id,
            }

        if score_hider >= 2:
            cur.execute("DELETE FROM duel_games WHERE id = %s", (game["id"],))
            conn.commit()
            cur.close()
            new_points = _credit(hider_id, pot)
            return True, {
                "status": "hider_win", "correct": correct, "revealed_hand": revealed_hand,
                "score_hider": score_hider, "score_guesser": score_guesser,
                "pot": pot, "new_points": new_points, "chat_id": game["chat_id"],
                "hider_id": hider_id, "guesser_id": guesser_id,
            }

        state["round"] += 1
        state["hidden"] = None
        cur.execute(
            "UPDATE duel_games SET state = %s, expires_at = NOW() + (%s || ' seconds')::interval WHERE id = %s",
            (psycopg2.extras.Json(state), DUEL_TURN_TIMEOUT_SECONDS, game["id"]),
        )
        conn.commit()
        cur.close()
        return True, {
            "status": "next_round", "correct": correct, "revealed_hand": revealed_hand,
            "score_hider": score_hider, "score_guesser": score_guesser,
            "round": state["round"], "chat_id": game["chat_id"],
            "hider_id": hider_id, "guesser_id": guesser_id,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

