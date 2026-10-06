"""Only group documents with an explicit, source-provided process folder."""
import re
from urllib.parse import urlparse, unquote
from datetime import date
from .normalization import normalize_text


def document_milestone_date(title: str) -> str:
    """Explicit Spanish document date, not an invented publication date."""
    months = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
    match = re.search(r"(?:fecha\s+)?(\d{1,2})\s+de\s+(" + "|".join(months) + r")\s+de\s+(\d{4})", normalize_text(title))
    if match:
        try:
            return date(int(match[3]), months.index(match[2]) + 1, int(match[1])).isoformat()
        except ValueError:
            pass
    return ""


def martos_process_folder(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"martos.es", "www.martos.es"}:
        return None
    match = re.match(r"/download/(\d+)/([^/]+)/", parsed.path)
    if not match:
        return None
    title = unquote(match[2]).replace("-", " ")
    if "convocatoria" not in title.lower() or "plaza" not in title.lower() or "informatic" not in title.lower():
        return None
    return match[1], title.capitalize()
