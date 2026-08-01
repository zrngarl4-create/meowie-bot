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
HELP_COMMANDS = ("راهنما", "راهنما پاوکینگ", "آموزشگاه")

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
COMPANY_NAME_PREFIX = "تنظیم شرکت"
COMPANY_UPGRADE_WORDS = ("توسعه شرکت", "ارتقا")
COMPANY_COLLECT_COMMAND = "برداشت سود شرکت"

PROFILE_SELF_COMMAND = "پروفایلم"
PROFILE_OTHER_COMMAND = "پروفایلش"

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
        resp = requests.post(f"{BASE_URL}/setWebHook", json={"url": webhook_url}, timeout=15)
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


def send_message(chat_id, text, reply_to_message_id=None):
    # روبیکا سینتکس مارک‌داونِ ** رو تو متن رندر نمی‌کنه؛ فرمت واقعی‌ای که
    # قبول می‌کنه یه فیلد جدا به اسم metadata با بازه‌های بولده.
    # چون کاربر می‌خواد کل پیام برجسته باشه، کل طول متن رو یه بازه‌ی Bold می‌گیریم.
    metadata = None
    if text:
        metadata = {
            "meta_data_parts": [
                {"from_index": 0, "length": _utf16_length(text), "type": "Bold"}
            ]
        }

    payload = {"chat_id": chat_id, "text": text}
    if metadata:
        payload["metadata"] = metadata
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    try:
        resp = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
        if resp.status_code != 200:
            print("خطای HTTP در ارسال پیام (تلاش با متادیتای بولد):", resp.status_code, resp.text)
            raise RuntimeError("send with bold metadata failed, will retry plain")
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("status") not in (None, "OK", "ok", "done", "Done"):
            print("خطای منطقی سرور در ارسال پیام (تلاش با متادیتای بولد):", data)
            raise RuntimeError("send with bold metadata returned error status, will retry plain")
        return
    except Exception as e:
        print("پیام با متادیتای بولد ارسال نشد، تلاش دوباره بدون فرمت:", e)

    # اگه ارسال با متادیتا هر دلیلی شکست خورد، ساده (بدون بولد) دوباره امتحان می‌کنیم
    # تا حداقل خود پیام حتماً به کاربر برسه.
    fallback_payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        fallback_payload["reply_to_message_id"] = reply_to_message_id
    try:
        resp = requests.post(f"{BASE_URL}/sendMessage", json=fallback_payload, timeout=15)
        if resp.status_code != 200:
            print("خطای HTTP در ارسال پیام (تلاش ساده):", resp.status_code, resp.text)
    except Exception as e:
        print("خطا در ارسال پیام (تلاش ساده هم شکست خورد):", e)


# ---------------------------------------------------------------------------
# قالب‌های پیام
# ---------------------------------------------------------------------------

def build_meow_success_message(display_name, result):
    return (
        "🌙 صدای میوت توی شهر پیچید...\n\n"
        f"🪙 +{format_number(result['points_earned'])} {CURRENCY_NAME}\n"
        f"💰 موجودی: {format_number(result['total_points'])} 🪙\n"
        f"⏳ {format_cooldown(result['cooldown_seconds'])} تا میوی بعدی"
        + (f"\n\n🎉 تبریک {display_name}! سطح گربه‌ت رفت رو {result['level']} ⭐️" if result["leveled_up"] else "")
    )


def build_cooldown_message(display_name, remaining):
    return f"⌛️ گربه {display_name} هنوز خسته‌ست، {format_cooldown(remaining)} دیگه صبر کن."


def build_leaderboard_message(rows, scope_label, viewer_rank=None, viewer_points=None):
    lines = [
        "🏆 ══════【 لیدربرد 】══════ 🏆",
        "",
        f"👑 دسته: ثروتمندترین گربه‌های {scope_label} 🪙",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for i, row in enumerate(rows):
        rank = i + 1
        lines.append(f"{rank_emoji(rank)} {row['username']}")
        lines.append(f"└ 💰 {format_number(row['points'])} 🪙")
        if rank <= 3:
            lines.append(f"└ ⭐️ سطح {row['level']}")
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
        f"🐈 گربه {sender_name}\n"
        f"└─ 💸 {format_number(amount)} 🪙\n"
        "        ⬇️\n"
        f"🐈 گربه {receiver_name}\n\n"
        "✅ انتقال با موفقیت انجام شد.\n\n"
        "💰 موجودی جدید:\n"
        f"{format_number(receiver_new_points)} 🪙"
    )


