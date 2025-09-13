import os
import json
import requests
import logging
import re
from flask import Flask, request, Response
from flask_sqlalchemy import SQLAlchemy
from predictor import predict_disease

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# 🔐 Load secrets
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Shivang")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

if not OPENROUTER_API_KEY or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    raise ValueError("Missing required environment variables.")

# 🧠 SQLite setup
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sessions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    history = db.Column(db.Text)  # JSON string of messages

with app.app_context():
    db.create_all()

# Disclaimer
DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, seek immediate care."
)

# --- Memory helpers ---
def load_session(phone_number):
    session = UserSession.query.filter_by(phone_number=phone_number).first()
    if session:
        return json.loads(session.history)
    return []

def save_session(phone_number, messages):
    session = UserSession.query.filter_by(phone_number=phone_number).first()
    if session:
        session.history = json.dumps(messages)
    else:
        session = UserSession(phone_number=phone_number, history=json.dumps(messages))
        db.session.add(session)
    db.session.commit()

def clear_session(phone_number):
    session = UserSession.query.filter_by(phone_number=phone_number).first()
    if session:
        db.session.delete(session)
        db.session.commit()

# --- WhatsApp sender ---
def send_whatsapp_message(to_number, message_text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message_text}
    }
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        logging.error(f"WhatsApp send error: {e}")

# --- Webhook verification ---
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(challenge, status=200)
    return Response("Verification failed", status=403)

# --- Webhook handler ---
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
                    contact_name = contacts[0].get("profile", {}).get("name", "Unknown") if contacts else "Unknown"

                    for message in messages:
                        message_text = message.get("text", {}).get("body", "").strip()
                        if not message_text or not phone_number:
                            continue

                        logging.info(f"👤 {contact_name} ({phone_number}): {message_text}")

                        # --- Check for follow-up response ---
                        history = load_session(phone_number)
                        if history and history[-1].get("role") == "followup":
                            prev_symptoms = history[-1]["symptoms"]
                            new_symptoms = [s.strip().lower().replace(" ", "_") for s in message_text.split(",")]
                            all_symptoms = prev_symptoms + new_symptoms
                            result = predict_disease(all_symptoms, days=2)

                            if "error" in result:
                                reply = f"⚠️ {result['error']}"
                            else:
                                reply = (
                                    f"🩺 You may have: {result['disease']} ({result['confidence']}% confidence)\n"
                                    f"📖 Description: {result['description']}\n"
                                    f"⚠️ Severity: {result['severity']}\n"
                                    f"✅ Precautions:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(result['precautions'])])
                                )
                            history.pop()  # remove followup state
                            save_session(phone_number, history)
                            send_whatsapp_message(phone_number, reply)
                            continue

                        # --- Normal symptom check ---
                        if message_text.lower().startswith("check:"):
                            raw = message_text.split("check:", 1)[1]
                            symptoms = [s.strip() for s in raw.split(",")]
                            result = predict_disease(symptoms, days=2)

                            if "error" in result:
                                reply = f"⚠️ {result['error']}"
                            elif "follow_up" in result:
                                reply = (
                                    "🧪 I need a bit more info to be accurate.\n"
                                    "Are you also experiencing any of these symptoms?\n" +
                                    "\n".join([f"- {s}" for s in result["follow_up"]])
                                )
                                history.append({"role": "followup", "symptoms": symptoms})
                                save_session(phone_number, history)
                            else:
                                reply = (
                                    f"🩺 You may have: {result['disease']} ({result['confidence']}% confidence)\n"
                                    f"📖 Description: {result['description']}\n"
                                    f"⚠️ Severity: {result['severity']}\n"
                                    f"✅ Precautions:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(result['precautions'])])
                                )

                            send_whatsapp_message(phone_number, reply)
                            continue

    return Response("EVENT_RECEIVED", status=200)

# --- Health check ---
@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

# --- Run ---
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
