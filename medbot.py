import os
import json
import requests
import logging
import re
from flask import Flask, request, Response
from flask_sqlalchemy import SQLAlchemy
from predictor import predict_disease
from predictor import training_columns
from difflib import get_close_matches




app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 🔐 Load secrets from environment variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Shivang")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

if not OPENROUTER_API_KEY or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    raise ValueError("Missing one or more required environment variables.")

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

# 💬 Predefined responses
DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, seek immediate care."
)

PREDEFINED_RESPONSES = {
    "hi": "👋 Hello! I'm your health assistant. How can I support you today || type 'help' to know what this bot can do",
    "hello": "Hi there! 😊 Feel free to ask about wellness, safety, or self-care.",
    "thanks": "You're welcome! 🙏 Stay safe and take care.",
    "bye": "Goodbye! 👋 Wishing you good health and happiness.",
    "who are you": "I'm a cautious, multilingual health assistant here to guide you with wellness tips and safety advice.",
    "help": "You can ask me about symptoms, healthy habits, or how to stay safe . type 'command' to see the available services.",
    "command": (
    "📋 Available commands:\n"
    "- '/reset' → Clear your memory and start fresh\n"
    "- '/summary' → Get a recap of our conversation\n"
    "- '/debug' → View current memory logs\n"
    "- 'check' → Run a health check based on symptoms\n"
    "- 'resources' → View trusted health websites\n"
    "- 'languages' → Learn about multilingual support\n"
    "- 'help' → See what I can do"
    ),
    "languages": "I can understand and respond in multiple languages. Just type your message in your preferred language!",
    "resources": "For reliable health information, visit:\n- National Health Portal (India): https://www.nhp.gov.in\n- Ministry of Health and Family Welfare: https://mohfw.gov.in\n- World Health Organization: https://www.who.int\n- Indian Council of Medical Research: https://www.icmr.gov.in",
    "emergency": (
    "🚨 If you're experiencing a medical emergency, please contact local emergency services immediately.\n"
    "In India, dial 102 for ambulance support. Your safety is the top priority!"
    ),
    "check": (
    "🩺 To check symptoms, type:\n"
    "'check: symptom1, symptom2, symptom3, .....'\n"
    "I'll analyze your symptoms and suggest possible conditions, precautions, and severity."
    ),
}



def match_predefined(text):
    text = text.lower().strip()
    if re.search(r"\b(hi|hello|hey)\b", text):
        return PREDEFINED_RESPONSES["hi"]
    elif re.search(r"\b(thanks|thank you)\b", text):
        return PREDEFINED_RESPONSES["thanks"]
    elif re.search(r"\b(bye|goodbye)\b", text):
        return PREDEFINED_RESPONSES["bye"]
    elif re.search(r"\b(who are you|your name)\b", text):
        return PREDEFINED_RESPONSES["who are you"]
    elif re.search(r"\b(help|support)\b", text):
        return PREDEFINED_RESPONSES["help"]
    elif re.search(r"\b(command|commands)\b", text):
        return PREDEFINED_RESPONSES["command"]
    elif re.search(r"\b(languages|language)\b", text):
        return PREDEFINED_RESPONSES["languages"]
    elif re.search(r"\b(resources|resource|info|information)\b", text):
        return PREDEFINED_RESPONSES["resources"]
    elif re.search(r"\b(emergency|urgent)\b", text):
        return PREDEFINED_RESPONSES["emergency"]
    elif re.search(r"\bcheck|check symptom|symptom|check symptoms\b", text):
        return PREDEFINED_RESPONSES["check"]
    return None

# 🧠 SQLite memory functions
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

def generate_followups(symptoms, phone_number):
    prompt = (
        f"A user reported these symptoms: {', '.join(symptoms)}.\n"
        "Generate 3–4 follow-up questions that would help refine a health diagnosis. "
        "Be empathetic, medically cautious, and avoid clinical advice. "
        "Use simple language and include emojis for warmth."
    )
    return call_openrouter(prompt, phone_number)

def match_symptom(s, known_symptoms):
    match = get_close_matches(s, known_symptoms, n=1, cutoff=0.8)
    return match[0] if match else s



# 🧠 OpenRouter API call
def call_openrouter(user_text, phone_number):
    messages = load_session(phone_number)
    messages.append({"role": "user", "content": user_text})

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
        "messages": [{"role": "system", "content": system_prompt}] + messages
    }

    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers={
                                 "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                 "Content-Type": "application/json"
                             },
                             json=payload, timeout=30)
        resp.raise_for_status()
        bot_reply = resp.json()["choices"][0]["message"]["content"].strip()
        messages.append({"role": "assistant", "content": bot_reply})
        save_session(phone_number, messages)
        return bot_reply
    except Exception as e:
        logging.error(f"❌ OpenRouter failed with: {e}")
        return "⚠️ I'm currently unable to respond. Please try again later.\n\n" + DISCLAIMER