def build_profile_message(display_name, user_id, profile):
    points_rank = db.get_rank_global(user_id, order_by="points")
    meows_rank = db.get_rank_global(user_id, order_by="total_meows")
    total_meows = profile.get("total_meows") or 0
    level = profile["level"]

    if level >= db.MAX_LEVEL:
        level_line = f"╯─ ⭐️ سطح : {level} (حداکثر سطح!) {progress_bar(1, 1)}"
    else:
        needed = db.exp_needed_for_next_level(level)
        level_line = f"╯─ ⭐️ سطح : {level} | {profile['exp']} / {needed} {progress_bar(profile['exp'], needed)}"

    lines = [
        "╮──「 🐱 پروفایل PawKing 🐱 」",
        "",
        f"┐─ 👤 کاربر : {display_name}",
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
        f"└ تأسیس اولین شرکت ({format_number(db.CAT_PRICE)} 🪙، نیاز به سطح {db.CAT_MIN_LEVEL}+)\n\n"
        "تنظیم شرکت <اسم>\n"
        "└ اسم گذاشتن رو شرکتت\n\n"
        "توسعه شرکت (یا فقط: ارتقا)\n"
        "└ بالا بردن سطح و رتبه‌ی شرکت\n\n"
        f"{COMPANY_COLLECT_COMMAND}\n"
        "└ خالی کردن خزانه‌ی شرکت\n\n"
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
    else:
        msg = build_blackjack_result_message(
            result["status"], result["bet"], result["player"], result["dealer"],
            result["winnings"], result["new_points"],
        )
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
    send_message(chat_id, msg, reply_to_message_id=message_id)


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
        # یادآوری خصوصی به قایم‌کننده — چون انتخابش نباید تو گروه دیده بشه
        send_message(
            challenger_id,
            "🌸 نوبت توئه که گل رو قایم کنی! بنویس «چپ» یا «راست» (این پیام خصوصیه، طرف مقابل نمی‌بینتش)",
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
        send_message(hider_id, "🌸 نوبت دور بعدیه! دوباره بنویس «چپ» یا «راست» (خصوصیه)")

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
            info = db.expire_duel_game(game)
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
            "برای انتقال کوین باید روی پیام همون گربه‌ای که می‌خوای بهش بدی ریپلای بزنی و بنویسی:\nانتقال کوین <عدد>",
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
            sender_name, receiver_name, amount, result["receiver_new_points"]
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


def handle_charge(chat_id, sender_id, message_id, text, reply_to_message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    if not reply_to_message_id:
        send_message(
            chat_id,
            "باید روی پیام همون کاربر ریپلای بزنی و بنویسی:\nشارژ <عدد>",
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

    amount = parse_amount_after_prefix(text, CHARGE_PREFIX)
    if amount is None or amount <= 0:
        send_message(
            chat_id,
            "بعد از «شارژ» یه مقدار معتبر بنویس، مثلاً:\nشارژ 50000",
            reply_to_message_id=message_id,
        )
        return

    target_name = db.get_username(target_id) or "ناشناس"
    db.get_or_create_user(target_id, target_name if target_name != "ناشناس" else None)

    new_points = db.add_points(target_id, amount)
    db.record_admin_action(sender_id, target_id, amount, "charge")

    send_message(
        chat_id,
        (
            "✅ شارژ با موفقیت انجام شد.\n\n"
            f"🐈 گربه {target_name}\n"
            f"💰 +{format_number(amount)} 🪙\n"
            f"💰 موجودی جدید: {format_number(new_points)} 🪙"
        ),
        reply_to_message_id=message_id,
    )


def handle_reset(chat_id, sender_id, message_id, reply_to_message_id):
    if str(sender_id) not in ADMIN_USER_IDS:
        send_message(chat_id, "⛔️ این قابلیت فقط برای مدیرهاست.", reply_to_message_id=message_id)
        return

    if not reply_to_message_id:
        send_message(
            chat_id,
            f"باید روی پیام همون کاربر ریپلای بزنی و بنویسی:\n{RESET_COMMAND}",
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


# ---------------------------------------------------------------------------
# سیستم شرکت
# ---------------------------------------------------------------------------

def cat_title_for_rank(rank):
    return CAT_TITLES.get(rank, f"شرکت رتبه {rank}")


def build_cat_weak_message():
    return (
        "🚧 هنوز برای ثبت شرکت آماده نیستی!\n\n"
        "اداره ثبت شرکت‌های PawKing درخواستت رو بخاطر نوب بودن و اینکه هنوز نوچه‌ای رد کرد. 😹\n\n"
        f"⭐ حداقل سطح موردنیاز: {db.CAT_MIN_LEVEL}\n"
        "فعلاً میو کن، امتیاز جمع کن و دوباره برگرد."
    )


def build_cat_shelter_message():
    return (
        "🏢 وقتشه اولین شرکتت رو تأسیس کنی!\n\n"
        "از این به بعد دیگه فقط با میو کردن پول درنمیاری... شرکتت هم شبانه‌روز برات سود جمع می‌کنه. 💰\n\n"
        f"📦 هزینه تأسیس: {format_number(db.CAT_PRICE)} 🪙  ⭐ حداقل سطح: {db.CAT_MIN_LEVEL}\n\n"
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
    per_hour = db.cat_production_per_hour(rank, level)
    per_second = round(per_hour / 3600)
    capacity = db.cat_capacity(rank, level)

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
        f"⭐️ سطح : {level} / {rank_cap}",
        "",
        f"💰 سود جمع‌شده در خزانه : {format_number(pending)} 🪙",
        f"💫 تولید کوین در ثانیه : {format_number(per_second)} 🪙",
        f"📦 ظرفیت خزانه : {format_number(capacity)}",
        "",
    ]
    if db.cat_is_maxed(rank, level):
        lines.append("🏆 این شرکت به حداکثر رتبه و سطح ممکن رسیده!")
    else:
        cost = db.cat_upgrade_cost(rank, level)
        lines.append(f"💰 هزینه توسعه : {format_number(cost)} 🪙")

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
        send_message(chat_id, "شرکتت به حداکثر رتبه و سطح ممکن رسیده! دیگه جای توسعه نداره 🏆", reply_to_message_id=message_id)
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"برای توسعه‌ی شرکت به {format_number(result['cost'])} 🪙 نیاز داری، ولی موجودیت کمتره.",
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
    db.record_message_context(chat_id, sender_id, message_id)

    # دستور تنظیم اسم: "تنظیم نام <اسم>"
    if text.startswith(SET_NAME_PREFIX):
        new_name = text[len(SET_NAME_PREFIX):].strip()
        if new_name:
            db.set_username(sender_id, new_name)
            send_message(
                chat_id,
                f"✅ باشه! از این به بعد صدات می‌زنم: {new_name}",
                reply_to_message_id=message_id,
            )
        else:
            send_message(
                chat_id,
                "بعد از «تنظیم نام» اسمتو بنویس، مثلاً:\nتنظیم نام علی",
                reply_to_message_id=message_id,
            )
        return

    # دستور انتقال کوین: "انتقال کوین <عدد>" (باید ریپلای شده باشه)
    if text.startswith(TRANSFER_PREFIX):
        handle_transfer(chat_id, sender_id, message_id, text, reply_to_message_id)
        return

    # دستور شارژ: فقط برای ادمین‌ها، "شارژ <عدد>" روی ریپلای
    if text.startswith(CHARGE_PREFIX):
        handle_charge(chat_id, sender_id, message_id, text, reply_to_message_id)
        return

    # دستور ریست کوین: فقط برای ادمین‌ها، روی ریپلای
    if text == RESET_COMMAND:
        handle_reset(chat_id, sender_id, message_id, reply_to_message_id)
        return

    sender_name = db.get_username(sender_id)

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
            send_message(chat_id, build_meow_success_message(sender_name, result), reply_to_message_id=message_id)
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

    elif text == COMPANY_BUY_COMMAND:
        handle_cat_buy(chat_id, sender_id, message_id, sender_name)

    elif text in COMPANY_UPGRADE_WORDS:
        handle_cat_upgrade(chat_id, sender_id, message_id, sender_name)

    elif text == COMPANY_COLLECT_COMMAND:
        handle_cat_collect(chat_id, sender_id, message_id, sender_name)


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


def process_update(update):
    message = update.get("new_message") or update.get("message") or {}

    chat_id = update.get("chat_id") or message.get("chat_id")
    sender_id = message.get("sender_id") or chat_id
    message_id = message.get("message_id")
    text = message.get("text")
    reply_to_message_id = message.get("reply_to_message_id")

    if chat_id and text:
        if _is_rate_limited(sender_id):
            # فلود شناسایی شد؛ این پیام رو کاملاً نادیده می‌گیریم (نه ذخیره،
            # نه پردازش) تا صف بقیه‌ی کاربرها رو کند نکنه.
            return
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
    db.ensure_broadcast_column()


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

    # هر چند وقت یه‌بار پیام‌های دیده‌شده‌ی خیلی قدیمی رو پاک می‌کنیم
    if time.time() - last_cleanup > 3600:
        try:
            db.cleanup_old_seen_messages()
        except Exception:
            traceback.print_exc()
        last_cleanup = time.time()

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
