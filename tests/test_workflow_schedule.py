from pathlib import Path

import yaml


def test_daily_search_keeps_spanish_local_hours():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/automatico.yml"
    # BaseLoader keeps YAML 1.1's "on" key as a string, like GitHub's parser.
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"] == [
        {"cron": "30 8,12,17 * * *", "timezone": "Europe/Madrid"},
        {"cron": "0 21 * * *", "timezone": "Europe/Madrid"}
    ]
    search = yaml.load((path.parent / "oposiciones.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert "schedule" not in search["on"]
    assert "workflow_dispatch" in search["on"]
    assert workflow["permissions"]["actions"] == "write"


def test_report_never_publishes_unaccepted_claim_commit():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/oposiciones.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["search"]["steps"]
    publish = next(step for step in steps if step.get("name") == "Guardar resumen")
    assert "steps.persist_claim.outcome != 'failure'" in publish["if"]
    notify = next(step for step in steps if step.get("name") == "Enviar resumen a Telegram")
    assert "always()" in notify["if"]
