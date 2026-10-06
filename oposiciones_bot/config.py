from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class AppConfig:
    data: dict[str, Any]
    path: Path

    def get(self, dotted: str, default: Any = None) -> Any:
        current: Any = self.data
        for key in dotted.split("."):
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    @property
    def database_path(self) -> Path:
        configured = Path(self.get("database.path", "data/oposiciones.db"))
        if configured.is_absolute():
            return configured
        return (self.path.parent / configured).resolve()

    @property
    def sources(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self.get("sources", {}))


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"No existe el fichero de configuracion: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml debe contener un objeto YAML en la raiz")
    return AppConfig(data=data, path=config_path)

