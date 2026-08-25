import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from miukoo_bot.config import Settings
from miukoo_bot.db import SQLiteStore
from miukoo_bot.messaging import MessageAdapter, SendResult
from miukoo_bot.service import BotService, ValidationError
from miukoo_bot.templates import TemplateStore


class CollectingAdapter(MessageAdapter):
    def __init__(self):
        self.sent = []

    def send(self, channel, contact_id, content, metadata=None):
        self.sent.append(
            {
                "channel": channel,
                "contact_id": contact_id,
                "content": content,
                "metadata": metadata or {},
            }
        )
        return SendResult(platform_message_id="test_{}".format(len(self.sent)))


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, minutes):
        self.value = self.value + timedelta(minutes=minutes)


def build_payload():
    return {
        "task_name": "库存确认",
        "channel": "mock",
        "message_type": "inventory_check",
        "recipients": [
            {
                "bd_id": "bd_001",
                "name": "张三",
                "contact_id": "mock_user_001",
                "variables": {
                    "city": "上海",
                    "shop_count": 12,
                    "deadline": "今天 18:00",
                },
            }
        ],
        "follow_up": {
            "enabled": True,
            "first_remind_after_minutes": 0,
            "remind_interval_minutes": 5,
            "max_remind_times": 1,
            "quiet_hours": {
                "start": "00:00",
                "end": "00:00",
            },
        },
    }


class BotServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_path="{}/test.sqlite3".format(self.tmpdir.name),
            host="127.0.0.1",
            port=8080,
            scheduler_interval_seconds=1,
            default_first_remind_after_minutes=120,
            default_remind_interval_minutes=180,
            default_max_remind_times=2,
            quiet_hours_start="00:00",
            quiet_hours_end="00:00",
        )
        self.store = SQLiteStore(self.settings.database_path)
        self.store.init_db()
        self.adapter = CollectingAdapter()
        self.clock = MutableClock()
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_create_task_sends_initial_message(self):
        task = self.service.create_task(build_payload())

        self.assertEqual(task["status"], "sent")
        self.assertEqual(len(task["recipients"]), 1)
        self.assertEqual(task["recipients"][0]["status"], "sent")
        self.assertEqual(task["recipients"][0]["send_count"], 1)
        self.assertEqual(len(self.adapter.sent), 1)
        self.assertIn("库存情况", self.adapter.sent[0]["content"])

    def test_due_unreplied_recipient_gets_follow_up(self):
        task = self.service.create_task(build_payload())

        result = self.service.process_follow_ups()
        updated = self.service.get_task(task["id"])

        self.assertEqual(result["sent_count"], 1)
        self.assertEqual(len(self.adapter.sent), 2)
        self.assertEqual(updated["recipients"][0]["status"], "completed")
        self.assertEqual(updated["recipients"][0]["remind_count"], 1)
        self.assertEqual(updated["status"], "completed")

    def test_reply_stops_follow_up(self):
        task = self.service.create_task(build_payload())
        self.service.record_reply(
            {
                "task_id": task["id"],
                "bd_id": "bd_001",
                "content": "已确认",
            }
        )

        result = self.service.process_follow_ups()
        updated = self.service.get_task(task["id"])

        self.assertEqual(result["due_count"], 0)
        self.assertEqual(len(self.adapter.sent), 1)
        self.assertEqual(updated["recipients"][0]["status"], "replied")
        self.assertEqual(updated["recipients"][0]["reply_count"], 1)

    def test_missing_template_variable_is_rejected(self):
        payload = build_payload()
        del payload["recipients"][0]["variables"]["deadline"]

        with self.assertRaises(ValidationError):
            self.service.create_task(payload)


if __name__ == "__main__":
    unittest.main()
