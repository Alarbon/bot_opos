from pathlib import Path
from datetime import date
from bs4 import BeautifulSoup
from oposiciones_bot.sources.iaap_fields import apply_process_fields
from oposiciones_bot.classifiers import evaluate_candidate, can_apply
from oposiciones_bot.models import AccessType, Compatibility, ProcessStatus

FIXTURE = Path(__file__).parent / "fixtures" / "iaap_b12000.html"


def test_real_b12000_fields_override_year_and_unrelated_mixed_access(candidate_factory, app_config):
    candidate = candidate_factory(title="B1.2000 Tecnologías de la Información y Comunicación", positions=2026, access=AccessType.MIXED)
    assert apply_process_fields(candidate, BeautifulSoup(FIXTURE.read_text(encoding="utf8"), "lxml"))
    assert candidate.positions == 20
    assert candidate.access is AccessType.INTERNAL_ONLY
    assert candidate.group == "B"
    assert candidate.deadline == "2026-06-23" and candidate.deadline_confirmed
    assert candidate.publication_date is None
    decision = evaluate_candidate(candidate, app_config)
    assert not decision.include
    assert candidate.compatibility is Compatibility.NOT_COMPATIBLE
    assert candidate.status is ProcessStatus.MARKS
    assert not can_apply(candidate, date(2026, 6, 12))
    assert candidate.raw["structured_evidence"]["positions"] == "20"


def test_c12003_structured_total_and_additional_vacancies_are_not_new_enrolment(candidate_factory, app_config):
    html = (FIXTURE.parent / "iaap_c12003.html").read_text(encoding="utf8")
    candidate = candidate_factory(title="C1.2003 Ayudantes técnicos - Opción Informática")
    apply_process_fields(candidate, BeautifulSoup(html, "lxml"))
    evaluate_candidate(candidate, app_config)
    assert candidate.positions == 40 and candidate.access is AccessType.OPEN
    assert candidate.status is ProcessStatus.MARKS
    assert not candidate.deadline_confirmed and not can_apply(candidate, date(2026, 11, 1))


def test_repair_copy_preserves_original_history_and_outbox(store, candidate_factory, included_decision, app_config, tmp_path, monkeypatch):
    import sqlite3
    from scripts.repair_iaap_copy import repair_copy
    url = "https://portalempleopublico.juntadeandalucia.es/sede/acceso-tramites/seguimiento-procesos-selectivos/P2B12000"
    candidate = candidate_factory(title="B1.2000 Tecnologías de la Información y Comunicación", positions=2026, access=AccessType.MIXED, url=url)
    result = store.ingest(candidate, included_decision)
    store.follow(result.process_id)
    original = tmp_path / "oposiciones.db"
    before = original.read_bytes()
    output = tmp_path / "repaired.db"
    audit = repair_copy(original, output, {url: FIXTURE}, app_config)
    assert original.read_bytes() == before
    assert audit[0]["before"]["positions"] == 2026
    with sqlite3.connect(output) as db:
        assert db.execute("SELECT positions,access FROM processes").fetchone() == (20, "PROMOCION_INTERNA_EXCLUSIVA")
        assert db.execute("SELECT count(*) FROM followed_processes").fetchone()[0] == 1
        assert db.execute("SELECT * FROM notification_outbox").fetchall() == [tuple(r) for r in store.connection.execute("SELECT * FROM notification_outbox")]
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert output.with_suffix(".audit.json").exists()
    monkeypatch.setattr("scripts.repair_iaap_copy.utc_now_iso", lambda: "2099-01-01T00:00:00+00:00")
    repeated = tmp_path / "repeated.db"
    repair_copy(output, repeated, {url: FIXTURE}, app_config)
    with sqlite3.connect(output) as first, sqlite3.connect(repeated) as second:
        tables = [r[0] for r in first.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            assert first.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() == second.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
    import pytest
    with pytest.raises(ValueError): repair_copy(original, original, {url: FIXTURE}, app_config)


def test_future_observation_replaces_bad_structured_fields_without_new_process(store, candidate_factory, included_decision, app_config):
    original = candidate_factory(title="B1.2000 Tecnologías de la Información y Comunicación", positions=2026, access=AccessType.MIXED)
    first = store.ingest(original, included_decision, notify=False)
    apply_process_fields(original, BeautifulSoup(FIXTURE.read_text(encoding="utf8"), "lxml"))
    decision = evaluate_candidate(original, app_config)
    second = store.ingest(original, decision, notify=False)
    assert first.process_id == second.process_id
    row = store.connection.execute("SELECT * FROM processes").fetchone()
    assert row["positions"] == 20 and row["deadline"] == "2026-06-23"
    assert row["organisation"].startswith("Junta de Andalucía")
    assert row["publication_date"] is None
