import os
import re
import time
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


def bold(text):
    # روبیکا مطابق چند کتابخونه‌ی عمومی از ** برای بولد پشتیبانی می‌کنه.
    # اگه توی ربات خودت درست رندر نشد، بگو تا این تابع رو عوض کنیم.
    return f"**{text}**"


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
        return resp.json()
    except Exception as e:
        print("خطا در دریافت آپدیت:", e)
        return None


def send_message(chat_id, text, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        print("خطا در ارسال پیام:", e)


# ---------------------------------------------------------------------------
# قالب‌های پیام
# ---------------------------------------------------------------------------

def build_meow_success_message(display_name, result):
    return (
        "🌙 صدای میوت توی شهر پیچید...\n\n"
        f"🪙 +{format_number(result['points_earned'])} میو پوینت\n"
        f"💰 موجودی: {format_number(result['total_points'])} 🪙\n"
        f"⏳ {format_cooldown(result['cooldown_seconds'])} تا میوی بعدی"
        + (f"\n\n🎉 تبریک {bold(display_name)}! سطح گربه‌ت رفت رو {result['level']} ⭐️" if result["leveled_up"] else "")
    )


def build_cooldown_message(display_name, remaining):
    return f"⌛️ گربه {bold(display_name)} هنوز خسته‌ست، {format_cooldown(remaining)} دیگه صبر کن."


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
        name = bold(row["username"]) if rank <= 3 else row["username"]
        lines.append(f"{rank_emoji(rank)} {name}")
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
        f"🐈 گربه {bold(sender_name)}\n"
        f"└─ 💸 {format_number(amount)} 🪙\n"
        "        ⬇️\n"
        f"🐈 گربه {bold(receiver_name)}\n\n"
        "✅ انتقال با موفقیت انجام شد.\n\n"
        "💰 موجودی جدید:\n"
        f"{format_number(receiver_new_points)} 🪙"
    )


# ---------------------------------------------------------------------------
# پردازش پیام‌ها
# ---------------------------------------------------------------------------

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

    amount_text = normalize_digits(text[len(TRANSFER_PREFIX):]).strip()
    amount_text = amount_text.replace(",", "").replace("،", "").strip()
    match = re.search(r"\d+", amount_text)
    if not match:
        send_message(
            chat_id,
            "بعد از «انتقال میویی» مقدار رو بنویس، مثلاً:\nانتقال میویی 1000",
            reply_to_message_id=message_id,
        )
        return

    amount = int(match.group())

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


def handle_message(chat_id, sender_id, message_id, text, reply_to_message_id):
    text = (text or "").strip()

    # هر پیام تو یه گروه، یعنی این کاربر عضو فعال اون گروهه (برای لیدربرد گروهی)
    db.record_group_membership(chat_id, sender_id)

    # فرستنده‌ی این پیام رو ذخیره می‌کنیم تا اگه بعداً یکی روش ریپلای زد
    # (مثلاً برای انتقال میویی) بشه فرستادش رو پیدا کرد.
    db.record_seen_message(message_id, chat_id, sender_id)

    # دستور تنظیم اسم: "تنظیم میویی <اسم>"
    if text.startswith(SET_NAME_PREFIX):
        new_name = text[len(SET_NAME_PREFIX):].strip()
        if new_name:
            db.set_username(sender_id, new_name)
            send_message(
                chat_id,
                f"✅ باشه! از این به بعد صدات می‌زنم: {bold(new_name)}",
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
            needed = db.exp_needed_for_next_level(profile["level"])
            msg = (
                f"🪪 پروفایل میویی گربه {bold(display_name)}\n"
                f"⭐️ سطح: {profile['level']}\n"
                f"🪙 میو پوینت: {format_number(profile['points'])}\n"
                f"🐾 پیشرفت تا سطح بعد: {profile['exp']}/{needed}"
            )
        else:
            msg = "هنوز هیچ میویی نکردی! بنویس 'میو' تا شروع کنی 🐾"
        send_message(chat_id, msg, reply_to_message_id=message_id)


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

        time.sleep(3)


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
    
