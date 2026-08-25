import uuid
from dataclasses import dataclass
from typing import Dict, Optional


class MessageSendError(RuntimeError):
    pass


@dataclass(frozen=True)
class SendResult:
    platform_message_id: str
    status: str = "sent"


class MessageAdapter:
    def send(
        self,
        channel: str,
        contact_id: str,
        content: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> SendResult:
        del channel, contact_id, content, metadata
        raise NotImplementedError


class MockMessageAdapter(MessageAdapter):
    """Local adapter that prints messages instead of calling a real platform API."""

    def __init__(self, echo: bool = True) -> None:
        self.echo = echo

    def send(
        self,
        channel: str,
        contact_id: str,
        content: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> SendResult:
        message_id = "mock_{}".format(uuid.uuid4().hex)
        if self.echo:
            kind = metadata.get("message_kind", "message") if metadata else "message"
            task_id = metadata.get("task_id", "-") if metadata else "-"
            print(
                "[{}:{}] task={} to={}\n{}\n".format(
                    channel,
                    kind,
                    task_id,
                    contact_id,
                    content,
                ),
                flush=True,
            )
        return SendResult(platform_message_id=message_id)
