from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Iterator

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


class BOESource(SourceAdapter):
    name = "boe"
    endpoint = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"

    def fetch(self, context: FetchContext) -> list[Candidate]:
        candidates: list[Candidate] = []
        for offset in range(context.lookback_days + 1):
            day = context.today - timedelta(days=offset)
            url = self.endpoint.format(date=day.strftime("%Y%m%d"))
            try:
                payload = self.client.get_json(url)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    continue
                raise
            for record in self._iter_items(payload):
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

