import os
import re
import time
import datetime
import threading
import traceback
import requests
from flask import Flask

import db

TOKEN = os.environ.get("BOT_TOKEN", "TOKEN_KHODETO_INJA_BEZAR")
BASE_URL = f"https://botapi.rubika.ir/v3/{TOKEN}"

app = Flask(__name__)

SET_NAME_PREFIX = "تنظیم میویی"
TRANSFER_PREFIX = "انتقال میویی"
CHARGE_PREFIX = "شارژ میویی"
HELP_COMMAND = "راهنما میویی"

CASINO_MENU_COMMAND = "کازینو میویی"
COINFLIP_PREFIX = "شیر یا خط"
DICE_PREFIX = "تاس"
HIGH_WORD = "بالا"
LOW_WORD = "پایین"
SLOT_PREFIX = "اسلات"
ROULETTE_PREFIX = "رولت"
BLACKJACK_PREFIX = "بلک جک"
HIT_WORD = "بکش"
STAND_WORD = "بایست"
CRASH_PREFIX = "کرش"
CRASH_STATUS_COMMAND = "وضعیت کرش"
CASHOUT_WORD = "برداشت"
MINES_PREFIX = "مین یاب"
MINES_OPEN_PREFIX = "باز کن"

# آیدی‌های ادمین از یه متغیر محیطی خونده می‌شه، نه از تو کد، که امن‌تره
# و بدون تغییر کد می‌شه عوضش کرد. تو Render باید مقدارش رو جدا با کاما بذاری:
# ADMIN_USER_IDS=u0Gw4KT048853a398113c76238074183,u0KTX2g022ecd0746f4b971ce14de578
ADMIN_USER_IDS = {
    uid.strip() for uid in os.environ.get("ADMIN_USER_IDS", "").split(",") if uid.strip()
}

