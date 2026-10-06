"""Only group documents with an explicit, source-provided process folder."""
import re
from urllib.parse import urlparse, unquote


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
