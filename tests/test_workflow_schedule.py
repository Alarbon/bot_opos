from pathlib import Path

import yaml


def test_search_is_manual_only():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/oposiciones.yml"
    # BaseLoader keeps YAML 1.1's "on" key as a string, like GitHub's parser.
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert "automatic" not in workflow["on"]["workflow_dispatch"]["inputs"]
    assert not (path.parent / "automatico.yml").exists()


def test_report_never_publishes_unaccepted_claim_commit():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/oposiciones.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["search"]["steps"]
    publish = next(step for step in steps if step.get("name") == "Guardar resumen")
    assert "steps.persist_claim.outcome != 'failure'" in publish["if"]
    notify = next(step for step in steps if step.get("name") == "Enviar resumen a Telegram")
    assert "always()" in notify["if"]
