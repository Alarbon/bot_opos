from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import clean_text, normalize_text
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

    @staticmethod
    def _request_error(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if response is not None and response.headers.get("x-bop-proxy") == "1":
            detail = clean_text(response.text)[:160]
            if detail:
                return f"{type(exc).__name__}: {exc} ({detail})"
        return f"{type(exc).__name__}: {exc}"

    def _proxy_headers(self) -> dict[str, str]:
        token = getattr(self, "_proxy_token", "")
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _proxy_day_url(self, day: date) -> str:
        base = getattr(self, "_proxy_base_url", "").rstrip("/")
        return f"{base}/bop/day/{day.strftime('%d-%m-%Y')}"

    def _proxy_document_url(self, day: date, edict: str) -> str:
        base = getattr(self, "_proxy_base_url", "").rstrip("/")
        return f"{base}/bop/edict/{day.strftime('%d-%m-%Y')}/{quote(edict, safe='')}"

    def _proxy_ready(self) -> bool:
        return bool(
            getattr(self, "_proxy_base_url", "")
            and getattr(self, "_proxy_token", "")
        )

    def _daily_html(self, page_url: str, day: date) -> str:
        proxy_error: Exception | None = None
        if self._proxy_ready():
            try:
                html = self.client.get_text(
                    self._proxy_day_url(day), headers=self._proxy_headers()
                )
                LOGGER.info("BOP Jaen: recuperado %s mediante proxy privado", day)
                return html
            except Exception as exc:
                proxy_error = exc
                LOGGER.warning(
                    "BOP Jaen: proxy no disponible para %s; se prueba el origen: %s",
                    day,
                    exc,
                )
        try:
            return self.client.get_text(page_url)
        except Exception as original:
            if getattr(getattr(original, "response", None), "status_code", None) == 404:
                raise
            fallback_detail = "portada no corresponde al dia solicitado"
            # The home page is another official route to the latest bulletin.
            # Never substitute a different date for a failed historical day.
            try:
                html = self.client.get_text("https://bop.dipujaen.es/")
                soup = BeautifulSoup(html, "lxml")
                dates = {
                    value
                    for anchor in soup.select("article a[href]")
                    for value in parse_qs(urlparse(anchor["href"]).query).get("fechaBoletin", [])
                }
                if dates == {day.isoformat()}:
                    LOGGER.warning("BOP Jaen: recuperado %s mediante portada oficial", day)
                    return html
            except Exception as fallback:
                fallback_detail = f"portada: {type(fallback).__name__}: {fallback}"
            proxy_detail = (
                f"; proxy privado: {self._request_error(proxy_error)}"
                if proxy_error
                else ""
            )
            raise RuntimeError(
                f"BOP Jaen: ruta diaria: {original}; alternativa oficial: "
                f"{fallback_detail}{proxy_detail}"
            ) from original

    def _document_bytes(self, official_url: str, day: date, edict: str) -> bytes:
        proxy_error: Exception | None = None
        if self._proxy_ready() and edict.isdigit():
            try:
                return self.client.get_bytes(
                    self._proxy_document_url(day, edict),
                    headers=self._proxy_headers(),
                )
            except Exception as exc:
                proxy_error = exc
                LOGGER.warning(
                    "BOP Jaen: proxy de documento no disponible para %s: %s",
                    edict,
                    exc,
                )
        try:
            return self.client.get_bytes(official_url)
        except Exception as original:
            if proxy_error:
                raise RuntimeError(
                    "BOP Jaen: no se pudo descargar el edicto por el origen ni "
                    f"por el proxy ({proxy_error})"
                ) from original
            raise

    def fetch(self, context: FetchContext) -> list[Candidate]:
        self._proxy_base_url = str(context.source_config.get("proxy_base_url", "")).strip()
        self._proxy_token = os.environ.get("BOP_PROXY_TOKEN", "").strip()
        candidates: list[Candidate] = []
        for offset in range(context.lookback_days + 1):
            day = context.today - timedelta(days=offset)
            page_url = self.endpoint.format(date=day.strftime("%d-%m-%Y"))
            try:
                html = self._daily_html(page_url, day)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    continue
                raise
            if not self._valid_daily_html(html, day):
                raise RuntimeError(
                    "BOP Jaen: la respuesta no contiene el boletin solicitado "
                    f"ni confirma que no hubo publicacion el {day.isoformat()}"
                )
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
                        full_text = pdf_to_text(
                            self._document_bytes(pdf_url, day, edict)
                        )
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
    def _valid_daily_html(html: str, day: date) -> bool:
        # The authenticated proxy uses HTTP 204 (and therefore an empty body)
        # when the official origin returns 404 for a non-publication day.
        if not html:
            return True
        soup = BeautifulSoup(html, "lxml")
        for anchor in soup.select("article a[href]"):
            target = urlparse(urljoin(BOPJaenSource.endpoint, anchor["href"]))
            query = parse_qs(target.query)
            if (
                target.netloc == "bop.dipujaen.es"
                and target.path == "/descargarws.dip"
                and query.get("fechaBoletin") == [day.isoformat()]
                and (query.get("numeroEdicto") or [""])[0].isdigit()
            ):
                return True
        page_text = normalize_text(soup.get_text(" ", strip=True))
        expected = normalize_text(
            "No hay ningun boletin publicado para el dia "
            f"{day.strftime('%d-%m-%Y')}"
        )
        return expected in page_text

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

