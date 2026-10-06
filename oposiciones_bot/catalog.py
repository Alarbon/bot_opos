"""Public, credential-free catalogue for the Telegram Worker."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .classifiers import can_apply, evaluate_candidate, reviewable
from .config import AppConfig
from .db import SQLiteStore
from .normalization import utc_now_iso


def build_catalog(store: SQLiteStore, config: AppConfig, today: date) -> dict:
    processes = []
    for row in store.connection.execute("SELECT * FROM processes ORDER BY last_changed DESC, id"):
        candidate = store._candidate_from_process(store.connection, row)
        original_status = candidate.status
        decision = evaluate_candidate(candidate, config)
        candidate.status = original_status
        followed = store.is_followed(row["id"])
        excluded = not decision.include and not reviewable(candidate, config)
        if excluded and not followed:
            continue
        applicable = can_apply(candidate, today)
        category = "OPEN" if decision.include and applicable else "REVIEW" if applicable or not candidate.deadline_confirmed else "TRACKING"
        advanced = candidate.status.value in {"NOTAS", "EXAMEN_ANUNCIADO", "EXAMEN_REALIZADO", "ADMITIDOS_PROVISIONAL", "ADMITIDOS_DEFINITIVO", "DESTINOS", "FINALIZADA", "ANULADA", "SUSPENDIDA"}
        if advanced:
            category = "TRACKING"
        if excluded:
            category = "TRACKING"
        history = []
        for event in store.connection.execute("SELECT kind,created_at,payload_json FROM notification_outbox WHERE process_id=? ORDER BY created_at DESC LIMIT 10", (row["id"],)):
            payload = json.loads(event["payload_json"])
            history.append({"date": event["created_at"], "kind": event["kind"], "changes": payload.get("changes", [])})
        data = candidate.to_dict()
        if not decision.include:
            data["compatibility"] = "REVISAR"
        # No personal identifiers, tokens, raw documents or notification receipts.
        data.pop("raw", None)
        data.pop("full_text", None)
        data.update(id=row["id"], category=category, review_reason=decision.reason if not decision.include else "", followed=followed, last_seen=row["last_seen"], last_changed=row["last_changed"], history=history)
        processes.append(data)
    sources = [dict(row) for row in store.connection.execute("SELECT source,started_at,status,items_found,error FROM source_runs WHERE id IN (SELECT MAX(id) FROM source_runs GROUP BY source) ORDER BY source")]
    return {"version": 1, "generated_at": utc_now_iso(), "processes": processes, "sources": sources}


def export_catalog(store: SQLiteStore, config: AppConfig, today: date, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_catalog(store, config, today), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
