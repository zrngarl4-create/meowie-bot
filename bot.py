import os
import re
import time
import datetime
import threading
import traceback
import requests
from flask import Flask, request, jsonify

import db

TOKEN = os.environ.get("BOT_TOKEN", "TOKEN_KHODETO_INJA_BEZAR")
BASE_URL = f"https://botapi.rubika.ir/v3/{TOKEN}"

# اگه فعال باشه، به‌جای پولینگ (هر ۱ ثانیه پرسیدن "پیام جدید داری؟")،
# روبیکا خودش فوری پیام رو به سرورمون پوش می‌کنه (سریع‌تر و بدون تأخیر پولینگ).
# چون این بخش از API روبیکا قبلاً امتحان نشده، اگه کار نکرد فقط این متغیر
# محیطی رو تو Render بذار روی false تا خودکار برگردیم به همون پولینگ قدیمی.
USE_WEBHOOK = os.environ.get("USE_WEBHOOK", "true").lower() == "true"
WEBHOOK_PATH = "/webhook"

app = Flask(__name__)

SET_NAME_PREFIX = "تنظیم نام"
TRANSFER_PREFIX = "انتقال کوین"
CHARGE_PREFIX = "شارژ"
RESET_COMMAND = "ریست کوین"
RESET_NAME_COMMAND = "ریست نام"
CLEANUP_NAMES_COMMAND = "پاکسازی اسم‌های نامناسب"
CLEANUP_COSMETIC_NAMES_COMMAND = "پاکسازی اسم‌های جعلی"
ADMIN_HELP_COMMAND = "دستورات ادمین"
GRANT_ITEM_PREFIX = "فعال کن"
SHOP_CYCLE_FORCE_COMMAND = "چرخش فروشگاه"
CODEBREAK_GUESS_PREFIX = "حدس"
EQUIP_TITLE_PREFIX = "لقبم"
EQUIP_THEME_PREFIX = "تمم"
GROUP_COUNT_COMMAND = "تعداد گروه"
PLAYER_COUNT_COMMAND = "تعداد پلیر"
HELP_COMMANDS = ("راهنما", "راهنما پاوکینگ", "آموزشگاه")

MAFIA_START_COMMAND = "مافیا تفنگدار"
MAFIA_JOIN_COMMAND = "ثبت نام"
MAFIA_FORCE_START_COMMAND = "استارت"
MAFIA_CANCEL_COMMAND = "لغو مافیا"

CASINO_MENU_COMMAND = "کازینو"
COINFLIP_PREFIX = "شیر یا خط"
DICE_PREFIX = "تاس"
HIGH_WORD = "بالا"
LOW_WORD = "پایین"
SLOT_PREFIX = "اسلات"
ROULETTE_PREFIX = "رولت"
BLACKJACK_PREFIX = "بلک جک"
HIT_WORD = "بکش"
STAND_WORD = "بایست"
DART_PREFIX = "دارت"
CASHOUT_WORD = "برداشت"
MINES_PREFIX = "مین یاب"
MINES_OPEN_PREFIX = "باز کن"

COMPANY_STATUS_WORDS = ("شرکت", "شرکت‌هام", "شرکت هام")
COMPANY_BUY_COMMAND = "تأسیس شرکت"
COMPANY_BUY_WORDS = ("تأسیس شرکت", "تاسیس شرکت")
COMPANY_NAME_PREFIX = "تنظیم شرکت"
COMPANY_UPGRADE_WORDS = ("توسعه شرکت", "ارتقا")
COMPANY_COLLECT_COMMAND = "برداشت سود شرکت"

PROFILE_SELF_COMMAND = "پروفایلم"
PROFILE_OTHER_COMMAND = "پروفایلش"

SHOP_COMMAND = "فروشگاه"
BUY_ITEM_PREFIX = "خرید"
INVENTORY_COMMAND = "کیف من"
USE_ITEM_PREFIX = "استفاده از"
GIFT_CODE_PREFIX = "کد"
DAILY_GIFT_CODE_ADMIN_COMMAND = "پاداش روزانه"
AD_BROADCAST_PREFIX = "تبلیغ"
AD_DELETE_COMMAND = "پاک کردن تبلیغ"

DUEL_REQUEST_PREFIX = "درخواست"
DUEL_GAME_NAMES = {"گل یا پوچ": "guessflower", "دوز": "tictactoe"}
DUEL_ACCEPT_WORD = "قبول"
DUEL_DECLINE_WORD = "رد"
DUEL_MOVE_PREFIX = "حرکت"
LEFT_WORD = "چپ"
RIGHT_WORD = "راست"

CURRENCY_NAME = "کوین پیشی"

# آیدی‌های ادمین از یه متغیر محیطی خونده می‌شه، نه از تو کد، که امن‌تره
# و بدون تغییر کد می‌شه عوضش کرد. تو Render باید مقدارش رو جدا با کاما بذاری:
# ADMIN_USER_IDS=u0Gw4KT048853a398113c76238074183,u0KTX2g022ecd0746f4b971ce14de578
ADMIN_USER_IDS = {
    uid.strip() for uid in os.environ.get("ADMIN_USER_IDS", "").split(",") if uid.strip()
}

CAT_TITLES = {
    1: "🏚️ غرفه دست‌ساز",
    2: "🛒 دکه محلی",
    3: "🏪 فروشگاه کوچک",
    4: "🏬 بازارچه",
    5: "🏢 شرکت نوپا",
    6: "💼 شرکت خصوصی",
    7: "🏭 کارخانه",
    8: "🚚 هلدینگ حمل‌ونقل",
    9: "🏦 گروه سرمایه‌گذاری",
    10: "🌆 مجتمع تجاری",
    11: "⚙️ صنایع بزرگ",
    12: "🚢 کنسرسیوم بازرگانی",
    13: "🌍 شرکت بین‌المللی",
    14: "💎 هلدینگ ثروت",
    15: "🏛️ امپراتوری اقتصادی",
    16: "🚀 صنایع فضایی",
    17: "👑 شرکت سلطنتی",
    18: "🌌 ابرشرکت جهانی",
    19: "💠 ابرهلدینگ PawKing",
    20: "👑 تاجران افسانه‌ای",
}

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def normalize_digits(text):
    """اعداد فارسی/عربی داخل متن رو به عدد انگلیسی تبدیل می‌کنه."""
    result = []
    for ch in text:
        if ch in PERSIAN_DIGITS:
            result.append(str(PERSIAN_DIGITS.index(ch)))
        elif ch in ARABIC_DIGITS:
            result.append(str(ARABIC_DIGITS.index(ch)))
        else:
            result.append(ch)
    return "".join(result)


def format_number(n):
    return f"{int(n):,}"


def progress_bar(current, total, length=5):
    if not total:
        return "▰" * length
    ratio = max(0, min(1, current / total))
    filled = round(length * ratio)
    filled = max(0, min(length, filled))
    return "▰" * filled + "▱" * (length - filled)


def rank_emoji(rank):
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    if rank in medals:
        return medals[rank]
    if rank == 10:
        return "🔟"
    return "".join(f"{d}\ufe0f\u20e3" for d in str(rank))


def format_cooldown(seconds):
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}:{secs:02d}"


@app.route("/")
def home():
    return "Meowie bot is alive!"


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print("پیام وبهوک دریافت شد:", data)
        # روبیکا احتمالاً یا خودِ آپدیت رو مستقیم می‌فرسته، یا زیر یه کلید
        # مثل "update" بسته‌بندیش می‌کنه؛ هر دو حالت رو پوشش می‌دیم.
        update = data.get("update") if isinstance(data, dict) and "update" in data else data
        process_update(update)
    except Exception:
        print("خطا در پردازش وبهوک:")
        traceback.print_exc()
    return jsonify({"status": "ok"})


def register_webhook():
    service_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_BASE_URL")
    if not service_url:
        print("⚠️ آدرس سرویس برای ثبت وبهوک پیدا نشد؛ اگه رو Render نیستی، متغیر WEBHOOK_BASE_URL رو دستی ست کن.")
        return
    webhook_url = service_url.rstrip("/") + WEBHOOK_PATH
    try:
        # روبیکا متدی به اسم setWebHook نداره؛ متد درست updateBotEndpoints هست
        # و یه فیلد type هم لازم داره تا مشخص کنه این آدرس برای چه نوع
        # رویدادیه. ReceiveUpdate یعنی پیام‌های معمولی (متن/ریپلای/...).
        resp = requests.post(
            f"{BASE_URL}/updateBotEndpoints",
            json={"url": webhook_url, "type": "ReceiveUpdate"},
            timeout=15,
        )
        print("نتیجه‌ی ثبت وبهوک:", resp.status_code, resp.text)
    except Exception as e:
        print("خطا در ثبت وبهوک:", e)


def get_updates(offset_id=None):
    payload = {"limit": 10}
    if offset_id:
        payload["offset_id"] = offset_id
    try:
        resp = requests.post(f"{BASE_URL}/getUpdates", json=payload, timeout=15)
        if resp.status_code != 200:
            print("خطای HTTP در دریافت آپدیت:", resp.status_code, resp.text)
            return None
        return resp.json()
    except Exception as e:
        print("خطا در دریافت آپدیت:", e)
        return None


def _utf16_length(s):
    """
    روبیکا طول متن برای metadata رو با واحد UTF-16 می‌شمره، نه با len() پایتون.
    اموجی‌های خارج از BMP (اکثر اموجی‌هایی که استفاده می‌کنیم) هرکدوم ۲ واحد
    UTF-16 حساب می‌شن ولی len() پایتون فقط ۱ می‌شمردشون. همین اختلاف باعث
    می‌شد بازه‌ی بولد زودتر از پایان واقعی متن تموم بشه و آخر جمله بولد نباشه.
    """
    return len(s.encode("utf-16-le")) // 2


def _send_message_attempt(payload, timeout=10):
    """
    یه تلاش تکی برای ارسال. برمی‌گردونه یکی از این سه حالت:
    - ("ok", None): واقعاً تحویل داده شد
    - ("permanent_fail", detail): خطای دائمی (مثلاً chat_id نامعتبر) —
      دوباره امتحان کردنش فایده نداره، فقط وقت تلف می‌کنه
    - ("transient_fail", detail): خطای موقتی (قطعی شبکه/تایم‌اوت/۵xx) —
      ارزش یه تلاش دیگه رو داره
    """
    try:
        resp = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=timeout)
    except Exception as e:
        return "transient_fail", e

    if resp.status_code != 200:
        if 500 <= resp.status_code < 600:
            return "transient_fail", f"HTTP {resp.status_code}: {resp.text}"
        return "permanent_fail", f"HTTP {resp.status_code}: {resp.text}"

    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("status") not in (None, "OK", "ok", "done", "Done"):
        return "permanent_fail", data
    return "ok", None


def send_message(chat_id, text, reply_to_message_id=None):
    # همون روشی که قبلاً واقعاً کار می‌کرد: نیازی به ** نیست، کل متن پیام
    # یه بازه‌ی Bold از ابتدا تا انتها می‌گیره (از_index=0 تا طول کامل متن
    # بر حسب UTF-16).
    #
    # نکته‌ی مهم (باگی که باعث کندی شدید کل بات شده بود): قبلاً برای هر
    # پیام، حتی وقتی روبیکا صراحتاً می‌گفت chat_id نامعتبره (خطای دائمی)،
    # بازم ۳ بار پشت‌سرهم با تایم‌اوت ۱۵ ثانیه تلاش می‌شد — یعنی هر پیوی
    # ناموفق تا ~۴۵ ثانیه کل پردازش اون پیام (که همزمانه) رو قفل می‌کرد.
    # الان فقط خطاهای واقعاً موقتی (قطعی شبکه، تایم‌اوت، ۵xx) دوباره
    # امتحان می‌شن؛ خطای دائمی همون تلاش اول فوری fail می‌شه.
    #
    # مقدار برگشتی: True اگه پیام واقعاً تحویل داده شد، False در غیر این
    # صورت. صداکننده‌هایی که قبل از این تابع یه تغییر برگشت‌ناپذیر تو
    # دیتابیس انجام دادن (مثلاً کم کردن شرط و شروع یه بازی) باید این
    # مقدار رو چک کنن.
    metadata = None
    if text:
        metadata = {
            "meta_data_parts": [
                {"from_index": 0, "length": _utf16_length(text), "type": "Bold"}
            ]
        }

    bold_payload = {"chat_id": chat_id, "text": text}
    if metadata:
        bold_payload["metadata"] = metadata
    if reply_to_message_id:
        bold_payload["reply_to_message_id"] = reply_to_message_id

    plain_payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        plain_payload["reply_to_message_id"] = reply_to_message_id

    result, detail = _send_message_attempt(bold_payload)
    if result == "ok":
        return True
    if result == "permanent_fail":
        print("ارسال پیام رد شد (خطای دائمی، دوباره امتحان نمی‌کنیم):", detail)
        return False
    print("خطای موقتی در ارسال پیام (تلاش با متادیتای بولد):", detail)

    # فقط چون تلاش اول موقتی (نه دائمی) شکست خورد، یه تلاش دوم با فرمت
    # ساده (بدون بولد) می‌کنیم.
    result, detail = _send_message_attempt(plain_payload)
    if result == "ok":
        return True
    if result == "permanent_fail":
        print("ارسال پیام رد شد (خطای دائمی، تلاش ساده):", detail)
        return False
    print("خطای موقتی در ارسال پیام (تلاش ساده #1):", detail)

    time.sleep(1.5)
    result, detail = _send_message_attempt(plain_payload)
    if result == "ok":
        return True
    print("ارسال پیام نهایتاً شکست خورد (تلاش ساده #2):", detail)
    return False


def send_message_get_id(chat_id, text):
    """
    مثل send_message ولی سعی می‌کنه message_id پیامی که فرستاده رو هم
    برگردونه (برای قابلیت «پاک کردن تبلیغ» بعداً). چون تا حالا این
    قسمت از API رو امتحان نکرده بودیم، مسیرهای مختلف پاسخ رو چک می‌کنه؛
    اگه هیچ‌کدوم نبود، پیام بازم فرستاده می‌شه ولی message_id برابر
    None برمی‌گرده (یعنی اون پیام بعداً قابل حذف خودکار نیست).
    """
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
        if resp.status_code != 200:
            print("خطای HTTP در ارسال پیام تبلیغاتی:", resp.status_code, resp.text)
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        candidates = []
        if isinstance(data, dict):
            candidates.append(data.get("message_id"))
            inner = data.get("data")
            if isinstance(inner, dict):
                candidates.append(inner.get("message_id"))
                new_msg = inner.get("new_message")
                if isinstance(new_msg, dict):
                    candidates.append(new_msg.get("message_id"))
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None
    except Exception as e:
        print("خطا در ارسال پیام تبلیغاتی:", e)
        return None


def delete_message(chat_id, message_id):
    try:
        resp = requests.post(
            f"{BASE_URL}/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=15,
        )
        if resp.status_code != 200:
            print("خطای HTTP در حذف پیام:", resp.status_code, resp.text)
            return False
        return True
    except Exception as e:
        print("خطا در حذف پیام:", e)
        return False


# ---------------------------------------------------------------------------
# قالب‌های پیام
# ---------------------------------------------------------------------------

def cosmetic_name_prefix(user_id, display_name):
    """
    اسم رو با نشان‌های ثابتی که کاربر خریده (تاج/VIP) جلو می‌ذاره — همون
    چیزی که قبلاً فقط تو «پروفایلم» دیده می‌شد، الان تو پیام‌های پرتکرار
    (میو، انتقال کوین) هم نشون داده می‌شه تا واقعاً ارزششون حس بشه.
    """
    cosmetics = db.get_profile_cosmetics(user_id)
    prefix = ""
    if cosmetics.get("has_vip_badge"):
        prefix += "💎 "
    if cosmetics.get("has_crown"):
        prefix += "👑 "
    return f"{prefix}{display_name}" if prefix else display_name


def build_meow_success_message(display_name, result):
    return (
        "🌙 صدای میوت توی شهر پیچید...\n\n"
        f"🪙 +{format_number(result['points_earned'])} {CURRENCY_NAME}\n"
        f"💰 موجودی: {format_number(result['total_points'])} 🪙\n"
        f"⏳ {format_cooldown(result['cooldown_seconds'])} تا میوی بعدی"
        + (
            f"\n\n🎉 تبریک {display_name}! سطح حسابت رفت رو {result['level']} ⭐️"
            f"\n🎁 پاداش سطح: +{format_number(result['bonus_coins'])} {CURRENCY_NAME}"
            if result["leveled_up"] else ""
        )
    )


def build_cooldown_message(display_name, remaining):
    return f"⌛️ گورباح {display_name} هنوز خسته‌ست، {format_cooldown(remaining)} دیگه صبر کن."


