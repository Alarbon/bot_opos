from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .matching import (
    canonical_key,
    changed_fields,
    explicit_relation_score,
    fuzzy_match_score,
    normalized_references,
    semantic_snapshot,
    title_signature,
)
from .models import (
    AccessType,
    Candidate,
    Compatibility,
    EventKind,
    FilterDecision,
    IngestResult,
    OutboxStatus,
    ProcessStatus,
)
from .normalization import (
    canonical_json,
    normalize_reference,
    normalize_text,
    stable_hash,
    utc_now_iso,
)


SCHEMA_VERSION = 4


SCHEMA = """
CREATE TABLE IF NOT EXISTS processes (
    id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    title TEXT NOT NULL,
    title_signature TEXT NOT NULL,
    organisation TEXT NOT NULL,
    normalised_organisation TEXT NOT NULL,
    locality TEXT NOT NULL DEFAULT '',
    province TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    positions INTEGER,
    access TEXT NOT NULL,
    qualification_text TEXT NOT NULL DEFAULT '',
    compatibility TEXT NOT NULL,
    publication_date TEXT,
    deadline TEXT,
    deadline_confirmed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    exam_date TEXT,
    exam_time TEXT,
    exam_place TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    primary_url TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 1,
    special_process TEXT NOT NULL DEFAULT '',
    semantic_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_changed TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_processes_org ON processes(normalised_organisation);
CREATE INDEX IF NOT EXISTS idx_processes_deadline ON processes(deadline);
CREATE INDEX IF NOT EXISTS idx_processes_canonical_key ON processes(canonical_key);

CREATE TABLE IF NOT EXISTS source_items (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    reference TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    raw_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_source_items_process ON source_items(process_id);

CREATE TABLE IF NOT EXISTS official_references (
    reference TEXT PRIMARY KEY,
    process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS process_urls (
    process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT 'Fuente oficial',
    reference TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    PRIMARY KEY(process_id, source, url)
);

CREATE TABLE IF NOT EXISTS notification_outbox (
    event_id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    process_id TEXT REFERENCES processes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    relevant_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    attempted_at TEXT,
    sent_at TEXT,
    telegram_message_id TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_status ON notification_outbox(status, created_at);

CREATE TABLE IF NOT EXISTS reminders (
    process_id TEXT NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    deadline TEXT NOT NULL,
    days_before INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(process_id, deadline, days_before)
);

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    items_found INTEGER NOT NULL DEFAULT 0,
    items_included INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_runs_name ON source_runs(source, started_at DESC);
CREATE TABLE IF NOT EXISTS followed_processes (
    process_id TEXT PRIMARY KEY REFERENCES processes(id) ON DELETE CASCADE,
    followed_at TEXT NOT NULL
);
"""


MIGRATION_V2 = """
BEGIN IMMEDIATE;
CREATE TABLE processes_v2 (
    id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    title TEXT NOT NULL,
    title_signature TEXT NOT NULL,
    organisation TEXT NOT NULL,
    normalised_organisation TEXT NOT NULL,
    locality TEXT NOT NULL DEFAULT '',
    province TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    positions INTEGER,
    access TEXT NOT NULL,
    qualification_text TEXT NOT NULL DEFAULT '',
    compatibility TEXT NOT NULL,
    publication_date TEXT,
    deadline TEXT,
    deadline_confirmed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    exam_date TEXT,
    summary TEXT NOT NULL DEFAULT '',
    primary_url TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 1,
    special_process TEXT NOT NULL DEFAULT '',
    semantic_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_changed TEXT NOT NULL
);
INSERT INTO processes_v2 SELECT * FROM processes;
DROP TABLE processes;
ALTER TABLE processes_v2 RENAME TO processes;
CREATE INDEX idx_processes_org ON processes(normalised_organisation);
CREATE INDEX idx_processes_deadline ON processes(deadline);
CREATE INDEX idx_processes_canonical_key ON processes(canonical_key);
COMMIT;
"""


MIGRATION_V3 = """
ALTER TABLE processes ADD COLUMN exam_time TEXT;
ALTER TABLE processes ADD COLUMN exam_place TEXT NOT NULL DEFAULT '';
"""


