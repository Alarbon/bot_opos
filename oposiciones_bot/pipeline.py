from __future__ import annotations

import logging
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .classifiers import can_apply, deadline_window_open, evaluate_candidate, reviewable
from .config import AppConfig
from .db import SQLiteStore
from .http import HttpClient
from .models import Candidate, Compatibility, EventKind, OutboxStatus
from .sources.base import FetchContext, SourceAdapter
from .telegram import (
    TelegramAmbiguousError,
    TelegramClient,
    TelegramError,
    render_event,
    event_keyboard,
)


LOGGER = logging.getLogger(__name__)

_SECRET_PATTERNS = (
    # Telegram bot tokens and bearer/authorization values must never reach
    # SQLite, the public catalogue or Telegram summaries.
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]{8,}"),
    re.compile(r"/bot[^/\s]{20,}/"),
)


def redact_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: (match.group(1) if match.groups() else "") + "<redacted>", text)
    return text


@dataclass(slots=True)
class CollectionSummary:
    sources_attempted: int = 0
    fetched: int = 0
    included: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    filtered: int = 0
    reminders: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    warnings: dict[str, str] = field(default_factory=dict)
    # Observability (added in 0.1.x; older readers ignore these keys).
    compatible: int = 0
    review: int = 0
    not_compatible: int = 0
    notified_new: int = 0
    silent_new: int = 0
    pending_notifications: int = 0
    duration_seconds: float = 0.0
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        """OK, PARTIAL or NO_SOURCES (every attempted source failed)."""
        if self.sources_attempted and len(self.errors) >= self.sources_attempted:
            return "NO_SOURCES"
        if self.errors or self.warnings:
            return "PARTIAL"
        return "OK"

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        data = asdict(self)
        data["outcome"] = self.outcome
        return data


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
    collection_started = time.monotonic()
    store.consolidate_document_folders()
    first_run = store.is_empty()
    lookback = int(config.get("collection.lookback_days", 10))
    max_lookback = max(lookback, int(config.get("collection.max_lookback_days", 45)))
    bootstrap_days = int(config.get("collection.bootstrap_notify_days", 14))
    only_enrollable = bool(config.get("eligibility.only_enrollable_new", False))
    show_review = bool(config.get("eligibility.show_review", False))
    for adapter, source_config in sources:
        summary.sources_attempted += 1
        source_started = time.monotonic()
        source_lookback = lookback
        gap_warning = ""
        if config.get("collection.incremental", False):
            checkpoint = store.collection_checkpoint(adapter.name, str(config.get("timezone", "Europe/Madrid")))
            if checkpoint is not None:
                overlap = max(0, int(config.get("collection.incremental_overlap_days", 0)))
                source_lookback = max(0, (today - checkpoint).days) + overlap
        if source_lookback > max_lookback:
            # A source stuck for weeks must not grow its window (and its
            # downloads) without bound.  The unread gap is reported.
            gap_warning = (
                f"{adapter.name}: sin cobertura acreditada desde hace {source_lookback} días; "
                f"solo se consultan los últimos {max_lookback} (revisa manualmente "
                f"hasta {(today - timedelta(days=max_lookback + 1)).isoformat()})"
            )
            source_lookback = max_lookback
        LOGGER.info("%s: ventana de consulta %s a %s (ambos incluidos)", adapter.name, today - timedelta(days=source_lookback), today)
        run_id = store.start_source_run(adapter.name)
        source_included = 0
        source_stats: dict[str, Any] = {
            "window_start": (today - timedelta(days=source_lookback)).isoformat(),
            "found": 0, "included": 0, "created": 0, "updated": 0,
        }
        summary.sources[adapter.name] = source_stats
        try:
            # Adapters are normally short-lived, but resetting these fields
            # keeps repeated in-process collections from reusing stale
            # coverage metadata.
            if hasattr(adapter, "covered_through"):
                adapter.covered_through = None
            if hasattr(adapter, "coverage_warning"):
                adapter.coverage_warning = None
            context = FetchContext(
                today=today,
                lookback_days=source_lookback,
                source_config=source_config,
                app_config=config,
                followed_source_ids=store.followed_source_ids(adapter.name),
            )
            candidates = adapter.fetch(context)
            summary.fetched += len(candidates)
            source_stats["found"] = len(candidates)
            for candidate in candidates:
                decision = evaluate_candidate(candidate, config)
                uncertain = not decision.include and show_review and reviewable(candidate, config)
                if uncertain:
                    candidate.compatibility = Compatibility.REVIEW
                    decision.include = True
                    decision.compatibility = Compatibility.REVIEW
                already_tracked = store.has_candidate(candidate)
                enrollable = can_apply(candidate, today)
                # "N días hábiles desde la publicación": the exact date is not
                # in the document.  Notify as REVISAR, never as confirmed.
                probable = not enrollable and deadline_window_open(candidate, today)
                if only_enrollable:
                    if not already_tracked and not (enrollable or probable):
                        if not show_review:
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
                if decision.include and candidate.compatibility is Compatibility.COMPATIBLE:
                    summary.compatible += 1
                elif decision.include:
                    summary.review += 1
                else:
                    summary.not_compatible += 1
                notify = not first_run or _should_notify_on_bootstrap(
                    candidate, today, bootstrap_days
                )
                if only_enrollable:
                    notify = notify and decision.include
                    if not already_tracked and not (enrollable or probable):
                        notify = False
                result = store.ingest(
                    candidate,
                    decision,
                    notify=notify,
                    force_review=probable,
                )
                if result.action == "created":
                    summary.created += 1
                    source_stats["created"] += 1
                    if result.event_id:
                        summary.notified_new += 1
                    else:
                        summary.silent_new += 1
                elif result.action == "updated":
                    summary.updated += 1
                    source_stats["updated"] += 1
                else:
                    summary.unchanged += 1
            coverage_warning = getattr(adapter, "coverage_warning", None)
            covered_through = getattr(adapter, "covered_through", None)
            if covered_through is None and not coverage_warning:
                covered_through = today
            if covered_through is not None and covered_through > today:
                covered_through = today
            if (
                covered_through is not None
                and covered_through < today
                and not coverage_warning
            ):
                coverage_warning = (
                    f"cobertura oficial acreditada hasta {covered_through.isoformat()}; "
                    f"queda pendiente comprobar hasta {today.isoformat()}"
                )
            if gap_warning:
                coverage_warning = "; ".join(part for part in (gap_warning, coverage_warning) if part)
            if coverage_warning:
                summary.warnings[adapter.name] = str(coverage_warning)
            status = "PARTIAL" if coverage_warning else "OK"
            store.finish_source_run(
                run_id,
                status=status,
                found=len(candidates),
                included=source_included,
                error=str(coverage_warning or ""),
                covered_through=covered_through,
            )
            source_stats.update(
                status=status,
                included=source_included,
                covered_through=covered_through.isoformat() if covered_through else None,
                detail=str(coverage_warning or "")[:500],
            )
        except Exception as exc:
            message = redact_secrets(f"{exc.__class__.__name__}: {exc}")
            summary.errors[adapter.name] = message
            LOGGER.exception("La fuente %s fallo; se continua con las demas", adapter.name)
            store.finish_source_run(run_id, status="ERROR", error=message)
            source_stats.update(status="ERROR", detail=message[:500])
        source_stats["duration_seconds"] = round(time.monotonic() - source_started, 1)

    if config.get("reminders.enabled", True):
        summary.reminders = store.enqueue_reminders(
            today,
            [int(value) for value in config.get("reminders.days_before", [7, 3, 1])],
        )
    summary.pending_notifications = len(
        store.list_outbox([OutboxStatus.PENDING.value, OutboxStatus.RETRYABLE.value])
    )
    summary.duration_seconds = round(time.monotonic() - collection_started, 1)
    return summary


