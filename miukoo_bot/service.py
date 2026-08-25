import uuid
from datetime import timedelta
from typing import Any, Callable, Dict, List

from miukoo_bot.config import Settings
from miukoo_bot.db import SQLiteStore, TERMINAL_RECIPIENT_STATUSES
from miukoo_bot.importers import load_recipients_from_csv
from miukoo_bot.lark_events import (
    LarkEventError,
    build_lark_challenge_response,
    is_lark_url_verification,
    parse_lark_reply_event,
)
from miukoo_bot.messaging import MessageAdapter
from miukoo_bot.templates import TemplateStore
from miukoo_bot.time_utils import is_quiet_time, next_allowed_time, to_iso, utc_now


class ValidationError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class RateLimitError(RuntimeError):
    pass


class BotService:
    def __init__(
        self,
        store: SQLiteStore,
        templates: TemplateStore,
        adapter: MessageAdapter,
        settings: Settings,
        clock: Callable[[], Any] = utc_now,
    ) -> None:
        self.store = store
        self.templates = templates
        self.adapter = adapter
        self.settings = settings
        self.clock = clock

    def create_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_id = uuid.uuid4().hex
        now = self.clock()
        follow_up = self._normalize_follow_up(payload.get("follow_up") or {})

        task = {
            "id": task_id,
            "task_name": self._required_string(payload, "task_name"),
            "channel": payload.get("channel") or "mock",
            "message_type": self._required_string(payload, "message_type"),
            "status": "sending",
            "created_by": payload.get("created_by"),
            "created_at": to_iso(now),
            "deadline_at": payload.get("deadline_at"),
            "follow_up_enabled": follow_up["enabled"],
            "first_remind_after_minutes": follow_up["first_remind_after_minutes"],
            "remind_interval_minutes": follow_up["remind_interval_minutes"],
            "max_remind_times": follow_up["max_remind_times"],
            "stop_when_replied": follow_up["stop_when_replied"],
            "quiet_hours_start": follow_up["quiet_hours"]["start"],
            "quiet_hours_end": follow_up["quiet_hours"]["end"],
        }

        recipients = self._normalize_recipients(
            task_id,
            task["message_type"],
            self._load_raw_recipients(payload),
            follow_up["enabled"],
        )

        self.store.create_task(task, recipients)

        sent_count = 0
        for recipient in recipients:
            try:
                self._send_initial(task, recipient, now)
                sent_count += 1
            except Exception as exc:
                self._mark_send_failure(task, recipient, "initial", str(exc), now)

        self.store.update_task_status(task_id, "sent" if sent_count else "failed")
        self.store.refresh_task_status(task_id)
        created = self.store.get_task(task_id)
        if created is None:
            raise NotFoundError("Task was created but could not be loaded")
        return created

    def preview_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        follow_up = self._normalize_follow_up(payload.get("follow_up") or {})
        message_type = self._required_string(payload, "message_type")
        recipients = self._normalize_recipients(
            "preview",
            message_type,
            self._load_raw_recipients(payload),
            follow_up["enabled"],
        )

        preview_recipients = []
        for recipient in recipients:
            variables = self._message_variables(recipient)
            messages = {
                "initial": self.templates.render(message_type, "initial", variables),
            }
            if follow_up["enabled"]:
                messages["follow_up"] = self.templates.render(
                    message_type,
                    "follow_up",
                    variables,
                )
            preview_recipients.append(
                {
                    "bd_id": recipient["bd_id"],
                    "name": recipient["name"],
                    "contact_id": recipient["contact_id"],
                    "group": recipient.get("group"),
                    "messages": messages,
                }
            )

        return {
            "task_name": payload.get("task_name"),
            "channel": payload.get("channel") or "mock",
            "message_type": message_type,
            "recipient_count": len(preview_recipients),
            "follow_up": follow_up,
            "recipients": preview_recipients,
        }

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self.store.list_tasks()

    def get_task(self, task_id: str) -> Dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            raise NotFoundError("Task not found: {}".format(task_id))
        return task

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        if not self.store.cancel_task(task_id):
            raise NotFoundError("Task not found: {}".format(task_id))
        return self.get_task(task_id)

    def stop_recipient(self, task_id: str, recipient_id: str) -> Dict[str, Any]:
        if not self.store.stop_recipient(task_id, recipient_id):
            raise NotFoundError(
                "Recipient {} not found in task {}".format(recipient_id, task_id)
            )
        self.store.refresh_task_status(task_id)
        return self.get_task(task_id)

    def record_reply(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        content = payload.get("content") or payload.get("text")
        if content is None:
            raise ValidationError("content is required")

        recipient = self.store.find_recipient_for_reply(
            task_id=payload.get("task_id"),
            bd_id=payload.get("bd_id"),
            contact_id=payload.get("contact_id"),
        )
        if recipient is None:
            raise NotFoundError("No matching recipient for reply")

        received_at = to_iso(self.clock())
        self.store.record_reply(
            reply_id=uuid.uuid4().hex,
            recipient_id=recipient["id"],
            task_id=recipient["task_id"],
            bd_id=recipient["bd_id"],
            content=str(content),
            platform_message_id=payload.get("platform_message_id"),
            received_at=received_at,
        )
        self.store.refresh_task_status(recipient["task_id"])
        return self.get_task(recipient["task_id"])

    def handle_webhook(self, channel: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if channel in ("lark", "feishu"):
            return self.handle_lark_webhook(payload)

        payload = dict(payload)
        payload.setdefault("channel", channel)
        return self.record_reply(payload)

    def handle_lark_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if is_lark_url_verification(payload):
                return build_lark_challenge_response(
                    payload,
                    self.settings.lark_verification_token,
                )

            event = parse_lark_reply_event(
                payload,
                self.settings.lark_verification_token,
                self.settings.lark_receive_id_type,
            )
        except LarkEventError as exc:
            raise ValidationError(str(exc)) from exc

        if event is None:
            return {"code": 0, "ignored": True, "reason": "unsupported_lark_event"}

        try:
            task = self.record_reply(
                {
                    "contact_id": event.contact_id,
                    "content": event.content,
                    "platform_message_id": event.platform_message_id,
                }
            )
        except NotFoundError:
            return {
                "code": 0,
                "ignored": True,
                "reason": "no_matching_recipient",
                "contact_id": event.contact_id,
            }

        return {
            "code": 0,
            "recorded": True,
            "task_id": task["id"],
            "contact_id": event.contact_id,
            "event_id": event.event_id,
        }

    def process_follow_ups(self) -> Dict[str, Any]:
        now = self.clock()
        due_recipients = self.store.get_due_recipients(to_iso(now))
        result = {
            "checked_at": to_iso(now),
            "due_count": len(due_recipients),
            "sent_count": 0,
            "rescheduled_count": 0,
            "failed_count": 0,
        }

        for recipient in due_recipients:
            current = self.store.get_recipient(recipient["id"])
            if current is None:
                continue
            if current["status"] in TERMINAL_RECIPIENT_STATUSES or current["reply_count"] > 0:
                continue

            if is_quiet_time(
                now,
                recipient["quiet_hours_start"],
                recipient["quiet_hours_end"],
            ):
                allowed_at = next_allowed_time(
                    now,
                    recipient["quiet_hours_start"],
                    recipient["quiet_hours_end"],
                )
                self.store.reschedule_recipient(recipient["id"], to_iso(allowed_at))
                result["rescheduled_count"] += 1
                continue

            try:
                self._send_follow_up(recipient, now)
                result["sent_count"] += 1
            except Exception as exc:
                self._mark_send_failure(
                    {
                        "id": recipient["task_id"],
                        "channel": recipient["channel"],
                    },
                    {
                        "id": recipient["id"],
                        "task_id": recipient["task_id"],
                        "bd_id": recipient["bd_id"],
                    },
                    "follow_up",
                    str(exc),
                    now,
                )
                result["failed_count"] += 1

            self.store.refresh_task_status(recipient["task_id"])

        return result

    def _send_initial(
        self,
        task: Dict[str, Any],
        recipient: Dict[str, Any],
        now: Any,
    ) -> None:
        self._ensure_contact_daily_limit(recipient["contact_id"], now)
        variables = self._message_variables(recipient)
        content = self.templates.render(task["message_type"], "initial", variables)
        result = self.adapter.send(
            task["channel"],
            recipient["contact_id"],
            content,
            metadata={
                "task_id": task["id"],
                "recipient_id": recipient["id"],
                "message_kind": "initial",
            },
        )

        follow_up_enabled = (
            task["follow_up_enabled"] and task["max_remind_times"] > 0
        )
        next_remind_at = None
        status = "completed"
        if follow_up_enabled:
            next_remind_at = to_iso(
                now + timedelta(minutes=task["first_remind_after_minutes"])
            )
            status = "sent"

        self.store.log_message(
            {
                "id": uuid.uuid4().hex,
                "task_id": task["id"],
                "recipient_id": recipient["id"],
                "bd_id": recipient["bd_id"],
                "message_kind": "initial",
                "content": content,
                "platform_message_id": result.platform_message_id,
                "status": result.status,
                "sent_at": to_iso(now),
            }
        )
        self.store.mark_recipient_sent(
            recipient["id"],
            status,
            to_iso(now),
            next_remind_at,
        )

    def _send_follow_up(self, recipient: Dict[str, Any], now: Any) -> None:
        self._ensure_contact_daily_limit(recipient["contact_id"], now)
        variables = self._message_variables(recipient)
        content = self.templates.render(recipient["message_type"], "follow_up", variables)
        result = self.adapter.send(
            recipient["channel"],
            recipient["contact_id"],
            content,
            metadata={
                "task_id": recipient["task_id"],
                "recipient_id": recipient["id"],
                "message_kind": "follow_up",
            },
        )

        next_remind_count = recipient["remind_count"] + 1
        has_more_reminders = next_remind_count < recipient["max_remind_times"]
        next_remind_at = None
        status = "completed"
        if has_more_reminders:
            next_remind_at = to_iso(
                now + timedelta(minutes=recipient["remind_interval_minutes"])
            )
            status = "followed_up"

        self.store.log_message(
            {
                "id": uuid.uuid4().hex,
                "task_id": recipient["task_id"],
                "recipient_id": recipient["id"],
                "bd_id": recipient["bd_id"],
                "message_kind": "follow_up",
                "content": content,
                "platform_message_id": result.platform_message_id,
                "status": result.status,
                "sent_at": to_iso(now),
            }
        )
        self.store.mark_recipient_followed_up(
            recipient["id"],
            status,
            to_iso(now),
            next_remind_at,
        )

    def _mark_send_failure(
        self,
        task: Dict[str, Any],
        recipient: Dict[str, Any],
        message_kind: str,
        error: str,
        now: Any,
    ) -> None:
        self.store.log_message(
            {
                "id": uuid.uuid4().hex,
                "task_id": task["id"],
                "recipient_id": recipient["id"],
                "bd_id": recipient["bd_id"],
                "message_kind": message_kind,
                "content": "",
                "status": "failed",
                "error": error,
                "sent_at": to_iso(now),
            }
        )
        self.store.mark_recipient_failed(recipient["id"], error)

    def _ensure_contact_daily_limit(self, contact_id: str, now: Any) -> None:
        limit = self.settings.daily_message_limit_per_contact
        if limit <= 0:
            return
        since = now - timedelta(days=1)
        sent_count = self.store.count_sent_messages_to_contact(contact_id, to_iso(since))
        if sent_count >= limit:
            raise RateLimitError(
                "Daily message limit reached for contact_id {}: {}/{}".format(
                    contact_id,
                    sent_count,
                    limit,
                )
            )

    def _normalize_follow_up(self, follow_up: Dict[str, Any]) -> Dict[str, Any]:
        quiet_hours = follow_up.get("quiet_hours") or {}
        return {
            "enabled": bool(follow_up.get("enabled", True)),
            "first_remind_after_minutes": int(
                follow_up.get(
                    "first_remind_after_minutes",
                    self.settings.default_first_remind_after_minutes,
                )
            ),
            "remind_interval_minutes": int(
                follow_up.get(
                    "remind_interval_minutes",
                    self.settings.default_remind_interval_minutes,
                )
            ),
            "max_remind_times": int(
                follow_up.get("max_remind_times", self.settings.default_max_remind_times)
            ),
            "stop_when_replied": bool(follow_up.get("stop_when_replied", True)),
            "quiet_hours": {
                "start": quiet_hours.get("start", self.settings.quiet_hours_start),
                "end": quiet_hours.get("end", self.settings.quiet_hours_end),
            },
        }

    def _normalize_recipients(
        self,
        task_id: str,
        message_type: str,
        raw_recipients: Any,
        follow_up_enabled: bool,
    ) -> List[Dict[str, Any]]:
        if not isinstance(raw_recipients, list) or not raw_recipients:
            raise ValidationError("recipients must be a non-empty list")

        recipients = []
        for index, raw in enumerate(raw_recipients):
            if not isinstance(raw, dict):
                raise ValidationError("recipient at index {} must be an object".format(index))

            bd_id = raw.get("bd_id") or raw.get("contact_id") or raw.get("mobile")
            if not bd_id:
                raise ValidationError(
                    "recipient at index {} must include bd_id, contact_id, or mobile".format(index)
                )

            name = raw.get("name") or str(bd_id)
            contact_id = raw.get("contact_id") or raw.get("mobile") or str(bd_id)
            variables = dict(raw.get("variables") or {})
            variables.update(
                {
                    "name": name,
                    "bd_id": str(bd_id),
                    "contact_id": str(contact_id),
                    "group": raw.get("group"),
                }
            )

            self._validate_template_variables(message_type, variables, follow_up_enabled)

            recipients.append(
                {
                    "id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "bd_id": str(bd_id),
                    "name": str(name),
                    "contact_id": str(contact_id),
                    "group": raw.get("group"),
                    "variables": variables,
                    "status": "pending",
                }
            )

        return recipients

    def _load_raw_recipients(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_recipients = payload.get("recipients")
        csv_path = payload.get("recipients_csv_path")
        if raw_recipients and csv_path:
            raise ValidationError("Use either recipients or recipients_csv_path, not both")
        if csv_path:
            try:
                return load_recipients_from_csv(str(csv_path))
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        return raw_recipients

    def _validate_template_variables(
        self,
        message_type: str,
        variables: Dict[str, Any],
        follow_up_enabled: bool,
    ) -> None:
        kinds = ["initial"]
        if follow_up_enabled:
            kinds.append("follow_up")

        for kind in kinds:
            required = self.templates.required_variables(message_type, kind)
            missing = sorted(name for name in required if name not in variables)
            if missing:
                raise ValidationError(
                    "Missing variables for {}.{}: {}".format(
                        message_type,
                        kind,
                        ", ".join(missing),
                    )
                )

    def _message_variables(self, recipient: Dict[str, Any]) -> Dict[str, Any]:
        variables = dict(recipient.get("variables") or {})
        variables.update(
            {
                "name": recipient.get("name"),
                "bd_id": recipient.get("bd_id"),
                "contact_id": recipient.get("contact_id"),
                "group": recipient.get("group"),
            }
        )
        return variables

    def _required_string(self, payload: Dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not value or not isinstance(value, str):
            raise ValidationError("{} is required".format(key))
        return value
