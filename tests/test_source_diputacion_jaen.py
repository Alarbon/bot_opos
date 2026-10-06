from __future__ import annotations

from datetime import date
from typing import Any

from oposiciones_bot.config import AppConfig
from oposiciones_bot.sources.base import FetchContext
from oposiciones_bot.sources.diputacion_jaen import DiputacionJaenSource


class FakeDiputacionClient:
    def __init__(self, listing: object, details: dict[str, object]) -> None:
        self.listing = listing
        self.details = details
        self.calls: list[str] = []

    def get_json(self, url: str, **_kwargs: Any) -> Any:
        self.calls.append(url)
        if url == DiputacionJaenSource.endpoint:
            return self.listing
        value = self.details[url]
        if isinstance(value, Exception):
            raise value
        return value


def test_diputacion_uses_stable_id_and_encoded_slug_then_hydrates_detail(
    app_config: AppConfig,
) -> None:
    slug = "A B/+?~_-"
    detail_url = (
        "https://sede.dipujaen.es/api/Tablon/GetAnuncio?slug=A%20B%2F%2B%3F~_-"
    )
    listing = {
        "data": [
            {
                "ID": 42,
                "Slug": slug,
                "Titulo": "Convocatoria de Técnico Informático",
                "Descripcion": "Proceso selectivo para dos plazas",
                "Tema": "Empleo público",
                "FechaInicio": "05/10/2026",
                "codDocPublicacion": "PUB-42",
            },
            {
                "id": 99,
                "slug": "no-se-consulta",
                "titulo": "Subvenciones para instalaciones deportivas",
                "descripcion": "Ayudas municipales",
                "fechaInicio": "05/10/2026",
            },
            {
                "id": 100,
                "slug": "antiguo",
                "titulo": "Bolsa de programador informático",
                "descripcion": "Convocatoria de empleo",
                "fechaInicio": "01/08/2026",
            },
        ]
    }
    detail = {
        "Descripcion": (
            "<p>Dos plazas. Requisito: Bachiller o Técnico Superior.</p>"
        ),
        "SafeUrl": "/documentos/publicacion-42.pdf",
        "Documentos": [
            {
                "UrlVisualizarDocumento": "/documentos/bases-42.pdf",
                "hashVerificacion": "HASH-BASES-42",
                "nombre": "Bases íntegras",
            }
        ],
    }
    client = FakeDiputacionClient(listing, {detail_url: detail})
    source = DiputacionJaenSource(client)  # type: ignore[arg-type]
    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=10,
        source_config={},
        app_config=app_config,
    )

    candidates = source.fetch(context)

    assert client.calls == [DiputacionJaenSource.endpoint, detail_url]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_id == "42"
    assert candidate.reference == "TABLON-DIPUJAEN-42"
    assert slug not in candidate.source_id
    assert candidate.publication_date == "2026-10-05"
    assert "Requisito: Bachiller o Técnico Superior" in candidate.full_text
    assert "<p>" not in candidate.full_text
    assert [link.url for link in candidate.links] == [
        "https://sede.dipujaen.es/Tablon",
        "https://sede.dipujaen.es/documentos/publicacion-42.pdf",
        "https://sede.dipujaen.es/documentos/bases-42.pdf",
    ]
    assert candidate.links[-1].reference == "HASH-BASES-42"
    assert candidate.links[-1].label == "Bases íntegras"
    assert candidate.raw["detail"] == detail


def test_diputacion_keeps_listing_candidate_if_detail_fails(
    app_config: AppConfig,
) -> None:
    detail_url = "https://sede.dipujaen.es/api/Tablon/GetAnuncio?slug=temporal"
    listing = [
        {
            "id": "77",
            "slug": "temporal",
            "titulo": "Bolsa de soporte TIC",
            "descripcion": "Convocatoria de bolsa de empleo",
            "fechaPublicacion": "2026-10-06",
        }
    ]
    client = FakeDiputacionClient(
        listing,
        {detail_url: RuntimeError("detalle temporalmente no disponible")},
    )
    source = DiputacionJaenSource(client)  # type: ignore[arg-type]
    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=10,
        source_config={},
        app_config=app_config,
    )

    candidates = source.fetch(context)

    assert len(candidates) == 1
    assert candidates[0].source_id == "77"
    assert len(candidates[0].links) == 1
    assert candidates[0].raw["detail"] == {}