@dataclass(slots=True)
class DispatchSummary:
    claimed: int = 0
    sent: int = 0
    retryable: int = 0
    uncertain: int = 0
    suppressed: int = 0
    released: int = 0


def dispatch(
    *,
    store: SQLiteStore,
    telegram: TelegramClient,
    event_ids: list[str] | None = None,
    limit: int = 20,
    config: AppConfig | None = None,
    min_interval_seconds: float = 1.0,
    sleep=time.sleep,
) -> DispatchSummary:
    if event_ids is None:
        event_ids = store.claim_outbox(limit=limit)
    events = store.get_outbox_events(event_ids)
    result = DispatchSummary(claimed=len(events))
    attempted_any = False
    for index, event in enumerate(events):
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
                local_today = datetime.now(ZoneInfo(config.get("timezone", "Europe/Madrid"))).date()
                open_now = can_apply(candidate, local_today)
                if event["kind"] == EventKind.REVIEW.value and not open_now:
                    # A REVISAR notice may rely on an official relative period.
                    open_now = deadline_window_open(candidate, local_today)
                eligible = eligible and open_now
            if not eligible:
                store.mark_event(event["event_id"], OutboxStatus.SUPPRESSED, error="Fuera del perfil personal o sin plazo de inscripcion confirmado")
                result.suppressed += 1
                continue
        if attempted_any and min_interval_seconds > 0:
            # Telegram allows roughly one message per second per chat.
            sleep(min_interval_seconds)
        attempted_any = True
        try:
            message_id = telegram.send(render_event(event), reply_markup=event_keyboard(event))
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
            if getattr(exc, "retry_after", None):
                # Rate limited: stop here.  The remaining claims were never
                # sent, so they safely go back to PENDING for the next run.
                remaining = [
                    item["event_id"]
                    for item in events[index + 1 :]
                    if item["status"] == OutboxStatus.SENDING.value
                ]
                result.released += store.release_unsent_claims(
                    remaining,
                    error=f"Telegram pidio esperar {exc.retry_after} s; no se llamo a Telegram",
                )
                break
        else:
            store.mark_event(
                event["event_id"],
                OutboxStatus.SENT,
                message_id=message_id,
            )
            result.sent += 1
    return result

