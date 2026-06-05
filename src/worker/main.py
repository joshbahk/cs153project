"""Polling worker for queued paper runs."""

from __future__ import annotations

import argparse
import time

from db.session import create_app_engine, create_session_factory, init_database
from web.settings import get_settings
from worker.jobs import process_next_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replication-triage worker")
    parser.add_argument("--once", action="store_true", help="Process one queued run and exit.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()
    engine = create_app_engine(settings.database_url)
    init_database(engine)
    session_factory = create_session_factory(engine)

    if args.once:
        process_next_run(session_factory, settings)
        return

    while True:
        processed = process_next_run(session_factory, settings)
        if not processed:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
