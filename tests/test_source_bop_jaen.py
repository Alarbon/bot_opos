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
