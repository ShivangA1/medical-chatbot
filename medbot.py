import re
import json
import requests
import logging
from flask import Flask, request, jsonify,Response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 🔐 Load API keys
OPENROUTER_API_KEY = "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"
WHATSAPP_TOKEN = "EAAc4OFrgGZCQBPeeqMK9MWXXPdd0JsKlEm4ra6ZCX2QHhWZCSgnLVK1AuVcv0T1FDg1rtdIrzD9udhHmY33u176UP6ZAs6x2ZCEMwQ8jGlExAgsIgeQFQfAZByPDBIWZAqMUw3qriUp8pWuIhm8EcLZBwVek4QFr28Er0LbxCqGXz4uEi53QLdkZCBFme1ip0FBXxa7eWwnx9W793EK0spgqjZAPZAxUtl7FmL2ph1dZAraKvEAukQZDZD"
VERIFY_TOKEN = "Shivang"
PHONE_NUMBER_ID = "785129378016997"  # e.g., "785129378016997"

if not OPENROUTER_API_KEY or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    raise ValueError("Missing one or more required environment variables.")

DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, "
    "seek immediate care."
)




def call_openrouter(user_text):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    system_prompt =(

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
    "If the user input is not related to health, politely inform them that you can only assist with health-related queries and encourage them to ask a relevant question. "
    "If the user gives red-flag symptoms like chest pain, dizziness, severe bleeding, or loss of consciousness, immediately tell them to seek emergency medical help and do not provide any other information. "
    "Always end with the disclaimer:\n\n" + DISCLAIMER +
    "\nFor more information: https://www.nhp.gov.in\n"+"https://mohfw.gov.in/\n"+"https://www.who.int/\n"+"https://www.icmr.gov.in/\n"

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

# 🌐 Webhook endpoint
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

                # ✅ Your conditional block goes here
                if "messages" in value:
                    messages = value["messages"]
                    contacts = value.get("contacts", [])

                    # Extract contact info
                    contact_name = contacts[0]["profile"]["name"] if contacts else "Unknown"
                    phone_number = contacts[0]["wa_id"] if contacts else "Unknown"

                    for message in messages:
                        message_text = message.get("text", {}).get("body", "")
                        logging.info(f"👤 Name: {contact_name}")
                        logging.info(f"📱 Phone: {phone_number}")
                        logging.info(f"💬 Message: {message_text}")

                        reply = call_openrouter(message_text)
                        reply += f"\n\n{DISCLAIMER}"
                        send_whatsapp_message(phone_number,reply)
                else:
                    logging.warning("⚠️ No messages found in value.")

    return Response("EVENT_RECEIVED", status=200)



    

if __name__ == '__main__':
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))





