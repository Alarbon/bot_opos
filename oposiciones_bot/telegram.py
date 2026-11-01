from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

import requests

from .models import Candidate, EventKind


class TelegramError(RuntimeError):
    """Fallo confirmado por Telegram; el evento se puede reintentar."""

    def __init__(self, message: str, *, retry_after: int | None = None, status_code: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


class TelegramAmbiguousError(RuntimeError):
    """No se sabe si Telegram acepto el mensaje; no se reintenta automaticamente."""


@dataclass(slots=True)
class TelegramSettings:
    token: str
    chat_id: str
    parse_mode: str = "HTML"
    disable_web_page_preview: bool = True
    timeout: int = 20

    @classmethod
    def from_environment(
        cls,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> "TelegramSettings | None":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            return None
        return cls(
            token=token,
            chat_id=chat_id,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )


class TelegramClient:
    def __init__(self, settings: TelegramSettings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()

    def send(self, text: str, *, reply_markup: dict | None = None) -> str:
        url = f"https://api.telegram.org/bot{self.settings.token}/sendMessage"
        payload = {
            "chat_id": self.settings.chat_id,
            "text": text,
            "parse_mode": self.settings.parse_mode,
            "disable_web_page_preview": self.settings.disable_web_page_preview,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            response = self.session.post(url, json=payload, timeout=self.settings.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TelegramAmbiguousError(
                "La conexion termino sin respuesta concluyente de Telegram"
            ) from exc
        except requests.RequestException as exc:
            raise TelegramError(f"Fallo confirmado al preparar el envio: {exc.__class__.__name__}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            if response.ok:
                raise TelegramAmbiguousError("Telegram devolvio una respuesta no interpretable") from exc
            raise TelegramError(
                f"Telegram respondio HTTP {response.status_code}",
                status_code=response.status_code,
            ) from exc
        if not response.ok or not data.get("ok"):
            description = str(data.get("description") or f"HTTP {response.status_code}")
            retry_after = (data.get("parameters") or {}).get("retry_after")
            suffix = f"; retry_after={retry_after}" if retry_after else ""
            raise TelegramError(
                f"Telegram rechazo el envio: {description}{suffix}",
                retry_after=int(retry_after) if str(retry_after or "").isdigit() else None,
                status_code=response.status_code,
            )
        message_id = (data.get("result") or {}).get("message_id")
        return str(message_id or "")


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def event_keyboard(event: dict[str, Any]) -> dict | None:
    """Persistent UUIDs work even after catalogue order changes."""
    import re
    payload = json.loads(event["payload_json"])
    process_id = str(payload.get("process_id") or "")
    if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", process_id):
        return None
    rows = [[{"text": "🔎 Ver detalles", "callback_data": f"d:{process_id}:a:1"}]]
    # The Worker checks the current followed state; notification snapshots
    # need not pretend to know it.
    rows.append([{"text": "⭐ Seguir", "callback_data": f"f:{process_id}:a:1"}])
    candidate = payload.get("candidate") or {}
    url = _safe_url(str(candidate.get("url") or ""))
    if url:
        rows.append([{"text": "🔗 Fuente oficial", "url": url}])
    rows.append([{"text": "📋 Copiar comando", "copy_text": {"text": f"/detalle {process_id}"}}])
    return {"inline_keyboard": rows}


def _e_limited(value: Any, max_escaped: int) -> str:
    """Escapa y recorta sin partir entidades HTML."""
    pieces: list[str] = []
    size = 0
    truncated = False
    for char in str(value or ""):
        escaped = html.escape(char, quote=True)
        if size + len(escaped) > max_escaped - 1:
            truncated = True
            break
        pieces.append(escaped)
        size += len(escaped)
    if truncated:
        pieces.append("…")
    return "".join(pieces)


def _date_es(value: str | None) -> str:
    if not value:
        return "No indicada"
    try:
        parsed = date.fromisoformat(value)
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return value


def _change_value(field: Any, value: Any) -> str:
    if value is None or value == "":
        return "sin dato"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if field in {"deadline", "exam_date"}:
        return _date_es(str(value))
    return str(value)


def _safe_url(value: str) -> str:
    parsed = urlparse(value or "")
    return value if parsed.scheme in {"http", "https"} else ""


def render_event(event: dict[str, Any]) -> str:
    payload = json.loads(event["payload_json"])
    candidate = Candidate.from_dict(payload["candidate"])
    kind = EventKind(event["kind"])
    if kind is EventKind.UPDATE:
        headline = "🔄 <b>ACTUALIZACIÓN DE CONVOCATORIA</b>"
    elif kind is EventKind.REMINDER:
        days = int(payload.get("days_before", 0))
        headline = f"⏰ <b>FIN DE PLAZO EN {days} DÍA{'S' if days != 1 else ''}</b>"
    elif kind is EventKind.REVIEW:
        headline = "🔎 <b>POSIBLE OPORTUNIDAD — REVISAR</b>"
    elif candidate.special_process == "TAI_ESTADO":
        headline = "🇪🇸 <b>TAI ESTADO — NUEVA CONVOCATORIA</b>"
    elif candidate.special_process == "JUNTA_INFORMATICA":
        headline = "🟢 <b>JUNTA DE ANDALUCÍA — INFORMÁTICA</b>"
    else:
        headline = "🚨 <b>NUEVA OPOSICIÓN INFORMÁTICA</b>"

    lines = [headline, ""]
    lines.extend(
        [
            f"🏛 <b>Organismo:</b> {_e_limited(candidate.organisation, 500)}",
            f"💻 <b>Puesto:</b> {_e_limited(candidate.title, 700)}",
        ]
    )
    lines.extend([
        f"🏘 <b>Municipio:</b> {_e_limited(candidate.locality or 'No confirmado', 200)}",
        f"📍 <b>Provincia:</b> {_e_limited(candidate.province or 'No confirmada', 200)}",
        f"🌐 <b>Ámbito:</b> {_e_limited(candidate.scope or 'No confirmado', 200)}",
        "Destino concreto: comprobar en las bases; el ámbito no garantiza destino.",
    ])
    qualification = candidate.qualification_text or "No confirmada automáticamente"
    lines.extend(
        [
            f"🎓 <b>Titulación:</b> {_e_limited(qualification, 600)}",
            f"✅ <b>Titulación FP Superior:</b> {_e(candidate.compatibility.value)}",
        ]
    )
    if candidate.positions is not None:
        lines.append(f"👥 <b>Plazas:</b> {candidate.positions}")
    if candidate.group:
        lines.append(f"🏷 <b>Grupo:</b> {_e(candidate.group)}")
    raw = candidate.raw if isinstance(candidate.raw, dict) else {}
    relative = raw.get("relative_deadline") if isinstance(raw.get("relative_deadline"), dict) else None
    if candidate.deadline:
        deadline_text = _date_es(candidate.deadline) + (
            "" if candidate.deadline_confirmed else " (sin confirmar)"
        )
    elif relative:
        deadline_text = (
            f"{relative.get('days')} días {'hábiles' if relative.get('kind') == 'habiles' else 'naturales'} "
            f"desde la publicación{' en ' + relative['starts_after'] if relative.get('starts_after') else ''} "
            "— fecha exacta sin confirmar; consúltala en las bases"
        )
    else:
        deadline_text = "No indicada"
    lines.extend(
        [
            f"🚪 <b>Acceso:</b> {_e(candidate.access.value)}",
            f"📌 <b>Estado:</b> {_e(candidate.status.value)}",
            f"📅 <b>Publicación:</b> {_e(_date_es(candidate.publication_date))}",
            f"⏳ <b>Fin de solicitudes:</b> {_e_limited(deadline_text, 300)}",
        ]
    )
    if candidate.exam_date:
        provisional = " (provisional según el anuncio)" if raw.get("exam_date_provisional") else ""
        lines.append(f"🗓 <b>Examen:</b> {_e(_date_es(candidate.exam_date))}{provisional}")
    if candidate.exam_time:
        lines.append(f"🕐 <b>Hora:</b> {_e(candidate.exam_time)}")
    if candidate.exam_place:
        lines.append(f"📌 <b>Lugar:</b> {_e_limited(candidate.exam_place, 400)}")
    lines.append(f"⭐ <b>Prioridad:</b> {'★' * max(1, min(candidate.priority, 5))}")
    changes = payload.get("changes") or []
    details = [item for item in payload.get("change_details") or [] if isinstance(item, dict)]
    if details and kind is EventKind.UPDATE:
        lines.extend(["", "<b>Cambios detectados:</b>"])
        for item in details[:6]:
            lines.append(
                f"• {_e_limited(item.get('label') or item.get('field'), 60)}: "
                f"{_e_limited(_change_value(item.get('field'), item.get('old')), 160)} → "
                f"<b>{_e_limited(_change_value(item.get('field'), item.get('new')), 160)}</b>"
            )
        source = _safe_url(str(details[0].get("source_url") or ""))
        reference = details[0].get("source_reference") or ""
        if source and len(_e(source)) <= 1000:
            lines.append(
                f'📄 Documento que lo indica: <a href="{_e(source)}">'
                f"{_e_limited(reference or 'fuente oficial', 80)}</a>"
            )
        other = [label for label in changes if label not in {item.get("label") for item in details}]
        if other:
            lines.append(f"Otros cambios: {_e_limited(', '.join(other), 300)}")
    elif changes and kind is EventKind.UPDATE:
        lines.extend(
            ["", f"<b>Cambios:</b> {_e_limited(', '.join(changes), 500)}"]
        )

    seen_urls: set[str] = set()
    link_lines: list[str] = []
    for link in candidate.links:
        url = _safe_url(link.url)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        escaped_url = _e(url)
        if len(escaped_url) > 1000:
            continue
        link_lines.append(
            f'🔗 <a href="{escaped_url}">{_e_limited(link.label or "Fuente oficial", 120)}</a>'
        )
    primary = _safe_url(candidate.url)
    if primary and primary not in seen_urls:
        escaped_primary = _e(primary)
        if len(escaped_primary) <= 1000:
            link_lines.append(f'🔗 <a href="{escaped_primary}">Fuente oficial</a>')

    footer = f"<code>Ref. aviso: {_e(event['event_id'][:12])}</code>"
    if link_lines:
        lines.append("")
        for link_line in link_lines[:5]:
            proposed = "\n".join(lines + [link_line, "", footer])
            if len(proposed) > 4000:
                break
            lines.append(link_line)
    lines.extend(["", footer])
    message = "\n".join(lines)
    # Los campos variables tienen presupuesto propio; esta comprobacion evita
    # volver a introducir un corte arbitrario dentro del HTML.
    if len(message) > 4096:
        raise ValueError("El mensaje Telegram supera el limite tras el render seguro")
    return message

