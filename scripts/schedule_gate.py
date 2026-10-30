"""Decide whether a scheduled run matches the intended Spanish local hour.

GitHub cron expressions are evaluated in UTC and know nothing about daylight
saving time.  The workflow therefore declares two crons (one valid in summer,
one in winter) and this gate lets exactly one of them through each day.  The
decision uses the cron's nominal time, not the moment the job starts, so a
GitHub delay of minutes or hours never lets both runs pass.

Standard library only: it runs before dependencies are installed.
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TARGET_LOCAL_HOUR = 8
TIMEZONE = "Europe/Madrid"


def should_run(event: str, schedule: str, now_utc: datetime) -> bool:
    if event != "schedule":
        return True
    match = re.fullmatch(r"\s*(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*\s*", schedule or "")
    if not match:
        # Unknown expression: prefer running over silently skipping a day.
        return True
    minute, hour = int(match.group(1)), int(match.group(2))
    nominal = now_utc.astimezone(timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return nominal.astimezone(ZoneInfo(TIMEZONE)).hour == TARGET_LOCAL_HOUR


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--schedule", default=os.environ.get("SCHEDULE", ""))
    args = parser.parse_args()
    decision = should_run(args.event, args.schedule, datetime.now(timezone.utc))
    print(
        f"evento={args.event or '-'} cron={args.schedule or '-'} "
        f"ejecutar={'si' if decision else 'no (otra franja horaria de verano/invierno)'}"
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'true' if decision else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
