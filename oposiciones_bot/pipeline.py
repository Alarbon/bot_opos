from __future__ import annotations

import logging
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .classifiers import can_apply, evaluate_candidate, reviewable
from .config import AppConfig
from .db import SQLiteStore
from .http import HttpClient
from .models import Candidate, EventKind, OutboxStatus
from .sources.base import FetchContext, SourceAdapter
from .telegram import (
    TelegramAmbiguousError,
    TelegramClient,
    TelegramError,
    render_event,
)


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CollectionSummary:
    fetched: int = 0
    included: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    filtered: int = 0
    reminders: int = 0
    errors: dict[str, str] = field(default_factory=dict)


def _should_notify_on_bootstrap(candidate: Any, today: date, max_age_days: int) -> bool:
    deadline_expired = False
    if candidate.deadline:
        try:
            deadline = date.fromisoformat(candidate.deadline)
            deadline_expired = deadline < today
            if candidate.deadline_confirmed and deadline >= today:
                return True
        except ValueError:
            pass
    if candidate.exam_date:
        try:
            if date.fromisoformat(candidate.exam_date) >= today:
                return True
        except ValueError:
            pass
    if deadline_expired:
        return False
    if candidate.publication_date:
        try:
            age = (today - date.fromisoformat(candidate.publication_date)).days
            return age <= max_age_days
        except ValueError:
            return False
    return bool(candidate.special_process)


def collect(
    *,
    config: AppConfig,
    store: SQLiteStore,
    client: HttpClient,
    sources: list[tuple[SourceAdapter, dict[str, Any]]],
    today: date,
) -> CollectionSummary:
    summary = CollectionSummary()
    first_run = store.is_empty()
    lookback = int(config.get("collection.lookback_days", 10))
    bootstrap_days = int(config.get("collection.bootstrap_notify_days", 14))
    for adapter, source_config in sources:
        run_id = store.start_source_run(adapter.name)
        source_included = 0
        try:
            context = FetchContext(
                today=today,
                lookback_days=lookback,
                source_config=source_config,
                app_config=config,
            )
            candidates = adapter.fetch(context)
            summary.fetched += len(candidates)
            for candidate in candidates:
                decision = evaluate_candidate(candidate, config)
                uncertain = not decision.include and config.get("eligibility.show_review", False) and reviewable(candidate, config)
                if uncertain:
                    from .models import Compatibility
                    candidate.compatibility = Compatibility.REVIEW
                    decision.include = True
                    decision.compatibility = Compatibility.REVIEW
                already_tracked = store.has_candidate(candidate)
                if config.get("eligibility.only_enrollable_new", False):
                    if not already_tracked and not can_apply(candidate, today):
                        if not config.get("eligibility.show_review", False):
                            summary.filtered += 1
                            continue
                if not decision.include and not already_tracked:
                    summary.filtered += 1
                    LOGGER.debug("Descartada %s: %s", candidate.source_id, decision.reason)
                    continue
                if not decision.include:
                    LOGGER.info(
                        "Se conserva la correccion excluyente de %s: %s",
                        candidate.source_id,
                        decision.reason,
                    )
                source_included += 1
                summary.included += 1
                notify = not first_run or _should_notify_on_bootstrap(
                    candidate, today, bootstrap_days
                )
                if config.get("eligibility.only_enrollable_new", False):
                    notify = notify and decision.include
                    if not already_tracked and not can_apply(candidate, today):
                        notify = False
                result = store.ingest(candidate, decision, notify=notify)
                if result.action == "created":
                    summary.created += 1
                elif result.action == "updated":
                    summary.updated += 1
                else:
                    summary.unchanged += 1
            store.finish_source_run(
                run_id,
                status="OK",
                found=len(candidates),
                included=source_included,
            )
        except Exception as exc:
            message = f"{exc.__class__.__name__}: {exc}"
            summary.errors[adapter.name] = message
            LOGGER.exception("La fuente %s fallo; se continua con las demas", adapter.name)
            store.finish_source_run(run_id, status="ERROR", error=message)

    if config.get("reminders.enabled", True):
        summary.reminders = store.enqueue_reminders(
            today,
            [int(value) for value in config.get("reminders.days_before", [7, 3, 1])],
        )
    return summary


@dataclass(slots=True)
class DispatchSummary:
    claimed: int = 0
    sent: int = 0
    retryable: int = 0
    uncertain: int = 0


def dispatch(
    *,
    store: SQLiteStore,
    telegram: TelegramClient,
    event_ids: list[str] | None = None,
    limit: int = 20,
    config: AppConfig | None = None,
) -> DispatchSummary:
    if event_ids is None:
        event_ids = store.claim_outbox(limit=limit)
    events = store.get_outbox_events(event_ids)
    result = DispatchSummary(claimed=len(events))
    for event in events:
        if event["status"] != OutboxStatus.SENDING.value:
            LOGGER.warning(
                "Se omite %s porque su estado es %s", event["event_id"], event["status"]
            )
            continue
        if config and config.get("eligibility.only_enrollable_new", False):
            candidate = Candidate.from_dict(json.loads(event["payload_json"])["candidate"])
            eligible = evaluate_candidate(candidate, config).include
            if not eligible and config.get("eligibility.show_review", False) and reviewable(candidate, config):
                eligible = True
                from .models import Compatibility
                candidate.compatibility = Compatibility.REVIEW
                event = dict(event)
                payload = json.loads(event["payload_json"])
                payload["candidate"] = candidate.to_dict()
                event["payload_json"] = json.dumps(payload)
                if event["kind"] == EventKind.NEW.value:
                    event["kind"] = EventKind.REVIEW.value
            if event["kind"] in {EventKind.UPDATE.value, EventKind.REMINDER.value} and config.get("eligibility.explicit_follow_only", False):
                eligible = eligible and store.is_followed(event["process_id"])
            if event["kind"] in {EventKind.NEW.value, EventKind.REVIEW.value, EventKind.REMINDER.value}:
                from zoneinfo import ZoneInfo
                from datetime import datetime
                eligible = eligible and can_apply(candidate, datetime.now(ZoneInfo(config.get("timezone", "Europe/Madrid"))).date())
            if not eligible:
                store.mark_event(event["event_id"], OutboxStatus.SUPPRESSED, error="Fuera del perfil personal o sin plazo de inscripcion confirmado")
                continue
        try:
            message_id = telegram.send(render_event(event))
        except TelegramAmbiguousError as exc:
            store.mark_event(
                event["event_id"], OutboxStatus.UNCERTAIN, error=str(exc)
            )
            result.uncertain += 1
        except TelegramError as exc:
            store.mark_event(
                event["event_id"], OutboxStatus.RETRYABLE, error=str(exc)
            )
            result.retryable += 1
        else:
            store.mark_event(
                event["event_id"],
                OutboxStatus.SENT,
                message_id=message_id,
            )
            result.sent += 1
    return result

