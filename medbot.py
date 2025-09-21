import os
import json
import requests
import logging
import re
from flask import Flask, request, Response
from flask_sqlalchemy import SQLAlchemy
from predictor import predict_disease, cols

try:
    from predictor import suggest_symptoms
except ImportError:
    from difflib import get_close_matches

    def suggest_symptoms(partial: str, n: int = 5):
        """Fallback fuzzy symptom search if predictor doesn't define suggest_symptoms"""
        partial = partial.strip().lower().replace(" ", "_")
        matches = get_close_matches(partial, [s.lower() for s in cols], n=n, cutoff=0.4)
        return matches



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
    followup_symptoms = db.Column(db.Text, default="[]")  # JSON list

with app.app_context():
    db.create_all()

# 💬 Predefined responses
DISCLAIMER = (
    "Note: This assistant provides general health information only and is not a substitute "
    "for professional medical advice. If you think you may be experiencing a medical emergency, seek immediate care."
)

PREDEFINED_RESPONSES = {
    "hi": "👋 Hello! I'm Botcure, your personal health assistant. How can I support you today? Type 'help' to know my features.",
    "hello": "Hi there! 😊 I'm Botcure. Feel free to ask about wellness, safety, or self-care.",
    "thanks": "You're welcome! 🙏 Stay safe and take care.",
    "bye": "Goodbye! 👋 Wishing you good health and happiness.",
    "who are you": "I'm Botcure — a cautious, multilingual health assistant here to guide you with wellness tips and safety advice.",
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
    "check": "🩺 To check symptoms, type 'check'. I will guide you interactively to add symptoms and refine accuracy.",
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

def get_or_create_session(phone_number):
    session = load_session(phone_number)
    if not session:
        session = UserSession(phone_number=phone_number)
        save_session(session)
    return session

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
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json={
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "interactive",
            "interactive": interactive_payload
        })
        logging.info(f"WhatsApp API status: {resp.status_code}, response: {resp.text}")
        if resp.status_code == 200:
            log_interaction(to_number, bot_message=f"[Interactive] {json.dumps(interactive_payload)}",
                            session_state=load_session(to_number).state)
    except Exception as e:
        logging.error(f"Interactive send error: {e}")

# 🔹 Start symptom checker (search-based)
def start_symptom_checker(phone_number):
    session = get_or_create_session(phone_number)
    session.state = "symptom_check"
    session.selected_symptoms = json.dumps([])
    save_session(session)

    send_whatsapp_message(
        phone_number,
        "🩺 Please type your symptom (e.g. 'headache', 'cough'). "
        "I'll suggest matches. Type 'finish' when done."
    )

def handle_symptom_search(phone_number, text):
    session = load_session(phone_number)
    if not session:
        return

    if text == "finish":
        finish_symptom_check(phone_number)
        return

    selected_symptoms = json.loads(session.selected_symptoms)

    # Find matches from dataset
    matches = [s for s in cols if text in s.lower()]

    if not matches:
        send_whatsapp_message(phone_number, f"⚠️ No matching symptoms found for '{text}'. Try again.")
        return

    # Build interactive list of up to 10 matches
    rows = [{"id": f"symptom_{s}", "title": s.replace("_", " ").title()[:24]} for s in matches[:10]]
    rows.append({"id": "finish", "title": "✅ Finish"})
    interactive = {
        "type": "list",
        "body": {"text": "🔍 Did you mean one of these symptoms?"},
        "action": {"button": "Select", "sections": [{"title": "Suggestions", "rows": rows}]}
    }
    send_whatsapp_interactive(phone_number, interactive)

def handle_symptom_input(phone_number, user_text):
    session = load_session(phone_number)
    if not session:
        return

    user_text = user_text.lower().strip()
    if user_text == "done":
        finish_symptom_check(phone_number)
        return

    matches = suggest_symptoms(user_text, n=5)
    if not matches:
        send_whatsapp_message(phone_number, "⚠️ No matches found. Try again with a different word.")
        return

    # If exact match, auto-add
    selected = json.loads(session.selected_symptoms)
    if user_text in matches and user_text not in selected:
        selected.append(user_text)
        session.selected_symptoms = json.dumps(selected)
        save_session(session)
        send_whatsapp_message(phone_number, f"✅ Added '{user_text}'. Type another symptom or 'done' to finish.")
        return

    # Otherwise show suggestions
    buttons = [{"type": "reply", "reply": {"id": f"symptom_{m}", "title": m.title()}} for m in matches]
    interactive = {
        "type": "button",
        "body": {"text": f"Did you mean one of these?"},
        "action": {"buttons": buttons}
    }
    send_whatsapp_interactive(phone_number, interactive)

def handle_symptom_selection(phone_number, selection_id):
    session = load_session(phone_number)
    if not session:
        return

    selected_symptoms = json.loads(session.selected_symptoms)

    # Finish check
    if selection_id == "finish":
        finish_symptom_check(phone_number)
        return

    # Add selected symptom
    symptom_name = selection_id.replace("symptom_", "").replace("_", " ").lower()
    if symptom_name not in selected_symptoms:
        selected_symptoms.append(symptom_name)

    session.selected_symptoms = json.dumps(selected_symptoms)
    save_session(session)

    interactive = {
        "type": "button",
        "body": {"text": f"✅ Added: {symptom_name.title()}. Add more or finish?"},
        "action": {"buttons": [
            {"type": "reply", "reply": {"id": "add_more", "title": "Add More"}},
            {"type": "reply", "reply": {"id": "finish", "title": "Finish"}}
        ]}
    }
    send_whatsapp_interactive(phone_number, interactive)

