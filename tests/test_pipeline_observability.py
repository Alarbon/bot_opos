"""Run summaries, partial failures, idempotency and delivery edge cases."""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pytest

from oposiciones_bot.config import load_config
from oposiciones_bot.db import MAX_DELIVERY_ATTEMPTS, SQLiteStore
from oposiciones_bot.enrichment import extract_relative_deadline
from oposiciones_bot.models import AccessType, Compatibility, EventKind, OutboxStatus, ProcessStatus
from oposiciones_bot.pipeline import collect, dispatch, redact_secrets
from oposiciones_bot.telegram import TelegramAmbiguousError, TelegramError, render_event

ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = load_config(ROOT / "config.yaml")


@dataclass
class Source:
    name: str
    candidates: list = field(default_factory=list)
    error: Exception | None = None
    warning: str | None = None

    def fetch(self, _context):
        if self.error:
            raise self.error
        if self.warning:
            self.coverage_warning = self.warning
        return list(self.candidates)


def run(store, config, sources, today=date(2026, 10, 30)):
    config.data.setdefault("reminders", {"enabled": False})
    return collect(config=config, store=store, client=object(), sources=[(s, {}) for s in sources], today=today)


# --- "no news" versus "nothing could be consulted" ---------------------------


def test_successful_run_without_news_is_ok(store, app_config):
    summary = run(store, app_config, [Source("boe"), Source("boja")])
    assert summary.outcome == "OK"
    assert summary.fetched == 0 and not summary.errors
    assert summary.sources["boe"]["status"] == "OK"
    assert summary.to_dict()["outcome"] == "OK"


def test_run_where_every_source_failed_is_not_no_news(store, app_config):
    summary = run(store, app_config, [Source("boe", error=RuntimeError("500")), Source("boja", error=OSError("dns"))])
    assert summary.outcome == "NO_SOURCES"
    assert summary.sources["boja"]["status"] == "ERROR"
    assert store.collection_checkpoint("boe") is None


def test_partially_failed_run_keeps_other_sources(store, app_config, candidate_factory):
    good = candidate_factory(source="boja", source_id="BOJA-1", reference="BOJA-1", official_references=["BOJA-1"])
    summary = run(store, app_config, [Source("boe", error=RuntimeError("500")), Source("boja", [good])])
    assert summary.outcome == "PARTIAL"
    assert summary.created == 1
    assert summary.sources["boja"]["created"] == 1
    assert "boe" in summary.errors


def test_source_errors_never_store_secrets(store, app_config):
    token = "123456789:AAH" + "x" * 32
    error = RuntimeError(f"fallo en https://api.telegram.org/bot{token}/sendMessage Authorization: Bearer {token}")
    summary = run(store, app_config, [Source("boe", error=error)])
    assert token not in summary.errors["boe"]
    stored = store.connection.execute("SELECT error FROM source_runs").fetchone()[0]
    assert token not in stored and "<redacted>" in stored
    assert redact_secrets("Bearer abcdefghijklmnop") == "Bearer <redacted>"


def test_window_of_a_long_stuck_source_is_capped_and_reported(store, app_config):
    app_config.data["collection"] = {"incremental": True, "lookback_days": 10, "max_lookback_days": 45}
    store.connection.execute("INSERT INTO source_checkpoints VALUES ('bop_jaen', '2026-08-01')")
    store.connection.commit()
    seen = []

    class Recording(Source):
        def fetch(self, context):
            seen.append(context.lookback_days)
            return []

    summary = run(store, app_config, [Recording("bop_jaen")])
    assert seen == [45]
    assert "sin cobertura acreditada desde hace 90 días" in summary.warnings["bop_jaen"]


# --- relative deadlines become REVISAR notices ---------------------------------

RELATIVE = (
    "Convocatoria de una plaza de Técnico Informático, grupo C1, turno libre. Requisitos: "
    "estar en posesión del título de Bachiller o Técnico. El plazo de presentación de solicitudes será "
    "de veinte días hábiles contados a partir del día siguiente al de la publicación del extracto en el "
    "Boletín Oficial del Estado."
)


def relative_candidate(candidate_factory, published="2026-10-20"):
    return candidate_factory(
        source="bop_jaen", source_id="BOP-2026-5000", reference="BOP-2026-5000",
        official_references=["BOP-2026-5000"], publication_date=published,
        deadline=None, deadline_confirmed=False, status=ProcessStatus.ANNOUNCED,
        full_text=RELATIVE, raw={"relative_deadline": extract_relative_deadline(RELATIVE)},
    )


