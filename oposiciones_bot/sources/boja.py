from __future__ import annotations

from datetime import date, timedelta
from typing import Any
import requests

from ..enrichment import enrich_candidate
from ..models import Candidate, SourceLink
from ..normalization import parse_date
from .base import FetchContext, SourceAdapter, has_it_signal


class BOJASource(SourceAdapter):
    name = "boja"
    endpoint = "https://datos.juntadeandalucia.es/api/v0/boja/get/search_pagination"

    def fetch(self, context: FetchContext) -> list[Candidate]:
        # An unbounded /search response can exceed the global HTTP size limit.
        # This documented endpoint bounds the query exactly and also works when
        # a lookback window crosses New Year.
        page_size = max(1, min(int(context.source_config.get("batch_size", 100)), 100))
        start = context.today - timedelta(days=context.lookback_days)
        params: dict[str, Any] = {
            "order_by": "dateUTC",
            "mode": "DESC",
            "size": page_size,
            "date_from": start.isoformat(),
            "date_to": context.today.isoformat(),
            "campos": [
                "id",
                "number",
                "date",
                "dateUTC",
                "titleSec",
                "organisation",
                "year",
                "type",
                "dispositionNumber",
                "sectionN2",
                "summaryNoHtml",
                "bodyNoHtml",
                "id_file",
                "pathPdf",
                "hashPdf",
                "publicUrl",
            ],
        }

        candidates: list[Candidate] = []
        seen_ids: set[str] = set()
        page = 0
        widened = False
        while True:
            try:
                payload = self.client.get_json(self.endpoint, params={**params, "page": page})
            except requests.HTTPError as exc:
                # The official service returns 400 for an empty date window
                # (observed 31 Oct–1 Nov 2026). Never interpret *any* 400 as
                # "no news": obtain and validate a real result set instead.
                if page != 0 or widened or exc.response is None or exc.response.status_code != 400:
                    raise
                params["date_from"] = (start - timedelta(days=7)).isoformat()
                widened = True
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ValueError("BOJA: esquema de resultados no reconocido")
            records = payload["results"]
            total = payload.get("total_hits")
            if type(total) is not int or total < 0 or (not records and page * page_size < total):
                raise ValueError("BOJA: paginación incompleta o total inválido")
            new_ids = 0
            for item in records:
                if not isinstance(item, dict):
                    raise ValueError("BOJA: documento no interpretable")
                item_id = str(item.get("id") or "")
                if not item_id:
                    raise ValueError("BOJA: documento sin identificador")
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                new_ids += 1

                published = parse_date(item.get("dateUTC") or item.get("date"))
                if not published:
                    raise ValueError("BOJA: documento sin fecha válida")
                if published:
                    age = (context.today - date.fromisoformat(published)).days
                    if age < 0 or age > context.lookback_days:
                        continue
                summary = str(item.get("summaryNoHtml") or item.get("summary") or "")
                body = str(item.get("bodyNoHtml") or "")
                if not summary.strip() and not body.strip():
                    raise ValueError("BOJA: documento sin contenido clasificable")
                if not self._potentially_relevant(
                    f"{summary} {body} {item.get('organisation', '')}"
                ):
                    continue

                pdf_records = item.get("pdf") or []
                if isinstance(pdf_records, dict):
                    pdf_records = [pdf_records]
                pdf = next(
                    (record for record in pdf_records if isinstance(record, dict)),
                    {},
                )
                pdf_url = str(pdf.get("publicUrl") or item.get("publicUrl") or "")
                year = str(item.get("year") or (published or "")[:4])
                number = str(item.get("number") or "")
                disposition = str(
                    item.get("dispositionNumber") or item_id.rsplit(".", 1)[-1]
                )
                public_url = (
                    f"https://www.juntadeandalucia.es/boja/{year}/{number}/{disposition}"
                    if year and number and disposition
                    else pdf_url
                )
                links = [SourceLink(self.name, public_url, item_id, "BOJA")]
                if pdf_url:
                    links.append(
                        SourceLink(self.name, pdf_url, item_id, "BOJA (PDF)")
                    )
                candidate = Candidate(
                    source=self.name,
                    source_id=item_id,
                    reference=item_id,
                    official_references=[item_id],
                    title=summary or item_id,
                    organisation=str(
                        item.get("organisation")
                        or item.get("organism")
                        or "Junta de Andalucia"
                    ),
                    url=public_url,
                    publication_date=published,
                    summary=summary,
                    full_text=body,
                    scope="Andalucia",
                    links=links,
                    raw={
                        "boja_number": item.get("number"),
                        "section": item.get("sectionN2") or item.get("subtitle"),
                        "type": item.get("type"),
                        "pdf_hash": pdf.get("hashPdf"),
                    },
                )
                candidates.append(enrich_candidate(candidate))

            page += 1
            if records and not new_ids:
                raise ValueError("BOJA: página repetida sin progreso")
            if page * page_size >= total:
                break
            if len(records) < page_size:
                raise ValueError("BOJA: página truncada antes del total anunciado")
            if page >= 1000:
                raise ValueError("BOJA: límite de páginas; consulta incompleta")
        return candidates

    @staticmethod
    def _potentially_relevant(text: str) -> bool:
        return has_it_signal(text)

