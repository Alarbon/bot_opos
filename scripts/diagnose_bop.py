"""Read-only BOP connectivity diagnostics; never prints credentials/cookies."""
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import re
import requests


def main():
    day = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%d-%m-%Y")
    session = requests.Session()
    session.headers.update({"User-Agent": "oposiciones-bot/0.1 (+uso personal; contacto configurable)", "Accept-Language": "es-ES,es;q=0.9"})
    for path in ["/robots.txt", "/", f"/bop/{day}"]:
        url = "https://bop.dipujaen.es" + path
        try:
            response = session.get(url, timeout=25)
            print(f"URL: {url}\nHTTP: {response.status_code}\nFinal: {response.url}")
            for header in ["Content-Type", "Server", "Via", "X-Powered-By", "Location", "Retry-After"]:
                if header in response.headers:
                    print(f"{header}: {response.headers[header]}")
            print(f"Bytes: {len(response.content)}; SHA256: {hashlib.sha256(response.content).hexdigest()}")
            articles = len(re.findall(r"<article\b", response.text))
            print(f"Articulos: {articles}")
            if response.status_code >= 400:
                body = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", " ", response.content.decode("utf-8", errors="replace"), flags=re.S | re.I)
                body = re.sub(r"<[^>]+>", " ", body)
                print("Error (extracto):", " ".join(body.split())[:2000])
        except requests.RequestException as exc:
            print("Error de conexion:", type(exc).__name__, str(exc))
        print()

    archive = session.get(
        "https://bophistorico.dipujaen.es/results.vm",
        params={"c": "1", "f": "", "l": "15", "lang": "es", "o": "", "p": "0", "s": "0", "t": "-creation", "view": "boletin", "w": "informatica"},
        timeout=40,
    )
    print(f"Archivo oficial HTTP: {archive.status_code}; Bytes: {len(archive.content)}")
    print(f"Resultados enlazados: {len(set(re.findall(r'viewer\\.vm\\?id=([0-9]+)', archive.text)))}")


if __name__ == "__main__":
    main()
