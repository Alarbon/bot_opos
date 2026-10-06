from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import fitz
from bs4 import BeautifulSoup

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import clean_text, normalize_text
from .base import FetchContext, has_it_signal, has_public_job_signal


ARCHIVE_BASE_URL = "https://bophistorico.dipujaen.es/"
ARCHIVE_RESULTS_URL = urljoin(ARCHIVE_BASE_URL, "results.vm")
OFFICIAL_BOP_BASE_URL = "https://bop.dipujaen.es/"
MAX_ARCHIVE_WINDOW_DAYS = 10


class ArchiveFetchError(RuntimeError):
    """The archive did not provide enough evidence for a successful fetch."""


class ArchiveWindowStatus(StrEnum):
    CURRENT = "CURRENT"
    LAGGING = "LAGGING"
    EMPTY = "EMPTY"


@dataclass(frozen=True, slots=True)
class ArchiveBulletin:
    archive_id: str
    publication_date: date
    title: str
    page_count: int | None
    viewer_url: str
    pdf_url: str


@dataclass(frozen=True, slots=True)
class ArchiveSearchPage:
    bulletins: tuple[ArchiveBulletin, ...]
    total: int


@dataclass(frozen=True, slots=True)
class ArchiveEdict:
    source_id: str
    title: str
    organisation: str
    text: str
    bulletin: ArchiveBulletin
    official_url: str


@dataclass(slots=True)
class ArchiveFetchResult:
    """Successful archive query, including conservative coverage evidence.

    ``covered_through`` is the newest bulletin date actually returned and fully
    parsed.  It is deliberately *not* the requested end date: the archive can
    lag behind the live BOP site.  A valid search with no bulletins is ``EMPTY``
    and has no coverage date; malformed responses raise ``ArchiveFetchError``.
    """

    candidates: list[Candidate]
    bulletins: tuple[ArchiveBulletin, ...]
    requested_start: date
    requested_end: date
    latest_available: date | None
    covered_through: date | None
    status: ArchiveWindowStatus

    @property
    def is_lagging(self) -> bool:
        return self.covered_through is None or self.covered_through < self.requested_end

    @property
    def coverage_warning(self) -> str | None:
        """Message ready to copy onto ``SourceAdapter.coverage_warning``."""
        if self.status is ArchiveWindowStatus.EMPTY:
            return (
                "BOP Jaen historico: consulta valida sin ejemplares; "
                "no se puede acreditar ninguna fecha de cobertura"
            )
        if self.status is ArchiveWindowStatus.LAGGING:
            return (
                "BOP Jaen historico actualizado solo hasta "
                f"{self.covered_through.isoformat() if self.covered_through else 'fecha desconocida'}; "
                f"se solicito hasta {self.requested_end.isoformat()}"
            )
        return None


_RESULT_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_TOC_REFERENCE_RE = re.compile(
    r"^\s*BOP[-\s](?P<year>\d{4})[-/](?P<number>\d{1,8})\s*$",
    re.IGNORECASE,
)
_BODY_REFERENCE_RE = re.compile(
    r"(?m)^\s*(?P<year>\d{4})/(?P<number>\d{1,8})(?:\s+|$)"
)

_SECTION_HEADINGS = {
    "administracion local",
    "administracion autonomica",
    "administracion de justicia",
    "administracion del estado",
    "junta de andalucia",
    "anuncios no oficiales",
}

_ORGANISATION_PREFIXES = (
    "ayuntamiento de ",
    "diputacion provincial de ",
    "entidad local autonoma ",
    "mancomunidad ",
    "consorcio ",
    "comunidad de regantes",
    "consejeria de ",
    "delegacion territorial de ",
    "agencia ",
    "instituto ",
    "patronato ",
    "ministerio ",
    "subdelegacion del gobierno",
    "confederacion hidrografica",
    "juzgado ",
    "camara oficial ",
)

_DEPARTMENT_RE = re.compile(
    r"^(?:area de |secretaria general$|servicio de |intervencion$|"
    r"recursos humanos$|delegacion territorial en |presidencia$)",
    re.IGNORECASE,
)


