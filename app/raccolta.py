"""Dati raccolti dal telefono in un appartamento (la cartella dell'app pareti/<DWG>/<APP n>/).

Ogni file è identificato dal suo percorso relativo alla cartella dell'appartamento, come nell'app:
"Soggiorno/Parete 2/1/wall.jpg", "Soggiorno/Parete 2/1/metadata.json", "Soggiorno/report.json",
"Bagno/Parete 1/foto/IMG_20261108_101500.jpg", "appartamento.json"... Sul server il contenuto sta in
<cartella dell'archivio>/raccolta/<id appartamento>/<nome interno>; nel database percorso, impronta e autore.

Chi può fare cosa:
- leggere: squadra del cantiere, committenti dell'appartamento, superuser;
- caricare: squadra del cantiere (e superuser). Un file caricato da un'altra persona non si sovrascrive
  senza `sostituisci` (due telefoni con la stessa scansione "Parete 2/1/");
- eliminare: chi l'ha caricato, il Direttore dei lavori della squadra, il superuser.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from . import attivita as att
from . import config, db

_VIETATI = re.compile(r'[\\:*?"<>|\x00-\x1f\x7f]')
MAX_PARTI = 10
MAX_LUNGHEZZA = 400


class PercorsoNonValido(ValueError):
    pass


def _ora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cartella(id_appartamento: int) -> Path:
    return Path(config.DB_PATH).parent / "raccolta" / str(id_appartamento)


def normalizza(percorso: str) -> str:
    """Percorso relativo "a/b/c.jpg" valido anche su Windows; altrimenti PercorsoNonValido."""
    percorso = (percorso or "").replace("\\", "/").strip("/")
    parti = percorso.split("/")
    if not percorso or len(parti) > MAX_PARTI or len(percorso) > MAX_LUNGHEZZA:
        raise PercorsoNonValido("Percorso vuoto o troppo lungo")
    for p in parti:
        if p in ("", ".", "..") or p != p.strip() or p.endswith(".") or _VIETATI.search(p):
            raise PercorsoNonValido(f"Nome non valido nel percorso: «{p}»")
        if p.startswith("."):
            raise PercorsoNonValido("I file e le cartelle nascosti (.nome) non si caricano")
    if Path(parti[-1]).suffix.lower() in att.ESTENSIONI_VIETATE:
        raise PercorsoNonValido(f"{parti[-1]}: questo tipo di file non è ammesso")
    return "/".join(parti)


# --- Permessi ----------------------------------------------------------------

def _info_appartamento(id_appartamento: int) -> dict | None:
    with db.connessione() as con:
        r = con.execute("SELECT id, nome, id_app, cantiere_id FROM appartamenti WHERE id = ?",
                        (id_appartamento,)).fetchone()
    return dict(r) if r else None


def accesso(id_appartamento: int, persona: dict | None) -> dict | None:
    """{"appartamento", "leggere", "caricare", "dl"} oppure None se l'appartamento non esiste.
    `persona` None = superuser (può tutto)."""
    ap = _info_appartamento(id_appartamento)
    if not ap:
        return None
    if persona is None:
        return {"appartamento": ap, "leggere": True, "caricare": True, "dl": True}
    with db.connessione() as con:
        in_squadra = con.execute("SELECT 1 FROM squadre WHERE cantiere_id = ? AND persona_id = ?",
                                 (ap["cantiere_id"], persona["id"])).fetchone() is not None
        committente = con.execute("SELECT 1 FROM appartamento_committenti WHERE appartamento_id = ? AND persona_id = ?",
                                  (id_appartamento, persona["id"])).fetchone() is not None
    return {"appartamento": ap, "leggere": in_squadra or committente, "caricare": in_squadra,
            "dl": in_squadra and persona["ruolo"] == db.RUOLO_DL}


# --- Lettura -----------------------------------------------------------------

def elenco(id_appartamento: int) -> list[dict]:
    with db.connessione() as con:
        righe = con.execute("""SELECT percorso, file, dimensione, sha256, modificato_dispositivo, caricato, persona_id, chi
                               FROM raccolta WHERE appartamento_id = ? ORDER BY percorso COLLATE NOCASE""",
                            (id_appartamento,)).fetchall()
    return [dict(r) for r in righe]


def leggi(id_appartamento: int, percorso: str) -> dict | None:
    with db.connessione() as con:
        r = con.execute("SELECT * FROM raccolta WHERE appartamento_id = ? AND percorso = ? COLLATE NOCASE",
                        (id_appartamento, percorso)).fetchone()
    return dict(r) if r else None


def riepilogo(ids_appartamenti: list[int]) -> dict[int, dict]:
    """{id appartamento: {n_file, dimensione, aggiornato}}"""
    risultato = {i: {"n_file": 0, "dimensione": 0, "aggiornato": None} for i in ids_appartamenti}
    if not ids_appartamenti:
        return risultato
    q = ",".join("?" * len(ids_appartamenti))
    with db.connessione() as con:
        for r in con.execute(f"""SELECT appartamento_id, COUNT(*), COALESCE(SUM(dimensione), 0), MAX(caricato)
                                 FROM raccolta WHERE appartamento_id IN ({q}) GROUP BY appartamento_id""",
                             ids_appartamenti):
            risultato[r[0]] = {"n_file": r[1], "dimensione": r[2], "aggiornato": r[3]}
    return risultato


def versione(file: list[dict]) -> str:
    testo = "\n".join(f'{f["percorso"]}|{f["sha256"]}|{f["persona_id"]}' for f in file)
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()[:32]


# --- Scrittura ---------------------------------------------------------------

def registra(id_appartamento: int, percorso: str, interno: str, dimensione: int, sha256: str,
             modificato_dispositivo: str | None, persona: dict | None) -> tuple[dict, str | None]:
    """Inserisce o sostituisce. Restituisce (riga, file interno precedente da cancellare)."""
    chi = f'{persona["nome"]} {persona["cognome"]}' if persona else "Superuser"
    pid = persona["id"] if persona else None
    precedente = leggi(id_appartamento, percorso)
    with db.connessione() as con:
        if precedente:
            con.execute("""UPDATE raccolta SET file = ?, dimensione = ?, sha256 = ?, modificato_dispositivo = ?,
                           caricato = ?, persona_id = ?, chi = ? WHERE id = ?""",
                        (interno, dimensione, sha256, modificato_dispositivo, _ora(), pid, chi, precedente["id"]))
        else:
            con.execute("""INSERT INTO raccolta (appartamento_id, percorso, file, dimensione, sha256,
                           modificato_dispositivo, caricato, persona_id, chi) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (id_appartamento, percorso, interno, dimensione, sha256, modificato_dispositivo, _ora(), pid, chi))
    return leggi(id_appartamento, percorso), (precedente["file"] if precedente else None)


def elimina(id_appartamento: int, percorso: str) -> bool:
    r = leggi(id_appartamento, percorso)
    if not r:
        return False
    with db.connessione() as con:
        con.execute("DELETE FROM raccolta WHERE id = ?", (r["id"],))
    (cartella(id_appartamento) / r["file"]).unlink(missing_ok=True)
    return True
