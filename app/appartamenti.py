"""Appartamenti dei cantieri e loro committenti.

Gli appartamenti nascono dalle piante DXF caricate nella cartella del cantiere (data/piante/<cartella>/):
ogni etichetta "u.a.03", "App. 3"… diventa "APP 3", con lo stesso id che usa l'app
("dwg:<nome pianta>/APP 3"). Si possono anche aggiungere a mano (per esempio per una pianta DWG).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, dxf, piante

log = logging.getLogger("arworksite")

RUOLO_COMMITTENTE = "CL"
# Ogni planimetria ha il suo "Spazio comune": un'attività lì vale per tutta la planimetria.
# Un cantiere senza planimetrie ne ha uno generico (pianta NULL), tolto quando arriva una planimetria se è vuoto.
SPAZIO_COMUNE = "Spazio comune"
COMMITTENTE_ESEMPIO = ("Lorenzo", "Orlandi")
_NOME_NON_VALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _ora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def nome_cartella(nome: str) -> str:
    """Nome della cartella delle piante: stesso criterio del caricamento piante."""
    nome = _NOME_NON_VALIDO.sub("_", " ".join((nome or "").split())).strip().strip(".")
    return nome[:150] or "_"


# --- Cantiere e cartella delle piante ----------------------------------------

def cartella_di(id_cantiere: int) -> str | None:
    with db.connessione() as con:
        r = con.execute("SELECT COALESCE(cartella_piante, nome) FROM cantieri WHERE id = ?", (id_cantiere,)).fetchone()
    return nome_cartella(r[0]) if r else None


def piante_del_cantiere(id_cantiere: int) -> list[piante.Pianta]:
    cartella = cartella_di(id_cantiere)
    if cartella is None:
        return []
    return [p for p in piante.elenco() if (p.cantiere or "").lower() == cartella.lower()]


def cantiere_della_cartella(cartella: str) -> int | None:
    with db.connessione() as con:
        for r in con.execute("SELECT id, COALESCE(cartella_piante, nome) FROM cantieri"):
            if nome_cartella(r[1]).lower() == nome_cartella(cartella).lower():
                return r[0]
    return None


# --- Sincronizzazione dalle piante -------------------------------------------

def sincronizza(id_cantiere: int) -> dict:
    """Legge le piante DXF del cantiere e aggiunge gli appartamenti nuovi (non cancella mai nulla).
    Restituisce {"nuovi": n, "piante_lette": [...], "piante_non_lette": [{nome, motivo}]}."""
    nuovi, lette, non_lette = _spazi_comuni(id_cantiere), [], []
    for p in piante_del_cantiere(id_cantiere):
        if p.formato != "dxf":
            non_lette.append({"nome": p.nome, "motivo": "DWG: aggiungi gli appartamenti a mano o carica il DXF"})
            continue
        try:
            trovati = dxf.appartamenti(p.percorso)
        except (OSError, dxf.DxfNonLeggibile) as e:
            non_lette.append({"nome": p.nome, "motivo": str(e)})
            continue
        if not trovati:
            non_lette.append({"nome": p.nome, "motivo": "nessuna etichetta di appartamento (u.a.01, App. 1…)"})
            continue
        lette.append(p.nome)
        with db.connessione() as con:
            for a in trovati:
                nuovi += con.execute(
                    """INSERT OR IGNORE INTO appartamenti
                       (cantiere_id, nome, numero, etichetta, pianta, id_app, origine, creato)
                       VALUES (?, ?, ?, ?, ?, ?, 'pianta', ?)""",
                    (id_cantiere, a["nome"], a["numero"], a["etichetta"], p.nome,
                     dxf.id_app(p.nome, a["nome"]), _ora())).rowcount
    return {"nuovi": nuovi, "piante_lette": lette, "piante_non_lette": non_lette}


def _spazi_comuni(id_cantiere: int) -> int:
    pi = piante_del_cantiere(id_cantiere)
    nuovi = 0
    with db.connessione() as con:
        for p in pi:
            nuovi += con.execute(
                """INSERT OR IGNORE INTO appartamenti (cantiere_id, nome, pianta, id_app, origine, creato)
                   VALUES (?, ?, ?, ?, 'comune', ?)""",
                (id_cantiere, SPAZIO_COMUNE, p.nome, dxf.id_app(p.nome, SPAZIO_COMUNE), _ora())).rowcount
        if not pi:
            nuovi += con.execute(
                """INSERT OR IGNORE INTO appartamenti (cantiere_id, nome, origine, creato)
                   VALUES (?, ?, 'comune', ?)""", (id_cantiere, SPAZIO_COMUNE, _ora())).rowcount
        else:
            # arrivata la prima planimetria: lo spazio comune generico sparisce se non è usato
            con.execute("""DELETE FROM appartamenti WHERE cantiere_id = ? AND origine = 'comune' AND pianta IS NULL
                           AND id NOT IN (SELECT appartamento_id FROM attivita WHERE appartamento_id IS NOT NULL)
                           AND id NOT IN (SELECT appartamento_id FROM raccolta)
                           AND id NOT IN (SELECT appartamento_id FROM appartamento_committenti)""", (id_cantiere,))
    return nuovi


def assicura_appartamento(id_cantiere: int, nome_pianta: str) -> None:
    """Pianta caricata dall'app iPad (RoomPlan): contiene un solo appartamento. Se la lettura del DXF non ne ha
    trovato nessuno (manca il testo «App. N», oppure è un DWG) si crea APP 1, così l'app ha subito l'id
    ("dwg:<nome pianta>/APP 1") per i dati raccolti."""
    with db.connessione() as con:
        if con.execute("""SELECT 1 FROM appartamenti WHERE cantiere_id = ? AND origine = 'pianta'
                          AND pianta = ? COLLATE NOCASE""", (id_cantiere, nome_pianta)).fetchone():
            return
        con.execute("""INSERT OR IGNORE INTO appartamenti
                       (cantiere_id, nome, numero, etichetta, pianta, id_app, origine, creato)
                       VALUES (?, 'APP 1', 1, NULL, ?, ?, 'pianta', ?)""",
                    (id_cantiere, nome_pianta, dxf.id_app(nome_pianta, "APP 1"), _ora()))


def spazio_comune(id_cantiere: int) -> int | None:
    """Lo spazio comune della prima planimetria del cantiere (o quello generico)."""
    with db.connessione() as con:
        r = con.execute("""SELECT id FROM appartamenti WHERE cantiere_id = ? AND origine = 'comune'
                           ORDER BY pianta IS NULL, pianta LIMIT 1""", (id_cantiere,)).fetchone()
    return r[0] if r else None


def sincronizza_cartella(cartella: str | None) -> None:
    """Dopo un caricamento dalla pagina Piante: aggiorna il cantiere che usa quella cartella, se c'è."""
    if cartella:
        cid = cantiere_della_cartella(cartella)
        if cid is not None:
            sincronizza(cid)


