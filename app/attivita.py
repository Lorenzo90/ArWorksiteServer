"""Attività: titolo, descrizione, cantiere (facoltativo), persone assegnate e allegati.

Gli allegati stanno in <cartella dell'archivio>/attivita/<id attività>/ con un nome interno univoco;
nel database restano il nome originale, il tipo (foto / scansione3d / file), la dimensione e l'impronta.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config, db

ESTENSIONI_FOTO = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp"}
ESTENSIONI_3D = {".glb"}
# File che un browser o Windows potrebbero eseguire: non li accettiamo.
ESTENSIONI_VIETATE = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".scr",
                      ".dll", ".jar", ".html", ".htm", ".svg", ".lnk", ".reg", ".hta"}


def tipo_da_nome(nome: str) -> str:
    est = Path(nome).suffix.lower()
    if est in ESTENSIONI_FOTO:
        return "foto"
    if est in ESTENSIONI_3D:
        return "scansione3d"
    return "file"


def cartella_allegati(id_attivita: int | None = None) -> Path:
    base = Path(config.DB_PATH).parent / "attivita"
    return base if id_attivita is None else base / str(id_attivita)


def _ora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Lettura -----------------------------------------------------------------

def _componi(con: sqlite3.Connection, righe: list[sqlite3.Row]) -> list[dict]:
    if not righe:
        return []
    ids = [r["id"] for r in righe]
    q = ",".join("?" * len(ids))
    persone: dict[int, list[dict]] = {i: [] for i in ids}
    for r in con.execute(f"""
            SELECT a.attivita_id, p.id, p.nome, p.cognome, p.ruolo, ru.nome AS ruolo_nome
            FROM attivita_assegnazioni a JOIN persone p ON p.id = a.persona_id
            LEFT JOIN ruoli ru ON ru.sigla = p.ruolo
            WHERE a.attivita_id IN ({q}) ORDER BY p.cognome, p.nome""", ids):
        persone[r["attivita_id"]].append({k: r[k] for k in ("id", "nome", "cognome", "ruolo", "ruolo_nome")})
    allegati: dict[int, list[dict]] = {i: [] for i in ids}
    for r in con.execute(f"""SELECT id, attivita_id, nome, tipo, dimensione, sha256, caricato
                             FROM attivita_allegati WHERE attivita_id IN ({q}) ORDER BY id""", ids):
        allegati[r["attivita_id"]].append({k: r[k] for k in ("id", "nome", "tipo", "dimensione", "sha256", "caricato")})
    return [{"id": r["id"], "titolo": r["titolo"], "descrizione": r["descrizione"],
             "cantiere": ({"id": r["cantiere_id"], "nome": r["cantiere_nome"]} if r["cantiere_id"] else None),
             "creata": r["creata"], "modificata": r["modificata"],
             "assegnatari": persone[r["id"]], "allegati": allegati[r["id"]]} for r in righe]


_SELECT = """SELECT t.*, c.nome AS cantiere_nome FROM attivita t LEFT JOIN cantieri c ON c.id = t.cantiere_id"""


def elenco(persona_id: int | None = None) -> list[dict]:
    """Tutte le attività (più recenti prima), oppure solo quelle assegnate a `persona_id`."""
    with db.connessione() as con:
        if persona_id is None:
            righe = con.execute(_SELECT + " ORDER BY t.id DESC").fetchall()
        else:
            righe = con.execute(_SELECT + """ JOIN attivita_assegnazioni a ON a.attivita_id = t.id
                                    AND a.persona_id = ? ORDER BY t.id DESC""", (persona_id,)).fetchall()
        return _componi(con, righe)


def leggi(id_attivita: int) -> dict | None:
    with db.connessione() as con:
        righe = con.execute(_SELECT + " WHERE t.id = ?", (id_attivita,)).fetchall()
        return _componi(con, righe)[0] if righe else None


def assegnata_a(id_attivita: int, persona_id: int) -> bool:
    with db.connessione() as con:
        return con.execute("SELECT 1 FROM attivita_assegnazioni WHERE attivita_id = ? AND persona_id = ?",
                           (id_attivita, persona_id)).fetchone() is not None


def allegato(id_attivita: int, id_allegato: int) -> tuple[dict, Path] | None:
    with db.connessione() as con:
        r = con.execute("SELECT * FROM attivita_allegati WHERE id = ? AND attivita_id = ?",
                        (id_allegato, id_attivita)).fetchone()
    if not r:
        return None
    return dict(r), cartella_allegati(id_attivita) / r["file"]


# --- Scrittura ---------------------------------------------------------------

def persone_esistenti(ids: list[int]) -> set[int]:
    if not ids:
        return set()
    with db.connessione() as con:
        q = ",".join("?" * len(ids))
        return {r[0] for r in con.execute(f"SELECT id FROM persone WHERE id IN ({q})", ids)}


def crea(titolo: str, descrizione: str, cantiere_id: int | None, persone_ids: list[int]) -> dict:
    ora = _ora()
    with db.connessione() as con:
        nuovo = con.execute("INSERT INTO attivita (titolo, descrizione, cantiere_id, creata, modificata) "
                            "VALUES (?, ?, ?, ?, ?)", (titolo, descrizione, cantiere_id, ora, ora)).lastrowid
        con.executemany("INSERT OR IGNORE INTO attivita_assegnazioni (attivita_id, persona_id) VALUES (?, ?)",
                        [(nuovo, p) for p in persone_ids])
    return leggi(nuovo)


def modifica(id_attivita: int, titolo: str | None, descrizione: str | None,
             cantiere: tuple[bool, int | None], persone_ids: list[int] | None) -> dict | None:
    """`cantiere` = (da cambiare?, nuovo id o None per toglierlo)."""
    with db.connessione() as con:
        if titolo is not None:
            con.execute("UPDATE attivita SET titolo = ? WHERE id = ?", (titolo, id_attivita))
        if descrizione is not None:
            con.execute("UPDATE attivita SET descrizione = ? WHERE id = ?", (descrizione, id_attivita))
        if cantiere[0]:
            con.execute("UPDATE attivita SET cantiere_id = ? WHERE id = ?", (cantiere[1], id_attivita))
        if persone_ids is not None:
            con.execute("DELETE FROM attivita_assegnazioni WHERE attivita_id = ?", (id_attivita,))
            con.executemany("INSERT OR IGNORE INTO attivita_assegnazioni (attivita_id, persona_id) VALUES (?, ?)",
                            [(id_attivita, p) for p in persone_ids])
        con.execute("UPDATE attivita SET modificata = ? WHERE id = ?", (_ora(), id_attivita))
    return leggi(id_attivita)


def registra_allegato(id_attivita: int, nome: str, file_interno: str, dimensione: int, sha256: str) -> dict:
    ora = _ora()
    with db.connessione() as con:
        nuovo = con.execute("""INSERT INTO attivita_allegati (attivita_id, nome, tipo, file, dimensione, sha256, caricato)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (id_attivita, nome, tipo_da_nome(nome), file_interno, dimensione, sha256, ora)).lastrowid
        con.execute("UPDATE attivita SET modificata = ? WHERE id = ?", (ora, id_attivita))
    return {"id": nuovo, "nome": nome, "tipo": tipo_da_nome(nome), "dimensione": dimensione,
            "sha256": sha256, "caricato": ora}


def elimina_allegato(id_attivita: int, id_allegato: int) -> bool:
    trovato = allegato(id_attivita, id_allegato)
    if not trovato:
        return False
    with db.connessione() as con:
        con.execute("DELETE FROM attivita_allegati WHERE id = ?", (id_allegato,))
        con.execute("UPDATE attivita SET modificata = ? WHERE id = ?", (_ora(), id_attivita))
    trovato[1].unlink(missing_ok=True)
    return True


def elimina(id_attivita: int) -> bool:
    with db.connessione() as con:
        ok = con.execute("DELETE FROM attivita WHERE id = ?", (id_attivita,)).rowcount > 0
    if ok:
        shutil.rmtree(cartella_allegati(id_attivita), ignore_errors=True)
    return ok
