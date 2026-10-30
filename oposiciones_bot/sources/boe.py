from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import clean_text, normalize_text
from ..parsers import html_to_text
from .base import (
    FetchContext,
    SourceAdapter,
    as_list,
    has_it_signal,
    it_signal_excerpt,
)


LOGGER = logging.getLogger(__name__)

BOE_BASE_URL = "https://www.boe.es/"
_BOE_ID_RE = re.compile(r"\bBOE-[AB]-\d{4}-\d+\b")


def _status(exc: Exception) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


def _short(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def parse_html_index(html: str, day: date) -> list[dict[str, str]]:
    """Parse sections II.A/II.B of the official daily HTML summary.

    Returns the same record shape as the API parser.  Raises ``ValueError``
    when the page does not look like the summary of ``day`` so a template
    change is reported instead of being read as "no news".
    """
    soup = BeautifulSoup(html or "", "lxml")
    title = normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    expected = normalize_text(f"Sumario del dia {day.strftime('%d/%m/%Y')}")
    if expected not in title:
        raise ValueError(f"El indice HTML del BOE no corresponde al {day.isoformat()}")
    container = soup.select_one("div.sumario")
    sections = container.find_all("h3") if container else []
    if not sections:
        raise ValueError("Indice HTML del BOE sin secciones reconocibles")
    records: list[dict[str, str]] = []
    for heading in sections:
        heading_text = normalize_text(heading.get_text(" ", strip=True))
        if not heading_text.startswith("ii autoridades y personal"):
            continue
        section_code = "2A" if " a nombramientos" in heading_text else "2B" if " b oposiciones" in heading_text else ""
        if not section_code:
            continue
        department = ""
        epigraph = ""
        for node in heading.find_next_siblings():
            if node.name == "h3":
                break
            if node.name == "h4":
                department = clean_text(node.get_text(" ", strip=True))
                epigraph = ""
            elif node.name == "h5":
                epigraph = clean_text(node.get_text(" ", strip=True))
            elif node.name == "ul":
                for item in node.select("li.dispo"):
                    text_node = item.find("p")
                    pdf_anchor = item.select_one("li.puntoPDF a[href]")
                    html_anchor = item.select_one("li.puntoHTML a[href]")
                    identifier = ""
                    for anchor in (pdf_anchor, html_anchor):
                        if anchor is None:
                            continue
                        match = _BOE_ID_RE.search(f"{anchor.get('href', '')} {anchor.get('title', '')}")
                        if match:
                            identifier = match.group(0)
                            break
                    if not identifier or text_node is None:
                        continue
                    records.append(
                        {
                            "id": identifier,
                            "control": "",
                            "title": clean_text(text_node.get_text(" ", strip=True)),
                            "department": department,
                            "epigraph": epigraph,
                            "section": section_code,
                            "html_url": urljoin(BOE_BASE_URL, html_anchor["href"]) if html_anchor else "",
                            "pdf_url": urljoin(BOE_BASE_URL, pdf_anchor["href"]) if pdf_anchor else "",
                        }
                    )
    return records


class BOESource(SourceAdapter):
    name = "boe"
    endpoint = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"

    html_index = "https://www.boe.es/boe/dias/{year:04d}/{month:02d}/{day:02d}/"

    def _day_records(self, day: date) -> list[dict[str, str]] | None:
        """Return section II records for ``day`` or ``None`` if there is no BOE.

        The open-data API is the primary route.  Since late October 2026 it has
        answered HTTP 500 ("Code: 101") for every date, so the official daily
        HTML index is used as a second official route for that day.  Each route
        must positively prove the result; an unrecognised page is an error.
        """
        url = self.endpoint.format(date=day.strftime("%Y%m%d"))
        try:
            if getattr(self, "_api_unavailable", None):
                # Do not hammer an API already failing with server errors in
                # this run; go straight to the official HTML route.
                raise RuntimeError(f"API omitida tras error previo: {self._api_unavailable}")
            return list(self._iter_items(self.client.get_json(url)))
        except Exception as api_error:
            if _status(api_error) == 404:
                return None
            status = _status(api_error)
            if not getattr(self, "_api_unavailable", None) and (status is None or status >= 500):
                self._api_unavailable = _short(api_error)
            index_url = self.html_index.format(year=day.year, month=day.month, day=day.day)
            try:
                html = self.client.get_text(index_url)
            except Exception as html_error:
                if _status(html_error) == 404:
                    # Both official routes agree there is no bulletin.
                    return None
                raise RuntimeError(
                    f"BOE {day.isoformat()}: API: {_short(api_error)}; "
                    f"indice HTML: {_short(html_error)}"
                ) from html_error
            records = parse_html_index(html, day)
            if not getattr(self, "_fallback_logged", False):
                LOGGER.warning(
                    "BOE: API no disponible (%s); se usa el indice HTML oficial",
                    _short(api_error),
                )
                self._fallback_logged = True
            LOGGER.info("BOE %s: %s disposiciones leidas del indice HTML", day.isoformat(), len(records))
            return records

    def fetch(self, context: FetchContext) -> list[Candidate]:
        candidates: list[Candidate] = []
        failed_days: dict[date, str] = {}
        self._api_unavailable = None
        self._fallback_logged = False
        days = [context.today - timedelta(days=offset) for offset in range(context.lookback_days + 1)]
        for day in days:
            try:
                records = self._day_records(day)
            except Exception as exc:
                # A single failing day must not discard the rest of the window.
                failed_days[day] = _short(exc)
                LOGGER.warning("BOE %s no consultado: %s", day.isoformat(), _short(exc))
                continue
            if records is None:
                continue
            for record in records:
                title = clean_text(
                    " ".join(
                        part
                        for part in (
                            record.get("epigraph", ""),
                            record.get("title", ""),
                        )
                        if part
                    )
                )
                quick_text = f"{title} {record.get('department', '')}"
                title_is_relevant = self._potentially_relevant(quick_text)
                target_local = self._target_local(quick_text, context)
                if not title_is_relevant and not target_local:
                    continue
                html_url = record.get("html_url", "")
                full_text = ""
                if html_url and context.app_config.get("collection.fetch_document_text", True):
                    try:
                        full_text = html_to_text(self.client.get_text(html_url))
                    except Exception as exc:
                        LOGGER.warning("BOE: no se pudo ampliar %s: %s", record.get("id"), exc)
                if not title_is_relevant and not has_it_signal(full_text):
                    # Local BOE headings commonly omit the job category. The
                    # detail is required to prove it is an IT vacancy.
                    continue
                role_excerpt = it_signal_excerpt(full_text) if not title_is_relevant else ""
                organisation = self._issuer_from_title(record.get("title", ""))
                candidate = Candidate(
                    source=self.name,
                    source_id=record["id"],
                    reference=record["id"],
                    official_references=[record["id"]],
                    title=title,
                    organisation=organisation or record.get("department", "BOE"),
                    url=html_url or record.get("pdf_url", ""),
                    publication_date=day.isoformat(),
                    summary=clean_text(f"{record.get('title', '')} {role_excerpt}"),
                    full_text=full_text,
                    scope="Nacional",
                    links=[
                        SourceLink(self.name, html_url, record["id"], "BOE (HTML)"),
                        SourceLink(self.name, record.get("pdf_url", ""), record["id"], "BOE (PDF)"),
                    ],
                    raw={"section": record.get("section"), "control": record.get("control")},
                )
                candidates.append(enrich_candidate(candidate))
        if failed_days:
            if len(failed_days) == len(days):
                first = max(failed_days)
                raise RuntimeError(
                    f"BOE: ningun dia consultado ({len(days)} dias); "
                    f"{first.isoformat()}: {failed_days[first]}"
                )
            earliest = min(failed_days)
            self.covered_through = earliest - timedelta(days=1)
            listed = ", ".join(day.isoformat() for day in sorted(failed_days))
            self.coverage_warning = (
                f"BOE: {len(failed_days)} dia(s) sin consultar ({listed}); "
                f"cobertura acreditada hasta {self.covered_through.isoformat()}"
            )
        return candidates

    @staticmethod
    def _potentially_relevant(text: str) -> bool:
        return has_it_signal(text)

    @staticmethod
    def _target_local(text: str, context: FetchContext) -> bool:
        normalized = normalize_text(text)
        locations = [
            *context.app_config.get("locations.priority", []),
            *context.app_config.get("locations.nearby", []),
            "Jaen",
            "Jaén",
        ]
        return any(normalize_text(str(location)) in normalized for location in locations)

    @staticmethod
    def _issuer_from_title(title: str) -> str:
        """Extract the actual local issuer instead of ``ADMINISTRACION LOCAL``.

        BOE sumaries put the municipality, provincial council or university in
        the item title while the department field contains only a generic
        section label.  Keeping the real issuer enables safe BOP/BOE matching.
        """
        match = re.search(
            r"\b(?:del|de la)\s+"
            r"((?:Ayuntamiento|Diputaci[oó]n Provincial|Universidad)\s+de\s+[^,.]+)",
            title,
            re.IGNORECASE,
        )
        return clean_text(match.group(1)) if match else ""

    def _iter_items(self, payload: dict[str, Any]) -> Iterator[dict[str, str]]:
        sumario = payload.get("data", {}).get("sumario", {})
        for diario in as_list(sumario.get("diario")):
            for section in as_list(diario.get("seccion")):
                section_code = str(section.get("codigo", ""))
                if section_code not in {"2A", "2B"}:
                    continue
                for department in as_list(section.get("departamento")):
                    dept_name = clean_text(department.get("nombre", ""))
                    epigraphs = as_list(department.get("epigrafe"))
                    if not epigraphs and department.get("item"):
                        epigraphs = [{"nombre": "", "item": department.get("item")}]
                    for epigraph in epigraphs:
                        for item in as_list(epigraph.get("item")):
                            identifier = str(item.get("identificador", ""))
                            if not identifier:
                                continue
                            pdf = item.get("url_pdf") or {}
                            pdf_url = pdf.get("texto", "") if isinstance(pdf, dict) else str(pdf)
                            yield {
                                "id": identifier,
                                "control": str(item.get("control", "")),
                                "title": clean_text(item.get("titulo", "")),
                                "department": dept_name,
                                "epigraph": clean_text(epigraph.get("nombre", "")),
                                "section": section_code,
                                "html_url": str(item.get("url_html", "")),
                                "pdf_url": pdf_url,
                            }

