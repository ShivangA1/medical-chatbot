import os
import json
import requests
import logging
import re
from flask import Flask, request, Response
from flask_sqlalchemy import SQLAlchemy
from predictor import predict_disease, cols  # upgraded predictor with follow-ups

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 🔐 Environment variables
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
    history = db.Column(db.Text, default="[]")  # JSON string
    state = db.Column(db.String(50), default="idle")
    selected_symptoms = db.Column(db.Text, default="[]")  # JSON list
    current_page = db.Column(db.Integer, default=0)

with app.app_context():
    db.create_all()

# 💬 Predefined responses
DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, seek immediate care."
)

PREDEFINED_RESPONSES = {
    "hi": "👋 Hello! I'm your health assistant. How can I support you today? Type 'help' to know my features.",
    "hello": "Hi there! 😊 Feel free to ask about wellness, safety, or self-care.",
    "thanks": "You're welcome! 🙏 Stay safe and take care.",
    "bye": "Goodbye! 👋 Wishing you good health and happiness.",
    "who are you": "I'm a cautious, multilingual health assistant here to guide you with wellness tips and safety advice.",
    "help": "You can ask me about symptoms, healthy habits, or how to stay safe. Type 'command' to see available services.",
    "command": (
        "📋 Available commands:\n"
        "- '/reset' → Clear your memory and start fresh\n"
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
        "🩺 To check symptoms, type 'check'. I will guide you interactively to select symptoms for more accurate results."
    ),
}

def match_predefined(text):
    text = text.lower().strip()
    for key in PREDEFINED_RESPONSES:
        if re.search(rf"\b{key}\b", text):
            return PREDEFINED_RESPONSES[key]
    return None

# 🧠 Session functions
def load_session(phone_number):
    return UserSession.query.filter_by(phone_number=phone_number).first()

def save_session(session):
    db.session.add(session)
    db.session.commit()

def clear_session(phone_number):
    session = load_session(phone_number)
    if session:
        db.session.delete(session)
        db.session.commit()


def log_interaction(phone_number, user_message=None, bot_message=None, session_state=None):
    log_parts = [f"📞 Phone: {phone_number}"]
    if user_message:
        log_parts.append(f"👤 User: {user_message}")
    if bot_message:
        log_parts.append(f"🤖 Bot: {bot_message}")
    if session_state:
        log_parts.append(f"⚙️ State: {session_state}")
    logging.info(" | ".join(log_parts))


