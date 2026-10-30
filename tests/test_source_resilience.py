"""Partial failures, oversized documents and changed templates per source."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
import requests

from oposiciones_bot.http import HttpClient, HttpSettings, ResponseTooLarge
from oposiciones_bot.sources.base import FetchContext
from oposiciones_bot.sources.boe import BOESource, parse_html_index
from oposiciones_bot.sources.bop_jaen_archive import (
    DEFAULT_BULLETIN_MAX_BYTES,
    ArchiveBulletin,
    ArchiveFetchError,
    ArchiveWindowStatus,
    BOPJaenArchive,
)
from oposiciones_bot.sources.diputacion_jaen import DiputacionJaenSource
from oposiciones_bot.sources.generic_html import GenericHTMLSource

FIXTURES = Path(__file__).parent / "fixtures"
BOE_INDEX = (FIXTURES / "boe_sumario_html.html").read_text(encoding="utf-8")


def http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} error", response=response)


def context(app_config, today=date(2026, 10, 29), lookback=2, **source_config):
    return FetchContext(today=today, lookback_days=lookback, source_config=source_config, app_config=app_config)


# --- HTTP client --------------------------------------------------------------


def fake_response(body: bytes, headers: dict[str, str] | None = None, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    response.raw = io.BytesIO(body)
    response.url = "https://example.org/doc.pdf"
    return response


def test_http_rejects_oversized_body_without_content_length(monkeypatch):
    client = HttpClient(HttpSettings(max_bytes=1000))
    monkeypatch.setattr(client.session, "get", lambda *a, **k: fake_response(b"x" * 5000))
    with pytest.raises(ResponseTooLarge):
        client.get_bytes("https://example.org/doc.pdf")


def test_http_rejects_declared_oversized_body_before_reading(monkeypatch):
    client = HttpClient(HttpSettings(max_bytes=1000))
    monkeypatch.setattr(
        client.session, "get",
        lambda *a, **k: fake_response(b"", {"Content-Length": "18122775"}),
    )
    with pytest.raises(ResponseTooLarge, match="18122775"):
        client.get_bytes("https://example.org/doc.pdf")


def test_http_per_call_limit_allows_a_trusted_large_document(monkeypatch):
    client = HttpClient(HttpSettings(max_bytes=1000))
    seen = {}

    def get(url, **kwargs):
        seen.update(kwargs)
        return fake_response(b"%PDF" + b"x" * 4000, {"Content-Type": "application/pdf"})

    monkeypatch.setattr(client.session, "get", get)
    assert len(client.get_bytes("https://example.org/doc.pdf", max_bytes=10_000)) == 4004
    assert seen["stream"] is True
    # The generic limit still applies to everything else.
    with pytest.raises(ResponseTooLarge):
        client.get_bytes("https://example.org/doc.pdf")


def test_http_text_and_json_still_work_after_streaming(monkeypatch):
    client = HttpClient(HttpSettings())
    monkeypatch.setattr(
        client.session, "get",
        lambda *a, **k: fake_response('{"ok": "sí"}'.encode(), {"Content-Type": "application/json; charset=utf-8"}),
    )
    assert client.get_json("https://example.org/api") == {"ok": "sí"}


# --- BOP archive ------------------------------------------------------------------


def bulletin(day: int, archive_id: str) -> ArchiveBulletin:
    return ArchiveBulletin(
        archive_id=archive_id,
        publication_date=date(2026, 10, day),
        title=f"BOP {day}",
        page_count=10,
        viewer_url=f"https://bophistorico.dipujaen.es/viewer?id={archive_id}",
        pdf_url=f"https://bophistorico.dipujaen.es/high.raw?id={archive_id}",
    )


class ArchiveClient:
    def __init__(self, failures: dict[str, Exception]):
        self.failures = failures
        self.limits: list[int | None] = []

    def get_bytes(self, url, max_bytes=None, **_kwargs):
        self.limits.append(max_bytes)
        archive_id = url.rsplit("=", 1)[-1]
        if archive_id in self.failures:
            raise self.failures[archive_id]
        return b"%PDF-" + archive_id.encode()


def run_archive(monkeypatch, failures, bulletins):
    client = ArchiveClient(failures)
    archive = BOPJaenArchive(client)
    monkeypatch.setattr(archive, "_search_window", lambda start, end: tuple(b for b in bulletins if start <= b.publication_date <= end))
    monkeypatch.setattr("oposiciones_bot.sources.bop_jaen_archive.extract_bulletin_text", lambda data: data.decode())
    monkeypatch.setattr("oposiciones_bot.sources.bop_jaen_archive.parse_bulletin_edicts", lambda text, b: ())
    return client, archive.fetch_range(date(2026, 10, 13), date(2026, 10, 20))


def test_archive_uses_a_limit_large_enough_for_a_complete_bulletin(monkeypatch):
    client, result = run_archive(monkeypatch, {}, [bulletin(14, "1"), bulletin(15, "2")])
    assert DEFAULT_BULLETIN_MAX_BYTES > 18_122_775  # real 2026-10-21 bulletin size
    assert client.limits == [DEFAULT_BULLETIN_MAX_BYTES, DEFAULT_BULLETIN_MAX_BYTES]
    assert result.status is ArchiveWindowStatus.LAGGING
    assert result.covered_through == date(2026, 10, 15)


def test_one_unreadable_bulletin_does_not_block_the_others(monkeypatch):
    bulletins = [bulletin(14, "1"), bulletin(16, "2"), bulletin(17, "3")]
    _, result = run_archive(monkeypatch, {"2": ResponseTooLarge("demasiado grande")}, bulletins)
    assert result.status is ArchiveWindowStatus.PARTIAL
    # Coverage stops the day before the failed bulletin: it will be retried.
    assert result.covered_through == date(2026, 10, 15)
    assert "2026-10-16" in result.coverage_warning
    assert "ResponseTooLarge" in result.coverage_warning
    assert [b.archive_id for b, _ in result.failed_bulletins] == ["2"]


def test_failure_on_first_day_claims_no_new_coverage(monkeypatch):
    bulletins = [bulletin(13, "1"), bulletin(16, "2")]
    _, result = run_archive(monkeypatch, {"1": ArchiveFetchError("indice ilegible")}, bulletins)
    assert result.status is ArchiveWindowStatus.PARTIAL
    assert result.covered_through is None


def test_archive_raises_when_no_bulletin_could_be_processed(monkeypatch):
    with pytest.raises(ArchiveFetchError, match="Ningun ejemplar"):
        run_archive(monkeypatch, {"1": ResponseTooLarge("x")}, [bulletin(14, "1")])


# --- BOE ------------------------------------------------------------------------


def test_boe_html_index_parser_reads_only_section_two(app_config):
    records = parse_html_index(BOE_INDEX, date(2026, 10, 29))
    assert [r["id"] for r in records] == ["BOE-A-2026-22621", "BOE-A-2026-22660", "BOE-A-2026-22661", "BOE-A-2026-22662"]
    tai = records[1]
    assert tai["section"] == "2B"
    assert tai["html_url"] == "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-22660"
    assert tai["pdf_url"].endswith("/pdfs/BOE-A-2026-22660.pdf")
    assert records[2]["department"] == "ADMINISTRACIÓN LOCAL"


@pytest.mark.parametrize(
    "html,message",
    [
        (BOE_INDEX, "no corresponde"),  # page of another day
        ("<html><title>Sumario del día 28/10/2026</title><body>Mantenimiento</body></html>", "sin secciones"),
    ],
)
def test_boe_html_index_rejects_unrecognised_pages(html, message):
    with pytest.raises(ValueError, match=message):
        parse_html_index(html, date(2026, 10, 28))


class BOEClient:
    """API returns 500 for every day (as observed since late October 2026)."""

    def __init__(self, html_by_day=None, api_status=500, html_status_by_day=None):
        self.html_by_day = html_by_day or {}
        self.api_status = api_status
        self.html_status_by_day = html_status_by_day or {}
        self.api_calls = 0
        self.html_calls: list[str] = []

    def get_json(self, url, **_kwargs):
        self.api_calls += 1
        raise http_error(self.api_status)

    def get_text(self, url, **_kwargs):
        if "/boe/dias/" in url:
            self.html_calls.append(url)
            day = url.rstrip("/").split("/boe/dias/")[1]
            if day in self.html_status_by_day:
                raise http_error(self.html_status_by_day[day])
            if day in self.html_by_day:
                return self.html_by_day[day]
            raise http_error(404)
        return "<html><body>Técnico Auxiliar de Informática. Proceso selectivo, turno libre.</body></html>"


def test_boe_falls_back_to_official_html_index_when_api_fails(app_config):
    client = BOEClient(html_by_day={"2026/10/29": BOE_INDEX})
    source = BOESource(client)
    candidates = source.fetch(context(app_config, lookback=1))
    assert {c.source_id for c in candidates} >= {"BOE-A-2026-22660"}
    # After the first server error the API is skipped for the rest of the run.
    assert client.api_calls == 1
    assert source.coverage_warning is None  # 29/10 parsed, 28/10 = no bulletin (404)


def test_boe_failed_day_is_partial_and_does_not_discard_other_days(app_config):
    client = BOEClient(
        html_by_day={"2026/10/29": BOE_INDEX},
        html_status_by_day={"2026/10/28": 503},
    )
    source = BOESource(client)
    candidates = source.fetch(context(app_config, lookback=2))
    assert any(c.source_id == "BOE-A-2026-22660" for c in candidates)
    assert source.covered_through == date(2026, 10, 27)
    assert "2026-10-28" in source.coverage_warning


def test_boe_raises_when_no_day_could_be_consulted(app_config):
    client = BOEClient(html_status_by_day={"2026/10/29": 503, "2026/10/28": 503})
    with pytest.raises(RuntimeError, match="ningun dia consultado"):
        BOESource(client).fetch(context(app_config, lookback=1))


def test_boe_404_on_both_routes_is_a_day_without_bulletin(app_config):
    client = BOEClient(api_status=404)
    source = BOESource(client)
    assert source.fetch(context(app_config, lookback=1)) == []
    assert source.coverage_warning is None


# --- Generic HTML and Diputación ------------------------------------------------


class ListingClient:
    def __init__(self, pages):
        self.pages = pages

    def get_text(self, url, **_kwargs):
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value

    def get_bytes(self, url, **_kwargs):
        raise AssertionError("no PDF expected")


LISTING = "<html><body>" + "".join(
    f'<p>Texto informativo del tablón número {i} con suficiente contenido.</p><a href="/otra/{i}">Enlace {i}</a>' for i in range(5)
) + "</body></html>"


def test_generic_listing_failure_is_reported_as_partial_coverage(app_config):
    source = GenericHTMLSource("iaap", ListingClient({
        "https://a.example/listado": LISTING,
        "https://b.example/listado": requests.ConnectTimeout("timeout"),
    }))
    app_config.data["collection"] = {"fetch_document_text": False}
    result = source.fetch(context(app_config, urls=["https://a.example/listado", "https://b.example/listado"]))
    assert result == []
    assert "b.example: ConnectTimeout" in source.coverage_warning
    assert "1 de 2" in source.coverage_warning


def test_generic_near_empty_page_is_not_silent_no_news(app_config):
    source = GenericHTMLSource("ayuntamiento_x", ListingClient({"https://a.example/": "<html><body></body></html>"}))
    source.fetch(context(app_config, urls=["https://a.example/"]))
    assert "página casi vacía" in source.coverage_warning


def test_generic_all_listings_failing_is_an_error(app_config):
    source = GenericHTMLSource("iaap", ListingClient({"https://a.example/": http_error(500)}))
    with pytest.raises(requests.HTTPError):
        source.fetch(context(app_config, urls=["https://a.example/"]))


def test_diputacion_missing_detail_is_reported(app_config):
    class Client:
        def get_json(self, url, **_kwargs):
            if "getall" in url:
                return [{"id": 7, "Slug": "abc", "titulo": "Convocatoria plaza Técnico de Informática", "fechaPublicacion": "2026-10-28"}]
            raise http_error(500)

    source = DiputacionJaenSource(Client())
    candidates = source.fetch(context(app_config, lookback=5))
    assert len(candidates) == 1
    assert "sin detalle" in source.coverage_warning
