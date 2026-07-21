import os
import time
import threading
import requests
from flask import Flask

import db

TOKEN = os.environ.get("BOT_TOKEN", "TOKEN_KHODETO_INJA_BEZAR")
BASE_URL = f"https://botapi.rubika.ir/v3/{TOKEN}"

app = Flask(__name__)


@app.route("/")
def home():
    return "Meowie bot is alive!"


def get_updates(offset_id=None):
    payload = {}
    if offset_id:
        payload["offset_id"] = offset_id
    try:
        resp = requests.post(f"{BASE_URL}/getUpdates", json=payload, timeout=15)
        return resp.json()
    except Exception as e:
        print("خطا در دریافت آپدیت:", e)
        return None


def send_message(chat_id, text):
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        print("خطا در ارسال پیام:", e)


def handle_message(chat_id, sender_id, sender_name, text):
    text = (text or "").strip()

    if text == "میو":
        ok, result = db.do_meow(sender_id, sender_name)
        if ok:
            msg = f"😽 گربه {sender_name} میو کرد و {result['points_earned']} میو پوینت گرفت!\n"
            msg += f"🪙 پوینت کل: {result['total_points']} | ⭐️ سطح: {result['level']}"
            if result["leveled_up"]:
                msg += f"\n🎉 تبریک! سطح گربه {sender_name} بالا رفت!"
            send_message(chat_id, msg)
        else:
            if result["reason"] == "cooldown":
                send_message(
                    chat_id,
                    f"⌛️ گربه {sender_name} هنوز خسته‌ست، {result['remaining']} ثانیه دیگه صبر کن.",
                )

    elif text == "میوهام":
        profile = db.get_profile(sender_id)
        if profile:
            needed = db.exp_needed_for_next_level(profile["level"])
            msg = (
                f"🪪 پروفایل میویی گربه {profile['username']}\n"
                f"⭐️ سطح: {profile['level']}\n"
                f"🪙 میو پوینت: {profile['points']}\n"
                f"🐾 پیشرفت تا سطح بعد: {profile['exp']}/{needed}"
            )
        else:
            msg = "هنوز هیچ میویی نکردی! بنویس 'میو' تا شروع کنی 🐾"
        send_message(chat_id, msg)


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
                offset_id = update.get("update_id", offset_id)
                message = update.get("new_message") or update.get("message") or {}

                chat_id = update.get("chat_id") or message.get("chat_id")
                sender_id = message.get("sender_id") or chat_id
                sender_name = message.get("sender_name") or message.get("first_name") or "ناشناس"
                text = message.get("text")

                if chat_id and text:
                    print(f"پیام از {sender_name} ({sender_id}): {text}")
                    handle_message(chat_id, sender_id, sender_name, text)

        time.sleep(3)


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
