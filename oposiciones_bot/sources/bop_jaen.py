from __future__ import annotations

import logging
import re
from datetime import timedelta
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import clean_text
from ..parsers import pdf_to_text
from .base import (
    FetchContext,
    SourceAdapter,
    has_it_signal,
    has_public_job_signal,
)


LOGGER = logging.getLogger(__name__)


class BOPJaenSource(SourceAdapter):
    name = "bop_jaen"
    endpoint = "https://bop.dipujaen.es/bop/{date}"

    def fetch(self, context: FetchContext) -> list[Candidate]:
        candidates: list[Candidate] = []
        for offset in range(context.lookback_days + 1):
            day = context.today - timedelta(days=offset)
            page_url = self.endpoint.format(date=day.strftime("%d-%m-%Y"))
            try:
                html = self.client.get_text(page_url)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    continue
                raise
            soup = BeautifulSoup(html, "lxml")
            for article in soup.find_all("article"):
                description_node = article.select_one(".edicto")
                anchor = article.find("a", href=True)
                if not description_node or not anchor:
                    continue
                title = clean_text(description_node.get_text(" ", strip=True))
                organisation = self._organisation(article)
                quick_text = f"{title} {organisation}"
                if not self._potentially_relevant(quick_text):
                    continue
                pdf_url = urljoin(page_url, anchor["href"])
                query = parse_qs(urlparse(pdf_url).query)
                edict = (query.get("numeroEdicto") or [""])[0]
                source_id = f"BOP-{day.year}-{edict}" if edict else pdf_url
                full_text = ""
                if context.app_config.get("collection.fetch_document_text", True):
                    try:
                        full_text = pdf_to_text(self.client.get_bytes(pdf_url))
                    except Exception as exc:
                        LOGGER.warning("BOP Jaen: no se pudo extraer %s: %s", source_id, exc)
                candidate = Candidate(
                    source=self.name,
                    source_id=source_id,
                    reference=source_id,
                    official_references=[source_id],
                    title=title,
                    organisation=organisation or "BOP de Jaen",
                    url=pdf_url,
                    publication_date=day.isoformat(),
                    summary=title,
                    full_text=full_text,
                    province="Jaen",
                    scope="Local",
                    links=[SourceLink(self.name, pdf_url, source_id, "BOP Jaen (edicto)")],
                    raw={"bulletin_url": page_url, "edict": edict},
                )
                candidates.append(enrich_candidate(candidate))
        return candidates

    @staticmethod
    def _organisation(article: object) -> str:
        """Return the nearest owning subsection and optional department.

        One subsection commonly contains several consecutive ``article``
        elements. Stopping at the previous article made every item after the
        first lose its municipality.
        """
        current = getattr(article, "previous_sibling", None)
        subsection = ""
        department = ""
        while current is not None:
            name = getattr(current, "name", None)
            classes = getattr(current, "attrs", {}).get("class", []) if name else []
            if name == "p" and "departamento" in classes and not department:
                department = clean_text(current.get_text(" ", strip=True))
            if name == "p" and "subseccion" in classes:
                subsection = clean_text(current.get_text(" ", strip=True))
                break
            if name == "p" and "seccion" in classes:
                break
            current = getattr(current, "previous_sibling", None)
        return " - ".join(part for part in (subsection, department) if part)

    @staticmethod
    def _potentially_relevant(text: str) -> bool:
        return has_it_signal(text) and has_public_job_signal(text)

