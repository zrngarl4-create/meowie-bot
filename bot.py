import os
import time
import threading
import requests
from flask import Flask

import db

TOKEN = os.environ.get("BOT_TOKEN", "TOKEN_KHODETO_INJA_BEZAR")
BASE_URL = f"https://botapi.rubika.ir/v3/{TOKEN}"

app = Flask(__name__)

SET_NAME_PREFIX = "تنظیم میویی"


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


def handle_message(chat_id, sender_id, message_id, text):
    text = (text or "").strip()

    # هر پیام تو یه گروه، یعنی این کاربر عضو فعال اون گروهه (برای لیدربرد گروهی)
    db.record_group_membership(chat_id, sender_id)

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
            msg = f"😽 گربه {sender_name} میو کرد و {result['points_earned']} میو پوینت گرفت!\n"
            msg += f"🪙 پوینت کل: {result['total_points']} | ⭐️ سطح: {result['level']}"
            if result["leveled_up"]:
                msg += f"\n🎉 تبریک! سطح گربه {sender_name} بالا رفت!"
            send_message(chat_id, msg, reply_to_message_id=message_id)
        else:
            if result["reason"] == "cooldown":
                remaining = result["remaining"]
                minutes = remaining // 60
                seconds = remaining % 60
                if minutes > 0:
                    time_text = f"{minutes} دقیقه و {seconds} ثانیه"
                else:
                    time_text = f"{seconds} ثانیه"
                send_message(
                    chat_id,
                    f"⌛️ گربه {sender_name} هنوز خسته‌ست، {time_text} دیگه صبر کن.",
                    reply_to_message_id=message_id,
                )

    elif text == "لیدربرد میویی":
        rows = db.get_leaderboard_group(chat_id, order_by="points", limit=10)
        if not rows:
            send_message(chat_id, "هنوز کسی تو این گروه لیدربرد نداره! اول میو کن 🐾", reply_to_message_id=message_id)
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 لیدربرد ثروتمندترین پیشی‌های این گروه:\n"]
        for i, row in enumerate(rows):
            rank = medals[i] if i < 3 else f"{i + 1}."
            lines.append(
                f"{rank} {row['username']} — 🪙 {row['points']} | ⭐️ سطح {row['level']}"
            )
        send_message(chat_id, "\n".join(lines), reply_to_message_id=message_id)

    elif text == "لیدربرد میویی کل":
        rows = db.get_leaderboard_global(order_by="points", limit=10)
        if not rows:
            send_message(chat_id, "هنوز کسی تو لیدربرد نیست! اول میو کن 🐾", reply_to_message_id=message_id)
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = ["👑 لیدربرد ثروتمندترین پیشی‌های کل دنیای میویی:\n"]
        for i, row in enumerate(rows):
            rank = medals[i] if i < 3 else f"{i + 1}."
            lines.append(
                f"{rank} {row['username']} — 🪙 {row['points']} | ⭐️ سطح {row['level']}"
            )
        send_message(chat_id, "\n".join(lines), reply_to_message_id=message_id)

    elif text == "میوهام":
        display_name = sender_name or "ناشناس"
        profile = db.get_profile(sender_id)
        if profile:
            needed = db.exp_needed_for_next_level(profile["level"])
            msg = (
                f"🪪 پروفایل میویی گربه {display_name}\n"
                f"⭐️ سطح: {profile['level']}\n"
                f"🪙 میو پوینت: {profile['points']}\n"
                f"🐾 پیشرفت تا سطح بعد: {profile['exp']}/{needed}"
            )
        else:
            msg = "هنوز هیچ میویی نکردی! بنویس 'میو' تا شروع کنی 🐾"
        send_message(chat_id, msg, reply_to_message_id=message_id)


def bot_loop():
    db.ensure_offset_table()
    db.ensure_extra_columns()
    db.ensure_group_members_table()
    offset_id = db.get_offset()
    print("بات میویی شروع به کار کرد... offset ذخیره‌شده:", offset_id)
    while True:
        result = get_updates(offset_id)

        if result:
            print("RAW UPDATE:", result)

            data = result.get("data", {})
            updates = data.get("updates", [])

            for update in updates:
                message = update.get("new_message") or update.get("message") or {}

                chat_id = update.get("chat_id") or message.get("chat_id")
                sender_id = message.get("sender_id") or chat_id
                message_id = message.get("message_id")
                text = message.get("text")

                if chat_id and text:
                    print(f"پیام از {sender_id}: {text}")
                    handle_message(chat_id, sender_id, message_id, text)

            new_offset_id = data.get("next_offset_id", offset_id)
            if new_offset_id != offset_id:
                offset_id = new_offset_id
                db.set_offset(offset_id)

        time.sleep(3)


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    