def archive_search_params(
    start: date,
    end: date,
    *,
    offset: int = 0,
    page_size: int = 100,
) -> list[tuple[str, str]]:
    """Return the repeated query parameters expected by Pandora's archive."""
    if end < start:
        raise ValueError("La fecha final del historico es anterior a la inicial")
    if (end - start).days + 1 > MAX_ARCHIVE_WINDOW_DAYS:
        raise ValueError("Cada consulta al historico puede abarcar como maximo 10 dias")
    if offset < 0:
        raise ValueError("El desplazamiento del historico no puede ser negativo")
    return [
        ("c", "1"),
        ("f", ""),
        ("l", str(page_size)),
        ("lang", "es"),
        ("o", ""),
        ("p", "0"),
        ("s", str(offset)),
        ("t", "-creation"),
        ("view", "boletin"),
        ("d", "creation"),
        ("d", f"{start.year:04d}"),
        ("d", f"{start.month:02d}"),
        ("d", f"{start.day:02d}"),
        ("d", f"{end.year:04d}"),
        ("d", f"{end.month:02d}"),
        ("d", f"{end.day:02d}"),
    ]


def _archive_url(href: str, *, expected_id: str) -> str:
    url = urljoin(ARCHIVE_BASE_URL, href)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "bophistorico.dipujaen.es":
        raise ArchiveFetchError(f"Enlace inesperado en el historico: {url}")
    ids = parse_qs(parsed.query).get("id", [])
    if ids != [expected_id]:
        raise ArchiveFetchError(
            f"El enlace del historico no conserva el id {expected_id}: {url}"
        )
    return url


def _result_total(soup: BeautifulSoup) -> int:
    page_text = normalize_text(soup.get_text(" ", strip=True))
    if "su consulta no ha devuelto ningun resultado" in page_text:
        return 0
    results = soup.select_one("div.results")
    if results is None:
        raise ArchiveFetchError(
            "La respuesta del historico no acredita un resultado ni una busqueda vacia"
        )
    match = re.search(r"\bresultados\s+(\d+)\b", normalize_text(results.get_text(" ")))
    if not match:
        raise ArchiveFetchError("No se pudo leer el total de resultados del historico")
    return int(match.group(1))


def parse_archive_results(
    html: str,
    *,
    expected_start: date | None = None,
    expected_end: date | None = None,
) -> ArchiveSearchPage:
    """Parse and validate one results page from the official archive."""
    soup = BeautifulSoup(html or "", "lxml")
    total = _result_total(soup)
    bulletins: list[ArchiveBulletin] = []
    seen: set[str] = set()

    for frame in soup.select("div.list-frame[id^='frame-']"):
        frame_id = str(frame.get("id", ""))
        archive_id = frame_id.removeprefix("frame-")
        if not archive_id.isdigit() or archive_id in seen:
            raise ArchiveFetchError(f"Id de ejemplar invalido o repetido: {archive_id!r}")
        seen.add(archive_id)

        open_link = frame.select_one(f"a#open-{archive_id}[href]")
        download_link = frame.select_one(f"a#download-{archive_id}[href]")
        thumbnail = frame.select_one(f"a#thumbnail-link-{archive_id}")
        if open_link is None or download_link is None:
            raise ArchiveFetchError(f"El ejemplar {archive_id} no conserva visor y PDF")

        descriptive_text = " ".join(
            part
            for part in (
                str(thumbnail.get("title", "")) if thumbnail else "",
                clean_text(frame.get_text(" ", strip=True)),
            )
            if part
        )
        date_match = _RESULT_DATE_RE.search(descriptive_text)
        if not date_match:
            raise ArchiveFetchError(f"El ejemplar {archive_id} no contiene fecha")
        day, month, year = map(int, date_match.groups())
        try:
            publication_date = date(year, month, day)
        except ValueError as exc:
            raise ArchiveFetchError(
                f"Fecha invalida en el ejemplar {archive_id}: {date_match.group(0)}"
            ) from exc
        if expected_start and publication_date < expected_start:
            raise ArchiveFetchError(
                f"El historico devolvio {publication_date} antes de {expected_start}"
            )
        if expected_end and publication_date > expected_end:
            raise ArchiveFetchError(
                f"El historico devolvio {publication_date} despues de {expected_end}"
            )

        title = clean_text(str(thumbnail.get("title", ""))) if thumbnail else ""
        title = re.sub(r"\s*\[Ejemplar\]\s*$", "", title, flags=re.IGNORECASE)
        if not title:
            title = f"Boletin Oficial de la Provincia de Jaen. {date_match.group(0)}."
        pages_match = re.search(
            r"\b(\d+)\s+paginas?\b", normalize_text(descriptive_text)
        )
        bulletins.append(
            ArchiveBulletin(
                archive_id=archive_id,
                publication_date=publication_date,
                title=title,
                page_count=int(pages_match.group(1)) if pages_match else None,
                viewer_url=_archive_url(open_link["href"], expected_id=archive_id),
                pdf_url=_archive_url(download_link["href"], expected_id=archive_id),
            )
        )

    if total == 0 and bulletins:
        raise ArchiveFetchError("El historico declara cero resultados pero incluye ejemplares")
    if total > 0 and not bulletins:
        raise ArchiveFetchError("El historico declara resultados pero no expone ejemplares")
    if len(bulletins) > total:
        raise ArchiveFetchError("La pagina contiene mas ejemplares que el total declarado")
    return ArchiveSearchPage(tuple(bulletins), total)