def test_relative_deadline_creates_one_review_notice_not_a_confirmed_one(store, candidate_factory):
    config = load_config(ROOT / "config.yaml")
    summary = run(store, config, [Source("bop_jaen", [relative_candidate(candidate_factory)])])
    events = store.list_outbox()
    assert [event["kind"] for event in events] == [EventKind.REVIEW.value]
    assert summary.notified_new == 1
    text = render_event(events[0])
    assert "POSIBLE OPORTUNIDAD — REVISAR" in text
    assert "20 días hábiles desde la publicación en BOE" in text
    assert "fecha exacta sin confirmar" in text
    # Idempotent: the same observation again creates no second notice.
    run(store, config, [Source("bop_jaen", [relative_candidate(candidate_factory)])])
    assert len(store.list_outbox()) == 1


def test_old_relative_deadline_is_kept_silently_and_counted(store, candidate_factory):
    config = load_config(ROOT / "config.yaml")
    summary = run(store, config, [Source("bop_jaen", [relative_candidate(candidate_factory, "2026-06-01")])])
    assert store.list_outbox() == []
    assert summary.created == 1 and summary.silent_new == 1


# --- change notices show old and new values ----------------------------------


def test_exam_change_notice_shows_previous_and_new_value_with_source(store, candidate_factory, included_decision):
    first = store.ingest(candidate_factory(exam_date="2026-11-10", exam_place="Aula 1"), included_decision)
    store.claim_outbox()
    store.mark_event(first.event_id, OutboxStatus.SENT, message_id="1")
    changed = candidate_factory(
        exam_date="2026-11-17", exam_place="Pabellón municipal",
        source_id="BOP-2026-7000", reference="BOE-A-2026-1000",
        url="https://bop.dipujaen.es/descargarws.dip?numeroEdicto=7000",
    )
    result = store.ingest(changed, included_decision)
    event = next(e for e in store.list_outbox() if e["event_id"] == result.event_id)
    payload = json.loads(event["payload_json"])
    details = {item["field"]: item for item in payload["change_details"]}
    assert details["exam_date"]["old"] == "2026-11-10"
    assert details["exam_date"]["new"] == "2026-11-17"
    assert details["exam_place"]["old"] == "aula 1"
    text = render_event(event)
    assert "10/11/2026 → <b>17/11/2026</b>" in text
    assert "Documento que lo indica" in text and "numeroEdicto=7000" in text


def test_compacted_pending_update_keeps_original_old_value(store, candidate_factory, included_decision):
    first = store.ingest(candidate_factory(exam_date="2026-11-10"), included_decision)
    store.claim_outbox()
    store.mark_event(first.event_id, OutboxStatus.SENT, message_id="1")
    store.ingest(candidate_factory(exam_date="2026-11-17"), included_decision)
    store.ingest(candidate_factory(exam_date="2026-11-24"), included_decision)
    updates = [e for e in store.list_outbox() if e["kind"] == EventKind.UPDATE.value]
    assert len(updates) == 1
    detail = json.loads(updates[0]["payload_json"])["change_details"][0]
    assert (detail["old"], detail["new"]) == ("2026-11-10", "2026-11-24")


# --- outbox delivery -------------------------------------------------------------


