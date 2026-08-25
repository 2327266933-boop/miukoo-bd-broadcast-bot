import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Settings:
    database_path: str
    host: str
    port: int
    message_adapter: str
    scheduler_interval_seconds: int
    default_first_remind_after_minutes: int
    default_remind_interval_minutes: int
    default_max_remind_times: int
    daily_message_limit_per_contact: int
    quiet_hours_start: str
    quiet_hours_end: str
    lark_app_id: Optional[str]
    lark_app_secret: Optional[str]
    lark_verification_token: Optional[str]
    lark_receive_id_type: str


def load_settings() -> Settings:
    return Settings(
        database_path=os.environ.get("BD_BOT_DATABASE", "data/bd_bot.sqlite3"),
        host=os.environ.get("BD_BOT_HOST", "127.0.0.1"),
        port=int(os.environ.get("BD_BOT_PORT", "8080")),
        message_adapter=os.environ.get("BD_BOT_ADAPTER", "mock"),
        scheduler_interval_seconds=int(os.environ.get("BD_BOT_SCHEDULER_INTERVAL_SECONDS", "30")),
        default_first_remind_after_minutes=int(
            os.environ.get("DEFAULT_FIRST_REMIND_AFTER_MINUTES", "120")
        ),
        default_remind_interval_minutes=int(
            os.environ.get("DEFAULT_REMIND_INTERVAL_MINUTES", "180")
        ),
        default_max_remind_times=int(os.environ.get("DEFAULT_MAX_REMIND_TIMES", "2")),
        daily_message_limit_per_contact=int(
            os.environ.get("DAILY_MESSAGE_LIMIT_PER_CONTACT", "3")
        ),
        quiet_hours_start=os.environ.get("QUIET_HOURS_START", "21:00"),
        quiet_hours_end=os.environ.get("QUIET_HOURS_END", "09:00"),
        lark_app_id=os.environ.get("LARK_APP_ID"),
        lark_app_secret=os.environ.get("LARK_APP_SECRET"),
        lark_verification_token=os.environ.get("LARK_VERIFICATION_TOKEN"),
        lark_receive_id_type=os.environ.get("LARK_RECEIVE_ID_TYPE", "open_id"),
    )
