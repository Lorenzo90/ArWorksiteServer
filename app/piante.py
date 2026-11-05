"""Catalogo delle piante DXF/DWG lette dalla cartella PIANTE_DIR."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Pianta:
    id: str
    nome: str
    cantiere: str | None
    formato: str
    dimensione: int
    modificato: str
    sha256: str
    percorso: Path


_hash_cache: dict[Path, tuple[int, int, str]] = {}
_lock = threading.Lock()


def _id_da_percorso(rel: str) -> str:
    """Id stabile e sicuro per gli URL, ricavato dal percorso relativo."""
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]


def _sha256(path: Path, size: int, mtime_ns: int) -> str:
    with _lock:
        cached = _hash_cache.get(path)
        if cached and cached[0] == size and cached[1] == mtime_ns:
            return cached[2]
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    digest = h.hexdigest()
    with _lock:
        _hash_cache[path] = (size, mtime_ns, digest)
    return digest


def elenco(cartella: Path | None = None) -> list[Pianta]:
    root = (cartella or config.PIANTE_DIR)
    if not root.is_dir():
        return []
    risultati: list[Pianta] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in config.ESTENSIONI_PIANTE:
            continue
        if any(parte.startswith(".") for parte in p.relative_to(root).parts):
            continue
        st = p.stat()
        rel = p.relative_to(root).as_posix()
        parti = p.relative_to(root).parts
        risultati.append(
            Pianta(
                id=_id_da_percorso(rel),
                nome=p.name,
                cantiere="/".join(parti[:-1]) or None,
                formato=p.suffix.lower().lstrip("."),
                dimensione=st.st_size,
                modificato=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
                sha256=_sha256(p, st.st_size, st.st_mtime_ns),
                percorso=p,
            )
        )
    return risultati


def trova(id_pianta: str) -> Pianta | None:
    return next((p for p in elenco() if p.id == id_pianta), None)
