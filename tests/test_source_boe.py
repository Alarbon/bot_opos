from __future__ import annotations

from datetime import date
from typing import Any

from oposiciones_bot.config import AppConfig
from oposiciones_bot.sources.base import FetchContext
from oposiciones_bot.sources.boe import BOESource


class FakeBOEClient:
    def __init__(self) -> None:
        self.detail_calls: list[str] = []

    def get_json(self, _url: str) -> dict[str, Any]:
        return {
            "data": {
                "sumario": {
                    "diario": {
                        "seccion": {
                            "codigo": "2B",
                            "departamento": {
                                "nombre": "ADMINISTRACIÓN LOCAL",
                                "epigrafe": {
                                    "nombre": "Personal",
                                    "item": {
                                        "identificador": "BOE-A-2026-20000",
                                        "titulo": (
                                            "Resolución de 1 de octubre de 2026, del "
                                            "Ayuntamiento de Martos (Jaén), referente a "
                                            "la convocatoria para proveer una plaza."
                                        ),
                                        "url_html": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-20000",
                                        "url_pdf": {"texto": "https://www.boe.es/boe/dias/2026/10/06/pdfs/BOE-A-2026-20000.pdf"},
                                    },
                                },
                            },
                        }
                    }
                }
            }
        }

    def get_text(self, url: str) -> str:
        self.detail_calls.append(url)
        return (
            "<html><body>Convocatoria por turno libre para proveer una plaza "
            "de Técnico Informático.</body></html>"
        )


def test_boe_extracts_local_issuer_from_item_title() -> None:
    title = (
        "Resolución de 1 de octubre de 2026, del Ayuntamiento de Martos "
        "(Jaén), referente a la convocatoria para proveer una plaza."
    )

    assert BOESource._issuer_from_title(title) == "Ayuntamiento de Martos (Jaén)"


def test_boe_keeps_generic_department_when_title_has_no_local_issuer() -> None:
    assert BOESource._issuer_from_title("Resolución de la Secretaría de Estado") == ""


def test_boe_hydrates_generic_local_heading_before_deciding_relevance(
    app_config: AppConfig,
) -> None:
    client = FakeBOEClient()
    source = BOESource(client)  # type: ignore[arg-type]
    context = FetchContext(
        today=date(2026, 10, 6),
        lookback_days=0,
        source_config={},
        app_config=app_config,
    )

    candidates = source.fetch(context)

    assert len(candidates) == 1
    assert client.detail_calls
    candidate = candidates[0]
    assert candidate.organisation == "Ayuntamiento de Martos (Jaén)"
    assert "tecnico informatico" in candidate.summary
    assert candidate.positions == 1
