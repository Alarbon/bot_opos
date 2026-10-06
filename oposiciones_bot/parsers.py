from __future__ import annotations

import io
import logging
from urllib.parse import urljoin

import fitz
from bs4 import BeautifulSoup

from .normalization import clean_text


LOGGER = logging.getLogger(__name__)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return clean_text(soup.get_text(" ", strip=True))


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "lxml")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base_url, anchor["href"])
        if url in seen:
            continue
        seen.add(url)
        links.append((clean_text(anchor.get_text(" ", strip=True)), url))
    return links


def pdf_to_text(data: bytes, max_pages: int = 120) -> str:
    if not data.startswith(b"%PDF"):
        raise ValueError("El documento descargado no parece un PDF")
    chunks: list[str] = []
    with fitz.open(stream=io.BytesIO(data), filetype="pdf") as document:
        for page_number, page in enumerate(document):
            if page_number >= max_pages:
                LOGGER.warning("PDF truncado a %s paginas durante la extraccion", max_pages)
                break
            chunks.append(page.get_text("text"))
    return clean_text("\n".join(chunks))