class FakeTelegram:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent = []

    def send(self, text, **kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.sent.append(text)
        return outcome


def three_events(store, candidate_factory, included_decision):
    ids = []
    for index in range(3):
        result = store.ingest(
            candidate_factory(source_id=f"BOE-A-2026-{index}", reference=f"BOE-A-2026-{index}",
                              official_references=[f"BOE-A-2026-{index}"], title=f"Técnico informático {index}",
                              organisation=f"Ayuntamiento {index}"),
            included_decision,
        )
        ids.append(result.event_id)
    return ids


def test_rate_limit_stops_the_batch_and_releases_unsent_claims(store, candidate_factory, included_decision):
    three_events(store, candidate_factory, included_decision)
    telegram = FakeTelegram(["10", TelegramError("Too Many Requests", retry_after=30, status_code=429)])
    sleeps = []
    result = dispatch(store=store, telegram=telegram, limit=10, sleep=sleeps.append)
    assert (result.sent, result.retryable, result.released) == (1, 1, 1)
    statuses = sorted(event["status"] for event in store.list_outbox())
    assert statuses == sorted([OutboxStatus.SENT.value, OutboxStatus.RETRYABLE.value, OutboxStatus.PENDING.value])
    assert sleeps == [1.0]  # paced between the two real attempts


def test_ambiguous_delivery_is_never_retried_automatically(store, candidate_factory, included_decision):
    three_events(store, candidate_factory, included_decision)
    telegram = FakeTelegram([TelegramAmbiguousError("timeout"), "2", "3"])
    result = dispatch(store=store, telegram=telegram, limit=10, min_interval_seconds=0)
    assert result.uncertain == 1 and result.sent == 2
    assert store.claim_outbox() == []


def test_permanently_rejected_message_stops_being_claimed(store, candidate_factory, included_decision):
    event_id = store.ingest(candidate_factory(), included_decision).event_id
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        assert store.claim_outbox() == [event_id]
        store.mark_event(event_id, OutboxStatus.RETRYABLE, error="Bad Request: can't parse entities")
    assert store.claim_outbox() == []
    assert store.list_outbox()[0]["status"] == OutboxStatus.RETRYABLE.value
    assert store.retry_event(event_id)
    assert store.claim_outbox() == [event_id]


def test_two_concurrent_claimers_never_claim_the_same_event(tmp_path, candidate_factory, included_decision):
    path = tmp_path / "shared.db"
    first, second = SQLiteStore(path), SQLiteStore(path)
    try:
        three_events(first, candidate_factory, included_decision)
        a = first.claim_outbox(limit=2)
        b = second.claim_outbox(limit=10)
        assert len(a) == 2 and len(b) == 1 and not set(a) & set(b)
    finally:
        first.close()
        second.close()


def test_migration_keeps_existing_rows_and_takes_a_consistent_copy(tmp_path, candidate_factory, included_decision):
    """Opening an existing v6 database must not alter or drop its data."""
    path = tmp_path / "v6.db"
    store = SQLiteStore(path)
    store.ingest(candidate_factory(), included_decision)
    store.close()
    before = sqlite3.connect(path).execute("SELECT COUNT(*), MAX(id) FROM processes").fetchone()
    reopened = SQLiteStore(path)
    try:
        assert reopened.integrity_check() == "ok"
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert tuple(reopened.connection.execute("SELECT COUNT(*), MAX(id) FROM processes").fetchone()) == before
    finally:
        reopened.close()


# --- run report ------------------------------------------------------------------


def load_report():
    spec = importlib.util.spec_from_file_location("run_report", ROOT / "scripts" / "run_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_distinguishes_total_outage_from_no_news():
    module = load_report()
    outage = module.create_report({"collection": {"sources_attempted": 2, "errors": {"boe": "RetryError: too many 500 error responses", "boja": "x"}, "fetched": 0}}, "success", {})
    text = module.render_report(outage)
    assert "NINGUNA FUENTE CONSULTADA" in text
    assert "no hay datos para afirmar que no haya novedades" in text
    assert "el servidor oficial devolvió errores 500" in text
    quiet = module.create_report({"collection": {"sources_attempted": 2, "errors": {}, "fetched": 0, "outcome": "OK"}}, "success", {})
    assert "✅ BÚSQUEDA COMPLETADA" in module.render_report(quiet)


def test_report_includes_classification_and_silent_new_processes():
    module = load_report()
    report = module.create_report({"collection": {
        "sources_attempted": 1, "errors": {}, "fetched": 3, "created": 2, "updated": 0,
        "compatible": 1, "review": 2, "not_compatible": 0, "silent_new": 1, "pending_notifications": 1,
    }, "delivery": {"sent": 1, "retryable": 0, "uncertain": 0, "suppressed": 1}}, "success", {})
    text = module.render_report(report)
    assert "1 compatibles, 2 por revisar" in text
    assert "1 proceso(s) nuevo(s) sin aviso individual" in text
    assert "descartados antes de enviar" in text


def test_long_report_fits_in_one_telegram_message():
    module = load_report()
    warnings = {f"fuente_{i}": "aviso & detalle <largo> " * 20 for i in range(30)}
    report = module.create_report({"collection": {"sources_attempted": 30, "errors": {}, "warnings": warnings, "fetched": 0}}, "success", {})
    text = module.render_report(report)
    assert len(text) < 4096
    assert "detalle completo en GitHub" in text
    # Never ends with a broken entity such as "&am".
    assert not re.search(r"&[a-z]*$", text.split("\n… (detalle")[0])


@pytest.mark.parametrize(("collection", "code"), [
    ({"sources_attempted": 2, "errors": {"a": "x", "b": "y"}}, 1),
    ({"sources_attempted": 2, "errors": {"a": "x"}}, 0),
    (None, 1),
])
def test_check_outcome_exit_code(tmp_path, monkeypatch, collection, code):
    import subprocess
    import sys

    data = tmp_path / "data"
    data.mkdir()
    metrics = {} if collection is None else {"collection": collection}
    (data / "run_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_report.py"), "--status", "success", "--check-outcome"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == code
