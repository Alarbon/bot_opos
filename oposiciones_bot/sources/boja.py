from __future__ import annotations

from datetime import date, timedelta
from typing import Any

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
        while True:
            payload = self.client.get_json(self.endpoint, params={**params, "page": page})
            records = payload.get("results", [])
            if not isinstance(records, list):
                break
            for item in records:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                published = parse_date(item.get("dateUTC") or item.get("date"))
                if published:
                    age = (context.today - date.fromisoformat(published)).days
                    if age < 0 or age > context.lookback_days:
                        continue
                summary = str(item.get("summaryNoHtml") or item.get("summary") or "")
                body = str(item.get("bodyNoHtml") or "")
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
                pdf_url = str(pdf.get("publicUrl") or "")
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

            total = int(payload.get("total_hits") or 0)
            page += 1
            if not records or len(records) < page_size or page * page_size >= total:
                break
        return candidates

    @staticmethod
    def _potentially_relevant(text: str) -> bool:
        return has_it_signal(text)

