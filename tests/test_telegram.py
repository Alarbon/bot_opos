from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import pytest
import requests

from oposiciones_bot.db import SQLiteStore
from oposiciones_bot.models import EventKind, OutboxStatus, SourceLink
from oposiciones_bot.pipeline import dispatch
from oposiciones_bot.telegram import TelegramClient, TelegramSettings, render_event


@dataclass
class FakeResponse:
    status_code: int
    body: dict[str, Any]

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self) -> dict[str, Any]:
        return self.body


class FakeSession:
    def __init__(
        self,
        *,
        response: FakeResponse | None = None,
        error: requests.RequestException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: int,
    ) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def event_for(candidate: Any, *, kind: EventKind = EventKind.NEW) -> dict[str, Any]:
    return {
        "event_id": "0123456789abcdef0123456789abcdef",
        "kind": kind.value,
        "payload_json": json.dumps(
            {
                "process_id": "process-1",
                "candidate": candidate.to_dict(),
                "changes": ["fecha <límite> & examen"],
                "days_before": 1,
            },
            ensure_ascii=False,
        ),
    }


def test_render_event_escapes_remote_text_and_rejects_unsafe_links(
    candidate_factory,
) -> None:
    candidate = candidate_factory(
        title='<script>alert("x")</script> & Técnico',
        organisation="Ayuntamiento <Martos> & asociados",
        qualification_text='Bachiller < Técnico & "equivalente"',
        url="javascript:alert(1)",
        links=[
            SourceLink(
                source="boe",
                url='https://example.test/bases?a=1&b="dos"',
                label="Bases <oficiales> & anexos",
            ),
            SourceLink(
                source="maliciosa",
                url="javascript:alert(document.cookie)",
                label="No debe aparecer",
            ),
        ],
    )

    message = render_event(event_for(candidate, kind=EventKind.UPDATE))

    assert "<script>" not in message
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; Técnico" in message
    assert "Ayuntamiento &lt;Martos&gt; &amp; asociados" in message
    assert "Bachiller &lt; Técnico &amp; &quot;equivalente&quot;" in message
    assert "fecha &lt;límite&gt; &amp; examen" in message
    assert "javascript:" not in message
    assert (
        'href="https://example.test/bases?a=1&amp;b=&quot;dos&quot;"' in message
    )
    assert "Bases &lt;oficiales&gt; &amp; anexos" in message
    assert "Ref. aviso: 0123456789ab" in message


def test_render_event_shows_exam_date(candidate_factory) -> None:
    message = render_event(
        event_for(
            candidate_factory(
                exam_date="2026-11-27",
                exam_time="12:30",
                exam_place="Aula Magna",
            )
        )
    )

    assert "<b>Examen:</b> 27/11/2026" in message
    assert "<b>Hora:</b> 12:30" in message
    assert "<b>Lugar:</b> Aula Magna" in message


def test_render_event_stays_below_telegram_limit_without_cutting_an_entity(
    candidate_factory,
) -> None:
    candidate = candidate_factory(title="<" * 5_000)

    message = render_event(event_for(candidate))
    content_before_truncation_marker = message.split("\n…\n", 1)[0]

    assert len(message) <= 4_096
    assert message.count("Ref. aviso: 0123456789ab") == 1
    assert not re.search(
        r"&(?:#(?:x[0-9a-fA-F]*)?|[A-Za-z]*)$",
        content_before_truncation_marker,
    )


def test_render_event_does_not_cut_inside_html_markup(candidate_factory) -> None:
    candidate = candidate_factory(
        links=[
            SourceLink(
                source="boe",
                url="https://example.test/bases",
                label="Documento oficial " + "x" * 5_000,
            )
        ]
    )

    message = render_event(event_for(candidate))

    assert len(message) <= 4_096
    for tag in ("b", "a", "code"):
        assert len(re.findall(fr"<{tag}(?:\s[^>]*)?>", message)) == message.count(
            f"</{tag}>"
        )