# --- Lettura -----------------------------------------------------------------

def elenco(id_cantiere: int) -> list[dict]:
    with db.connessione() as con:
        righe = con.execute("""
            SELECT a.*, (SELECT COUNT(*) FROM attivita t WHERE t.appartamento_id = a.id) AS n_attivita
            FROM appartamenti a WHERE a.cantiere_id = ?
            ORDER BY COALESCE(a.pianta, '~'), a.origine <> 'comune', COALESCE(a.numero, 1e9), a.nome""",
            (id_cantiere,)).fetchall()
        ids = [r["id"] for r in righe]
        committenti: dict[int, list[dict]] = {i: [] for i in ids}
        if ids:
            q = ",".join("?" * len(ids))
            for r in con.execute(f"""
                    SELECT c.appartamento_id, p.id, p.nome, p.cognome, p.ruolo, ru.nome AS ruolo_nome
                    FROM appartamento_committenti c JOIN persone p ON p.id = c.persona_id
                    LEFT JOIN ruoli ru ON ru.sigla = p.ruolo
                    WHERE c.appartamento_id IN ({q}) ORDER BY p.cognome, p.nome""", ids):
                committenti[r["appartamento_id"]].append(
                    {k: r[k] for k in ("id", "nome", "cognome", "ruolo", "ruolo_nome")})
    from . import raccolta  # qui per evitare un giro di import
    riepilogo = raccolta.riepilogo(ids)
    return [{"id": r["id"], "nome": r["nome"], "numero": r["numero"], "etichetta": r["etichetta"],
             "pianta": r["pianta"], "id_app": r["id_app"], "origine": r["origine"],
             "committenti": committenti[r["id"]], "n_attivita": r["n_attivita"],
             "raccolta": riepilogo[r["id"]]} for r in righe]


def leggi(id_appartamento: int) -> dict | None:
    with db.connessione() as con:
        r = con.execute("SELECT cantiere_id FROM appartamenti WHERE id = ?", (id_appartamento,)).fetchone()
    if not r:
        return None
    return next((a | {"cantiere_id": r[0]} for a in elenco(r[0]) if a["id"] == id_appartamento), None)


# --- Scrittura ---------------------------------------------------------------

