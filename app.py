"""Gradio application for Hemanth's guarded, human-in-the-loop digital twin."""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

from context import get_profile_context, get_system_prompt
from styles import CSS, EXAMPLES, JS
from tools import (
    DATA_DIR,
    complete_request,
    fetch_hemanth_reply,
    handle_tool_calls,
    latest_followup_for_visitor,
    mark_chat_answer_displayed,
    mark_request_awaiting_visitor_email,
    normalize_history,
    record_contact_request,
    record_unknown_question,
    route_request_to_visitor_email,
    start_reply_watcher,
    tools,
)


BASE_DIR = Path(__file__).resolve().parent
for env_path in (BASE_DIR / ".env", BASE_DIR.parent / ".env", BASE_DIR.parent.parent / ".env"):
    if env_path.exists():
        load_dotenv(env_path, override=True)
        break

CHAT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
GUARD_MODEL = os.getenv("OPENAI_GUARD_MODEL", "gpt-5.4-nano")
APP_VERSION = "2026-09-23.14"
MAX_TOOL_ROUNDS = 5
MAX_INPUT_CHARACTERS = 2000
RATE_LIMIT_WINDOW_SECONDS = 120
RATE_LIMIT_WINDOW_MAX = 8
RATE_LIMIT_HOUR_MAX = 30
HUMAN_REPLY_POLL_SECONDS = 10
HUMAN_REPLY_WAIT_SECONDS = 5 * 60
HUMAN_REPLY_STATUS = (
    "I emailed the real Hemanth and sent him a Pushover notification. I’ll check "
    "for his response every 10 seconds for the next 5 minutes."
)
BROWSER_CHATS_FILE = DATA_DIR / "browser_chats.json"
BROWSER_CHATS_LOCK = threading.RLock()

ADULT_CONTENT_PATTERN = re.compile(
    r"\b(?:porn(?:ography)?|explicit sexual|sexual roleplay|sext(?:ing)?|"
    r"nudes?|naked|erotic(?:a)?|fetish|nsfw|xxx|onlyfans|hookup|"
    r"boobs?|genitals?|masturbat(?:e|ion|ing))\b",
    re.IGNORECASE,
)
PROMPT_ATTACK_PATTERN = re.compile(
    r"(?:ignore|forget|override|bypass).{0,40}"
    r"(?:previous|system|developer|instruction|guardrail)|"
    r"(?:reveal|show|print).{0,30}"
    r"(?:system prompt|hidden instruction|developer message)|jailbreak",
    re.IGNORECASE,
)
VISITOR_INTRO_PATTERN = re.compile(
    r"^\s*(?:(?:hi|hello|hey)\b[\s,!.-]*)?"
    r"(?:this\s+is|i\s+am|i['’]?m|my\s+name\s+is)\s+"
    r"([A-Za-z][A-Za-z .'-]{0,49})[.!]?\s*$",
    re.IGNORECASE,
)
GREETING_PATTERN = re.compile(
    r"^\s*(?:hi|hello|hey|good\s+morning|good\s+afternoon|good\s+evening)"
    r"(?:\s+there)?[\s!,.]*$",
    re.IGNORECASE,
)
KNOWN_PROFESSIONAL_TOPIC_PATTERN = re.compile(
    r"\b(?:ai|artificial intelligence|generative ai|llm|large language model|"
    r"agentic|rag|retrieval.augmented|langgraph|langchain|mcp|model context protocol|"
    r"machine learning|data engineering|java|python|spark|pyspark|aws|cloud|"
    r"architecture|engineering skills?|technical skills?|projects?|experience)\b",
    re.IGNORECASE,
)
UNKNOWN_ANSWER_PATTERN = re.compile(
    r"\b(?:i|we)\s+(?:do not|don't|don’t|cannot|can't|can’t)\s+"
    r"(?:know|have|find|verify)\b|"
    r"\b(?:no|not enough)\s+verified information\b|"
    r"\b(?:not|isn't|isn’t|is not)\s+(?:in|included in|available in|stated in)\b"
    r".{0,80}\b(?:verified )?(?:sources?|profile|resume|information)\b|"
    r"\bnot (?:available|stated|included|provided|verified) in (?:his|the) "
    r"(?:profile|resume|context|sources?)\b",
    re.IGNORECASE,
)
HEMANTH_REFERENCE_PATTERN = re.compile(
    r"\b(?:hemanth|hemanthkumar|hemanth's|hemanth’s)\b",
    re.IGNORECASE,
)
VISITOR_EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
CONTACT_CONSENT_PATTERN = re.compile(
    r"^\s*(?:yes(?:\s+please)?(?:\s+(?:check|ask|contact|email|reach\s+out)"
    r"(?:\s+with|\s+to)?\s+(?:hemanth|him))?|please do|sure|okay|ok|go ahead|"
    r"contact him|reach out|ask him|email him)[.!]?\s*$",
    re.IGNORECASE,
)
CONTACT_DECLINE_PATTERN = re.compile(
    r"^\s*(?:no|no thanks|not now|don't|do not|cancel)[.!]?\s*$",
    re.IGNORECASE,
)
TIME_SENSITIVE_AVAILABILITY_PATTERN = re.compile(
    r"\b(?:(?:is|will|would)\s+(?:he|hemanth)\s+(?:be\s+)?(?:available|free)|"
    r"(?:available|free)\s+(?:now|today|tomorrow|tonight)|quick\s+call|"
    r"coffee|meet(?:ing)?\s+(?:now|today|tomorrow)|current availability)\b",
    re.IGNORECASE,
)