def test_telegram_client_success_uses_expected_request_payload() -> None:
    session = FakeSession(
        response=FakeResponse(
            200,
            {"ok": True, "result": {"message_id": 987}},
        )
    )
    client = TelegramClient(
        TelegramSettings(token="secret-token", chat_id="12345", timeout=7),
        session=session,  # type: ignore[arg-type]
    )

    assert client.send("<b>mensaje</b>") == "987"
    assert session.calls == [
        {
            "url": "https://api.telegram.org/botsecret-token/sendMessage",
            "json": {
                "chat_id": "12345",
                "text": "<b>mensaje</b>",
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            "timeout": 7,
        }
    ]


def test_telegram_settings_accept_preview_configuration(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    settings = TelegramSettings.from_environment(
        parse_mode="HTML", disable_web_page_preview=False
    )

    assert settings is not None
    assert settings.disable_web_page_preview is False


def make_client(session: FakeSession) -> TelegramClient:
    return TelegramClient(
        TelegramSettings(token="secret-token", chat_id="12345"),
        session=session,  # type: ignore[arg-type]
    )


def test_dispatch_success_marks_event_sent(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    result = store.ingest(candidate_factory(), included_decision)
    session = FakeSession(
        response=FakeResponse(200, {"ok": True, "result": {"message_id": 321}})
    )

    summary = dispatch(store=store, telegram=make_client(session))

    assert summary.claimed == 1
    assert summary.sent == 1
    assert summary.retryable == 0
    assert summary.uncertain == 0
    event = store.list_outbox()[0]
    assert event["status"] == OutboxStatus.SENT.value
    assert event["telegram_message_id"] == "321"
    assert event["attempts"] == 1
    assert len(session.calls) == 1
    assert result.event_id[:12] in session.calls[0]["json"]["text"]


def test_dispatch_confirmed_rejection_is_retryable_and_can_succeed_later(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
) -> None:
    store.ingest(candidate_factory(), included_decision)
    rejected = FakeSession(
        response=FakeResponse(
            429,
            {
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 11},
            },
        )
    )

    first = dispatch(store=store, telegram=make_client(rejected))

    assert first.claimed == 1
    assert first.retryable == 1
    event = store.list_outbox()[0]
    assert event["status"] == OutboxStatus.RETRYABLE.value
    assert "retry_after=11" in event["last_error"]

    accepted = FakeSession(
        response=FakeResponse(200, {"ok": True, "result": {"message_id": 654}})
    )
    second = dispatch(store=store, telegram=make_client(accepted))

    assert second.claimed == 1
    assert second.sent == 1
    event = store.list_outbox()[0]
    assert event["status"] == OutboxStatus.SENT.value
    assert event["attempts"] == 2
    assert event["telegram_message_id"] == "654"


@pytest.mark.parametrize(
    "network_error",
    [
        pytest.param(requests.Timeout("timeout"), id="timeout"),
        pytest.param(requests.ConnectionError("connection reset"), id="connection"),
    ],
)
def test_dispatch_ambiguous_transport_failure_is_uncertain_and_not_retried(
    store: SQLiteStore,
    candidate_factory,
    included_decision,
    network_error: requests.RequestException,
) -> None:
    store.ingest(candidate_factory(), included_decision)
    ambiguous = FakeSession(error=network_error)

    first = dispatch(store=store, telegram=make_client(ambiguous))

    assert first.claimed == 1
    assert first.uncertain == 1
    event = store.list_outbox()[0]
    assert event["status"] == OutboxStatus.UNCERTAIN.value
    assert "sin respuesta concluyente" in event["last_error"]
    assert event["attempts"] == 1

    accepted = FakeSession(
        response=FakeResponse(200, {"ok": True, "result": {"message_id": 777}})
    )
    second = dispatch(store=store, telegram=make_client(accepted))

    assert second.claimed == 0
    assert second.sent == 0
    assert accepted.calls == []
    assert store.list_outbox()[0]["status"] == OutboxStatus.UNCERTAIN.value