# 🧠 Generate summary from memory
def generate_summary(phone_number):
    messages = load_session(phone_number)
    if not messages:
        return "🧠 No memory to summarize yet."

    conversation_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
    summary_prompt = (
        "You are a multilingual health assistant. Summarize the following conversation between a user and assistant. "
        "Focus on health concerns, advice given, and any follow-up suggestions. Keep it empathetic, clear, and concise. "
        "Do not include unrelated content or hallucinate. End with a reminder to seek professional care if symptoms persist.\n\n"
        f"Conversation:\n{conversation_text}"
    )

    payload = {
        "model": "deepseek/deepseek-chat-v3.1:free",
        "temperature": 0.5,
        "messages": [{"role": "system", "content": summary_prompt}]
    }

    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers={
                                 "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                 "Content-Type": "application/json"
                             },
                             json=payload, timeout=30)
        resp.raise_for_status()
        return "🧠 Summary:\n" + resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"Summary error: {e}")
        return "⚠️ Couldn't generate summary. Try again later."

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
        "text": {"body": message_text}
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
                    contact_name = contacts[0].get("profile", {}).get("name", "Unknown") if contacts else "Unknown"

                    for message in messages:
                        message_text = message.get("text", {}).get("body", "")
                        if message_text and phone_number:
                            logging.info(f"👤 Name: {contact_name}")
                            logging.info(f"📱 Phone: {phone_number}")
                            logging.info(f"💬 Message: {message_text}")

                            # 🧹 Clear memory
                            if message_text.lower().strip() == "/reset":
                                clear_session(phone_number)
                                send_whatsapp_message(phone_number, "🧹 Memory cleared. Let's start fresh!")
                                continue

                            # 🧪 Debug memory
                            elif message_text.lower().strip() == "/debug":
                                history = load_session(phone_number)
                                if not history:
                                    reply = "🧪 No memory found for this session."
                                else:
                                    formatted = "\n".join([f"{m['role']}: {m['content']}" for m in history])
                                    reply = f"🧠 Current memory:\n{formatted}"
                                send_whatsapp_message(phone_number, reply)
                                continue

                            # 🧠 Summarize memory
                            elif message_text.lower().strip() == "/summary":
                                reply = generate_summary(phone_number)
                                send_whatsapp_message(phone_number, reply)
                                continue

                            # 🩺 Symptom checker
                            elif message_text.lower().startswith("check:"):
                                raw = message_text.split("check:", 1)[1]
                                symptoms = [s.strip().lower() for s in raw.split(",")]

                                # Load known symptoms from your model or dataset
                                known_symptoms = training_columns
                                valid_symptoms = [s for s in symptoms if s in known_symptoms]
                                unknown_symptoms = [s for s in symptoms if s not in known_symptoms]
                                logging.info(f"User symptoms: {symptoms}")
                                logging.info(f"Known symptoms: {known_symptoms}")
                                logging.info(f"Valid symptoms: {valid_symptoms}")
                                logging.info(f"Unknown symptoms: {unknown_symptoms}")
                                # Save initial symptoms to memory
                                history = load_session(phone_number)
                                history.append({"role": "user", "content": f"Symptoms: {', '.join(symptoms)}"})
                                save_session(phone_number, history)

                                # Handle unknown symptoms via fallback
                                if unknown_symptoms:
                                    fallback = call_openrouter(f"User mentioned unknown symptoms: {', '.join(unknown_symptoms)}. Suggest related conditions or follow-up questions.", phone_number)
                                    send_whatsapp_message(phone_number, f"🤖 I didn’t recognize: {', '.join(unknown_symptoms)}.\nHere's what I found:\n{fallback}")
                                    continue

                                # Ask AI-generated follow-up questions if input is sparse
                                if len(valid_symptoms) < 3:
                                    follow_ups = generate_followups(valid_symptoms, phone_number)
                                    send_whatsapp_message(phone_number, "🩺 To help me be more accurate, could you answer these:\n" + follow_ups)
                                    continue

                                # Run prediction
                                result = predict_disease(valid_symptoms, days=2)
                                if "error" in result:
                                    reply = f"⚠️ {result['error']}"
                                else:
                                    reply = (
                                        f"🩺 You may have: {result['disease']}\n"
                                        f"📖 Description: {result['description']}\n"
                                        f"⚠️ Severity: {result['severity']}\n"
                                        f"✅ Precautions:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(result['precautions'])])
                                    )
                                send_whatsapp_message(phone_number, reply)
                                continue
                                                        # 🔍 Check for predefined reply
                            reply = match_predefined(message_text)
                            if not reply:
                                reply = call_openrouter(message_text, phone_number)

                            send_whatsapp_message(phone_number, reply)

    return Response("EVENT_RECEIVED", status=200)

# 🏠 Health check route
@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

# 🚀 Run the app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))