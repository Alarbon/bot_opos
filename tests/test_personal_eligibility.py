from datetime import date
from pathlib import Path

import pytest

from oposiciones_bot.classifiers import can_apply, evaluate_candidate, infer_status
from oposiciones_bot.models import ProcessStatus, SourceLink
from oposiciones_bot.pipeline import collect, dispatch
from oposiciones_bot.models import OutboxStatus
from oposiciones_bot.config import load_config


@pytest.mark.parametrize("title,group,qualification,expected", [
    ("Convocatoria Técnico Informático", "C1", "Bachiller o título de Técnico", True),
    ("Convocatoria Cuerpo de Técnicos Auxiliares de Informática de la Administración del Estado", "C1", "Bachiller o título de Técnico", True),
    ("Convocatoria Técnico Especialista en Informática", "B", "Técnico Superior en Desarrollo de Aplicaciones Multiplataforma", True),
    ("Convocatoria Administrativo", "C1", "Bachiller o título de Técnico", False),
    ("Convocatoria Técnico Informático", "A2", "Grado universitario en Ingeniería Informática", False),
    ("Convocatoria Técnico Informático", "C1", "", False),
])
def test_actual_profile_accepts_bachiller_only_for_it_posts(candidate_factory, title, group, qualification, expected):
    config = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    candidate = candidate_factory(title=title, group=group, qualification_text=qualification)
    assert evaluate_candidate(candidate, config).include is expected


@pytest.mark.parametrize("group", ["A1", "A2", "C2", ""])
def test_personal_group_filter(candidate_factory, app_config, group):
    app_config.data["eligibility"] = {"allowed_groups": ["B", "C1"]}
    assert not evaluate_candidate(candidate_factory(group=group), app_config).include


def test_admin_is_not_it_even_with_it_qualification(candidate_factory, app_config):
    candidate = candidate_factory(title="Convocatoria auxiliar administrativo", qualification_text="Técnico Superior en Desarrollo de Aplicaciones Multiplataforma")
    assert not evaluate_candidate(candidate, app_config).include


def test_marks_attachment_is_not_new_opportunity(candidate_factory, app_config):
    candidate = candidate_factory(links=[SourceLink("jaen", "https://example.org/notas", label="anuncio_notas_segundo_examen_1AP.pdf")])
    evaluate_candidate(candidate, app_config)
    assert candidate.status is ProcessStatus.MARKS
    assert not can_apply(candidate, date(2026, 10, 6))


@pytest.mark.parametrize("deadline,confirmed,expected", [("2026-10-20", True, True), ("2026-10-01", True, False), (None, False, False), ("2026-10-20", False, False)])
def test_only_verified_unexpired_deadlines(candidate_factory, deadline, confirmed, expected):
    assert can_apply(candidate_factory(deadline=deadline, deadline_confirmed=confirmed), date(2026, 10, 6)) is expected


def test_sas_it_role_is_in_scope(candidate_factory, app_config):
    candidate = candidate_factory(source="sas", organisation="Servicio Andaluz de Salud", title="Convocatoria Técnico Especialista en Informática", province="", locality="")
    assert evaluate_candidate(candidate, app_config).include


@pytest.mark.parametrize("qualification,expected", [
    ("Bachiller o título de Técnico", False),
    ("Título de Técnico Superior de cualquier familia", False),
    ("Título de Técnico Superior en Desarrollo de Aplicaciones Multiplataforma", True),
    ("Título de Técnico Superior en Desarrollo de Aplicaciones Web", True),
    ("Título de Técnico Superior en Administración de Sistemas Informáticos en Red", True),
    ("Título de Técnico Superior de la familia profesional Informática y Comunicaciones", True),
])
def test_required_it_family(candidate_factory, app_config, qualification, expected):
    app_config.data["eligibility"] = {"require_it_qualification": True}
    assert evaluate_candidate(candidate_factory(qualification_text=qualification), app_config).include is expected


def test_new_marks_not_ingested_but_known_process_followed(candidate_factory, app_config, store, included_decision):
    app_config.data["eligibility"] = {"only_enrollable_new": True}
    app_config.data["reminders"] = {"enabled": False}
    candidate = candidate_factory(full_text="Calificaciones del segundo examen")
    class Source:
        name = "test"
        def fetch(self, context):
            return [candidate]
    kwargs = dict(config=app_config, store=store, client=object(), sources=[(Source(), {})], today=date(2026, 10, 6))
    assert collect(**kwargs).created == 0
    store.ingest(candidate_factory(), included_decision, notify=False)
    assert collect(**kwargs).updated == 1


def test_old_pending_a2_notice_suppressed_before_send(candidate_factory, app_config, store, included_decision):
    app_config.data["eligibility"] = {"only_enrollable_new": True, "allowed_groups": ["B", "C1"]}
    store.ingest(candidate_factory(group="A2"), included_decision)
    class Telegram:
        def send(self, text):
            raise AssertionError("No debe enviar un aviso A2")
    result = dispatch(store=store, telegram=Telegram(), config=app_config)
    assert result.sent == 0
    assert store.list_outbox()[0]["status"] == OutboxStatus.SUPPRESSED.value
