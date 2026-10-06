from __future__ import annotations

import logging
from datetime import date
from typing import Any
from urllib.parse import quote, urljoin

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import clean_text, parse_date
from ..parsers import html_to_text
from .base import (
    FetchContext,
    SourceAdapter,
    has_it_signal,
    has_public_job_signal,
)


LOGGER = logging.getLogger(__name__)


class DiputacionJaenSource(SourceAdapter):
    name = "diputacion_jaen"
    endpoint = "https://sede.dipujaen.es/api/tablon/getall"
    detail_endpoint = "https://sede.dipujaen.es/api/Tablon/GetAnuncio?slug={slug}"
    public_url = "https://sede.dipujaen.es/Tablon"
    site_base = "https://sede.dipujaen.es"

    def fetch(self, context: FetchContext) -> list[Candidate]:
        payload = self.client.get_json(self.endpoint)
        records = self._records(payload)
        candidates: list[Candidate] = []
        for item in records:
            title = clean_text(
                str(self._value(item, "titulo", "title", "descripcion") or "")
            )
            summary = clean_text(
                str(self._value(item, "descripcion", "description") or title)
            )
            topic = clean_text(
                str(self._value(item, "tema", "area", "tipo") or "")
            )
            if not self._potentially_relevant(f"{title} {summary} {topic}"):
                continue
            # Slug is an encrypted, rotating access token. The numeric id is
            # the stable external identifier and must drive deduplication.
            slug = str(self._value(item, "Slug", "slug") or "")
            item_id = str(self._value(item, "id") or "")
            if not slug or not item_id:
                continue
            published = parse_date(
                self._value(
                    item,
                    "fechaPublicacion",
                    "fecha",
                    "publicationDate",
                    "FechaInicio",
                    "fechaInicio",
                )
            )
            if item_id not in context.followed_source_ids and published and (context.today - date.fromisoformat(published)).days > context.lookback_days:
                continue
            detail: dict[str, Any] = {}
            try:
                detail = self.client.get_json(
                    self.detail_endpoint.format(slug=quote(slug, safe="~_-"))
                )
            except Exception as exc:
                LOGGER.warning("Diputacion Jaen: detalle %s no disponible: %s", item_id, exc)
            detail_text = html_to_text(
                str(self._value(detail, "descripcion", "contenido") or "")
            )
            links = [
                SourceLink(
                    self.name,
                    self.public_url,
                    item_id,
                    "Tablon Diputacion de Jaen",
                )
            ]
            publication_document = str(
                self._value(detail, "safeurl") or self._value(item, "safeurl") or ""
            )
            if publication_document:
                links.append(
                    SourceLink(
                        self.name,
                        urljoin(self.site_base, publication_document),
                        str(self._value(item, "codDocPublicacion") or ""),
                        "Anuncio publicado",
                    )
                )
            documents = self._value(detail, "documentos", "archivos") or []
            for document in self._records(documents):
                document_url = str(
                    self._value(
                        document,
                        "UrlVisualizarDocumento",
                        "url",
                        "enlace",
                    )
                    or ""
                )
                if document_url:
                    links.append(
                        SourceLink(
                            self.name,
                            urljoin(self.site_base, document_url),
                            str(
                                self._value(document, "hashVerificacion", "id") or ""
                            ),
                            clean_text(
                                str(self._value(document, "nombre") or "Documento")
                            ),
                        )
                    )
            candidate = Candidate(
                source=self.name,
                source_id=item_id,
                reference=f"TABLON-DIPUJAEN-{item_id}",
                title=title or summary[:240],
                organisation="Diputacion Provincial de Jaen",
                url=self.public_url,
                publication_date=published,
                summary=summary,
                full_text=clean_text(f"{summary} {detail_text}"),
                province="Jaen",
                scope="Local",
                links=links,
                raw={"topic": topic, "detail": detail},
            )
            candidates.append(enrich_candidate(candidate))
        return candidates

    @staticmethod
    def _records(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for key in ("data", "items", "result", "results", "anuncios"):
                nested = value.get(key)
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, dict)]
            return [value]
        return []

    @staticmethod
    def _value(record: dict[str, Any], *names: str) -> Any:
        for name in names:
            if name in record:
                return record[name]
        lowered = {str(key).lower(): value for key, value in record.items()}
        for name in names:
            if name.lower() in lowered:
                return lowered[name.lower()]
        return None

    @staticmethod
    def _potentially_relevant(text: str) -> bool:
        return has_it_signal(text) and has_public_job_signal(text)
