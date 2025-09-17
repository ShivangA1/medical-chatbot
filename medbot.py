import os
import json
import requests
import logging
import re
from flask import Flask, request, Response
from flask_sqlalchemy import SQLAlchemy
from predictor import predict_disease, cols
import difflib

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
    "hi": "👋 Hello! I'm Cura.ai, your personal health assistant. How can I support you today? Type 'help' to know my features.",
    "hello": "Hi there! 😊 I'm Cura.ai. Feel free to ask about wellness, safety, or self-care.",
    "thanks": "You're welcome! 🙏 Stay safe and take care.",
    "bye": "Goodbye! 👋 Wishing you good health and happiness.",
    "who are you": "I'm Cura.ai — a cautious, multilingual health assistant here to guide you with wellness tips and safety advice.",
    "help": "You can ask me about symptoms, healthy habits, or how to stay safe. Type 'command' to see available services.",
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
        "🩺 To check symptoms, type 'check'. I will guide you interactively to select symptoms for more accurate results."
    ),
}

def match_predefined(text):
    text = text.lower().strip()
    for key in PREDEFINED_RESPONSES:
        if re.search(rf"\b{key}\b", text):
            return PREDEFINED_RESPONSES[key]
    return None

# --------------------
# Session functions
# --------------------
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

# --------------------
# WhatsApp helpers
# --------------------
def send_whatsapp_message(to_number, message_text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": message_text}}
    try:
        resp = requests.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            logging.error(f"WhatsApp send error: {resp.status_code} {resp.text}")
        # safe session-state logging (session may not exist)
        s = load_session(to_number)
        log_interaction(to_number, bot_message=message_text, session_state=(s.state if s else None))
    except Exception as e:
        logging.error(f"WhatsApp send error: {e}")

def send_whatsapp_interactive(to_number, interactive_payload):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
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
            s = load_session(to_number)
            log_interaction(to_number, bot_message=f"[Interactive Message] {json.dumps(interactive_payload)}", session_state=(s.state if s else None))
    except Exception as e:
        logging.error(f"Interactive send error: {e}")

# --------------------
# Symptom browse/search config (Option 3 hybrid)
# --------------------
COMMON_SYMPTOMS = cols[:15]

def get_common_symptoms():
    rows = [{"id": f"symptom_{s.lower()}", "title": s.replace("_", " ").title()[:24]} 
            for s in COMMON_SYMPTOMS[:9]]
    rows.append({"id": "search_symptom", "title": "🔎 Search Symptoms"})
    return rows

def search_symptom(user_input):
    return difflib.get_close_matches(user_input.lower(), [s.lower() for s in cols], n=8, cutoff=0.3)

# --------------------
# Pagination (with prev_page support)
# --------------------
def get_symptom_page(page=0, page_size=7):  
    """
    Returns at most 10 rows (7 symptoms + up to 3 controls).
    """
    start = page * page_size
    end = start + page_size
    page_symptoms = cols[start:end]

    rows = [{"id": f"symptom_{s.lower()}", "title": s.replace("_", " ").title()[:24]} 
            for s in page_symptoms]

    # Add navigation controls
    if page > 0:
        rows.append({"id": "prev_page", "title": "⬅ Back Page"})
    if end < len(cols):
        rows.append({"id": "next_page", "title": "➡ Next Page"})
    rows.append({"id": "finish", "title": "✅ Finish"})

    # ✅ enforce WhatsApp 10-row limit
    return rows[:10]

# --------------------
# Symptom-check flow
# --------------------
def get_or_create_session(phone_number):
    session = load_session(phone_number)
    if not session:
        session = UserSession(phone_number=phone_number)
        save_session(session)
    return session

def start_symptom_checker(phone_number):
    session = get_or_create_session(phone_number)
    session.state = "symptom_check"
    session.selected_symptoms = json.dumps([])
    session.current_page = 0
    save_session(session)

    rows = get_common_symptoms()
    interactive = {
        "type": "list",
        "body": {"text": "🩺 Select a common symptom or search:"},
        "footer": {"text": "Choose one option."},
        "action": {"button": "Symptoms", "sections": [{"title": "Symptoms", "rows": rows}]}
    }
    logging.info(f"Sending symptom list to {phone_number}")
    send_whatsapp_interactive(phone_number, interactive)

def handle_symptom_selection(phone_number, selection_id, user_text=None):
    session = load_session(phone_number)
    if not session:
        return
    selected_symptoms = json.loads(session.selected_symptoms or "[]")

    # Finish
    if selection_id == "finish":
        finish_symptom_check(phone_number)
        return

    # Search -> move to symptom_search state and ask for text
    if selection_id == "search_symptom":
        session.state = "symptom_search"
        save_session(session)
        send_whatsapp_message(phone_number, "🔎 Please type the name of your symptom (e.g., 'rash', 'headache').")
        return

    # Next page
    if selection_id == "next_page":
        session.current_page += 1
        save_session(session)
        rows = get_symptom_page(page=session.current_page)
        interactive_payload = {
            "type": "list",
            "body": {"text": f"🩺 Page {session.current_page + 1}. Select your symptom:"},
            "footer": {"text": "You can select one symptom per message."},
            "action": {"button": "Symptoms", "sections": [{"title": f"Symptoms {session.current_page+1}", "rows": rows}]}
        }
        send_whatsapp_interactive(phone_number, interactive_payload)
        return

    # Prev page
    if selection_id == "prev_page":
        if session.current_page > 0:
            session.current_page -= 1
            save_session(session)
        rows = get_symptom_page(page=session.current_page)
        interactive_payload = {
            "type": "list",
            "body": {"text": f"🩺 Page {session.current_page + 1}. Select your symptom:"},
            "footer": {"text": "You can select one symptom per message."},
            "action": {"button": "Symptoms", "sections": [{"title": f"Symptoms {session.current_page+1}", "rows": rows}]}
        }
        send_whatsapp_interactive(phone_number, interactive_payload)
        return

    # Add symptom
    if selection_id.startswith("symptom_"):
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
        return

# Finish check and followups
def finish_symptom_check(phone_number):
    session = load_session(phone_number)
    if not session:
        return
    symptoms = json.loads(session.selected_symptoms)

    if not symptoms:
        send_whatsapp_message(phone_number, "⚠️ You didn’t select any symptoms. Please try again with at least one.")
        session.state = "idle"
        save_session(session)
        return

    result = predict_disease(symptoms, days=2)
    if "error" in result:
        send_whatsapp_message(phone_number, f"⚠️ {result['error']}")
        session.state, session.selected_symptoms, session.current_page = "idle", json.dumps([]), 0
        save_session(session)
        return

    reply = (
        f"🩺 Based on the symptoms you provided, you may have: {result['disease']} "
        f"({result.get('confidence','N/A')}% confidence)\n"
        f"📖 Description: {result['description']}\n"
        f"⚠️ Severity: {result['severity']}\n"
        f"✅ Precautions:\n" + "\n".join([f"{i+1}) {p}" for i, p in enumerate(result['precautions'])])
    )
    if result.get('confidence', 0) < 70:
        reply += "\n\n⚠️ Confidence is low. Consider adding more symptoms."
    if len(symptoms) < 3:
        reply += "\n\n⚠️ Very few symptoms provided. Accuracy may be limited."
    reply += f"\n\n{DISCLAIMER}"
    send_whatsapp_message(phone_number, reply)

    # ✅ Handle follow-ups separately
    if "followup" in result and result["followup"]:
        rows = [{"id": f"symptom_{s}", "title": s.replace('_', ' ').title()} for s in result["followup"]]
        interactive_payload = {
            "type": "list",
            "body": {"text": "🤔 To improve accuracy, can you tell me if you also have any of these symptoms?"},
            "footer": {"text": "Select a symptom or click 'Finish' if none apply."},
            "action": {
                "button": "Select Symptom",
                "sections": [{"title": "Follow-Up Symptoms", "rows": rows + [{"id": "finish", "title": "✅ Finish"}]}]
            }
        }
        send_whatsapp_interactive(phone_number, interactive_payload)

        session.state = "symptom_followup"  # 🔹 NEW STATE
        save_session(session)
    else:
        session.state, session.selected_symptoms, session.current_page = "idle", json.dumps([]), 0
        save_session(session)

# --------------------
# OpenRouter summary / fallback
# --------------------
def generate_summary(phone_number):
    session = load_session(phone_number)
    if not session or not session.history:
        return "🧠 No memory to summarize yet."
    messages = json.loads(session.history)
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
                             headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                             json=payload, timeout=30)
        resp.raise_for_status()
        return "🧠 Summary:\n" + resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"Summary error: {e}")
        return "⚠️ Couldn't generate summary. Try again later."

