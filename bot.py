import os
import time
import threading
import requests
from flask import Flask

import db

TOKEN = os.environ.get("BOT_TOKEN", "TOKEN_KHODETO_INJA_BEZAR")
BASE_URL = f"https://botapi.rubika.ir/v3/{TOKEN}"

app = Flask(__name__)

user_name_cache = {}


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


def get_user_display_name(user_id):
    if user_id in user_name_cache:
        return user_name_cache[user_id]

    name = "ناشناس"
    try:
        resp = requests.post(
            f"{BASE_URL}/getChat", json={"chat_id": user_id}, timeout=15
        )
        print("RAW GETCHAT:", result)
        chat = result.get("data", {}).get("chat", {})

        first_name = chat.get("first_name", "")
        last_name = chat.get("last_name", "")
        title = chat.get("title", "")
        username = chat.get("username", "")

        if first_name or last_name:
            name = f"{first_name} {last_name}".strip()
        elif title:
            name = title
        elif username:
            name = username
    except Exception as e:
        print("خطا در گرفتن اطلاعات کاربر:", e)

    user_name_cache[user_id] = name
    return name


def handle_message(chat_id, sender_id, message_id, text):
    text = (text or "").strip()
    sender_name = get_user_display_name(sender_id)

    if text == "میو":
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

    elif text == "میوهام":
        profile = db.get_profile(sender_id)
        if profile:
            needed = db.exp_needed_for_next_level(profile["level"])
            msg = (
                f"🪪 پروفایل میویی گربه {sender_name}\n"
                f"⭐️ سطح: {profile['level']}\n"
                f"🪙 میو پوینت: {profile['points']}\n"
                f"🐾 پیشرفت تا سطح بعد: {profile['exp']}/{needed}"
            )
        else:
            msg = "هنوز هیچ میویی نکردی! بنویس 'میو' تا شروع کنی 🐾"
        send_message(chat_id, msg, reply_to_message_id=message_id)


def bot_loop():
    offset_id = None
    print("بات میویی شروع به کار کرد...")
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

            offset_id = data.get("next_offset_id", offset_id)

        time.sleep(3)


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
