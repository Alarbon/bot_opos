"""Standard-library report works even when dependency installation failed."""
from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


def create_report(metrics: dict, status: str, env: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": env.get("GITHUB_RUN_ID", ""),
        "event": env.get("GITHUB_EVENT_NAME", ""),
        "url": f'https://github.com/{env.get("GITHUB_REPOSITORY", "")}/actions/runs/{env.get("GITHUB_RUN_ID", "")}',
        "status": status, "finished_at": now,
        "started_at": metrics.get("started_at"),
        "collection": metrics.get("collection"),
        "delivery": metrics.get("delivery"),
        "steps": {name: env.get(key, "unknown") for name, key in [("consulta", "COLLECT_OUTCOME"), ("guardado", "PERSIST_OUTCOME"), ("envio", "SEND_OUTCOME"), ("recibos", "RECEIPTS_OUTCOME")]},
    }


def collection_outcome(collection: dict | None) -> str:
    """OK / PARTIAL / NO_SOURCES / MISSING, also for reports of older runs."""
    if collection is None:
        return "MISSING"
    if collection.get("outcome"):
        return str(collection["outcome"])
    attempted = int(collection.get("sources_attempted") or 0)
    errors = collection.get("errors") or {}
    if attempted and len(errors) >= attempted:
        return "NO_SOURCES"
    return "PARTIAL" if errors or collection.get("warnings") else "OK"


def _brief(detail: str) -> str:
    """Short, user-facing reason; full tracebacks stay in the GitHub log."""
    text = str(detail)
    for needle, label in (
        ("ResponseTooLarge", "documento demasiado grande"),
        ("too many 500", "el servidor oficial devolvió errores 500"),
        ("ConnectTimeout", "tiempo de conexión agotado"),
        ("ReadTimeout", "tiempo de respuesta agotado"),
        ("ConnectionError", "sin conexión con el servidor"),
        ("403", "acceso rechazado (403)"),
        ("404", "página no encontrada (404)"),
    ):
        if needle in text:
            return f"{label}. Detalle: {text[:140]}"
    return text[:200]


def render_report(report: dict) -> str:
    failed = report["status"] != "success"
    collection = report.get("collection")
    errors = (collection or {}).get("errors", {})
    warnings = (collection or {}).get("warnings", {})
    delivery_problem = any((report.get("delivery") or {}).get(key, 0) for key in ("retryable", "uncertain"))
    no_sources = collection_outcome(collection) == "NO_SOURCES"
    heading = (
        "❌ BÚSQUEDA FALLIDA" if failed
        else "❌ NINGUNA FUENTE CONSULTADA — NO SIGNIFICA «SIN NOVEDADES»" if no_sources
        else "⚠️ BÚSQUEDA TERMINADA CON COBERTURA INCOMPLETA" if errors
        else "⚠️ BÚSQUEDA COMPLETADA CON AVISOS DE COBERTURA" if warnings
        else "⚠️ BÚSQUEDA TERMINADA CON AVISOS PENDIENTES" if delivery_problem
        else "✅ BÚSQUEDA COMPLETADA"
    )
    lines = [heading, "Automática" if report["event"] == "schedule" else "Manual", ""]
    if collection is None:
        lines.append("No se completó la consulta de fuentes. Revisa la ejecución.")
    else:
        attempted = collection.get("sources_attempted", 0)
        healthy = attempted - len(errors) - len(warnings)
        lines.extend([
            f'Fuentes: {healthy} al día / {len(warnings)} con aviso de cobertura / {len(errors)} fallidas.',
            f'Registros examinados: {collection.get("fetched", 0)}.',
            f'Procesos nuevos registrados: {collection.get("created", 0)}; actualizados: {collection.get("updated", 0)}.',
        ])
        if "compatible" in collection:
            lines.append(
                f'Clasificación de lo incluido: {collection.get("compatible", 0)} compatibles, '
                f'{collection.get("review", 0)} por revisar, '
                f'{collection.get("not_compatible", 0)} ya registrados que ahora no cumplen el perfil.'
            )
        if collection.get("silent_new"):
            lines.append(
                f'{collection["silent_new"]} proceso(s) nuevo(s) sin aviso individual '
                "(sin plazo de inscripción confirmado): revísalos en /convocatorias."
            )
        if collection.get("pending_notifications"):
            lines.append(f'Avisos en cola antes del envío: {collection["pending_notifications"]}.')
        if no_sources:
            lines.append("No se pudo consultar ninguna fuente: no hay datos para afirmar que no haya novedades.")
        if errors:
            lines.append("Fuentes fallidas (se reintentarán en la próxima búsqueda):")
            lines.extend(f"- {name}: {_brief(detail)}" for name, detail in sorted(errors.items()))
        if warnings:
            lines.append("Avisos de cobertura:")
            lines.extend(f"- {name}: {detail}" for name, detail in sorted(warnings.items()))
    delivery = report.get("delivery")
    if delivery is not None:
        lines.append(f'Avisos de convocatorias enviados: {delivery.get("sent", 0)}; pendientes de reintento: {delivery.get("retryable", 0)}; entrega incierta: {delivery.get("uncertain", 0)}.')
        if delivery.get("suppressed"):
            lines.append(f'Avisos descartados antes de enviar (ya no cumplen el perfil o el plazo): {delivery["suppressed"]}.')
    else:
        lines.append("Entrega de avisos no completada o no registrada.")
    if report.get("started_at"):
        elapsed = max(0, int((datetime.fromisoformat(report["finished_at"]) - datetime.fromisoformat(report["started_at"])).total_seconds()))
        lines.append(f"Duración medida desde la consulta: {elapsed // 60} min {elapsed % 60} s.")
    failed_steps = [name for name, status in report.get("steps", {}).items() if status == "failure"]
    if failed_steps:
        lines.append("Pasos fallidos: " + ", ".join(failed_steps))
    lines.extend(["", "/convocatorias para consultar las fichas; /estado para ver el resultado.", report["url"]])
    text = "\n".join(lines)
    escaped = html.escape(text)
    budget = 3400
    while len(escaped) > 3900 and budget > 200:
        # Telegram rejects messages over 4096 characters.  Cut the raw text
        # (never the escaped one, so an entity is never split) until even the
        # escaped form fits.
        cut = text[:budget].rsplit("\n", 1)[0]
        escaped = html.escape(cut + "\n… (detalle completo en GitHub)\n" + report["url"])
        budget -= 400
    return escaped


def send_report(report: dict) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Faltan secretos de Telegram para el resumen")
    payload = json.dumps({"chat_id": chat_id, "text": render_report(report), "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, headers={"Content-Type": "application/json"}, method="POST")
    # No automatic retry: a lost response could otherwise duplicate the message.
    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
        if not data.get("ok"):
            raise RuntimeError("Telegram rechazo el resumen")
    except Exception:
        raise RuntimeError("No se pudo confirmar la entrega del resumen; consulta GitHub") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--send", action="store_true")
    parser.add_argument(
        "--check-outcome",
        action="store_true",
        help="exit 1 when no source could be consulted (marks the run as failed)",
    )
    args = parser.parse_args()
    path = Path("data/run_metrics.json")
    metrics = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if args.check_outcome:
        outcome = collection_outcome(metrics.get("collection"))
        print(f"Resultado de la consulta: {outcome}")
        raise SystemExit(1 if outcome in {"NO_SOURCES", "MISSING"} else 0)
    report = create_report(metrics, args.status, dict(os.environ))
    if args.send:
        send_report(report)
    else:
        Path("data").mkdir(exist_ok=True)
        Path("data/latest_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render_report(report))
