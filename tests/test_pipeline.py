from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from oposiciones_bot.models import Compatibility, EventKind, OutboxStatus
from oposiciones_bot.pipeline import _should_notify_on_bootstrap, collect


@dataclass
class StaticSource:
    candidate: object
    name: str = "boe"

    def fetch(self, _context):
        return [self.candidate]


def test_bootstrap_notifies_old_publication_with_confirmed_future_deadline(
    candidate_factory,
) -> None:
    candidate = candidate_factory(
        publication_date="2026-08-01",
        deadline="2026-10-20",
        deadline_confirmed=True,
    )

    assert _should_notify_on_bootstrap(candidate, date(2026, 10, 6), 14)


def test_bootstrap_notifies_future_exam_even_after_application_deadline(
    candidate_factory,
) -> None:
    candidate = candidate_factory(
        publication_date="2026-08-01",
        deadline="2026-09-01",
        deadline_confirmed=True,
        exam_date="2026-10-20",
    )

    assert _should_notify_on_bootstrap(candidate, date(2026, 10, 6), 14)


def test_known_process_keeps_an_excluding_correction(
    store,
    candidate_factory,
    included_decision,
    app_config,
) -> None:
    original = candidate_factory()
    initial = store.ingest(original, included_decision)
    assert initial.event_id
    store.claim_outbox(now="2026-10-06T08:01:00+00:00")
    store.mark_event(initial.event_id, OutboxStatus.SENT, message_id="100")

    corrected = candidate_factory(
        qualification_text="Requisito: Grado en Ingeniería Informática",
        full_text=(
            "Convocatoria de Técnico Informático por turno libre. "
            "Requisito: Grado en Ingeniería Informática."
        ),
    )
    app_config.data["reminders"] = {"enabled": False}

    summary = collect(
        config=app_config,
        store=store,
        client=object(),
        sources=[(StaticSource(corrected), {})],
        today=date(2026, 10, 6),
    )

    process = store.connection.execute("SELECT * FROM processes").fetchone()
    assert process["compatibility"] == Compatibility.NOT_COMPATIBLE.value
    assert summary.updated == 1
    events = store.list_outbox()
    status_by_kind = {event["kind"]: event["status"] for event in events}
    assert status_by_kind == {
        EventKind.NEW.value: OutboxStatus.SENT.value,
        EventKind.UPDATE.value: OutboxStatus.PENDING.value,
    }