def crea_manuale(id_cantiere: int, nome: str) -> dict:
    m = re.match(r"(?i)^\s*app\s*0*(\d+)\s*$", nome)
    with db.connessione() as con:
        nuovo = con.execute("""INSERT INTO appartamenti (cantiere_id, nome, numero, origine, creato)
                               VALUES (?, ?, ?, 'manuale', ?)""",
                            (id_cantiere, nome, int(m.group(1)) if m else None, _ora())).lastrowid
    return leggi(nuovo)


def elimina(id_appartamento: int) -> bool:
    import shutil
    from . import raccolta
    with db.connessione() as con:
        ok = con.execute("DELETE FROM appartamenti WHERE id = ?", (id_appartamento,)).rowcount > 0
    if ok:
        shutil.rmtree(raccolta.cartella(id_appartamento), ignore_errors=True)
    return ok


def aggiungi_committente(id_appartamento: int, id_persona: int) -> bool:
    with db.connessione() as con:
        return con.execute("INSERT OR IGNORE INTO appartamento_committenti (appartamento_id, persona_id) VALUES (?, ?)",
                           (id_appartamento, id_persona)).rowcount > 0


def togli_committente(id_appartamento: int, id_persona: int) -> bool:
    with db.connessione() as con:
        return con.execute("DELETE FROM appartamento_committenti WHERE appartamento_id = ? AND persona_id = ?",
                           (id_appartamento, id_persona)).rowcount > 0


# --- Avvio -------------------------------------------------------------------

def _impostazione(chiave: str) -> str | None:
    with db.connessione() as con:
        r = con.execute("SELECT valore FROM impostazioni WHERE chiave = ?", (chiave,)).fetchone()
    return r[0] if r else None


def _imposta(chiave: str, valore: str) -> None:
    with db.connessione() as con:
        con.execute("INSERT OR REPLACE INTO impostazioni (chiave, valore) VALUES (?, ?)", (chiave, valore))


def _committente_esempio() -> None:
    """Una sola volta: Lorenzo Orlandi, committente dell'APP 3 di Lungo Avisio (o del primo appartamento)."""
    if _impostazione("esempio_committente"):
        return
    nome, cognome = COMMITTENTE_ESEMPIO
    persona = next((p for p in db.elenco_persone() if (p["nome"], p["cognome"]) == (nome, cognome)), None)
    if persona is None:
        persona, password = db.crea_persona(nome, cognome, RUOLO_COMMITTENTE)
        db._registra_credenziali_iniziali([(f"{nome} {cognome}", persona["username"], password)])
    candidati = []
    for c in db.elenco_cantieri():
        for a in elenco(c["id"]):
            candidati.append((c["nome"] != "Lungo Avisio", a["nome"] != "APP 3", c["id"], a["id"]))
    if candidati:
        aggiungi_committente(min(candidati)[3], persona["id"])
    _imposta("esempio_committente", _ora())


def _attivita_su_appartamenti() -> None:
    """Una sola volta: le attività senza appartamento vanno sull'APP 3 del loro cantiere,
    oppure sullo spazio comune se il cantiere non ha un APP 3."""
    if _impostazione("attivita_su_appartamenti"):
        return
    with db.connessione() as con:
        righe = con.execute("""SELECT id, cantiere_id FROM attivita
                               WHERE appartamento_id IS NULL AND cantiere_id IS NOT NULL""").fetchall()
    for id_attivita, id_cantiere in righe:
        with db.connessione() as con:
            r = con.execute("""SELECT id FROM appartamenti WHERE cantiere_id = ? AND nome = 'APP 3' COLLATE NOCASE
                               ORDER BY pianta IS NULL, pianta LIMIT 1""", (id_cantiere,)).fetchone()
        destinazione = r[0] if r else spazio_comune(id_cantiere)
        if destinazione:
            with db.connessione() as con:
                con.execute("UPDATE attivita SET appartamento_id = ? WHERE id = ?", (destinazione, id_attivita))
            log.info("Attività %s spostata sull'appartamento %s", id_attivita, destinazione)
    _imposta("attivita_su_appartamenti", _ora())


def inizializza() -> None:
    """All'avvio: legge le piante di tutti i cantieri (appartamenti e spazi comuni), mette le attività
    vecchie su un appartamento e crea il committente di esempio."""
    for c in db.elenco_cantieri():
        try:
            sincronizza(c["id"])
        except Exception:  # una pianta rovinata non deve impedire l'avvio
            log.exception("Lettura delle piante del cantiere %s non riuscita", c["nome"])
    _attivita_su_appartamenti()
    _committente_esempio()