CAT_TITLES = {
    1: "🐱 پیشی کوچولو",
    2: "🐾 پیشی بازیگوش",
    3: "🌿 پیشی کنجکاو",
    4: "🐈 پیشی شکارچی",
    5: "⚔️ پنجه‌تیز",
    6: "🌙 پیشی شبگرد",
    7: "💨 پنجه‌سریع",
    8: "🐅 ببرک میویی",
    9: "👑 فرمانده پنجه‌ها",
    10: "🔥 شکارچی افسانه‌ای",
    11: "🌌 سایهٔ شب",
    12: "⚡ صاعقهٔ پنجه",
    13: "🩸 شکارچی خاموش",
    14: "🐉 اژدهای میویی",
    15: "💎 پیشی سلطنتی",
    16: "🌠 نگهبان کهکشان",
    17: "👑 سلطان پیشی‌ها",
    18: "☠️ فرمانروای میویی",
    19: "🌟 اسطورهٔ پنجه",
    20: "🐈‍⬛ خدای پیشی‌ها",
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


def send_message(chat_id, text, reply_to_message_id=None):
    # روبیکا سینتکس مارک‌داونِ ** رو تو متن رندر نمی‌کنه؛ فرمت واقعی‌ای که
    # قبول می‌کنه یه فیلد جدا به اسم metadata با بازه‌های بولده.
    # چون کاربر می‌خواد کل پیام برجسته باشه، کل طول متن رو یه بازه‌ی Bold می‌گیریم.
    metadata = None
    if text:
        metadata = {
            "meta_data_parts": [
                {"from_index": 0, "length": len(text), "type": "Bold"}
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
        f"🪙 +{format_number(result['points_earned'])} میو پوینت\n"
        f"💰 موجودی: {format_number(result['total_points'])} 🪙\n"
        f"⏳ {format_cooldown(result['cooldown_seconds'])} تا میوی بعدی"
        + (f"\n\n🎉 تبریک {display_name}! سطح گربه‌ت رفت رو {result['level']} ⭐️" if result["leveled_up"] else "")
    )


def build_cooldown_message(display_name, remaining):
    return f"⌛️ گربه {display_name} هنوز خسته‌ست، {format_cooldown(remaining)} دیگه صبر کن."


def build_leaderboard_message(rows, scope_label, viewer_rank=None, viewer_points=None):
    lines = [
        "🏆 ══════【 لیدربرد میویی 】══════ 🏆",
        "",
        f"👑 دسته: ثروتمندترین پیشی‌های {scope_label} 🪙",
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
        "🧲 انتقال میویی\n\n"
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
        "╮──「 🐱 پروفایل میویی 🐱 」",
        "",
        f"┐─ 👤 کاربر : {display_name}",
        f"┘─ 🪪 آیدی : {user_id}",
        "",
        f"┐─ 💰 میو پوینت ها : {format_number(profile['points'])} 🪙",
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
        "📖═══「 راهنمای میویی 」═══📖\n\n"
        "🐾 دستورات پایه\n"
        "میو\n"
        "└ گرفتن میوپوینت (هر ۵ دقیقه یه‌بار)\n\n"
        "تنظیم میویی <اسم>\n"
        "└ تنظیم اسمی که ربات صدات می‌زنه\n\n"
        "میوهام\n"
        "└ دیدن پروفایل خودت\n\n"
        "میوهاش (با ریپلای رو یکی دیگه)\n"
        "└ دیدن پروفایل اون شخص\n\n"
        "آیدی من\n"
        "└ دیدن آیدی داخلیت\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏆 لیدربرد\n"
        "لیدربرد میویی\n"
        "└ لیدربرد همین گروه\n\n"
        "لیدربرد میویی کل\n"
        "└ لیدربرد کل بازیکن‌های ربات\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💸 اقتصاد\n"
        "انتقال میویی <عدد> (با ریپلای رو گیرنده)\n"
        "└ انتقال میوپوینت به یکی دیگه (حداقل ۵۰۰، حداکثر ۱۰,۰۰۰)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🐱 پیشی\n"
        "پیشی / پیشی‌هام\n"
        "└ دیدن یا سرپرستی گرفتن یه پیشی\n\n"
        "خرید پیشی\n"
        "└ خرید اولین پیشی (۵۰۰ 🪙، نیاز به سطح ۲+)\n\n"
        "ارتقا پیشی\n"
        "└ بالا بردن سطح پیشی\n\n"
        "برداشت میوپوینت ها\n"
        "└ خالی کردن صندوقچه‌ی پیشی\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎰 کازینو میویی\n"
        "└ ۷ تا بازی داره! برای دیدن همه بنویس: کازینو میویی\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🐾 هر وقت گم شدی، کافیه دوباره بنویسی «راهنما میویی»"
    )


def build_casino_menu_message():
    return (
        "🎰═══「 کازینو میویی 」═══🎰\n\n"
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
        "💥 کرش\n"
        "└ دستور: کرش <مبلغ>  (بعدش «وضعیت کرش» یا «برداشت»)\n\n"
        "💣 مین‌یاب\n"
        "└ دستور: مین یاب <مبلغ> <تعداد مین>  (بعدش «باز کن <شماره>» یا «برداشت»)\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏰ تو بلک‌جک و مین‌یاب، اگه {db.GAME_TIMEOUT_SECONDS} ثانیه جواب ندی، خودکار می‌بازی!\n\n"
        f"💰 حداقل شرط: {format_number(db.CASINO_MIN_BET)} 🪙  |  حداکثر: {format_number(db.CASINO_MAX_BET)} 🪙"
    )


def send_casino_error(chat_id, message_id, result, usage_text):
    reason = result["reason"]
    if reason == "below_min":
        send_message(chat_id, f"حداقل شرط {format_number(result['min'])} میوپوینته.", reply_to_message_id=message_id)
    elif reason == "above_max":
        send_message(chat_id, f"حداکثر شرط {format_number(result['max'])} میوپوینته.", reply_to_message_id=message_id)
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
# 💥 کرش
# ---------------------------------------------------------------------------

def handle_crash_start(chat_id, sender_id, message_id, text):
    usage = "بنویس مثلاً:\nکرش 500"
    rest = normalize_digits(text[len(CRASH_PREFIX):]).strip()
    amount_match = re.search(r"\d+", rest.replace(",", "").replace("،", ""))
    if not amount_match:
        send_message(chat_id, usage, reply_to_message_id=message_id)
        return

    bet = int(amount_match.group())
    ok, result = db.start_crash(sender_id, chat_id, bet)
    if not ok:
        if result["reason"] == "already_in_game":
            send_message(chat_id, "تو همین الان وسط یه بازی دیگه‌ای! اول تمومش کن.", reply_to_message_id=message_id)
        else:
            send_casino_error(chat_id, message_id, result, usage)
        return

    msg = (
        "💥 دور کرش شروع شد!\n\n"
        f"💵 شرط: {format_number(result['bet'])} 🪙\n"
        "📈 ضریب فعلی: 1.00x\n\n"
        "بنویس «وضعیت کرش» برای دیدن ضریب فعلی،\n"
        "یا هر وقت خواستی بنویس «برداشت» تا با همون ضریب ببری...\n"
        "ولی مراقب باش دیر نشه! 😨"
    )
    send_message(chat_id, msg, reply_to_message_id=message_id)


def handle_crash_status(chat_id, sender_id, message_id):
    ok, result = db.get_crash_status(sender_id)
    if not ok:
        send_message(chat_id, "الان تو دور کرشی نیستی. بنویس «کرش <مبلغ>» تا شروع کنی.", reply_to_message_id=message_id)
        return

    if result["crashed"]:
        send_message(
            chat_id,
            f"💥 دیر شد! این دور تو {result['multiplier']}x ترکیده بود.",
            reply_to_message_id=message_id,
        )
        return

    potential = int(result["bet"] * result["multiplier"])
    send_message(
        chat_id,
        (
            f"📈 ضریب الان: {result['multiplier']}x\n"
            f"💰 اگه همین الان برداشت کنی: {format_number(potential)} 🪙\n\n"
            "برداشت کنم یا ریسک کنم؟ 🤔"
        ),
        reply_to_message_id=message_id,
    )


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

    if game["game_type"] == "crash":
        ok, result = db.crash_cashout(sender_id)
        if not ok:
            return
        if result["status"] == "too_late":
            send_message(
                chat_id,
                f"😿 دیر شد! تو {result['multiplier']}x ترکید و {format_number(result['bet'])} 🪙 از دست دادی.",
                reply_to_message_id=message_id,
            )
        else:
            send_message(
                chat_id,
                (
                    "✅ درست موقع برداشت کردی!\n\n"
                    f"📈 ضریب لحظه‌ی برداشت: {result['multiplier']}x\n"
                    f"💵 شرط: {format_number(result['bet'])} 🪙  ←  🎉 بردی: {format_number(result['winnings'])} 🪙\n\n"
                    f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙"
                ),
                reply_to_message_id=message_id,
            )

    elif game["game_type"] == "mines":
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
            "هنوز اسمتو نمی‌دونم! اول بنویس:\nتنظیم میویی <اسمت>",
            reply_to_message_id=message_id,
        )
        return

    if not reply_to_message_id:
        send_message(
            chat_id,
            "برای انتقال میویی باید روی پیام همون گربه‌ای که می‌خوای بهش بدی ریپلای بزنی و بنویسی:\nانتقال میویی <عدد>",
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
            "نمی‌تونی به خودت میوپوینت انتقال بدی 😹",
            reply_to_message_id=message_id,
        )
        return

    amount = parse_amount_after_prefix(text, TRANSFER_PREFIX)
    if amount is None:
        send_message(
            chat_id,
            "بعد از «انتقال میویی» مقدار رو بنویس، مثلاً:\nانتقال میویی 1000",
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
            f"حداقل مقدار انتقال {format_number(result['min'])} میوپوینته.",
            reply_to_message_id=message_id,
        )
    elif reason == "above_max":
        send_message(
            chat_id,
            f"حداکثر مقدار انتقال {format_number(result['max'])} میوپوینته.",
            reply_to_message_id=message_id,
        )
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"تو درحال حاضر این مقدار میوپوینت رو نداری 😿\nموجودی فعلیت: {format_number(result['have'])} 🪙",
            reply_to_message_id=message_id,
        )
    elif reason == "self":
        send_message(
            chat_id,
            "نمی‌تونی به خودت میوپوینت انتقال بدی 😹",
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
            "باید روی پیام همون کاربر ریپلای بزنی و بنویسی:\nشارژ میویی <عدد>",
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
            "بعد از «شارژ میویی» یه مقدار معتبر بنویس، مثلاً:\nشارژ میویی 50000",
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


# ---------------------------------------------------------------------------
# سیستم پیشی
# ---------------------------------------------------------------------------

def cat_title_for_rank(rank):
    return CAT_TITLES.get(rank, f"پیشی رتبه {rank}")


def build_cat_weak_message():
    return (
        "😾 تو هنوز یه گربه ضعیف , نوب و بی خاصیت هستی\n"
        "❗️ برای خرید پیشی , باید حداقل سطح 2 باشی."
    )


def build_cat_shelter_message():
    return (
        "🐈✨ پناهگاه پیشی‌ها ✨🐈\n\n"
        "بالاخره وقتشه اولین پیشی خودتو به سرپرستی بگیری...\n\n"
        "اما یادت باشه؛\n"
        "هر پیشی فقط صاحب یه نفر میشه و تا آخر همراهت می‌مونه. 🐾\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "💰 هزینه سرپرستی:\n"
        f"🐾{format_number(db.CAT_PRICE)} میوپوینت"
    )


def build_cat_adopt_success_message():
    return (
        "🌌 در سکوت شب، صدای آروم یه «میووو...» به گوشت رسید...\n"
        "یه پیشی کوچولو آروم آروم به سمتت اومد و کنارت نشست. 🐈\n"
        "انگار از امروز تو رو به عنوان صاحب خودش انتخاب کرده...\n"
        "❤️ از این لحظه شما باهم یک خانواده‌اید.\n"
        "🏠 تعداد پیشی‌های تو: ۱\n"
        "🔎 برای دیدنش بنویس: پیشی‌هام"
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
        f"🐱 پیشی {owner_display_name} 🐈",
        "",
        f"💕 نام : {cat['name'] or '—'}",
        "",
        f"🌟 مقام : {cat_title_for_rank(rank)} ({rank})",
        f"⭐️ سطح : {level} / {rank_cap}",
        "",
        f"💰 میو پوینت های تولید شده : {format_number(pending)} 🪙",
        f"💫 تولید میو پوینت در ثانیه : {format_number(per_second)} 🪙",
        f"📦 ظرفیت : {format_number(capacity)}",
        "",
    ]
    if db.cat_is_maxed(rank, level):
        lines.append("🏆 این پیشی به حداکثر رتبه و سطح ممکن رسیده!")
    else:
        cost = db.cat_upgrade_cost(rank, level)
        lines.append(f"💰 هزینه ارتقا سطح : {format_number(cost)} 🪙")

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
        send_message(chat_id, "تو همین الان یه پیشی داری! نمی‌تونی بیشتر از یکی داشته باشی 🐾", reply_to_message_id=message_id)
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"برای گرفتن پیشی به {format_number(db.CAT_PRICE)} میوپوینت نیاز داری.",
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
                f"✅ {format_number(result['collected'])} میوپوینت از صندوقچه‌ی پیشی‌ت برداشت شد!\n"
                f"💰 موجودی جدید: {format_number(result['new_points'])} 🪙\n"
                "📦 صندوقچه از الان دوباره شروع به پر شدن می‌کنه."
            ),
            reply_to_message_id=message_id,
        )
        return

    reason = result["reason"]
    if reason == "no_cat":
        send_message(chat_id, "هنوز پیشی نداری! اول بنویس «پیشی» تا یکی به سرپرستی بگیری 🐾", reply_to_message_id=message_id)
    else:
        send_message(chat_id, "صندوقچه‌ی پیشی‌ت هنوز خالیه، بعداً دوباره سر بزن.", reply_to_message_id=message_id)