RATE_LIMITS: dict[str, deque[float]] = defaultdict(deque)
RATE_LIMIT_LOCK = threading.Lock()
PENDING_FOLLOWUPS: dict[str, dict[str, str | float]] = {}
PENDING_FOLLOWUP_LOCK = threading.Lock()
ACTIVE_BROWSER_SESSIONS: dict[str, str] = {}
ACTIVE_BROWSER_SESSIONS_LOCK = threading.Lock()
VISITOR_NAMES: dict[str, str] = {}
openai = OpenAI()
# Human follow-up email is initiated only by the deterministic consent branch
# after an explicit yes. The model may record an unknown question, but it cannot
# send the follow-up notification itself.
CHAT_TOOLS = [
    tool
    for tool in tools
    if tool.get("function", {}).get("name") != "record_contact_request"
]


def request_identity(request: gr.Request | None) -> str:
    session_hash = getattr(request, "session_hash", None) if request else None
    client = getattr(request, "client", None) if request else None
    client_host = getattr(client, "host", None) if client else None
    # Prefer the network identity so opening new browser sessions does not bypass
    # the model-cost rate limit. Fall back to Gradio's session identifier locally.
    return f"ip:{client_host}" if client_host else session_hash or "anonymous"


def session_identity(request: gr.Request | None) -> str:
    """Use Gradio's browser-session identifier for conversational state."""
    session_hash = getattr(request, "session_hash", None) if request else None
    return session_hash or request_identity(request)


def browser_identity(request: gr.Request | None) -> str:
    """Stable, non-plaintext browser key that survives a page refresh."""
    client = getattr(request, "client", None) if request else None
    host = getattr(client, "host", "") if client else ""
    headers = getattr(request, "headers", {}) if request else {}
    user_agent = headers.get("user-agent", "") if headers else ""
    return hashlib.sha256(f"{host}|{user_agent}".encode("utf-8")).hexdigest()


