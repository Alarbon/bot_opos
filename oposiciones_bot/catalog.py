"""Public, credential-free catalogue for the Telegram Worker."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .classifiers import can_apply, evaluate_candidate, reviewable
from .config import AppConfig
from .db import SQLiteStore
from .normalization import utc_now_iso
from .process_groups import martos_process_folder


def build_catalog(store: SQLiteStore, config: AppConfig, today: date) -> dict:
    store.consolidate_document_folders()
    processes = []
    for row in store.connection.execute("SELECT * FROM processes ORDER BY last_changed DESC, id"):
        if store.canonical_process_id(row["id"]) != row["id"]:
            continue
        candidate = store._candidate_from_process(store.connection, row)
        aliases = [r["alias_id"] for r in store.connection.execute("SELECT alias_id FROM process_aliases WHERE process_id=?", (row["id"],))]
        related_rows = [row, *(store.connection.execute("SELECT * FROM processes WHERE id=?", (alias,)).fetchone() for alias in aliases)]
        folder = next((martos_process_folder(link.url) for link in candidate.links if martos_process_folder(link.url)), None)
        if folder:
            from .models import SourceLink
            candidate.title = folder[1]
            links = {}
            for document in related_rows:
                for link in store._candidate_from_process(store.connection, document).links:
                    if link.label == "Fuente oficial":
                        link = SourceLink(link.source, link.url, link.reference, document["title"])
                    links[link.url] = link
            candidate.links = list(links.values())
            candidate.summary = f"Documentos de una única convocatoria: {len(candidate.links)}. Consulta las bases y el último anuncio en los enlaces oficiales."
            if "fotocopia" in candidate.qualification_text.lower() or "titulacion requerida" in candidate.qualification_text.lower():
                candidate.qualification_text = ""
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
        process_ids = [row["id"], *aliases]
        placeholders = ",".join("?" for _ in process_ids)
        for event in store.connection.execute(f"SELECT kind,created_at,payload_json FROM notification_outbox WHERE process_id IN ({placeholders}) ORDER BY created_at DESC LIMIT 10", process_ids):
            payload = json.loads(event["payload_json"])
            history.append({"date": event["created_at"], "kind": event["kind"], "changes": payload.get("changes", [])})
        data = candidate.to_dict()
        if not decision.include:
            data["compatibility"] = "REVISAR"
        # No personal identifiers, tokens, raw documents or notification receipts.
        data.pop("raw", None)
        data.pop("full_text", None)
        data.update(id=row["id"], aliases=aliases, category=category, review_reason=decision.reason if not decision.include else "", followed=followed, last_seen=max(r["last_seen"] for r in related_rows), last_changed=max(r["last_changed"] for r in related_rows), history=history)
        processes.append(data)
    sources = [dict(row) for row in store.connection.execute("SELECT source,started_at,status,items_found,error FROM source_runs WHERE id IN (SELECT MAX(id) FROM source_runs GROUP BY source) ORDER BY source")]
    return {"version": 1, "generated_at": utc_now_iso(), "processes": processes, "sources": sources}


def export_catalog(store: SQLiteStore, config: AppConfig, today: date, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_catalog(store, config, today), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