def handle_cat_upgrade(chat_id, sender_id, message_id, sender_name):
    ok, result = db.upgrade_cat(sender_id)
    if ok:
        if result["rank_up"]:
            title = cat_title_for_rank(result["new_rank"])
            msg = (
                f"🎉 تبریک! پیشی‌ت به مقام جدید رسید: {title} ({result['new_rank']})!\n"
                f"💸 هزینه: {format_number(result['cost'])} 🪙\n"
                f"💰 موجودی باقی‌مونده: {format_number(result['remaining_points'])} 🪙"
            )
        else:
            msg = (
                f"✅ پیشی‌ت رفت رو سطح {result['new_level']}!\n"
                f"💸 هزینه: {format_number(result['cost'])} 🪙\n"
                f"💰 موجودی باقی‌مونده: {format_number(result['remaining_points'])} 🪙"
            )
        send_message(chat_id, msg, reply_to_message_id=message_id)
        return

    reason = result["reason"]
    if reason == "no_cat":
        send_message(chat_id, "هنوز پیشی نداری! اول بنویس «پیشی» تا یکی به سرپرستی بگیری 🐾", reply_to_message_id=message_id)
    elif reason == "maxed":
        send_message(chat_id, "پیشی‌ت به حداکثر رتبه و سطح ممکن رسیده! دیگه جای ارتقا نداره 🏆", reply_to_message_id=message_id)
    elif reason == "insufficient":
        send_message(
            chat_id,
            f"برای ارتقا به {format_number(result['cost'])} میوپوینت نیاز داری، ولی موجودیت کمتره.",
            reply_to_message_id=message_id,
        )
    else:
        send_message(chat_id, "یه مشکلی پیش اومد، دوباره امتحان کن.", reply_to_message_id=message_id)