def call_openrouter(user_text, phone_number):
    session = load_session(phone_number)
    messages = json.loads(session.history) if session and session.history else []
    messages.append({"role": "user", "content": user_text})
    system_prompt = (
        "You are Cura.ai — a cautious, empathetic health assistant designed to support general wellness. "
        "Be multilingual, empathetic, clear, and culturally sensitive. "
        "Avoid diagnosing or prescribing. Advise emergency care for red-flag symptoms. "
        f"Always identify yourself as Cura.ai. "
        f"End with this disclaimer:\n{DISCLAIMER}"
    )
    payload = {
        "model": "deepseek/deepseek-chat-v3.1:free",
        "temperature": 0.7,
        "messages": [{"role": "system", "content": system_prompt}] + messages
    }
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                             json=payload, timeout=30)
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

# --------------------
# Webhook endpoints
# --------------------
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode, token, challenge = request.args.get("hub.mode"), request.args.get("hub.verify_token"), request.args.get("hub.challenge")
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
            messages, contacts = value["messages"], value.get("contacts", [])
            phone_number = contacts[0]["wa_id"] if contacts else None
            if not phone_number:
                continue

            # create/load session
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

                # Log user message to backend logs
                log_interaction(phone_number, user_message=text, session_state=session.state)

                # --- Symptom search handling (user typed a search while in symptom_search state) ---
                if session.state == "symptom_search" and text:
                    matches = search_symptom(text)
                    if matches:
                        rows = [{"id": f"symptom_{m}", "title": m.replace('_',' ').title()[:24]} for m in matches[:9]]
                        rows.append({"id": "finish", "title": "✅ Finish"})
                        interactive_payload = {
                            "type": "list",
                            "body": {"text": f"🔎 Results for '{text}':"},
                            "footer": {"text": "Select one or finish."},
                            "action": {"button": "Choose", "sections": [{"title": "Matches", "rows": rows}]}
                        }
                        send_whatsapp_interactive(phone_number, interactive_payload)
                        # keep them in search mode until they click
                        session.state = "symptom_check"
                        save_session(session)
                    else:
                        send_whatsapp_message(phone_number, f"⚠️ No symptoms found for '{text}'. Try again.")
                    continue

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
                elif text == "/summary":
                    summary_text = generate_summary(phone_number)
                    send_whatsapp_message(phone_number, summary_text)
                    continue

                # Start symptom check
                if text == "check":
                    start_symptom_checker(phone_number)
                    continue

                if session.state == "symptom_followup" and interactive_id:
                    if interactive_id.startswith("symptom_"):
                        handle_symptom_selection(phone_number, interactive_id)
                    elif interactive_id == "finish":
                        # ✅ End follow-up loop
                        session.state, session.selected_symptoms, session.current_page = "idle", json.dumps([]), 0
                        save_session(session)
                        send_whatsapp_message(phone_number, "✅ Thanks! That’s all I needed. Stay healthy!")
                    continue

                # Handle interactive replies
                if interactive_id:
                    # include prev_page handling here (so it routes to handler)
                    if interactive_id.startswith("symptom_") or interactive_id in {"next_page", "prev_page"}:
                        handle_symptom_selection(phone_number, interactive_id)
                    elif interactive_id == "add_more":
                        # show current page symptoms again
                        rows = get_symptom_page(page=session.current_page)
                        interactive_payload = {
                            "type": "list",
                            "body": {"text": f"🩺 Page {session.current_page + 1}. Select your symptom:"},
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
    return "✅ Cura.ai Medical Chatbot is running!"

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
