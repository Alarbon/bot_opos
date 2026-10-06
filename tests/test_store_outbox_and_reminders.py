from __future__ import annotations

from datetime import date

from oposiciones_bot.db import SQLiteStore
from oposiciones_bot.models import (
    AccessType,
    Compatibility,
    EventKind,
    OutboxStatus,
    ProcessStatus,
)


def test_claim_is_single_use_until_an_operator_resolves_uncertain_delivery(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    result = store.ingest(candidate_factory(), included_decision)
    assert result.event_id

    assert store.claim_outbox(now="2000-01-01T00:00:00+00:00") == [result.event_id]
    assert store.claim_outbox(now="2000-01-01T00:01:00+00:00") == []
    sending = store.list_outbox()[0]
    assert sending["status"] == OutboxStatus.SENDING.value
    assert sending["attempts"] == 1

    assert store.recover_stale_sending(older_than_minutes=30) == 1
    uncertain = store.list_outbox()[0]
    assert uncertain["status"] == OutboxStatus.UNCERTAIN.value
    assert "revisar Telegram" in uncertain["last_error"]
    assert store.claim_outbox() == []

    assert store.retry_event(result.event_id)
    assert store.claim_outbox(now="2026-10-06T09:00:00+00:00") == [result.event_id]
    store.mark_event(
        result.event_id,
        OutboxStatus.SENT,
        message_id="telegram-42",
        now="2026-10-06T09:01:00+00:00",
    )
    sent = store.list_outbox()[0]
    assert sent["status"] == OutboxStatus.SENT.value
    assert sent["attempts"] == 2
    assert sent["telegram_message_id"] == "telegram-42"
    assert sent["sent_at"] == "2026-10-06T09:01:00+00:00"
    assert store.claim_outbox() == []
    assert not store.retry_event(result.event_id)
    assert not store.suppress_event(result.event_id)


def test_sent_is_terminal_even_if_a_late_failure_callback_arrives(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    result = store.ingest(candidate_factory(), included_decision)
    assert result.event_id
    store.claim_outbox(now="2026-10-06T09:00:00+00:00")
    store.mark_event(
        result.event_id,
        OutboxStatus.SENT,
        message_id="telegram-42",
        now="2026-10-06T09:01:00+00:00",
    )

    store.mark_event(
        result.event_id,
        OutboxStatus.RETRYABLE,
        error="callback tardío",
        now="2026-10-06T09:02:00+00:00",
    )

    event = store.list_outbox()[0]
    assert event["status"] == OutboxStatus.SENT.value
    assert event["telegram_message_id"] == "telegram-42"
    assert store.claim_outbox() == []


def test_definitely_unsent_claim_can_return_to_pending(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    result = store.ingest(candidate_factory(), included_decision)
    assert result.event_id
    assert store.claim_outbox() == [result.event_id]

    assert store.release_unsent_claims(
        [result.event_id], error="fallo antes de Telegram"
    ) == 1

    event = store.list_outbox()[0]
    assert event["status"] == OutboxStatus.PENDING.value
    assert event["attempts"] == 0
    assert event["attempted_at"] is None
    assert store.claim_outbox() == [result.event_id]


def test_confirmed_deadline_creates_each_reminder_only_once(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    store.ingest(candidate_factory(deadline="2026-10-20"), included_decision, notify=False)

    assert store.enqueue_reminders(date(2026, 10, 13), [7, 3, 1]) == 1
    assert store.enqueue_reminders(date(2026, 10, 13), [7, 3, 1]) == 0

    events = store.list_outbox()
    assert len(events) == 1
    assert events[0]["kind"] == EventKind.REMINDER.value
    assert events[0]["status"] == OutboxStatus.PENDING.value
    assert store.connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1


def test_changed_deadline_gets_a_new_reminder_key(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    store.ingest(candidate_factory(deadline="2026-10-20"), included_decision, notify=False)
    assert store.enqueue_reminders(date(2026, 10, 13), [7]) == 1

    changed = candidate_factory(deadline="2026-10-23")
    update = store.ingest(changed, included_decision, notify=False)
    assert update.action == "updated"
    assert "fecha limite" in update.changes
    assert store.enqueue_reminders(date(2026, 10, 16), [7]) == 1
    assert store.enqueue_reminders(date(2026, 10, 16), [7]) == 0

    rows = store.connection.execute(
        "SELECT deadline,days_before FROM reminders ORDER BY deadline"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("2026-10-20", 7),
        ("2026-10-23", 7),
    ]


def test_unconfirmed_or_closed_deadline_never_creates_reminder(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    store.ingest(
        candidate_factory(deadline="2026-10-20", deadline_confirmed=False),
        included_decision,
        notify=False,
    )
    store.ingest(
        candidate_factory(
            source_id="BOE-A-2026-2000",
            title="Convocatoria de Operador Informático 2026",
            reference="BOE-A-2026-2000",
            official_references=["BOE-A-2026-2000"],
            deadline="2026-10-20",
            status=ProcessStatus.CLOSED,
        ),
        included_decision,
        notify=False,
    )
    store.ingest(
        candidate_factory(
            source_id="BOE-A-2026-3000",
            title="Convocatoria suspendida de Programador Informático 2026",
            reference="BOE-A-2026-3000",
            official_references=["BOE-A-2026-3000"],
            deadline="2026-10-20",
            status=ProcessStatus.SUSPENDED,
        ),
        included_decision,
        notify=False,
    )

    assert store.enqueue_reminders(date(2026, 10, 13), [7]) == 0
    assert store.list_outbox() == []


def test_incompatible_or_internal_process_never_creates_reminder(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    store.ingest(
        candidate_factory(
            compatibility=Compatibility.NOT_COMPATIBLE,
            deadline="2026-10-20",
        ),
        included_decision,
        notify=False,
    )
    store.ingest(
        candidate_factory(
            source_id="BOE-A-2026-2000",
            title="Convocatoria de Operador Informático 2026",
            reference="BOE-A-2026-2000",
            official_references=["BOE-A-2026-2000"],
            access=AccessType.INTERNAL_ONLY,
            deadline="2026-10-20",
        ),
        included_decision,
        notify=False,
    )

    assert store.enqueue_reminders(date(2026, 10, 13), [7]) == 0
    assert store.list_outbox() == []
