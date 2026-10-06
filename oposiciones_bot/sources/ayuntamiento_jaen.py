from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from ..enrichment import enrich_candidate
from ..http import ResponseTooLarge
from ..models import Candidate, SourceLink
from ..normalization import clean_text, normalize_text
from .base import FetchContext, SourceAdapter, has_it_signal


class AyuntamientoJaenSource(SourceAdapter):
    """Public employment board exposed by the Ayuntamiento e-office.

    The landing page is server-rendered, but the records are loaded by the
    same public AJAX action used by its tabs. The tab key is discovered on
    every run because it is an implementation detail of the e-office.
    """

    name = "ayuntamiento_jaen"
    landing_url = (
        "https://sede.aytojaen.es/sta/CarpetaPublic/doEvent"
        "?APP_CODE=STA&PAGE_CODE=PTS2_EMPLEO"
    )
    ajax_url = "https://sede.aytojaen.es/sta/CarpetaPublic/submitAjax.aa"

    def fetch(self, context: FetchContext) -> list[Candidate]:
        landing_html = self.client.get_text(self.landing_url)
        tab_key = self._process_tab_key(landing_html)
        response = self.client.session.post(
            self.ajax_url,
            data={
                "eventScreenId": "PTS2_EMPLEO",
                "eventObject": "LISTATABLON",
                "eventAction": "LISTATABLON",
                "eventArguments": f"KEY={tab_key}",
                "PAGE_CODE": "PTS2_EMPLEO",
                "APP_CODE": "STA",
                "ROOTID": "1",
                "HFC": "HEADER#FOOTER",
            },
            headers={
                "Referer": self.landing_url,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.client.settings.timeout,
        )
        response.raise_for_status()
        if len(response.content) > self.client.settings.max_bytes:
            raise ResponseTooLarge(
                f"Respuesta superior al limite permitido: {self.ajax_url}"
            )
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        records = self._embedded_dataset(response.text)

        candidates: list[Candidate] = []
        seen: set[str] = set()
        for item in records:
            item_id = str(item.get("dboid") or "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            published = self._date_value(
                item.get("pubDateCreation") or item.get("pubDateIni")
            )
            if published:
                age = (context.today - date.fromisoformat(published)).days
                if age < 0 or age > context.lookback_days:
                    continue
            title = clean_text(str(item.get("descriptionProc") or ""))
            reference = clean_text(str(item.get("externString") or ""))
            sender = item.get("remitent") if isinstance(item.get("remitent"), dict) else {}
            organisation = clean_text(str(sender.get("description") or "Ayuntamiento de Jaen"))
            if not has_it_signal(f"{title} {reference} {organisation}"):
                continue
            stable_reference = reference or f"AYTOJAEN-{item_id}"
            candidate = Candidate(
                source=self.name,
                source_id=item_id,
                reference=stable_reference,
                official_references=[stable_reference],
                title=title or stable_reference,
                organisation=organisation,
                url=self.landing_url,
                publication_date=published,
                summary=clean_text(f"{title} {reference}"),
                full_text=clean_text(f"{title} {reference} {organisation}"),
                locality="Jaen",
                province="Jaen",
                scope="Local",
                links=[
                    SourceLink(
                        self.name,
                        self.landing_url,
                        stable_reference,
                        "Empleo publico del Ayuntamiento de Jaen",
                    )
                ],
                raw={
                    "tab": tab_key,
                    "sender_id": sender.get("dboid"),
                    "tablon": (item.get("tablon") or {}).get("description")
                    if isinstance(item.get("tablon"), dict)
                    else "",
                },
            )
            candidates.append(enrich_candidate(candidate))
        return candidates

    @staticmethod
    def _process_tab_key(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for anchor in soup.find_all("a", onclick=True):
            label = normalize_text(anchor.get_text(" ", strip=True))
            if "procesos selectivos" not in label or "historico" in label:
                continue
            match = re.search(r"KEY=([^'\";)]+)", str(anchor.get("onclick") or ""))
            if match:
                return match.group(1)
        raise ValueError("No se encontro la pestana publica de procesos selectivos")

    @staticmethod
    def _embedded_dataset(html: str) -> list[dict[str, Any]]:
        marker = re.search(r"var\s+dataset_PTS2_EMPLEO\s*=\s*", html)
        if not marker:
            raise ValueError("La sede no incluyo dataset_PTS2_EMPLEO")
        value, _ = json.JSONDecoder().raw_decode(html[marker.end() :].lstrip())
        if not isinstance(value, list):
            raise ValueError("dataset_PTS2_EMPLEO no es una lista")
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _date_value(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        try:
            return date(
                int(value["year"]),
                int(value["month"]),
                int(value["day"]),
            ).isoformat()
        except (KeyError, TypeError, ValueError):
            return None
