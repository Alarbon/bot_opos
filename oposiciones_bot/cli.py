from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from dataclasses import asdict
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .config import load_config
from .catalog import build_catalog, export_catalog
from .db import SQLiteStore
from .http import HttpClient, HttpSettings
from .models import OutboxStatus
from .pipeline import collect, dispatch
from .sources import build_sources
from .telegram import TelegramClient, TelegramSettings, render_event


LOGGER = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor de oposiciones de informatica")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="consultar fuentes y gestionar avisos")
    run.add_argument("--config", default="config.yaml")
    run.add_argument("--dry-run", action="store_true", help="simula sin cambiar DB ni Telegram")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--collect-only", action="store_true")
    mode.add_argument("--dispatch-only", action="store_true")
    run.add_argument("--source", action="append", default=[])
    run.add_argument("--today", help="fecha ISO para pruebas (AAAA-MM-DD)")
    run.add_argument("--limit", type=int, default=20, help="maximo de avisos por envio")
    run.add_argument("--log-level", default="INFO")
    run.add_argument("--metrics-file")
    catalog = subparsers.add_parser("catalog", help="exportar y gestionar seguimientos")
    catalog.add_argument("--config", default="config.yaml")
    catalog.add_argument("--operation", choices=["export", "follow", "unfollow"], default="export")
    catalog.add_argument("--process", default="")
    catalog.add_argument("--output", default="data/catalog.json")

    outbox = subparsers.add_parser("outbox", help="operar la cola transaccional")
    outbox.add_argument("--config", default="config.yaml")
    outbox.add_argument("--log-level", default="INFO")
    outbox_sub = outbox.add_subparsers(dest="outbox_command", required=True)
    outbox_list = outbox_sub.add_parser("list")
    outbox_list.add_argument("--status", action="append", default=[])
    outbox_claim = outbox_sub.add_parser("claim")
    outbox_claim.add_argument("--limit", type=int, default=20)
    outbox_claim.add_argument("--batch-file", required=True)
    outbox_dispatch = outbox_sub.add_parser("dispatch")
    outbox_dispatch.add_argument("--batch-file", required=True)
    outbox_dispatch.add_argument("--metrics-file")
    outbox_retry = outbox_sub.add_parser("retry")
    outbox_retry.add_argument("event_id")
    outbox_suppress = outbox_sub.add_parser("suppress")
    outbox_suppress.add_argument("event_id")

    database = subparsers.add_parser("db", help="diagnostico de la base")
    database.add_argument("--config", default="config.yaml")
    database.add_argument("--log-level", default="INFO")
    database.add_argument("action", choices=["check", "stats"])
    return parser


def _telegram(config) -> TelegramClient | None:
    if not bool(config.get("notifications.telegram", True)):
        return None
    policy = str(config.get("notifications.delivery_policy", "at_most_once"))
    if policy != "at_most_once":
        raise ValueError(f"Politica de entrega no soportada: {policy!r}")
    settings = TelegramSettings.from_environment(
        parse_mode=str(config.get("notifications.parse_mode", "HTML")),
        disable_web_page_preview=bool(
            config.get("notifications.disable_web_page_preview", True)
        ),
    )
    return TelegramClient(settings) if settings else None


def _run(args: argparse.Namespace) -> int:
    started_at = datetime.now(ZoneInfo("Europe/Madrid")).isoformat()
    config = load_config(args.config)
    today = (
        date.fromisoformat(args.today)
        if args.today
        else datetime.now(ZoneInfo(str(config.get("timezone", "Europe/Madrid")))).date()
    )
    store = (
        SQLiteStore.dry_run_copy(config.database_path)
        if args.dry_run
        else SQLiteStore(config.database_path)
    )
    try:
        recovered = store.recover_stale_sending()
        if recovered:
            LOGGER.warning("%s envios interrumpidos pasan a UNCERTAIN", recovered)
        if not args.dispatch_only:
            settings = HttpSettings(
                timeout=int(config.get("collection.request_timeout_seconds", 25)),
                user_agent=str(config.get("collection.user_agent", "oposiciones-bot/0.1")),
                max_bytes=int(config.get("collection.max_document_bytes", 15_000_000)),
            )
            http = HttpClient(settings)
            try:
                sources = build_sources(
                    config,
                    http,
                    selected=set(args.source) if args.source else None,
                )
                collection = collect(
                    config=config,
                    store=store,
                    client=http,
                    sources=sources,
                    today=today,
                )
                if args.metrics_file and not args.dry_run:
                    _save_metrics(args.metrics_file, {"started_at": started_at, "collection": asdict(collection)})
                LOGGER.info(
                    "Recogidas=%s incluidas=%s nuevas=%s actualizadas=%s sin_cambios=%s filtradas=%s recordatorios=%s errores=%s",
                    collection.fetched,
                    collection.included,
                    collection.created,
                    collection.updated,
                    collection.unchanged,
                    collection.filtered,
                    collection.reminders,
                    len(collection.errors),
                )
            finally:
                http.close()

        if args.dry_run:
            pending = store.list_outbox(
                [OutboxStatus.PENDING.value, OutboxStatus.RETRYABLE.value]
            )
            print(f"DRY-RUN: {len(pending)} aviso(s) se enviarian; no se ha modificado el disco.")
            for event in pending[: args.limit]:
                print("\n" + render_event(event) + "\n" + ("-" * 60))
            return 0
        if args.collect_only:
            return 0

        telegram = _telegram(config)
        if telegram is None:
            LOGGER.warning(
                "Faltan TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID; los avisos quedan PENDING"
            )
            return 0 if not args.dispatch_only else 2
        sent = dispatch(store=store, telegram=telegram, limit=args.limit, config=config)
        LOGGER.info(
            "Outbox reclamados=%s enviados=%s reintentables=%s inciertos=%s",
            sent.claimed,
            sent.sent,
            sent.retryable,
            sent.uncertain,
        )
        return 0 if not sent.uncertain else 3
    finally:
        store.close()


