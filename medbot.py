import os
import json
import requests
import logging
import time
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
    "for professional medical advice. If you think you may be experiencing a medical emergency, "
    "seek immediate care."
)

# 🧠 OpenRouter API call with retry logic
def call_openrouter(user_text):
    if not user_text or not isinstance(user_text, str):
        logging.warning("⚠️ Invalid user_text passed to OpenRouter.")
        return "Sorry, I couldn't understand your message. Please try again."

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "You are a cautious, empathetic health assistant. Offer general wellness advice, safety tips, and self-care suggestions. "
        "Do not diagnose, prescribe, or make clinical decisions. If symptoms are severe or unusual, encourage professional care. "
        "Respond clearly and warmly:\n- Precautions\n- Self-care tips\n- Reminder to consult a provider\n"
        "Use emojis for clarity. Redirect non-health queries. For red-flag symptoms (e.g., chest pain, severe bleeding), instruct emergency care only.\n\n"
        f"Always end with this disclaimer:\n{DISCLAIMER}\n\n"
        "For more info:\nhttps://www.nhp.gov.in\nhttps://mohfw.gov.in\nhttps://www.who.int\nhttps://www.icmr.gov.in"
    )

    payload = {
        "model": "openchat/openchat-3.5-1210",
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]
    }

    logging.info(f"🧠 OpenRouter payload:\n{json.dumps(payload, indent=2)}")

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            logging.info(f"✅ OpenRouter response:\n{json.dumps(data, indent=2)}")
            return data["choices"][0]["message"]["content"].strip()
        except requests.exceptions.HTTPError as e:
            logging.error(f"OpenRouter error: {e}")
            logging.error(f"Response content: {resp.text}")
            break
        except Exception as e:
            logging.error(f"OpenRouter error: {e}")
            break

    return "⚠️ I'm currently experiencing high traffic. Please try again shortly.\n\n" + DISCLAIMER

# 📤 Send WhatsApp message
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
        response = requests.post(url, headers=headers, json=payload)
        logging.info(f"📤 WhatsApp response: {response.json()}")
    except Exception as e:
        logging.error(f"WhatsApp send error: {e}")

# 🌐 Webhook verification (GET)
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logging.info("✅ Webhook verified successfully.")
        return Response(challenge, status=200)
    else:
        logging.warning("❌ Webhook verification failed.")
        return Response("Verification failed", status=403)

# 🌐 Webhook message handler (POST)
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    logging.info("✅ POST request received at /webhook")
    logging.info(f"📦 Raw POST data:\n{data}")

    if data.get("object") == "whatsapp_business_account":
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                field = change.get("field")
                logging.info(f"🔍 Incoming field: {field}")

                if "messages" in value:
                    messages = value["messages"]
                    contacts = value.get("contacts", [])

                    contact_name = contacts[0]["profile"]["name"] if contacts else "Unknown"
                    phone_number = contacts[0]["wa_id"] if contacts else "Unknown"

                    for message in messages:
                        message_text = message.get("text", {}).get("body", "")
                        if not message_text:
                            logging.warning("⚠️ Message is not text or is empty.")
                            continue

                        logging.info(f"👤 Name: {contact_name}")
                        logging.info(f"📱 Phone: {phone_number}")
                        logging.info(f"💬 Message: {message_text}")

                        reply = call_openrouter(message_text)
                        send_whatsapp_message(phone_number, reply)
                elif "statuses" in value:
                    logging.info("📬 Delivery status received. No user message to process.")
                else:
                    logging.warning("⚠️ No messages or statuses found.")

    return Response("EVENT_RECEIVED", status=200)

# 🏥 Health check route
@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

# 🚀 Start the Flask app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))