def extract_bulletin_text(data: bytes) -> str:
    """Extract layout-preserving text needed to split a complete bulletin PDF."""
    if not data.startswith(b"%PDF"):
        raise ArchiveFetchError("El ejemplar historico descargado no parece un PDF")
    chunks: list[str] = []
    try:
        with fitz.open(stream=io.BytesIO(data), filetype="pdf") as document:
            for page in document:
                # The PDF draws TOC references in a separate text box.  MuPDF's
                # coordinate sort moves them onto the title line; stream order
                # keeps each ``BOP-YYYY-N`` marker on its own line.
                chunks.append(page.get_text("text"))
    except Exception as exc:
        raise ArchiveFetchError(f"No se pudo leer el PDF del historico: {exc}") from exc
    text = "\n".join(chunks).replace("\r\n", "\n").replace("\r", "\n")
    if len(clean_text(text)) < 40:
        raise ArchiveFetchError("El PDF del historico no contiene texto extraible")
    return text


def _boilerplate_line(line: str) -> bool:
    normalized = normalize_text(line)
    return bool(
        not normalized
        or re.fullmatch(r"numero\s+\d+", normalized)
        or re.fullmatch(r"pag\.?\s*\d+", normalized)
        or re.fullmatch(
            r"(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo),?.*\d{4}",
            normalized,
        )
        or (
            normalized.startswith("instituto de estudios giennenses")
            and "pagina" in normalized
        )
    )


def _looks_like_organisation(line: str) -> bool:
    normalized = normalize_text(line)
    if normalized in _SECTION_HEADINGS:
        return False
    # Organisation headers are typeset in capitals.  Requiring that visual
    # cue avoids treating a wrapped title such as "Ayuntamiento de Cazorla y
    # aprobacion de sus Estatutos" as the start of a new issuer.
    uppercase_heading = line == line.upper()
    return (uppercase_heading and normalized.startswith(_ORGANISATION_PREFIXES)) or bool(
        re.fullmatch(r"[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ\s.,'’()/-]{4,180}", line)
        and any(
            word in normalized
            for word in (
                "ayuntamiento",
                "diputacion",
                "consejeria",
                "consorcio",
                "mancomunidad",
                "comunidad",
                "juzgado",
            )
        )
    )


def _looks_like_department(line: str) -> bool:
    normalized = normalize_text(line)
    return len(line) <= 140 and bool(_DEPARTMENT_RE.match(normalized))


@dataclass(frozen=True, slots=True)
class _TocEntry:
    source_id: str
    title: str
    organisation: str


