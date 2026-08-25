import threading
from typing import Optional

from miukoo_bot.service import BotService


class FollowUpScheduler:
    def __init__(self, service: BotService, interval_seconds: int) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="follow-up-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.service.process_follow_ups()
            except Exception as exc:
                print("[scheduler] follow-up scan failed: {}".format(exc), flush=True)
            self._stop_event.wait(self.interval_seconds)