def handle_message(chat_id, sender_id, message_id, text, reply_to_message_id):
    text = (text or "").strip()

    # هر پیام تو یه گروه، یعنی این کاربر عضو فعال اون گروهه (برای لیدربرد گروهی)،
    # و همینطور فرستنده‌ی این پیام رو ذخیره می‌کنیم تا اگه بعداً یکی روش ریپلای
    # زد (انتقال میویی، شارژ، میوهاش، ...) بشه فرستنده‌ش رو پیدا کرد.
    # این کار تو یه اتصال دیتابیس (نه دوتا) انجام می‌شه تا سریع‌تر باشه.
    db.record_message_context(chat_id, sender_id, message_id)

    # دستور تنظیم اسم: "تنظیم میویی <اسم>"
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
                "بعد از «تنظیم میویی» اسمتو بنویس، مثلاً:\nتنظیم میویی علی",
                reply_to_message_id=message_id,
            )
        return

    # دستور انتقال میویی: "انتقال میویی <عدد>" (باید ریپلای شده باشه)
    if text.startswith(TRANSFER_PREFIX):
        handle_transfer(chat_id, sender_id, message_id, text, reply_to_message_id)
        return

    # دستور شارژ میویی: فقط برای ادمین‌ها، "شارژ میویی <عدد>" روی ریپلای
    if text.startswith(CHARGE_PREFIX):
        handle_charge(chat_id, sender_id, message_id, text, reply_to_message_id)
        return

    sender_name = db.get_username(sender_id)

    if text == "میو":
        if not sender_name:
            send_message(
                chat_id,
                "هنوز اسمتو نمی‌دونم! اول بنویس:\nتنظیم میویی <اسمت>\nبعد دوباره میو کن 🐾",
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

    elif text == "لیدربرد میویی":
        rows = db.get_leaderboard_group(chat_id, order_by="points", limit=10)
        if not rows:
            send_message(chat_id, "هنوز کسی تو این گروه لیدربرد نداره! اول میو کن 🐾", reply_to_message_id=message_id)
            return
        viewer_rank = db.get_rank_group(chat_id, sender_id, order_by="points")
        viewer_points = db.get_points(sender_id)
        msg = build_leaderboard_message(rows, "این گروه", viewer_rank, viewer_points)
        send_message(chat_id, msg, reply_to_message_id=message_id)

    elif text == "لیدربرد میویی کل":
        rows = db.get_leaderboard_global(order_by="points", limit=10)
        if not rows:
            send_message(chat_id, "هنوز کسی تو لیدربرد نیست! اول میو کن 🐾", reply_to_message_id=message_id)
            return
        viewer_rank = db.get_rank_global(sender_id, order_by="points")
        viewer_points = db.get_points(sender_id)
        msg = build_leaderboard_message(rows, "جهان", viewer_rank, viewer_points)
        send_message(chat_id, msg, reply_to_message_id=message_id)

    elif text == "میوهام":
        display_name = sender_name or "ناشناس"
        profile = db.get_profile(sender_id)
        if profile:
            msg = build_profile_message(display_name, sender_id, profile)
        else:
            msg = "هنوز هیچ میویی نکردی! بنویس 'میو' تا شروع کنی 🐾"
        send_message(chat_id, msg, reply_to_message_id=message_id)

    elif text == "میوهاش":
        if not reply_to_message_id:
            send_message(
                chat_id,
                "برای دیدن پروفایل یکی دیگه، باید روی پیامش ریپلای بزنی و بنویسی «میوهاش».",
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

    elif text == HELP_COMMAND:
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

    elif text == CRASH_STATUS_COMMAND:
        handle_crash_status(chat_id, sender_id, message_id)

    elif text.startswith(CRASH_PREFIX):
        handle_crash_start(chat_id, sender_id, message_id, text)

    elif text.startswith(MINES_OPEN_PREFIX):
        handle_mines_open(chat_id, sender_id, message_id, text)

    elif text.startswith(MINES_PREFIX):
        handle_mines_start(chat_id, sender_id, message_id, text)

    elif text == CASHOUT_WORD:
        handle_cashout(chat_id, sender_id, message_id)

    elif text in ("پیشی", "پیشی‌هام", "پیشی هام"):
        handle_cat_status(chat_id, sender_id, message_id, sender_name)

    elif text == "خرید پیشی":
        handle_cat_buy(chat_id, sender_id, message_id, sender_name)

    elif text in ("ارتقا پیشی", "ارتقا"):
        handle_cat_upgrade(chat_id, sender_id, message_id, sender_name)

    elif text == "برداشت میوپوینت ها" or text == "برداشت میوپوینت‌ها":
        handle_cat_collect(chat_id, sender_id, message_id, sender_name)


def process_update(update):
    message = update.get("new_message") or update.get("message") or {}

    chat_id = update.get("chat_id") or message.get("chat_id")
    sender_id = message.get("sender_id") or chat_id
    message_id = message.get("message_id")
    text = message.get("text")
    reply_to_message_id = message.get("reply_to_message_id")

    if chat_id and text:
        print(f"پیام از {sender_id}: {text}")
        handle_message(chat_id, sender_id, message_id, text, reply_to_message_id)


def bot_loop():
    db.ensure_offset_table()
    db.ensure_extra_columns()
    db.ensure_group_members_table()
    db.ensure_seen_messages_table()
    db.ensure_admin_actions_table()
    db.ensure_cats_table()
    db.ensure_active_games_table()

    offset_id = db.get_offset()
    print("بات میویی شروع به کار کرد... offset ذخیره‌شده:", offset_id)

    last_cleanup = time.time()

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

            # هر تکرار حلقه (هر ~۱ ثانیه) چک می‌کنیم ببینیم مهلت بازی‌های
            # چندمرحله‌ای (بلک‌جک/کرش/مین‌یاب) کسی تموم شده یا نه. این باید
            # مکرر انجام بشه، نه هر ساعت، چون هم قانون ۶۵ ثانیه‌ای هم لحظه‌ی
            # ترکیدن کرش باید تقریباً بلافاصله رصد بشه.
            try:
                resolve_expired_games()
            except Exception:
                print("خطا در رسیدگی به بازی‌های منقضی‌شده:")
                traceback.print_exc()

            # هر چند وقت یه‌بار پیام‌های دیده‌شده‌ی خیلی قدیمی رو پاک می‌کنیم
            if time.time() - last_cleanup > 3600:
                try:
                    db.cleanup_old_seen_messages()
                except Exception:
                    traceback.print_exc()
                last_cleanup = time.time()

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
    threading.Thread(target=start_bot_loop_forever, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
