import os
import json
import requests
import logging
from flask import Flask, request, Response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 🔐 Load secrets from environment variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Shivang")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

if not OPENROUTER_API_KEY or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    raise ValueError("Missing one or more required environment variables.")

DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, seek immediate care."
)

# 🧠 OpenRouter API call
def call_openrouter(user_text):
    logging.info(f"🧠 Incoming user_text: {user_text}")
    if not user_text or not isinstance(user_text, str):
        return "Sorry, I couldn't understand your message. Please try again."

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "You are a cautious, empathetic health assistant. Offer general wellness advice, safety tips, and self-care suggestions. "
        "Do not diagnose, prescribe, or make clinical decisions. If symptoms are severe or unusual, encourage professional care. "
        "Use emojis for clarity. Redirect non-health queries. For red-flag symptoms (e.g., chest pain, severe bleeding), instruct emergency care only.\n\n"
        f"Always end with this disclaimer:\n{DISCLAIMER}\n\n"
        "For more info:\nhttps://www.nhp.gov.in\nhttps://mohfw.gov.in\nhttps://www.who.int\nhttps://www.icmr.gov.in"
    )

    payload = {
        "model": "mistralai/mistral-7b-instruct:free",
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"OpenRouter error: {e}")
        return "⚠️ I'm currently unable to respond. Please try again later.\n\n" + DISCLAIMER

# 📤 Send WhatsApp text message
def send_whatsapp_message(to_number, message_text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "body": message_text
        }
    }
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        logging.error(f"WhatsApp send error: {e}")

# 📤 Send WhatsApp buttons
def send_whatsapp_buttons(to_number):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "Hi 👋 I'm your health assistant. What would you like to do?"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "get_tips",
                            "title": "🩺 Get Wellness Tips"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "find_clinic",
                            "title": "🏥 Find Nearby Clinic"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "talk_human",
                            "title": "👨‍⚕️ Talk to a Human"
                        }
                    }
                ]
            }
        }
    }
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        logging.error(f"WhatsApp button send error: {e}")

# 🌐 Webhook verification
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(challenge, status=200)
    return Response("Verification failed", status=403)

# 🌐 Webhook message handler
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()

    if data.get("object") == "whatsapp_business_account":
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" in value:
                    messages = value["messages"]
                    contacts = value.get("contacts", [])
                    phone_number = contacts[0]["wa_id"] if contacts else None
                    contact_name = contacts[0]["profile"]["name"] if contacts else "Unknown"

                    for message in messages:
                        message_type = message.get("type")
                        message_text = ""

                        if message_type == "text":
                            message_text = message.get("text", {}).get("body", "")
                        elif message_type == "button":
                            message_text = message.get("button", {}).get("payload", "")
                        else:
                            logging.warning("⚠️ Unsupported message type.")
                            continue

                        if not message_text or not phone_number:
                            logging.warning("⚠️ Message text is empty or missing phone number.")
                            continue

                        logging.info(f"👤 Name: {contact_name}")
                        logging.info(f"📱 Phone: {phone_number}")
                        logging.info(f"💬 Message: {message_text}")

                        # Handle button replies
                        if message_text == "get_tips":
                            reply = "🌿 Wellness Tips:\n- Stay hydrated\n- Sleep 7–8 hrs\n- Take short walks\n\n" + DISCLAIMER
                        elif message_text == "find_clinic":
                            reply = "📍 Please share your location so I can help you find nearby clinics.\n\n" + DISCLAIMER
                        elif message_text == "talk_human":
                            reply = "👨‍⚕️ You can reach a health professional at 1800-XYZ-HELP or visit https://www.nhp.gov.in\n\n" + DISCLAIMER
                        elif message_text.lower() in ["hi", "hello", "start"]:
                            send_whatsapp_buttons(phone_number)
                            continue
                        else:
                            reply = call_openrouter(message_text)

                        send_whatsapp_message(phone_number, reply)

    return Response("EVENT_RECEIVED", status=200)

# 🏥 Health check route
@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

# 🚀 Start the Flask app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))