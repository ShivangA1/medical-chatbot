import os
import re
import json
import requests
import logging
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 🔐 Load API keys from environment variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Shivang")  # Default fallback
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

if not OPENROUTER_API_KEY or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    raise ValueError("Missing one or more required environment variables.")

DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, "
    "seek immediate care."
)

# 🧠 OpenRouter API call
def call_openrouter(user_text):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    system_prompt = (
        "You are a cautious and empathetic health information assistant. "
        "Your role is to offer general wellness guidance, safety precautions, and self-care suggestions based on user input. "
        "Do not diagnose conditions, recommend treatments, or make clinical decisions. "
        "If symptoms appear severe, unusual, or persistent, gently encourage the user to seek professional medical care. "
        "Always prioritize clarity, emotional support, and responsible communication. "
        "Respond in a clear, structured format:\n"
        "- Overview of general guidance\n"
        "- Relevant precautions\n"
        "- Suggested self-care tips\n"
        "- Reminder to consult a healthcare professional if needed\n"
        "Keep responses concise and avoid unnecessary medical jargon. "
        "Also make it interactive by asking relevant questions based on user input. "
        "Use emojis where appropriate to enhance clarity and warmth. "
        "strictly If the user input is not related to health queries, politely inform them that you can only assist with health-related queries. "
        "If the user gives red-flag symptoms like chest pain, dizziness, severe bleeding, or loss of consciousness, immediately tell them to seek emergency medical help. "
        "Always end with the disclaimer:\n\n" + DISCLAIMER +
        "\nFor more information:\n"
        "https://www.nhp.gov.in\n"
        "https://mohfw.gov.in\n"
        "https://www.who.int\n"
        "https://www.icmr.gov.in\n"
    )
    payload = {
        "model": "mistralai/mistral-7b-instruct:free",
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
        return "Sorry, I'm unable to process your request right now. Please try again later."

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
                        logging.info(f"👤 Name: {contact_name}")
                        logging.info(f"📱 Phone: {phone_number}")
                        logging.info(f"💬 Message: {message_text}")

                        reply = call_openrouter(message_text)
                        reply += f"\n\n{DISCLAIMER}"
                        send_whatsapp_message(phone_number, reply)
                else:
                    logging.warning("⚠️ No messages found in value.")

    return Response("EVENT_RECEIVED", status=200)

# 🏥 Health check route
@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

# 🚀 Start the Flask app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))