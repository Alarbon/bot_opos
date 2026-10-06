from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
import re
from typing import Any

from ..config import AppConfig
from ..http import HttpClient
from ..models import Candidate
from ..normalization import normalize_text


@dataclass(slots=True)
class FetchContext:
    today: date
    lookback_days: int
    source_config: dict[str, Any]
    app_config: AppConfig


class SourceAdapter(ABC):
    name: str

    def __init__(self, client: HttpClient):
        self.client = client

    @abstractmethod
    def fetch(self, context: FetchContext) -> list[Candidate]:
        raise NotImplementedError


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# Keep these checks in one place.  Substring tests such as ``"tic" in text``
# are unsafe in Spanish (for example, they match ``justicia``), while
# ``"informat"`` also matches ``informativa``.  The source adapters run this
# inexpensive pre-filter before downloading a detail document.
_IT_SIGNAL_RE = re.compile(
    r"(?:"
    r"\binformatic(?:a|as|o|os)\b|"
    r"\bmicroinformatic(?:a|as|o|os)\b|"
    r"\bprogramador(?:a|as|es)?\b|"
    r"\bdesarrollador(?:a|as|es)?\b|"
    r"\badministrador(?:a|as|es)?\s+de\s+sistemas\b|"
    r"\btecnic(?:a|as|o|os)\s+de\s+sistemas\b|"
    r"\bsistemas?\s+informatic(?:o|os|a|as)\b|"
    r"\bsistemas?\s+y\s+bases?\s+de\s+datos\b|"
    r"\btecnologias?\s+de\s+la\s+informacion\b|"
    r"\bsoporte\s+tic\b|"
    r"\btic\b|"
    r"\bciberseguridad\b|"
    r"\bsoftware\b|"
    r"\bc1\s*2003\b"
    r")"
)

_PUBLIC_JOB_SIGNAL_RE = re.compile(
    r"(?:"
    r"\bconvoc|\boposici|\bprocesos?\s+selectivos?\b|"
    r"\bbolsas?\b|\bplazas?\b|\badmitid|\bexcluid|\bexamen|"
    r"\bejercicios?\b|\bbases\b|\btribunal|\bnombramiento|"
    r"\bbarem|\bresultados?\b|\blistad[oa]s?\b|\blistas?\b|"
    r"\boferta\s+de\s+empleo\b"
    r")"
)


def has_it_signal(text: str) -> bool:
    return bool(_IT_SIGNAL_RE.search(normalize_text(text)))


def it_signal_excerpt(text: str, *, before: int = 120, after: int = 260) -> str:
    """Return a short normalized context around the first IT role signal."""
    normalized = normalize_text(text)
    match = _IT_SIGNAL_RE.search(normalized)
    if not match:
        return ""
    return normalized[max(0, match.start() - before) : match.end() + after].strip()


def has_public_job_signal(text: str) -> bool:
    return bool(_PUBLIC_JOB_SIGNAL_RE.search(normalize_text(text)))

