import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from miukoo_bot.api import make_handler
from miukoo_bot.config import Settings
from miukoo_bot.db import SQLiteStore
from miukoo_bot.messaging import MessageAdapter, SendResult
from miukoo_bot.service import BotService
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
                "group": "华东一区",
                "variables": {
                    "city": "上海",
                    "shop_count": 12,
                    "deadline": "今天 18:00",
                },
            }
        ],
        "follow_up": {
            "enabled": True,
            "first_remind_after_minutes": 120,
            "remind_interval_minutes": 180,
            "max_remind_times": 2,
        },
    }


class APITest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.mapping_path = Path(self.tmpdir.name) / "merchant_bd_mapping.csv"
        self.mapping_path.write_text(
            "总户商家名称,销售名称_最新,bd_id,contact_id,group,city\n"
            "杭州沐暮子科技有限公司,高流,bd_gaoliu,mock_user_gaoliu,华东一区,杭州\n",
            encoding="utf-8",
        )
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
            merchant_bd_lookup_provider="csv",
            merchant_bd_mapping_csv=str(self.mapping_path),
            fengshen_merchant_bd_lookup_url=None,
            fengshen_api_token=None,
            fengshen_token_url=None,
            fengshen_client_id=None,
            fengshen_client_secret=None,
            fengshen_client_id_field="clientId",
            fengshen_client_secret_field="clientSecret",
            fengshen_scope=None,
            fengshen_timeout_seconds=10,
        )
        store = SQLiteStore(self.settings.database_path)
        store.init_db()
        self.service = BotService(
            store=store,
            templates=TemplateStore(),
            adapter=CollectingAdapter(),
            settings=self.settings,
        )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(self.service),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:{}".format(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmpdir.cleanup()

    def test_workbench_page_is_served(self):
        with urlopen("{}/workbench".format(self.base_url), timeout=5) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn("BD 群发工作台", body)

    def test_task_replies_csv_can_be_downloaded(self):
        task = self.service.create_task(build_payload())
        self.service.record_reply(
            {
                "task_id": task["id"],
                "bd_id": "bd_001",
                "content": "已确认，库存正常",
            }
        )

        with urlopen(
            "{}/api/tasks/{}/replies.csv".format(self.base_url, task["id"]),
            timeout=5,
        ) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("text/csv", response.headers["Content-Type"])
        self.assertIn("last_reply_content", body)
        self.assertIn("已确认，库存正常", body)

    def test_merchant_bd_lookup_endpoint(self):
        request = Request(
            "{}/api/merchant-bd/lookup".format(self.base_url),
            data=json.dumps(
                {"merchant_names": ["沐暮子", "未知商家"]},
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(body["matched_count"], 1)
        self.assertEqual(body["unmatched_count"], 1)
        self.assertEqual(body["recipient_count"], 1)
        self.assertEqual(body["results"][0]["matches"][0]["sales_name"], "高流")
        self.assertEqual(body["recipients"][0]["contact_id"], "mock_user_gaoliu")


if __name__ == "__main__":
    unittest.main()
