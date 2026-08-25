import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


class LarkEventError(ValueError):
    pass


@dataclass(frozen=True)
class LarkReplyEvent:
    contact_id: str
    content: str
    platform_message_id: Optional[str]
    event_id: Optional[str]


def is_lark_url_verification(payload: Dict[str, Any]) -> bool:
    if payload.get("type") == "url_verification":
        return True
    header = payload.get("header") or {}
    return header.get("event_type") == "url_verification"


def build_lark_challenge_response(
    payload: Dict[str, Any],
    expected_token: Optional[str],
) -> Dict[str, str]:
    _reject_encrypted_payload(payload)
    _verify_lark_token(payload, expected_token)

    challenge = payload.get("challenge")
    if challenge is None:
        event = payload.get("event") or {}
        challenge = event.get("challenge")
    if not challenge:
        raise LarkEventError("Lark url_verification payload missing challenge")
    return {"challenge": str(challenge)}


def parse_lark_reply_event(
    payload: Dict[str, Any],
    expected_token: Optional[str],
    receive_id_type: str,
) -> Optional[LarkReplyEvent]:
    _reject_encrypted_payload(payload)
    _verify_lark_token(payload, expected_token)

    event_type = _event_type(payload)
    if event_type != "im.message.receive_v1":
        return None

    event = payload.get("event") or {}
    sender = event.get("sender") or {}
    if sender.get("sender_type") == "app":
        return None

    message = event.get("message") or {}
    content = _extract_message_text(message)
    if not content:
        return None

    contact_id = _extract_contact_id(sender, receive_id_type)
    if not contact_id:
        raise LarkEventError(
            "Lark message event missing sender_id.{}".format(receive_id_type)
        )

    header = payload.get("header") or {}
    return LarkReplyEvent(
        contact_id=contact_id,
        content=content,
        platform_message_id=message.get("message_id"),
        event_id=header.get("event_id"),
    )


def _verify_lark_token(payload: Dict[str, Any], expected_token: Optional[str]) -> None:
    if not expected_token:
        return

    header = payload.get("header") or {}
    token = payload.get("token") or header.get("token")
    if token != expected_token:
        raise LarkEventError("Invalid Lark verification token")


def _reject_encrypted_payload(payload: Dict[str, Any]) -> None:
    if "encrypt" in payload:
        raise LarkEventError(
            "Encrypted Lark events are not supported yet. "
            "Disable event encryption in Lark for this MVP."
        )


def _event_type(payload: Dict[str, Any]) -> str:
    header = payload.get("header") or {}
    return str(header.get("event_type") or payload.get("type") or "")


def _extract_contact_id(sender: Dict[str, Any], receive_id_type: str) -> str:
    sender_id = sender.get("sender_id") or {}
    ordered_keys = [
        receive_id_type,
        "open_id",
        "user_id",
        "union_id",
    ]
    for key in ordered_keys:
        value = sender_id.get(key)
        if value:
            return str(value)
    return ""


def _extract_message_text(message: Dict[str, Any]) -> str:
    raw_content = message.get("content")
    if raw_content is None:
        return ""
    if isinstance(raw_content, str):
        try:
            decoded = json.loads(raw_content)
        except json.JSONDecodeError:
            return raw_content.strip()
    elif isinstance(raw_content, dict):
        decoded = raw_content
    else:
        return str(raw_content).strip()

    text = decoded.get("text")
    if isinstance(text, str):
        return text.strip()

    flattened = _flatten_rich_text(decoded)
    return flattened.strip()


def _flatten_rich_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_flatten_rich_text(item) for item in value)
    if isinstance(value, dict):
        if value.get("tag") == "text" and isinstance(value.get("text"), str):
            return value["text"]
        if value.get("tag") == "at" and isinstance(value.get("user_name"), str):
            return "@{}".format(value["user_name"])
        parts = []
        for key in ("title", "content", "elements"):
            if key in value:
                parts.append(_flatten_rich_text(value[key]))
        return "".join(parts)
    return ""
