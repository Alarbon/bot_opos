from __future__ import annotations

from datetime import date

import pytest

from oposiciones_bot.sources.base import FetchContext
from oposiciones_bot.sources.bop_jaen_archive import (
    ARCHIVE_RESULTS_URL,
    ArchiveBulletin,
    ArchiveFetchError,
    ArchiveWindowStatus,
    BOPJaenArchive,
    archive_search_params,
    parse_archive_results,
    parse_bulletin_edicts,
)


RESULTS_HTML = """
<html><body>
  <div class="results">
    Pagina <strong>1</strong> de <strong>1</strong>.
    Resultados: <strong>1</strong>.
  </div>
  <div class="list-frame" id="frame-0001747080">
    <a id="thumbnail-link-0001747080"
       title="Boletin Oficial de la Provincia de Jaen. 28/9/2026. [Ejemplar]"
       href="viewer.vm?id=0001747080&amp;page=1"></a>
    <p class="list-record-name">Boletin Oficial. 28/9/2026. (77 paginas.)</p>
    <a id="open-0001747080"
       href="viewer.vm?id=0001747080&amp;page=1&amp;view=boletin">Ver</a>
    <a id="download-0001747080"
       href="high.raw?id=0001747080&amp;name=00000001.original.pdf">Descargar</a>
  </div>
</body></html>
"""


EMPTY_HTML = """
<html><body><h1>Resultados de la busqueda</h1>
<p>Su consulta no ha devuelto ningun resultado.</p></body></html>
"""


BULLETIN_TEXT = """
Numero 188
Lunes, 28 de septiembre de 2026
Pag. 14225

ADMINISTRACION LOCAL
AYUNTAMIENTO DE BAEZA (JAEN)
Aprobacion del listado definitivo de aspirantes admitidos en el proceso de
seleccion de una plaza de Tecnico de Recursos Humanos.
BOP-2026-4283
Listado definitivo de aspirantes admitidos en el proceso de seleccion de una plaza
de Tecnico Superior de Informatica y Seguridad, por promocion interna.
BOP-2026-4284

Instituto de Estudios Giennenses. Boletin Oficial. Pagina 29
Numero 188
ADMINISTRACION LOCAL
AYUNTAMIENTO DE BAEZA (JAEN)
2026/4283 Aprobacion del listado definitivo de aspirantes admitidos en el proceso de
seleccion de una plaza de Tecnico de Recursos Humanos.
Edicto
Se aprueba la lista del proceso selectivo de Recursos Humanos.
Instituto de Estudios Giennenses. Boletin Oficial. Pagina 30
Numero 188
ADMINISTRACION LOCAL
AYUNTAMIENTO DE BAEZA (JAEN)
2026/4284 Listado definitivo de aspirantes admitidos en el proceso de seleccion de una
plaza de Tecnico Superior de Informatica y Seguridad, por promocion interna.
Edicto
Se aprueba la lista definitiva de personas admitidas para Tecnico Superior de Informatica.
"""


def bulletin() -> ArchiveBulletin:
    return ArchiveBulletin(
        archive_id="0001747080",
        publication_date=date(2026, 9, 28),
        title="Boletin Oficial de la Provincia de Jaen. 28/9/2026.",
        page_count=77,
        viewer_url=(
            "https://bophistorico.dipujaen.es/"
            "viewer.vm?id=0001747080&page=1&view=boletin"
        ),
        pdf_url=(
            "https://bophistorico.dipujaen.es/"
            "high.raw?id=0001747080&name=00000001.original.pdf"
        ),
    )


def test_archive_query_uses_seven_repeated_date_values():
    params = archive_search_params(date(2026, 9, 27), date(2026, 10, 6))

    assert [value for key, value in params if key == "d"] == [
        "creation",
        "2026",
        "09",
        "27",
        "2026",
        "10",
        "06",
    ]
    with pytest.raises(ValueError, match="10 dias"):
        archive_search_params(date(2026, 9, 26), date(2026, 10, 6))


def test_parse_results_preserves_archive_id_date_and_links():
    page = parse_archive_results(
        RESULTS_HTML,
        expected_start=date(2026, 9, 27),
        expected_end=date(2026, 10, 6),
    )

    assert page.total == 1
    assert page.bulletins == (bulletin(),)