STATUS_RANK = {
    ProcessStatus.UNKNOWN.value: 0,
    ProcessStatus.DETECTED.value: 1,
    ProcessStatus.ANNOUNCED.value: 2,
    ProcessStatus.OPEN.value: 3,
    ProcessStatus.CLOSED.value: 4,
    ProcessStatus.PROVISIONAL_ADMITTED.value: 5,
    ProcessStatus.FINAL_ADMITTED.value: 6,
    ProcessStatus.EXAM_ANNOUNCED.value: 7,
    ProcessStatus.EXAM_DONE.value: 8,
    ProcessStatus.MARKS.value: 9,
    ProcessStatus.DESTINATIONS.value: 10,
    ProcessStatus.FINISHED.value: 11,
}


class SQLiteStore:
    def __init__(self, path: str | Path, *, in_memory: bool = False):
        self.path = Path(path) if not in_memory else None
        if in_memory:
            self.connection = sqlite3.connect(":memory:")
        else:
            assert self.path is not None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    @classmethod
    def dry_run_copy(cls, path: str | Path) -> "SQLiteStore":
        instance = cls(path, in_memory=True)
        source_path = Path(path)
        if source_path.exists() and source_path.stat().st_size:
            source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
            try:
                source.backup(instance.connection)
            finally:
                source.close()
            instance.connection.row_factory = sqlite3.Row
            instance._configure()
            instance._migrate()
        return instance

    def _configure(self) -> None:
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"La base usa una version {version} mas nueva que el programa ({SCHEMA_VERSION})"
            )
        if version < 1:
            with self.connection:
                self.connection.executescript(SCHEMA)
                self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return
        if version < 2:
            # SQLite implements a column UNIQUE constraint as an auto-index,
            # which cannot be dropped independently. Rebuild only this table;
            # child tables keep their process_id values while foreign-key
            # enforcement is temporarily disabled for the atomic migration.
            self.connection.commit()
            self.connection.execute("PRAGMA foreign_keys=OFF")
            try:
                self.connection.executescript(MIGRATION_V2)
                self.connection.execute("PRAGMA user_version=2")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            finally:
                self.connection.execute("PRAGMA foreign_keys=ON")
        if version < 3:
            with self.connection:
                self.connection.executescript(MIGRATION_V3)
                self.connection.execute("PRAGMA user_version=3")
        if version < 4:
            with self.connection:
                self.connection.execute("CREATE TABLE IF NOT EXISTS followed_processes (process_id TEXT PRIMARY KEY REFERENCES processes(id) ON DELETE CASCADE, followed_at TEXT NOT NULL)")
                self.connection.execute("PRAGMA user_version=4")

    def is_followed(self, process_id: str) -> bool:
        return bool(self.connection.execute("SELECT 1 FROM followed_processes WHERE process_id=?", (process_id,)).fetchone())

    def follow(self, prefix: str, enabled: bool = True) -> str:
        import re
        if not re.fullmatch(r"[a-f0-9-]{8,36}", prefix):
            raise ValueError("ID invalido: usa el ID de /convocatorias")
        matches = self.connection.execute("SELECT id FROM processes WHERE id LIKE ?", (prefix + "%",)).fetchall()
        if len(matches) != 1:
            raise ValueError("ID no encontrado o ambiguo")
        process_id = matches[0]["id"]
        with self.transaction() as db:
            if enabled:
                db.execute("INSERT OR IGNORE INTO followed_processes VALUES (?,?)", (process_id, utc_now_iso()))
            else:
                db.execute("DELETE FROM followed_processes WHERE process_id=?", (process_id,))
        return process_id

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def integrity_check(self) -> str:
        return str(self.connection.execute("PRAGMA integrity_check").fetchone()[0])

    def is_empty(self) -> bool:
        row = self.connection.execute("SELECT COUNT(*) FROM processes").fetchone()
        return int(row[0]) == 0

    def start_source_run(self, source: str, now: str | None = None) -> int:
        now = now or utc_now_iso()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO source_runs(source, started_at, status) VALUES (?, ?, 'RUNNING')",
                (source, now),
            )
        return int(cursor.lastrowid)

    def finish_source_run(
        self,
        run_id: int,
        *,
        status: str,
        found: int = 0,
        included: int = 0,
        error: str | None = None,
        now: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE source_runs
                SET finished_at=?, status=?, items_found=?, items_included=?, error=?
                WHERE id=?
                """,
                (now or utc_now_iso(), status, found, included, (error or "")[:2000], run_id),
            )

    def _resolve_process(self, db: sqlite3.Connection, candidate: Candidate) -> sqlite3.Row | None:
        row = db.execute(
            """
            SELECT p.* FROM source_items s
            JOIN processes p ON p.id=s.process_id
            WHERE s.source=? AND s.external_id=?
            """,
            (candidate.source, candidate.source_id),
        ).fetchone()
        if row:
            return row

        refs = normalized_references(candidate)
        if refs:
            placeholders = ",".join("?" for _ in refs)
            row = db.execute(
                f"""
                SELECT p.* FROM official_references r
                JOIN processes p ON p.id=r.process_id
                WHERE r.reference IN ({placeholders})
                ORDER BY p.first_seen LIMIT 1
                """,
                refs,
            ).fetchone()
            if row:
                return row

        # Body citations are not global identity keys: many notices cite the
        # same statute or bulletin.  They may establish an explicit relation
        # only when the cited primary publication also agrees on the strong
        # fuzzy guards (issuer, year, role, group, access and places).
        cited_refs = [
            str(value)
            for value in candidate.raw.get("cited_references", [])
            if value
        ]
        for cited_reference in cited_refs:
            normalized = normalize_reference(cited_reference)
            if not normalized:
                continue
            cited_process = db.execute(
                """
                SELECT p.* FROM official_references r
                JOIN processes p ON p.id=r.process_id
                WHERE r.reference=? LIMIT 1
                """,
                (normalized,),
            ).fetchone()
            if cited_process and explicit_relation_score(candidate, dict(cited_process)) >= 0.92:
                return cited_process

        key = canonical_key(candidate)
        row = db.execute("SELECT * FROM processes WHERE canonical_key=?", (key,)).fetchone()
        if row and not self._identity_conflict(db, row, candidate):
            return row

        org = normalize_text(candidate.organisation)
        if not org:
            return None
        rows = db.execute(
            """
            SELECT * FROM processes
            WHERE normalised_organisation=?
            ORDER BY last_seen DESC LIMIT 100
            """,
            (org,),
        ).fetchall()
        best: tuple[float, sqlite3.Row] | None = None
        for process in rows:
            if self._identity_conflict(db, process, candidate):
                continue
            score = fuzzy_match_score(candidate, dict(process))
            if score >= 0.92 and (best is None or score > best[0]):
                best = (score, process)
        return best[1] if best else None

    @staticmethod
    def _identity_conflict(
        db: sqlite3.Connection,
        process: sqlite3.Row,
        candidate: Candidate,
    ) -> bool:
        """Reject unsafe automatic merges before canonical/fuzzy matching.

        A distinct identifier from the same official feed is a strong signal of
        a separate publication.  Without a shared cited reference, merging it
        automatically can collapse two calls with identical job names.  Exact
        ``source+external_id`` and official-reference matches are handled before
        this guard, so known corrections still resolve normally.
        """
        same_source_other_id = db.execute(
            """
            SELECT 1 FROM source_items
            WHERE process_id=? AND source=? AND external_id<>? LIMIT 1
            """,
            (process["id"], candidate.source, candidate.source_id),
        ).fetchone()
        if same_source_other_id:
            return True
        if candidate.positions is not None and process["positions"] is not None:
            return int(candidate.positions) != int(process["positions"])
        return False

    def has_candidate(self, candidate: Candidate) -> bool:
        """Return whether an observation resolves to an already tracked process."""
        return self._resolve_process(self.connection, candidate) is not None

    @staticmethod
    def _prefer_status(old: str, new: str) -> str:
        exceptional = {
            ProcessStatus.SUSPENDED.value,
            ProcessStatus.CANCELLED.value,
            ProcessStatus.REOPENED.value,
        }
        if new in exceptional:
            return new
        if old in exceptional and new not in {
            ProcessStatus.REOPENED.value,
            ProcessStatus.OPEN.value,
        }:
            return old
        return new if STATUS_RANK.get(new, 0) >= STATUS_RANK.get(old, 0) else old

    def _merge_candidate(self, old: sqlite3.Row, candidate: Candidate) -> Candidate:
        merged = Candidate.from_dict(candidate.to_dict())
        merged.title = old["title"] or candidate.title
        merged.organisation = old["organisation"] or candidate.organisation
        for attr, column in (
            ("locality", "locality"),
            ("province", "province"),
            ("scope", "scope"),
            ("group", "group_name"),
            ("qualification_text", "qualification_text"),
            ("summary", "summary"),
        ):
            if not getattr(merged, attr):
                setattr(merged, attr, old[column] or "")
        if merged.positions is None:
            merged.positions = old["positions"]
        if merged.access is AccessType.UNKNOWN:
            merged.access = AccessType(old["access"])
        if merged.compatibility is Compatibility.REVIEW and old["compatibility"] != Compatibility.REVIEW.value:
            merged.compatibility = Compatibility(old["compatibility"])
        if not merged.publication_date:
            merged.publication_date = old["publication_date"]
        if not merged.deadline:
            merged.deadline = old["deadline"]
            merged.deadline_confirmed = bool(old["deadline_confirmed"])
        if not merged.exam_date:
            merged.exam_date = old["exam_date"]
        if not merged.exam_time:
            merged.exam_time = old["exam_time"]
        if not merged.exam_place:
            merged.exam_place = old["exam_place"]
        merged.status = ProcessStatus(self._prefer_status(old["status"], merged.status.value))
        merged.priority = max(int(old["priority"]), merged.priority)
        merged.special_process = merged.special_process or old["special_process"]
        merged.url = merged.url or old["primary_url"]
        return merged

    @staticmethod
    def _payload(candidate: Candidate, process_id: str, changes: list[str]) -> dict[str, Any]:
        return {
            "process_id": process_id,
            "candidate": candidate.to_dict(),
            "changes": changes,
        }

    @staticmethod
    def _material_notice_change(candidate: Candidate) -> str | None:
        text = normalize_text(f"{candidate.title} {candidate.summary}")
        if any(marker in text for marker in ("correccion de errores", "rectificacion")):
            return "correccion o rectificacion oficial"
        if any(
            marker in text
            for marker in (
                "modificacion de las bases",
                "se modifican las bases",
                "modifica la convocatoria",
                "ampliacion de plazo",
            )
        ):
            return "modificacion oficial pendiente de revision"
        return None

    def _insert_event(
        self,
        db: sqlite3.Connection,
        *,
        process_id: str,
        kind: EventKind,
        relevant_hash: str,
        payload: dict[str, Any],
        now: str,
    ) -> str | None:
        if kind is EventKind.UPDATE:
            # An update represents a transition, not only a destination state.
            # Give each actual transition its own identity so a legitimate
            # A -> B -> C -> B cycle can notify B again. Repeated observations
            # of the same state never reach this point because the process
            # semantic hash already matches. Pending updates are still
            # compacted in place below and therefore keep their claim identity.
            event_id = str(uuid.uuid4())
            event_key = f"update:{process_id}:{event_id}"
        else:
            event_key = (
                f"new:{process_id}"
                if kind in {EventKind.NEW, EventKind.REVIEW}
                else f"{kind.value.lower()}:{process_id}:{relevant_hash}"
            )
            event_id = stable_hash(event_key)[:32]
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO notification_outbox(
                event_id,event_key,process_id,kind,relevant_hash,payload_json,status,created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                event_key,
                process_id,
                kind.value,
                relevant_hash,
                canonical_json(payload),
                OutboxStatus.PENDING.value,
                now,
            ),
        )
        return event_id if cursor.rowcount else None

    def ingest(
        self,
        candidate: Candidate,
        decision: FilterDecision,
        *,
        notify: bool = True,
        now: str | None = None,
    ) -> IngestResult:
        now = now or utc_now_iso()
        raw_hash = stable_hash(candidate.to_dict())
        with self.transaction() as db:
            source_item_already_known = bool(
                db.execute(
                    "SELECT 1 FROM source_items WHERE source=? AND external_id=?",
                    (candidate.source, candidate.source_id),
                ).fetchone()
            )
            existing = self._resolve_process(db, candidate)
            if existing is None:
                process_id = str(uuid.uuid4())
                snapshot = semantic_snapshot(candidate)
                semantic_hash = stable_hash(snapshot)
                key = canonical_key(candidate)
                db.execute(
                    """
                    INSERT INTO processes(
                        id,canonical_key,title,title_signature,organisation,normalised_organisation,
                        locality,province,scope,group_name,positions,access,qualification_text,
                        compatibility,publication_date,deadline,deadline_confirmed,status,exam_date,
                        exam_time,exam_place,
                        summary,primary_url,priority,special_process,semantic_hash,snapshot_json,
                        first_seen,last_seen,last_changed
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        process_id,
                        key,
                        candidate.title,
                        title_signature(candidate.title),
                        candidate.organisation,
                        normalize_text(candidate.organisation),
                        candidate.locality,
                        candidate.province,
                        candidate.scope,
                        candidate.group,
                        candidate.positions,
                        candidate.access.value,
                        candidate.qualification_text,
                        candidate.compatibility.value,
                        candidate.publication_date,
                        candidate.deadline,
                        int(candidate.deadline_confirmed),
                        candidate.status.value,
                        candidate.exam_date,
                        candidate.exam_time,
                        candidate.exam_place,
                        candidate.summary,
                        candidate.url,
                        candidate.priority,
                        candidate.special_process,
                        semantic_hash,
                        canonical_json(snapshot),
                        now,
                        now,
                        now,
                    ),
                )
                action = "created"
                changes: list[str] = []
                event_id = None
                if notify:
                    kind = (
                        EventKind.REVIEW
                        if candidate.compatibility is Compatibility.REVIEW
                        else EventKind.NEW
                    )
                    event_id = self._insert_event(
                        db,
                        process_id=process_id,
                        kind=kind,
                        relevant_hash=semantic_hash,
                        payload=self._payload(candidate, process_id, changes),
                        now=now,
                    )
            else:
                process_id = str(existing["id"])
                merged = self._merge_candidate(existing, candidate)
                old_snapshot = json.loads(existing["snapshot_json"])
                new_snapshot = semantic_snapshot(merged)
                changes = changed_fields(old_snapshot, new_snapshot)
                semantic_hash = stable_hash(new_snapshot)
                event_id = None
                action = "unchanged"
                if semantic_hash != existing["semantic_hash"]:
                    action = "updated"
                    db.execute(
                        """
                        UPDATE processes SET
                            locality=?,province=?,scope=?,group_name=?,positions=?,access=?,
                            qualification_text=?,compatibility=?,publication_date=?,deadline=?,
                            deadline_confirmed=?,status=?,exam_date=?,exam_time=?,exam_place=?,
                            summary=?,primary_url=?,
                            priority=?,special_process=?,semantic_hash=?,snapshot_json=?,
                            last_seen=?,last_changed=?
                        WHERE id=?
                        """,
                        (
                            merged.locality,
                            merged.province,
                            merged.scope,
                            merged.group,
                            merged.positions,
                            merged.access.value,
                            merged.qualification_text,
                            merged.compatibility.value,
                            merged.publication_date,
                            merged.deadline,
                            int(merged.deadline_confirmed),
                            merged.status.value,
                            merged.exam_date,
                            merged.exam_time,
                            merged.exam_place,
                            merged.summary,
                            merged.url,
                            merged.priority,
                            merged.special_process,
                            semantic_hash,
                            canonical_json(new_snapshot),
                            now,
                            now,
                            process_id,
                        ),
                    )
                    pending_initial = db.execute(
                        """
                        SELECT event_id FROM notification_outbox
                        WHERE process_id=? AND kind IN (?,?) AND status=?
                        ORDER BY created_at LIMIT 1
                        """,
                        (
                            process_id,
                            EventKind.NEW.value,
                            EventKind.REVIEW.value,
                            OutboxStatus.PENDING.value,
                        ),
                    ).fetchone()
                    pending_update = db.execute(
                        """
                        SELECT event_id,payload_json FROM notification_outbox
                        WHERE process_id=? AND kind=? AND status=?
                        ORDER BY created_at LIMIT 1
                        """,
                        (
                            process_id,
                            EventKind.UPDATE.value,
                            OutboxStatus.PENDING.value,
                        ),
                    ).fetchone()
                    if pending_initial and decision.include:
                        event_id = str(pending_initial["event_id"])
                        db.execute(
                            """
                            UPDATE notification_outbox
                            SET relevant_hash=?, payload_json=? WHERE event_id=?
                            """,
                            (
                                semantic_hash,
                                canonical_json(self._payload(merged, process_id, changes)),
                                event_id,
                            ),
                        )
                    elif pending_initial:
                        # Do not deliver a NEW/REVIEW alert that became
                        # incompatible before it was ever sent.
                        db.execute(
                            """
                            UPDATE notification_outbox
                            SET status=?, last_error=? WHERE event_id=? AND status=?
                            """,
                            (
                                OutboxStatus.SUPPRESSED.value,
                                "La observacion mas reciente ya no supera los filtros",
                                str(pending_initial["event_id"]),
                                OutboxStatus.PENDING.value,
                            ),
                        )
                    elif pending_update:
                        event_id = str(pending_update["event_id"])
                        previous_payload = json.loads(pending_update["payload_json"])
                        combined_changes = list(
                            dict.fromkeys(
                                [*(previous_payload.get("changes") or []), *changes]
                            )
                        )
                        db.execute(
                            """
                            UPDATE notification_outbox
                            SET relevant_hash=?, payload_json=? WHERE event_id=?
                            """,
                            (
                                semantic_hash,
                                canonical_json(
                                    self._payload(
                                        merged,
                                        process_id,
                                        combined_changes,
                                    )
                                ),
                                event_id,
                            ),
                        )
                    elif notify and (
                        decision.include
                        or db.execute(
                            """
                            SELECT 1 FROM notification_outbox
                            WHERE process_id=? AND status=? LIMIT 1
                            """,
                            (process_id, OutboxStatus.SENT.value),
                        ).fetchone()
                    ):
                        event_id = self._insert_event(
                            db,
                            process_id=process_id,
                            kind=EventKind.UPDATE,
                            relevant_hash=semantic_hash,
                            payload=self._payload(merged, process_id, changes),
                            now=now,
                        )
                else:
                    material_change = (
                        None
                        if source_item_already_known
                        else self._material_notice_change(candidate)
                    )
                    if material_change and notify:
                        action = "updated"
                        material_hash = stable_hash(
                            {
                                "process_id": process_id,
                                "source": candidate.source,
                                "source_id": candidate.source_id,
                                "raw_hash": raw_hash,
                            }
                        )
                        pending = db.execute(
                            """
                            SELECT event_id,payload_json FROM notification_outbox
                            WHERE process_id=? AND kind IN (?,?,?) AND status=?
                            ORDER BY created_at LIMIT 1
                            """,
                            (
                                process_id,
                                EventKind.NEW.value,
                                EventKind.REVIEW.value,
                                EventKind.UPDATE.value,
                                OutboxStatus.PENDING.value,
                            ),
                        ).fetchone()
                        if pending:
                            event_id = str(pending["event_id"])
                            previous_payload = json.loads(pending["payload_json"])
                            combined_changes = list(
                                dict.fromkeys(
                                    [
                                        *(previous_payload.get("changes") or []),
                                        material_change,
                                    ]
                                )
                            )
                            db.execute(
                                """
                                UPDATE notification_outbox
                                SET relevant_hash=?, payload_json=? WHERE event_id=?
                                """,
                                (
                                    material_hash,
                                    canonical_json(
                                        self._payload(
                                            candidate,
                                            process_id,
                                            combined_changes,
                                        )
                                    ),
                                    event_id,
                                ),
                            )
                        else:
                            event_id = self._insert_event(
                                db,
                                process_id=process_id,
                                kind=EventKind.UPDATE,
                                relevant_hash=material_hash,
                                payload=self._payload(
                                    candidate,
                                    process_id,
                                    [material_change],
                                ),
                                now=now,
                            )
                        db.execute(
                            "UPDATE processes SET last_seen=?,last_changed=? WHERE id=?",
                            (now, now, process_id),
                        )
                    else:
                        db.execute(
                            "UPDATE processes SET last_seen=? WHERE id=?", (now, process_id)
                        )

            db.execute(
                """
                INSERT INTO source_items(source,external_id,process_id,reference,url,raw_hash,first_seen,last_seen)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(source,external_id) DO UPDATE SET
                    reference=excluded.reference,url=excluded.url,raw_hash=excluded.raw_hash,last_seen=excluded.last_seen
                """,
                (
                    candidate.source,
                    candidate.source_id,
                    process_id,
                    candidate.reference,
                    candidate.url,
                    raw_hash,
                    now,
                    now,
                ),
            )
            for reference in normalized_references(candidate):
                db.execute(
                    "INSERT OR IGNORE INTO official_references(reference,process_id,first_seen) VALUES (?,?,?)",
                    (reference, process_id, now),
                )
            links = list(candidate.links)
            if candidate.url and not any(link.url == candidate.url for link in links):
                from .models import SourceLink

                links.append(SourceLink(candidate.source, candidate.url, candidate.reference))
            for link in links:
                if not link.url:
                    continue
                db.execute(
                    """
                    INSERT OR IGNORE INTO process_urls(process_id,source,url,label,reference,first_seen)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (process_id, link.source, link.url, link.label, link.reference, now),
                )
        return IngestResult(
            process_id=process_id,
            action=action,
            event_id=event_id,
            changes=changes,
        )

    def enqueue_reminders(
        self,
        today: date,
        days_before: list[int],
        now: str | None = None,
    ) -> int:
        now = now or utc_now_iso()
        created = 0
        excluded = {
            ProcessStatus.CLOSED.value,
            ProcessStatus.CANCELLED.value,
            ProcessStatus.FINISHED.value,
            ProcessStatus.SUSPENDED.value,
        }
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM processes WHERE deadline IS NOT NULL AND deadline_confirmed=1"
            ).fetchall()
            for row in rows:
                if (
                    row["compatibility"] == Compatibility.NOT_COMPATIBLE.value
                    or row["access"] == AccessType.INTERNAL_ONLY.value
                ):
                    continue
                if row["status"] in excluded:
                    continue
                try:
                    deadline = date.fromisoformat(row["deadline"])
                except (TypeError, ValueError):
                    continue
                remaining = (deadline - today).days
                if remaining not in days_before:
                    continue
                reminder_key = f"{row['id']}:{deadline.isoformat()}:{remaining}"
                event_hash = stable_hash(reminder_key)
                exists = db.execute(
                    "SELECT 1 FROM reminders WHERE process_id=? AND deadline=? AND days_before=?",
                    (row["id"], deadline.isoformat(), remaining),
                ).fetchone()
                if exists:
                    continue
                candidate = self._candidate_from_process(db, row)
                event_id = self._insert_event(
                    db,
                    process_id=row["id"],
                    kind=EventKind.REMINDER,
                    relevant_hash=event_hash,
                    payload={
                        "process_id": row["id"],
                        "candidate": candidate.to_dict(),
                        "changes": [],
                        "days_before": remaining,
                    },
                    now=now,
                )
                if event_id:
                    db.execute(
                        "INSERT INTO reminders(process_id,deadline,days_before,event_id,created_at) VALUES (?,?,?,?,?)",
                        (row["id"], deadline.isoformat(), remaining, event_id, now),
                    )
                    created += 1
        return created

    def _candidate_from_process(self, db: sqlite3.Connection, row: sqlite3.Row) -> Candidate:
        links = [
            dict(link)
            for link in db.execute(
                "SELECT source,url,reference,label FROM process_urls WHERE process_id=? ORDER BY first_seen",
                (row["id"],),
            ).fetchall()
        ]
        from .models import SourceLink

        return Candidate(
            source=links[0]["source"] if links else "database",
            source_id=str(row["id"]),
            title=row["title"],
            organisation=row["organisation"],
            url=row["primary_url"],
            publication_date=row["publication_date"],
            summary=row["summary"],
            locality=row["locality"],
            province=row["province"],
            scope=row["scope"],
            group=row["group_name"],
            positions=row["positions"],
            access=AccessType(row["access"]),
            qualification_text=row["qualification_text"],
            compatibility=Compatibility(row["compatibility"]),
            deadline=row["deadline"],
            deadline_confirmed=bool(row["deadline_confirmed"]),
            status=ProcessStatus(row["status"]),
            exam_date=row["exam_date"],
            exam_time=row["exam_time"],
            exam_place=row["exam_place"],
            priority=int(row["priority"]),
            special_process=row["special_process"],
            links=[SourceLink(**link) for link in links],
        )

    def list_outbox(self, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = "SELECT * FROM notification_outbox"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY created_at, event_id"
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def claim_outbox(self, limit: int = 20, now: str | None = None) -> list[str]:
        now = now or utc_now_iso()
        with self.transaction() as db:
            rows = db.execute(
                """
                SELECT event_id FROM notification_outbox
                WHERE status IN (?,?) ORDER BY created_at LIMIT ?
                """,
                (OutboxStatus.PENDING.value, OutboxStatus.RETRYABLE.value, limit),
            ).fetchall()
            ids = [str(row["event_id"]) for row in rows]
            for event_id in ids:
                db.execute(
                    """
                    UPDATE notification_outbox
                    SET status=?, attempts=attempts+1, attempted_at=?, last_error=NULL
                    WHERE event_id=?
                    """,
                    (OutboxStatus.SENDING.value, now, event_id),
                )
        return ids

    def release_unsent_claims(
        self,
        event_ids: list[str],
        *,
        error: str,
    ) -> int:
        """Return definitely-unsent claims to PENDING.

        This is only for failures that happen before any Telegram API call,
        such as being unable to persist the batch file or missing credentials.
        Ambiguous transport outcomes must remain UNCERTAIN instead.
        """
        if not event_ids:
            return 0
        placeholders = ",".join("?" for _ in event_ids)
        with self.transaction() as db:
            cursor = db.execute(
                f"""
                UPDATE notification_outbox
                SET status=?, attempts=MAX(attempts-1, 0), attempted_at=NULL,
                    last_error=?
                WHERE event_id IN ({placeholders}) AND status=?
                """,
                (
                    OutboxStatus.PENDING.value,
                    error[:2000],
                    *event_ids,
                    OutboxStatus.SENDING.value,
                ),
            )
        return int(cursor.rowcount)

    def get_outbox_events(self, event_ids: list[str]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        rows = self.connection.execute(
            f"SELECT * FROM notification_outbox WHERE event_id IN ({placeholders}) ORDER BY created_at",
            event_ids,
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_event(
        self,
        event_id: str,
        status: OutboxStatus,
        *,
        message_id: str | None = None,
        error: str | None = None,
        now: str | None = None,
    ) -> None:
        now = now or utc_now_iso()
        sent_at = now if status is OutboxStatus.SENT else None
        with self.connection:
            self.connection.execute(
                """
                UPDATE notification_outbox
                SET status=?, sent_at=COALESCE(?,sent_at), telegram_message_id=COALESCE(?,telegram_message_id),
                    last_error=? WHERE event_id=? AND status<>?
                """,
                (
                    status.value,
                    sent_at,
                    message_id,
                    (error or "")[:2000] or None,
                    event_id,
                    OutboxStatus.SENT.value,
                ),
            )

    def recover_stale_sending(self, older_than_minutes: int = 30) -> int:
        threshold = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).isoformat(
            timespec="seconds"
        )
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE notification_outbox SET status=?, last_error=?
                WHERE status=? AND attempted_at < ?
                """,
                (
                    OutboxStatus.UNCERTAIN.value,
                    "Envio interrumpido; revisar Telegram antes de reintentar",
                    OutboxStatus.SENDING.value,
                    threshold,
                ),
            )
        return int(cursor.rowcount)

    def retry_event(self, event_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE notification_outbox SET status=?, last_error=NULL
                WHERE event_id=? AND status IN (?,?,?)
                """,
                (
                    OutboxStatus.PENDING.value,
                    event_id,
                    OutboxStatus.UNCERTAIN.value,
                    OutboxStatus.RETRYABLE.value,
                    OutboxStatus.SUPPRESSED.value,
                ),
            )
        return bool(cursor.rowcount)

    def suppress_event(self, event_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE notification_outbox SET status=? WHERE event_id=? AND status<>?",
                (OutboxStatus.SUPPRESSED.value, event_id, OutboxStatus.SENT.value),
            )
        return bool(cursor.rowcount)

    def stats(self) -> dict[str, int]:
        result = {
            "processes": int(self.connection.execute("SELECT COUNT(*) FROM processes").fetchone()[0]),
        }
        for row in self.connection.execute(
            "SELECT status,COUNT(*) AS n FROM notification_outbox GROUP BY status"
        ):
            result[f"outbox_{str(row['status']).lower()}"] = int(row["n"])
        return result
