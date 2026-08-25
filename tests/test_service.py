import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import miukoo_bot.merchant_lookup as merchant_lookup_module
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
            lark_department_id_type="open_department_id",
            lark_timeout_seconds=10,
            merchant_bd_lookup_provider="csv",
            merchant_bd_mapping_csv=None,
            fengshen_merchant_bd_lookup_url=None,
            fengshen_api_token=None,
            fengshen_token_url=None,
            fengshen_client_id=None,
            fengshen_client_secret=None,
            fengshen_client_id_field="clientId",
            fengshen_client_secret_field="clientSecret",
            fengshen_scope=None,
            fengshen_timeout_seconds=10,
            sales_contact_lookup_provider="csv",
            sales_contact_directory_csv=None,
            sales_target_department="服务零售KA——丽人",
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

    def test_per_recipient_custom_messages_override_shared_template(self):
        payload = build_payload()
        payload["recipients"] = [
            {
                "bd_id": "bd_001",
                "name": "张三",
                "contact_id": "mock_user_001",
                "variables": {
                    "custom_message": "张三，今天重点确认上海门店库存。",
                    "custom_follow_up_message": "张三，上海库存结果还没收到，麻烦回一下。",
                },
            },
            {
                "bd_id": "bd_002",
                "name": "李四",
                "contact_id": "mock_user_002",
                "variables": {
                    "custom_message": "李四，广州门店先看晚高峰备货。",
                    "custom_follow_up_message": "李四，广州备货反馈还没收到，麻烦同步。",
                },
            },
        ]

        preview = self.service.preview_task(payload)
        task = self.service.create_task(payload)
        self.service.process_follow_ups()

        self.assertEqual(preview["recipients"][0]["messages"]["initial"], "张三，今天重点确认上海门店库存。")
        self.assertEqual(preview["recipients"][1]["messages"]["initial"], "李四，广州门店先看晚高峰备货。")
        self.assertEqual(self.adapter.sent[0]["content"], "张三，今天重点确认上海门店库存。")
        self.assertEqual(self.adapter.sent[1]["content"], "李四，广州门店先看晚高峰备货。")
        self.assertEqual(self.adapter.sent[2]["content"], "张三，上海库存结果还没收到，麻烦回一下。")
        self.assertEqual(self.adapter.sent[3]["content"], "李四，广州备货反馈还没收到，麻烦同步。")
        self.assertEqual(task["recipients"][0]["status"], "sent")

    def test_custom_message_can_use_recipient_variables(self):
        payload = build_payload()
        payload["recipients"] = [
            {
                "bd_id": "bd_001",
                "name": "张三",
                "contact_id": "mock_user_001",
                "custom_message": "{name}，请优先处理 {city} 的 {shop_count} 家门店。",
                "custom_follow_up_message": "{name}，{city} 门店还没反馈。",
                "variables": {
                    "city": "上海",
                    "shop_count": 12,
                },
            }
        ]

        preview = self.service.preview_task(payload)

        self.assertEqual(
            preview["recipients"][0]["messages"]["initial"],
            "张三，请优先处理 上海 的 12 家门店。",
        )
        self.assertEqual(
            preview["recipients"][0]["messages"]["follow_up"],
            "张三，上海 门店还没反馈。",
        )

    def test_create_task_can_load_recipients_from_csv(self):
        csv_path = Path(self.tmpdir.name) / "recipients.csv"
        csv_path.write_text(
            "bd_id,name,contact_id,group,city,shop_count,deadline,custom_message\n"
            "bd_001,张三,mock_user_001,华东一区,上海,12,今天 18:00,"
            "\"张三，这条是 CSV 单独话术。\"\n",
            encoding="utf-8",
        )
        payload = build_payload()
        payload.pop("recipients")
        payload["recipients_csv_path"] = str(csv_path)

        task = self.service.create_task(payload)

        self.assertEqual(len(task["recipients"]), 1)
        self.assertEqual(task["recipients"][0]["variables"]["city"], "上海")
        self.assertEqual(self.adapter.sent[0]["content"], "张三，这条是 CSV 单独话术。")
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
            lark_department_id_type=self.settings.lark_department_id_type,
            lark_timeout_seconds=self.settings.lark_timeout_seconds,
            merchant_bd_lookup_provider=self.settings.merchant_bd_lookup_provider,
            merchant_bd_mapping_csv=self.settings.merchant_bd_mapping_csv,
            fengshen_merchant_bd_lookup_url=self.settings.fengshen_merchant_bd_lookup_url,
            fengshen_api_token=self.settings.fengshen_api_token,
            fengshen_token_url=self.settings.fengshen_token_url,
            fengshen_client_id=self.settings.fengshen_client_id,
            fengshen_client_secret=self.settings.fengshen_client_secret,
            fengshen_client_id_field=self.settings.fengshen_client_id_field,
            fengshen_client_secret_field=self.settings.fengshen_client_secret_field,
            fengshen_scope=self.settings.fengshen_scope,
            fengshen_timeout_seconds=self.settings.fengshen_timeout_seconds,
            sales_contact_lookup_provider=self.settings.sales_contact_lookup_provider,
            sales_contact_directory_csv=self.settings.sales_contact_directory_csv,
            sales_target_department=self.settings.sales_target_department,
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

    def test_lookup_merchant_bds_from_csv_groups_recipients(self):
        csv_path = Path(self.tmpdir.name) / "merchant_bd_mapping.csv"
        csv_path.write_text(
            "merchant_name,bd_id,name,contact_id,group,city\n"
            "上海悦来火锅,bd_001,张三,mock_user_001,华东一区,上海\n"
            "上海云亭烤肉,bd_001,张三,mock_user_001,华东一区,上海\n"
            "广州花城茶餐厅,bd_002,李四,mock_user_002,华南二区,广州\n",
            encoding="utf-8",
        )
        self.settings = replace(self.settings, merchant_bd_mapping_csv=str(csv_path))
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )

        result = self.service.lookup_merchant_bds(
            {"merchant_names": ["上海悦来火锅", "上海云亭烤肉", "不存在商家"]}
        )

        self.assertEqual(result["source"], "csv")
        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(result["unmatched_count"], 1)
        self.assertEqual(result["recipient_count"], 1)
        self.assertEqual(result["recipients"][0]["bd_id"], "bd_001")
        self.assertEqual(result["recipients"][0]["variables"]["merchant_count"], 2)
        self.assertIn("上海悦来火锅", result["recipients"][0]["variables"]["merchant_names"])

    def test_lookup_supports_total_merchant_name_and_latest_sales_name(self):
        csv_path = Path(self.tmpdir.name) / "merchant_bd_mapping.csv"
        csv_path.write_text(
            "总户商家名称,销售名称_最新,bd_id,contact_id,group\n"
            "杭州沐暮子科技有限公司,高流,bd_gaoliu,mock_user_gaoliu,华东一区\n",
            encoding="utf-8",
        )
        self.settings = replace(self.settings, merchant_bd_mapping_csv=str(csv_path))
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )

        result = self.service.lookup_merchant_bds({"text": "沐暮子"})

        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["results"][0]["merchant_name"], "沐暮子")
        self.assertEqual(
            result["results"][0]["matches"][0]["merchant_name"],
            "杭州沐暮子科技有限公司",
        )
        self.assertEqual(result["results"][0]["matches"][0]["sales_name"], "高流")
        self.assertEqual(result["recipients"][0]["name"], "高流")
        self.assertEqual(
            result["recipients"][0]["variables"]["merchant_names"],
            "杭州沐暮子科技有限公司",
        )

    def test_lookup_resolves_duplicate_sales_name_by_target_department(self):
        merchant_csv_path = Path(self.tmpdir.name) / "merchant_bd_mapping.csv"
        merchant_csv_path.write_text(
            "总户商家名称,销售名称_最新,group\n"
            "杭州沐暮子科技有限公司,高流,华东一区\n",
            encoding="utf-8",
        )
        sales_csv_path = Path(self.tmpdir.name) / "sales_contact_directory.csv"
        sales_csv_path.write_text(
            "销售名称_最新,bd_id,contact_id,所属部门\n"
            "高流,bd_gaoliu,mock_user_gaoliu,服务零售KA——丽人\n"
            "高流,bd_other,mock_user_other,服务零售KA——到综\n",
            encoding="utf-8",
        )
        self.settings = replace(
            self.settings,
            merchant_bd_mapping_csv=str(merchant_csv_path),
            sales_contact_directory_csv=str(sales_csv_path),
            sales_target_department="服务零售KA——丽人",
        )
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )

        result = self.service.lookup_merchant_bds({"text": "沐暮子"})
        match = result["results"][0]["matches"][0]

        self.assertEqual(result["results"][0]["status"], "matched")
        self.assertEqual(match["sales_name"], "高流")
        self.assertEqual(match["contact_id"], "mock_user_gaoliu")
        self.assertEqual(match["department"], "服务零售KA——丽人")
        self.assertTrue(match["sales_resolution"]["duplicate_name"])
        self.assertEqual(
            match["sales_resolution"]["status"],
            "resolved_by_department",
        )
        self.assertEqual(len(match["sales_resolution"]["candidates"]), 2)
        self.assertEqual(result["recipient_count"], 1)

    def test_lookup_marks_duplicate_sales_name_when_department_not_unique(self):
        merchant_csv_path = Path(self.tmpdir.name) / "merchant_bd_mapping.csv"
        merchant_csv_path.write_text(
            "总户商家名称,销售名称_最新,group\n"
            "杭州沐暮子科技有限公司,高流,华东一区\n",
            encoding="utf-8",
        )
        sales_csv_path = Path(self.tmpdir.name) / "sales_contact_directory.csv"
        sales_csv_path.write_text(
            "销售名称_最新,bd_id,contact_id,所属部门\n"
            "高流,bd_gaoliu_1,mock_user_1,服务零售KA——丽人\n"
            "高流,bd_gaoliu_2,mock_user_2,服务零售KA——丽人\n",
            encoding="utf-8",
        )
        self.settings = replace(
            self.settings,
            merchant_bd_mapping_csv=str(merchant_csv_path),
            sales_contact_directory_csv=str(sales_csv_path),
            sales_target_department="服务零售KA——丽人",
        )
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )

        result = self.service.lookup_merchant_bds({"text": "沐暮子"})
        match = result["results"][0]["matches"][0]

        self.assertEqual(result["results"][0]["status"], "ambiguous")
        self.assertEqual(match["sales_resolution"]["status"], "ambiguous")
        self.assertTrue(match["sales_resolution"]["duplicate_name"])
        self.assertEqual(len(match["sales_resolution"]["candidates"]), 2)
        self.assertEqual(result["recipient_count"], 0)

    def test_lookup_searches_feishu_users_and_selects_target_department(self):
        merchant_csv_path = Path(self.tmpdir.name) / "merchant_bd_mapping.csv"
        merchant_csv_path.write_text(
            "总户商家名称,销售名称_最新,group\n"
            "杭州沐暮子科技有限公司,高流,华东一区\n",
            encoding="utf-8",
        )
        captured = {
            "token_body": {},
            "search_body": {},
        }

        class LarkContactHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                _ = _args
                return

            def do_POST(handler_self):
                length = int(handler_self.headers.get("Content-Length", "0"))
                body = handler_self.rfile.read(length).decode("utf-8")
                if handler_self.path == "/token":
                    captured["token_body"] = json.loads(body)
                    handler_self._send_json(
                        {
                            "code": 0,
                            "tenant_access_token": "tenant-token",
                            "expire": 7200,
                        }
                    )
                    return
                if handler_self.path.startswith("/contact/v3/users/search"):
                    captured["search_body"] = json.loads(body)
                    handler_self._send_json(
                        {
                            "code": 0,
                            "data": {
                                "items": [
                                    {
                                        "name": "高流",
                                        "open_id": "ou_liren",
                                        "user_id": "user_liren",
                                        "department_ids": ["dep_liren"],
                                    },
                                    {
                                        "name": "高流",
                                        "open_id": "ou_other",
                                        "user_id": "user_other",
                                        "department_ids": ["dep_other"],
                                    },
                                ]
                            },
                        }
                    )
                    return
                handler_self.send_response(404)
                handler_self.end_headers()

            def do_GET(handler_self):
                if handler_self.path.startswith("/contact/v3/departments/dep_liren"):
                    handler_self._send_json(
                        {
                            "code": 0,
                            "data": {
                                "department": {
                                    "name": "服务零售KA——丽人",
                                }
                            },
                        }
                    )
                    return
                if handler_self.path.startswith("/contact/v3/departments/dep_other"):
                    handler_self._send_json(
                        {
                            "code": 0,
                            "data": {
                                "department": {
                                    "name": "服务零售KA——到综",
                                }
                            },
                        }
                    )
                    return
                handler_self.send_response(404)
                handler_self.end_headers()

            def _send_json(handler_self, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                handler_self.send_response(200)
                handler_self.send_header("Content-Type", "application/json")
                handler_self.send_header("Content-Length", str(len(body)))
                handler_self.end_headers()
                handler_self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), LarkContactHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        original_token_url = merchant_lookup_module.LARK_TENANT_TOKEN_URL
        original_search_url = merchant_lookup_module.LARK_USER_SEARCH_URL
        original_department_url = merchant_lookup_module.LARK_DEPARTMENT_URL
        thread.start()
        try:
            base_url = "http://127.0.0.1:{}".format(server.server_port)
            merchant_lookup_module.LARK_TENANT_TOKEN_URL = "{}/token".format(base_url)
            merchant_lookup_module.LARK_USER_SEARCH_URL = (
                "{}/contact/v3/users/search".format(base_url)
            )
            merchant_lookup_module.LARK_DEPARTMENT_URL = (
                "{}/contact/v3/departments/{{}}".format(base_url)
            )
            self.settings = replace(
                self.settings,
                merchant_bd_mapping_csv=str(merchant_csv_path),
                lark_app_id="app-id",
                lark_app_secret="app-secret",
                sales_contact_lookup_provider="lark",
                sales_target_department="服务零售KA——丽人",
            )
            self.service = BotService(
                store=self.store,
                templates=TemplateStore(),
                adapter=self.adapter,
                settings=self.settings,
                clock=self.clock,
            )

            result = self.service.lookup_merchant_bds({"text": "沐暮子"})
            match = result["results"][0]["matches"][0]

            self.assertEqual(captured["token_body"]["app_id"], "app-id")
            self.assertEqual(captured["search_body"]["query"], "高流")
            self.assertEqual(result["results"][0]["status"], "matched")
            self.assertEqual(match["contact_id"], "ou_liren")
            self.assertEqual(match["department"], "服务零售KA——丽人")
            self.assertEqual(
                match["sales_resolution"]["status"],
                "resolved_by_department",
            )
            self.assertEqual(result["recipients"][0]["contact_id"], "ou_liren")
        finally:
            merchant_lookup_module.LARK_TENANT_TOKEN_URL = original_token_url
            merchant_lookup_module.LARK_USER_SEARCH_URL = original_search_url
            merchant_lookup_module.LARK_DEPARTMENT_URL = original_department_url
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_lookup_recipients_can_render_merchant_follow_up_template(self):
        csv_path = Path(self.tmpdir.name) / "merchant_bd_mapping.csv"
        csv_path.write_text(
            "merchant_name,bd_id,name,contact_id,group\n"
            "上海悦来火锅,bd_001,张三,mock_user_001,华东一区\n",
            encoding="utf-8",
        )
        self.settings = replace(self.settings, merchant_bd_mapping_csv=str(csv_path))
        self.service = BotService(
            store=self.store,
            templates=TemplateStore(),
            adapter=self.adapter,
            settings=self.settings,
            clock=self.clock,
        )
        lookup = self.service.lookup_merchant_bds({"text": "上海悦来火锅"})

        preview = self.service.preview_task(
            {
                "task_name": "商家跟进",
                "channel": "mock",
                "message_type": "merchant_follow_up",
                "recipients": lookup["recipients"],
                "follow_up": {"enabled": True},
            }
        )

        self.assertIn("上海悦来火锅", preview["recipients"][0]["messages"]["initial"])
        self.assertIn("1 个商家", preview["recipients"][0]["messages"]["initial"])

    def test_fengshen_lookup_can_use_client_credentials_token(self):
        captured = {
            "token_body": {},
            "token_content_type": "",
            "lookup_authorization": "",
        }

        class FengshenHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                _ = _args
                return

            def do_POST(handler_self):
                length = int(handler_self.headers.get("Content-Length", "0"))
                body = handler_self.rfile.read(length).decode("utf-8")
                if handler_self.path == "/token":
                    captured["token_content_type"] = handler_self.headers.get(
                        "Content-Type",
                        "",
                    )
                    captured["token_body"] = json.loads(body)
                    handler_self._send_json(
                        {
                            "jwtToken": "fengshen_test_token",
                            "expires_in": 3600,
                        }
                    )
                    return
                if handler_self.path == "/lookup":
                    captured["lookup_authorization"] = handler_self.headers.get(
                        "Authorization",
                        "",
                    )
                    handler_self._send_json(
                        {
                            "results": [
                                {
                                    "merchant_name": "上海悦来火锅",
                                    "matches": [
                                        {
                                            "merchant_name": "上海悦来火锅",
                                            "bd_id": "bd_001",
                                            "name": "张三",
                                            "contact_id": "mock_user_001",
                                            "group": "华东一区",
                                        }
                                    ],
                                }
                            ]
                        }
                    )
                    return
                handler_self.send_response(404)
                handler_self.end_headers()

            def _send_json(handler_self, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                handler_self.send_response(200)
                handler_self.send_header("Content-Type", "application/json")
                handler_self.send_header("Content-Length", str(len(body)))
                handler_self.end_headers()
                handler_self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), FengshenHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = "http://127.0.0.1:{}".format(server.server_port)
            self.settings = replace(
                self.settings,
                merchant_bd_lookup_provider="fengshen",
                fengshen_merchant_bd_lookup_url="{}/lookup".format(base_url),
                fengshen_token_url="{}/token".format(base_url),
                fengshen_client_id="client-id",
                fengshen_client_secret="client-secret",
            )
            self.service = BotService(
                store=self.store,
                templates=TemplateStore(),
                adapter=self.adapter,
                settings=self.settings,
                clock=self.clock,
            )

            result = self.service.lookup_merchant_bds({"text": "上海悦来火锅"})

            self.assertIn("application/json", captured["token_content_type"])
            self.assertEqual(captured["token_body"]["clientId"], "client-id")
            self.assertEqual(captured["token_body"]["clientSecret"], "client-secret")
            self.assertEqual(
                captured["lookup_authorization"],
                "Bearer fengshen_test_token",
            )
            self.assertEqual(result["source"], "fengshen")
            self.assertEqual(result["recipient_count"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

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
            lark_department_id_type=self.settings.lark_department_id_type,
            lark_timeout_seconds=self.settings.lark_timeout_seconds,
            merchant_bd_lookup_provider=self.settings.merchant_bd_lookup_provider,
            merchant_bd_mapping_csv=self.settings.merchant_bd_mapping_csv,
            fengshen_merchant_bd_lookup_url=self.settings.fengshen_merchant_bd_lookup_url,
            fengshen_api_token=self.settings.fengshen_api_token,
            fengshen_token_url=self.settings.fengshen_token_url,
            fengshen_client_id=self.settings.fengshen_client_id,
            fengshen_client_secret=self.settings.fengshen_client_secret,
            fengshen_client_id_field=self.settings.fengshen_client_id_field,
            fengshen_client_secret_field=self.settings.fengshen_client_secret_field,
            fengshen_scope=self.settings.fengshen_scope,
            fengshen_timeout_seconds=self.settings.fengshen_timeout_seconds,
            sales_contact_lookup_provider=self.settings.sales_contact_lookup_provider,
            sales_contact_directory_csv=self.settings.sales_contact_directory_csv,
            sales_target_department=self.settings.sales_target_department,
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
