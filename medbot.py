import os
import json
import requests
import logging
import re
from flask import Flask, request, Response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 🔐 Load secrets from environment variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Shivang")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
MAPMYINDIA_API_KEY = "kqkihoffqsittbvhvodrptxujmpvwbjeqdpd"  # Static key

if not OPENROUTER_API_KEY or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    raise ValueError("Missing one or more required environment variables.")

DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, seek immediate care."
)

# 💬 Predefined responses
PREDEFINED_RESPONSES = {
    "hi": "👋 Hello! I'm your health assistant. How can I support you today?",
    "hello": "Hi there! 😊 Feel free to ask about wellness, safety, or self-care.",
    "thanks": "You're welcome! 🙏 Stay safe and take care.",
    "bye": "Goodbye! 👋 Wishing you good health and happiness.",
    "who are you": "I'm a cautious, multilingual health assistant here to guide you with wellness tips and safety advice.",
    "help": "You can ask me about symptoms, healthy habits, or how to stay safe. I'm here to support you!",
    "नमस्ते": "🙏 नमस्ते! मैं आपका स्वास्थ्य सहायक हूँ। आपकी कैसे मदद कर सकता हूँ?",
    "ধন্যবাদ": "আপনাকে স্বাগতম! 🙏 সুস্থ থাকুন এবং যত্ন নিন।",
    "বিদায়": "বিদায়! 👋 আপনার সুস্বাস্থ্য কামনা করছি।"
}

# 🔍 Regex matcher for flexible input
def match_predefined(text):
    text = text.lower().strip()
    if re.search(r"\b(hi|hello|hey|नमस्ते|হ্যালো)\b", text):
        return PREDEFINED_RESPONSES.get("hi") or PREDEFINED_RESPONSES.get("नमस्ते")
    elif re.search(r"\b(thanks|thank you|धन्यवाद|ধন্যবাদ)\b", text):
        return PREDEFINED_RESPONSES.get("thanks") or PREDEFINED_RESPONSES.get("धन्यवाद")
    elif re.search(r"\b(bye|goodbye|विदाई|বিদায়)\b", text):
        return PREDEFINED_RESPONSES.get("bye") or PREDEFINED_RESPONSES.get("विदाई")
    elif re.search(r"\b(who are you|your name|तुम कौन हो|তুমি কে)\b", text):
        return PREDEFINED_RESPONSES["who are you"]
    elif re.search(r"\b(help|support|मदद|সাহায্য)\b", text):
        return PREDEFINED_RESPONSES["help"]
    return None

# 📍 Geocode location name to coordinates
def get_coordinates_from_location(location_text):
    url = f"https://apis.mappls.com/advancedmaps/v1/{MAPMYINDIA_API_KEY}/geoCode"
    params = {"address": location_text}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("copResults", [])[0]
        lat = float(result["latitude"])
        lon = float(result["longitude"])
        return lat, lon
    except Exception as e:
        logging.error(f"Geocoding failed: {e}")
        return None, None

# 🏥 Hospital search via MapMyIndia
def get_nearby_hospitals(lat=22.215, lon=83.396):
    url = f"https://apis.mappls.com/advancedmaps/v1/{MAPMYINDIA_API_KEY}/search"
    params = {
        "keywords": "hospital",
        "refLocation": f"{lat},{lon}",
        "radius": 5000
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        hospitals = data.get("suggestedLocations", [])
        if not hospitals:
            return "🚫 No hospitals found nearby."
        reply = "🏥 Nearby Hospitals:\n"
        for i, h in enumerate(hospitals[:5], 1):
            reply += f"{i}. {h['placeName']} – {h['placeAddress']} ({h['distance']})\n"
        return reply
    except Exception as e:
        logging.error(f"Hospital search failed: {e}")
        return "⚠️ Unable to fetch hospital info right now."

# 🧠 OpenRouter API call
def call_openrouter(user_text):
    if not user_text or not isinstance(user_text, str):
        return "Sorry, I couldn't understand your message. Please try again."

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "You are a cautious, empathetic health assistant designed to support general wellness. "
        "You are a multilingual health assistant. Always reply in the user's language. Be empathetic, clear, and culturally sensitive. "
        "Provide friendly, informative guidance on self-care, lifestyle habits, and safety tips. "
        "Avoid diagnosing, prescribing, or making clinical decisions. If symptoms are severe, unusual, or potentially life-threatening, advise users to seek immediate professional care. "
        "Use emojis to enhance clarity and warmth. Politely redirect non-health queries. "
        "For red-flag symptoms (e.g., chest pain, severe bleeding, difficulty breathing), instruct users to contact emergency services without delay.\n\n"
        f"Always end with this disclaimer:\n{DISCLAIMER}\n\n"
        "Trusted health resources:\n"
        "- National Health Portal (India): https://www.nhp.gov.in\n"
        "- Ministry of Health and Family Welfare: https://mohfw.gov.in\n"
        "- World Health Organization: https://www.who.int\n"
        "- Indian Council of Medical Research: https://www.icmr.gov.in"
    )

    payload = {
        "model": "deepseek/deepseek-chat-v3.1:free",
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
        logging.error(f"❌ OpenRouter failed with: {e}")
        return "⚠️ I'm currently unable to respond. Please try again later.\n\n" + DISCLAIMER

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
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        logging.error(f"WhatsApp send error: {e}")

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
                        message_text = message.get("text", {}).get("body", "")
                        if message_text and phone_number:
                            logging.info(f"👤 Name: {contact_name}")
                            logging.info(f"📱 Phone: {phone_number}")
                            logging.info(f"💬 Message: {message_text}")

                            # 🏥 Check for hospital-related query
                            if re.search(r"\b(hospital|clinic|emergency|doctor)\b", message_text.lower()):
                                # Try to extract location from message
                                match = re.search(r"\b(?:near|in)\s+([a-zA-Z\s]+)", message_text.lower())
                                location = match.group(1).strip() if match else None

                                if location:
                                    lat, lon = get_coordinates_from_location(location)
                                    if lat and lon:
                                        reply = get_nearby_hospitals(lat, lon)
                                    else:
                                        reply = "⚠️ I couldn't find that location. Please try again with a city or area name."
                                else:
                                    reply = get_nearby_hospitals()  # fallback to Raigarh
                            else:
                                reply = match_predefined(message_text) or call_openrouter(message_text)

                            send_whatsapp_message(phone_number, reply)

    return Response("EVENT_RECEIVED", status=200)
# 🏥 Health check route
@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

# 🚀 Start the Flask app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))