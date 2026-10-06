from datetime import date

import pytest
import requests

from oposiciones_bot.http import HttpClient, HttpSettings
from oposiciones_bot.sources.bop_jaen import BOPJaenSource


def test_explicit_latin1_preserves_informatica(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response.headers['Content-Type'] = 'text/html; charset=iso-8859-1'
    response.encoding = 'iso-8859-1'
    response._content = 'Técnico de informática'.encode('latin-1')
    client = HttpClient(HttpSettings())
    monkeypatch.setattr(client, 'get', lambda *args, **kwargs: response)
    assert client.get_text('https://example.org') == 'Técnico de informática'
    assert BOPJaenSource._potentially_relevant(client.get_text('https://example.org') + ' convocatoria')


@pytest.mark.parametrize('bulletin_day,accepted', [('2026-10-06', True), ('2026-10-05', False)])
def test_official_home_fallback_requires_same_day(bulletin_day, accepted):
    failure = requests.HTTPError('500')

    class Client:
        def get_text(self, url):
            if '/bop/' in url:
                raise failure
            return f'<article><a href="/descargarws.dip?fechaBoletin={bulletin_day}">Edicto</a></article>'

    source = BOPJaenSource(Client())
    if accepted:
        assert 'Edicto' in source._daily_html('https://bop.dipujaen.es/bop/06-10-2026', date(2026, 10, 6))
    else:
        with pytest.raises(RuntimeError, match="no corresponde"):
            source._daily_html('https://bop.dipujaen.es/bop/06-10-2026', date(2026, 10, 6))


def test_private_proxy_is_preferred_and_authenticated(monkeypatch):
    calls = []

    class Client:
        def get_text(self, url, **kwargs):
            calls.append((url, kwargs))
            return '<article><p class="edicto">Convocatoria informatica</p></article>'

    source = BOPJaenSource(Client())
    source._proxy_base_url = 'https://worker.example'
    source._proxy_token = 'private-token'

    html = source._daily_html(
        'https://bop.dipujaen.es/bop/06-10-2026', date(2026, 10, 6)
    )

    assert 'Convocatoria' in html
    assert calls == [
        (
            'https://worker.example/bop/day/06-10-2026',
            {'headers': {'Authorization': 'Bearer private-token'}},
        )
    ]


def test_private_proxy_failure_falls_back_to_official_origin():
    calls = []

    class Client:
        def get_text(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.startswith('https://worker.example/'):
                raise requests.HTTPError('502')
            return '<article>Origen oficial</article>'

    source = BOPJaenSource(Client())
    source._proxy_base_url = 'https://worker.example'
    source._proxy_token = 'private-token'

    html = source._daily_html(
        'https://bop.dipujaen.es/bop/06-10-2026', date(2026, 10, 6)
    )

    assert 'Origen oficial' in html
    assert calls[1][0] == 'https://bop.dipujaen.es/bop/06-10-2026'
    assert calls[1][1] == {}


def test_private_document_proxy_uses_only_day_and_numeric_edict():
    calls = []

    class Client:
        def get_bytes(self, url, **kwargs):
            calls.append((url, kwargs))
            return b'%PDF-safe'

    source = BOPJaenSource(Client())
    source._proxy_base_url = 'https://worker.example/'
    source._proxy_token = 'private-token'

    result = source._document_bytes(
        'https://bop.dipujaen.es/descargarws.dip?numeroEdicto=4696',
        date(2026, 10, 6),
        '4696',
    )

    assert result == b'%PDF-safe'
    assert calls == [
        (
            'https://worker.example/bop/edict/06-10-2026/4696',
            {'headers': {'Authorization': 'Bearer private-token'}},
        )
    ]


@pytest.mark.parametrize(
    'html,accepted',
    [
        (
            '<article><a href="https://bop.dipujaen.es/descargarws.dip?'
            'fechaBoletin=2026-10-06&amp;numeroEdicto=4696">PDF</a></article>',
            True,
        ),
        ('<p>No hay ningún boletín publicado para el día 06-10-2026</p>', True),
        ('<h1>Error temporal</h1>', False),
        (
            '<article><a href="https://bop.dipujaen.es/descargarws.dip?'
            'fechaBoletin=2026-10-05&amp;numeroEdicto=4696">PDF</a></article>',
            False,
        ),
    ],
)
def test_daily_html_must_prove_requested_date_coverage(html, accepted):
    assert BOPJaenSource._valid_daily_html(html, date(2026, 10, 6)) is accepted
