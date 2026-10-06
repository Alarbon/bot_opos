from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from oposiciones_bot.config import AppConfig
from oposiciones_bot.db import SQLiteStore
from oposiciones_bot.models import (
    AccessType,
    Candidate,
    Compatibility,
    FilterDecision,
    ProcessStatus,
)


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make accidental real network access fail immediately in every test."""

    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("La suite de tests no permite acceso a red")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data={
            "locations": {
                "priority": ["Martos", "Torredonjimeno", "Torredelcampo", "Jaén"],
                "nearby": ["Jamilena", "Torredelcampo"],
            }
        },
        path=tmp_path / "config.yaml",
    )


@pytest.fixture
def candidate_factory() -> Callable[..., Candidate]:
    def factory(**overrides: object) -> Candidate:
        values: dict[str, object] = {
            "source": "boe",
            "source_id": "BOE-A-2026-1000",
            "title": "Convocatoria de Técnico Informático 2026",
            "organisation": "Ayuntamiento de Martos",
            "url": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-1000",
            "publication_date": "2026-10-01",
            "summary": "Proceso selectivo para dos plazas de informática",
            "full_text": "Convocatoria por turno libre para Técnico Informático.",
            "reference": "BOE-A-2026-1000",
            "official_references": ["BOE-A-2026-1000"],
            "locality": "Martos",
            "province": "Jaén",
            "scope": "LOCAL",
            "group": "C1",
            "positions": 2,
            "access": AccessType.OPEN,
            "qualification_text": "Bachiller o título de Técnico",
            "compatibility": Compatibility.COMPATIBLE,
            "deadline": "2026-10-20",
            "deadline_confirmed": True,
            "status": ProcessStatus.OPEN,
            "priority": 5,
        }
        values.update(overrides)
        return Candidate(**values)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def included_decision() -> FilterDecision:
    return FilterDecision(
        include=True,
        relevant=True,
        geographic_match=True,
        reason="compatible",
        compatibility=Compatibility.COMPATIBLE,
        access=AccessType.OPEN,
        priority=5,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStore]:
    database = SQLiteStore(tmp_path / "oposiciones.db")
    try:
        yield database
    finally:
        database.close()
