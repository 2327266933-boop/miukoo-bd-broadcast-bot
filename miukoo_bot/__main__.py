import argparse
from dataclasses import replace

from miukoo_bot.api import run_http_server
from miukoo_bot.config import load_settings
from miukoo_bot.db import SQLiteStore
from miukoo_bot.messaging import build_message_adapter
from miukoo_bot.scheduler import FollowUpScheduler
from miukoo_bot.service import BotService
from miukoo_bot.templates import TemplateStore


def build_service(settings):
    store = SQLiteStore(settings.database_path)
    store.init_db()
    return BotService(
        store=store,
        templates=TemplateStore(),
        adapter=build_message_adapter(settings),
        settings=settings,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="BD broadcast workbench")
    parser.add_argument("--host", help="HTTP host")
    parser.add_argument("--port", type=int, help="HTTP port")
    parser.add_argument("--database", help="SQLite database path")
    parser.add_argument(
        "--adapter",
        choices=["mock", "lark", "feishu"],
        help="Message adapter to use",
    )
    parser.add_argument(
        "--scheduler-interval-seconds",
        type=int,
        help="Follow-up scan interval",
    )
    parser.add_argument(
        "--no-scheduler",
        action="store_true",
        help="Start API without background follow-up scheduler",
    )
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="Initialize database and exit",
    )
    args = parser.parse_args()

    settings = load_settings()
    if args.host:
        settings = replace(settings, host=args.host)
    if args.port:
        settings = replace(settings, port=args.port)
    if args.database:
        settings = replace(settings, database_path=args.database)
    if args.adapter:
        settings = replace(settings, message_adapter=args.adapter)
    if args.scheduler_interval_seconds:
        settings = replace(
            settings,
            scheduler_interval_seconds=args.scheduler_interval_seconds,
        )

    service = build_service(settings)
    if args.init_only:
        print("Database initialized at {}".format(settings.database_path), flush=True)
        return

    scheduler = FollowUpScheduler(service, settings.scheduler_interval_seconds)
    if not args.no_scheduler:
        scheduler.start()

    try:
        run_http_server(service, settings.host, settings.port)
    except KeyboardInterrupt:
        print("\nShutting down", flush=True)
    finally:
        scheduler.stop()


if __name__ == "__main__":
    main()
