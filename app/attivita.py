"""Attività: titolo, descrizione, cantiere (facoltativo), persone assegnate, allegati e stato.

Stati, come li usa l'app (ActivityStates.kt): aperta → presa_in_carico (un assegnato ci lavora)
→ chiusa (lavoro fatto, con foto e file). "da_verificare" lo mette il Direttore dei lavori (o la pagina
web) per far ricontrollare: chi l'aveva aggiunge i file e la chiude di nuovo. Chi è assegnato può
prenderla, rilasciarla e chiuderla, ma non se l'ha presa un'altra persona; il DL della squadra del
cantiere (e il superuser dalla pagina web) può mettere qualsiasi stato.

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


STATI = {"aperta": "Aperta", "presa_in_carico": "Presa in carico", "da_verificare": "Da verificare", "chiusa": "Chiusa"}
STATO_INIZIALE = "aperta"
# Passaggi permessi a chi è assegnato all'attività (il DL può tutto).
PASSAGGI_ASSEGNATARIO = {
    ("aperta", "presa_in_carico"), ("presa_in_carico", "aperta"),   # prende in carico / rilascia
    ("presa_in_carico", "chiusa"),                                  # fatto
    ("da_verificare", "chiusa"),                                    # ricontrollata e richiusa
}
# Chi ha fatto la segnalazione dall'app: come chi è assegnato, e in più la riapre quando è chiusa.
PASSAGGI_AUTORE = PASSAGGI_ASSEGNATARIO | {("chiusa", "aperta")}
# Stati in cui conta chi ha preso in carico l'attività.
_CON_INCARICATO = {"presa_in_carico", "da_verificare"}


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
    for r in con.execute(f"""SELECT id, attivita_id, nome, tipo, dimensione, sha256, caricato, persona_id, chi, riferimento
                             FROM attivita_allegati WHERE attivita_id IN ({q}) ORDER BY id""", ids):
        allegati[r["attivita_id"]].append({k: r[k] for k in ("id", "nome", "tipo", "dimensione", "sha256", "caricato",
                                                             "persona_id", "chi", "riferimento")})
    storico: dict[int, list[dict]] = {i: [] for i in ids}
    for r in con.execute(f"""SELECT attivita_id, stato, quando, persona_id, chi, nota FROM attivita_stati
                             WHERE attivita_id IN ({q}) ORDER BY id""", ids):
        storico[r["attivita_id"]].append({"stato": r["stato"], "stato_nome": STATI.get(r["stato"], r["stato"]),
                                          "quando": r["quando"], "persona_id": r["persona_id"],
                                          "chi": r["chi"], "nota": r["nota"]})
    note: dict[int, list[dict]] = {i: [] for i in ids}
    for r in con.execute(f"""SELECT id, attivita_id, quando, persona_id, chi, testo FROM attivita_note
                             WHERE attivita_id IN ({q}) ORDER BY id""", ids):
        note[r["attivita_id"]].append({k: r[k] for k in ("id", "quando", "persona_id", "chi", "testo")})
    return [{"id": r["id"], "titolo": r["titolo"], "descrizione": r["descrizione"],
             "cantiere": ({"id": r["cantiere_id"], "nome": r["cantiere_nome"]} if r["cantiere_id"] else None),
             "appartamento": ({"id": r["appartamento_id"], "nome": r["appartamento_nome"], "id_app": r["appartamento_id_app"]}
                              if r["appartamento_id"] else None),
             "creata": r["creata"], "modificata": r["modificata"],
             "stato": r["stato"], "stato_nome": STATI.get(r["stato"], r["stato"]),
             "stato_modificato": r["stato_modificato"], "storico_stati": storico[r["id"]],
             "in_carico": _incaricato(r["stato"], storico[r["id"]]),
             "note": note[r["id"]],
             "autore": ({"id": r["autore_id"], "nome": r["autore_nome"], "cognome": r["autore_cognome"],
                         "ruolo": r["autore_ruolo"], "ruolo_nome": r["autore_ruolo_nome"]}
                        if r["autore_id"] and r["autore_nome"] is not None else None),
             "assegnatari": persone[r["id"]], "allegati": allegati[r["id"]]} for r in righe]


def _incaricato(stato: str, storico: list[dict]) -> dict | None:
    """Chi ha preso in carico l'attività (l'ultima presa in carico), finché è presa in carico o da verificare."""
    if stato not in _CON_INCARICATO:
        return None
    presa = next((x for x in reversed(storico) if x["stato"] == "presa_in_carico"), None)
    return {k: presa[k] for k in ("persona_id", "chi", "quando")} if presa else None


_SELECT = """SELECT t.*, c.nome AS cantiere_nome, ap.nome AS appartamento_nome, ap.id_app AS appartamento_id_app,
                    au.nome AS autore_nome, au.cognome AS autore_cognome, au.ruolo AS autore_ruolo,
                    aur.nome AS autore_ruolo_nome
             FROM attivita t LEFT JOIN cantieri c ON c.id = t.cantiere_id
             LEFT JOIN appartamenti ap ON ap.id = t.appartamento_id
             LEFT JOIN persone au ON au.id = t.autore_id LEFT JOIN ruoli aur ON aur.sigla = au.ruolo"""


def elenco(persona_id: int | None = None) -> list[dict]:
    """Tutte le attività (più recenti prima), oppure solo quelle assegnate a `persona_id`."""
    with db.connessione() as con:
        if persona_id is None:
            righe = con.execute(_SELECT + " ORDER BY t.id DESC").fetchall()
        else:
            righe = con.execute(_SELECT + """ JOIN attivita_assegnazioni a ON a.attivita_id = t.id
                                    AND a.persona_id = ? ORDER BY t.id DESC""", (persona_id,)).fetchall()
        return _componi(con, righe)


def elenco_per_app(persona: dict) -> list[dict]:
    """Le attività che l'app di una persona riceve: quelle assegnate a lei, le segnalazioni che ha fatto
    e, se è Direttore dei lavori, tutte quelle dei cantieri della sua squadra (deve poterle verificare e chiudere)."""
    with db.connessione() as con:
        righe = con.execute(_SELECT + """
            WHERE t.id IN (SELECT attivita_id FROM attivita_assegnazioni WHERE persona_id = :p)
               OR t.autore_id = :p
               OR (:dl AND t.cantiere_id IN (SELECT cantiere_id FROM squadre WHERE persona_id = :p))
            ORDER BY t.id DESC""", {"p": persona["id"], "dl": persona["ruolo"] == db.RUOLO_DL}).fetchall()
        return _componi(con, righe)


def e_dl_del_cantiere(attivita: dict, persona: dict) -> bool:
    if persona["ruolo"] != db.RUOLO_DL or not attivita["cantiere"]:
        return False
    with db.connessione() as con:
        return con.execute("SELECT 1 FROM squadre WHERE cantiere_id = ? AND persona_id = ?",
                           (attivita["cantiere"]["id"], persona["id"])).fetchone() is not None


def e_autore(attivita: dict, persona: dict) -> bool:
    return bool(attivita.get("autore")) and attivita["autore"]["id"] == persona["id"]


def visibile_a(attivita: dict, persona: dict) -> bool:
    return (any(p["id"] == persona["id"] for p in attivita["assegnatari"]) or e_autore(attivita, persona)
            or e_dl_del_cantiere(attivita, persona))


def aggiungi_nota(id_attivita: int, testo: str, chi: str, persona_id: int | None = None) -> dict | None:
    """Nota libera (senza cambiare stato): resta nella cronologia dell'attività."""
    ora = _ora()
    with db.connessione() as con:
        if not con.execute("UPDATE attivita SET modificata = ? WHERE id = ?", (ora, id_attivita)).rowcount:
            return None
        con.execute("INSERT INTO attivita_note (attivita_id, quando, persona_id, chi, testo) VALUES (?, ?, ?, ?, ?)",
                    (id_attivita, ora, persona_id, chi, testo))
    return leggi(id_attivita)


