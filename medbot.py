"""
Upgraded medbot.py

- Preserves all original functionality:
  - predefined responses, commands (/reset, /debug, /summary)
  - OpenRouter integration fallback for general chat
  - WhatsApp send via Graph API
  - SQLite-backed session memory

- Adds:
  - Confidence-based symptom predictions with follow-up questions
  - State stored in session to handle follow-up answers
  - Improved logging, memory size limit, safer error handling
"""

import os
import json
import logging
import re
import requests
from flask import Flask, request, Response
from flask_sqlalchemy import SQLAlchemy
from predictor import predict_disease

# --- App & Logging ---
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

# --- Environment / secrets ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "Shivang")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

if not OPENROUTER_API_KEY or not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
    logging.warning("One or more environment variables missing (OPENROUTER_API_KEY, WHATSAPP_TOKEN, PHONE_NUMBER_ID). "
                    "Ensure they are set in production.")

# --- Database (sessions) ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sessions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

MAX_HISTORY = 20  # keep last N items in memory

class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(40), unique=True, nullable=False)
    history = db.Column(db.Text)  # JSON list of dicts

with app.app_context():
    db.create_all()

# --- Predefined responses & helpers ---
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
        "- 'check:' → Run a health check based on symptoms (e.g. check: fever, cough)\n"
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
        "'check: symptom1, symptom2, fatigue'\n"
        "I'll analyze your symptoms and suggest possible conditions, precautions, and severity."
    ),
}

def match_predefined(text):
    text = (text or "").lower().strip()
    if re.search(r"\b(hi|hello|hey)\b", text):
        return PREDEFINED_RESPONSES["hi"]
    if re.search(r"\b(thanks|thank you)\b", text):
        return PREDEFINED_RESPONSES["thanks"]
    if re.search(r"\b(bye|goodbye)\b", text):
        return PREDEFINED_RESPONSES["bye"]
    if re.search(r"\b(who are you|your name)\b", text):
        return PREDEFINED_RESPONSES["who are you"]
    if re.search(r"\b(help|support)\b", text):
        return PREDEFINED_RESPONSES["help"]
    if re.search(r"\b(command|commands)\b", text):
        return PREDEFINED_RESPONSES["command"]
    if re.search(r"\b(languages|language)\b", text):
        return PREDEFINED_RESPONSES["languages"]
    if re.search(r"\b(resources|resource|info|information)\b", text):
        return PREDEFINED_RESPONSES["resources"]
    if re.search(r"\b(emergency|urgent)\b", text):
        return PREDEFINED_RESPONSES["emergency"]
    if re.search(r"\b(check:|check symptom|symptom|check symptoms)\b", text):
        return PREDEFINED_RESPONSES["check"]
    return None

# --- Session helpers ---
def load_session(phone_number):
    session = UserSession.query.filter_by(phone_number=phone_number).first()
    if session:
        try:
            return json.loads(session.history or "[]")
        except Exception:
            return []
    return []

def save_session(phone_number, messages):
    # trim history to MAX_HISTORY
    messages = messages[-MAX_HISTORY:]
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

# --- OpenRouter chat fallback ---
def call_openrouter(user_text, phone_number):
    # load conversation context (simple)
    messages = load_session(phone_number)
    # convert saved history into message format for model
    # we'll send the user's recent messages and a system prompt
    recent = [m for m in messages if m.get("role") in ("user", "assistant")]
    system_prompt = (
        "You are a cautious, empathetic health assistant designed to support general wellness. "
        "Always reply in user's language. Be empathetic, clear, culturally sensitive. "
        "Avoid diagnosing, prescribing, or making clinical decisions. If symptoms are severe, advise to seek immediate care.\n\n"
        f"Always end with this disclaimer:\n{DISCLAIMER}"
    )
    payload = {
        "model": "deepseek/deepseek-chat-v3.1:free",
        "temperature": 0.7,
        "messages": [{"role": "system", "content": system_prompt}] + recent + [{"role": "user", "content": user_text}]
    }
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        bot_reply = resp.json()["choices"][0]["message"]["content"].strip()
        # append to session
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": bot_reply})
        save_session(phone_number, messages)
        return bot_reply
    except Exception as e:
        logging.error(f"OpenRouter error: {e}")
        return "⚠️ I'm currently unable to respond via the AI assistant. Please try again later.\n\n" + DISCLAIMER

# --- Summary via OpenRouter ---
def generate_summary(phone_number):
    messages = load_session(phone_number)
    if not messages:
        return "🧠 No memory to summarize yet."
    conversation_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages if m.get("role") in ("user", "assistant")])
    summary_prompt = (
        "Summarize the following conversation between a user and assistant. "
        "Focus on health concerns, advice given, and any follow-up suggestions. Keep it empathetic and concise.\n\n"
        f"Conversation:\n{conversation_text}"
    )
    payload = {
        "model": "deepseek/deepseek-chat-v3.1:free",
        "temperature": 0.5,
        "messages": [{"role": "system", "content": summary_prompt}]
    }
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return "🧠 Summary:\n" + resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"Summary error: {e}")
        return "⚠️ Couldn't generate summary. Try again later."

