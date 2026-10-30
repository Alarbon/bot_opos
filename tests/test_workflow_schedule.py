import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oposiciones.yml"


def load_workflow():
    # BaseLoader keeps YAML 1.1's "on" key as a string, like GitHub's parser.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def load_gate():
    path = ROOT / "scripts" / "schedule_gate.py"
    spec = importlib.util.spec_from_file_location("schedule_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manual_search_is_kept_and_daily_schedule_is_declared():
    workflow = load_workflow()
    assert set(workflow["on"]) == {"workflow_dispatch", "schedule"}
    assert "dry_run" in workflow["on"]["workflow_dispatch"]["inputs"]
    assert "automatic" not in workflow["on"]["workflow_dispatch"]["inputs"]
    crons = sorted(item["cron"] for item in workflow["on"]["schedule"])
    assert crons == ["17 6 * * *", "17 7 * * *"]
    assert not (WORKFLOW.parent / "automatico.yml").exists()


def test_search_job_is_gated_serialised_and_uses_current_branch_state():
    workflow = load_workflow()
    assert workflow["concurrency"]["group"] == "oposiciones-production-state"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    search = workflow["jobs"]["search"]
    assert search["needs"] == "gate"
    assert "needs.gate.outputs.run == 'true'" in search["if"]
    checkout = search["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.ref }}"
    seguimiento = yaml.load(
        (WORKFLOW.parent / "seguimiento.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert seguimiento["concurrency"]["group"] == workflow["concurrency"]["group"]


def test_bop_proxy_prefers_dedicated_secret():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("secrets.BOP_PROXY_TOKEN || secrets.TELEGRAM_BOT_TOKEN") == 2


def test_report_never_publishes_unaccepted_claim_commit():
    workflow = load_workflow()
    steps = workflow["jobs"]["search"]["steps"]
    publish = next(step for step in steps if step.get("name") == "Guardar resumen")
    assert "steps.persist_claim.outcome != 'failure'" in publish["if"]
    notify = next(step for step in steps if step.get("name") == "Enviar resumen a Telegram")
    assert "always()" in notify["if"]
    gate = steps[-1]
    assert "--check-outcome" in gate["run"]
    assert "always()" in gate["if"]


@pytest.mark.parametrize(
    ("now", "cron", "expected"),
    [
        # Summer (CEST, UTC+2): 06:17 UTC is 08:17 in Madrid.
        (datetime(2026, 7, 1, 6, 40, tzinfo=timezone.utc), "17 6 * * *", True),
        (datetime(2026, 7, 1, 7, 20, tzinfo=timezone.utc), "17 7 * * *", False),
        # Winter (CET, UTC+1): 07:17 UTC is 08:17 in Madrid.
        (datetime(2026, 12, 1, 6, 20, tzinfo=timezone.utc), "17 6 * * *", False),
        (datetime(2026, 12, 1, 7, 20, tzinfo=timezone.utc), "17 7 * * *", True),
        # DST ends on 2026-10-25 at 01:00 UTC; only the winter cron runs.
        (datetime(2026, 10, 25, 6, 17, tzinfo=timezone.utc), "17 6 * * *", False),
        (datetime(2026, 10, 25, 7, 17, tzinfo=timezone.utc), "17 7 * * *", True),
        # DST starts on 2026-03-29 at 01:00 UTC; only the summer cron runs.
        (datetime(2026, 3, 29, 6, 17, tzinfo=timezone.utc), "17 6 * * *", True),
        (datetime(2026, 3, 29, 7, 17, tzinfo=timezone.utc), "17 7 * * *", False),
        # A run delayed by GitHub for hours is judged by its nominal cron time.
        (datetime(2026, 12, 1, 9, 55, tzinfo=timezone.utc), "17 6 * * *", False),
        (datetime(2026, 12, 1, 9, 55, tzinfo=timezone.utc), "17 7 * * *", True),
    ],
)
def test_schedule_gate_lets_exactly_one_daily_run_through(now, cron, expected):
    assert load_gate().should_run("schedule", cron, now) is expected


def test_schedule_gate_never_blocks_manual_runs():
    gate = load_gate()
    now = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
    assert gate.should_run("workflow_dispatch", "", now)
    assert gate.should_run("schedule", "not a cron", now)


def test_each_calendar_day_has_exactly_one_scheduled_run():
    gate = load_gate()
    for month in range(1, 13):
        for day in (1, 15, 28):
            now = datetime(2026, month, day, 12, 0, tzinfo=timezone.utc)
            allowed = [cron for cron in ("17 6 * * *", "17 7 * * *") if gate.should_run("schedule", cron, now)]
            assert len(allowed) == 1, (month, day, allowed)