def _toc_entries(text: str) -> list[_TocEntry]:
    entries: list[_TocEntry] = []
    current_organisation = ""
    current_department = ""
    pending_title: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = clean_text(raw_line)
        if _boilerplate_line(line):
            continue
        normalized = normalize_text(line)
        if normalized in _SECTION_HEADINGS:
            pending_title.clear()
            current_department = ""
            continue
        reference = _TOC_REFERENCE_RE.fullmatch(line)
        if reference:
            source_id = f"BOP-{reference.group('year')}-{reference.group('number')}"
            title = clean_text(" ".join(pending_title))
            if source_id in seen:
                raise ArchiveFetchError(f"Referencia duplicada en el indice: {source_id}")
            if not title:
                raise ArchiveFetchError(f"El indice no contiene titulo para {source_id}")
            organisation = " - ".join(
                value for value in (current_organisation, current_department) if value
            )
            entries.append(_TocEntry(source_id, title, organisation))
            seen.add(source_id)
            pending_title.clear()
            continue
        if _looks_like_organisation(line):
            current_organisation = line
            current_department = ""
            pending_title.clear()
            continue
        if not pending_title and _looks_like_department(line):
            current_department = line
            continue
        pending_title.append(line)

    if not entries:
        raise ArchiveFetchError("No se encontraron referencias BOP en el indice del ejemplar")
    return entries


def _body_match(text: str, source_id: str, start: int) -> re.Match[str] | None:
    _, year, number = source_id.split("-", 2)
    pattern = re.compile(rf"(?m)^\s*{re.escape(year)}/{re.escape(number)}(?:\s+|$)")
    return pattern.search(text, start)


def _clean_edict_text(text: str) -> str:
    lines = [
        clean_text(line)
        for line in text.splitlines()
        if not _boilerplate_line(clean_text(line))
    ]
    return clean_text(" ".join(line for line in lines if line))


def parse_bulletin_edicts(
    text: str,
    bulletin: ArchiveBulletin,
) -> tuple[ArchiveEdict, ...]:
    """Split an archive's full-bulletin text into stable BOP edicts."""
    normalized_newlines = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    first_body = _BODY_REFERENCE_RE.search(normalized_newlines)
    if first_body is None:
        raise ArchiveFetchError(
            f"El ejemplar {bulletin.archive_id} no contiene comienzos de edicto AAAA/N"
        )
    entries = _toc_entries(normalized_newlines[: first_body.start()])

    matches: list[tuple[_TocEntry, re.Match[str]]] = []
    cursor = first_body.start()
    for entry in entries:
        match = _body_match(normalized_newlines, entry.source_id, cursor)
        if match is None:
            raise ArchiveFetchError(
                f"No se encontro el cuerpo de {entry.source_id} en {bulletin.archive_id}"
            )
        matches.append((entry, match))
        cursor = match.end()

    edicts: list[ArchiveEdict] = []
    for index, (entry, match) in enumerate(matches):
        end = matches[index + 1][1].start() if index + 1 < len(matches) else len(normalized_newlines)
        body = _clean_edict_text(normalized_newlines[match.start() : end])
        if len(body) < len(entry.title):
            raise ArchiveFetchError(f"Texto insuficiente para {entry.source_id}")
        _, year, number = entry.source_id.split("-", 2)
        official_url = urljoin(
            OFFICIAL_BOP_BASE_URL,
            "descargarws.dip?"
            + urlencode(
                {
                    "fechaBoletin": bulletin.publication_date.isoformat(),
                    "numeroEdicto": number,
                }
            ),
        )
        edicts.append(
            ArchiveEdict(
                source_id=entry.source_id,
                title=entry.title,
                organisation=entry.organisation or "BOP de Jaen",
                text=body,
                bulletin=bulletin,
                official_url=official_url,
            )
        )
    return tuple(edicts)


def _is_it_public_job(edict: ArchiveEdict) -> bool:
    # The role itself must be explicit in the heading.  Searching the complete
    # body creates false positives when a non-IT exam merely mentions office
    # software deep in its syllabus.
    heading = f"{edict.title} {edict.organisation}"
    opening = f"{heading} {edict.text[:4000]}"
    return has_it_signal(heading) and has_public_job_signal(opening)


