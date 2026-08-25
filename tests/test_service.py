import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
            message_adapter="mock",
            scheduler_interval_seconds=1,
            default_first_remind_after_minutes=120,
            default_remind_interval_minutes=180,
            default_max_remind_times=2,
            daily_message_limit_per_contact=3,
            quiet_hours_start="00:00",
            quiet_hours_end="00:00",
            lark_app_id=None,
            lark_app_secret=None,
            lark_verification_token=None,
            lark_receive_id_type="open_id",
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

    def test_preview_task_renders_without_sending_or_persisting(self):
        preview = self.service.preview_task(build_payload())

        self.assertEqual(preview["recipient_count"], 1)
        self.assertIn("库存情况", preview["recipients"][0]["messages"]["initial"])
        self.assertEqual(len(self.adapter.sent), 0)
        self.assertEqual(self.service.list_tasks(), [])

    def test_create_task_can_load_recipients_from_csv(self):
        csv_path = Path(self.tmpdir.name) / "recipients.csv"
        csv_path.write_text(
            "bd_id,name,contact_id,group,city,shop_count,deadline\n"
            "bd_001,张三,mock_user_001,华东一区,上海,12,今天 18:00\n",
            encoding="utf-8",
        )
        payload = build_payload()
        payload.pop("recipients")
        payload["recipients_csv_path"] = str(csv_path)

        task = self.service.create_task(payload)

        self.assertEqual(len(task["recipients"]), 1)
        self.assertEqual(task["recipients"][0]["variables"]["city"], "上海")
        self.assertEqual(len(self.adapter.sent), 1)

    def test_stop_recipient_cancels_future_follow_up(self):
        payload = build_payload()
        payload["follow_up"]["first_remind_after_minutes"] = 5
        task = self.service.create_task(payload)
        recipient_id = task["recipients"][0]["id"]

        updated = self.service.stop_recipient(task["id"], recipient_id)
        self.clock.advance(10)
        result = self.service.process_follow_ups()

        self.assertEqual(updated["recipients"][0]["status"], "cancelled")
        self.assertEqual(result["due_count"], 0)
        self.assertEqual(len(self.adapter.sent), 1)

    def test_daily_rate_limit_blocks_extra_messages(self):
        self.settings = Settings(
            database_path=self.settings.database_path,
            host=self.settings.host,
            port=self.settings.port,
            message_adapter="mock",
            scheduler_interval_seconds=self.settings.scheduler_interval_seconds,
            default_first_remind_after_minutes=self.settings.default_first_remind_after_minutes,
            default_remind_interval_minutes=self.settings.default_remind_interval_minutes,
            default_max_remind_times=self.settings.default_max_remind_times,
            daily_message_limit_per_contact=1,
            quiet_hours_start=self.settings.quiet_hours_start,
            quiet_hours_end=self.settings.quiet_hours_end,
            lark_app_id=None,
            lark_app_secret=None,
            lark_verification_token=None,
            lark_receive_id_type="open_id",
        )
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )

        first = self.service.create_task(build_payload())
        second = self.service.create_task(build_payload())

        self.assertEqual(first["recipients"][0]["status"], "sent")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["recipients"][0]["status"], "failed")
        self.assertIn("Daily message limit", second["recipients"][0]["failure_reason"])
        self.assertEqual(len(self.adapter.sent), 1)

    def test_lark_url_verification_returns_challenge(self):
        self._set_lark_token("verify-token")

        response = self.service.handle_webhook(
            "lark",
            {
                "schema": "2.0",
                "header": {
                    "event_type": "url_verification",
                    "token": "verify-token",
                },
                "event": {
                    "challenge": "challenge-value",
                },
            },
        )

        self.assertEqual(response, {"challenge": "challenge-value"})

    def test_lark_url_verification_rejects_wrong_token(self):
        self._set_lark_token("verify-token")

        with self.assertRaises(ValidationError):
            self.service.handle_webhook(
                "lark",
                {
                    "type": "url_verification",
                    "token": "wrong-token",
                    "challenge": "challenge-value",
                },
            )

    def test_lark_message_event_records_reply_by_open_id(self):
        self._set_lark_token("verify-token")
        payload = build_payload()
        payload["channel"] = "lark"
        payload["recipients"][0]["contact_id"] = "ou_mock_user"
        task = self.service.create_task(payload)

        response = self.service.handle_webhook(
            "lark",
            {
                "schema": "2.0",
                "header": {
                    "event_id": "event_001",
                    "event_type": "im.message.receive_v1",
                    "token": "verify-token",
                },
                "event": {
                    "sender": {
                        "sender_type": "user",
                        "sender_id": {
                            "open_id": "ou_mock_user",
                            "user_id": "user_mock",
                        },
                    },
                    "message": {
                        "message_id": "om_mock",
                        "message_type": "text",
                        "content": "{\"text\":\"已确认\"}",
                    },
                },
            },
        )
        updated = self.service.get_task(task["id"])

        self.assertTrue(response["recorded"])
        self.assertEqual(response["task_id"], task["id"])
        self.assertEqual(updated["recipients"][0]["status"], "replied")
        self.assertEqual(updated["reply_logs"][0]["content"], "已确认")

    def test_lark_message_without_matching_task_is_ignored(self):
        self._set_lark_token("verify-token")

        response = self.service.handle_webhook(
            "lark",
            {
                "schema": "2.0",
                "header": {
                    "event_type": "im.message.receive_v1",
                    "token": "verify-token",
                },
                "event": {
                    "sender": {
                        "sender_type": "user",
                        "sender_id": {
                            "open_id": "unknown_user",
                        },
                    },
                    "message": {
                        "message_id": "om_unknown",
                        "message_type": "text",
                        "content": "{\"text\":\"已确认\"}",
                    },
                },
            },
        )

        self.assertEqual(response["reason"], "no_matching_recipient")

    def _set_lark_token(self, token):
        self.settings = Settings(
            database_path=self.settings.database_path,
            host=self.settings.host,
            port=self.settings.port,
            message_adapter=self.settings.message_adapter,
            scheduler_interval_seconds=self.settings.scheduler_interval_seconds,
            default_first_remind_after_minutes=self.settings.default_first_remind_after_minutes,
            default_remind_interval_minutes=self.settings.default_remind_interval_minutes,
            default_max_remind_times=self.settings.default_max_remind_times,
            daily_message_limit_per_contact=self.settings.daily_message_limit_per_contact,
            quiet_hours_start=self.settings.quiet_hours_start,
            quiet_hours_end=self.settings.quiet_hours_end,
            lark_app_id=None,
            lark_app_secret=None,
            lark_verification_token=token,
            lark_receive_id_type="open_id",
        )
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )


if __name__ == "__main__":
    unittest.main()
