from __future__ import annotations

from ..config import AppConfig
from ..http import HttpClient
from .ayuntamiento_jaen import AyuntamientoJaenSource
from .base import SourceAdapter
from .boe import BOESource
from .boja import BOJASource
from .bop_jaen import BOPJaenSource
from .diputacion_jaen import DiputacionJaenSource
from .generic_html import GenericHTMLSource
from .pag import PAGSource


def build_sources(
    config: AppConfig,
    client: HttpClient,
    selected: set[str] | None = None,
) -> list[tuple[SourceAdapter, dict]]:
    sources: list[tuple[SourceAdapter, dict]] = []
    for name, source_config in config.sources.items():
        if not source_config.get("enabled", True):
            continue
        if selected and name not in selected:
            continue
        kind = source_config.get("kind", "html")
        adapter: SourceAdapter
        if kind == "boe_api":
            adapter = BOESource(client)
        elif kind == "boja_api":
            adapter = BOJASource(client)
        elif kind == "pag":
            adapter = PAGSource(client)
        elif kind == "bop_jaen":
            adapter = BOPJaenSource(client)
        elif kind == "diputacion_jaen_api":
            adapter = DiputacionJaenSource(client)
        elif kind == "ayuntamiento_jaen":
            adapter = AyuntamientoJaenSource(client)
        elif kind == "html":
            adapter = GenericHTMLSource(name, client)
        else:
            raise ValueError(f"Tipo de fuente no soportado: {kind!r} ({name})")
        sources.append((adapter, source_config))
    return sources