def _candidate_from_edict(edict: ArchiveEdict) -> Candidate:
    bulletin = edict.bulletin
    links = [
        SourceLink("bop_jaen", edict.official_url, edict.source_id, "BOP Jaen (edicto)"),
        SourceLink(
            "bop_jaen_archive",
            bulletin.viewer_url,
            bulletin.archive_id,
            "Historico BOP Jaen (visor del ejemplar)",
        ),
        SourceLink(
            "bop_jaen_archive",
            bulletin.pdf_url,
            bulletin.archive_id,
            "Historico BOP Jaen (PDF del ejemplar)",
        ),
    ]
    candidate = Candidate(
        source="bop_jaen",
        source_id=edict.source_id,
        reference=edict.source_id,
        official_references=[edict.source_id],
        title=edict.title,
        organisation=edict.organisation,
        url=edict.official_url,
        publication_date=bulletin.publication_date.isoformat(),
        summary=edict.title,
        full_text=edict.text,
        province="Jaen",
        scope="Local",
        links=links,
        raw={
            "archive_fallback": True,
            "archive_bulletin_id": bulletin.archive_id,
            "archive_viewer_url": bulletin.viewer_url,
            "archive_pdf_url": bulletin.pdf_url,
        },
    )
    return enrich_candidate(candidate)


def _date_windows(start: date, end: date):
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=MAX_ARCHIVE_WINDOW_DAYS - 1), end)
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


class BOPJaenArchive:
    """Read-only fallback over ``bophistorico.dipujaen.es``."""

    name = "bop_jaen"

    def __init__(self, client: object):
        self.client = client

    def _search_window(self, start: date, end: date) -> tuple[ArchiveBulletin, ...]:
        offset = 0
        expected_total: int | None = None
        bulletins: list[ArchiveBulletin] = []
        seen: set[str] = set()

        while True:
            html = self.client.get_text(
                ARCHIVE_RESULTS_URL,
                params=archive_search_params(start, end, offset=offset),
            )
            page = parse_archive_results(
                html,
                expected_start=start,
                expected_end=end,
            )
            if expected_total is None:
                expected_total = page.total
            elif page.total != expected_total:
                raise ArchiveFetchError("El total del historico cambio durante la paginacion")

            for bulletin in page.bulletins:
                if bulletin.archive_id in seen:
                    raise ArchiveFetchError(
                        f"El historico repitio el ejemplar {bulletin.archive_id}"
                    )
                seen.add(bulletin.archive_id)
                bulletins.append(bulletin)

            if len(bulletins) >= expected_total:
                break
            if not page.bulletins:
                raise ArchiveFetchError("La paginacion del historico termino antes del total")
            offset += len(page.bulletins)

        if len(bulletins) != expected_total:
            raise ArchiveFetchError(
                f"El historico declaro {expected_total} ejemplares y se leyeron {len(bulletins)}"
            )
        return tuple(bulletins)

    def fetch_range(self, start: date, end: date) -> ArchiveFetchResult:
        if end < start:
            raise ValueError("La fecha final del historico es anterior a la inicial")

        all_bulletins: list[ArchiveBulletin] = []
        seen: set[str] = set()
        for window_start, window_end in _date_windows(start, end):
            for bulletin in self._search_window(window_start, window_end):
                if bulletin.archive_id in seen:
                    raise ArchiveFetchError(
                        f"Ejemplar duplicado entre ventanas: {bulletin.archive_id}"
                    )
                seen.add(bulletin.archive_id)
                all_bulletins.append(bulletin)

        all_bulletins.sort(key=lambda item: (item.publication_date, item.archive_id))
        candidates: list[Candidate] = []
        for bulletin in all_bulletins:
            pdf = self.client.get_bytes(bulletin.pdf_url)
            text = extract_bulletin_text(pdf)
            for edict in parse_bulletin_edicts(text, bulletin):
                if _is_it_public_job(edict):
                    candidates.append(_candidate_from_edict(edict))

        latest = max((item.publication_date for item in all_bulletins), default=None)
        if latest is None:
            status = ArchiveWindowStatus.EMPTY
        elif latest < end:
            status = ArchiveWindowStatus.LAGGING
        else:
            status = ArchiveWindowStatus.CURRENT
        return ArchiveFetchResult(
            candidates=candidates,
            bulletins=tuple(all_bulletins),
            requested_start=start,
            requested_end=end,
            latest_available=latest,
            covered_through=latest,
            status=status,
        )

    def fetch_window(self, context: FetchContext) -> ArchiveFetchResult:
        """Fetch a ``FetchContext`` range, split into <=10-day HTTP queries."""
        start = context.today - timedelta(days=max(0, context.lookback_days))
        return self.fetch_range(start, context.today)
