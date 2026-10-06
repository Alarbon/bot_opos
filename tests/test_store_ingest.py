from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from oposiciones_bot.db import SCHEMA, SCHEMA_VERSION, SQLiteStore
from oposiciones_bot.models import (
    AccessType,
    Compatibility,
    EventKind,
    OutboxStatus,
    ProcessStatus,
)


def scalar(store: SQLiteStore, sql: str, params: tuple[object, ...] = ()) -> object:
    return store.connection.execute(sql, params).fetchone()[0]


def test_v1_database_migrates_canonical_key_to_a_non_unique_locator(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    legacy_schema = SCHEMA.replace(
        "canonical_key TEXT NOT NULL,", "canonical_key TEXT NOT NULL UNIQUE,"
    ).replace(
        "CREATE INDEX IF NOT EXISTS idx_processes_canonical_key ON processes(canonical_key);",
        "",
    ).replace("    exam_time TEXT,\n", "").replace(
        "    exam_place TEXT NOT NULL DEFAULT '',\n", ""
    )
    connection = sqlite3.connect(path)
    connection.executescript(legacy_schema)
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    migrated = SQLiteStore(path)
    try:
        assert scalar(migrated, "PRAGMA user_version") == SCHEMA_VERSION
        indexes = migrated.connection.execute("PRAGMA index_list(processes)").fetchall()
        canonical = [row for row in indexes if row[1] == "idx_processes_canonical_key"]
        assert canonical and canonical[0][2] == 0
        assert migrated.integrity_check() == "ok"
    finally:
        migrated.close()


def test_first_ingest_creates_process_source_and_pending_event_atomically(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    result = store.ingest(
        candidate_factory(), included_decision, now="2026-10-06T08:00:00+00:00"
    )

    assert result.action == "created"
    assert result.event_id
    assert store.stats() == {"processes": 1, "outbox_pending": 1}
    event = store.list_outbox()[0]
    assert event["kind"] == EventKind.NEW.value
    assert event["status"] == OutboxStatus.PENDING.value
    assert json.loads(event["payload_json"])["process_id"] == result.process_id
    assert scalar(store, "SELECT COUNT(*) FROM source_items") == 1
    assert scalar(store, "SELECT COUNT(*) FROM official_references") == 1
    assert store.integrity_check() == "ok"


def test_repeated_identical_ingest_does_not_enqueue_again(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    first = store.ingest(
        candidate_factory(), included_decision, now="2026-10-06T08:00:00+00:00"
    )
    second = store.ingest(
        candidate_factory(), included_decision, now="2026-10-06T12:00:00+00:00"
    )

    assert second.process_id == first.process_id
    assert second.action == "unchanged"
    assert second.event_id is None
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 1
    assert len(store.list_outbox()) == 1
    assert scalar(store, "SELECT last_seen FROM processes") == "2026-10-06T12:00:00+00:00"


def test_same_official_reference_from_another_source_is_one_process(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    first = store.ingest(candidate_factory(), included_decision)
    mirror = candidate_factory(
        source="pag",
        source_id="PAG-9988",
        url="https://administracion.gob.es/pagFront/empleoBecas/empleo/buscadorEmpleo.htm?id=9988",
        reference="BOE A 2026 1000",
        official_references=["BOE A 2026 1000"],
    )

    second = store.ingest(mirror, included_decision)

    assert second.process_id == first.process_id
    assert second.action == "unchanged"
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 1
    assert scalar(store, "SELECT COUNT(*) FROM source_items") == 2
    assert scalar(store, "SELECT COUNT(*) FROM process_urls") == 2
    assert len(store.list_outbox()) == 1


def test_fuzzy_matching_does_not_merge_equivalent_calls_from_different_years(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    first = candidate_factory(
        title="Convocatoria de Técnico Informático",
        publication_date="2025-10-01",
        source_id="BOE-A-2025-1000",
        reference="BOE-A-2025-1000",
        official_references=["BOE-A-2025-1000"],
    )
    second = candidate_factory(
        title="Convocatoria de Técnico Informático",
        publication_date="2026-10-01",
        source_id="BOE-A-2026-2000",
        reference="BOE-A-2026-2000",
        official_references=["BOE-A-2026-2000"],
    )

    first_result = store.ingest(first, included_decision)
    second_result = store.ingest(second, included_decision)

    assert first_result.process_id != second_result.process_id
    assert second_result.action == "created"
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 2


def test_shared_corps_code_never_merges_calls_from_different_years(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    first = candidate_factory(
        source_id="BOE-A-2025-1",
        publication_date="2025-01-01",
        reference="BOE-A-2025-1",
        official_references=["BOE-A-2025-1", "C1.2003"],
    )
    second = candidate_factory(
        source_id="BOE-A-2026-2",
        publication_date="2026-01-01",
        reference="BOE-A-2026-2",
        official_references=["BOE-A-2026-2", "C1.2003"],
    )

    first_result = store.ingest(first, included_decision)
    second_result = store.ingest(second, included_decision)

    assert first_result.process_id != second_result.process_id
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 2


def test_bop_and_boe_versions_merge_when_real_local_issuer_and_role_match(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    bop = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-1234",
        title="Bases y convocatoria para una plaza de Técnico de Informática",
        organisation="Ayuntamiento de Martos (Jaén)",
        reference="BOP-2026-1234",
        official_references=["BOP-2026-1234"],
        access=AccessType.UNKNOWN,
        publication_date="2026-09-20",
    )
    boe = candidate_factory(
        source="boe",
        source_id="BOE-A-2026-20000",
        title=(
            "Resolución de 1 de octubre de 2026, del Ayuntamiento de Martos "
            "(Jaén), referente a la convocatoria para proveer una plaza de "
            "Técnico de Informática"
        ),
        organisation="Ayuntamiento de Martos (Jaén)",
        reference="BOE-A-2026-20000",
        official_references=["BOE-A-2026-20000"],
        publication_date="2026-10-01",
    )

    first = store.ingest(bop, included_decision)
    second = store.ingest(boe, included_decision)

    assert second.process_id == first.process_id
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 1
    assert scalar(store, "SELECT COUNT(*) FROM source_items") == 2


def test_distinct_ids_from_same_source_are_not_merged_without_shared_reference(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    first = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-111",
        reference="BOP-2026-111",
        official_references=["BOP-2026-111"],
        positions=1,
    )
    second = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-222",
        reference="BOP-2026-222",
        official_references=["BOP-2026-222"],
        positions=2,
    )

    first_result = store.ingest(first, included_decision)
    second_result = store.ingest(second, included_decision)

    assert second_result.process_id != first_result.process_id
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 2


def test_explicit_citation_can_link_a_later_notice_from_the_same_source(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    first = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-111",
        reference="BOP-2026-111",
        official_references=["BOP-2026-111"],
    )
    later = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-222",
        reference="BOP-2026-222",
        official_references=["BOP-2026-222"],
        raw={"cited_references": ["BOP2026111"]},
    )

    first_result = store.ingest(first, included_decision)
    second_result = store.ingest(later, included_decision)

    assert second_result.process_id == first_result.process_id
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 1
    assert scalar(store, "SELECT COUNT(*) FROM source_items") == 2


def test_explicit_correction_can_change_places_without_splitting_process(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    original = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-1234",
        reference="BOP-2026-1234",
        official_references=["BOP-2026-1234"],
        positions=1,
    )
    correction = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-5678",
        reference="BOP-2026-5678",
        official_references=["BOP-2026-5678"],
        title="Corrección de errores de la convocatoria de Técnico Informático 2026",
        positions=2,
        raw={"cited_references": ["BOP20261234"]},
    )

    first = store.ingest(original, included_decision)
    second = store.ingest(correction, included_decision)

    assert second.process_id == first.process_id
    assert second.action == "updated"
    assert "numero de plazas" in second.changes
    assert scalar(store, "SELECT COUNT(*) FROM processes") == 1


def test_relevant_change_updates_pending_initial_event_instead_of_bursting(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    first = store.ingest(candidate_factory(), included_decision)
    updated = candidate_factory(
        status=ProcessStatus.EXAM_ANNOUNCED,
        exam_date="2026-12-12",
    )

    result = store.ingest(updated, included_decision)

    assert result.action == "updated"
    assert result.event_id == first.event_id
    assert "fecha de examen" in result.changes
    events = store.list_outbox()
    assert len(events) == 1
    assert events[0]["kind"] == EventKind.NEW.value
    payload = json.loads(events[0]["payload_json"])
    assert payload["candidate"]["exam_date"] == "2026-12-12"


def test_excluding_change_suppresses_an_unsent_initial_event(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    initial = store.ingest(candidate_factory(), included_decision)
    changed = candidate_factory(
        qualification_text="Requisito: Grado en Ingeniería Informática",
    )
    excluded = included_decision
    excluded.include = False
    changed.compatibility = Compatibility.NOT_COMPATIBLE

    result = store.ingest(changed, excluded)

    assert result.action == "updated"
    assert result.event_id is None
    events = store.list_outbox()
    assert len(events) == 1
    assert events[0]["status"] == OutboxStatus.SUPPRESSED.value


def test_relevant_change_after_initial_delivery_creates_one_update(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    initial = store.ingest(candidate_factory(), included_decision)
    assert initial.event_id
    claimed = store.claim_outbox(now="2026-10-06T08:01:00+00:00")
    assert claimed == [initial.event_id]
    store.mark_event(
        initial.event_id,
        OutboxStatus.SENT,
        message_id="telegram-100",
        now="2026-10-06T08:02:00+00:00",
    )
    changed = candidate_factory(
        status=ProcessStatus.EXAM_ANNOUNCED,
        exam_date="2026-12-12",
    )

    update = store.ingest(changed, included_decision)
    repeated = store.ingest(changed, included_decision)

    assert update.action == "updated"
    assert update.event_id and update.event_id != initial.event_id
    assert repeated.action == "unchanged"
    assert repeated.event_id is None
    events = store.list_outbox()
    assert len(events) == 2
    status_by_kind = {event["kind"]: event["status"] for event in events}
    assert status_by_kind == {
        EventKind.NEW.value: OutboxStatus.SENT.value,
        EventKind.UPDATE.value: OutboxStatus.PENDING.value,
    }


def test_exam_time_and_place_change_is_semantic_update(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    original = candidate_factory(
        exam_date="2026-11-20", exam_time="10:00", exam_place="Aula 1"
    )
    initial = store.ingest(original, included_decision)
    assert initial.event_id
    store.claim_outbox()
    store.mark_event(initial.event_id, OutboxStatus.SENT, message_id="10")

    changed = candidate_factory(
        exam_date="2026-11-20", exam_time="12:00", exam_place="Aula 9"
    )
    result = store.ingest(changed, included_decision)

    assert result.action == "updated"
    assert "hora de examen" in result.changes
    assert "lugar de examen" in result.changes
    assert result.event_id is not None


def test_pending_updates_are_compacted_to_latest_state(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    initial = store.ingest(candidate_factory(positions=1), included_decision)
    assert initial.event_id
    store.claim_outbox()
    store.mark_event(initial.event_id, OutboxStatus.SENT, message_id="10")

    first_update = store.ingest(candidate_factory(positions=2), included_decision)
    second_update = store.ingest(candidate_factory(positions=3), included_decision)

    assert first_update.event_id == second_update.event_id
    events = store.list_outbox()
    assert len(events) == 2
    update = next(event for event in events if event["kind"] == EventKind.UPDATE.value)
    payload = json.loads(update["payload_json"])
    assert payload["candidate"]["positions"] == 3
    assert payload["changes"] == ["numero de plazas"]


def test_state_cycle_can_notify_a_previously_seen_state_again(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    initial = store.ingest(candidate_factory(positions=1), included_decision)
    assert initial.event_id
    store.claim_outbox()
    store.mark_event(initial.event_id, OutboxStatus.SENT, message_id="10")

    state_b = candidate_factory(positions=2)
    first_update = store.ingest(state_b, included_decision)
    assert first_update.action == "updated"
    assert first_update.event_id

    state_c = candidate_factory(positions=3)
    compacted = store.ingest(state_c, included_decision)
    assert compacted.action == "updated"
    assert compacted.event_id == first_update.event_id
    store.claim_outbox()
    store.mark_event(compacted.event_id, OutboxStatus.SENT, message_id="11")

    returned_to_b = store.ingest(state_b, included_decision)
    assert returned_to_b.action == "updated"
    assert returned_to_b.event_id
    assert returned_to_b.event_id != first_update.event_id

    events = store.list_outbox()
    assert len(events) == 3
    statuses = {event["event_id"]: event["status"] for event in events}
    assert statuses[initial.event_id] == OutboxStatus.SENT.value
    assert statuses[first_update.event_id] == OutboxStatus.SENT.value
    assert statuses[returned_to_b.event_id] == OutboxStatus.PENDING.value


def test_new_linked_correction_notifies_even_without_extractable_field_change(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    original = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-111",
        reference="BOP-2026-111",
        official_references=["BOP-2026-111"],
    )
    initial = store.ingest(original, included_decision)
    assert initial.event_id
    store.claim_outbox()
    store.mark_event(initial.event_id, OutboxStatus.SENT, message_id="10")

    correction = candidate_factory(
        source="bop_jaen",
        source_id="BOP-2026-222",
        reference="BOP-2026-222",
        official_references=["BOP-2026-222"],
        title="Corrección de errores de las bases y convocatoria de Técnico Informático",
        raw={"cited_references": ["BOP2026111"]},
    )
    result = store.ingest(correction, included_decision)
    repeated = store.ingest(correction, included_decision)

    assert result.process_id == initial.process_id
    assert result.action == "updated"
    assert result.event_id is not None
    assert repeated.action == "unchanged"
    assert repeated.event_id is None
    events = store.list_outbox()
    assert len(events) == 2
    update = next(event for event in events if event["kind"] == EventKind.UPDATE.value)
    payload = json.loads(update["payload_json"])
    assert payload["changes"] == ["correccion o rectificacion oficial"]


def test_editorial_or_url_change_does_not_create_update(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    initial = store.ingest(candidate_factory(), included_decision)
    assert initial.event_id
    store.claim_outbox(now="2026-10-06T08:01:00+00:00")
    store.mark_event(initial.event_id, OutboxStatus.SENT, message_id="1")

    changed = candidate_factory(
        url="https://sede.martos.es/convocatorias/tecnico-informatico",
        summary="El mismo anuncio con un resumen editorial diferente",
    )
    result = store.ingest(changed, included_decision)

    assert result.action == "unchanged"
    assert result.event_id is None
    assert len(store.list_outbox()) == 1


def test_process_and_event_roll_back_together_when_event_creation_fails(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_event(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("fallo simulado al crear el outbox")

    monkeypatch.setattr(store, "_insert_event", fail_event)

    with pytest.raises(RuntimeError, match="fallo simulado"):
        store.ingest(candidate_factory(), included_decision)

    assert scalar(store, "SELECT COUNT(*) FROM processes") == 0
    assert scalar(store, "SELECT COUNT(*) FROM notification_outbox") == 0
    assert scalar(store, "SELECT COUNT(*) FROM source_items") == 0


def test_dry_run_copy_changes_only_the_in_memory_database(
    tmp_path: Path,
    candidate_factory,
    included_decision,
) -> None:
    path = tmp_path / "persistent.db"
    persistent = SQLiteStore(path)
    persistent.ingest(candidate_factory(), included_decision)
    persistent.close()
    before = path.read_bytes()

    dry_run = SQLiteStore.dry_run_copy(path)
    dry_run.ingest(
        candidate_factory(
            source_id="BOE-A-2027-2000",
            title="Convocatoria de Programador Informático 2027",
            publication_date="2027-01-01",
            reference="BOE-A-2027-2000",
            official_references=["BOE-A-2027-2000"],
        ),
        included_decision,
    )
    assert dry_run.stats()["processes"] == 2
    dry_run.close()

    assert path.read_bytes() == before
    reopened = SQLiteStore(path)
    try:
        assert reopened.stats()["processes"] == 1
    finally:
        reopened.close()


def test_dry_run_copy_of_missing_database_does_not_create_a_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "does-not-exist.db"

    dry_run = SQLiteStore.dry_run_copy(path)
    try:
        assert dry_run.is_empty()
        assert dry_run.integrity_check() == "ok"
    finally:
        dry_run.close()

    assert not path.exists()