# --- WhatsApp sending ---
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
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code >= 400:
            logging.error(f"WhatsApp API error: {resp.status_code} {resp.text}")
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

# --- Webhook message handler ---
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(silent=True) or {}
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

                        # load history
                        history = load_session(phone_number)

                        # handle commands first
                        lowered = message_text.lower().strip()
                        if lowered == "/reset":
                            clear_session(phone_number)
                            send_whatsapp_message(phone_number, "🧹 Memory cleared. Let's start fresh!")
                            continue
                        if lowered == "/debug":
                            history_dump = load_session(phone_number)
                            if not history_dump:
                                send_whatsapp_message(phone_number, "🧪 No memory found for this session.")
                            else:
                                formatted = "\n".join([f"{m.get('role')}: {m.get('content')}" for m in history_dump])
                                send_whatsapp_message(phone_number, f"🧠 Current memory:\n{formatted}")
                            continue
                        if lowered == "/summary":
                            summary = generate_summary(phone_number)
                            send_whatsapp_message(phone_number, summary)
                            continue

                        # If last entry in history is a followup state -> treat this message as follow-up answer
                        if history and history[-1].get("role") == "followup":
                            follow_state = history[-1]
                            prev_symptoms = follow_state.get("symptoms", [])
                            # user may reply "yes", "no", or list symptoms; we'll accept comma-separated symptom additions
                            # if they reply 'no' or 'none', we'll re-run with same symptoms but return top predictions
                            lower_msg = message_text.lower().strip()
                            if lower_msg in ("no", "none", "not sure", "nope"):
                                all_symptoms = prev_symptoms
                            else:
                                # parse additional symptoms (comma separated)
                                new_symptoms = [s.strip() for s in re.split(r",|\n", message_text) if s.strip()]
                                all_symptoms = prev_symptoms + new_symptoms

                            result = predict_disease(all_symptoms, days=2)
                            # remove followup state
                            history.pop()
                            # append conversation pieces for memory
                            history.append({"role": "user", "content": message_text})
                            if "error" in result:
                                reply = f"⚠️ {result['error']}"
                            elif "follow_up" in result:
                                # still low confidence: ask next follow-up
                                reply = (
                                    "🧪 I still need a bit more info to be accurate.\n"
                                    "Are you also experiencing any of these symptoms?\n" +
                                    "\n".join([f"- {s}" for s in result["follow_up"]])
                                )
                                history.append({"role": "followup", "symptoms": all_symptoms, "options": result.get("follow_up", [])})
                            else:
                                reply = (
                                    f"🩺 You may have: {result['disease']} ({result['confidence']}% confidence)\n"
                                    f"📖 Description: {result['description']}\n"
                                    f"⚠️ Severity: {result['severity']}\n"
                                    f"✅ Precautions:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(result['precautions'])]) +
                                    "\n\n" + DISCLAIMER
                                )
                                # record assistant reply
                                history.append({"role": "assistant", "content": reply})

                            save_session(phone_number, history)
                            send_whatsapp_message(phone_number, reply)
                            continue

                        # Symptom check flow
                        if lowered.startswith("check:"):
                            raw = message_text.split("check:", 1)[1]
                            symptoms = [s.strip() for s in re.split(r",|\n", raw) if s.strip()]
                            # store user input
                            history.append({"role": "user", "content": message_text})
                            result = predict_disease(symptoms, days=2)

                            if "error" in result:
                                reply = f"⚠️ {result['error']}"
                                history.append({"role": "assistant", "content": reply})
                                save_session(phone_number, history)
                                send_whatsapp_message(phone_number, reply)
                                continue

                            if "follow_up" in result:
                                # Ask follow-up question and store state
                                reply = (
                                    "🧪 I need a bit more info to be accurate.\n"
                                    "Are you also experiencing any of these symptoms?\n" +
                                    "\n".join([f"- {s}" for s in result["follow_up"]])
                                )
                                history.append({"role": "followup", "symptoms": symptoms, "options": result.get("follow_up", [])})
                                history.append({"role": "assistant", "content": reply})
                                save_session(phone_number, history)
                                send_whatsapp_message(phone_number, reply)
                                continue

                            # direct result
                            reply = (
                                f"🩺 You may have: {result['disease']} ({result['confidence']}% confidence)\n"
                                f"📖 Description: {result['description']}\n"
                                f"⚠️ Severity: {result['severity']}\n"
                                f"✅ Precautions:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(result['precautions'])]) +
                                "\n\n" + DISCLAIMER
                            )
                            history.append({"role": "assistant", "content": reply})
                            save_session(phone_number, history)
                            send_whatsapp_message(phone_number, reply)
                            continue

                        # Predefined reply check
                        predefined = match_predefined(message_text)
                        if predefined:
                            history.append({"role": "user", "content": message_text})
                            history.append({"role": "assistant", "content": predefined})
                            save_session(phone_number, history)
                            send_whatsapp_message(phone_number, predefined)
                            continue

                        # Fallback: send to OpenRouter for general chat
                        reply = call_openrouter(message_text, phone_number)
                        # message & reply saved inside call_openrouter
                        send_whatsapp_message(phone_number, reply)

    return Response("EVENT_RECEIVED", status=200)

# Simple health check route
@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