# 🔹 Follow-up questions
def finish_symptom_check(phone_number):
    session = load_session(phone_number)
    if not session:
        return
    symptoms = json.loads(session.selected_symptoms)
    if not symptoms:
        send_whatsapp_message(phone_number, "⚠️ You didn't add any symptoms. Please try again.")
        return

    result = predict_disease(symptoms, days=3)
    if "error" in result:
        send_whatsapp_message(phone_number, result["error"])
        return

    if "followup" in result and result["followup"]:
        # Save followups as a queue so we don’t repeat
        session.state = "followup_check"
        session.followup_symptoms = json.dumps(result["followup"])
        save_session(session)
        ask_next_followup(phone_number)
    else:
        send_diagnosis(phone_number, result)
        session.state = "idle"
        session.selected_symptoms = json.dumps([])
        session.followup_symptoms = json.dumps([])  # reset followups
        save_session(session)

def ask_next_followup(phone_number):
    session = load_session(phone_number)
    followups = json.loads(session.followup_symptoms)
    if not followups:
        # No more followups → finalize
        symptoms = json.loads(session.selected_symptoms)
        result = predict_disease(symptoms, days=3)
        send_diagnosis(phone_number, result)
        session.state = "idle"
        session.selected_symptoms = json.dumps([])
        session.followup_symptoms = json.dumps([])
        save_session(session)
        return

    next_symptom = followups.pop(0)  # ✅ remove so we don’t loop
    session.followup_symptoms = json.dumps(followups)
    save_session(session)

    buttons = [
        {"type": "reply", "reply": {"id": f"followup_yes_{next_symptom}", "title": "Yes"}},
        {"type": "reply", "reply": {"id": f"followup_no_{next_symptom}", "title": "No"}}
    ]
    interactive = {
        "type": "button",
        "body": {"text": f"Do you also have: {next_symptom.replace('_',' ').title()}?"},
        "action": {"buttons": buttons}
    }
    send_whatsapp_interactive(phone_number, interactive)

def handle_followup_response(phone_number, selection_id):
    session = load_session(phone_number)
    if not session:
        return

    symptoms = json.loads(session.selected_symptoms)

    if selection_id.startswith("followup_yes_"):
        symptom = selection_id.replace("followup_yes_", "")
        if symptom not in symptoms:
            symptoms.append(symptom)
            session.selected_symptoms = json.dumps(symptoms)

    # No need to handle “no” → just skip
    save_session(session)
    ask_next_followup(phone_number)

def send_diagnosis(phone_number, result):
    msg = (
        f"🤖 Based on your symptoms, you may have: *{result['disease']}* "
        f"(confidence: {result['confidence']}%).\n\n"
        f"📖 {result['description']}\n\n"
        f"⚠️ Severity: {result['severity'].title()}\n\n"
        f"💡 Precautions:\n" + "\n".join(f"- {p}" for p in result['precautions'])
    )
    send_whatsapp_message(phone_number, msg)
    send_whatsapp_message(phone_number, f"⚠️ {DISCLAIMER}")

# 🔹 OpenRouter fallback
def call_openrouter(user_text, phone_number):
    session = load_session(phone_number)
    messages = json.loads(session.history) if session and session.history else []
    messages.append({"role": "user", "content": user_text})

    system_prompt = (
        "You are Botcure — a cautious, empathetic health assistant designed to support general wellness. "
        "Be multilingual, empathetic, clear, and culturally sensitive. "
        "Avoid diagnosing or prescribing. Advise emergency care for red-flag symptoms. "
        f"Always identify yourself as Botcure. "
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
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
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
                interactive_id = None
                if "interactive" in message:
                    interactive = message["interactive"]
                    if interactive["type"] == "button_reply":
                        interactive_id = interactive["button_reply"]["id"]
                    elif interactive["type"] == "list_reply":
                        interactive_id = interactive["list_reply"]["id"]

                log_interaction(phone_number, user_message=text, session_state=session.state)

                # Commands
                if text == "/reset":
                    clear_session(phone_number)
                    send_whatsapp_message(phone_number, "🧹 Memory cleared. Let's start fresh!")
                    continue
                elif text == "/debug":
                    history = json.loads(session.history) if session.history else []
                    reply = "🧪 Current memory:\n" + "\n".join(
                        [f"{m['role']}: {m['content']}" for m in history]
                    ) if history else "🧪 No memory found."
                    send_whatsapp_message(phone_number, reply)
                    continue

                # Start symptom checker
                if text == "check":
                    start_symptom_checker(phone_number)
                    continue

                # Handle symptom checker flow
                if session.state == "symptom_check":
                    if interactive_id:
                        if interactive_id.startswith("symptom_") or interactive_id == "finish":
                            handle_symptom_selection(phone_number, interactive_id)
                        elif interactive_id == "add_more":
                            send_whatsapp_message(phone_number, "🩺 Please type another symptom:")
                        continue
                    elif text and text != "check":  # user typed a symptom
                        handle_symptom_search(phone_number, text)
                        continue

                # Handle follow-up flow ✅ patched
                if session.state == "followup_check" and interactive_id:
                    if interactive_id.startswith("followup_yes_") or interactive_id.startswith("followup_no_"):
                        handle_followup_response(phone_number, interactive_id)
                        continue

                # Predefined replies
                reply = match_predefined(text)
                if reply:
                    send_whatsapp_message(phone_number, reply)
                    continue
                else:
                    # Only call LLM if no predefined response matched
                    if text:
                        reply = call_openrouter(text, phone_number)
                        send_whatsapp_message(phone_number, reply)
                        continue

    return Response("EVENT_RECEIVED", status=200)


@app.route('/')
def home():
    return "✅ Botcure Medical Chatbot is running!"

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
