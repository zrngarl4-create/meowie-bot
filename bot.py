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


# ---------------------------------------------------------------------------
# پردازش پیام‌ها
# ---------------------------------------------------------------------------

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
    if text.startswith(SET_NAME_PREFIX