def test_valid_empty_results_are_distinct_from_malformed_html():
    assert parse_archive_results(EMPTY_HTML).total == 0
    with pytest.raises(ArchiveFetchError, match="no acredita"):
        parse_archive_results("<html><h1>Error temporal</h1></html>")


def test_results_outside_requested_range_are_rejected():
    with pytest.raises(ArchiveFetchError, match="antes de"):
        parse_archive_results(
            RESULTS_HTML,
            expected_start=date(2026, 9, 29),
            expected_end=date(2026, 10, 6),
        )


def test_bulletin_is_split_into_edicts_with_title_organisation_and_text():
    edicts = parse_bulletin_edicts(BULLETIN_TEXT, bulletin())

    assert [item.source_id for item in edicts] == ["BOP-2026-4283", "BOP-2026-4284"]
    assert edicts[1].title == (
        "Listado definitivo de aspirantes admitidos en el proceso de seleccion de una "
        "plaza de Tecnico Superior de Informatica y Seguridad, por promocion interna."
    )
    assert edicts[1].organisation == "AYUNTAMIENTO DE BAEZA (JAEN)"
    assert "lista definitiva" in edicts[1].text
    assert edicts[1].official_url.endswith(
        "fechaBoletin=2026-09-28&numeroEdicto=4284"
    )


def test_fetch_window_returns_candidate_and_does_not_overstate_archive_coverage(
    monkeypatch, app_config
):
    class Client:
        def __init__(self):
            self.search_calls = []

        def get_text(self, url, **kwargs):
            self.search_calls.append((url, kwargs["params"]))
            return RESULTS_HTML

        def get_bytes(self, url):
            assert url == bulletin().pdf_url
            return b"%PDF-fixture"

    client = Client()
    monkeypatch.setattr(
        "oposiciones_bot.sources.bop_jaen_archive.extract_bulletin_text",
        lambda _data: BULLETIN_TEXT,
    )
    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=9,
        source_config={},
        app_config=app_config,
    )

    result = BOPJaenArchive(client).fetch_window(context)

    assert client.search_calls[0][0] == ARCHIVE_RESULTS_URL
    assert result.status is ArchiveWindowStatus.LAGGING
    assert result.latest_available == date(2026, 9, 28)
    assert result.covered_through == date(2026, 9, 28)
    assert result.covered_through != context.today
    assert "actualizado solo hasta 2026-09-28" in result.coverage_warning
    assert [candidate.source_id for candidate in result.candidates] == ["BOP-2026-4284"]
    candidate = result.candidates[0]
    assert candidate.source == "bop_jaen"
    assert candidate.organisation == "AYUNTAMIENTO DE BAEZA (JAEN)"
    assert candidate.full_text
    assert candidate.raw["archive_bulletin_id"] == "0001747080"
    assert {link.label for link in candidate.links} == {
        "BOP Jaen (edicto)",
        "Historico BOP Jaen (visor del ejemplar)",
        "Historico BOP Jaen (PDF del ejemplar)",
    }


def test_fetch_window_returns_empty_status_without_claiming_coverage(app_config):
    class Client:
        def get_text(self, _url, **_kwargs):
            return EMPTY_HTML

        def get_bytes(self, _url):
            raise AssertionError("No debe descargar un PDF sin ejemplares")

    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=0,
        source_config={},
        app_config=app_config,
    )
    result = BOPJaenArchive(Client()).fetch_window(context)

    assert result.status is ArchiveWindowStatus.EMPTY
    assert result.bulletins == ()
    assert result.candidates == []
    assert result.covered_through is None
    assert "sin ejemplares" in result.coverage_warning


def test_long_context_is_split_into_non_overlapping_ten_day_queries(app_config):
    calls = []

    class Client:
        def get_text(self, _url, **kwargs):
            calls.append([value for key, value in kwargs["params"] if key == "d"])
            return EMPTY_HTML

        def get_bytes(self, _url):
            raise AssertionError

    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=20,
        source_config={},
        app_config=app_config,
    )

    BOPJaenArchive(Client()).fetch_window(context)

    assert calls == [
        ["creation", "2026", "09", "16", "2026", "09", "25"],
        ["creation", "2026", "09", "26", "2026", "10", "05"],
        ["creation", "2026", "10", "06", "2026", "10", "06"],
    ]