# 📤 WhatsApp message functions
def send_whatsapp_message(to_number, message_text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": message_text}}
    try:
        requests.post(url, headers=headers, json=payload)
        log_interaction(to_number, bot_message=message_text, session_state=load_session(to_number).state)
    except Exception as e:
        logging.error(f"WhatsApp send error: {e}")

def send_whatsapp_interactive(to_number, interactive_payload):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Send request and capture response
    try:
        resp = requests.post(url, headers=headers, json={
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "interactive",
            "interactive": interactive_payload
        })
        logging.info(f"WhatsApp API status: {resp.status_code}, response: {resp.text}")
        if resp.status_code != 200:
            logging.error(f"Failed to send interactive message: {resp.text}")
        else:
            log_interaction(
                to_number,
                bot_message=f"[Interactive Message] {json.dumps(interactive_payload)}",
                session_state=load_session(to_number).state
            )
    except Exception as e:
        logging.error(f"Interactive send error: {e}")

# 🧠 Symptom pagination helpers
def get_symptom_page(page=0, page_size=9):  # max 9 + 1 "Next Page"
    start = page * page_size
    end = start + page_size
    page_symptoms = cols[start:end]

    # Truncate titles to 24 characters (WhatsApp requirement)
    rows = [{
        "id": f"symptom_{s.lower()}",
        "title": s.replace("_", " ").title()[:24]
    } for s in page_symptoms]

    # Add "Next Page" button if more symptoms remain
    if end < len(cols):
        rows.append({"id": "next_page", "title": "➡ Next Page"})
    
    return rows


# 🔹 Start symptom checker reliably
def start_symptom_checker(phone_number):
    session = get_or_create_session(phone_number)
    session.state = "symptom_check"
    session.selected_symptoms = json.dumps([])
    session.current_page = 0
    save_session(session)

    rows = get_symptom_page(page=0)
    interactive = {
        "type": "list",
        "body": {"text": "🩺 Select your symptom:"},
        "footer": {"text": "You can select one symptom per message."},
        "action": {"button": "Symptoms", "sections": [{"title": "Symptoms", "rows": rows}]}
    }
    logging.info(f"Sending symptom list to {phone_number}")
    send_whatsapp_interactive(phone_number, interactive)

def handle_symptom_selection(phone_number, selection_id):
    session = load_session(phone_number)
    if not session:
        return

    selected_symptoms = json.loads(session.selected_symptoms)

    # User clicked "Finish" → exit loop
    if selection_id == "finish":
        session.state = "idle"
        session.selected_symptoms = json.dumps([])
        session.current_page = 0
        save_session(session)
        send_whatsapp_message(phone_number, "✅ Symptom check finished. Stay safe!")
        return

    # User clicked "Next Page" → paginate symptoms
    if selection_id == "next_page":
        session.current_page += 1
        save_session(session)
        rows = get_symptom_page(page=session.current_page)
        interactive = {
            "type": "list",
            "body": {"text": "🩺 Select your symptom:"},
            "footer": {"text": "You can select one symptom per message."},
            "action": {"button": "Symptoms", "sections": [{"title": "Symptoms", "rows": rows}]}
        }
        send_whatsapp_interactive(phone_number, interactive)
        return

    # Add selected symptom
    symptom_name = selection_id.replace("symptom_", "").replace("_", " ").lower()
    if symptom_name not in selected_symptoms:
        selected_symptoms.append(symptom_name)

    session.selected_symptoms = json.dumps(selected_symptoms)
    save_session(session)

    # Send "Add more or Finish" buttons
    interactive = {
        "type": "button",
        "body": {"text": f"✅ Added: {symptom_name.title()}. Add more or finish?"},
        "action": {"buttons": [
            {"type": "reply", "reply": {"id": "add_more", "title": "Add More"}},
            {"type": "reply", "reply": {"id": "finish", "title": "Finish"}}
        ]}
    }
    send_whatsapp_interactive(phone_number, interactive)



# 🧠 Session helper
def get_or_create_session(phone_number):
    session = load_session(phone_number)
    if not session:
        session = UserSession(phone_number=phone_number)
        save_session(session)
    return session

def finish_symptom_check(phone_number):
    session = load_session(phone_number)
    if not session:
        return

    symptoms = json.loads(session.selected_symptoms)
    result = predict_disease(symptoms, days=2)

    if "error" in result:
        send_whatsapp_message(phone_number, f"⚠️ {result['error']}")
        session.state = "idle"
        session.selected_symptoms = json.dumps([])
        session.current_page = 0
        save_session(session)
        return

    # Build main result text
    reply = (
        f"🩺 Based on the symptoms you provided, you may have: {result['disease']} "
        f"({result.get('confidence','N/A')}% confidence)\n"
        f"📖 Description: {result['description']}\n"
        f"⚠️ Severity: {result['severity']}\n"
        f"✅ Precautions:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(result['precautions'])])
    )

    # Add confidence/warning notes
    if result.get('confidence', 0) < 70:
        reply += "\n\n⚠️ Confidence is low. Consider adding more symptoms."
    if len(symptoms) < 3:
        reply += "\n\n⚠️ Very few symptoms provided. Accuracy may be limited."

    reply += f"\n\n{DISCLAIMER}"
    send_whatsapp_message(phone_number, reply)

    # Handle follow-up symptoms (if any)
    if "followup" in result and result["followup"]:
        rows = [{"id": f"symptom_{s}", "title": s.replace('_', ' ').title()} for s in result["followup"]]
        interactive_payload = {
            "type": "list",
            "body": {"text": "🤔 To improve accuracy, can you tell me if you also have any of these symptoms?"},
            "footer": {"text": "Select a symptom or click 'Finish' if none apply."},
            "action": {"button": "Select Symptom", "sections": [{"title": "Follow-Up Symptoms", "rows": rows + [{"id": "finish", "title": "✅ Finish"}]}]}
        }
        send_whatsapp_interactive(phone_number, interactive_payload)
        session.state = "symptom_check"  # stay in selection mode for follow-ups
        save_session(session)
    else:
        # No follow-ups → reset session completely
        session.state = "idle"
        session.selected_symptoms = json.dumps([])
        session.current_page = 0
        save_session(session)





# 🔹 OpenRouter Fallback
def call_openrouter(user_text, phone_number):
    session = load_session(phone_number)
    messages = json.loads(session.history) if session and session.history else []
    messages.append({"role": "user", "content": user_text})

    system_prompt = (
        "You are a cautious, empathetic health assistant designed to support general wellness. "
        "Be multilingual, empathetic, clear, and culturally sensitive. "
        "Avoid diagnosing or prescribing. Advise emergency care for red-flag symptoms. "
        f"End with this disclaimer:\n{DISCLAIMER}"
    )

    payload = {
        "model": "deepseek/deepseek-chat-v3.1:free",
        "temperature": 0.7,
        "messages": [{"role": "system", "content": system_prompt}] + messages
    }

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        bot_reply = resp.json()["choices"][0]["message"]["content"].strip()
        if session:
            messages.append({"role": "assistant", "content": bot_reply})
            session.history = json.dumps(messages)
            save_session(session)
        return bot_reply
    except Exception as e:
        logging.error(f"❌ OpenRouter failed: {e}")
        return "⚠️ I'm currently unable to respond. Please try again later.\n\n" + DISCLAIMER

# 🌐 Webhook
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(challenge, status=200)
    return Response("Verification failed", status=403)

# 🌐 Webhook POST
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    logging.info(f"Webhook received: {json.dumps(data)}")
    if data.get("object") != "whatsapp_business_account":
        return Response("EVENT_RECEIVED", status=200)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if "messages" not in value:
                continue
            messages = value["messages"]
            contacts = value.get("contacts", [])
            phone_number = contacts[0]["wa_id"] if contacts else None
            if not phone_number:
                continue

            session = get_or_create_session(phone_number)

            for message in messages:
                text = message.get("text", {}).get("body", "").strip().lower()
                logging.info(f"Incoming message from {phone_number}: {text}")
                
                interactive_id = None
                if "interactive" in message:
                    interactive = message["interactive"]
                    if interactive["type"] == "button_reply":
                        interactive_id = interactive["button_reply"]["id"]
                    elif interactive["type"] == "list_reply":
                        interactive_id = interactive["list_reply"]["id"]
                # Log user message
                log_interaction(phone_number, user_message=text, session_state=session.state)

                # Commands
                if text == "/reset":
                    clear_session(phone_number)
                    send_whatsapp_message(phone_number, "🧹 Memory cleared. Let's start fresh!")
                    continue
                elif text == "/debug":
                    history = json.loads(session.history) if session.history else []
                    reply = "🧪 Current memory:\n" + "\n".join([f"{m['role']}: {m['content']}" for m in history]) if history else "🧪 No memory found."
                    send_whatsapp_message(phone_number, reply)
                    continue
                

                 # Start symptom check
                if text == "check":
                    start_symptom_checker(phone_number)
                    continue

                # Handle interactive replies
                if interactive_id:
                    if interactive_id.startswith("symptom_") or interactive_id == "next_page":
                        handle_symptom_selection(phone_number, interactive_id)
                    elif interactive_id == "add_more":
                        rows = get_symptom_page(page=session.current_page)
                        interactive_payload = {
                            "type": "list",
                            "body": {"text": "🩺 Select your symptom:"},
                            "footer": {"text": "You can select one symptom per message."},
                            "action": {"button": "Symptoms", "sections": [{"title": "Symptoms", "rows": rows}]}
                        }
                        send_whatsapp_interactive(phone_number, interactive_payload)
                    elif interactive_id == "finish":
                        finish_symptom_check(phone_number)
                    continue

                # Predefined responses
                reply = match_predefined(text)
                if reply:
                    send_whatsapp_message(phone_number, reply)
                    continue

                # OpenRouter fallback
                reply = call_openrouter(text, phone_number)
                send_whatsapp_message(phone_number, reply)

    return Response("EVENT_RECEIVED", status=200)

@app.route('/')
def home():
    return "✅ Medical Chatbot is running!"

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
