from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Compatibility(StrEnum):
    COMPATIBLE = "COMPATIBLE"
    NOT_COMPATIBLE = "NO_COMPATIBLE"
    REVIEW = "REVISAR"


class AccessType(StrEnum):
    OPEN = "LIBRE"
    MIXED = "MIXTO"
    INTERNAL_ONLY = "PROMOCION_INTERNA_EXCLUSIVA"
    UNKNOWN = "REVISAR"


class ProcessStatus(StrEnum):
    UNKNOWN = "DESCONOCIDA"
    DETECTED = "DETECTADA"
    ANNOUNCED = "CONVOCADA"
    OPEN = "PLAZO_ABIERTO"
    CLOSED = "PLAZO_CERRADO"
    PROVISIONAL_ADMITTED = "ADMITIDOS_PROVISIONAL"
    FINAL_ADMITTED = "ADMITIDOS_DEFINITIVO"
    EXAM_ANNOUNCED = "EXAMEN_ANUNCIADO"
    EXAM_DONE = "EXAMEN_REALIZADO"
    MARKS = "NOTAS"
    DESTINATIONS = "DESTINOS"
    APPOINTMENT_PROPOSED = "PROPUESTA_NOMBRAMIENTO"
    SUSPENDED = "SUSPENDIDA"
    CANCELLED = "ANULADA"
    REOPENED = "REABIERTA"
    FINISHED = "FINALIZADA"


class EventKind(StrEnum):
    NEW = "NEW"
    UPDATE = "UPDATE"
    REMINDER = "REMINDER"
    REVIEW = "REVIEW"
    SOURCE_WARNING = "SOURCE_WARNING"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    RETRYABLE = "RETRYABLE"
    UNCERTAIN = "UNCERTAIN"
    SUPPRESSED = "SUPPRESSED"


@dataclass(slots=True)
class SourceLink:
    source: str
    url: str
    reference: str = ""
    label: str = "Fuente oficial"


@dataclass(slots=True)
class Candidate:
    source: str
    source_id: str
    title: str
    organisation: str
    url: str
    publication_date: str | None = None
    summary: str = ""
    full_text: str = ""
    reference: str = ""
    official_references: list[str] = field(default_factory=list)
    links: list[SourceLink] = field(default_factory=list)
    locality: str = ""
    province: str = ""
    scope: str = ""
    group: str = ""
    positions: int | None = None
    access: AccessType = AccessType.UNKNOWN
    qualification_text: str = ""
    compatibility: Compatibility = Compatibility.REVIEW
    deadline: str | None = None
    deadline_confirmed: bool = False
    status: ProcessStatus = ProcessStatus.DETECTED
    exam_date: str | None = None
    exam_time: str | None = None
    exam_place: str = ""
    priority: int = 1
    special_process: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def searchable_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.title,
                self.organisation,
                self.summary,
                self.full_text,
                self.qualification_text,
                self.locality,
                self.province,
            )
            if part
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["access"] = self.access.value
        data["compatibility"] = self.compatibility.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        values = dict(data)
        values["access"] = AccessType(values.get("access", AccessType.UNKNOWN))
        values["compatibility"] = Compatibility(
            values.get("compatibility", Compatibility.REVIEW)
        )
        values["status"] = ProcessStatus(
            values.get("status", ProcessStatus.DETECTED)
        )
        values["links"] = [
            link if isinstance(link, SourceLink) else SourceLink(**link)
            for link in values.get("links", [])
        ]
        return cls(**values)


@dataclass(slots=True)
class FilterDecision:
    include: bool
    relevant: bool
    geographic_match: bool
    reason: str
    compatibility: Compatibility
    access: AccessType
    priority: int


@dataclass(slots=True)
class IngestResult:
    process_id: str
    action: str
    event_id: str | None = None
    changes: list[str] = field(default_factory=list)

