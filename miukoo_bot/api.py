import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from miukoo_bot.service import BotService, NotFoundError, ValidationError


def make_handler(service: BotService):
    class BotHTTPRequestHandler(BaseHTTPRequestHandler):
        server_version = "BDGroupBot/0.1"

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_common_headers()
            self.end_headers()

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/health":
                    self._send_json(200, {"ok": True})
                    return

                if path == "/api/templates":
                    self._send_json(
                        200,
                        {"message_types": list(service.templates.message_types())},
                    )
                    return

                if path == "/api/tasks":
                    self._send_json(200, {"tasks": service.list_tasks()})
                    return

                task_id = self._match_task_path(path)
                if task_id:
                    self._send_json(200, service.get_task(task_id))
                    return

                self._send_json(404, {"error": "Not found"})
            except Exception as exc:
                self._handle_error(exc)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/api/tasks/preview":
                    payload = self._read_json()
                    self._send_json(200, service.preview_task(payload))
                    return

                if path == "/api/tasks":
                    payload = self._read_json()
                    self._send_json(201, service.create_task(payload))
                    return

                stop_path = self._match_stop_recipient_path(path)
                if stop_path[0]:
                    task_id, recipient_id = stop_path
                    self._send_json(200, service.stop_recipient(task_id, recipient_id))
                    return

                cancel_task_id = self._match_cancel_path(path)
                if cancel_task_id:
                    self._send_json(200, service.cancel_task(cancel_task_id))
                    return

                webhook_channel = self._match_webhook_path(path)
                if webhook_channel:
                    payload = self._read_json()
                    payload.setdefault("channel", webhook_channel)
                    self._send_json(200, service.record_reply(payload))
                    return

                if path == "/api/scheduler/run-once":
                    self._send_json(200, service.process_follow_ups())
                    return

                self._send_json(404, {"error": "Not found"})
            except Exception as exc:
                self._handle_error(exc)

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValidationError("Invalid JSON: {}".format(exc)) from exc
            if not isinstance(decoded, dict):
                raise ValidationError("Request body must be a JSON object")
            return decoded

        def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status_code)
            self._send_common_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_common_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _handle_error(self, exc: Exception) -> None:
            if isinstance(exc, ValidationError):
                self._send_json(400, {"error": str(exc)})
                return
            if isinstance(exc, NotFoundError):
                self._send_json(404, {"error": str(exc)})
                return
            self._send_json(500, {"error": str(exc)})

        def _match_task_path(self, path: str) -> str:
            prefix = "/api/tasks/"
            if not path.startswith(prefix):
                return ""
            suffix = path[len(prefix):]
            if not suffix or "/" in suffix:
                return ""
            return suffix

        def _match_cancel_path(self, path: str) -> str:
            prefix = "/api/tasks/"
            suffix = "/cancel"
            if path.startswith(prefix) and path.endswith(suffix):
                task_id = path[len(prefix):-len(suffix)]
                if task_id and "/" not in task_id:
                    return task_id
            return ""

        def _match_stop_recipient_path(self, path: str) -> Tuple[str, str]:
            prefix = "/api/tasks/"
            suffix = "/stop"
            if not path.startswith(prefix) or not path.endswith(suffix):
                return "", ""

            middle = path[len(prefix):-len(suffix)]
            parts = [part for part in middle.split("/") if part]
            if len(parts) == 3 and parts[1] == "recipients":
                return parts[0], parts[2]
            return "", ""

        def _match_webhook_path(self, path: str) -> str:
            prefix = "/api/webhooks/"
            suffix = "/message"
            if path.startswith(prefix) and path.endswith(suffix):
                channel = path[len(prefix):-len(suffix)]
                if channel and "/" not in channel:
                    return channel
            return ""

    return BotHTTPRequestHandler


def run_http_server(
    service: BotService,
    host: str,
    port: int,
) -> Tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    url = "http://{}:{}".format(host, port)
    print("BD group bot API listening on {}".format(url), flush=True)
    server.serve_forever()
    return server, url
