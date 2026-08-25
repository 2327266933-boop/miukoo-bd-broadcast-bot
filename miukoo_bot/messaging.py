import json
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional
from urllib import error, request

from miukoo_bot.config import Settings


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


class LarkMessageAdapter(MessageAdapter):
    """Feishu/Lark adapter for sending text messages to a user or chat."""

    TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        receive_id_type: str = "open_id",
        timeout_seconds: int = 10,
    ) -> None:
        if not app_id or not app_secret:
            raise MessageSendError("LARK_APP_ID and LARK_APP_SECRET are required")
        self.app_id = app_id
        self.app_secret = app_secret
        self.receive_id_type = receive_id_type
        self.timeout_seconds = timeout_seconds
        self._tenant_access_token: Optional[str] = None
        self._token_expires_at = 0.0

    def send(
        self,
        channel: str,
        contact_id: str,
        content: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> SendResult:
        del channel, metadata
        token = self._get_tenant_access_token()
        url = "{}?receive_id_type={}".format(self.SEND_URL, self.receive_id_type)
        payload = {
            "receive_id": contact_id,
            "msg_type": "text",
            "content": json.dumps({"text": content}, ensure_ascii=False),
        }
        response = self._post_json(
            url,
            payload,
            headers={"Authorization": "Bearer {}".format(token)},
        )
        data = response.get("data") or {}
        return SendResult(platform_message_id=data.get("message_id", ""))

    def _get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expires_at - 60:
            return self._tenant_access_token

        response = self._post_json(
            self.TOKEN_URL,
            {"app_id": self.app_id, "app_secret": self.app_secret},
            headers={},
        )
        token = response.get("tenant_access_token")
        if not token:
            raise MessageSendError("Lark token response missing tenant_access_token")

        expire_seconds = int(response.get("expire", 7200))
        self._tenant_access_token = token
        self._token_expires_at = now + expire_seconds
        return token

    def _post_json(
        self,
        url: str,
        payload: Dict[str, object],
        headers: Dict[str, str],
    ) -> Dict[str, object]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json; charset=utf-8",
            **headers,
        }
        req = request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MessageSendError("Lark HTTP {}: {}".format(exc.code, detail)) from exc
        except error.URLError as exc:
            raise MessageSendError("Lark request failed: {}".format(exc.reason)) from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MessageSendError("Lark returned invalid JSON: {}".format(raw)) from exc

        code = decoded.get("code")
        if code not in (0, None):
            raise MessageSendError(
                "Lark API error {}: {}".format(code, decoded.get("msg") or decoded)
            )
        return decoded


def build_message_adapter(settings: Settings) -> MessageAdapter:
    adapter = settings.message_adapter.lower()
    if adapter in ("mock", "local"):
        return MockMessageAdapter(echo=True)
    if adapter in ("lark", "feishu"):
        return LarkMessageAdapter(
            app_id=settings.lark_app_id or "",
            app_secret=settings.lark_app_secret or "",
            receive_id_type=settings.lark_receive_id_type,
        )
    raise MessageSendError("Unsupported BD_BOT_ADAPTER: {}".format(settings.message_adapter))
