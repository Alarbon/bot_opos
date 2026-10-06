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


def render_report(report: dict) -> str:
    failed = report["status"] != "success"
    collection = report.get("collection")
    errors = (collection or {}).get("errors", {})
    warnings = (collection or {}).get("warnings", {})
    delivery_problem = any((report.get("delivery") or {}).get(key, 0) for key in ("retryable", "uncertain"))
    heading = "❌ BÚSQUEDA FALLIDA" if failed else "⚠️ BÚSQUEDA TERMINADA CON COBERTURA INCOMPLETA" if errors else "⚠️ BÚSQUEDA COMPLETADA CON AVISOS DE COBERTURA" if warnings else "⚠️ BÚSQUEDA TERMINADA CON AVISOS PENDIENTES" if delivery_problem else "✅ BÚSQUEDA COMPLETADA"
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
        if errors:
            lines.append("Fuentes pendientes: " + ", ".join(sorted(errors)))
        if warnings:
            lines.append("Avisos de cobertura:")
            lines.extend(f"- {name}: {detail}" for name, detail in sorted(warnings.items()))
    delivery = report.get("delivery")
    if delivery is not None:
        lines.append(f'Avisos de convocatorias enviados: {delivery.get("sent", 0)}; pendientes de reintento: {delivery.get("retryable", 0)}; entrega incierta: {delivery.get("uncertain", 0)}.')
    else:
        lines.append("Entrega de avisos no completada o no registrada.")
    if report.get("started_at"):
        elapsed = max(0, int((datetime.fromisoformat(report["finished_at"]) - datetime.fromisoformat(report["started_at"])).total_seconds()))
        lines.append(f"Duración medida desde la consulta: {elapsed // 60} min {elapsed % 60} s.")
    failed_steps = [name for name, status in report.get("steps", {}).items() if status == "failure"]
    if failed_steps:
        lines.append("Pasos fallidos: " + ", ".join(failed_steps))
    lines.extend(["", "/convocatorias para consultar las fichas; /estado para ver el resultado.", report["url"]])
    return html.escape("\n".join(lines))


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
    args = parser.parse_args()
    path = Path("data/run_metrics.json")
    metrics = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    report = create_report(metrics, args.status, dict(os.environ))
    if args.send:
        send_report(report)
    else:
        Path("data").mkdir(exist_ok=True)
        Path("data/latest_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render_report(report))
