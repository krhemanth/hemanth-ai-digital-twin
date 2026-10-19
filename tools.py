"""Tools, persistence, notifications, and human follow-up for the digital twin."""

from __future__ import annotations

import base64
import email as email_parser
import hashlib
import hmac
import json
import os
import re
import smtplib
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from pathlib import Path
from typing import Any, Callable

import requests
from google.auth.exceptions import RefreshError
from dotenv import load_dotenv
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from context import append_confirmed_answer


BASE_DIR = Path(__file__).resolve().parent
APP_ROOT = BASE_DIR.parent
for env_path in (
    APP_ROOT.parent / ".env",
    APP_ROOT / ".env",
    BASE_DIR / ".env",
    Path("/etc/secrets/.env"),
):
    if env_path.exists():
        load_dotenv(env_path, override=True)

DATA_DIR = Path(os.getenv("TWIN_DATA_DIR", str(BASE_DIR))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONTACT_REQUESTS_FILE = DATA_DIR / "contact_requests.json"
EMAILS_FILE = DATA_DIR / "emails.txt"
UNKNOWN_QUESTIONS_FILE = DATA_DIR / "unknown_questions.txt"
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
REQUEST_ID_PATTERN = re.compile(r"\[Twin request ([0-9a-f-]+)\]")
WHATSAPP_REPLY_PATTERN = re.compile(
    r"^\s*\[?([0-9A-Fa-f]{8})\]?(?:\s*[:\-])?\s+(.+?)\s*$", re.DOTALL
)
TIME_SENSITIVE_QUESTION_PATTERN = re.compile(
    r"\b(?:available|availability|free|quick call|call now|coffee|meet(?:ing)?|"
    r"today|tomorrow|tonight|right now|currently|this (?:morning|afternoon|evening)|"
    r"schedule)\b",
    re.IGNORECASE,
)
DELIVERY_CHOICE_PATTERN = re.compile(
    r"^(?:.*\b(?:wait|chat|email|reply|respond|send|contact)\b.*|"
    r"yes(?: please)?|(?:perfect|thank you|thanks|got it|okay|ok)\b.*)$",
    re.IGNORECASE,
)

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

REQUEST_LOCK = threading.RLock()
NOTIFICATION_LOCK = threading.Lock()
GMAIL_LOCK = threading.Lock()
WATCHER_LOCK = threading.Lock()
REPLY_CANDIDATE_LOCK = threading.Lock()
_gmail_service = None
_gmail_retry_after = 0.0
_gmail_auth_warning_shown = False
_reply_watcher_started = False
_reply_candidate_cache: dict[str, tuple[str, float]] = {}
REPLY_STABILITY_SECONDS = 8

AnswerValidator = Callable[..., tuple[str, bool]]
QuestionResolver = Callable[[list[dict[str, str]], str, str], str]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_config_path(value: str | None, default_name: str) -> Path:
    path = Path(value or default_name)
    if path.is_absolute():
        return path
    for base in (APP_ROOT, BASE_DIR):
        candidate = base / path
        if candidate.exists():
            return candidate
    return APP_ROOT / path


def _atomic_write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_requests() -> list[dict[str, Any]]:
    if not CONTACT_REQUESTS_FILE.exists():
        return []
    try:
        value = json.loads(CONTACT_REQUESTS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError) as error:
        print(f"Unable to read contact requests: {error}")
        return []


def save_requests(requests_data: list[dict[str, Any]]) -> None:
    _atomic_write_json(CONTACT_REQUESTS_FILE, requests_data)


def push(message: str) -> dict[str, Any]:
    """Send a non-fatal Pushover notification."""
    user = os.getenv("PUSHOVER_USER")
    token = os.getenv("PUSHOVER_TOKEN")
    if not user or not token:
        return {"delivered": False, "reason": "Pushover is not configured"}
    try:
        response = requests.post(
            PUSHOVER_URL,
            data={"user": user, "token": token, "message": message[:1024]},
            timeout=20,
        )
        response.raise_for_status()
        return {"delivered": True}
    except requests.RequestException as error:
        print(f"Pushover delivery failed: {error}")
        return {"delivered": False, "reason": "Pushover delivery failed"}


def gmail_service(force_interactive: bool = False):
    global _gmail_service
    with GMAIL_LOCK:
        if _gmail_service:
            return _gmail_service

        client_file = _resolve_config_path(
            os.getenv("GMAIL_OAUTH_CLIENT_FILE"), "gmail_client_secret.json"
        )
        token_file = _resolve_config_path(
            os.getenv("GMAIL_OAUTH_TOKEN_FILE"), "gmail_token.json"
        )
        token_json = os.getenv("GMAIL_OAUTH_TOKEN_JSON")
        credentials = None
        if token_json:
            try:
                credentials = Credentials.from_authorized_user_info(
                    json.loads(token_json), GMAIL_SCOPES
                )
            except (json.JSONDecodeError, TypeError, ValueError) as error:
                raise RuntimeError("GMAIL_OAUTH_TOKEN_JSON is not valid OAuth JSON") from error
        elif token_file.exists():
            credentials = Credentials.from_authorized_user_file(token_file, GMAIL_SCOPES)
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(GoogleRequest())
            except RefreshError:
                credentials = None

        if not credentials or not credentials.valid:
            interactive = force_interactive or (
                os.getenv("GMAIL_ALLOW_INTERACTIVE_OAUTH", "false").lower() == "true"
            )
            if not interactive:
                raise RuntimeError(
                    "Gmail authorization is missing; run OAuth setup locally or set "
                    "GMAIL_OAUTH_TOKEN_JSON as a deployment secret"
                )
            if not client_file.exists():
                raise FileNotFoundError(f"OAuth client file not found: {client_file}")
            credentials = InstalledAppFlow.from_client_secrets_file(
                client_file, GMAIL_SCOPES
            ).run_local_server(port=0)

        if not token_json:
            token_file.write_text(credentials.to_json(), encoding="utf-8")
        _gmail_service = build("gmail", "v1", credentials=credentials)
        return _gmail_service


def _smtp_deliver(message: EmailMessage) -> dict[str, Any]:
    host = os.getenv("CONTACT_SMTP_HOST")
    username = os.getenv("CONTACT_SMTP_USERNAME")
    password = os.getenv("CONTACT_SMTP_PASSWORD")
    if not all([host, username, password]):
        return {"delivered": False, "reason": "SMTP is not configured"}
    try:
        port = int(os.getenv("CONTACT_SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
        return {"delivered": True, "channel": "smtp"}
    except Exception as error:
        print(f"SMTP delivery failed: {error}")
        return {"delivered": False, "reason": "SMTP delivery failed"}


def deliver(message: EmailMessage) -> dict[str, Any]:
    """Deliver with Gmail OAuth, falling back to configured SMTP."""
    try:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        sent = gmail_service().users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return {
            "delivered": True,
            "channel": "gmail",
            "gmail_message_id": sent.get("id"),
            "gmail_thread_id": sent.get("threadId"),
        }
    except Exception as error:
        print(f"Gmail delivery failed: {error}")
        return _smtp_deliver(message)


def _owner_message(subject: str, body: str, reply_to: str | None = None) -> EmailMessage | None:
    owner = os.getenv("OWNER_EMAIL")
    sender = os.getenv("CONTACT_FROM_EMAIL") or owner or os.getenv("CONTACT_SMTP_USERNAME")
    if not owner or not sender:
        return None
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = owner
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    return message


def notify_unknown_question_email(question: str) -> dict[str, Any]:
    message = _owner_message(
        "Digital twin needs Hemanth's answer",
        "The digital twin could not answer this question from verified context:\n\n"
        f"{question}\n\nA visitor can create a routed follow-up request in chat.",
    )
    return deliver(message) if message else {
        "delivered": False,
        "reason": "Owner email is not configured",
    }


def notify_user_details_email(
    visitor_email: str, name: str, notes: str
) -> dict[str, Any]:
    message = _owner_message(
        f"Digital twin contact request from {name}",
        f"A visitor wants to get in touch with Hemanth.\n\n"
        f"Name: {name}\n"
        f"Email: {visitor_email}\n"
        f"Notes: {notes}",
        visitor_email,
    )
    return deliver(message) if message else {
        "delivered": False,
        "reason": "Owner email is not configured",
    }


def record_user_details(
    email: str, name: str = "Name not provided", notes: str = "not provided"
) -> dict[str, Any]:
    email = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(email):
        return {"recorded": False, "reason": "A valid email is required"}
    with NOTIFICATION_LOCK:
        with EMAILS_FILE.open("a", encoding="utf-8") as file:
            file.write(f"{_utc_now()}\t{name.strip()}\t{email}\t{notes.strip()}\n")
    whatsapp_delivery = send_whatsapp_message(
        "Digital twin contact request\n"
        f"Visitor: {name.strip() or 'Name not provided'}\n"
        f"Email: {email}\n"
        f"Notes: {notes.strip()}"
    )
    return {
        "recorded": True,
        "whatsapp_delivery": whatsapp_delivery,
        "notified": bool(whatsapp_delivery.get("delivered")),
    }


def _required_whatsapp_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith(("YOUR_", "PASTE_")):
        raise RuntimeError(f"Set {name} before using WhatsApp notifications")
    return value


def _normalize_phone_number(number: str) -> str:
    return "".join(character for character in number if character.isdigit())


def send_whatsapp_message(message: str) -> dict[str, Any]:
    """Send an owner notification through the WhatsApp Cloud API."""
    try:
        token = _required_whatsapp_env("WHATSAPP_TOKEN")
        phone_number_id = _required_whatsapp_env("WHATSAPP_PHONE_NUMBER_ID")
        recipient = _normalize_phone_number(
            _required_whatsapp_env("WHATSAPP_HUMAN_RECIPIENT")
        )
        api_version = os.getenv("WHATSAPP_API_VERSION", "v26.0")
        response = requests.post(
            f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": message[:4096]},
            },
            timeout=30,
        )
        if response.ok:
            message_id = response.json().get("messages", [{}])[0].get("id")
            return {"delivered": True, "whatsapp_message_id": message_id}
        try:
            detail = response.json()
        except requests.exceptions.JSONDecodeError:
            detail = response.text
        return {
            "delivered": False,
            "reason": f"Meta API returned HTTP {response.status_code}: {detail}",
        }
    except (RuntimeError, requests.RequestException) as error:
        return {"delivered": False, "reason": str(error)}


def whatsapp_signature_is_valid(raw_body: bytes, signature_header: str | None) -> bool:
    app_secret = os.getenv("META_APP_SECRET", "").strip()
    if not app_secret or app_secret.startswith("PASTE_"):
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def record_unknown_question(question: str) -> dict[str, Any]:
    question = question.strip()
    if not question:
        return {"recorded": False, "reason": "Question is required", "notified": False}
    with NOTIFICATION_LOCK:
        with UNKNOWN_QUESTIONS_FILE.open("a", encoding="utf-8") as file:
            file.write(f"{_utc_now()}\t{question}\n")
    return {
        "recorded": True,
        "notified": False,
        "awaiting_contact_consent": True,
    }


def notify_hemanth(request_data: dict[str, Any]) -> dict[str, Any]:
    recent_questions = request_data.get("recent_questions") or [request_data["question"]]
    questions_text = "\n".join(
        f"{index}. {question}" for index, question in enumerate(recent_questions[-4:], 1)
    )
    reply_code = request_data["reply_code"]
    return send_whatsapp_message(
        f"[Twin request {reply_code}]\n"
        f"Visitor: {request_data['name']}\n\n"
        f"Current question:\n{request_data['question']}\n\n"
        f"Recent visitor questions (up to 4):\n{questions_text}\n\n"
        f"Reply with:\n{reply_code} Your answer"
    )


def record_contact_request(
    question: str,
    reply_channel: str,
    email: str | None = None,
    name: str = "Name not provided",
    recent_questions: list[str] | None = None,
    conversation: str | None = None,
    visitor_key: str | None = None,
) -> dict[str, Any]:
    if reply_channel != "chat":
        return {"recorded": False, "reason": "WhatsApp follow-ups return through chat"}

    with REQUEST_LOCK:
        requests_data = load_requests()
        existing_codes = {
            str(item.get("reply_code") or "").upper() for item in requests_data
        }
        while True:
            request_id = str(uuid.uuid4())
            reply_code = request_id.split("-", 1)[0].upper()
            if reply_code not in existing_codes:
                break
    request_data = {
        "id": request_id,
        "reply_code": reply_code,
        "created_at": _utc_now(),
        "name": name.strip() or "Name not provided",
        "email": email.strip().lower() if email else None,
        "question": question.strip(),
        "recent_questions": recent_questions or [question.strip()],
        "conversation": conversation or question.strip(),
        "reply_channel": reply_channel,
        "visitor_key": visitor_key,
        "chat_wait_deadline": (
            (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
            if reply_channel == "chat"
            else None
        ),
        "notification_message_id": make_msgid(),
    }
    with REQUEST_LOCK:
        requests_data = load_requests()
        requests_data.append(request_data)
        save_requests(requests_data)

    whatsapp_delivery = notify_hemanth(request_data)
    with REQUEST_LOCK:
        requests_data = load_requests()
        stored = next((item for item in requests_data if item["id"] == request_data["id"]), None)
        if stored:
            stored["whatsapp_delivery"] = whatsapp_delivery
            save_requests(requests_data)

    return {
        "recorded": True,
        "request_id": request_data["id"],
        "reply_channel": reply_channel,
        "reply_code": request_data["reply_code"],
        "delivered": bool(whatsapp_delivery.get("delivered")),
        "notified": bool(whatsapp_delivery.get("delivered")),
        "whatsapp_delivery": whatsapp_delivery,
    }


def record_visitor_contact_details(
    request_id: str, name: str, contact_details: str
) -> dict[str, Any]:
    """Attach timeout contact details without changing browser answer routing."""
    contact_details = contact_details.strip()
    if not contact_details:
        return {"recorded": False, "reason": "Contact details are required"}
    with REQUEST_LOCK:
        requests_data = load_requests()
        request_data = next(
            (item for item in requests_data if item.get("id") == request_id), None
        )
        if not request_data:
            return {"recorded": False, "reason": "The follow-up request was not found"}
        request_data["name"] = name.strip() or request_data.get("name") or "Name not provided"
        request_data["visitor_contact_details"] = contact_details
        request_data["visitor_contact_added_at"] = _utc_now()
        reply_code = request_data.get("reply_code") or str(request_id)[:8].upper()
        save_requests(requests_data)
    delivery = send_whatsapp_message(
        f"[Contact update {reply_code}]\n"
        f"Visitor: {name.strip() or 'Name not provided'}\n"
        f"Contact details: {contact_details}"
    )
    return {"recorded": True, "whatsapp_delivery": delivery}


def mark_request_awaiting_visitor_email(request_id: str) -> bool:
    """Pause background completion until a timed-out visitor supplies an email."""
    with REQUEST_LOCK:
        requests_data = load_requests()
        request_data = next(
            (item for item in requests_data if item.get("id") == request_id), None
        )
        if not request_data or request_data.get("answered_at"):
            return False
        request_data["awaiting_visitor_email"] = True
        request_data["chat_wait_expired_at"] = _utc_now()
        save_requests(requests_data)
        return True


def latest_followup_for_visitor(visitor_key: str) -> dict[str, Any] | None:
    """Return the newest chat follow-up that still needs browser-side delivery."""
    if not visitor_key:
        return None
    for request_data in reversed(load_requests()):
        if request_data.get("visitor_key") != visitor_key:
            continue
        if request_data.get("reply_channel") != "chat":
            continue
        if request_data.get("answer") and request_data.get("chat_answer_displayed_at"):
            continue
        if request_data.get("answer") or not request_data.get("answered_at"):
            return request_data
    return None


def mark_chat_answer_displayed(request_id: str) -> None:
    with REQUEST_LOCK:
        requests_data = load_requests()
        request_data = next(
            (item for item in requests_data if item.get("id") == request_id), None
        )
        if request_data:
            request_data["chat_answer_displayed_at"] = _utc_now()
            save_requests(requests_data)


def route_request_to_visitor_email(
    request_id: str,
    email: str,
    name: str = "Name not provided",
    visitor_details: str = "",
) -> dict[str, Any]:
    """Switch a timed-out chat request to personal-email delivery."""
    email = email.strip().lower()
    name = name.strip() or "Name not provided"
    if not EMAIL_PATTERN.fullmatch(email):
        return {"recorded": False, "reason": "A valid email is required"}

    with REQUEST_LOCK:
        requests_data = load_requests()
        request_data = next(
            (item for item in requests_data if item.get("id") == request_id), None
        )
        if not request_data:
            return {"recorded": False, "reason": "The follow-up request was not found"}
        request_data["name"] = name
        request_data["email"] = email
        request_data["reply_channel"] = "email"
        request_data["awaiting_visitor_email"] = False
        request_data["visitor_email_added_at"] = _utc_now()
        existing_answer = request_data.get("answer")
        original_question = str(request_data.get("question") or "Not provided")
        save_requests(requests_data)

    contact_notice = record_user_details(
        email,
        name,
        f"Email follow-up for twin request {request_id}. "
        f"Visitor question: {original_question}. "
        f"Visitor details: {visitor_details or 'Not provided'}",
    )
    visitor_delivery = None
    if existing_answer:
        visitor_delivery = send_visitor_answer(email, existing_answer)
    return {
        "recorded": True,
        "request_id": request_id,
        "reply_channel": "email",
        "contact_notification": contact_notice,
        "visitor_delivery": visitor_delivery,
    }


def _clean_email_reply(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if (
            re.match(r"^On .+ wrote:$", line.strip())
            or line.startswith(">")
            or line.strip() == "---------- Forwarded message ---------"
        ):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _message_text(message) -> str:
    texts = []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_type() == "text/plain" and "attachment" not in str(
            part.get("Content-Disposition", "")
        ):
            payload = part.get_payload(decode=True)
            if payload:
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                cleaned = _clean_email_reply(text)
                if cleaned:
                    texts.append(cleaned)
    return max(texts, key=len) if texts else ""


def _stable_reply_candidate(request_id: str, candidate: str, now: float | None = None) -> str | None:
    """Return a Gmail reply only after its body stops changing during synchronization."""
    observed_at = time.monotonic() if now is None else now
    with REPLY_CANDIDATE_LOCK:
        previous = _reply_candidate_cache.get(request_id)
        if previous and previous[0] == candidate:
            if observed_at - previous[1] >= REPLY_STABILITY_SECONDS:
                _reply_candidate_cache.pop(request_id, None)
                return candidate
            return None
        _reply_candidate_cache[request_id] = (candidate, observed_at)
        return None


def fetch_hemanth_reply(request_id: str) -> str | None:
    """Read a WhatsApp-completed answer from the persisted request."""
    request_data = next(
        (item for item in load_requests() if item.get("id") == request_id), None
    )
    if request_data and request_data.get("answered_at") and request_data.get("answer"):
        return str(request_data["answer"])
    return None


def send_visitor_answer(visitor_email: str, answer: str) -> dict[str, Any]:
    owner = os.getenv("OWNER_EMAIL")
    sender = os.getenv("CONTACT_FROM_EMAIL") or owner or os.getenv("CONTACT_SMTP_USERNAME")
    if not sender:
        return {"delivered": False, "reason": "Sender email is not configured"}
    message = EmailMessage()
    message["Subject"] = "Response from Hemanth"
    message["From"] = sender
    message["To"] = visitor_email
    message.set_content(answer)
    return deliver(message)


def complete_request(
    request_id: str,
    answer: str,
    answer_validator: AnswerValidator | None = None,
    source_message_id: str | None = None,
) -> dict[str, Any] | None:
    with REQUEST_LOCK:
        requests_data = load_requests()
        request_data = next(
            (item for item in requests_data if item.get("id") == request_id), None
        )
        if not request_data:
            return request_data
        if request_data.get("answered_at") and request_data.get("answer"):
            return request_data

        # Human replies are authoritative and are never filtered. If an older
        # process marked a reply rejected without saving an answer, recover the
        # request here instead of losing Hemanth's response.
        request_data.pop("answer_rejected_at", None)

        request_data["answer"] = answer.strip()
        request_data["answered_at"] = _utc_now()
        if source_message_id:
            request_data["whatsapp_reply_message_id"] = source_message_id
        with REPLY_CANDIDATE_LOCK:
            _reply_candidate_cache.pop(request_id, None)
        if request_data["reply_channel"] == "email":
            request_data["visitor_delivery"] = send_visitor_answer(
                request_data["email"], request_data["answer"]
            )
        # Availability and scheduling answers expire quickly and must never train
        # future responses. Every such question is checked with Hemanth again.
        if not TIME_SENSITIVE_QUESTION_PATTERN.search(request_data["question"]):
            append_confirmed_answer(request_data["question"], request_data["answer"])
        else:
            request_data["summary_update_skipped"] = "time-sensitive question"
        save_requests(requests_data)
        return request_data


def process_whatsapp_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Match owner WhatsApp replies to pending requests by their eight-digit code."""
    expected_sender = _normalize_phone_number(os.getenv("WHATSAPP_HUMAN_RECIPIENT", ""))
    results: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                message_id = str(message.get("id") or "")
                sender = _normalize_phone_number(str(message.get("from") or ""))
                if not expected_sender or sender != expected_sender:
                    results.append({"accepted": False, "reason": "unexpected sender"})
                    continue
                if message.get("type") != "text":
                    results.append({"accepted": False, "reason": "text reply required"})
                    continue
                body = str(message.get("text", {}).get("body") or "").strip()
                match = WHATSAPP_REPLY_PATTERN.match(body)
                if not match:
                    results.append({"accepted": False, "reason": "reply code missing"})
                    continue
                reply_code, answer = match.group(1).upper(), match.group(2).strip()
                request_data = next(
                    (
                        item
                        for item in load_requests()
                        if str(item.get("reply_code") or "").upper() == reply_code
                    ),
                    None,
                )
                if not request_data:
                    results.append({"accepted": False, "reply_code": reply_code, "reason": "unknown code"})
                    continue
                if request_data.get("answered_at"):
                    duplicate = request_data.get("whatsapp_reply_message_id") == message_id
                    results.append({"accepted": duplicate, "duplicate": duplicate, "reply_code": reply_code, "request_id": request_data.get("id"), "reason": "already answered"})
                    continue
                completed = complete_request(str(request_data["id"]), answer, source_message_id=message_id)
                results.append({"accepted": bool(completed and completed.get("answer")), "reply_code": reply_code, "request_id": request_data.get("id")})
                if completed:
                    print(f"[WhatsApp answer] {reply_code} -> {completed.get('name', 'visitor')}: {answer}")
    return results


def process_owner_replies_once(answer_validator: AnswerValidator | None = None) -> None:
    for request_data in load_requests():
        if (
            request_data.get("answered_at")
            and request_data.get("answer")
        ) or request_data.get("awaiting_visitor_email"):
            continue
        deadline_text = request_data.get("chat_wait_deadline")
        if request_data.get("reply_channel") == "chat" and deadline_text:
            try:
                if datetime.now(timezone.utc) >= datetime.fromisoformat(deadline_text):
                    mark_request_awaiting_visitor_email(str(request_data["id"]))
                    continue
            except (TypeError, ValueError):
                pass
        # WhatsApp replies are completed immediately by the webhook. This worker
        # only advances the two-minute timeout state.


def start_reply_watcher(
    answer_validator: AnswerValidator | None = None, interval_seconds: int = 60
) -> None:
    global _reply_watcher_started
    with WATCHER_LOCK:
        if _reply_watcher_started:
            return
        _reply_watcher_started = True

    def watch() -> None:
        while True:
            try:
                process_owner_replies_once(answer_validator)
            except Exception as error:
                print(f"Reply watcher error: {error}")
            time.sleep(interval_seconds)

    threading.Thread(target=watch, daemon=True, name="twin-reply-watcher").start()


def normalize_history(history) -> list[dict[str, str]]:
    converted = []
    for item in history or []:
        if isinstance(item, dict):
            role, content = item.get("role"), item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str):
                converted.append({"role": role, "content": content})
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            user, assistant = item
            if user:
                converted.append({"role": "user", "content": str(user)})
            if assistant:
                converted.append({"role": "assistant", "content": str(assistant)})
    return converted


def recent_visitor_questions(history, current_message: str) -> list[str]:
    messages = [
        item["content"] for item in normalize_history(history) if item["role"] == "user"
    ] + [current_message]
    questions = [message for message in messages if "?" in message]
    return questions[-2:] or messages[-1:]


def conversation_transcript(history, current_message: str) -> str:
    labels = {"user": "Visitor", "assistant": "Hemanth's AI twin"}
    messages = normalize_history(history) + [{"role": "user", "content": current_message}]
    return "\n\n".join(
        f"{labels.get(item['role'], item['role'])}: {item['content']}" for item in messages
    )


def original_unanswered_question(history, current_message: str, fallback: str) -> str:
    visitor_messages = [
        item["content"].strip()
        for item in normalize_history(history)[-10:]
        + [{"role": "user", "content": current_message}]
        if item["role"] == "user" and item["content"].strip()
    ]
    candidates = [
        message for message in visitor_messages if not DELIVERY_CHOICE_PATTERN.match(message)
    ]
    return candidates[-1] if candidates else fallback


record_contact_request_json = {
    "name": "record_contact_request",
    "description": (
        "Use when verified profile sources cannot answer a visitor's question and "
        "the visitor explicitly chooses an email reply or to wait in chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "reply_channel": {"type": "string", "enum": ["email", "chat"]},
            "email": {"type": "string", "format": "email"},
            "name": {"type": "string"},
        },
        "required": ["question", "reply_channel"],
        "additionalProperties": False,
    },
}

record_user_details_json = {
    "name": "record_user_details",
    "description": (
        "Record a visitor who explicitly asks to connect and provides an email. "
        "Use for general networking, not an unanswered-question follow-up."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "name": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["email"],
        "additionalProperties": False,
    },
}

record_unknown_question_json = {
    "name": "record_unknown_question",
    "description": (
        "Record a personal or professional question about Hemanth that cannot be "
        "answered from the verified profile. This records it locally while the visitor "
        "is asked for permission to contact Hemanth. Call once per unanswered question."
    ),
    "parameters": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
        "additionalProperties": False,
    },
}

tools = [
    {"type": "function", "function": record_user_details_json},
    {"type": "function", "function": record_unknown_question_json},
]

TOOL_FUNCTIONS = {
    "record_contact_request": record_contact_request,
    "record_user_details": record_user_details,
    "record_unknown_question": record_unknown_question,
}


def handle_tool_calls(
    tool_calls,
    history=None,
    current_message: str = "",
    question_resolver: QuestionResolver | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Validate, execute, and format any number of model tool calls."""
    tool_messages = []
    outcomes = []
    for tool_call in tool_calls or []:
        tool_name = tool_call.function.name
        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object")
            function = TOOL_FUNCTIONS.get(tool_name)
            if function is None:
                raise ValueError(f"Unknown tool: {tool_name}")
            if tool_name == "record_contact_request":
                fallback = arguments.get("question", current_message)
                if question_resolver:
                    arguments["question"] = question_resolver(
                        normalize_history(history), current_message, fallback
                    )
                else:
                    arguments["question"] = original_unanswered_question(
                        history, current_message, fallback
                    )
                arguments["recent_questions"] = recent_visitor_questions(
                    history, current_message
                )
                arguments["conversation"] = conversation_transcript(
                    history, current_message
                )
            result = function(**arguments)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
            result = {"recorded": False, "reason": str(error)}
        except Exception as error:
            print(f"Tool {tool_name} failed: {error}")
            result = {"recorded": False, "reason": "Tool execution failed"}
        outcomes.append({"tool_name": tool_name, "result": result})
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result),
            }
        )
    return tool_messages, outcomes
