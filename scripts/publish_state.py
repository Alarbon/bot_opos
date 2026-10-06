"""Publish generated state; rebase only over changes that do not touch state."""
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

STATE_PATHS = ("data/oposiciones.db", "data/catalog.json", "data/latest_run.json")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"Git fallo en {args[0]} (codigo {result.returncode})")
    return result


def publish(message: str, attempts: int = 3) -> None:
    git("config", "user.name", "oposiciones-bot")
    git("config", "user.email", "actions@github.com")
    paths = [p for p in STATE_PATHS if Path(p).exists()]
    if not paths:
        raise RuntimeError("No hay estado que publicar")
    git("add", "--", *paths)
    if git("diff", "--cached", "--quiet", check=False).returncode:
        git("commit", "-m", message)
    # Never rebase unrelated uncommitted edits.
    if git("diff", "--quiet", check=False).returncode:
        raise RuntimeError("Hay cambios locales sin guardar; se evita el rebase")
    branch = git("branch", "--show-current").stdout.strip()
    if not branch:
        raise RuntimeError("Se requiere una rama, no detached HEAD")
    for attempt in range(attempts):
        if git("push", "origin", f"HEAD:refs/heads/{branch}", check=False).returncode == 0:
            return
        git("fetch", "origin", branch)
        remote = "FETCH_HEAD"
        if git("merge-base", "--is-ancestor", "HEAD", remote, check=False).returncode == 0:
            # The commit was already accepted, even if the push response was lost.
            return
        base = git("merge-base", "HEAD", remote).stdout.strip()
        changed = set(git("diff", "--name-only", base, remote).stdout.splitlines())
        if changed.intersection(STATE_PATHS):
            raise RuntimeError("El estado remoto cambio: no se sobrescribe ni se fuerza el push. Revisa la ejecucion y vuelve a buscar.")
        result = git("rebase", remote, check=False)
        if result.returncode:
            git("rebase", "--abort", check=False)
            raise RuntimeError("No se pudo integrar el codigo remoto de forma segura")
        if attempt + 1 < attempts:
            time.sleep(attempt + 1)
    raise RuntimeError("No se pudo publicar tras tres intentos; no se enviaran avisos sin guardar su estado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    publish(args.message)
