from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from oposiciones_bot.config import AppConfig
from oposiciones_bot.sources.ayuntamiento_jaen import AyuntamientoJaenSource
from oposiciones_bot.sources.base import FetchContext


FIXTURES = Path(__file__).parent / "fixtures"


class FakeAjaxResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.encoding = "ISO-8859-1"
        self.apparent_encoding = "utf-8"
        self.raise_calls = 0

    def raise_for_status(self) -> None:
        self.raise_calls += 1


class FakeAjaxSession:
    def __init__(self, response: FakeAjaxResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeAjaxResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


class FakeAyuntamientoClient:
    def __init__(self, landing: str, ajax: str) -> None:
        self.landing = landing
        self.response = FakeAjaxResponse(ajax)
        self.session = FakeAjaxSession(self.response)
        self.settings = SimpleNamespace(timeout=17, max_bytes=100_000)
        self.text_calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.text_calls.append(url)
        return self.landing


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_ayuntamiento_jaen_discovers_active_tab_and_parses_ajax_dataset(
    app_config: AppConfig,
) -> None:
    client = FakeAyuntamientoClient(
        fixture_text("ayuntamiento_jaen_landing.html"),
        fixture_text("ayuntamiento_jaen_ajax.html"),
    )
    source = AyuntamientoJaenSource(client)  # type: ignore[arg-type]
    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=10,
        source_config={},
        app_config=app_config,
    )

    candidates = source.fetch(context)

    assert client.text_calls == [AyuntamientoJaenSource.landing_url]
    assert len(client.session.calls) == 1
    call = client.session.calls[0]
    assert call["url"] == AyuntamientoJaenSource.ajax_url
    assert call["data"]["eventArguments"] == "KEY=PROCESOS_ACTIVOS_42"
    assert call["data"]["eventScreenId"] == "PTS2_EMPLEO"
    assert call["headers"] == {
        "Referer": AyuntamientoJaenSource.landing_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    assert call["timeout"] == 17
    assert client.response.raise_calls == 1
    assert client.response.encoding == "utf-8"

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_id == "JAEN-100"
    assert candidate.reference == "EXP-2026-100"
    assert candidate.publication_date == "2026-10-05"
    assert candidate.title == "Convocatoria de Técnico Informático"
    assert candidate.organisation == "Ayuntamiento de Jaén"
    assert candidate.locality == "Jaen"
    assert candidate.province == "Jaen"
    assert candidate.raw == {
        "tab": "PROCESOS_ACTIVOS_42",
        "sender_id": "ORG-1",
        "tablon": "Procesos selectivos",
    }


def test_ayuntamiento_jaen_helpers_reject_missing_or_invalid_datasets() -> None:
    try:
        AyuntamientoJaenSource._process_tab_key(
            "<a onclick=\"load('KEY=OLD')\">Histórico de procesos selectivos</a>"
        )
    except ValueError as exc:
        assert "pestana" in str(exc)
    else:
        raise AssertionError("La pestaña histórica no debe aceptarse como activa")

    for html in (
        "<script>var otra_variable = [];</script>",
        "<script>var dataset_PTS2_EMPLEO = {\"items\": []};</script>",
    ):
        try:
            AyuntamientoJaenSource._embedded_dataset(html)
        except ValueError as exc:
            assert "dataset_PTS2_EMPLEO" in str(exc)
        else:
            raise AssertionError("Un dataset ausente o no-lista debe fallar")