def _outbox(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = SQLiteStore(config.database_path)
    try:
        if args.outbox_command == "list":
            rows = store.list_outbox(args.status or None)
            for row in rows:
                print(
                    f"{row['event_id']} {row['status']:<10} {row['kind']:<10} "
                    f"intentos={row['attempts']} error={row['last_error'] or '-'}"
                )
            return 0
        if args.outbox_command == "claim":
            ids = store.claim_outbox(limit=args.limit)
            batch_path = Path(args.batch_file)
            try:
                batch_path.parent.mkdir(parents=True, exist_ok=True)
                batch_path.write_text(json.dumps(ids, indent=2), encoding="utf-8")
            except Exception:
                store.release_unsent_claims(
                    ids,
                    error="No se pudo persistir el lote; no se llamo a Telegram",
                )
                raise
            print(f"Reclamados {len(ids)} evento(s) en {batch_path}")
            return 0
        if args.outbox_command == "dispatch":
            ids = json.loads(Path(args.batch_file).read_text(encoding="utf-8"))
            if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
                raise ValueError("El batch de outbox no contiene una lista valida")
            telegram = _telegram(config)
            if telegram is None:
                store.release_unsent_claims(
                    ids,
                    error="Faltan credenciales; no se llamo a Telegram",
                )
                LOGGER.error("Faltan TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")
                return 2
            result = dispatch(store=store, telegram=telegram, event_ids=ids, config=config)
            if args.metrics_file:
                _save_metrics(args.metrics_file, {"delivery": asdict(result)})
            print(
                f"Enviados={result.sent} reintentables={result.retryable} inciertos={result.uncertain}"
            )
            return 0 if not result.uncertain else 3
        if args.outbox_command == "retry":
            return 0 if store.retry_event(args.event_id) else 1
        if args.outbox_command == "suppress":
            return 0 if store.suppress_event(args.event_id) else 1
        return 2
    finally:
        store.close()


def _database(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    store = SQLiteStore(config.database_path)
    try:
        if args.action == "check":
            result = store.integrity_check()
            print(result)
            return 0 if result == "ok" else 1
        print(json.dumps(store.stats(), indent=2, ensure_ascii=False))
        return 0
    finally:
        store.close()


def _save_metrics(path: str, values: dict) -> None:
    target = Path(path)
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    data.update(values)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    # Windows puede heredar una consola cp1252; los mensajes contienen tildes y emoji.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0].startswith("-"):
        values.insert(0, "run")
    parser = _parser()
    args = parser.parse_args(values)
    _configure_logging(getattr(args, "log_level", "INFO"))
    if args.command == "run":
        return _run(args)
    if args.command == "outbox":
        return _outbox(args)
    if args.command == "db":
        return _database(args)
    if args.command == "catalog":
        config = load_config(args.config)
        today = datetime.now(ZoneInfo(config.get("timezone", "Europe/Madrid"))).date()
        store = SQLiteStore(config.database_path)
        try:
            if args.operation != "export":
                matches = [p for p in build_catalog(store, config, today)["processes"] if p["id"].startswith(args.process)]
                if not args.process or len(matches) != 1:
                    raise ValueError("ID no encontrado o ambiguo en el catalogo informatico")
                store.follow(matches[0]["id"], args.operation == "follow")
            export_catalog(store, config, today, Path(args.output))
            return 0
        finally:
            store.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
