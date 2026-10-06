from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import clean_text, parse_date, stable_hash
from ..parsers import html_to_text, pdf_to_text
from .base import (
    FetchContext,
    SourceAdapter,
    has_it_signal,
    has_public_job_signal,
)


LOGGER = logging.getLogger(__name__)


class GenericHTMLSource(SourceAdapter):
    """Adaptador prudente para tablones oficiales sin API estable.

    Solo convierte enlaces cuyo texto y contexto ya contienen una senal de
    informatica y otra de proceso selectivo. No rastrea fuera del host ni
    inventa convocatorias a partir de la pagina completa.
    """

    def __init__(self, name: str, client):
        super().__init__(client)
        self.name = name

    def fetch(self, context: FetchContext) -> list[Candidate]:
        results: list[Candidate] = []
        seen: set[str] = set()
        errors: list[Exception] = []
        successful_listings = 0
        for listing_url in context.source_config.get("urls", []):
            try:
                html = self.client.get_text(listing_url)
            except Exception as exc:
                errors.append(exc)
                LOGGER.warning("%s: listado no disponible %s: %s", self.name, listing_url, exc)
                continue
            successful_listings += 1
            soup = BeautifulSoup(html, "lxml")
            for anchor in soup.find_all("a", href=True):
                raw_href = str(anchor.get("href") or "").strip()
                if self._skip_href(raw_href):
                    continue
                href = self._without_fragment(urljoin(listing_url, raw_href))
                if not href or href == self._without_fragment(listing_url):
                    continue
                title = clean_text(anchor.get_text(" ", strip=True))
                if not title or self._navigation_label(title):
                    continue
                container = self._result_container(anchor)
                container_text = (
                    clean_text(container.get_text(" ", strip=True)) if container else title
                )
                context_text = clean_text(f"{title} {container_text}")
                if not self._potentially_relevant(context_text):
                    continue
                source_id = stable_hash({"source": self.name, "url": href})[:32]
                if source_id in seen:
                    continue
                seen.add(source_id)
                full_text = ""
                publication_date = self._publication_date(container)
                detail_links: list[SourceLink] = []
                if context.app_config.get("collection.fetch_document_text", True):
                    if self._is_pdf(href):
                        try:
                            full_text = pdf_to_text(self.client.get_bytes(href))
                        except Exception as exc:
                            LOGGER.warning("%s: PDF no extraible %s: %s", self.name, href, exc)
                    elif self._may_hydrate_html(listing_url, href):
                        try:
                            detail_html = self.client.get_text(href)
                            full_text = html_to_text(detail_html)
                            detail_soup = BeautifulSoup(detail_html, "lxml")
                            publication_date = (
                                self._publication_date(detail_soup) or publication_date
                            )
                            detail_links = self._document_links(detail_soup, href)
                        except Exception as exc:
                            LOGGER.warning(
                                "%s: detalle HTML no ampliable %s: %s",
                                self.name,
                                href,
                                exc,
                            )
                candidate = Candidate(
                    source=self.name,
                    source_id=source_id,
                    title=title or context_text[:240],
                    organisation="Servicio Andaluz de Salud" if self.name == "sas" else self.name.replace("_", " ").title(),
                    url=href,
                    publication_date=publication_date,
                    summary=context_text,
                    full_text=full_text,
                    province="" if self.name in {"iaap", "sas"} else "Jaen",
                    scope="Andalucia" if self.name in {"iaap", "sas"} else "Local",
                    links=[SourceLink(self.name, href, "", "Fuente oficial"), *detail_links],
                    raw={"listing_url": listing_url},
                )
                results.append(enrich_candidate(candidate))
        if not successful_listings and errors:
            raise errors[0]
        return results

    @staticmethod
    def _skip_href(href: str) -> bool:
        lowered = href.lower()
        return not href or lowered.startswith(("#", "javascript:", "mailto:", "tel:"))

    @staticmethod
    def _without_fragment(url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(parsed._replace(fragment=""))

    @staticmethod
    def _navigation_label(title: str) -> bool:
        normalized = clean_text(title).lower()
        return normalized in {
            "inicio",
            "volver",
            "ver más",
            "ver mas",
            "leer más",
            "leer mas",
            "avance",
            "abierta",
            "abierto",
            "cerrada",
            "cerrado",
            "finalizada",
            "finalizado",
            "pasar al contenido principal",
            "skip to main content",
        }

    @staticmethod
    def _result_container(anchor: Tag) -> Tag | None:
        # Rows/cards contain status and year information that is absent from
        # generic link labels such as a Junta process name. Avoid body/nav
        # ancestors, which turn a skip link into a false positive by including
        # the text of the entire page.
        for parent in anchor.parents:
            if not isinstance(parent, Tag):
                continue
            if parent.name in {"nav", "header", "footer", "body", "html"}:
                if parent.name in {"body", "html"}:
                    break
                continue
            classes = {str(value) for value in parent.get("class", [])}
            if parent.name in {"article", "tr", "li"} or any(
                marker in " ".join(classes).lower()
                for marker in ("views-row", "card", "resultado", "anuncio", "item")
            ):
                return parent
        return anchor.parent if isinstance(anchor.parent, Tag) else None

    @staticmethod
    def _is_pdf(url: str) -> bool:
        return urlparse(url).path.lower().endswith(".pdf")

    def _may_hydrate_html(self, listing_url: str, href: str) -> bool:
        if self.name == "ayuntamiento_torredonjimeno":
            # The Gestiona robots policy only permits / and /info(.0), not
            # /board or its detail routes.
            return False
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            return False
        if parsed.hostname != urlparse(listing_url).hostname:
            return False
        suffix = parsed.path.rsplit("/", 1)[-1].lower()
        return not re.search(
            r"\.(?:docx?|xlsx?|odt|ods|zip|rar|7z|jpe?g|png|gif|svg)$", suffix
        )

    @staticmethod
    def _publication_date(node: Tag | BeautifulSoup | None) -> str | None:
        if node is None:
            return None
        for time_node in node.select("time[datetime]"):
            parsed = parse_date(str(time_node.get("datetime") or "")[:10])
            if parsed:
                return parsed
        for selector, attribute in (
            ('meta[property="article:published_time"]', "content"),
            ('meta[name="date"]', "content"),
            ('meta[itemprop="datePublished"]', "content"),
        ):
            meta = node.select_one(selector)
            if meta:
                parsed = parse_date(str(meta.get(attribute) or "")[:10])
                if parsed:
                    return parsed
        text = clean_text(node.get_text(" ", strip=True))
        match = re.search(
            r"(?:fecha\s+de\s+publicaci[oó]n|publicad[oa]|actualizad[oa])"
            r"[^\d]{0,30}(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
            text,
            re.IGNORECASE,
        )
        return parse_date(match.group(1)) if match else None

    def _document_links(self, soup: BeautifulSoup, base_url: str) -> list[SourceLink]:
        links: list[SourceLink] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            url = self._without_fragment(urljoin(base_url, str(anchor["href"])))
            if not self._is_pdf(url) or url in seen:
                continue
            seen.add(url)
            links.append(
                SourceLink(
                    self.name,
                    url,
                    "",
                    clean_text(anchor.get_text(" ", strip=True)) or "Documento oficial",
                )
            )
        return links

    @staticmethod
    def _potentially_relevant(text: str) -> bool:
        return has_it_signal(text) and has_public_job_signal(text)