def build_leaderboard_message(rows, scope_label, viewer_rank=None, viewer_points=None):
    lines = [
        "🏆 ══════【 لیدربرد 】══════ 🏆",
        "",
        f"👑 دسته: ثروتمندترین گورباح‌های {scope_label} 🪙",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for i, row in enumerate(rows):
        rank = i + 1
        lines.append(f"{rank_emoji(rank)} {row['username']}")
        lines.append(f"└ 💰 {format_number(row['points'])} 🪙")
        if rank <= 3:
            lines.append(f"└ ⭐️ سطح حساب {row['level']}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    if viewer_rank is not None:
        lines.append("")
        lines.append(f"📍 رتبه تو: #{viewer_rank}")
        lines.append(f"🐾 دارایی تو: {format_number(viewer_points or 0)} 🪙")

    return "\n".join(lines)


def build_transfer_success_message(sender_name, receiver_name, amount, receiver_new_points):
    return (
        "🧲 انتقال کوین\n\n"
        f"🐈 گورباح {sender_name}\n"
        f"└─ 💸 {format_number(amount)} 🪙\n"
        "        ⬇️\n"
        f"🐈 گورباح {receiver_name}\n\n"
        "✅ انتقال با موفقیت انجام شد.\n\n"
        "💰 موجودی جدید:\n"
        f"{format_number(receiver_new_points)} 🪙"
    )


PROFILE_THEMES = {
    "الماسی": "💎",
    "مهتابی": "🌙",
}


def build_profile_message(display_name, user_id, profile):
    points_rank = db.get_rank_global(user_id, order_by="points")
    meows_rank = db.get_rank_global(user_id, order_by="total_meows")
    total_meows = profile.get("total_meows") or 0
    level = profile["level"]

    cosmetics = db.get_profile_cosmetics(user_id)
    corner = PROFILE_THEMES.get(cosmetics["active_theme"], "🐱")
    prefix = ""
    if cosmetics["has_vip_badge"]:
        prefix += "💎 "
    if cosmetics["has_crown"]:
        prefix += "👑 "
    name_line = f"{prefix}{display_name}" if prefix else display_name

    if level >= db.MAX_LEVEL:
        level_line = f"╯─ ⭐️ سطح حساب : {level} (حداکثر!) {progress_bar(1, 1)}"
    else:
        needed = db.exp_needed_for_next_level(level)
        level_line = f"╯─ ⭐️ سطح حساب : {level} | {profile['exp']} / {needed} {progress_bar(profile['exp'], needed)}"

    lines = [
        f"╮──「 {corner} پروفایل PawKing {corner} 」",
        "",
        f"┐─ 👤 کاربر : {name_line}",
    ]
    if cosmetics["active_title"]:
        lines.append(f"┘─ 🏷️ لقب : {cosmetics['active_title']}")
    lines += [
        f"┘─ 🪪 آیدی : {user_id}",
        "",
        f"┐─ 💰 {CURRENCY_NAME} ها : {format_number(profile['points'])} 🪙",
        f"┘─ 🎖️ رتبه ({format_number(points_rank)})" if points_rank else "┘─ 🎖️ رتبه (—)",
        "",
        f"┐─ 🐾 میو میو ها : {format_number(total_meows)}",
        f"┘─ 🎖️ رتبه ({format_number(meows_rank)})" if meows_rank else "┘─ 🎖️ رتبه (—)",
        "",
        level_line,
    ]
    if cosmetics["has_effect"]:
        lines += ["", "═══ ✨ ═══"]
    return "\n".join(lines)


def build_help_message():
    return (
        "📖═══「 راهنمای PawKing 」═══📖\n\n"
        "🐾 دستورات پایه\n"
        "میو\n"
        f"└ گرفتن {CURRENCY_NAME} (هر ۵ دقیقه یه‌بار)\n\n"
        "تنظیم نام <اسم>\n"
        "└ تنظیم اسمی که ربات صدات می‌زنه\n\n"
        "پروفایلم\n"
        "└ دیدن پروفایل خودت\n\n"
        "پروفایلش (با ریپلای رو یکی دیگه)\n"
        "└ دیدن پروفایل اون شخص\n\n"
        "آیدی من\n"
        "└ دیدن آیدی داخلیت\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏆 لیدربرد\n"
        "لیدربرد\n"
        "└ لیدربرد همین گروه\n\n"
        "لیدربرد کل\n"
        "└ لیدربرد کل بازیکن‌های ربات\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💸 اقتصاد\n"
        "انتقال کوین <عدد> (با ریپلای رو گیرنده)\n"
        f"└ انتقال {CURRENCY_NAME} به یکی دیگه (حداقل ۵۰۰، حداکثر ۱۰,۰۰۰)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏢 شرکت\n"
        "شرکت / شرکت‌هام\n"
        "└ دیدن یا تأسیس شرکتت\n\n"
        f"{COMPANY_BUY_COMMAND}\n"
        f"└ تأسیس اولین شرکت ({format_number(db.CAT_PRICE)} 🪙، نیاز به سطح حساب {db.CAT_MIN_LEVEL}+)\n\n"
        "تنظیم شرکت <اسم>\n"
        "└ اسم گذاشتن رو شرکتت\n\n"
        "توسعه شرکت (یا فقط: ارتقا)\n"
        "└ بالا بردن سطح شرکت و رتبه‌ش (رتبه‌های بالاتر به سطح حساب بیشتری هم نیاز دارن)\n\n"
        f"{COMPANY_COLLECT_COMMAND}\n"
        "└ خالی کردن خزانه‌ی شرکت\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏪 فروشگاه\n"
        f"{SHOP_COMMAND}\n"
        "└ دیدن لیست آیتم‌های قابل‌خرید\n\n"
        f"{BUY_ITEM_PREFIX} <اسم آیتم>  (مثلاً: {BUY_ITEM_PREFIX} قهوه)\n"
        "└ خرید یه آیتم\n\n"
        f"{INVENTORY_COMMAND}\n"
        "└ دیدن آیتم‌هایی که خریدی\n\n"
        f"{CODEBREAK_GUESS_PREFIX} <عدد سه‌رقمی>\n"
        "└ حدس زدن تو بازی «جعبه رمز» (بعد از «استفاده از جعبه رمز»)\n\n"
        f"{EQUIP_TITLE_PREFIX} <لقب>\n"
        "└ فعال کردن یکی از لقب‌هایی که خریدی (بدون نوشتن اسم، لیست لقب‌هات میاد)\n\n"
        f"{EQUIP_THEME_PREFIX} <تم>\n"
        "└ فعال کردن یکی از تم‌هایی که خریدی (بدون نوشتن اسم، لیست تم‌هات میاد)\n\n"
        f"{USE_ITEM_PREFIX} <اسم آیتم>  (مثلاً: {USE_ITEM_PREFIX} قهوه)\n"
        "└ مصرف یه آیتم از کیفت\n\n"
        f"{GIFT_CODE_PREFIX} <کد>  (مثلاً: {GIFT_CODE_PREFIX} AB12CD)\n"
        "└ استفاده از کد هدیه‌ی روزانه (هر روز یه کد جدید، محدود به چند نفر اول)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎰 کازینو\n"
        "└ ۸ تا بازی داره! برای دیدن همه بنویس: کازینو\n"
        "   (شیر یا خط، تاس، بالا/پایین، اسلات، رولت، بلک‌جک، دارت، مین‌یاب)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚔️ بازی‌های دونفره\n"
        "درخواست دوز <مبلغ> (ریپلای رو حریف)\n"
        "└ دعوت به دوز؛ برای قبول «قبول»، برای رد «رد»\n"
        "   بعدش هر نوبت بنویس: حرکت <شماره خونه ۱ تا ۹>\n\n"
        "درخواست گل یا پوچ <مبلغ> (ریپلای رو حریف)\n"
        "└ دعوت به گل‌یا‌پوچ (بهترین از ۳ دور)\n"
        "   قایم‌کننده تو پی‌وی بات «چپ»/«راست» می‌فرسته؛\n"
        "   حدس‌زننده همون رو تو گروه می‌نویسه\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🐾 هر وقت گم شدی، کافیه دوباره بنویسی «راهنما» (یا «آموزشگاه»)"
    )


def build_casino_menu_message():
    return (
        "🎰═══「 کازینو PawKing 」═══🎰\n\n"
        "سلامتی و شانس رفیق! کدوم بازی رو امتحان می‌کنی؟\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🪙 شیر یا خط\n"
        "└ دستور: شیر یا خط <مبلغ> <شیر/خط>\n\n"
        "🎲 تاس\n"
        "└ دستور: تاس <مبلغ> <عدد ۱ تا ۶>\n\n"
        "🔢 بالا یا پایین\n"
        "└ دستور: بالا <مبلغ>  یا  پایین <مبلغ>\n\n"
        "🎰 اسلات\n"
        "└ دستور: اسلات <مبلغ>\n\n"
        "🎡 رولت مینی\n"
        "└ دستور: رولت <مبلغ> <قرمز/مشکی>  یا  رولت <مبلغ> <عدد ۰ تا ۳۶>\n\n"
        "🃏 بلک‌جک\n"
        "└ دستور: بلک جک <مبلغ>  (بعدش «بکش» یا «بایست»)\n\n"
        "🎯 دارت\n"
        "└ دستور: دارت <مبلغ>  (یه پرتاب آنی، نتیجه فوریه)\n\n"
        "💣 مین‌یاب\n"
        "└ دستور: مین یاب <مبلغ> <تعداد مین>  (بعدش «باز کن <شماره>» یا «برداشت»)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏰ تو بلک‌جک و مین‌یاب، اگه {db.GAME_TIMEOUT_SECONDS} ثانیه جواب ندی، خودکار می‌بازی!\n\n"
        f"💰 حداقل شرط: {format_number(db.CASINO_MIN_BET)} 🪙  |  حداکثر: {format_number(db.CASINO_MAX_BET)} 🪙"
    )


def send_casino_error(chat_id, message_id, result, usage_text):
    reason = result["reason"]
    if reason == "below_min":
        send_message(chat_id, f"حداقل شرط {format_number(result['min'])} کوین پیشیه.", reply_to_message_id=message_id)
    elif reason == "above_max":
        send_message(chat_id, f"حداکثر شرط {format_number(result['max'])} کوین پیشیه.", reply_to_message_id=message_id)
    elif reason == "insufficient":
        send_message(chat_id, "موجودیت برای این شرط کافی نیست.", reply_to_message_id=message_id)
    else:
        send_message(chat_id, usage_text, reply_to_message_id=message_id)


def casino_effect_suffix(result):
    """اگه طلسم شانس یا بیمه سرمایه رو این بازی اثر گذاشته باشن، یه
    توضیح کوتاه براش اضافه می‌کنه تا کاربر بفهمه چرا نتیجه فرق کرد."""
    lines = []
    if result.get("luck_flipped"):
        lines.append("🎲 طلسم شانس فعال شد و باخت رو به برد تبدیل کرد!")
    if result.get("insurance_refund"):
        lines.append(f"💣 بیمه سرمایه فعال شد؛ {format_number(result['insurance_refund'])} 🪙 برگشت.")
    if not lines:
        return ""
    return "\n\n" + "\n".join(lines)


def handle_coinflip(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nشیر یا خط 500 شیر"
    rest = normalize_digits(text[len(COINFLIP_PREFIX):]).strip()

    choice = None
    if "شیر" in rest:
        choice = "شیر"
    elif "خط" in rest:
        choice = "خط"

    amount_match = re.search(r"\d+", rest.replace(",", "").replace("،", ""))
    if not choice or not amount_match:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet = int(amount_match.group())
    ok, result = db.coin_flip(sender_id, bet, choice)
    if not ok:
        send_casino_error(chat_id, message_id, result, usage)
        return

    if result["won"]:
        msg = (
            "🪙 سکه پرید تو هوا... 🌀\n"
            f"   نتیجه: {result['result']}\n\n"
            "✅ درست حدس زدی رفیق!\n"
            f"💵 شرط: {format_number(result['bet'])} 🪙  ←  🎉 برد: {format_number(result['winnings'])} 🪙\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    else:
        msg = (
            "🪙 سکه پرید تو هوا... 🌀\n"
            f"   نتیجه: {result['result']}\n\n"
            "❌ اشتباه حدس زدی.\n"
            f"💸 {format_number(result['bet'])} 🪙 از دست دادی.\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    msg += casino_effect_suffix(result)
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_dice(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nتاس 300 4\n(عدد اول مبلغ شرط، عدد دوم حدس بین ۱ تا ۶)"
    rest = normalize_digits(text[len(DICE_PREFIX):]).strip()
    nums = [int(n) for n in re.findall(r"\d+", rest.replace(",", "").replace("،", ""))]

    if len(nums) < 2:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet, guess = nums[0], nums[1]
    ok, result = db.dice_roll(sender_id, bet, guess)
    if not ok:
        send_casino_error(chat_id, message_id, result, usage)
        return

    if result["won"]:
        msg = (
            "🎲 تاس رو انداختیم...\n"
            f"   عدد اومد: {result['result']}\n\n"
            "✅ دقیقاً حدس زدی!\n"
            f"💵 شرط: {format_number(result['bet'])} 🪙 × {db.DICE_PAYOUT_MULTIPLIER}  ←  🎉 برد: {format_number(result['winnings'])} 🪙\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    else:
        msg = (
            "🎲 تاس رو انداختیم...\n"
            f"   عدد اومد: {result['result']} (تو گفتی {result['guess']})\n\n"
            "❌ این‌بار نه...\n"
            f"💸 {format_number(result['bet'])} 🪙 از دست دادی.\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    msg += casino_effect_suffix(result)
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_highlow(chat_id, sender_id, message_id, text, direction):
    usage = f"بنویس مثلاً:\n{direction} 400"
    rest = normalize_digits(text[len(direction):]).strip()
    amount_match = re.search(r"\d+", rest.replace(",", "").replace("،", ""))
    if not amount_match:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet = int(amount_match.group())
    ok, result = db.highlow_play(sender_id, bet, direction)
    if not ok:
        send_casino_error(chat_id, message_id, result, usage)
        return

    arrow = "📈" if direction == HIGH_WORD else "📉"
    header = (
        f"🔢 عدد فعلی: {result['current']}\n"
        f"{arrow} حدس تو: {direction} می‌ره\n\n"
        f"عدد بعدی: {result['next']}\n\n"
    )
    if result["won"]:
        msg = (
            header
            + "✅ درست گفتی!\n"
            + f"💵 شرط: {format_number(result['bet'])} 🪙  ←  🎉 برد: {format_number(result['winnings'])} 🪙\n\n"
            + f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    else:
        msg = (
            header
            + "❌ این‌بار نه...\n"
            + f"💸 {format_number(result['bet'])} 🪙 از دست دادی.\n\n"
            + f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    msg += casino_effect_suffix(result)
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_slot(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nاسلات 200"
    rest = normalize_digits(text[len(SLOT_PREFIX):]).strip()
    amount_match = re.search(r"\d+", rest.replace(",", "").replace("،", ""))
    if not amount_match:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet = int(amount_match.group())
    ok, result = db.slot_spin(sender_id, bet)
    if not ok:
        send_casino_error(chat_id, message_id, result, usage)
        return

    reel_display = f"┃ {result['reel'][0]} │ {result['reel'][1]} │ {result['reel'][2]} ┃"
    if result["won"]:
        celebration = "🎉🎉 جکپاتِ الماس! 🎉🎉" if result["multiplier"] >= 20 else "✅ برد!"
        msg = (
            "🎰 اسلات می‌چرخه... 🌀\n\n"
            f"{reel_display}\n\n"
            f"{celebration}\n"
            f"💵 شرط: {format_number(result['bet'])} 🪙 × {result['multiplier']}  ←  🎉 برد: {format_number(result['winnings'])} 🪙\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    else:
        msg = (
            "🎰 اسلات می‌چرخه... 🌀\n\n"
            f"{reel_display}\n\n"
            "❌ این‌بار ترکیبی نساختی...\n"
            f"💸 {format_number(result['bet'])} 🪙 از دست دادی.\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    msg += casino_effect_suffix(result)
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_roulette(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nرولت 600 قرمز\nیا\nرولت 600 17"
    rest = normalize_digits(text[len(ROULETTE_PREFIX):]).strip()
    amount_match = re.search(r"\d+", rest.replace(",", "").replace("،", ""))
    if not amount_match:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return
    bet = int(amount_match.group())

    remainder = rest[amount_match.end():].strip()
    remainder_no_amount = re.sub(r"\d+", "", remainder).strip()

    if "قرمز" in remainder or "مشکی" in remainder_no_amount or "قرمز" in remainder_no_amount:
        bet_type = "color"
        bet_value = "قرمز" if "قرمز" in remainder_no_amount else ("مشکی" if "مشکی" in remainder_no_amount else None)
        if not bet_value:
            send_message(chat_id, usage, reply_to_message_id=message_id)
            return
    else:
        number_match = re.search(r"\d+", remainder)
        if not number_match:
            send_message(chat_id, usage, reply_to_message_id=message_id)
            return
        bet_type = "number"
        bet_value = int(number_match.group())
        if bet_value < 0 or bet_value > 36:
            send_message(chat_id, "عدد باید بین ۰ تا ۳۶ باشه.", reply_to_message_id=message_id)
            return

    ok, result = db.roulette_spin(sender_id, bet, bet_type, bet_value)
    if not ok:
        send_casino_error(chat_id, message_id, result, usage)
        return

    color_emoji = {"قرمز": "🔴", "مشکی": "⚫️", "سبز": "🟢"}[result["color"]]
    header = f"🎡 چرخ رولت می‌چرخه... 🌀\n   ایستاد رو: {result['result']} {color_emoji}\n\n"
    if result["won"]:
        msg = (
            header
            + ("✅ رنگ رو درست حدس زدی!\n" if bet_type == "color" else "✅ دقیقاً عدد درست رو گفتی!\n")
            + f"💵 شرط: {format_number(result['bet'])} 🪙  ←  🎉 برد: {format_number(result['winnings'])} 🪙\n\n"
            + f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    else:
        msg = (
            header
            + "❌ این‌بار نه...\n"
            + f"💸 {format_number(result['bet'])} 🪙 از دست دادی.\n\n"
            + f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    msg += casino_effect_suffix(result)
    send_message(chat_id, msg, reply_to_message_id=message_id)


# ---------------------------------------------------------------------------
# 🃏 بلک‌جک
# ---------------------------------------------------------------------------

def _bj_card_value(rank):
    if rank in ("J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def _bj_hand_total(cards):
    total = sum(_bj_card_value(c) for c in cards)
    aces = cards.count("A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def build_blackjack_progress_message(bet, player, dealer_shown):
    player_total = _bj_hand_total(player)
    return (
        f"🃏 بلک‌جک شروع شد! شرط: {format_number(bet)} 🪙\n\n"
        f"🧑 برگ‌های تو: {' '.join(player)} = {player_total}\n"
        f"🤖 برگ باز خونه: {dealer_shown[0]} + ❓\n\n"
        "بنویس «بکش» یا «بایست»"
    )


def build_blackjack_result_message(status, bet, player, dealer, winnings, new_points):
    player_total = _bj_hand_total(player)
    dealer_total = _bj_hand_total(dealer)
    lines = [
        "🃏 نتیجه‌ی بازی\n",
        f"🧑 تو: {' '.join(player)} = {player_total}",
        f"🤖 خونه: {' '.join(dealer)} = {dealer_total}",
        "",
    ]
    if status in ("win", "natural_win"):
        extra = " (بلک‌جک طبیعی! 🎉)" if status == "natural_win" else ""
        lines.append(f"✅ بردی!{extra}")
        lines.append(f"💵 شرط: {format_number(bet)} 🪙  ←  🎉 برد: {format_number(winnings)} 🪙")
    elif status == "bust":
        lines.append("❌ از ۲۱ رد شدی، باختی!")
        lines.append(f"💸 {format_number(bet)} 🪙 از دست دادی.")
    elif status == "push_loss":
        lines.append("🤝 مساوی شدید، ولی طبق قانون بازی، مساوی به نفع خونه‌ست.")
        lines.append(f"💸 {format_number(bet)} 🪙 از دست دادی.")
    else:
        lines.append("❌ باختی...")
        lines.append(f"💸 {format_number(bet)} 🪙 از دست دادی.")
    lines.append("")
    lines.append(f"💰 موجودی جدید: {format_number(new_points)} 🪙")
    return "\n".join(lines)


def handle_blackjack_start(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nبلک جک 500"
    rest = normalize_digits(text[len(BLACKJACK_PREFIX):]).strip()
    amount_match = re.search(r"\d+", rest.replace(",", "").replace("،", ""))
    if not amount_match:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet = int(amount_match.group())
    ok, result = db.start_blackjack(sender_id, chat_id, bet)
    if not ok:
        if result["reason"] == "already_in_game":
            send_message(chat_id, "تو همین الان وسط یه بازی دیگه‌ای! اول تمومش کن.", reply_to_message_id=message_id)
        else:
            send_casino_error(chat_id, message_id, result, usage)
        return

    if result["status"] == "in_progress":
        msg = build_blackjack_progress_message(result["bet"], result["player"], result["dealer_shown"])
        if not send_message(chat_id, msg, reply_to_message_id=message_id):
            # همون باگی که تو مین‌یاب داشتیم: شرط کم شده و بازی «فعال»
            # ثبت شده ولی بازیکن هیچ‌وقت دستش رو نمی‌بینه. لغو و برگردوندن
            # شرط، به‌جای اینکه بذاریم بعد از تایم‌اوت خودکار ببازه.
            db.end_active_game(sender_id)
            db.refund_bet(sender_id, result["bet"])
            send_message(
                chat_id,
                "⚠️ یه مشکل فنی تو ارسال پیام پیش اومد، بازی بلک‌جک لغو و شرطت کامل برگردونده شد. دوباره امتحان کن.",
                reply_to_message_id=message_id,
            )
    else:
        msg = build_blackjack_result_message(
            result["status"], result["bet"], result["player"], result["dealer"],
            result["winnings"], result["new_points"],
        )
        # اینجا بازی همون لحظه (بلک‌جک طبیعی) تسویه شده، نه فعال مونده؛
        # اگه پیام نرسه چیزی «گیر» نمی‌مونه، فقط بازیکن فوری خبردار
        # نمی‌شه (نتیجه‌ش با «موجودی» قابل چک کردنه).
        send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_blackjack_hit(chat_id, sender_id, message_id):
    ok, result = db.blackjack_hit(sender_id)
    if not ok:
        return  # کاربر بازی فعال بلک‌جکی نداره؛ نادیده می‌گیریم

    if result["status"] == "in_progress":
        msg = build_blackjack_progress_message(result["bet"], result["player"], result["dealer_shown"])
    else:
        msg = build_blackjack_result_message(
            result["status"], result["bet"], result["player"], result["dealer"],
            result["winnings"], result["new_points"],
        )
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_blackjack_stand(chat_id, sender_id, message_id):
    ok, result = db.blackjack_stand(sender_id)
    if not ok:
        return  # کاربر بازی فعال بلک‌جکی نداره؛ نادیده می‌گیریم

    msg = build_blackjack_result_message(
        result["status"], result["bet"], result["player"], result["dealer"],
        result["winnings"], result["new_points"],
    )
    send_message(chat_id, msg, reply_to_message_id=message_id)


# ---------------------------------------------------------------------------
# 🎯 دارت
# ---------------------------------------------------------------------------

DART_ZONE_LABELS = {
    "bullseye": ("🎯", "درست وسط بولزای"),
    "red": ("🔴", "حلقه‌ی قرمز"),
    "blue": ("🔵", "حلقه‌ی آبی"),
    "miss": ("⚪️", "کاملاً از تخته زدی بیرون"),
}


def handle_dart(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nدارت 200"
    rest = normalize_digits(text[len(DART_PREFIX):]).strip()
    amount_match = re.search(r"\d+", rest.replace(",", "").replace("،", ""))
    if not amount_match:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet = int(amount_match.group())
    ok, result = db.dart_throw(sender_id, bet)
    if not ok:
        send_casino_error(chat_id, message_id, result, usage)
        return

    emoji, zone_name = DART_ZONE_LABELS[result["zone"]]
    header = f"🎯 دارت رو پرتاب کردی... 🌀\n\n   برخورد کرد به: {emoji} {zone_name}\n\n"

    if result["status"] == "win":
        msg = header + (
            "🎉 عالی بود!\n"
            f"💵 شرط: {format_number(result['bet'])} 🪙 × {result['multiplier']}  ←  🎉 برد: {format_number(result['winnings'])} 🪙\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    elif result["status"] == "push":
        msg = header + (
            "🤝 نه بردی نه باختی، شرطت برگشت.\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    else:
        msg = header + (
            "❌ باختی...\n"
            f"💸 {format_number(result['bet'])} 🪙 از دست دادی.\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    msg += casino_effect_suffix(result)
    send_message(chat_id, msg, reply_to_message_id=message_id)


# ---------------------------------------------------------------------------
# 💣 مین‌یاب
# ---------------------------------------------------------------------------

def build_mines_grid(opened, revealed_mines=None, total_cells=25, cols=5):
    revealed_mines = revealed_mines or []
    lines = []
    for row_start in range(0, total_cells, cols):
        row_cells = []
        for i in range(row_start, row_start + cols):
            if i in revealed_mines:
                row_cells.append("💥")
            elif i in opened:
                row_cells.append("💎")
            else:
                row_cells.append("❓")
        lines.append(" ".join(row_cells))
    return "\n".join(lines)


def handle_mines_start(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nمین یاب 300 5\n(عدد دوم تعداد مین‌هاست، بین ۱ تا ۱۰)"
    rest = normalize_digits(text[len(MINES_PREFIX):]).strip()
    nums = [int(n) for n in re.findall(r"\d+", rest.replace(",", "").replace("،", ""))]
    if len(nums) < 2:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet, mine_count = nums[0], nums[1]
    ok, result = db.start_mines(sender_id, chat_id, bet, mine_count)
    if not ok:
        reason = result["reason"]
        if reason == "already_in_game":
            send_message(chat_id, "تو همین الان وسط یه بازی دیگه‌ای! اول تمومش کن.", reply_to_message_id=message_id)
        elif reason == "invalid_mine_count":
            send_message(
                chat_id,
                f"تعداد مین باید بین {result['min']} تا {result['max']} باشه.",
                reply_to_message_id=message_id,
            )
        else:
            send_casino_error(chat_id, message_id, result, usage)
        return

    grid = build_mines_grid([])
    msg = (
        "💣 مین‌یاب شروع شد!\n\n"
        f"💵 شرط: {format_number(result['bet'])} 🪙\n"
        f"💥 تعداد مین‌ها: {result['mine_count']} از {db.MINES_TOTAL_CELLS}\n\n"
        f"{grid}\n\n"
        "بنویس «باز کن <شماره>» تا یه خونه رو باز کنی 🐾\n"
        "(شماره‌ها از ۱ تا ۲۵، از بالا-چپ به پایین-راست)"
    )
    if not send_message(chat_id, msg, reply_to_message_id=message_id):
        # پیام شروع نرسید؛ اگه بازی رو همینجوری فعال ول کنیم، بازیکن نه
        # صفحه‌ی بازی رو می‌بینه نه می‌دونه شرطش کم شده، و بعد از مهلت
        # تایم‌اوت خودکار می‌بازه بدون اینکه اصلاً بفهمه بازی شروع شده
        # بود. پس بازی رو لغو و شرط رو برمی‌گردونیم و یه بار دیگه سعی
        # می‌کنیم خبرش کنیم.
        db.end_active_game(sender_id)
        db.refund_bet(sender_id, result["bet"])
        send_message(
            chat_id,
            "⚠️ یه مشکل فنی تو ارسال پیام پیش اومد، بازی مین‌یاب لغو و شرطت کامل برگردونده شد. دوباره امتحان کن.",
            reply_to_message_id=message_id,
        )


def handle_mines_open(chat_id, sender_id, message_id, text):
    rest = normalize_digits(text[len(MINES_OPEN_PREFIX):]).strip()
    number_match = re.search(r"\d+", rest)
    if not number_match:
        send_message(chat_id, "بنویس مثلاً:\nباز کن 7", reply_to_message_id=message_id)
        return

    cell_number = int(number_match.group())
    cell_index = cell_number - 1

    ok, result = db.mines_open(sender_id, cell_index)
    if not ok:
        reason = result["reason"]
        if reason == "no_game":
            send_message(
                chat_id,
                "الان تو بازی مین‌یابی نیستی. بنویس «مین یاب <مبلغ> <تعداد مین>» تا شروع کنی.",
                reply_to_message_id=message_id,
            )
        elif reason == "invalid_cell":
            send_message(chat_id, "شماره باید بین ۱ تا ۲۵ باشه.", reply_to_message_id=message_id)
        elif reason == "already_open":
            send_message(chat_id, "این خونه رو قبلاً باز کردی!", reply_to_message_id=message_id)
        else:
            send_message(chat_id, "یه مشکلی پیش اومد.", reply_to_message_id=message_id)
        return

    status = result["status"]
    if status == "hit_mine":
        grid = build_mines_grid(result["opened"], revealed_mines=result["mine_positions"])
        msg = (
            "💥 وای نه! رو یه مین رفتی!\n\n"
            f"{grid}\n\n"
            f"😿 {format_number(result['bet'])} 🪙 از دست دادی.\n"
            "دفعه‌ی بعد زودتر برداشت کن رفیق!"
        )
    elif status == "cleared_all":
        grid = build_mines_grid(result["opened"])
        msg = (
            "🎉 همه‌ی خونه‌های امن رو پیدا کردی!\n\n"
            f"{grid}\n\n"
            f"📈 ضریب نهایی: {result['multiplier']}x\n"
            f"💵 شرط: {format_number(result['bet'])} 🪙  ←  🎉 برد: {format_number(result['winnings'])} 🪙\n\n"
            f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
        )
    else:
        grid = build_mines_grid(result["opened"])
        potential = int(result["bet"] * result["multiplier"])
        msg = (
            f"💎 خونه‌ی {cell_number} امن بود! 😺\n\n"
            f"{grid}\n\n"
            f"📈 ضریب فعلی: {result['multiplier']}x\n"
            f"💰 اگه برداشت کنی: {format_number(potential)} 🪙\n\n"
            "ادامه بدی یا برداشت کنی؟"
        )
    send_message(chat_id, msg, reply_to_message_id=message_id)


# ---------------------------------------------------------------------------
# «برداشت» — مشترک بین کرش و مین‌یاب، بسته به نوع بازی فعال کاربر
# ---------------------------------------------------------------------------

def handle_cashout(chat_id, sender_id, message_id):
    game = db.get_active_game(sender_id)
    if not game:
        return  # بازی فعالی نداره؛ نادیده می‌گیریم

    if game["game_type"] == "mines":
        ok, result = db.mines_cashout(sender_id)
        if not ok:
            if result["reason"] == "no_cells_opened":
                send_message(
                    chat_id,
                    "هنوز هیچ خونه‌ای باز نکردی! اول حداقل یه «باز کن <شماره>» بزن.",
                    reply_to_message_id=message_id,
                )
            return
        grid = build_mines_grid(result["opened"])
        send_message(
            chat_id,
            (
                "✅ خیلی خوب بازی کردی!\n\n"
                f"{grid}\n\n"
                f"📈 ضریب نهایی: {result['multiplier']}x\n"
                f"💵 شرط: {format_number(result['bet'])} 🪙  ←  🎉 بردی: {format_number(result['winnings'])} 🪙\n\n"
                f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
            ),
            reply_to_message_id=message_id,
        )
    # اگه بازی فعال از نوع دیگه‌ای بود (مثلاً بلک‌جک)، «برداشت» معنی نداره، نادیده می‌گیریم


def handle_cat_set_name(chat_id, sender_id, message_id, text):
    new_name = text[len(COMPANY_NAME_PREFIX):].strip()
    if not new_name:
        send_message(
            chat_id,
            "بعد از «تنظیم شرکت» اسمشو بنویس، مثلاً:\nتنظیم شرکت هانیه تجارت",
            reply_to_message_id=message_id,
        )
        return
    if db.contains_blocked_word(new_name):
        send_message(chat_id, "این اسم قابل قبول نیست، یه اسم دیگه انتخاب کن.", reply_to_message_id=message_id)
        return
    if db.contains_reserved_cosmetic_symbol(new_name):
        send_message(
            chat_id,
            "تو اسم شرکتت نمی‌تونی از ایموجی یا نمادهای فروشگاهی (تاج، 💎 و…) استفاده کنی — این‌ها فقط با خرید واقعی از فروشگاه فعال می‌شن.",
            reply_to_message_id=message_id,
        )
        return

    updated = db.set_cat_name(sender_id, new_name)
    if updated:
        send_message(chat_id, f"✅ اسم شرکتت شد: {new_name} 💼", reply_to_message_id=message_id)
    else:
        send_message(
            chat_id,
            f"هنوز شرکتی نداری! اول بنویس «{COMPANY_BUY_COMMAND}» تا یکی تأسیس کنی 💼",
            reply_to_message_id=message_id,
        )


# ---------------------------------------------------------------------------
# 🆚 بازی‌های دونفره (دوز، گل یا پوچ)
# ---------------------------------------------------------------------------

def build_tictactoe_board(board):
    def cell(i):
        return board[i] if board[i] else str(i + 1)
    return (
        f" {cell(0)} | {cell(1)} | {cell(2)} \n"
        "-----------\n"
        f" {cell(3)} | {cell(4)} | {cell(5)} \n"
        "-----------\n"
        f" {cell(6)} | {cell(7)} | {cell(8)} "
    )


def parse_duel_request(text):
    rest = normalize_digits(text[len(DUEL_REQUEST_PREFIX):]).strip()
    for name, game_type in DUEL_GAME_NAMES.items():
        if rest.startswith(name):
            remainder = rest[len(name):].strip()
            amount_match = re.search(r"\d+", remainder.replace(",", "").replace("،", ""))
            if amount_match:
                return game_type, int(amount_match.group())
    return None, None


def handle_duel_request(chat_id, sender_id, message_id, text, reply_to_message_id):
    if not reply_to_message_id:
        send_message(
            chat_id,
            "باید روی پیام کسی که می‌خوای دعوتش کنی ریپلای بزنی.",
            reply_to_message_id=message_id,
        )
        return

    game_type, bet = parse_duel_request(text)
    if not game_type:
        send_message(
            chat_id,
            "بنویس مثلاً:\nدرخواست دوز 5000\nیا\nدرخواست گل یا پوچ 5000",
            reply_to_message_id=message_id,
        )
        return

    target_id = db.get_sender_of_message(reply_to_message_id)
    if not target_id:
        send_message(chat_id, "نتونستم بفهمم این پیام برای کیه.", reply_to_message_id=message_id)
        return

    ok, result = db.create_duel_invite(chat_id, sender_id, target_id, game_type, bet)
    if not ok:
        reason = result["reason"]
        if reason == "self":
            send_message(chat_id, "نمی‌تونی خودتو دعوت کنی 😹", reply_to_message_id=message_id)
        elif reason == "below_min":
            send_message(chat_id, f"حداقل شرط {format_number(result['min'])} کوین پیشیه.", reply_to_message_id=message_id)
        elif reason == "above_max":
            send_message(chat_id, f"حداکثر شرط {format_number(result['max'])} کوین پیشیه.", reply_to_message_id=message_id)
        elif reason == "already_in_game":
            send_message(chat_id, "یکی از شما دو نفر همین الان وسط یه بازی دیگه‌این.", reply_to_message_id=message_id)
        elif reason == "pending_invite_exists":
            send_message(chat_id, "یکی از شما دو نفر یه دعوت‌نامه‌ی جواب‌نداده‌ی دیگه داره.", reply_to_message_id=message_id)
        elif reason == "insufficient":
            send_message(chat_id, "موجودیت برای این شرط کافی نیست.", reply_to_message_id=message_id)
        return

    challenger_name = db.get_username(sender_id) or "ناشناس"
    game_display = "دوز" if game_type == "tictactoe" else "گل یا پوچ"
    msg = (
        f"⚔️ دعوت به {game_display}!\n\n"
        f"🧑 {challenger_name} می‌خواد باهات {game_display} بازی کنه\n"
        f"💰 شرط: {format_number(bet)} 🪙 (برنده {format_number(bet * 2)} 🪙 می‌بره)\n"
    )
    if game_type == "guessflower":
        msg += "🖐 طرف مقابل قایم‌کننده‌ست، تو باید حدس بزنی\n"
    msg += "\nبرای قبول کردن بنویس: قبول\nبرای رد کردن بنویس: رد"
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_duel_accept(chat_id, sender_id, message_id):
    ok, result = db.accept_duel_invite(chat_id, sender_id)
    if not ok:
        return  # کاربر دعوتی نداره؛ نادیده می‌گیریم

    game_type = result["game_type"]
    challenger_id = result["challenger_id"]
    target_id = result["target_id"]
    challenger_name = db.get_username(challenger_id) or "ناشناس"
    target_name = db.get_username(target_id) or "ناشناس"

    if game_type == "tictactoe":
        state = result["state"]
        board_display = build_tictactoe_board(state["board"])
        msg = (
            f"🎮 دوز: {challenger_name} (❌) 🆚 {target_name} (⭕)\n\n"
            f"{board_display}\n\n"
            f"نوبت: {challenger_name} ❌\n"
            "بنویس «حرکت <شماره خونه>»"
        )
        send_message(chat_id, msg)

    elif game_type == "guessflower":
        msg = (
            "🌸 گل یا پوچ — دور 1 از 3\n"
            f"🏆 امتیاز: {challenger_name} 0 - 0 {target_name}\n\n"
            f"🖐 {challenger_name} قایم‌کننده‌ست. الان یه پیام خصوصی بهش می‌فرستیم."
        )
        send_message(chat_id, msg)
        # یادآوری خصوصی به قایم‌کننده — چون انتخابش نباید تو گروه دیده بشه.
        # همون باگ همیشگی: باید از chat_id واقعیِ پیوی استفاده کنیم، نه
        # مستقیم آیدی خودِ کاربر.
        pv_chat_id = db.get_pv_chat_id(challenger_id)
        if pv_chat_id:
            send_message(
                pv_chat_id,
                "🌸 نوبت توئه که گل رو قایم کنی! بنویس «چپ» یا «راست» (این پیام خصوصیه، طرف مقابل نمی‌بینتش)",
            )
        else:
            send_message(
                chat_id,
                f"⚠️ {challenger_name} عزیز، نتونستم بهت پیوی بدم. اول یه پیام (مثلاً «سلام») تو پیوی خودِ من بفرست، بعد اینجا بگو آماده‌ای تا ادامه بدیم.",
            )


def handle_duel_decline(chat_id, sender_id, message_id):
    ok, result = db.decline_duel_invite(chat_id, sender_id)
    if not ok:
        return
    game_display = "دوز" if result["game_type"] == "tictactoe" else "گل یا پوچ"
    send_message(chat_id, f"❌ دعوت به {game_display} رد شد.", reply_to_message_id=message_id)


def handle_tictactoe_move(chat_id, sender_id, message_id, text):
    rest = normalize_digits(text[len(DUEL_MOVE_PREFIX):]).strip()
    number_match = re.search(r"\d+", rest)
    if not number_match:
        return

    cell_index = int(number_match.group()) - 1
    ok, result = db.duel_tictactoe_move(sender_id, cell_index)
    if not ok:
        reason = result["reason"]
        if reason == "not_your_turn":
            send_message(chat_id, "نوبت تو نیست!", reply_to_message_id=message_id)
        elif reason == "invalid_cell":
            send_message(chat_id, "شماره باید بین ۱ تا ۹ باشه.", reply_to_message_id=message_id)
        elif reason == "cell_taken":
            send_message(chat_id, "این خونه قبلاً پر شده!", reply_to_message_id=message_id)
        return

    board_display = build_tictactoe_board(result["board"])
    status = result["status"]
    game_chat_id = result["chat_id"]

    if status == "win":
        winner_name = db.get_username(result["winner_id"]) or "ناشناس"
        msg = (
            f"🎮 دوز\n\n{board_display}\n\n"
            f"🎉 {winner_name} برد!\n"
            f"💰 {format_number(result['pot'])} 🪙 به {winner_name} رسید."
        )
    elif status == "coin_of_fate":
        winner_name = db.get_username(result["winner_id"]) or "ناشناس"
        msg = (
            f"😮 صفحه پر شد و مساوی شدید!\nطبق قانون این بازی، مساوی نداریم...\n\n"
            f"🪙 سکه‌ی سرنوشت انداختیم...\n"
            f"🎉 {winner_name} برنده شد و {format_number(result['pot'])} 🪙 رو گرفت!"
        )
    else:
        next_name = db.get_username(result["next_turn"]) or "ناشناس"
        msg = f"🎮 دوز\n\n{board_display}\n\nنوبت: {next_name}\nبنویس «حرکت <شماره خونه>»"

    send_message(game_chat_id, msg)


# ---------------------------------------------------------------------------
# مافیا — سناریوی «تفنگدار» (مرحله‌ی ۱: لابی + ثبت‌نام + تخصیص نقش)
# ---------------------------------------------------------------------------

def handle_mafia_lobby_start(chat_id, sender_id, message_id, sender_name):
    display_name = sender_name or "ناشناس"
    ok, result = db.create_mafia_lobby(chat_id, sender_id, display_name)
    if not ok:
        send_message(
            chat_id,
            "یه بازی مافیا از قبل تو همین گروه فعاله. اول اونو تموم کنید یا میزبانش بنویسه «لغو مافیا».",
            reply_to_message_id=message_id,
        )
        return
    send_message(
        chat_id,
        (
            "🔫 لابی مافیای «تفنگدار» باز شد!\n\n"
            f"میزبان: {display_name} (خودکار ثبت‌نام شد)\n"
            f"برای پیوستن بنویس: {MAFIA_JOIN_COMMAND}\n\n"
            f"با {db.MAFIA_MIN_PLAYERS} نفر، میزبان می‌تونه با نوشتن «{MAFIA_FORCE_START_COMMAND}» بازی رو شروع کنه.\n"
            f"با {db.MAFIA_MAX_PLAYERS} نفر، بازی خودکار شروع می‌شه.\n\n"
            "⚠️ برای گرفتن نقش، حتماً باید قبلاً تو پیوی خودِ من پیام داده باشی."
        ),
        reply_to_message_id=message_id,
    )


def handle_mafia_join(chat_id, sender_id, message_id, sender_name):
    display_name = sender_name or db.get_username(sender_id)
    if not display_name:
        send_message(
            chat_id,
            "اول یه بار بنویس «میو» تا تو سیستم ثبت بشی، بعد بیا ثبت‌نام کن.",
            reply_to_message_id=message_id,
        )
        return

    # قبل از ثبت واقعی تو دیتابیس، یه پیام تستی به پیوی خودش می‌فرستیم.
    # اگه نرسه (چون هیچ‌وقت مستقیم با بات پیوی نزده — که با نوشتن دستور
    # تو گروه فرق داره)، اصلاً ثبت‌نامش کامل نمی‌شه؛ این‌جوری همون لحظه
    # می‌فهمه، نه بعد از شروع کل بازی که دیگه دیر شده.
    pv_chat_id = db.get_pv_chat_id(sender_id)
    pv_ok = pv_chat_id and send_message(
        pv_chat_id,
        f"✅ {display_name} عزیز، تو لابی مافیای «تفنگدار» ثبت‌نام شدی. صبر کن بقیه هم بیان 🐾",
    )
    if not pv_ok:
        send_message(
            chat_id,
            f"{display_name} عزیز، نتونستم بهت پیوی بدم — برای گرفتن نقش تو مافیا، اول باید مستقیم با خودِ من (بات) یه پیوی باز کنی (نه فقط تو گروه دستور بدی). یه پیام (مثلاً «سلام») تو پیوی من بفرست، بعد دوباره بنویس «{MAFIA_JOIN_COMMAND}».",
            reply_to_message_id=message_id,
        )
        return

    ok, result = db.join_mafia_lobby(chat_id, sender_id, display_name)
    if not ok:
        reason = result
        if reason == "no_lobby":
            send_message(
                chat_id,
                f"الان لابی مافیایی باز نیست. یه نفر بنویسه «{MAFIA_START_COMMAND}» تا لابی باز بشه.",
                reply_to_message_id=message_id,
            )
        elif reason == "already_started":
            send_message(chat_id, "بازی مافیای این گروه از لابی رد شده، دیگه نمی‌تونی ثبت‌نام کنی.", reply_to_message_id=message_id)
        elif reason == "already_joined":
            send_message(chat_id, "قبلاً ثبت‌نام کردی، صبر کن بقیه هم بیان 🐾", reply_to_message_id=message_id)
        elif reason == "full":
            send_message(chat_id, f"لابی پره ({db.MAFIA_MAX_PLAYERS} نفر). صبر کن بازی شروع بشه.", reply_to_message_id=message_id)
        return

    player_count = result["player_count"]
    send_message(
        chat_id,
        f"✅ {display_name} ثبت‌نام شد. ({player_count}/{db.MAFIA_MAX_PLAYERS})",
        reply_to_message_id=message_id,
    )

    if result["auto_start"]:
        _start_mafia_game_and_announce(chat_id, sender_id, message_id, is_bot_admin=True)


def handle_mafia_force_start(chat_id, sender_id, message_id):
    is_bot_admin = str(sender_id) in ADMIN_USER_IDS
    _start_mafia_game_and_announce(chat_id, sender_id, message_id, is_bot_admin=is_bot_admin)


def handle_mafia_cancel(chat_id, sender_id, message_id):
    game = db.get_active_mafia_game(chat_id)
    if not game:
        send_message(chat_id, "الان بازی/لابی مافیای فعالی تو این گروه نیست.", reply_to_message_id=message_id)
        return
    is_host = str(sender_id) == game["host_id"]
    is_bot_admin = str(sender_id) in ADMIN_USER_IDS
    if not (is_host or is_bot_admin):
        send_message(chat_id, "فقط میزبان لابی یا ادمین می‌تونه لغوش کنه.", reply_to_message_id=message_id)
        return
    db.cancel_mafia_lobby(chat_id)
    send_message(chat_id, "🚫 لابی/بازی مافیا لغو شد.", reply_to_message_id=message_id)


def _start_mafia_game_and_announce(chat_id, requester_id, message_id, is_bot_admin):
    ok, result = db.start_mafia_game(chat_id, requester_id, is_bot_admin)
    if not ok:
        reason = result
        if reason == "no_lobby":
            return
        if reason == "already_started":
            return
        if isinstance(reason, dict) and reason.get("reason") == "not_enough":
            send_message(
                chat_id,
                f"هنوز {reason['count']} نفر ثبت‌نام کردن؛ حداقل {reason['needed']} نفر لازمه.",
                reply_to_message_id=message_id,
            )
        elif reason == "not_host":
            send_message(chat_id, "فقط میزبان لابی (یا ادمین) می‌تونه با ۹ نفر زودتر شروع کنه.", reply_to_message_id=message_id)
        return

    assigned_players = result["assigned_players"]
    mafia_team = result["mafia_team"]
    mafia_names = "، ".join(p["name"] for p in mafia_team)

    failed_deliveries = []
    for player in assigned_players:
        role = player["role"]
        role_fa = db.MAFIA_ROLE_NAMES_FA[role]
        lines = [f"🎭 نقش تو تو این بازی مافیا: {role_fa}"]
        if role in db.MAFIA_TEAM_ROLES:
            lines.append(f"\n🤝 هم‌تیمی‌های مافیای تو: {mafia_names}")
            lines.append("\nهر پیامی که تو همین پیوی برای من (بات) بفرستی، برای بقیه‌ی هم‌تیمی‌های مافیات هم پیوی می‌شه — این‌جوری می‌تونید شب‌ها با هم هماهنگ بشید.")
        lines.append("\n(بقیه‌ی توضیحات نقش و فاز شب، تو مرحله‌ی بعدی فعال می‌شه.)")
        pv_chat_id = db.get_pv_chat_id(player["user_id"])
        delivered = pv_chat_id and send_message(pv_chat_id, "\n".join(lines))
        if not delivered:
            failed_deliveries.append(player["name"])

    announce = f"🎬 بازی مافیای «تفنگدار» با {len(assigned_players)} نفر شروع شد! نقش‌ها تو پیوی هرکس فرستاده شد."
    if failed_deliveries:
        announce += (
            "\n\n⚠️ نتونستم برای این افراد پیوی بفرستم (احتمالاً تو پیوی من پیام نداده بودن): "
            + "، ".join(failed_deliveries)
        )
    send_message(chat_id, announce, reply_to_message_id=message_id)


def handle_flower_guess_result(result):
    status = result["status"]
    hider_id = result["hider_id"]
    guesser_id = result["guesser_id"]
    hider_name = db.get_username(hider_id) or "ناشناس"
    guesser_name = db.get_username(guesser_id) or "ناشناس"
    revealed_hand = result["revealed_hand"]
    correct = result["correct"]
    guess_hand = revealed_hand if correct else (RIGHT_WORD if revealed_hand == LEFT_WORD else LEFT_WORD)
    group_chat_id = result["chat_id"]

    lines = [f"✋ دست {guess_hand} رو باز کردیم..."]
    lines.append("🌸 گل همونجا بود!" if correct else "😅 پوچ بود...")
    lines.append("")
    lines.append(f"{'✅' if correct else '❌'} {guesser_name} {'درست حدس زد!' if correct else 'اشتباه حدس زد.'}")
    lines.append(f"🏆 امتیاز: {hider_name} {result['score_hider']} - {result['score_guesser']} {guesser_name}")
    lines.append("")

    if status == "guesser_win":
        lines.append(f"🎉 {guesser_name} با ۲ حدس درست برد!")
        lines.append(f"💰 {format_number(result['pot'])} 🪙 به {guesser_name} رسید.")
    elif status == "hider_win":
        lines.append(f"🎉 {hider_name} برد!")
        lines.append(f"💰 {format_number(result['pot'])} 🪙 به {hider_name} رسید.")
    else:
        lines.append(f"➡️ دور بعد ({result['round']} از 3) شروع می‌شه...")
        lines.append(f"🖐 {hider_name}، دوباره باید تو پی‌وی بات گل رو قایم کنی!")
        pv_chat_id = db.get_pv_chat_id(hider_id)
        if pv_chat_id:
            send_message(pv_chat_id, "🌸 نوبت دور بعدیه! دوباره بنویس «چپ» یا «راست» (خصوصیه)")
        else:
            lines.append(f"⚠️ نتونستم پیویت بدم؛ اول تو پیوی خودِ من پیام بده، بعد همینجا بگو آماده‌ای.")

    send_message(group_chat_id, "\n".join(lines))


def handle_hand_word(chat_id, sender_id, message_id, text):
    """
    به‌جای حدس زدن این‌که پیام تو پی‌وی اومده یا گروه (که تشخیصش مطمئن نبود)،
    اول امتحان می‌کنیم ببینیم فرستنده الان «قایم‌کننده‌ی منتظر» تو یه بازی
    فعاله؛ اگه بود همون رو پردازش می‌کنیم. اگه نبود، امتحان می‌کنیم ببینیم
    «حدس‌زننده‌ی منتظر» هست یا نه. این‌جوری فارغ از این‌که کجا فرستاده شده،
    درست کار می‌کنه.
    """
    ok, result = db.duel_flower_hide(sender_id, text)
    if ok:
        group_chat_id = result["chat_id"]
        # اگه همون‌جایی که پیام اومده با گروه بازی فرق داشت (یعنی احتمالاً پی‌ویه)،
        # یه تأیید کوتاه هم همون‌جا می‌فرستیم.
        if str(chat_id) != str(group_chat_id):
            send_message(chat_id, "✅ گل قایم شد! حالا صبر کن طرف مقابل حدس بزنه.")
        send_message(
            group_chat_id,
            f"🌸 گل یا پوچ — دور {result['round']} از 3\n\n🖐 گل قایم شد...\nحدس بزن: «چپ» یا «راست»",
        )
        return

    ok, result = db.duel_flower_guess(sender_id, text)
    if not ok:
        return  # نه قایم‌کننده‌ی منتظره نه حدس‌زننده‌ی منتظر؛ ربطی به بازی نداره
    handle_flower_guess_result(result)


def resolve_expired_duels():
    for invite in db.get_expired_duel_invites():
        try:
            info = db.expire_duel_invite(invite)
            game_display = "دوز" if info["game_type"] == "tictactoe" else "گل یا پوچ"
            send_message(info["chat_id"], f"⏰ دعوت به {game_display} به‌خاطر بی‌پاسخ ماندن لغو شد.")
        except Exception:
            print("خطا در رسیدگی به یه دعوت‌نامه‌ی منقضی‌شده:")
            traceback.print_exc()
            try:
                db._delete_duel_invite(invite["id"])
            except Exception:
                pass

    for game in db.get_expired_duel_games():
        try:
            info = db.expire_duel_game(game["id"])
            if not info:
                # یا خودِ بازیکن دقیقاً همین لحظه بازی رو تموم کرده، یا
                # یه تیک قبلی همین حلقه زودتر رسیدگی کرده بود؛ کاری نکن.
                continue
            if not info["winner_id"]:
                continue
            winner_name = db.get_username(info["winner_id"]) or "ناشناس"
            game_display = "دوز" if info["game_type"] == "tictactoe" else "گل یا پوچ"
            send_message(
                info["chat_id"],
                (
                    f"⏰ وقت برای {game_display} تموم شد!\n"
                    f"چون طرف مقابل دیر جنبید، {winner_name} برد و {format_number(info['pot'])} 🪙 گرفت."
                ),
            )
        except Exception:
            # اگه رسیدگی عادی خطا داد، به‌جای گیر کردن بازی برای همیشه، بازی رو
            # می‌بندیم و شرط هر دو طرف رو به‌خاطر باگ برمی‌گردونیم (تقصیر کاربر نیست).
            print("خطا در رسیدگی به یه بازی دوئل منقضی‌شده:")
            traceback.print_exc()
            try:
                db._end_duel_game(game["id"])
                bet = game["bet"]
                db._credit(game["player1_id"], bet)
                db._credit(game["player2_id"], bet)
                send_message(
                    game["chat_id"],
                    "⚠️ یه مشکل فنی تو رسیدگی به این بازی پیش اومد، بازی لغو شد و شرط هر دو نفر برگشت.",
                )
            except Exception:
                traceback.print_exc()


# ---------------------------------------------------------------------------
# چک دوره‌ای بازی‌های چندمرحله‌ای (بلک‌جک، کرش، مین‌یاب) که مهلتشون تموم شده
# ---------------------------------------------------------------------------

def resolve_expired_games():
    expired = db.get_expired_games()
    for game in expired:
        user_id = game["user_id"]
        chat_id = game["chat_id"]
        game_type = game["game_type"]
        bet = game["bet"]
        state = game["state"]

        try:
            if game_type == "blackjack":
                db.end_active_game(user_id)
                send_message(
                    chat_id,
                    (
                        "⏰ وقتت برای بلک‌جک تموم شد!\n"
                        f"💸 {format_number(bet)} 🪙 از دست دادی.\n"
                        "دفعه‌ی بعد سریع‌تر تصمیم بگیر 🐾"
                    ),
                )

            elif game_type == "crash":
                crash_point = state.get("crash_point", 1.0)
                db.end_active_game(user_id)
                send_message(
                    chat_id,
                    (
                        f"💥 ترکید!! در ضریب {crash_point}x 💔\n\n"
                        f"😿 {format_number(bet)} 🪙 از دست دادی.\n"
                        "دفعه‌ی بعد زودتر برداشت کن!"
                    ),
                )

            elif game_type == "mines":
                opened_count = len(state.get("opened", []))
                db.end_active_game(user_id)
                send_message(
                    chat_id,
                    (
                        "⏰ وقتت برای مین‌یاب تموم شد!\n"
                        f"🐾 {opened_count} خونه باز کرده بودی، ولی دیر جنبیدی.\n"
                        f"💸 {format_number(bet)} 🪙 از دست دادی."
                    ),
                )

            elif game_type == "codebreak":
                secret = state.get("secret", "؟؟؟")
                db.end_active_game(user_id)
                send_message(
                    chat_id,
                    f"⏰ وقتت برای جعبه رمز تموم شد!\nرمز درست {secret} بود.",
                )

            else:
                db.end_active_game(user_id)

        except Exception:
            # اگه رسیدگی عادی خطا داد، به‌جای گیر کردن بازی برای همیشه (که یعنی
            # کاربر دیگه هیچ‌وقت پیام یا نتیجه‌ای نمی‌بینه و نمی‌تونه بازی جدید
            # شروع کنه)، بازی رو می‌بندیم و شرط رو به‌خاطر باگ برمی‌گردونیم.
            print("خطا در رسیدگی به یه بازی منقضی‌شده:")
            traceback.print_exc()
            try:
                db.end_active_game(user_id)
                db._credit(user_id, bet)
                send_message(
                    chat_id,
                    "⚠️ یه مشکل فنی تو رسیدگی به این بازی پیش اومد، بازی لغو شد و شرطت برگشت.",
                )
            except Exception:
                traceback.print_exc()




def parse_amount_after_prefix(text, prefix):
    """بعد از یه پیشوند دستور، عدد رو استخراج می‌کنه (فارسی/عربی/انگلیسی، با یا بدون ویرگول)."""
    amount_text = normalize_digits(text[len(prefix):]).strip()
    amount_text = amount_text.replace(",", "").replace("،", "").strip()
    match = re.search(r"\d+", amount_text)
    if not match:
        return None
    return int(match.group())


def handle_transfer(chat_id, sender_id, message_id, text, reply_to_message_id):
    sender_name = db.get_username(sender_id)
    if not sender_name:
        send_message(
            chat_id,
            "هنوز اسمتو نمی‌دونم! اول بنویس:\nتنظیم نام <اسمت>",
            reply_to_message_id=message_id,
        )
        return

    if not reply_to_message_id:
        send_message(
            chat_id,
            "برای انتقال کوین باید روی پیام همون گورباحی که می‌خوای بهش بدی ریپلای بزنی و بنویسی:\nانتقال کوین <عدد>",
            reply_to_message_id=message_id,
        )
        return

    receiver_id = db.get_sender_of_message(reply_to_message_id)
    if not receiver_id:
        send_message(
            chat_id,
            "نتونستم بفهمم این پیام برای کیه (شاید خیلی قدیمیه). یه پیام تازه از طرف مقابل پیدا کن و روش ریپلای بزن.",
            reply_to_message_id=message_id,
        )
        return

    if str(receiver_id) == str(sender_id):
        send_message(
            chat_id,
            f"نمی‌تونی به خودت {CURRENCY_NAME} انتقال بدی 😹",
            reply_to_message_id=message_id,
        )
        return

    amount = parse_amount_after_prefix(text, TRANSFER_PREFIX)
    if amount is None:
        send_message(
            chat_id,
            "بعد از «انتقال کوین» مقدار رو بنویس، مثلاً:\nانتقال کوین 1000",
            reply_to_message_id=message_id,
        )
        return

    receiver_name = db.get_username(receiver_id) or "ناشناس"
    # مطمئن می‌شیم گیرنده هم ردیف دیتابیس داره (حتی اگه هنوز میو نکرده باشه)
    db.get_or_create_user(receiver_id, receiver_name if receiver_name != "ناشناس" else None)

    ok, result = db.transfer_points(sender_id, receiver_id, amount)

    if ok:
        msg = build_transfer_success_message(
            cosmetic_name_prefix(sender_id, sender_name),
            cosmetic_name_prefix(receiver_id, receiver_name),
            amount, result["receiver_new_points"]
        )
        send_message(chat_id, msg, reply_to_message_id=message_id)
        return

    reason = result["reason"]
    if reason == "below_min":
        send_message(
            chat_id,
            f"حداقل مقدار انتقال {format_number(result['min'])} {CURRENCY_NAME}ه.",
            reply_to_message_id=message_id,
        )
    elif reason == "above_max":
        send_message(
            chat_id,
            f"حداکثر مقدار انتقال {format_number(result['max'])} {CURRENCY_NAME}ه.",
            reply_to_message_id=message_id,
        )
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"تو درحال حاضر این مقدار {CURRENCY_NAME} رو نداری 😿\nموجودی فعلیت: {format_number(result['have'])} 🪙",
            reply_to_message_id=message_id,
        )
    elif reason == "self":
        send_message(
            chat_id,
            f"نمی‌تونی به خودت {CURRENCY_NAME} انتقال بدی 😹",
            reply_to_message_id=message_id,
        )
    else:
        send_message(
            chat_id,
            "یه مشکلی پیش اومد، دوباره امتحان کن.",
            reply_to_message_id=message_id,
        )


def handle_force_shop_cycle(chat_id, sender_id, message_id, text):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    rest = normalize_digits(text[len(SHOP_CYCLE_FORCE_COMMAND):]).strip()
    number_match = re.search(r"\d+", rest)

    if not number_match:
        # بدون شماره: به‌جای چرخش تصادفی، لیست چرخه‌ها رو نشون بده تا
        # ادمین دقیقاً بدونه باید چه شماره‌ای بفرسته.
        cycles = db.list_shop_cycles()
        current = db.get_current_shop_cycle()
        current_id = current["id"] if current else None
        lines = ["🛠 برای فعال کردن یه چرخه، بنویس: چرخش فروشگاه <شماره>\n"]
        for i, cycle in enumerate(cycles, start=1):
            mark = "  ← فعلاً همینه" if cycle["id"] == current_id else ""
            lines.append(f"{i}. {cycle['name']}{mark}")
        send_message(chat_id, "\n".join(lines), reply_to_message_id=message_id)
        return

    cycle_number = int(number_match.group())
    ok, result = db.set_shop_cycle_by_number(cycle_number)
    if not ok:
        send_message(
            chat_id,
            "چرخه‌ای با این شماره پیدا نشد. بنویس «چرخش فروشگاه» تنها (بدون شماره) تا لیست کامل رو ببینی.",
            reply_to_message_id=message_id,
        )
        return
    send_message(chat_id, f"🔄 فروشگاه رفت رو: {result['name']}", reply_to_message_id=message_id)


def resolve_admin_target(chat_id, message_id, reply_to_message_id, target_text, command_hint):
    """
    هدف یه دستور ادمین رو یا از ریپلای، یا از اسم نوشته‌شده پیدا می‌کنه.
    خروجی: (target_id, target_name) اگه پیدا شد، وگرنه (None, None) —
    که تو این حالت خودش پیام مناسب (خطا/راهنما) رو فرستاده و کار تمومه.
    """
    if reply_to_message_id:
        target_id = db.get_sender_of_message(reply_to_message_id)
        if not target_id:
            send_message(chat_id, "نتونستم بفهمم این پیام برای کیه (شاید خیلی قدیمیه).", reply_to_message_id=message_id)
            return None, None
        return target_id, db.get_username(target_id) or "ناشناس"

    if target_text:
        matches = db.find_user_ids_by_username(target_text)
        if not matches:
            send_message(chat_id, f"هیچ کاربری با اسم «{target_text}» پیدا نشد.", reply_to_message_id=message_id)
            return None, None
        if len(matches) > 1:
            send_message(
                chat_id,
                f"چند نفر با اسم «{target_text}» پیدا شدن! برای دقیق‌تر بودن، روی پیام همون فرد ریپلای بزن و بنویس «{command_hint}».",
                reply_to_message_id=message_id,
            )
            return None, None
        target_id = matches[0]
        return target_id, db.get_username(target_id) or "ناشناس"

    send_message(chat_id, "یا روی پیام همون کاربر ریپلای بزن، یا اسمشو بنویس.", reply_to_message_id=message_id)
    return None, None


def split_target_and_keyword(raw_text):
    """
    وقتی «فعال کن» بدون ریپلای استفاده بشه، هم اسم هدف و هم کلیدواژه‌ی
    آیتم می‌تونن چندکلمه‌ای باشن، پس نمی‌شه ساده از رو فاصله جداشون کرد.
    به‌جاش، طولانی‌ترین کلیدواژه‌ی واقعیِ فروشگاه که آخر متن اومده رو
    پیدا می‌کنیم؛ هرچی قبلش مونده، اسم هدفه.
    """
    keywords = sorted(db.get_all_shop_keywords(), key=len, reverse=True)
    for kw in keywords:
        if raw_text == kw:
            return "", kw
        if raw_text.endswith(" " + kw):
            return raw_text[: -(len(kw) + 1)].strip(), kw
    return None, None


def handle_admin_grant_item(chat_id, sender_id, message_id, text, reply_to_message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    raw = text[len(GRANT_ITEM_PREFIX):].strip()
    if not raw:
        send_message(
            chat_id,
            f"یا روی پیام همون کاربر ریپلای بزن و بنویس «{GRANT_ITEM_PREFIX} <کلیدواژه آیتم>»،\nیا بنویس: {GRANT_ITEM_PREFIX} <اسم اکانتش> <کلیدواژه آیتم>",
            reply_to_message_id=message_id,
        )
        return

    if reply_to_message_id:
        keyword = raw
        target_text = ""
    else:
        target_text, keyword = split_target_and_keyword(raw)
        if keyword is None:
            send_message(chat_id, "نتونستم کلیدواژه‌ی هیچ آیتمی رو تو این متن پیدا کنم.", reply_to_message_id=message_id)
            return

    target_id, target_name = resolve_admin_target(chat_id, message_id, reply_to_message_id, target_text, GRANT_ITEM_PREFIX)
    if not target_id:
        return

    db.get_or_create_user(target_id, target_name if target_name != "ناشناس" else None)

    ok, info = db.grant_item(target_id, keyword)
    if not ok:
        reason = info.get("reason")
        if reason == "not_found":
            send_message(chat_id, "همچین آیتمی تو فروشگاه پیدا نکردم.", reply_to_message_id=message_id)
        elif reason == "limit_reached":
            send_message(chat_id, f"این کاربر قبلاً به سقف مجاز این آیتم رسیده ({info.get('max_per_user')} تا).", reply_to_message_id=message_id)
        else:
            send_message(chat_id, "یه مشکلی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)
        return

    db.record_admin_action(sender_id, target_id, 0, "grant_item")
    item = info["item"]
    send_message(
        chat_id,
        f"✅ {item['name']} برای {target_name} فعال شد.",
        reply_to_message_id=message_id,
    )


def handle_charge(chat_id, sender_id, message_id, text, reply_to_message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    raw = text[len(CHARGE_PREFIX):].strip()
    target_text = ""
    amount_text = raw
    if not reply_to_message_id and " " in raw:
        target_text, _, amount_text = raw.rpartition(" ")
        target_text = target_text.strip()

    amount_norm = normalize_digits(amount_text).strip()
    amount = int(amount_norm) if amount_norm.lstrip("-").isdigit() else None
    if amount is None or amount <= 0:
        send_message(
            chat_id,
            f"یا روی پیام همون کاربر ریپلای بزن و بنویس «{CHARGE_PREFIX} <عدد>»،\nیا بنویس: {CHARGE_PREFIX} <اسم اکانتش> <عدد>",
            reply_to_message_id=message_id,
        )
        return

    target_id, target_name = resolve_admin_target(chat_id, message_id, reply_to_message_id, target_text, CHARGE_PREFIX)
    if not target_id:
        return

    db.get_or_create_user(target_id, target_name if target_name != "ناشناس" else None)

    new_points = db.add_points(target_id, amount)
    db.record_admin_action(sender_id, target_id, amount, "charge")

    send_message(
        chat_id,
        (
            "✅ شارژ با موفقیت انجام شد.\n\n"
            f"🐈 گورباح {target_name}\n"
            f"💰 +{format_number(amount)} 🪙\n"
            f"💰 موجودی جدید: {format_number(new_points)} 🪙"
        ),
        reply_to_message_id=message_id,
    )


def handle_reset(chat_id, sender_id, message_id, reply_to_message_id, target_text=""):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    target_id = None
    if reply_to_message_id:
        target_id = db.get_sender_of_message(reply_to_message_id)
        if not target_id:
            send_message(chat_id, "نتونستم بفهمم این پیام برای کیه (شاید خیلی قدیمیه).", reply_to_message_id=message_id)
            return
    elif target_text:
        matches = db.find_user_ids_by_username(target_text)
        if not matches:
            send_message(chat_id, f"هیچ کاربری با اسم «{target_text}» پیدا نشد.", reply_to_message_id=message_id)
            return
        if len(matches) > 1:
            send_message(
                chat_id,
                f"چند نفر با اسم «{target_text}» پیدا شدن! برای دقیق‌تر بودن، روی پیام همون فرد ریپلای بزن و بنویس «{RESET_COMMAND}».",
                reply_to_message_id=message_id,
            )
            return
        target_id = matches[0]
    else:
        send_message(
            chat_id,
            f"یا روی پیام همون کاربر ریپلای بزن و بنویس «{RESET_COMMAND}»،\nیا بنویس: {RESET_COMMAND} <اسم اکانتش>",
            reply_to_message_id=message_id,
        )
        return

    target_name = db.get_username(target_id) or "ناشناس"
    updated = db.reset_user_points(target_id)
    if not updated:
        send_message(chat_id, "این کاربر هنوز تو دیتابیس ثبت نشده، چیزی برای ریست کردن نیست.", reply_to_message_id=message_id)
        return

    db.record_admin_action(sender_id, target_id, 0, "reset")
    send_message(
        chat_id,
        f"✅ موجودی {target_name} صفر شد.",
        reply_to_message_id=message_id,
    )


def handle_reset_name(chat_id, sender_id, message_id, reply_to_message_id, target_text=""):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    target_id, target_name = resolve_admin_target(chat_id, message_id, reply_to_message_id, target_text, RESET_NAME_COMMAND)
    if not target_id:
        return

    updated = db.reset_username(target_id)
    if not updated:
        send_message(chat_id, "این کاربر هنوز تو دیتابیس ثبت نشده، چیزی برای ریست کردن نیست.", reply_to_message_id=message_id)
        return

    db.record_admin_action(sender_id, target_id, 0, "reset_name")
    send_message(chat_id, f"✅ اسم {target_name} پاک شد؛ دفعه‌ی بعد باید دوباره برای خودش اسم بذاره.", reply_to_message_id=message_id)


def handle_cleanup_names(chat_id, sender_id, message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    matches = db.find_users_with_blocked_names()
    if not matches:
        send_message(chat_id, "هیچ اکانتی با اسم نامناسب پیدا نشد. 👍", reply_to_message_id=message_id)
        return

    for user_id, _ in matches:
        db.full_reset_user_account(user_id)
        db.record_admin_action(sender_id, user_id, 0, "full_reset_bad_name")

    send_message(
        chat_id,
        f"🧹 {len(matches)} اکانت با اسم نامناسب پیدا و کامل ریست شد (کوین، سطح، شرکت، کیف آیتم — همه صفر شد).",
        reply_to_message_id=message_id,
    )


def handle_cleanup_cosmetic_names(chat_id, sender_id, message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    bad_users, bad_companies = db.find_users_with_reserved_symbols()
    if not bad_users and not bad_companies:
        send_message(chat_id, "هیچ اسم یا اسم‌شرکتی با نماد/ایموجی جعلی پیدا نشد. 👍", reply_to_message_id=message_id)
        return

    for user_id, _ in bad_users:
        db.reset_username(user_id)
        db.record_admin_action(sender_id, user_id, 0, "reset_cosmetic_name")
    for owner_id, _ in bad_companies:
        db.reset_cat_name(owner_id)
        db.record_admin_action(sender_id, owner_id, 0, "reset_cosmetic_company_name")

    send_message(
        chat_id,
        (
            f"🧹 {len(bad_users)} اسم کاربر و {len(bad_companies)} اسم شرکت با نماد/ایموجی جعلی پاک شد.\n"
            "فقط خودِ اسم‌ها ریست شدن (کوین و بقیه‌ی اکانت دست‌نخورده موند)."
        ),
        reply_to_message_id=message_id,
    )


def handle_admin_help(chat_id, sender_id, message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    msg = (
        "🛠 دستورات مخصوص ادمین\n\n"
        f"{CHARGE_PREFIX} <عدد>  (روی ریپلای)\n"
        f"{CHARGE_PREFIX} <اسم اکانت> <عدد>  (بدون ریپلای)\n"
        "└ شارژ کوین برای یه کاربر\n\n"
        f"{GRANT_ITEM_PREFIX} <کلیدواژه آیتم>  (روی ریپلای)\n"
        f"{GRANT_ITEM_PREFIX} <اسم اکانت> <کلیدواژه آیتم>  (بدون ریپلای)\n"
        "└ فعال‌سازی رایگان یه آیتم فروشگاه برای یه کاربر (بدون کم شدن کوین) — برای فروش دستی چرخه‌ی VIP\n\n"
        f"{AD_BROADCAST_PREFIX} همه <متن آگهی>\n"
        f"{AD_BROADCAST_PREFIX} <عدد> <متن آگهی>\n"
        "└ ارسال آگهی به همه‌ی گروه‌ها یا فقط تعداد مشخصی از گروه‌ها\n\n"
        f"{AD_DELETE_COMMAND}\n"
        "└ پاک کردن آخرین آگهی ارسالی از همه‌ی گروه‌ها\n\n"
        f"{SHOP_CYCLE_FORCE_COMMAND}\n"
        f"{SHOP_CYCLE_FORCE_COMMAND} <شماره ۱ تا ۶>\n"
        "└ بدون شماره: نمایش لیست چرخه‌ها. با شماره: فعال کردن همون چرخه‌ی مشخص (بدون تصادفی بودن)\n\n"
        f"{DAILY_GIFT_CODE_ADMIN_COMMAND}\n"
        "└ تو همون گروه: اگه امروز کد هدیه هنوز ساخته نشده، می‌سازه؛ اگه ساخته شده، همونو دوباره نشون می‌ده\n\n"
        f"{RESET_COMMAND}  (روی ریپلای)\n"
        f"{RESET_COMMAND} <اسم اکانت>  (بدون ریپلای)\n"
        "└ صفر کردن موجودی یه کاربر\n\n"
        f"{RESET_NAME_COMMAND}  (روی ریپلای)\n"
        f"{RESET_NAME_COMMAND} <اسم اکانت>  (بدون ریپلای)\n"
        "└ پاک کردن اسم انتخابی یه کاربر\n\n"
        f"{CLEANUP_NAMES_COMMAND}\n"
        f"{CLEANUP_COSMETIC_NAMES_COMMAND}\n"
        "└ پیدا کردن و کامل‌ریست‌کردن اکانت‌هایی که اسم نامناسب دارن\n\n"
        f"{GROUP_COUNT_COMMAND}\n"
        "└ تعداد گروه‌هایی که بات توشونه\n\n"
        f"{PLAYER_COUNT_COMMAND}\n"
        "└ تعداد کل بازیکن‌های ثبت‌شده\n\n"
        f"{ADMIN_HELP_COMMAND}\n"
        "└ همین لیست"
    )
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_group_count(chat_id, sender_id, message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return
    count = db.get_group_count()
    send_message(chat_id, f"👥 بات الان تو {format_number(count)} گروه فعالیت داره.", reply_to_message_id=message_id)


def handle_player_count(chat_id, sender_id, message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return
    count = db.get_player_count()
    send_message(chat_id, f"🐾 تعداد کل بازیکن‌های ثبت‌شده: {format_number(count)}", reply_to_message_id=message_id)


# ---------------------------------------------------------------------------
# سیستم شرکت
# ---------------------------------------------------------------------------

def cat_title_for_rank(rank):
    return CAT_TITLES.get(rank, f"شرکت رتبه {rank}")


def build_cat_weak_message():
    return (
        "🚧 هنوز برای ثبت شرکت آماده نیستی!\n\n"
        "اداره ثبت شرکت‌های PawKing درخواستت رو بخاطر نوب بودن و اینکه هنوز نوچه‌ای رد کرد. 😹\n\n"
        f"⭐ حداقل سطح حساب موردنیاز: {db.CAT_MIN_LEVEL}\n"
        "فعلاً میو کن، امتیاز جمع کن و دوباره برگرد."
    )


def build_cat_shelter_message():
    return (
        "🏢 وقتشه اولین شرکتت رو تأسیس کنی!\n\n"
        "از این به بعد دیگه فقط با میو کردن پول درنمیاری... شرکتت هم شبانه‌روز برات سود جمع می‌کنه. 💰\n\n"
        f"📦 هزینه تأسیس: {format_number(db.CAT_PRICE)} 🪙  ⭐ حداقل سطح حساب: {db.CAT_MIN_LEVEL}\n\n"
        "بعد از خرید می‌تونی شرکتت رو ارتقا بدی تا درآمدش بیشتر بشه.\n\n"
        f"✨ برای ادامه بنویس: {COMPANY_BUY_COMMAND}"
    )


def build_cat_adopt_success_message():
    return (
        "🏢 تبریک! شرکتت به‌طور رسمی ثبت شد!\n\n"
        "از امروز، شرکتت شبانه‌روز برات سود جمع می‌کنه. 💰\n\n"
        f"📊 برای دیدن وضعیتش بنویس: {COMPANY_STATUS_WORDS[1]}"
    )


def build_cat_status_message(owner_display_name, cat):
    rank = cat["rank"]
    level = cat["level"]
    rank_cap = db.cat_rank_cap(rank)
    owner_id = cat["owner_id"]
    per_hour = db.cat_production_per_hour(rank, level, owner_id)
    per_second = round(per_hour / 3600)
    capacity = db.cat_capacity(rank, level, owner_id)

    now = datetime.datetime.utcnow()
    elapsed_hours = (now - cat["last_collect_at"]).total_seconds() / 3600
    pending = min(capacity, per_hour * elapsed_hours)
    pending = int(pending)

    lines = [
        f"🏢 شرکت {owner_display_name} 💼",
        "",
        f"💕 نام : {cat['name'] or '—'}",
        "",
        f"🌟 مقام : {cat_title_for_rank(rank)} ({rank})",
        f"⭐️ سطح شرکت : {level} / {rank_cap}",
        "",
        f"💰 سود جمع‌شده در خزانه : {format_number(pending)} 🪙",
        f"💫 تولید کوین در ثانیه : {format_number(per_second)} 🪙",
        f"📦 ظرفیت خزانه : {format_number(capacity)}",
        "",
    ]
    if db.cat_is_maxed(rank, level):
        lines.append("🏆 این شرکت به حداکثر رتبه و سطح شرکت ممکن رسیده!")
    else:
        cost = db.cat_upgrade_cost(rank, level)
        lines.append(f"💰 هزینه توسعه : {format_number(cost)} 🪙")
        if rank < db.CAT_MAX_RANK:
            next_required = db.cat_required_account_level(rank + 1)
            if level >= rank_cap:
                lines.append(f"🎯 برای رتبه‌ی بعدی، سطح حساب حداقل {next_required} لازمه.")

    return "\n".join(lines)


def handle_cat_status(chat_id, sender_id, message_id, sender_name):
    profile = db.get_profile(sender_id)
    level = profile["level"] if profile else 0

    cat = db.get_cat(sender_id)
    if cat:
        msg = build_cat_status_message(sender_name or "ناشناس", cat)
        send_message(chat_id, msg, reply_to_message_id=message_id)
        return

    if level < db.CAT_MIN_LEVEL:
        send_message(chat_id, build_cat_weak_message(), reply_to_message_id=message_id)
        return

    send_message(chat_id, build_cat_shelter_message(), reply_to_message_id=message_id)


def handle_cat_buy(chat_id, sender_id, message_id, sender_name):
    profile = db.get_profile(sender_id)
    level = profile["level"] if profile else 0

    if level < db.CAT_MIN_LEVEL:
        send_message(chat_id, build_cat_weak_message(), reply_to_message_id=message_id)
        return

    ok, result = db.buy_cat(sender_id)
    if ok:
        send_message(chat_id, build_cat_adopt_success_message(), reply_to_message_id=message_id)
        return

    reason = result["reason"]
    if reason == "already_has_cat":
        send_message(chat_id, "تو همین الان یه شرکت داری! نمی‌تونی بیشتر از یکی تأسیس کنی 💼", reply_to_message_id=message_id)
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"برای تأسیس شرکت به {format_number(db.CAT_PRICE)} 🪙 نیاز داری.",
            reply_to_message_id=message_id,
        )
    else:
        send_message(chat_id, "یه مشکلی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)


def handle_cat_collect(chat_id, sender_id, message_id, sender_name):
    ok, result = db.collect_cat_points(sender_id)
    if ok:
        send_message(
            chat_id,
            (
                "💼 حسابدار شرکت گزارش داد...\n\n"
                f"💰 {format_number(result['collected'])} 🪙 سود داخل خزانه شرکت جمع شده بود.\n"
                f"🏦 موجودی جدیدت: {format_number(result['new_points'])} 🪙\n\n"
                "مدیرعامل! شرکت دوباره شروع کرد به جمع‌آوری سود. 📈"
            ),
            reply_to_message_id=message_id,
        )
        return

    reason = result["reason"]
    if reason == "no_cat":
        send_message(chat_id, f"هنوز شرکتی نداری! اول بنویس «{COMPANY_BUY_COMMAND}» تا یکی تأسیس کنی 💼", reply_to_message_id=message_id)
    else:
        send_message(chat_id, "🏦 هنوز سودی تو خزانه‌ی شرکت جمع نشده، بعداً دوباره سر بزن.", reply_to_message_id=message_id)


def build_shop_message():
    items = db.get_shop_items()
    cycle = db.get_current_shop_cycle()
    if cycle:
        header = f"🏪 فروشگاه PawKing\n\n{cycle['name']}"
        if cycle.get("description"):
            header += f"\n{cycle['description']}"
    else:
        header = "🏪 فروشگاه PawKing"
    header += "\n\n⚔️💎 بخش‌های «جنگ مدیرعامل‌ها» و «VIP» همیشه در دسترسن، فارغ از اینکه فروشگاه الان رو کدوم چرخه چرخیده."
    if not items:
        return f"{header}\n\nاین چرخه فعلاً آیتمی نداره، بعداً دوباره سر بزن."

    lines = [header, ""]
    current_category = None
    for item in items:
        if item["category"] != current_category:
            current_category = item["category"]
            lines.append(f"— {current_category} —")
        if item.get("is_vip_only"):
            lines.append(f"{item['name']} — 💎 فقط با هماهنگی ادمین (VIP)")
        else:
            lines.append(f"{item['name']} — {format_number(item['price'])} 🪙")
        lines.append(f"└ {item['description']}")
        if item["max_per_user"]:
            lines.append(f"   (هر نفر حداکثر {item['max_per_user']} بار)")
        if item.get("is_vip_only"):
            lines.append("   برای فعال‌سازی: با ادمین هماهنگ کن")
        else:
            lines.append(f"   برای خرید: «{BUY_ITEM_PREFIX} {item['keyword']}»")
        lines.append("")

    return "\n".join(lines).strip()


def handle_shop(chat_id, message_id):
    send_message(chat_id, build_shop_message(), reply_to_message_id=message_id)


def handle_buy_item(chat_id, sender_id, message_id, text):
    keyword = text[len(BUY_ITEM_PREFIX):].strip()
    if not keyword:
        send_message(chat_id, f"باید بگی چی می‌خوای بخری، مثلاً: «{BUY_ITEM_PREFIX} قهوه»", reply_to_message_id=message_id)
        return

    ok, result = db.buy_item(sender_id, keyword)
    if ok:
        item = result["item"]
        if item["category"] == "ارتقای شرکت":
            extra = "این ارتقا دائمیه و از همین الان خودکار فعاله، نیازی به «استفاده» نداره."
        else:
            extra = f"برای استفاده بنویس: «{USE_ITEM_PREFIX} {item['keyword']}»"
        send_message(
            chat_id,
            (
                f"✅ {item['name']} خریداری شد!\n\n"
                f"💸 هزینه: {format_number(item['price'])} 🪙\n"
                f"💰 موجودی باقی‌مونده: {format_number(result['new_points'])} 🪙\n\n"
                f"{extra}"
            ),
            reply_to_message_id=message_id,
        )
        return

    reason = result["reason"]
    if reason == "not_found":
        send_message(chat_id, "چنین آیتمی تو فروشگاه نیست. برای دیدن لیست بنویس «فروشگاه».", reply_to_message_id=message_id)
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"برای این خرید به {format_number(result['price'])} 🪙 نیاز داری، ولی موجودیت کمتره.",
            reply_to_message_id=message_id,
        )
    elif reason == "limit_reached":
        send_message(
            chat_id,
            f"تو قبلاً این آیتم رو حداکثر تعداد مجاز ({result['max_per_user']} بار) خریده بودی.",
            reply_to_message_id=message_id,
        )
    elif reason == "vip_only":
        send_message(
            chat_id,
            "این آیتم مخصوص چرخه‌ی VIPه و با کوین خریداری نمی‌شه — برای خرید باید با ادمین هماهنگ کنی.",
            reply_to_message_id=message_id,
        )
    elif reason == "cycle_inactive":
        send_message(
            chat_id,
            "این آیتم الان تو چرخه‌ی فعلی فروشگاه نیست. برای دیدن آیتم‌های الان بنویس «فروشگاه».",
            reply_to_message_id=message_id,
        )
    else:
        send_message(chat_id, "یه مشکلی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)


def handle_inventory(chat_id, sender_id, message_id):
    items = db.get_inventory(sender_id)
    if not items:
        send_message(chat_id, "🎒 کیفت خالیه! برای خرید آیتم بنویس «فروشگاه».", reply_to_message_id=message_id)
        return

    lines = ["🎒 کیف آیتم‌هات", ""]
    for item in items:
        lines.append(f"{item['name']} × {item['quantity']}")
        lines.append(f"└ {item['description']}")
        if item["category"] == "ارتقای شرکت":
            lines.append("   (دائمی، از قبل فعاله)")
        else:
            lines.append(f"   برای استفاده: «{USE_ITEM_PREFIX} {item['keyword']}»")
        lines.append("")

    send_message(chat_id, "\n".join(lines).strip(), reply_to_message_id=message_id)


def handle_redeem_gift_code(chat_id, sender_id, message_id, text, sender_name):
    code_text = text[len(GIFT_CODE_PREFIX):].strip()
    if not code_text:
        send_message(chat_id, f"باید کد رو بعدش بنویسی، مثلاً: «{GIFT_CODE_PREFIX} AB12CD»", reply_to_message_id=message_id)
        return

    db.get_or_create_user(sender_id, sender_name)
    ok, result = db.redeem_gift_code(sender_id, code_text)
    if ok:
        send_message(
            chat_id,
            (
                f"🎁 کد هدیه فعال شد!\n\n"
                f"🪙 +{format_number(result['coins'])} کوین پیشی\n"
                f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙\n\n"
                f"(این کد {result['remaining_uses']} استفاده‌ی دیگه داره)"
            ),
            reply_to_message_id=message_id,
        )
        return

    reason = result["reason"]
    if reason == "invalid_code":
        send_message(chat_id, "این کد معتبر نیست.", reply_to_message_id=message_id)
    elif reason == "fully_used":
        send_message(chat_id, "ظرفیت این کد پر شده، دیگه قابل استفاده نیست.", reply_to_message_id=message_id)
    elif reason == "already_redeemed":
        send_message(chat_id, "تو قبلاً همین کد رو استفاده کردی.", reply_to_message_id=message_id)
    else:
        send_message(chat_id, "یه مشکلی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)


def handle_ad_broadcast(chat_id, sender_id, message_id, text):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    rest = text[len(AD_BROADCAST_PREFIX):].strip()
    if not rest:
        send_message(
            chat_id,
            f"بنویس: «{AD_BROADCAST_PREFIX} همه <متن آگهی>» یا «{AD_BROADCAST_PREFIX} <عدد> <متن آگهی>»",
            reply_to_message_id=message_id,
        )
        return

    parts = rest.split(" ", 1)
    if len(parts) < 2:
        send_message(chat_id, "بعد از تعداد گروه (یا کلمه‌ی «همه»)، متن آگهی رو هم بنویس.", reply_to_message_id=message_id)
        return

    target_spec, ad_text = parts[0], parts[1]
    if target_spec == "همه":
        target_ids = db.get_all_group_chat_ids()
    else:
        digits = normalize_digits(target_spec)
        if not digits.isdigit():
            send_message(chat_id, "بعد از «تبلیغ» یا «همه» بنویس یا یه عدد (تعداد گروه).", reply_to_message_id=message_id)
            return
        target_ids = db.get_group_chat_ids_by_size(limit=int(digits))

    if not target_ids:
        send_message(chat_id, "هیچ گروهی برای ارسال پیدا نشد.", reply_to_message_id=message_id)
        return

    broadcast_id = db.start_ad_broadcast()
    sent_count = 0
    for group_chat_id in target_ids:
        try:
            sent_message_id = send_message_get_id(group_chat_id, ad_text)
            db.record_ad_broadcast_message(broadcast_id, group_chat_id, sent_message_id)
            sent_count += 1
        except Exception:
            print(f"خطا در ارسال تبلیغ به {group_chat_id}:")
            traceback.print_exc()

    send_message(
        chat_id,
        (
            f"✅ آگهی به {format_number(sent_count)} گروه فرستاده شد.\n\n"
            f"برای پاک کردنش (اگه شناسه‌ی پیام‌ها گرفته شده باشه) بنویس: «{AD_DELETE_COMMAND}»"
        ),
        reply_to_message_id=message_id,
    )


def handle_ad_delete(chat_id, sender_id, message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    targets = db.get_latest_ad_broadcast_messages()
    if not targets:
        send_message(chat_id, "هیچ تبلیغ قابل‌حذفی پیدا نشد (یا شناسه‌ی پیام‌هاش گرفته نشده بود).", reply_to_message_id=message_id)
        return

    deleted_count = 0
    for group_chat_id, ad_message_id in targets:
        if delete_message(group_chat_id, ad_message_id):
            deleted_count += 1

    send_message(
        chat_id,
        f"🗑 از {format_number(len(targets))} گروه، {format_number(deleted_count)} تا پاک شد.",
        reply_to_message_id=message_id,
    )


def handle_equip_title(chat_id, sender_id, message_id, text):
    keyword = text[len(EQUIP_TITLE_PREFIX):].strip()
    if not keyword:
        owned = db.get_owned_cosmetic_keywords(sender_id, "cosmetic_title")
        if not owned:
            send_message(chat_id, "هنوز هیچ لقبی نخریدی. تو فروشگاه، چرخه‌ی «اشراف‌زادگان» رو نگاه کن.", reply_to_message_id=message_id)
        else:
            send_message(chat_id, "لقب‌هایی که داری:\n" + "\n".join(f"• {t}" for t in owned) + f"\n\nبرای فعال کردن: «{EQUIP_TITLE_PREFIX} <لقب>»", reply_to_message_id=message_id)
        return
    if db.set_active_title(sender_id, keyword):
        send_message(chat_id, f"🏷️ لقبت شد: {keyword}", reply_to_message_id=message_id)
    else:
        send_message(chat_id, "این لقب رو نداری. برای دیدن لقب‌هات فقط بنویس «لقبم».", reply_to_message_id=message_id)


def handle_equip_theme(chat_id, sender_id, message_id, text):
    keyword = text[len(EQUIP_THEME_PREFIX):].strip()
    if not keyword:
        owned = db.get_owned_cosmetic_keywords(sender_id, "cosmetic_theme")
        if not owned:
            send_message(chat_id, "هنوز هیچ تمی نخریدی. تو فروشگاه، چرخه‌ی «اشراف‌زادگان» رو نگاه کن.", reply_to_message_id=message_id)
        else:
            send_message(chat_id, "تم‌هایی که داری:\n" + "\n".join(f"• {t}" for t in owned) + f"\n\nبرای فعال کردن: «{EQUIP_THEME_PREFIX} <تم>»", reply_to_message_id=message_id)
        return
    if db.set_active_theme(sender_id, keyword):
        send_message(chat_id, f"🎨 تمت شد: {keyword}", reply_to_message_id=message_id)
    else:
        send_message(chat_id, "این تم رو نداری. برای دیدن تم‌هات فقط بنویس «تمم».", reply_to_message_id=message_id)


def handle_codebreak_guess(chat_id, sender_id, message_id, text):
    guess = normalize_digits(text[len(CODEBREAK_GUESS_PREFIX):]).strip()
    guess = re.sub(r"\D", "", guess)
    if len(guess) != 3:
        send_message(chat_id, f"باید یه عدد سه‌رقمی حدس بزنی، مثلاً: «{CODEBREAK_GUESS_PREFIX} 537»", reply_to_message_id=message_id)
        return

    ok, result = db.submit_codebreak_guess(sender_id, guess)
    if not ok:
        reason = result["reason"]
        if reason == "no_game":
            send_message(chat_id, "بازی جعبه‌رمزی برات فعال نیست. اول با «استفاده از جعبه رمز» شروعش کن.", reply_to_message_id=message_id)
        else:
            send_message(chat_id, f"باید یه عدد سه‌رقمی حدس بزنی، مثلاً: «{CODEBREAK_GUESS_PREFIX} 537»", reply_to_message_id=message_id)
        return

    feedback = " ".join(result["feedback"])
    if result["solved"]:
        send_message(
            chat_id,
            f"🧩 {guess}\n{feedback}\n\n🎉 رمز رو باز کردی!!\n💰 +{format_number(result['reward'])} 🪙\n\nموجودی جدید: {format_number(result['new_points'])} 🪙",
            reply_to_message_id=message_id,
        )
    elif result["attempts_left"] == 0:
        send_message(
            chat_id,
            f"🧩 {guess}\n{feedback}\n\n😿 تلاش‌هات تموم شد. رمز درست {result['secret']} بود.",
            reply_to_message_id=message_id,
        )
    else:
        send_message(
            chat_id,
            f"🧩 {guess}\n{feedback}\n\n{result['attempts_left']} تلاش دیگه داری.",
            reply_to_message_id=message_id,
        )


def handle_use_item(chat_id, sender_id, message_id, text, reply_to_message_id):
    keyword = text[len(USE_ITEM_PREFIX):].strip()
    if not keyword:
        send_message(chat_id, f"باید بگی از چی می‌خوای استفاده کنی، مثلاً: «{USE_ITEM_PREFIX} قهوه»", reply_to_message_id=message_id)
        return

    # کارت جاسوسی هدف می‌خواد (روی ریپلای)، پس جدا از مسیر عمومی مصرف آیتم مدیریت می‌شه.
    lookup_item = db.get_shop_item_by_keyword(keyword)
    if lookup_item and lookup_item["effect_type"] == "spy_card":
        if not reply_to_message_id:
            send_message(
                chat_id,
                f"باید روی پیام همون بازیکن ریپلای بزنی و بنویسی «{USE_ITEM_PREFIX} {lookup_item['keyword']}».",
                reply_to_message_id=message_id,
            )
            return
        target_id = db.get_sender_of_message(reply_to_message_id)
        if not target_id:
            send_message(chat_id, "نتونستم بفهمم این پیام برای کیه.", reply_to_message_id=message_id)
            return
        ok, result = db.use_spy_card(sender_id, target_id, keyword)
        if not ok:
            reason = result["reason"]
            if reason == "not_owned":
                send_message(chat_id, "از این آیتم چیزی تو کیفت نداری. برای دیدن کیفت بنویس «کیف من».", reply_to_message_id=message_id)
            elif reason == "self_target":
                send_message(chat_id, "نمی‌تونی روی خودت جاسوسی کنی 😹", reply_to_message_id=message_id)
            elif reason == "target_not_found":
                send_message(chat_id, "این بازیکن هنوز تو بات ثبت نشده.", reply_to_message_id=message_id)
            else:
                send_message(chat_id, "همچین آیتمی تو فروشگاه نیست.", reply_to_message_id=message_id)
            return
        info = result["info"]
        cat_text = f"رتبه {info['cat_rank']} سطح {info['cat_level']}" if info["cat_rank"] else "هنوز شرکت نداره"
        send_message(
            chat_id,
            (
                f"🕵️ گزارش جاسوسی درباره‌ی {info['username'] or 'ناشناس'}\n\n"
                f"📊 سطح اکانت: {info['account_level']}\n"
                f"🏢 شرکت: {cat_text}\n"
                f"💰 دارایی تقریبی: {format_number(info['approx_points'])} 🪙"
            ),
            reply_to_message_id=message_id,
        )
        return

    # حمله تبلیغاتی / بمب اقتصادی / مجوز مسابقه هم مثل کارت جاسوسی روی
    # ریپلای یه بازیکن دیگه استفاده می‌شن.
    if lookup_item and lookup_item["effect_type"] in ("ad_attack", "economic_bomb", "ceo_duel_license"):
        if not reply_to_message_id:
            send_message(
                chat_id,
                f"باید روی پیام همون بازیکن ریپلای بزنی و بنویسی «{USE_ITEM_PREFIX} {lookup_item['keyword']}».",
                reply_to_message_id=message_id,
            )
            return
        target_id = db.get_sender_of_message(reply_to_message_id)
        if not target_id:
            send_message(chat_id, "نتونستم بفهمم این پیام برای کیه.", reply_to_message_id=message_id)
            return

        effect = lookup_item["effect_type"]
        if effect == "ad_attack":
            ok, result = db.attempt_ad_attack(sender_id, target_id, keyword)
        elif effect == "economic_bomb":
            ok, result = db.attempt_economic_bomb(sender_id, target_id, keyword)
        else:
            ok, result = db.attempt_ceo_duel(sender_id, target_id, keyword)

        if not ok:
            reason = result["reason"]
            if reason == "not_owned":
                send_message(chat_id, "از این آیتم چیزی تو کیفت نداری.", reply_to_message_id=message_id)
            elif reason == "self_target":
                send_message(chat_id, "نمی‌تونی رو خودت استفاده‌ش کنی 😹", reply_to_message_id=message_id)
            elif reason == "target_shielded":
                send_message(chat_id, "🛡️ این بازیکن الان سپر داره، حمله‌ت اثر نکرد (ولی آیتمت هم مصرف نشد).", reply_to_message_id=message_id)
            elif reason == "target_no_company":
                send_message(chat_id, "این بازیکن هنوز شرکتی نداره که چیزی ازش بدزدی.", reply_to_message_id=message_id)
            elif reason == "missing_company":
                send_message(chat_id, "برای این مسابقه هر دو نفر باید شرکت داشته باشن.", reply_to_message_id=message_id)
            elif reason == "nothing_to_steal":
                send_message(chat_id, "خزانه‌ی این بازیکن الان خالیه، چیزی برای دزدیدن نبود (آیتمت مصرف نشد).", reply_to_message_id=message_id)
            else:
                send_message(chat_id, "همچین آیتمی تو فروشگاه نیست.", reply_to_message_id=message_id)
            return

        if effect in ("ad_attack", "economic_bomb"):
            if effect == "economic_bomb" and not result.get("success"):
                send_message(chat_id, "💣 بمب بی‌نتیجه ترکید! این‌بار چیزی گیرت نیومد.", reply_to_message_id=message_id)
            else:
                send_message(
                    chat_id,
                    (
                        f"{'⚡' if effect == 'ad_attack' else '💣'} حمله موفق بود!\n"
                        f"🪙 {format_number(result['stolen'])} از خزانه‌ی هدف دزدیدی.\n\n"
                        f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
                    ),
                    reply_to_message_id=message_id,
                )
            return

        # ceo_duel_license
        target_name = db.get_username(target_id) or "ناشناس"
        if result["challenger_won"]:
            send_message(
                chat_id,
                f"🎟️ نبرد تموم شد — بردی! 🏆\n💰 {format_number(result['pot'])} 🪙 از {target_name} گرفتی.",
                reply_to_message_id=message_id,
            )
        else:
            send_message(
                chat_id,
                f"🎟️ نبرد تموم شد — این‌بار {target_name} برد 😿\n💸 {format_number(result['pot'])} 🪙 از دست دادی.",
                reply_to_message_id=message_id,
            )
        return

    # مینی‌گیم‌های شهر بازی که نیاز به یه انتخاب عددی (۱ تا ۳) بعد از اسم آیتم دارن
    m = re.match(r"^(.*?)\s+([1-3۱۲۳])$", normalize_digits(keyword))
    if m:
        base_keyword, choice_str = m.group(1).strip(), m.group(2)
        base_item = db.get_shop_item_by_keyword(base_keyword)
        if base_item and base_item["effect_type"].startswith("shooting_game_"):
            ok, result = db.play_shooting_game(sender_id, base_keyword, int(choice_str))
            if not ok:
                send_message(chat_id, "این آیتم رو تو کیفت نداری یا کلیدواژه اشتباهه.", reply_to_message_id=message_id)
                return
            if result["won"]:
                msg = f"🎯 هدف رو زدی!! 🎉\n💰 +{format_number(result['reward'])} 🪙\n\nموجودی جدید: {format_number(result['new_points'])} 🪙"
            else:
                msg = f"🎯 این‌بار جا زدی 😿\nجایزه تو هدف شماره‌ی {result['winning_slot']} بود."
            send_message(chat_id, msg, reply_to_message_id=message_id)
            return
        if base_item and base_item["effect_type"] == "luck_card_game":
            ok, result = db.play_luck_card_game(sender_id, base_keyword, int(choice_str))
            if not ok:
                send_message(chat_id, "این آیتم رو تو کیفت نداری یا کلیدواژه اشتباهه.", reply_to_message_id=message_id)
                return
            msg = f"🃏 کارتت: {result['label']}"
            if result["reward"]:
                msg += f"\n💰 +{format_number(result['reward'])} 🪙\n\nموجودی جدید: {format_number(result['new_points'])} 🪙"
            send_message(chat_id, msg, reply_to_message_id=message_id)
            return

    lookup_boxing = db.get_shop_item_by_keyword(keyword)
    if lookup_boxing and lookup_boxing["effect_type"] == "boxing_game":
        ok, result = db.play_boxing_game(sender_id, keyword)
        if not ok:
            send_message(chat_id, "از این آیتم چیزی تو کیفت نداری.", reply_to_message_id=message_id)
            return
        msg = f"🥊 نتیجه‌ی ضربه‌ت: {result['label']}"
        if result["reward"]:
            msg += f"\n💰 +{format_number(result['reward'])} 🪙\n\nموجودی جدید: {format_number(result['new_points'])} 🪙"
        else:
            msg += "\nاین‌بار چیزی گیرت نیومد 😿"
        send_message(chat_id, msg, reply_to_message_id=message_id)
        return

    if lookup_boxing and lookup_boxing["effect_type"] == "codebreak_game":
        ok, result = db.start_codebreak_game(sender_id, chat_id, keyword)
        if not ok:
            reason = result["reason"]
            if reason == "game_in_progress":
                send_message(chat_id, "یه بازی نیمه‌کاره داری، اول اونو تموم کن.", reply_to_message_id=message_id)
            elif reason == "not_owned":
                send_message(chat_id, "از این آیتم چیزی تو کیفت نداری.", reply_to_message_id=message_id)
            else:
                send_message(chat_id, "همچین آیتمی تو فروشگاه نیست.", reply_to_message_id=message_id)
            return
        send_message(
            chat_id,
            (
                "🧩 یه رمز سه‌رقمی بین ۱۰۰ تا ۹۹۹ انتخاب کردم!\n"
                f"🎯 {result['attempts_left']} تلاش داری، هر تلاش {db.GAME_TIMEOUT_SECONDS} ثانیه وقت داری.\n"
                f"با «{CODEBREAK_GUESS_PREFIX} <عدد سه‌رقمی>» حدس بزن."
            ),
            reply_to_message_id=message_id,
        )
        return

    ok, result = db.use_item(sender_id, keyword)
    if ok:
        item = result["item"]
        if item["effect_type"] == "coffee_boost":
            send_message(
                chat_id,
                (
                    f"☕ {item['name']} رو مصرف کردی!\n\n"
                    "⏳ کولداون میوت صفر شد، همین الان می‌تونی دوباره میو کنی.\n"
                    "🏦 صندوق شرکتت هم (اگه شرکت داشته باشی) بدون در نظر گرفتن زمان، تا سقف پر شد — الان برو «برداشت سود شرکت» بزن!"
                ),
                reply_to_message_id=message_id,
            )
        elif item["effect_type"] == "luck_boost":
            send_message(
                chat_id,
                f"🎲 {item['name']} فعال شد! رو بازی بعدی کازینوت (سکه، تاس، بالا/پایین، اسلات، رولت، دارت) اثر می‌ذاره.",
                reply_to_message_id=message_id,
            )
        elif item["effect_type"] == "bet_insurance":
            send_message(
                chat_id,
                f"💣 {item['name']} فعال شد! اگه بازی بعدی کازینوت رو باختی، نصف شرطت برمی‌گرده.",
                reply_to_message_id=message_id,
            )
        elif item["effect_type"] in ("lootbox_common", "lootbox_silver", "lootbox_gold"):
            reward = result["reward"]
            lines = [f"📦 {item['name']} رو باز کردی...", ""]
            if reward["kind"] == "nothing":
                lines.append("😹 هیچی توش نبود! بار بعد شانس بیشتری داشته باش.")
            else:
                if reward["kind"] == "jackpot":
                    lines.append(f"🎉 جکپات زدی! +{format_number(reward['coins'])} 🪙")
                else:
                    lines.append(f"🪙 +{format_number(reward['coins'])} کوین پیشی")
                if reward["bonus_item_code"]:
                    bonus_item = db.get_shop_item_by_code(reward["bonus_item_code"])
                    bonus_name = bonus_item["name"] if bonus_item else reward["bonus_item_code"]
                    lines.append(f"🎁 یه {bonus_name} هم گرفتی!")
            lines.append(f"\n💰 موجودی جدید: {format_number(reward['new_points'])} 🪙")
            send_message(chat_id, "\n".join(lines), reply_to_message_id=message_id)
        elif item["effect_type"] == "lucky_wheel":
            reward = result["reward"]
            lines = ["🎡 چرخ گردونه می‌چرخه... 🌀", ""]
            if reward["kind"] == "nothing":
                lines.append("😹 این‌بار پوچ اومد! بار بعد شانس بیشتری داشته باش.")
            elif reward["kind"] == "jackpot":
                lines.append(f"🎉🎉 جکپات!! +{format_number(reward['coins'])} 🪙")
            else:
                lines.append(f"🪙 +{format_number(reward['coins'])} کوین پیشی")
            lines.append(f"\n💰 موجودی جدید: {format_number(reward['new_points'])} 🪙")
            send_message(chat_id, "\n".join(lines), reply_to_message_id=message_id)
        elif item["effect_type"].startswith("temp_income_boost_"):
            send_message(
                chat_id,
                (
                    f"🧪 {item['name']} فعال شد!\n\n"
                    f"⏳ برای {result['hours']} ساعت آینده، سود شرکتت {result['boost_pct']}٪ بیشتره.\n"
                    "یادت نره تو همین بازه برداشت بزنی، وگرنه اثرش از دست می‌ره!"
                ),
                reply_to_message_id=message_id,
            )
        elif item["effect_type"].startswith("company_shield_"):
            send_message(
                chat_id,
                f"🛡️ {item['name']} فعال شد! تا {result['hours']} ساعت آینده شرکتت در برابر حمله‌ی بازیکن‌های دیگه محافظت‌شده‌ست.",
                reply_to_message_id=message_id,
            )
        elif item["effect_type"].startswith("lucky_contract_"):
            send_message(
                chat_id,
                f"🎲 {item['name']} ثبت شد! حدود {result['hours']} ساعت دیگه نتیجه‌ش رو برات پیوی می‌کنم.",
                reply_to_message_id=message_id,
            )
        else:
            send_message(chat_id, f"{item['name']} مصرف شد.", reply_to_message_id=message_id)
        return

    reason = result["reason"]
    if reason == "not_owned":
        send_message(chat_id, "از این آیتم چیزی تو کیفت نداری. برای دیدن کیفت بنویس «کیف من».", reply_to_message_id=message_id)
    elif reason == "not_found":
        send_message(chat_id, "چنین آیتمی وجود نداره.", reply_to_message_id=message_id)
    elif reason == "permanent_active":
        send_message(chat_id, "این یه ارتقای دائمیه، نیازی به «استفاده» نداره — از لحظه‌ی خرید همیشه فعاله! 💼", reply_to_message_id=message_id)
    else:
        send_message(chat_id, "یه مشکلی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)


def handle_cat_upgrade(chat_id, sender_id, message_id, sender_name):
    ok, result = db.upgrade_cat(sender_id)
    if ok:
        if result["rank_up"]:
            title = cat_title_for_rank(result["new_rank"])
            new_cap = db.cat_rank_cap(result["new_rank"])
            msg = (
                "🏢 تبریک!\n"
                "شرکتت آن‌قدر رشد کرد که به رتبه بالاتری رسید.\n\n"
                f"🏷️ رتبه جدید: {title}\n"
                f"🔄 سطح داخلی شرکت دوباره از {result['new_level']}/{new_cap} شروع شد.\n"
                "📈 اما درآمد و ظرفیت شرکت به شکل محسوسی افزایش پیدا کرد.\n\n"
                "امپراتوری PawKing هر روز بزرگ‌تر می‌شود... 👑\n\n"
                f"💸 هزینه: {format_number(result['cost'])} 🪙\n"
                f"💰 موجودی باقی‌مونده: {format_number(result['remaining_points'])} 🪙"
            )
        else:
            msg = (
                "🚀 شرکتت ارتقا پیدا کرد!\n\n"
                f"⬆️ سطح شرکت: {result['new_level'] - 1} ➜ {result['new_level']}\n"
                "📦 ظرفیت خزانه بیشتر شد. 💸 درآمد ساعتی هم افزایش پیدا کرد.\n\n"
                "مدیرعامل، کارت عالی بود! 👑\n\n"
                f"💸 هزینه: {format_number(result['cost'])} 🪙\n"
                f"💰 موجودی باقی‌مونده: {format_number(result['remaining_points'])} 🪙"
            )
        send_message(chat_id, msg, reply_to_message_id=message_id)
        return

    reason = result["reason"]
    if reason == "no_cat":
        send_message(chat_id, f"هنوز شرکتی نداری! اول بنویس «{COMPANY_BUY_COMMAND}» تا یکی تأسیس کنی 💼", reply_to_message_id=message_id)
    elif reason == "maxed":
        send_message(chat_id, "شرکتت به حداکثر رتبه و سطح شرکت ممکن رسیده! دیگه جای توسعه نداره 🏆", reply_to_message_id=message_id)
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"برای توسعه‌ی شرکت به {format_number(result['cost'])} 🪙 نیاز داری، ولی موجودیت کمتره.",
            reply_to_message_id=message_id,
        )
    elif reason == "level_too_low":
        send_message(
            chat_id,
            (
                "🚫 برای رسیدن به رتبه‌ی بعدی شرکت، فقط پول کافی نیست!\n\n"
                f"🎯 باید سطح حسابت حداقل {result['required_level']} باشه "
                f"(الان سطح {result['account_level']}ـه).\n"
                "🐾 برو یه‌کم میو بزن تا سطحت بره بالا، بعد دوباره امتحان کن."
            ),
            reply_to_message_id=message_id,
        )
    else:
        send_message(chat_id, "یه مشکلی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)


def handle_message(chat_id, sender_id, message_id, text, reply_to_message_id):
    """
    پوسته‌ی امن دور کل منطق پردازش پیام: اگه هر جای دستورات یه خطای
    غیرمنتظره بده (نه یه پیام نامعتبر عادی، بلکه باگ)، به‌جای سکوت کامل
    (که کاربر فکر کنه بات خرابه)، هم لاگ می‌کنیم هم به کاربر خبر می‌دیم.
    """
    try:
        _dispatch_message(chat_id, sender_id, message_id, text, reply_to_message_id)
    except Exception:
        print("خطای غیرمنتظره در پردازش دستور:")
        traceback.print_exc()
        try:
            send_message(
                chat_id,
                "⚠️ یه مشکل غیرمنتظره پیش اومد، دوباره امتحان کن. اگه شرطی وسط بود و از دست رفت، به ادمین خبر بده.",
                reply_to_message_id=message_id,
            )
        except Exception:
            pass


def _dispatch_message(chat_id, sender_id, message_id, text, reply_to_message_id):
    text = (text or "").strip()

    # هر پیام تو یه گروه، یعنی این کاربر عضو فعال اون گروهه (برای لیدربرد گروهی)،
    # و همینطور فرستنده‌ی این پیام رو ذخیره می‌کنیم تا اگه بعداً یکی روش ریپلای
    # زد (انتقال کوین، شارژ، پروفایلش، ...) بشه فرستنده‌ش رو پیدا کرد.
    # این کار تو یه اتصال دیتابیس (نه دوتا) انجام می‌شه تا سریع‌تر باشه.
    #
    # نکته‌ی حیاتی: اگه همین message_id قبلاً دیده شده باشه (یعنی روبیکا یا
    # شبکه دوباره همون وبهوک رو فرستاده)، این پیام کاملاً نادیده گرفته
    # می‌شه — چون در غیر این صورت هر دستور مالی (انتقال کوین، برد بازی،
    # میو) می‌تونست به‌خاطر تحویل تکراری، دوبار اجرا بشه.
    is_new_message = db.record_message_context(chat_id, sender_id, message_id)
    if message_id and not is_new_message:
        return

    # دستور تنظیم اسم: "تنظیم نام <اسم>" — این باید قبل از فیلتر عمومی
    # چک بشه، چون برای اسم نامناسب باید یه پیام رد صریح بدیم، نه سکوت.
    if text.startswith(SET_NAME_PREFIX):
        new_name = text[len(SET_NAME_PREFIX):].strip()
        if not new_name:
            send_message(
                chat_id,
                "بعد از «تنظیم نام» اسمتو بنویس، مثلاً:\nتنظیم نام علی",
                reply_to_message_id=message_id,
            )
        elif db.contains_blocked_word(new_name):
            send_message(
                chat_id,
                "این اسم قابل قبول نیست، یه اسم دیگه انتخاب کن.",
                reply_to_message_id=message_id,
            )
        elif db.contains_reserved_cosmetic_symbol(new_name):
            send_message(
                chat_id,
                "تو اسمت نمی‌تونی از ایموجی یا نمادهای فروشگاهی (تاج، 💎 و…) استفاده کنی — این‌ها فقط با خرید واقعی از فروشگاه فعال می‌شن.",
                reply_to_message_id=message_id,
            )
        else:
            db.set_username(sender_id, new_name)
            send_message(
                chat_id,
                f"✅ باشه! از این به بعد صدات می‌زنم: {new_name}",
                reply_to_message_id=message_id,
            )
        return

    # فیلتر نام‌های نامناسب: برای هر پیام دیگه‌ای (غیر از تنظیم نام که
    # بالاتر جدا مدیریت شد)، اگه پیام کلمه‌ی غیرمجاز داشت، بات کاملاً
    # سکوت می‌کنه (نه پردازش، نه پاسخ) تا خطر فیلتر شدن بات پیش نیاد.
    # ادمین‌ها از این فیلتر مستثنی‌ان چون ممکنه برای ریست/مدیریت این
    # کلمات رو تایپ کنن (مثلاً هدف‌گیری یه اکانت با اسم نامناسب).
    is_admin_sender = str(sender_id) in ADMIN_USER_IDS
    if not is_admin_sender and db.contains_blocked_word(text):
        return

    # دستور انتقال کوین: "انتقال کوین <عدد>" (باید ریپلای شده باشه)
    if text.startswith(TRANSFER_PREFIX):
        handle_transfer(chat_id, sender_id, message_id, text, reply_to_message_id)
        return

    # دستور شارژ: فقط برای ادمین‌ها، "شارژ <عدد>" روی ریپلای
    if text.startswith(CHARGE_PREFIX):
        handle_charge(chat_id, sender_id, message_id, text, reply_to_message_id)
        return

    # دستور فعال‌سازی رایگان آیتم فروشگاه: فقط ادمین‌ها، "فعال کن <کلیدواژه>" روی ریپلای
    if text.startswith(GRANT_ITEM_PREFIX):
        handle_admin_grant_item(chat_id, sender_id, message_id, text, reply_to_message_id)
        return

    if text == SHOP_CYCLE_FORCE_COMMAND or text.startswith(SHOP_CYCLE_FORCE_COMMAND + " "):
        handle_force_shop_cycle(chat_id, sender_id, message_id, text)
        return


    # دستور ریست کوین: فقط برای ادمین‌ها — یا روی ریپلای، یا با نوشتن
    # اسم اکانت بعدش (مثلاً: «ریست کوین علی»)
    if text == RESET_COMMAND:
        handle_reset(chat_id, sender_id, message_id, reply_to_message_id)
        return
    if text.startswith(RESET_COMMAND + " "):
        target_text = text[len(RESET_COMMAND):].strip()
        handle_reset(chat_id, sender_id, message_id, reply_to_message_id, target_text)
        return

    # دستور ریست نام: فقط برای ادمین‌ها — یا روی ریپلای، یا با نوشتن اسم اکانت بعدش
    if text == RESET_NAME_COMMAND:
        handle_reset_name(chat_id, sender_id, message_id, reply_to_message_id)
        return
    if text.startswith(RESET_NAME_COMMAND + " "):
        target_text = text[len(RESET_NAME_COMMAND):].strip()
        handle_reset_name(chat_id, sender_id, message_id, reply_to_message_id, target_text)
        return

    # دستور پاکسازی اسم‌های نامناسب: فقط برای ادمین‌ها، اسکن کل دیتابیس
    if text == CLEANUP_NAMES_COMMAND:
        handle_cleanup_names(chat_id, sender_id, message_id)
        return
    if text == CLEANUP_COSMETIC_NAMES_COMMAND:
        handle_cleanup_cosmetic_names(chat_id, sender_id, message_id)
        return

    # دستورات آماری/راهنمای ادمین
    if text == ADMIN_HELP_COMMAND:
        handle_admin_help(chat_id, sender_id, message_id)
        return
    if text == GROUP_COUNT_COMMAND:
        handle_group_count(chat_id, sender_id, message_id)
        return
    if text == PLAYER_COUNT_COMMAND:
        handle_player_count(chat_id, sender_id, message_id)
        return

    sender_name = db.get_username(sender_id)

    # مافیا فعلاً غیرفعاله (چون پیوی‌ها هنوز شکست می‌خورن و باعث کندی
    # کل بات می‌شدن). کد و جدول‌هاش دست‌نخورده موند؛ برای فعال کردن
    # دوباره، فقط همین بلوک رو از کامنت دربیار.
    # if text == MAFIA_START_COMMAND:
    #     handle_mafia_lobby_start(chat_id, sender_id, message_id, sender_name)
    #     return
    # if text == MAFIA_JOIN_COMMAND:
    #     handle_mafia_join(chat_id, sender_id, message_id, sender_name)
    #     return
    # if text == MAFIA_FORCE_START_COMMAND:
    #     handle_mafia_force_start(chat_id, sender_id, message_id)
    #     return
    # if text == MAFIA_CANCEL_COMMAND:
    #     handle_mafia_cancel(chat_id, sender_id, message_id)
    #     return

    if text == "میو":
        if not sender_name:
            send_message(
                chat_id,
                "هنوز اسمتو نمی‌دونم! اول بنویس:\nتنظیم نام <اسمت>\nبعد دوباره میو کن 🐾",
                reply_to_message_id=message_id,
            )
            return

        ok, result = db.do_meow(sender_id, sender_name)
        if ok:
            send_message(chat_id, build_meow_success_message(cosmetic_name_prefix(sender_id, sender_name), result), reply_to_message_id=message_id)
        else:
            if result["reason"] == "cooldown":
                send_message(
                    chat_id,
                    build_cooldown_message(sender_name, result["remaining"]),
                    reply_to_message_id=message_id,
                )

    elif text == "لیدربرد":
        rows = db.get_leaderboard_group(chat_id, order_by="points", limit=10)
        if not rows:
            send_message(chat_id, "هنوز کسی تو این گروه لیدربرد نداره! اول میو کن 🐾", reply_to_message_id=message_id)
            return
        viewer_rank = db.get_rank_group(chat_id, sender_id, order_by="points")
        viewer_points = db.get_points(sender_id)
        msg = build_leaderboard_message(rows, "این گروه", viewer_rank, viewer_points)
        send_message(chat_id, msg, reply_to_message_id=message_id)

    elif text == "لیدربرد کل":
        rows = db.get_leaderboard_global(order_by="points", limit=10)
        if not rows:
            send_message(chat_id, "هنوز کسی تو لیدربرد نیست! اول میو کن 🐾", reply_to_message_id=message_id)
            return
        viewer_rank = db.get_rank_global(sender_id, order_by="points")
        viewer_points = db.get_points(sender_id)
        msg = build_leaderboard_message(rows, "جهان", viewer_rank, viewer_points)
        send_message(chat_id, msg, reply_to_message_id=message_id)

    elif text == PROFILE_SELF_COMMAND:
        display_name = sender_name or "ناشناس"
        profile = db.get_profile(sender_id)
        if profile:
            msg = build_profile_message(display_name, sender_id, profile)
        else:
            msg = "هنوز هیچ میویی نکردی! بنویس 'میو' تا شروع کنی 🐾"
        send_message(chat_id, msg, reply_to_message_id=message_id)

    elif text == PROFILE_OTHER_COMMAND:
        if not reply_to_message_id:
            send_message(
                chat_id,
                "برای دیدن پروفایل یکی دیگه، باید روی پیامش ریپلای بزنی و بنویسی «پروفایلش».",
                reply_to_message_id=message_id,
            )
            return
        target_id = db.get_sender_of_message(reply_to_message_id)
        if not target_id:
            send_message(
                chat_id,
                "نتونستم بفهمم این پیام برای کیه (شاید خیلی قدیمیه).",
                reply_to_message_id=message_id,
            )
            return
        target_name = db.get_username(target_id) or "ناشناس"
        profile = db.get_profile(target_id)
        if profile:
            msg = build_profile_message(target_name, target_id, profile)
        else:
            msg = f"{target_name} هنوز هیچ میویی نکرده!"
        send_message(chat_id, msg, reply_to_message_id=message_id)

    elif text == "آیدی من":
        send_message(chat_id, f"🪪 آیدی شما: {sender_id}", reply_to_message_id=message_id)

    elif text in HELP_COMMANDS:
        send_message(chat_id, build_help_message(), reply_to_message_id=message_id)

    elif text == CASINO_MENU_COMMAND:
        send_message(chat_id, build_casino_menu_message(), reply_to_message_id=message_id)

    elif text.startswith(COINFLIP_PREFIX):
        handle_coinflip(chat_id, sender_id, message_id, text)

    elif text.startswith(DICE_PREFIX):
        handle_dice(chat_id, sender_id, message_id, text)

    elif text.startswith(HIGH_WORD) and re.search(r"\d", text):
        handle_highlow(chat_id, sender_id, message_id, text, HIGH_WORD)

    elif text.startswith(LOW_WORD) and re.search(r"\d", text):
        handle_highlow(chat_id, sender_id, message_id, text, LOW_WORD)

    elif text.startswith(SLOT_PREFIX):
        handle_slot(chat_id, sender_id, message_id, text)

    elif text.startswith(ROULETTE_PREFIX):
        handle_roulette(chat_id, sender_id, message_id, text)

    elif text.startswith(BLACKJACK_PREFIX):
        handle_blackjack_start(chat_id, sender_id, message_id, text)

    elif text == HIT_WORD:
        handle_blackjack_hit(chat_id, sender_id, message_id)

    elif text == STAND_WORD:
        handle_blackjack_stand(chat_id, sender_id, message_id)

    elif text.startswith(DART_PREFIX):
        handle_dart(chat_id, sender_id, message_id, text)

    elif text.startswith(MINES_OPEN_PREFIX):
        handle_mines_open(chat_id, sender_id, message_id, text)

    elif text.startswith(MINES_PREFIX):
        handle_mines_start(chat_id, sender_id, message_id, text)

    elif text == CASHOUT_WORD:
        handle_cashout(chat_id, sender_id, message_id)

    elif text.startswith(COMPANY_NAME_PREFIX):
        handle_cat_set_name(chat_id, sender_id, message_id, text)

    elif text.startswith(DUEL_REQUEST_PREFIX):
        handle_duel_request(chat_id, sender_id, message_id, text, reply_to_message_id)

    elif text == DUEL_ACCEPT_WORD:
        handle_duel_accept(chat_id, sender_id, message_id)

    elif text == DUEL_DECLINE_WORD:
        handle_duel_decline(chat_id, sender_id, message_id)

    elif text.startswith(DUEL_MOVE_PREFIX):
        handle_tictactoe_move(chat_id, sender_id, message_id, text)

    elif text in (LEFT_WORD, RIGHT_WORD):
        handle_hand_word(chat_id, sender_id, message_id, text)

    elif text in COMPANY_STATUS_WORDS:
        handle_cat_status(chat_id, sender_id, message_id, sender_name)

    elif text in COMPANY_BUY_WORDS:
        handle_cat_buy(chat_id, sender_id, message_id, sender_name)

    elif text in COMPANY_UPGRADE_WORDS:
        handle_cat_upgrade(chat_id, sender_id, message_id, sender_name)

    elif text == COMPANY_COLLECT_COMMAND:
        handle_cat_collect(chat_id, sender_id, message_id, sender_name)

    elif text == SHOP_COMMAND:
        handle_shop(chat_id, message_id)

    elif text.startswith(BUY_ITEM_PREFIX + " "):
        handle_buy_item(chat_id, sender_id, message_id, text)

    elif text == INVENTORY_COMMAND:
        handle_inventory(chat_id, sender_id, message_id)

    elif text.startswith(USE_ITEM_PREFIX + " "):
        handle_use_item(chat_id, sender_id, message_id, text, reply_to_message_id)

    elif text.startswith(CODEBREAK_GUESS_PREFIX + " "):
        handle_codebreak_guess(chat_id, sender_id, message_id, text)

    elif text == EQUIP_TITLE_PREFIX or text.startswith(EQUIP_TITLE_PREFIX + " "):
        handle_equip_title(chat_id, sender_id, message_id, text)

    elif text == EQUIP_THEME_PREFIX or text.startswith(EQUIP_THEME_PREFIX + " "):
        handle_equip_theme(chat_id, sender_id, message_id, text)

    elif text.startswith(GIFT_CODE_PREFIX + " "):
        handle_redeem_gift_code(chat_id, sender_id, message_id, text, sender_name)

    elif text == DAILY_GIFT_CODE_ADMIN_COMMAND:
        handle_daily_gift_code_admin_command(chat_id, sender_id, message_id)

    elif text == AD_DELETE_COMMAND:
        handle_ad_delete(chat_id, sender_id, message_id)

    elif text.startswith(AD_BROADCAST_PREFIX + " "):
        handle_ad_broadcast(chat_id, sender_id, message_id, text)


# ---------------------------------------------------------------------------
# محدودیت نرخ ارسال (برای اسپم/فلود) — فقط تو حافظه، بدون رفت‌وبرگشت دیتابیس
# ---------------------------------------------------------------------------

RATE_LIMIT_WINDOW_SECONDS = 5
RATE_LIMIT_MAX_MESSAGES = 3

_recent_message_times = {}


def _is_rate_limited(sender_id):
    now = time.time()
    times = _recent_message_times.setdefault(sender_id, [])
    while times and now - times[0] > RATE_LIMIT_WINDOW_SECONDS:
        times.pop(0)
    times.append(now)
    return len(times) > RATE_LIMIT_MAX_MESSAGES


def _cleanup_rate_limit_memory():
    """
    _recent_message_times قبلاً هیچ‌وقت هیچ کلیدی رو حذف نمی‌کرد — یعنی
    هر کاربر یکتایی که تا حالا حتی یه پیام فرستاده، برای همیشه تو
    حافظه‌ی پردازش می‌موند، حتی اگه ماه‌ها غیرفعال باشه. با رشد تعداد
    کاربرا، این آرام‌آرام حافظه رو اشغال می‌کرد. حالا هر ساعت (کنار
    بقیه‌ی پاکسازی‌های دوره‌ای) ورودی‌های خالی رو پاک می‌کنیم.
    """
    now = time.time()
    stale_ids = [
        sender_id
        for sender_id, times in _recent_message_times.items()
        if not times or now - times[-1] > RATE_LIMIT_WINDOW_SECONDS
    ]
    for sender_id in stale_ids:
        _recent_message_times.pop(sender_id, None)


def process_update(update):
    message = update.get("new_message") or update.get("message") or {}

    chat_id = update.get("chat_id") or message.get("chat_id")
    raw_sender_id = message.get("sender_id")
    sender_id = raw_sender_id or chat_id
    message_id = message.get("message_id")
    text = message.get("text")
    reply_to_message_id = message.get("reply_to_message_id")

    if chat_id and text:
        if _is_rate_limited(sender_id):
            # فلود شناسایی شد؛ این پیام رو کاملاً نادیده می‌گیریم (نه ذخیره،
            # نه پردازش) تا صف بقیه‌ی کاربرها رو کند نکنه.
            return

        # محافظت در برابر ارسال دوباره‌ی همون پیام از طرف روبیکا (که تو
        # وبهوک واقعاً پیش میاد، مثلاً موقع تلاش مجدد شبکه‌ای). بدون این
        # چک، اگه یه پیام دوبار برسه، دوبار هم پردازش می‌شد — یعنی
        # احتمال کم شدن دوبرابری کوین یا دوبار انجام شدن یه اکشن بازی.
        #
        # این‌جا حتماً باید try/except داشته باشه: قبلاً نداشت، و اگه این
        # تابع به هر دلیلی خطا می‌داد (مثلاً یه لحظه دیرتر از موعد جدول
        # ساخته شده بود)، کل پیام بی‌صدا پردازش نمی‌شد — نه خطایی که
        # بشه راحت پیدا کرد، نه جوابی به کاربر. فقط وقتی مطمئنیم پیام
        # واقعاً تکراریه (خودِ تابع صریحاً False برگردونه) رد می‌شیم؛
        # هر خطای دیگه‌ای رو محافظه‌کارانه نادیده می‌گیریم و پردازش رو
        # ادامه می‌دیم (بهتره یه پیام به‌ندرت دوبار پردازش بشه تا اینکه
        # کلاً گم بشه).
        try:
            is_new = db.record_seen_message(message_id, chat_id, sender_id)
        except Exception:
            print(f"خطا در ثبت پیام دیده‌شده (نادیده گرفته شد، پردازش ادامه پیدا می‌کنه):")
            traceback.print_exc()
            is_new = True
        if not is_new:
            print(f"پیام تکراری نادیده گرفته شد: {message_id}")
            return

        # اگه sender_id تو خودِ پیام صراحتاً نیومده بود (یعنی از fallback
        # به chat_id رسیدیم)، این پیام از پیوی مستقیم بین این کاربر و
        # بات اومده، نه یه گروه — چون تو گروه همیشه sender_id جداگانه
        # می‌رسه. این‌جا دقیقاً همون chat_id معتبریه که برای پیوی این
        # کاربر لازم داریم؛ ذخیره‌ش می‌کنیم تا بعداً درست بتونیم پیوی‌ش کنیم.
        if not raw_sender_id:
            try:
                db.set_pv_chat_id(sender_id, chat_id)
            except Exception:
                print(f"خطا در ذخیره‌ی chat_id پیویِ {sender_id}:")
                traceback.print_exc()
        print(f"پیام از {sender_id}: {text}")
        handle_message(chat_id, sender_id, message_id, text, reply_to_message_id)


# ---------------------------------------------------------------------------
# پیام دوره‌ای به همه‌ی گروه‌ها
# ---------------------------------------------------------------------------

BROADCAST_INTERVAL_SECONDS = 10 * 3600  # هر ۱۰ ساعت

BROADCAST_TEXT = (
    "🐈 پیشی‌ها! یه سر به امپراتوریتون بزنید...\n\n"
    "✨ میو یادتون نره؛ هر بار می‌تونه کوین بیشتری به جیبتون اضافه کنه.\n"
    "🏢 اگه شرکت داری، حتماً برداشت سود شرکت رو انجام بده؛ شاید خزانه‌ت پر شده باشه!\n"
    "🎰 حوصله‌ت سر رفته؟ سری به کازینو بزن و شانس خودتو امتحان کن.\n"
    "🎮 اگه دنبال رقابتی، بازی‌های دوز و گل یا پوچ منتظرن تا رفیقاتو به چالش بکشی.\n"
    "📚 دستوری یادت رفته و نمیدونی آپدیت جدید چی باید بگی؟ فقط بنویس راهنما. یا آموزشگاه\n\n"
    "چنل اطلاع‌رسانی رسمی ما: @pawking_official\n\n"
    "👑 پاوکینگ هیچ‌وقت نمی‌خوابه... فقط منتظر حرکت بعدی توئه. 🐾"
)


def broadcast_to_all_groups():
    chat_ids = db.get_all_group_chat_ids()
    for chat_id in chat_ids:
        try:
            send_message(chat_id, BROADCAST_TEXT)
        except Exception:
            print(f"خطا در ارسال پیام دوره‌ای به {chat_id}:")
            traceback.print_exc()


def _ensure_all_tables():
    db.ensure_offset_table()
    db.ensure_extra_columns()
    db.ensure_group_members_table()
    db.ensure_seen_messages_table()
    db.ensure_admin_actions_table()
    db.ensure_cats_table()
    db.ensure_active_games_table()
    db.ensure_duel_tables()
    db.ensure_shop_tables()
    db.ensure_shop_effects_table()
    db.ensure_black_market_tables()
    db.ensure_company_shield_table()
    db.ensure_cosmetics_columns()
    db.ensure_gift_code_tables()
    db.ensure_ad_broadcast_table()
    db.ensure_mafia_tables()
    db.ensure_broadcast_column()
    # ایندکس‌ها فعلاً غیرفعالن: CREATE INDEX معمولی موقع ساختن یه قفل
    # می‌گیره که جلوی نوشتن رو جدول رو می‌گیره — دقیقاً همون چیزی بود که
    # باعث می‌شد بات یه پیام رو بی‌صدا معطل نگه‌داره (نه خطا، نه جواب).
    # برای فعال‌سازی امن، باید با CREATE INDEX CONCURRENTLY (که قفل
    # نمی‌گیره) و با یه اتصال autocommit جداگانه انجام بشه، نه اینجا.
    # db.ensure_performance_indexes()


def handle_daily_gift_code_admin_command(chat_id, sender_id, message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    existing = db.get_todays_gift_code()
    if existing:
        code = existing["code"]
        remaining = existing["max_uses"] - existing["uses_count"]
        if remaining <= 0:
            send_message(
                chat_id,
                f"کد امروز («{code}») از قبل ساخته شده ولی ظرفیتش تموم شده. کد جدید فردا به‌صورت خودکار ساخته می‌شه.",
                reply_to_message_id=message_id,
            )
            return
        send_message(
            chat_id,
            (
                f"🎁 کد امروز: «{code}»\n"
                f"💰 هر نفر با فرستادن «{GIFT_CODE_PREFIX} {code}» تو گروه، {format_number(db.GIFT_CODE_COIN_VALUE)} 🪙 می‌گیره.\n"
                f"👥 {remaining} ظرفیت باقی‌مونده."
            ),
            reply_to_message_id=message_id,
        )
        return

    code = db.generate_daily_gift_code_if_needed()
    if not code:
        send_message(chat_id, "یه مشکل موقتی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)
        return
    send_message(
        chat_id,
        (
            f"🎁 کد جدید ساخته شد: «{code}»\n"
            f"💰 هر نفر با فرستادن «{GIFT_CODE_PREFIX} {code}» تو گروه، {format_number(db.GIFT_CODE_COIN_VALUE)} 🪙 می‌گیره.\n"
            f"👥 فقط {db.GIFT_CODE_MAX_USES} نفر اول می‌تونن استفاده‌ش کنن."
        ),
        reply_to_message_id=message_id,
    )


def _run_maintenance_tick(last_cleanup, last_broadcast_at):
    # هر تکرار (هر ~۱ ثانیه) چک می‌کنیم ببینیم مهلت بازی‌های چندمرحله‌ای
    # (بلک‌جک/مین‌یاب) کسی تموم شده یا نه.
    try:
        resolve_expired_games()
    except Exception:
        print("خطا در رسیدگی به بازی‌های منقضی‌شده:")
        traceback.print_exc()

    try:
        resolve_expired_duels()
    except Exception:
        print("خطا در رسیدگی به دوئل‌های منقضی‌شده:")
        traceback.print_exc()

    # قراردادهای شانسی‌ای که زمانشون رسیده رو تسویه و به صاحبشون پیوی می‌کنیم
    try:
        for result in db.deliver_ready_lucky_contracts():
            outcome = "🎉 برنده شدی" if result["multiplier"] >= 1 else "😿 این‌بار ضررش کردی"
            # همون باگی که تو مافیا و کد پاداش داشتیم: اینجا هم قبلاً
            # مستقیم result["user_id"] به‌عنوان chat_id پیوی داده می‌شد،
            # نه chat_id واقعیِ ذخیره‌شده از یه پیامِ پیوی واقعی.
            pv_chat_id = db.get_pv_chat_id(result["user_id"])
            if not pv_chat_id:
                print(f"کاربر {result['user_id']} پیوی بات رو نزده، نتیجه‌ی قرارداد شانسی پیویش نمی‌شه.")
                continue
            send_message(
                pv_chat_id,
                (
                    f"🎲 نتیجه‌ی قرارداد شانسی‌ت مشخص شد!\n\n"
                    f"ضریب: ×{result['multiplier']}\n"
                    f"{outcome}\n"
                    f"🪙 دریافتی: {format_number(result['payout'])} کوین"
                ),
            )
    except Exception:
        print("خطا در تسویه‌ی قراردادهای شانسی:")
        traceback.print_exc()

    # چرخش تصادفی چرخه‌ی فروشگاه (اگه زمانش رسیده باشه)
    try:
        db.rotate_shop_cycle_if_needed()
    except Exception:
        print("خطا در چرخش فروشگاه:")
        traceback.print_exc()

    # لابی‌های مافیایی که به حدنصاب نرسیدن و بازی‌هایی که خیلی طولانی
    # شدن رو خودکار می‌بندیم (فعلاً غیرفعال، چون خودِ مافیا موقتاً
    # خاموشه — وقتی دوباره فعالش کردیم، این بلوک رو هم برگردون).
    # try:
    #     for lobby in db.cancel_stale_mafia_lobbies():
    #         send_message(
    #             lobby["chat_id"],
    #             f"⌛️ لابی مافیا چون تو {db.MAFIA_LOBBY_TIMEOUT_SECONDS // 60} دقیقه به حدنصاب نرسید، خودکار لغو شد. دوباره با «{MAFIA_START_COMMAND}» شروع کنید.",
    #         )
    # except Exception:
    #     print("خطا در لغو لابی‌های مافیای منقضی‌شده:")
    #     traceback.print_exc()

    # try:
    #     for game in db.force_end_stuck_mafia_games():
    #         send_message(
    #             game["chat_id"],
    #             f"⌛️ این بازی مافیا بیشتر از {db.MAFIA_GAME_MAX_SECONDS // 60} دقیقه طول کشید و خودکار بسته شد.",
    #         )
    # except Exception:
    #     print("خطا در بستن بازی‌های مافیای طولانی:")
    #     traceback.print_exc()

    # هر چند وقت یه‌بار پیام‌های دیده‌شده‌ی خیلی قدیمی و قراردادهای شانسیِ
    # تسویه‌شده‌ی قدیمی رو پاک می‌کنیم که جدول‌ها بی‌نهایت بزرگ نشن
    if time.time() - last_cleanup > 3600:
        try:
            db.cleanup_old_seen_messages()
        except Exception:
            traceback.print_exc()
        try:
            db.cleanup_old_lucky_contracts()
        except Exception:
            traceback.print_exc()
        try:
            _cleanup_rate_limit_memory()
        except Exception:
            traceback.print_exc()
        last_cleanup = time.time()

    # هر روز از ساعت ۱۲ ظهر (به وقت ایران) به بعد، یه کد هدیه‌ی تازه
    # می‌سازیم (فقط تو دیتابیس، بدون پیوی زدن به کسی — دیگه پیوی مستقیم
    # نمی‌زنیم چون قبلاً مشکل ایجاد می‌کرد). ادمین هروقت خواست، با گفتن
    # «پاداش روزانه» تو همون گروه، همین کد رو می‌گیره.
    try:
        if db.iran_now().hour >= 12:
            db.generate_daily_gift_code_if_needed()
    except Exception:
        print("خطا در ساخت کد هدیه‌ی روزانه:")
        traceback.print_exc()

    # هر ۱۰ ساعت یه‌بار به همه‌ی گروه‌های شناخته‌شده پیام یادآوری می‌فرستیم
    if (datetime.datetime.utcnow() - last_broadcast_at).total_seconds() > BROADCAST_INTERVAL_SECONDS:
        try:
            broadcast_to_all_groups()
            db.set_last_broadcast_time()
            last_broadcast_at = datetime.datetime.utcnow()
        except Exception:
            print("خطا در ارسال پیام دوره‌ای:")
            traceback.print_exc()

    return last_cleanup, last_broadcast_at


def maintenance_loop():
    """
    وقتی وبهوک فعاله، دیگه نیازی به پولینگ (getUpdates) نیست، چون خودِ
    روبیکا پیام‌ها رو مستقیم پوش می‌کنه. ولی همچنان باید هر چند ثانیه چک
    کنیم بازی‌های منقضی‌شده و پیام دوره‌ای رو رسیدگی کنیم.
    """
    _ensure_all_tables()
    print("بات PawKing تو حالت وبهوک شروع به کار کرد.")

    last_cleanup = time.time()
    last_broadcast_at = db.get_last_broadcast_time()
    if last_broadcast_at is None:
        db.set_last_broadcast_time()
        last_broadcast_at = datetime.datetime.utcnow()

    while True:
        try:
            last_cleanup, last_broadcast_at = _run_maintenance_tick(last_cleanup, last_broadcast_at)
        except Exception:
            print("خطای غیرمنتظره تو حلقه‌ی نگهداری:")
            traceback.print_exc()
        time.sleep(1)


def start_maintenance_loop_forever():
    while True:
        try:
            maintenance_loop()
        except Exception:
            print("maintenance_loop به‌طور کامل متوقف شد، در حال راه‌اندازی مجدد:")
            traceback.print_exc()
            time.sleep(5)


def bot_loop():
    _ensure_all_tables()

    offset_id = db.get_offset()
    print("بات PawKing شروع به کار کرد (حالت پولینگ)... offset ذخیره‌شده:", offset_id)

    last_cleanup = time.time()

    # زمان آخرین پیام دوره‌ای رو از دیتابیس می‌خونیم (نه از حافظه) تا با
    # ری‌استارت سرویس (دیپلوی جدید، خواب رفتن Render) گم نشه. اگه هنوز
    # هیچ‌وقت پیامی نرفته، از همین لحظه شمارش رو شروع می‌کنیم.
    last_broadcast_at = db.get_last_broadcast_time()
    if last_broadcast_at is None:
        db.set_last_broadcast_time()
        last_broadcast_at = datetime.datetime.utcnow()

    while True:
        # هر تکرار حلقه توی try/except جداگونه‌ست تا یه خطای غیرمنتظره
        # کل ترد رو نکشه و باعث "خاموش شدن" بی‌صدای ربات نشه.
        try:
            result = get_updates(offset_id)

            if result:
                data = result.get("data", {})
                updates = data.get("updates", [])

                if not updates and "data" not in result:
                    # این یعنی خود API یه چیز غیرمنتظره برگردونده (نه یه پاسخ عادیِ بدون آپدیت)
                    print("پاسخ غیرمنتظره از getUpdates:", result)

                for update in updates:
                    # هر آپدیت جدا پردازش و ذخیره می‌شه؛ اگه یکیشون خطا داد
                    # بقیه رو از دست نمی‌دیم و offset درست جلو می‌ره.
                    try:
                        process_update(update)
                    except Exception:
                        print("خطا در پردازش یه پیام:")
                        traceback.print_exc()

                new_offset_id = data.get("next_offset_id", offset_id)
                if new_offset_id != offset_id:
                    offset_id = new_offset_id
                    db.set_offset(offset_id)

            last_cleanup, last_broadcast_at = _run_maintenance_tick(last_cleanup, last_broadcast_at)

        except Exception:
            print("خطای غیرمنتظره تو حلقه‌ی اصلی بات:")
            traceback.print_exc()

        time.sleep(1)


def start_bot_loop_forever():
    """
    اگه bot_loop به هر دلیلی (باگ ناشناخته، کرش شدید) کامل متوقف بشه،
    این تابع دوباره راه‌اندازیش می‌کنه به‌جای اینکه ربات برای همیشه خاموش بمونه.
    """
    while True:
        try:
            bot_loop()
        except Exception:
            print("bot_loop به‌طور کامل متوقف شد، در حال راه‌اندازی مجدد:")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    if USE_WEBHOOK:
        register_webhook()
        threading.Thread(target=start_maintenance_loop_forever, daemon=True).start()
    else:
        threading.Thread(target=start_bot_loop_forever, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