def cambia_stato(id_attivita: int, stato: str, chi: str, persona_id: int | None = None, nota: str = "") -> dict | None:
    ora = _ora()
    with db.connessione() as con:
        if not con.execute("UPDATE attivita SET stato = ?, stato_modificato = ?, modificata = ? WHERE id = ?",
                           (stato, ora, ora, id_attivita)).rowcount:
            return None
        con.execute("INSERT INTO attivita_stati (attivita_id, stato, quando, persona_id, chi, nota) VALUES (?, ?, ?, ?, ?, ?)",
                    (id_attivita, stato, ora, persona_id, chi, nota))
    return leggi(id_attivita)


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


def crea(titolo: str, descrizione: str, cantiere_id: int | None, persone_ids: list[int],
         appartamento_id: int | None = None, autore_id: int | None = None) -> dict:
    ora = _ora()
    with db.connessione() as con:
        nuovo = con.execute("INSERT INTO attivita (titolo, descrizione, cantiere_id, appartamento_id, creata, modificata, "
                            "autore_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (titolo, descrizione, cantiere_id, appartamento_id, ora, ora, autore_id)).lastrowid
        con.executemany("INSERT OR IGNORE INTO attivita_assegnazioni (attivita_id, persona_id) VALUES (?, ?)",
                        [(nuovo, p) for p in persone_ids])
    return leggi(nuovo)


def modifica(id_attivita: int, titolo: str | None, descrizione: str | None,
             cantiere: tuple[bool, int | None], persone_ids: list[int] | None,
             appartamento: tuple[bool, int | None] = (False, None)) -> dict | None:
    """`cantiere` e `appartamento` = (da cambiare?, nuovo id o None per toglierlo)."""
    with db.connessione() as con:
        if titolo is not None:
            con.execute("UPDATE attivita SET titolo = ? WHERE id = ?", (titolo, id_attivita))
        if descrizione is not None:
            con.execute("UPDATE attivita SET descrizione = ? WHERE id = ?", (descrizione, id_attivita))
        if cantiere[0]:
            con.execute("UPDATE attivita SET cantiere_id = ? WHERE id = ?", (cantiere[1], id_attivita))
        if appartamento[0]:
            con.execute("UPDATE attivita SET appartamento_id = ? WHERE id = ?", (appartamento[1], id_attivita))
        if persone_ids is not None:
            con.execute("DELETE FROM attivita_assegnazioni WHERE attivita_id = ?", (id_attivita,))
            con.executemany("INSERT OR IGNORE INTO attivita_assegnazioni (attivita_id, persona_id) VALUES (?, ?)",
                            [(id_attivita, p) for p in persone_ids])
        con.execute("UPDATE attivita SET modificata = ? WHERE id = ?", (_ora(), id_attivita))
    return leggi(id_attivita)


def registra_allegato(id_attivita: int, nome: str, file_interno: str, dimensione: int, sha256: str,
                      persona_id: int | None = None, chi: str = "Superuser", riferimento: str | None = None) -> dict:
    ora = _ora()
    with db.connessione() as con:
        nuovo = con.execute("""INSERT INTO attivita_allegati
                               (attivita_id, nome, tipo, file, dimensione, sha256, caricato, persona_id, chi, riferimento)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (id_attivita, nome, tipo_da_nome(nome), file_interno, dimensione, sha256, ora,
                             persona_id, chi, riferimento)).lastrowid
        con.execute("UPDATE attivita SET modificata = ? WHERE id = ?", (ora, id_attivita))
    return {"id": nuovo, "nome": nome, "tipo": tipo_da_nome(nome), "dimensione": dimensione,
            "sha256": sha256, "caricato": ora, "persona_id": persona_id, "chi": chi, "riferimento": riferimento}


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
