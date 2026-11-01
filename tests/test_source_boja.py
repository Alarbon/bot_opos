from __future__ import annotations

from datetime import date
from typing import Any

from oposiciones_bot.config import AppConfig
from oposiciones_bot.sources.base import FetchContext
from oposiciones_bot.sources.boja import BOJASource


class FakePagedJSONClient:
    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        params = dict(kwargs.get("params") or {})
        self.calls.append({"url": url, "params": params})
        return self.pages[int(params["page"])]


def boja_item(
    item_id: str,
    *,
    published: str,
    summary: str,
    body: str = "",
    number: str = "190",
    disposition: str = "1",
    pdf: object = None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "dateUTC": published,
        "summaryNoHtml": summary,
        "bodyNoHtml": body,
        "organisation": "Consejería de Justicia, Administración Local y Función Pública",
        "year": "2026",
        "number": number,
        "dispositionNumber": disposition,
        "sectionN2": "Autoridades y personal",
        "type": "Resolución",
        "pdf": pdf or [],
    }


def test_boja_paginates_deduplicates_and_filters_the_lookback(
    app_config: AppConfig,
) -> None:
    repeated = boja_item(
        "BOJA-2026-100",
        published="2026-10-05T08:00:00Z",
        summary="Convocatoria C1.2003 opción informática",
        number="190",
        disposition="100",
        pdf={
            "publicUrl": "https://www.juntadeandalucia.es/eboja/2026/190/BOJA26-190-00100.pdf",
            "hashPdf": "hash-100",
        },
    )
    pages = {
        0: {
            "total_hits": 5,
            "results": [
                repeated,
                boja_item(
                    "BOJA-2026-NOISE",
                    published="2026-10-05",
                    summary="Resolución sobre explotaciones agrícolas",
                ),
            ],
        },
        1: {
            "total_hits": 5,
            "results": [
                repeated,
                boja_item(
                    "BOJA.2026.200",
                    published="06/10/2026",
                    summary="Bolsa para personal técnico",
                    body="Proceso selectivo de sistemas y bases de datos",
                    number="191",
                    disposition="",
                    pdf=[
                        {
                            "publicUrl": "https://www.juntadeandalucia.es/eboja/2026/191/BOJA26-191-00200.pdf",
                            "hashPdf": "hash-200",
                        }
                    ],
                ),
            ],
        },
        2: {
            "total_hits": 5,
            "results": [
                boja_item(
                    "BOJA-2026-OLD",
                    published="2026-08-01",
                    summary="Convocatoria de programador informático",
                )
            ],
        },
    }
    client = FakePagedJSONClient(pages)
    source = BOJASource(client)  # type: ignore[arg-type]
    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=10,
        source_config={"batch_size": 2},
        app_config=app_config,
    )

    candidates = source.fetch(context)

    assert [candidate.source_id for candidate in candidates] == [
        "BOJA-2026-100",
        "BOJA.2026.200",
    ]
    assert [call["params"]["page"] for call in client.calls] == [0, 1, 2]
    assert all(call["url"] == BOJASource.endpoint for call in client.calls)
    assert client.calls[0]["params"]["size"] == 2
    assert client.calls[0]["params"]["date_from"] == "2026-09-26"
    assert client.calls[0]["params"]["date_to"] == "2026-10-06"

    first, second = candidates
    assert first.url == "https://www.juntadeandalucia.es/boja/2026/190/100"
    assert first.publication_date == "2026-10-05"
    assert [link.label for link in first.links] == ["BOJA", "BOJA (PDF)"]
    assert first.raw["pdf_hash"] == "hash-100"
    assert second.url == "https://www.juntadeandalucia.es/boja/2026/191/200"
    assert second.publication_date == "2026-10-06"
    assert second.reference == "BOJA.2026.200"


def test_boja_clamps_batch_size_and_crosses_year_boundary(
    app_config: AppConfig,
) -> None:
    client = FakePagedJSONClient({0: {"total_hits": 0, "results": []}})
    source = BOJASource(client)  # type: ignore[arg-type]
    context = FetchContext(
        today=date(2026, 1, 3),
        lookback_days=7,
        source_config={"batch_size": 999},
        app_config=app_config,
    )

    assert source.fetch(context) == []
    assert client.calls[0]["params"]["size"] == 100
    assert client.calls[0]["params"]["date_from"] == "2025-12-27"
    assert client.calls[0]["params"]["date_to"] == "2026-01-03"


import pytest
import requests


def test_empty_window_400_requires_validated_bounded_fallback(app_config):
    class Client(FakePagedJSONClient):
        def get_json(self, url, **kwargs):
            if kwargs['params']['date_from'] == '2026-10-31':
                self.calls.append({'url': url, 'params': dict(kwargs['params'])})
                response = requests.Response()
                response.status_code = 400
                raise requests.HTTPError(response=response)
            return super().get_json(url, **kwargs)
    client = Client({0: {'total_hits': 1, 'results': [boja_item('disposition.2026.211.1', published='2026-10-30', summary='Convocatoria de informática')]}})
    source = BOJASource(client)
    assert source.fetch(FetchContext(date(2026, 11, 1), 1, {}, app_config)) == []
    assert [c['params']['date_from'] for c in client.calls] == ['2026-10-31', '2026-10-24']


@pytest.mark.parametrize('payload', [{}, {'results': {}, 'total_hits': 0}, {'results': [], 'total_hits': 3}, {'results': [{'dateUTC': '2026-10-30'}], 'total_hits': 1}, {'results': [boja_item('x', published='invalid', summary='informática')], 'total_hits': 1}])
def test_boja_rejects_200_without_valid_extraction(app_config, payload):
    with pytest.raises(ValueError):
        BOJASource(FakePagedJSONClient({0: payload})).fetch(FetchContext(date(2026, 11, 1), 10, {}, app_config))


def test_boja_400_retry_is_limited_and_pipeline_preserves_checkpoint(app_config, store):
    from oposiciones_bot.pipeline import collect
    class Client:
        calls = 0
        def get_json(self, *_args, **_kwargs):
            self.calls += 1
            response = requests.Response(); response.status_code = 400
            raise requests.HTTPError(response=response)
    run = store.start_source_run('boja')
    store.finish_source_run(run, status='OK', covered_through=date(2026, 10, 30))
    client = Client()
    result = collect(config=app_config, store=store, client=client, sources=[(BOJASource(client), {})], today=date(2026, 11, 1))
    assert client.calls == 2
    assert result.sources['boja']['status'] == 'ERROR'
    assert store.collection_checkpoint('boja') == date(2026, 10, 30)


def test_boja_detects_pagination_loop(app_config):
    item = boja_item('x', published='2026-10-30', summary='informática')
    client = FakePagedJSONClient({0: {'total_hits': 3, 'results': [item]}, 1: {'total_hits': 3, 'results': [item]}})
    with pytest.raises(ValueError, match='repetida'):
        BOJASource(client).fetch(FetchContext(date(2026, 11, 1), 10, {'batch_size': 1}, app_config))


def test_boja_missing_document_content_is_not_silent_empty_success(app_config):
    client = FakePagedJSONClient({0: {"results": [boja_item("x", published="2026-10-30", summary="")], "total_hits": 1}})
    with pytest.raises(ValueError, match="contenido clasificable"):
        BOJASource(client).fetch(FetchContext(date(2026, 11, 1), 10, {}, app_config))
