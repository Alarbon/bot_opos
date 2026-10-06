from datetime import date

from bs4 import BeautifulSoup

from oposiciones_bot.catalog import build_catalog
from oposiciones_bot.models import SourceLink
from oposiciones_bot.sources.base import FetchContext
from oposiciones_bot.sources.generic_html import GenericHTMLSource
from oposiciones_bot.process_groups import martos_process_folder


BASE = "https://martos.es/download/1150/convocatoria-bases-1-plaza-tecnico-medio-de-informatica/"


def test_card_context_does_not_leak_whole_list():
    soup = BeautifulSoup('<div class="wpfd_list"><div class="filecontent"><h3><a href="a">Técnico informático</a></h3><div>Fecha añadida: 24-07-2025</div></div><div class="filecontent">Otra convocatoria</div></div>', "lxml")
    card = GenericHTMLSource._result_container(soup.a)
    assert "Otra convocatoria" not in card.get_text()
    assert GenericHTMLSource._publication_date(card) == "2025-07-24"


def test_group_only_explicit_martos_process_folder():
    assert martos_process_folder(BASE + "a.pdf")[0] == "1150"
    assert martos_process_folder(BASE.replace("martos.es", "example.com") + "a.pdf") is None
    assert martos_process_folder("https://martos.es/download/1150/otros-documentos/a.pdf") is None


def test_source_groups_forms_and_announcements(app_config):
    html = ''.join(f'<div class="filecontent"><h3><a href="{BASE}{i}/file.pdf">{title}</a></h3><div>Fecha añadida: {day}</div></div>' for i, title, day in [(1, "Modelo solicitud plaza técnico informático", "24-07-2025"), (2, "Convocatoria fecha de examen técnico informático", "28-10-2025")])
    class Client:
        def get_text(self, url):
            return html
    app_config.data["collection"] = {"fetch_document_text": False}
    source = GenericHTMLSource("ayuntamiento_martos", Client())
    result = source.fetch(FetchContext(date(2026, 10, 6), 10, {"urls": ["https://martos.es/listado"]}, app_config))
    assert len(result) == 1
    assert result[0].reference == "MARTOS-CATEGORY-1150"
    assert result[0].publication_date == "2025-10-28"
    assert len(result[0].links) == 2
    assert "fecha de examen" in result[0].raw["current_milestone_text"]


def test_legacy_docs_group_without_deleting_and_keep_old_ids(store, candidate_factory, included_decision, app_config):
    ids = []
    for i, title in [(1, "Modelo solicitud plaza técnico informático"), (2, "Acta tribunal ejercicio práctico plaza técnico informático")]:
        url = BASE + f"{i}/file.pdf"
        candidate = candidate_factory(source="ayuntamiento_martos", source_id=str(i), reference=f"DOC-{i}", title=title, url=url, links=[SourceLink("ayuntamiento_martos", url)])
        ids.append(store.ingest(candidate, included_decision).process_id)
    assert len(set(ids)) == 2
    catalog = build_catalog(store, app_config, date(2026, 10, 6))
    assert len(catalog["processes"]) == 1
    process = catalog["processes"][0]
    assert len(process["links"]) == 2
    assert len(process["summary"]) < 200
    assert store.connection.execute("SELECT COUNT(*) FROM processes").fetchone()[0] == 2
    store.follow(ids[1][:8])
    assert store.is_followed(ids[0]) and store.is_followed(ids[1])
    store.follow(ids[0][:8], False)
    assert not store.is_followed(ids[1])
