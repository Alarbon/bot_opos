from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger(__name__)


class ResponseTooLarge(RuntimeError):
    pass


@dataclass(slots=True)
class HttpSettings:
    timeout: int = 25
    user_agent: str = "oposiciones-bot/0.1"
    max_bytes: int = 15_000_000


class HttpClient:
    def __init__(self, settings: HttpSettings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept-Language": "es-ES,es;q=0.9",
            }
        )

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", self.settings.timeout)
        response = self.session.get(url, timeout=timeout, **kwargs)
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length and int(length) > self.settings.max_bytes:
            response.close()
            raise ResponseTooLarge(f"Respuesta superior al limite permitido: {url}")
        if len(response.content) > self.settings.max_bytes:
            raise ResponseTooLarge(f"Respuesta superior al limite permitido: {url}")
        return response

    def get_json(self, url: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Accept", "application/json")
        return self.get(url, headers=headers, **kwargs).json()

    def get_text(self, url: str, **kwargs: Any) -> str:
        response = self.get(url, **kwargs)
        # Requests defaults to Latin-1 for unlabelled HTML, but an explicit
        # server charset (as used by BOP Jaen) must not be guessed away.
        content_type = response.headers.get("Content-Type", "").lower()
        if not response.encoding or (
            response.encoding.lower() == "iso-8859-1" and "charset=" not in content_type
        ):
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    def get_bytes(self, url: str, **kwargs: Any) -> bytes:
        return self.get(url, **kwargs).content

    def close(self) -> None:
        self.session.close()


def redact_url(url: str) -> str:
    if "/bot" in url and "/send" in url:
        prefix, _, suffix = url.partition("/bot")
        _, _, endpoint = suffix.partition("/")
        return f"{prefix}/bot<redacted>/{endpoint}"
    return url