def load_browser_chat(browser_key: str) -> list[dict[str, str]]:
    with BROWSER_CHATS_LOCK:
        try:
            data = json.loads(BROWSER_CHATS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []
        return normalize_history(data.get(browser_key, []))


def save_browser_chat(browser_key: str, messages: list[dict[str, str]]) -> None:
    with BROWSER_CHATS_LOCK:
        try:
            data = json.loads(BROWSER_CHATS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}
        data[browser_key] = normalize_history(messages)[-100:]
        temporary = BROWSER_CHATS_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(BROWSER_CHATS_FILE)


def _message_identity(message: dict[str, str]) -> tuple[str, str]:
    return (
        message.get("role", ""),
        message.get("content", "").replace("\u200b", "").strip(),
    )


def merge_conversation_history(
    existing, incoming
) -> list[dict[str, str]]:
    """Merge overlapping Gradio/browser history without discarding older turns."""
    left = normalize_history(existing)
    right = normalize_history(incoming)
    if not left:
        return right
    if not right:
        return left

    left_keys = [_message_identity(item) for item in left]
    right_keys = [_message_identity(item) for item in right]
    max_overlap = min(len(left_keys), len(right_keys))
    for size in range(max_overlap, 0, -1):
        if left_keys[-size:] == right_keys[:size]:
            return left + right[size:]
        if right_keys[-size:] == left_keys[:size]:
            return right + left[size:]

    # If one transcript is a complete prefix of the other, retain the longer one.
    if left_keys == right_keys[: len(left_keys)]:
        return right
    if right_keys == left_keys[: len(right_keys)]:
        return left
    # They may represent distinct consecutive portions of the same browser chat.
    return left + right


def conversation_text(history, current_message: str) -> str:
    messages = normalize_history(history) + [{"role": "user", "content": current_message}]
    return "\n".join(
        f"{item.get('role', 'unknown').title()}: {item.get('content', '')}"
        for item in messages[-20:]
    )


def recent_visitor_messages(history, current_message: str, limit: int = 3) -> list[str]:
    messages = [
        item["content"]
        for item in normalize_history(history)
        if item["role"] == "user"
    ]
    messages.append(current_message)
    return messages[-limit:]


def pending_question_from_history(history) -> str | None:
    """Recover the unanswered question when refresh replaced the Gradio session."""
    messages = normalize_history(history)
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if item["role"] != "assistant":
            continue
        content = item["content"].replace("\u200b", "")
        if "Please reply **yes** or **no**" not in content:
            continue
        for earlier in reversed(messages[:index]):
            if earlier["role"] == "user" and not CONTACT_CONSENT_PATTERN.fullmatch(
                earlier["content"]
            ):
                return earlier["content"]
    return None


def social_message_response(message: str, session_key: str) -> str | None:
    """Handle greetings and visitor introductions without model escalation."""
    introduction = VISITOR_INTRO_PATTERN.fullmatch(message)
    if introduction:
        name = " ".join(introduction.group(1).strip().split()).rstrip(".!,'-")
        VISITOR_NAMES[session_key] = name
        return (
            f"Hello, {name}! Nice to meet you. I’m Hemanth’s AI digital twin. "
            "You can ask me about his engineering experience, AI projects, skills, "
            "architecture work, or verified personal interests."
        )
    if GREETING_PATTERN.fullmatch(message):
        return (
            "Hello! I’m Hemanth’s AI digital twin. Ask me about his engineering "
            "experience, AI projects, skills, architecture work, or verified personal interests."
        )
    return None


def sanitize_input(message) -> str:
    if not isinstance(message, str):
        return ""
    normalized = unicodedata.normalize("NFKC", message)
    normalized = "".join(
        character
        for character in normalized
        if character in "\n\t" or unicodedata.category(character) != "Cc"
    )
    normalized = re.sub(r"(.)\1{20,}", lambda match: match.group(1) * 10, normalized)
    return normalized.strip()[:MAX_INPUT_CHARACTERS]


def apply_local_input_guardrails(
    message, request: gr.Request | None
) -> tuple[bool, str, str | None, int]:
    """Apply free deterministic checks before making any model request."""
    clean_message = sanitize_input(message)
    if not clean_message:
        return False, clean_message, "Please enter a question.", 0
    if len(message) > MAX_INPUT_CHARACTERS:
        return (
            False,
            clean_message,
            f"Please keep each message under {MAX_INPUT_CHARACTERS} characters.",
            0,
        )
    if clean_message.count("?") > 5:
        return False, clean_message, "Please ask no more than five questions at a time.", 0
    if ADULT_CONTENT_PATTERN.search(clean_message):
        return (
            False,
            clean_message,
            "This chat window is for respectful personal and professional questions "
            "about Hemanth. I can't help with explicit, adult, or unethical sexual content.",
            0,
        )
    if PROMPT_ATTACK_PATTERN.search(clean_message):
        return (
            False,
            clean_message,
            "I can't reveal or override private instructions. Ask me about Hemanth's "
            "experience, AI work, projects, or safe personal interests instead.",
            0,
        )

    identity = request_identity(request)
    now = time.monotonic()
    with RATE_LIMIT_LOCK:
        timestamps = RATE_LIMITS[identity]
        while timestamps and now - timestamps[0] > 3600:
            timestamps.popleft()
        recent_count = sum(
            now - timestamp <= RATE_LIMIT_WINDOW_SECONDS for timestamp in timestamps
        )
        if recent_count >= RATE_LIMIT_WINDOW_MAX or len(timestamps) >= RATE_LIMIT_HOUR_MAX:
            return (
                False,
                clean_message,
                "You have sent several messages quickly. Please wait a few minutes "
                "before trying again.",
                len(timestamps),
            )
        timestamps.append(now)
        return True, clean_message, None, len(timestamps)


def passes_input_guardrail(message: str, history) -> bool:
    """Personal and professional questions pass after deterministic safety checks."""
    return True


def needs_human_followup(message: str, history) -> bool:
    # The verified resumes extensively cover these professional areas. Broad
    # questions about them should reach the grounded answering model instead of
    # being preemptively escalated by a smaller classifier.
    if KNOWN_PROFESSIONAL_TOPIC_PATTERN.search(message):
        return False
    prompt = f"""Return JSON only:
{{"about_hemanth": true or false, "answerable_from_profile": true or false}}.

Set about_hemanth=true whenever the visitor asks for a factual or preference-based fact
about Hemanth. A message that names Hemanth and asks what he knows, likes, wants, did,
built, prefers, or is available for is about Hemanth. Pronoun-based follow-ups can also be
about Hemanth when conversation history makes that clear. Set it false only for a delivery
choice, an email address, an acknowledgement, or a general question not about Hemanth.

Set answerable_from_profile=true only when a verified source directly states enough to
answer the exact question. Do not infer one preference from another or fill missing facts
from general knowledge. Follow source precedence for conflicts.

Examples:
- "What movie does Hemanth like most?" -> about_hemanth=true,
  answerable_from_profile=false unless a favorite movie is explicitly stated.
- "Is Hemanth available tomorrow?" -> true, false unless current availability is stated.
- "What are Hemanth's strongest engineering skills?" -> true, true when the resumes list them.
- "Wait in chat" -> false, false.
- "What is RAG?" -> false, false.

{get_profile_context()}"""
    try:
        response = openai.chat.completions.create(
            model=GUARD_MODEL,
            messages=[{"role": "system", "content": prompt}]
            + normalize_history(history)[-8:]
            + [{"role": "user", "content": message}],
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        return bool(result.get("about_hemanth")) and not bool(
            result.get("answerable_from_profile")
        )
    except Exception as error:
        print(f"Human-follow-up classifier failed: {error}")
        return False


def apply_output_guardrail(
    user_message: str,
    answer: str,
    trusted_owner: bool = False,
    tool_context: str = "",
) -> tuple[str, bool]:
    """Return model and owner answers unchanged; input checks run separately."""
    return answer, False


def resolve_original_question(
    history_messages: list[dict[str, str]], current_message: str, fallback: str
) -> str:
    prompt = (
        "Return only the concise factual question the visitor wants the real Hemanth "
        "to answer. Never return a channel choice such as email, wait in chat, or yes. "
        "Do not answer the question."
    )
    try:
        response = openai.chat.completions.create(
            model=GUARD_MODEL,
            messages=[{"role": "system", "content": prompt}]
            + history_messages[-10:]
            + [{"role": "user", "content": current_message}],
        )
        result = (response.choices[0].message.content or "").strip()
        return result or fallback
    except Exception as error:
        print(f"Question extraction failed: {error}")
        return fallback


def create_chat_response(messages):
    try:
        return openai.chat.completions.create(
            model=CHAT_MODEL, messages=messages, tools=CHAT_TOOLS
        )
    except Exception as error:
        print(f"Chat model request failed: {error}")
        return None


def add_contact_reminder(answer: str, message_count: int) -> str:
    if message_count >= 4 and (message_count - 4) % 3 == 0:
        return (
            answer
            + "\n\nIf you'd like to continue with the real Hemanth, share your name, "
            "email address, organization, and a short note about what you'd like to discuss."
        )
    return answer


def visitor_name_from_message(message: str, email: str | None = None) -> str | None:
    if CONTACT_CONSENT_PATTERN.fullmatch(message) or CONTACT_DECLINE_PATTERN.fullmatch(message):
        return None
    explicit_name = re.search(
        r"\b(?:my name is|name\s*:|name|this is|i am|i'm)\s+"
        r"([A-Za-z][A-Za-z .'-]{0,49}?)(?=\s*(?:,|;|\band\b|\bemail\b|$))",
        message,
        re.IGNORECASE,
    )
    if explicit_name:
        return " ".join(explicit_name.group(1).strip().split()).rstrip(".!,'-")
    introduction = VISITOR_INTRO_PATTERN.fullmatch(message)
    if introduction:
        return " ".join(introduction.group(1).strip().split()).rstrip(".!,'-")
    candidate = message.replace(email, " ") if email else message
    candidate = re.sub(
        r"\b(?:my name is|name|this is|i am|i'm|email|is|and|hello|hi|from)\b",
        " ",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = " ".join(candidate.strip(" ,.!:-").split())
    if candidate and len(candidate.split()) <= 5 and "@" not in candidate:
        return candidate
    return None


def monitor_human_reply(
    request_id: str,
    question: str,
    session_key: str,
    visitor_name: str,
    visitor_email: str | None = None,
    visitor_details: str = "",
    wait_seconds: int = HUMAN_REPLY_WAIT_SECONDS,
):
    status = HUMAN_REPLY_STATUS
    yield status
    poll_count = max(
        1, (wait_seconds + HUMAN_REPLY_POLL_SECONDS - 1) // HUMAN_REPLY_POLL_SECONDS
    )
    for _ in range(poll_count):
        time.sleep(HUMAN_REPLY_POLL_SECONDS)
        owner_answer = fetch_hemanth_reply(request_id)
        if owner_answer:
            completed = complete_request(
                request_id, owner_answer
            )
            if completed and completed.get("answer"):
                with PENDING_FOLLOWUP_LOCK:
                    PENDING_FOLLOWUPS.pop(session_key, None)
                yield status + (
                    f"\n\n### Here is the response from the actual Hemanth\n"
                    f"{completed['answer']}\n\n"
                    "Is there anything else I can help you with?"
                )
                mark_chat_answer_displayed(request_id)
            else:
                yield status + (
                    "\n\nHemanth replied, but I could not match the response to the "
                    "saved request. Please try again."
                )
            return
        # Keep the original waiting bubble unchanged between polls. Re-yielding
        # invisible markers made Gradio repaint and briefly erase the message.

    mark_request_awaiting_visitor_email(request_id)
    if visitor_email:
        route_request_to_visitor_email(
            request_id,
            visitor_email,
            visitor_name,
            visitor_details,
        )
        with PENDING_FOLLOWUP_LOCK:
            PENDING_FOLLOWUPS.pop(session_key, None)
        yield status + (
            f"\n\nIt looks like Hemanth is busy right now. Your details are recorded, "
            f"and he will respond to **{visitor_email}** by the end of the day. "
            "Is there anything else I can help you with?"
        )
        return

    with PENDING_FOLLOWUP_LOCK:
        PENDING_FOLLOWUPS[session_key] = {
            "question": question,
            "request_id": request_id,
            "created_at": time.monotonic(),
            "name": visitor_name,
            "awaiting_visitor_email": True,
        }
    yield status + (
        "\n\nIt looks like Hemanth is busy right now. Please drop your name and "
        "email address. Hemanth will respond to your personal email by the end of the day."
    )


def _chat_impl(message, history, request: gr.Request):
    """Single guarded chat entry point used by Gradio."""
    allowed, message, rejection, message_count = apply_local_input_guardrails(
        message, request
    )
    if not allowed:
        yield rejection
        return

    # Channel selections are continuations of the preceding unknown question,
    # not standalone questions for the model to interpret.
    session_key = session_identity(request)
    with PENDING_FOLLOWUP_LOCK:
        pending = PENDING_FOLLOWUPS.get(session_key)
        if pending and time.monotonic() - float(pending["created_at"]) > 1800:
            PENDING_FOLLOWUPS.pop(session_key, None)
            pending = None

    if not pending and CONTACT_CONSENT_PATTERN.fullmatch(message):
        restored_question = pending_question_from_history(history)
        if restored_question:
            pending = {
                "question": restored_question,
                "created_at": time.monotonic(),
                "name": VISITOR_NAMES.get(session_key, "Name not provided"),
                "awaiting_contact_consent": True,
            }
            with PENDING_FOLLOWUP_LOCK:
                PENDING_FOLLOWUPS[session_key] = pending

    # A browser refresh creates a new Gradio session. Reattach the newest
    # persisted follow-up using the stable visitor identity so waiting, timeout,
    # and completed replies are not lost with the old websocket connection.
    if not pending:
        recovered = latest_followup_for_visitor(browser_identity(request))
        if recovered:
            request_id = str(recovered["id"])
            recovered_answer = recovered.get("answer")
            if recovered_answer:
                yield (
                    "### Here is the response from the actual Hemanth\n"
                    f"{recovered_answer}\n\n"
                    "Is there anything else I can help you with?"
                )
                mark_chat_answer_displayed(request_id)
                return
            if recovered.get("awaiting_visitor_email"):
                pending = {
                    "question": str(recovered["question"]),
                    "request_id": request_id,
                    "created_at": time.monotonic(),
                    "name": str(recovered.get("name") or "Name not provided"),
                    "awaiting_visitor_email": True,
                }
                with PENDING_FOLLOWUP_LOCK:
                    PENDING_FOLLOWUPS[session_key] = pending
            else:
                remaining = HUMAN_REPLY_WAIT_SECONDS
                deadline_text = recovered.get("chat_wait_deadline")
                if deadline_text:
                    try:
                        remaining = max(
                            1,
                            int(
                                (
                                    datetime.fromisoformat(str(deadline_text))
                                    - datetime.now(timezone.utc)
                                ).total_seconds()
                            ),
                        )
                    except (TypeError, ValueError):
                        pass
                yield from monitor_human_reply(
                    request_id,
                    str(recovered["question"]),
                    session_key,
                    str(recovered.get("name") or "Name not provided"),
                    wait_seconds=remaining,
                )
                return

    if pending and pending.get("awaiting_visitor_email"):
        email_match = VISITOR_EMAIL_PATTERN.search(message)
        introduction = VISITOR_INTRO_PATTERN.fullmatch(message)
        if introduction and not email_match:
            name = " ".join(introduction.group(1).strip().split()).rstrip(".!,'-")
            VISITOR_NAMES[session_key] = name
            with PENDING_FOLLOWUP_LOCK:
                pending["name"] = name
            yield (
                f"Thanks, {name}. Please send your email address so Hemanth can "
                "respond to you personally by the end of the day."
            )
            return
        if not email_match:
            yield (
                "Please send your name and email address. Hemanth will respond to "
                "your personal email by the end of the day."
            )
            return

        visitor_email = email_match.group(0).rstrip(".,;:!?")
        name = str(
            pending.get("name")
            or VISITOR_NAMES.get(session_key)
            or "Name not provided"
        )
        candidate_name = visitor_name_from_message(message, email_match.group(0))
        if candidate_name:
            name = candidate_name
            VISITOR_NAMES[session_key] = name

        result = route_request_to_visitor_email(
            str(pending["request_id"]), visitor_email, name, message
        )
        if result.get("recorded"):
            with PENDING_FOLLOWUP_LOCK:
                PENDING_FOLLOWUPS.pop(session_key, None)
            notification = result.get("contact_notification") or {}
            email_sent = bool((notification.get("email_delivery") or {}).get("delivered"))
            push_sent = bool((notification.get("pushover") or {}).get("delivered"))
            if email_sent and push_sent:
                delivery_note = "Hemanth was notified by email and Pushover."
            elif push_sent:
                delivery_note = "Email delivery had an issue, but Hemanth was notified by Pushover."
            elif email_sent:
                delivery_note = "Hemanth was notified by email."
            else:
                delivery_note = "Your details were saved, but notification delivery had an issue."
            yield (
                f"Thanks, {name}. Your details are recorded. {delivery_note} "
                f"He will respond to **{visitor_email}** by the end "
                "of the day. Is there anything else I can help you with?"
            )
        else:
            yield "I couldn't save that email address. Please check it and try again."
        return

    if pending and pending.get("awaiting_contact_consent"):
        email_match = VISITOR_EMAIL_PATTERN.search(message)
        supplied_name = visitor_name_from_message(
            message, email_match.group(0) if email_match else None
        )
        if supplied_name:
            VISITOR_NAMES[session_key] = supplied_name
            with PENDING_FOLLOWUP_LOCK:
                pending["name"] = supplied_name

        if email_match:
            visitor_email = email_match.group(0).rstrip(".,;:!?")
            name = str(
                supplied_name
                or pending.get("name")
                or VISITOR_NAMES.get(session_key)
                or "Name not provided"
            )
            with PENDING_FOLLOWUP_LOCK:
                pending["name"] = name
                pending["visitor_email"] = visitor_email
                pending["visitor_details"] = message
            yield (
                f"Thanks, {name}. I noted your details, but I have not contacted Hemanth "
                "yet. Would you like me to reach out to him by email and get the answer "
                "here in chat? Please reply **yes** or **no**."
            )
            return

        if CONTACT_DECLINE_PATTERN.fullmatch(message):
            with PENDING_FOLLOWUP_LOCK:
                PENDING_FOLLOWUPS.pop(session_key, None)
            yield "No problem. Feel free to ask another personal or professional question."
            return

        if CONTACT_CONSENT_PATTERN.fullmatch(message):
            name = str(
                pending.get("name")
                or VISITOR_NAMES.get(session_key)
                or "Name not provided"
            )
            # Yield the final waiting text before network I/O. The short pause lets
            # Gradio flush it to the browser so the response bubble never appears blank.
            yield HUMAN_REPLY_STATUS
            time.sleep(0.5)
            result = record_contact_request(
                question=str(pending["question"]),
                reply_channel="chat",
                name=name,
                recent_questions=recent_visitor_messages(history, message, limit=3),
                conversation=conversation_text(history, message),
                visitor_key=browser_identity(request),
            )
            if not (result.get("email_delivery") or {}).get("delivered"):
                request_id = str(result.get("request_id") or "")
                push_sent = bool((result.get("pushover") or {}).get("delivered"))
                if request_id:
                    mark_request_awaiting_visitor_email(request_id)
                    with PENDING_FOLLOWUP_LOCK:
                        PENDING_FOLLOWUPS[session_key] = {
                            "question": str(pending["question"]),
                            "request_id": request_id,
                            "created_at": time.monotonic(),
                            "name": name,
                            "awaiting_visitor_email": True,
                        }
                push_note = (
                    "I notified him through Pushover."
                    if push_sent
                    else "I also could not deliver the Pushover notification."
                )
                yield (
                    "There was an issue emailing Hemanth. "
                    f"{push_note} Please drop your name and email address; "
                    "Hemanth will personally email you by the end of the day."
                )
                return
            request_id = str(result["request_id"])
            with PENDING_FOLLOWUP_LOCK:
                pending["request_id"] = request_id
                pending["awaiting_contact_consent"] = False
            yield from monitor_human_reply(
                request_id,
                str(pending["question"]),
                session_key,
                name,
                pending.get("visitor_email"),
                str(pending.get("visitor_details") or ""),
            )
            return

        if supplied_name:
            yield (
                f"Thanks, {supplied_name}. Would you like me to reach out to the real "
                "Hemanth by email and get the answer here in chat? Please reply **yes** or **no**."
            )
        else:
            yield (
                "Please reply **yes** if you want me to email the real Hemanth and "
                "get the answer here in chat, or **no** to continue without contacting him."
            )
        return

    social_response = social_message_response(message, session_key)
    if social_response:
        yield social_response
        return

    if TIME_SENSITIVE_AVAILABILITY_PATTERN.search(message):
        record_unknown_question(message)
        with PENDING_FOLLOWUP_LOCK:
            PENDING_FOLLOWUPS[session_key] = {
                "question": message,
                "created_at": time.monotonic(),
                "name": VISITOR_NAMES.get(session_key, "Name not provided"),
                "awaiting_contact_consent": True,
                "time_sensitive": True,
            }
        yield (
            "Hemanth’s availability changes in real time, so I won’t rely on an older "
            "answer. Would you like me to reach out to the real Hemanth by email and "
            "get his current answer here in chat? Please reply **yes** or **no**."
        )
        return

    if False and pending and re.fullmatch(  # Legacy channel-choice flow is disabled.
        r"(?:yes,?\s*)?(?:please\s*)?(?:wait|reply|respond)(?:\s+for me)?\s+(?:in|here in)\s+(?:the\s+)?chat(?:\s+window)?[.!]?",
        message,
        re.IGNORECASE,
    ):
        result = record_contact_request(
            question=str(pending["question"]),
            reply_channel="chat",
            name=str(
                pending.get("name")
                or VISITOR_NAMES.get(session_key)
                or "Name not provided"
            ),
            recent_questions=[str(pending["question"])],
            conversation=conversation_text(history, message),
        )
        with PENDING_FOLLOWUP_LOCK:
            PENDING_FOLLOWUPS.pop(session_key, None)

        request_id = result.get("request_id")
        email_sent = bool((result.get("email_delivery") or {}).get("delivered"))
        push_sent = bool((result.get("pushover") or {}).get("delivered"))
        if not email_sent:
            extra = (
                " A Pushover alert was delivered, but the email was not."
                if push_sent
                else " Neither email nor Pushover could be delivered."
            )
            yield (
                "I saved your chat follow-up request, but Gmail could not send the "
                "notification because its authorization needs to be renewed."
                f"{extra} Hemanth must run `uv run .\\1_foundations\\twin\\setup_gmail.py` "
                "and restart the app."
            )
            return

        answer = (
            "Your request was emailed to Hemanth. I’ll check this chat for his reply "
            "every 10 seconds for up to 5 minutes."
        )
        yield answer
        for _ in range(30):
            owner_answer = fetch_hemanth_reply(request_id)
            if owner_answer:
                completed = complete_request(
                    request_id, owner_answer
                )
                if completed and completed.get("answer"):
                    yield answer + f"\n\n### Update from Hemanth\n{completed['answer']}"
                else:
                    yield answer + (
                        "\n\nHemanth replied, but I could not match the response to "
                        "the saved request. Please try again."
                    )
                return
            time.sleep(10)
        yield answer + "\n\nI have not received Hemanth's reply yet. Please try again shortly."
        return

    if not passes_input_guardrail(message, history):
        yield (
            "This chat window is for respectful personal and professional questions "
            "about Hemanth. I can't help with explicit or unsafe requests."
        )
        return

    messages = [
        {"role": "system", "content": get_system_prompt()},
        *normalize_history(history),
        {"role": "user", "content": message},
    ]
    response = create_chat_response(messages)
    if response is None:
        yield "I'm temporarily unable to answer. Please try again in a moment."
        return

    request_id = None
    reply_channel = None
    reply_tracking_ready = False
    unknown_recorded = False
    unknown_result = None
    all_outcomes = []
    tool_rounds = 0

    while response.choices[0].finish_reason == "tool_calls":
        tool_rounds += 1
        if tool_rounds > MAX_TOOL_ROUNDS:
            yield "I couldn't complete the request because too many tool actions were requested."
            return
        assistant_message = response.choices[0].message
        messages.append(assistant_message)
        tool_messages, outcomes = handle_tool_calls(
            assistant_message.tool_calls,
            history,
            message,
            question_resolver=resolve_original_question,
        )
        messages.extend(tool_messages)
        all_outcomes.extend(outcomes)
        for outcome in outcomes:
            result = outcome["result"]
            if outcome["tool_name"] == "record_unknown_question" and result.get("recorded"):
                unknown_recorded = True
                unknown_result = result
                with PENDING_FOLLOWUP_LOCK:
                    PENDING_FOLLOWUPS[session_key] = {
                        "question": message,
                        "created_at": time.monotonic(),
                        "name": VISITOR_NAMES.get(session_key, "Name not provided"),
                        "awaiting_contact_consent": True,
                    }
            request_id = result.get("request_id") or request_id
            reply_channel = result.get("reply_channel") or reply_channel
            email_delivery = result.get("email_delivery") or {}
            reply_tracking_ready = bool(email_delivery.get("delivered")) or reply_tracking_ready
        response = create_chat_response(messages)
        if response is None:
            yield (
                "I completed the available tool actions, but the response service is "
                "temporarily unavailable. Please try again."
            )
            return

    answer = response.choices[0].message.content or "I wasn't able to produce a response."
    answer, output_blocked = apply_output_guardrail(
        message, answer, tool_context=json.dumps(all_outcomes)
    )
    missing_answer = bool(UNKNOWN_ANSWER_PATTERN.search(answer))
    explicitly_about_hemanth = bool(HEMANTH_REFERENCE_PATTERN.search(message))
    if (
        not unknown_recorded
        and (output_blocked or missing_answer)
        and (
            explicitly_about_hemanth
            or needs_human_followup(message, history)
        )
    ):
        try:
            unknown_result = record_unknown_question(message)
            unknown_recorded = bool(unknown_result.get("recorded"))
            if unknown_recorded:
                request_id = unknown_result.get("request_id") or request_id
                reply_channel = unknown_result.get("reply_channel") or reply_channel
                reply_tracking_ready = bool(
                    (unknown_result.get("email_delivery") or {}).get("delivered")
                ) or reply_tracking_ready
                with PENDING_FOLLOWUP_LOCK:
                    PENDING_FOLLOWUPS[session_key] = {
                        "question": message,
                        "created_at": time.monotonic(),
                        "name": VISITOR_NAMES.get(session_key, "Name not provided"),
                        "awaiting_contact_consent": True,
                    }
        except Exception as error:
            print(f"Unable to escalate blocked output: {error}")

    if unknown_recorded:
        answer = (
            "I don’t know the answer to that from Hemanth’s verified information. "
            "Would you like me to reach out to the real Hemanth by email and get the "
            "answer here in chat? Please reply **yes** or **no**."
        )

    yield answer

    if request_id and reply_channel == "chat" and reply_tracking_ready:
        for _ in range(30):
            time.sleep(10)
            owner_answer = fetch_hemanth_reply(request_id)
            if owner_answer:
                completed = complete_request(
                    request_id, owner_answer
                )
                if completed and completed.get("answer"):
                    with PENDING_FOLLOWUP_LOCK:
                        PENDING_FOLLOWUPS.pop(session_key, None)
                    yield answer + f"\n\n### Here is the response from the actual Hemanth\n{completed['answer']}"
                else:
                    yield (
                        answer
                        + "\n\nHemanth replied, but I could not match the response to "
                        "the saved request. Please try again."
                    )
                return
        mark_request_awaiting_visitor_email(request_id)
        with PENDING_FOLLOWUP_LOCK:
            PENDING_FOLLOWUPS[session_key] = {
                "question": message,
                "request_id": request_id,
                "created_at": time.monotonic(),
                "name": VISITOR_NAMES.get(session_key, "Name not provided"),
                "awaiting_visitor_email": True,
            }
        yield answer + (
            "\n\nIt seems Hemanth is busy right now. Please drop your name and email "
            "address. Hemanth will respond to your personal email by the end of the day."
        )
        return

    if unknown_recorded and request_id:
        mark_request_awaiting_visitor_email(request_id)
        with PENDING_FOLLOWUP_LOCK:
            pending = PENDING_FOLLOWUPS.get(session_key, {})
            pending["awaiting_visitor_email"] = True
            pending["request_id"] = request_id
            PENDING_FOLLOWUPS[session_key] = pending
        yield answer + (
            "\n\nI could not start the email reply monitor. Please drop your name and "
            "email address so Hemanth can respond personally."
        )


def chat(message, history, request: gr.Request):
    """Persist every streamed response so an in-progress chat survives refresh."""
    browser_key = browser_identity(request)
    active_session = session_identity(request)
    base_messages = merge_conversation_history(
        load_browser_chat(browser_key), normalize_history(history)
    )
    current_user = {"role": "user", "content": message}
    if not base_messages or _message_identity(base_messages[-1]) != _message_identity(
        current_user
    ):
        base_messages.append(current_user)
    with ACTIVE_BROWSER_SESSIONS_LOCK:
        ACTIVE_BROWSER_SESSIONS[browser_key] = active_session
    try:
        for answer in _chat_impl(message, history, request):
            save_browser_chat(
                browser_key,
                base_messages + [{"role": "assistant", "content": str(answer)}],
            )
            yield answer
    finally:
        with ACTIVE_BROWSER_SESSIONS_LOCK:
            if ACTIVE_BROWSER_SESSIONS.get(browser_key) == active_session:
                ACTIVE_BROWSER_SESSIONS.pop(browser_key, None)


def restore_browser_chat(saved_conversations, request: gr.Request):
    browser_key = browser_identity(request)
    messages = load_browser_chat(browser_key)
    if saved_conversations:
        # Gradio's saved conversation contains the completed turns preceding an
        # in-progress server stream, so it belongs before any server-only tail.
        messages = merge_conversation_history(saved_conversations[0], messages)
    if messages:
        save_browser_chat(browser_key, messages)
    return messages, messages


def merge_followup_response(
    messages: list[dict[str, str]], response: str
) -> tuple[list[dict[str, str]], bool]:
    """Replace the waiting bubble and remove duplicate timer-generated copies."""
    changed = False
    while len(messages) >= 2:
        last = messages[-1]
        previous = messages[-2]
        if last.get("role") != "assistant" or previous.get("role") != "assistant":
            break
        last_text = last.get("content", "").replace("\u200b", "")
        previous_text = previous.get("content", "").replace("\u200b", "")
        if not (
            last_text.startswith(HUMAN_REPLY_STATUS)
            and previous_text.startswith(HUMAN_REPLY_STATUS)
        ):
            break
        messages.pop()
        changed = True

    if messages and messages[-1].get("role") == "assistant":
        raw_last_text = messages[-1].get("content", "")
        last_text = raw_last_text.replace("\u200b", "")
        if last_text == response:
            if raw_last_text != response:
                messages[-1] = {"role": "assistant", "content": response}
                changed = True
            return messages, changed
        if last_text.startswith(HUMAN_REPLY_STATUS):
            messages[-1] = {"role": "assistant", "content": response}
            return messages, True
    messages.append({"role": "assistant", "content": response})
    return messages, True


def poll_browser_followup(request: gr.Request):
    """Keep a refreshed browser synchronized with its persisted human follow-up."""
    browser_key = browser_identity(request)
    with ACTIVE_BROWSER_SESSIONS_LOCK:
        if ACTIVE_BROWSER_SESSIONS.get(browser_key) == session_identity(request):
            return gr.skip(), gr.skip()
    request_data = latest_followup_for_visitor(browser_key)
    if not request_data:
        return gr.skip(), gr.skip()

    messages = load_browser_chat(browser_key)
    request_id = str(request_data["id"])
    answer = request_data.get("answer")
    if not answer and not request_data.get("awaiting_visitor_email"):
        answer = fetch_hemanth_reply(request_id)
        if answer:
            completed = complete_request(request_id, answer)
            answer = completed.get("answer") if completed else None

    if answer:
        response = (
            f"{HUMAN_REPLY_STATUS}\n\n"
            "### Here is the response from the actual Hemanth\n"
            f"{answer}\n\nIs there anything else I can help you with?"
        )
        messages, changed = merge_followup_response(messages, response)
        if changed:
            save_browser_chat(browser_key, messages)
        mark_chat_answer_displayed(request_id)
        return (messages, messages) if changed else (gr.skip(), gr.skip())

    deadline_expired = bool(request_data.get("awaiting_visitor_email"))
    deadline_text = request_data.get("chat_wait_deadline")
    if deadline_text and not deadline_expired:
        try:
            deadline_expired = datetime.now(timezone.utc) >= datetime.fromisoformat(
                str(deadline_text)
            )
        except (TypeError, ValueError):
            pass
    if deadline_expired:
        mark_request_awaiting_visitor_email(request_id)
        response = (
            f"{HUMAN_REPLY_STATUS}\n\n"
            "It looks like Hemanth is busy right now. Please drop your name and "
            "email address. Hemanth will respond to your personal email by the end of the day."
        )
    else:
        response = HUMAN_REPLY_STATUS

    messages, changed = merge_followup_response(messages, response)
    if changed:
        save_browser_chat(browser_key, messages)
        return messages, messages
    return gr.skip(), gr.skip()


def create_demo() -> gr.ChatInterface:
    start_reply_watcher()
    chatbot = gr.Chatbot(
        show_label=False,
        height=560,
        autoscroll=False,
        placeholder=(
            "Ask about Hemanth's professional background, projects, skills, or personal life."
        ),
    )
    demo = gr.ChatInterface(
        fn=chat,
        examples=EXAMPLES,
        title="Hemanth · AI Digital Twin",
        description=(
            "Ask respectful professional or personal questions. Answers about Hemanth "
            "are grounded in verified information."
        ),
        chatbot=chatbot,
        analytics_enabled=False,
        autofocus=False,
        autoscroll=False,
        fill_height=False,
        show_progress="minimal",
        save_history=True,
        concurrency_limit=8,
        api_name="chat",
        api_visibility="private",
    )
    with demo:
        demo.load(
            fn=restore_browser_chat,
            inputs=[demo.saved_conversations],
            outputs=[demo.chatbot, demo.chatbot_state],
            queue=False,
        )
        followup_timer = gr.Timer(HUMAN_REPLY_POLL_SECONDS)
        followup_timer.tick(
            fn=poll_browser_followup,
            inputs=None,
            outputs=[demo.chatbot, demo.chatbot_state],
            queue=False,
            show_progress="hidden",
        )
    return demo


if __name__ == "__main__":
    print(f"Starting Hemanth Digital Twin app version {APP_VERSION}")
    demo = create_demo()
    is_hosted = bool(os.getenv("RENDER") or os.getenv("SPACE_ID"))
    default_inbrowser = "false" if is_hosted else "true"
    server_name = os.getenv(
        "GRADIO_SERVER_NAME", "0.0.0.0" if is_hosted else "127.0.0.1"
    )
    server_port = int(os.getenv("PORT", os.getenv("GRADIO_SERVER_PORT", "7860")))
    demo.queue(default_concurrency_limit=16, max_size=64).launch(
        server_name=server_name,
        server_port=server_port,
        inbrowser=os.getenv("GRADIO_INBROWSER", default_inbrowser).lower() == "true",
        share=os.getenv("GRADIO_SHARE", "false").lower() == "true",
        footer_links=[],
        theme=gr.themes.Base(),
        css=CSS,
        js=JS,
    )
