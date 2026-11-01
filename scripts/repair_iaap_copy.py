"""Repair verified IAAP fields in a NEW database copy; never changes the input.

Use captured official HTML as evidence. No HTTP or Telegram calls are made.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup
from oposiciones_bot.classifiers import evaluate_candidate
from oposiciones_bot.config import load_config
from oposiciones_bot.db import SQLiteStore
from oposiciones_bot.matching import semantic_snapshot
from oposiciones_bot.normalization import canonical_json, normalize_text, stable_hash, utc_now_iso
from oposiciones_bot.sources.iaap_fields import apply_process_fields


def repair_copy(database: Path, output: Path, evidence: dict[str, Path], config) -> list[dict]:
    if output.exists() or output.resolve() == database.resolve():
        raise ValueError("La salida debe ser una copia nueva, nunca la base original")
    # Validate all evidence against the existing row before creating any copy.
    source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        for url, path in evidence.items():
            row = source.execute("SELECT title FROM processes WHERE primary_url=?", (url,)).fetchone()
            soup = BeautifulSoup(path.read_text(encoding="utf8"), "lxml")
            heading = soup.select_one("h1")
            if not row or not heading or normalize_text(heading.get_text(" ", strip=True)) != normalize_text(row["title"]):
                raise ValueError("La evidencia no corresponde inequívocamente a la ficha guardada")
        with sqlite3.connect(output) as destination:
            source.backup(destination)
    finally:
        source.close()
    store = SQLiteStore(output)
    audit = []
    try:
        with store.connection:
            for url, path in evidence.items():
                row = store.connection.execute("SELECT * FROM processes WHERE primary_url=?", (url,)).fetchone()
                candidate = store._candidate_from_process(store.connection, row)
                soup = BeautifulSoup(path.read_text(encoding="utf8"), "lxml")
                if not apply_process_fields(candidate, soup):
                    raise ValueError("La evidencia carece de campos estructurados IAAP")
                decision = evaluate_candidate(candidate, config)
                snapshot = semantic_snapshot(candidate)
                audit.append({"id": row["id"], "source_url": url, "evidence_file": str(path), "before": dict(row), "after": candidate.to_dict(), "reason": decision.reason})
                fields = {
                    "organisation": candidate.organisation,
                    "normalised_organisation": normalize_text(candidate.organisation),
                    "group_name": candidate.group, "positions": candidate.positions,
                    "access": candidate.access.value, "compatibility": candidate.compatibility.value,
                    "publication_date": candidate.publication_date, "deadline": candidate.deadline,
                    "deadline_confirmed": int(candidate.deadline_confirmed),
                    "status": candidate.status.value, "semantic_hash": stable_hash(snapshot),
                    "snapshot_json": canonical_json(snapshot),
                }
                # A repeated repair must not create a fictitious new change date.
                if all(row[key] == value for key, value in fields.items()):
                    continue
                store.connection.execute(
                    """UPDATE processes SET organisation=?,normalised_organisation=?,group_name=?,
                    positions=?,access=?,compatibility=?,publication_date=?,deadline=?,
                    deadline_confirmed=?,status=?,semantic_hash=?,snapshot_json=?,last_changed=? WHERE id=?""",
                    (candidate.organisation, normalize_text(candidate.organisation), candidate.group,
                     candidate.positions, candidate.access.value, candidate.compatibility.value,
                     candidate.publication_date, candidate.deadline, int(candidate.deadline_confirmed),
                     candidate.status.value, stable_hash(snapshot), canonical_json(snapshot), utc_now_iso(), row["id"]),
                )
        if store.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("La copia no supera integrity_check")
        output.with_suffix(".audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        return audit
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--evidence", action="append", required=True, help="official_url=local_html_file")
    args = parser.parse_args()
    evidence = {url: Path(path) for url, path in (item.split("=", 1) for item in args.evidence)}
    audit = repair_copy(args.database, args.output, evidence, load_config(args.config))
    print(f"Copia verificada: {args.output}; {len(audit)} fichas. Original intacto; outbox sin modificar.")
