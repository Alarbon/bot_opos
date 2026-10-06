from __future__ import annotations

import pytest

from oposiciones_bot.sources.base import has_it_signal, has_public_job_signal


@pytest.mark.parametrize(
    "text",
    [
        "Técnica informática",
        "Programadores de aplicaciones",
        "Desarrolladora de software",
        "Administrador de sistemas",
        "Técnico de sistemas",
        "Sistemas informáticos",
        "Sistemas y bases de datos",
        "Tecnologías de la información",
        "Soporte TIC",
        "Especialista en ciberseguridad",
        "C1.2003 opción informática",
    ],
)
def test_it_signal_regex_accepts_real_it_roles(text: str) -> None:
    assert has_it_signal(text)


@pytest.mark.parametrize(
    "text",
    [
        "Administración de Justicia",
        "Nota informativa sobre estadística",
        "Actividad artística y cultural",
        "Técnico de administración general",
        "Curso de gestión administrativa",
    ],
)
def test_it_signal_regex_rejects_substring_false_positives(text: str) -> None:
    assert not has_it_signal(text)


@pytest.mark.parametrize(
    "text",
    [
        "Convocatoria de una plaza",
        "Proceso selectivo por oposición",
        "Bolsa de empleo temporal",
        "Lista definitiva de admitidos y excluidos",
        "Fecha del segundo ejercicio",
        "Bases reguladoras y tribunal calificador",
        "Nombramiento y resultados finales",
    ],
)
def test_public_job_signal_regex_accepts_selection_vocabulary(text: str) -> None:
    assert has_public_job_signal(text)


@pytest.mark.parametrize(
    "text",
    [
        "Curso práctico de programación",
        "Noticias de empleo en el sector privado",
        "Información general del ayuntamiento",
    ],
)
def test_public_job_signal_regex_rejects_unrelated_content(text: str) -> None:
    assert not has_public_job_signal(text)
