import importlib.util
from pathlib import Path


def load_module(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_no_news_still_explains_search():
    module = load_module("run_report")
    report = module.create_report({"collection": {"sources_attempted": 8, "errors": {}, "fetched": 12, "created": 0, "updated": 0}, "delivery": {"sent": 0}}, "success", {"GITHUB_EVENT_NAME": "schedule", "GITHUB_RUN_ID": "1", "GITHUB_REPOSITORY": "Alarbon/bot_opos"})
    text = module.render_report(report)
    assert "BÚSQUEDA COMPLETADA" in text and "Automática" in text
    assert "8 correctas / 0 fallidas" in text
    assert "enviados: 0" in text


def test_summary_partial_failure_not_called_complete():
    module = load_module("run_report")
    report = module.create_report({"collection": {"sources_attempted": 8, "errors": {"bop_jaen": "500"}}}, "success", {})
    assert "COBERTURA INCOMPLETA" in module.render_report(report)
    assert "bop_jaen" in module.render_report(report)


def test_failure_without_collection_does_not_claim_zero_results():
    module = load_module("run_report")
    report = module.create_report({}, "failure", {"PERSIST_OUTCOME": "failure"})
    text = module.render_report(report)
    assert "BÚSQUEDA FALLIDA" in text and "No se completó" in text
    assert "Pasos fallidos: guardado" in text
    assert "Registros examinados" not in text
