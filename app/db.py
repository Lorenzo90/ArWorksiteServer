"""Archivio SQLite di ruoli, persone, cantieri con le loro squadre e sessioni (data/arworksite.db)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, credenziali

SCHEMA = """
CREATE TABLE IF NOT EXISTS ruoli (
    sigla TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    descrizione TEXT NOT NULL DEFAULT '',
    ordine INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS persone (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    cognome TEXT NOT NULL,
    ruolo TEXT NOT NULL DEFAULT 'CC',
    UNIQUE (nome, cognome)
);
CREATE TABLE IF NOT EXISTS cantieri (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
    creato TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS squadre (
    cantiere_id INTEGER NOT NULL REFERENCES cantieri(id) ON DELETE CASCADE,
    persona_id INTEGER NOT NULL REFERENCES persone(id) ON DELETE CASCADE,
    aggiunto TEXT NOT NULL,
    PRIMARY KEY (cantiere_id, persona_id)
);
CREATE TABLE IF NOT EXISTS amministratori (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    creato TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessioni_admin (
    token_hash TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES amministratori(id) ON DELETE CASCADE,
    creata TEXT NOT NULL,
    scadenza TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessioni (
    token_hash TEXT PRIMARY KEY,
    persona_id INTEGER NOT NULL REFERENCES persone(id) ON DELETE CASCADE,
    creata TEXT NOT NULL,
    scadenza TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appartamenti (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cantiere_id INTEGER NOT NULL REFERENCES cantieri(id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    numero INTEGER,
    etichetta TEXT,
    pianta TEXT,
    id_app TEXT,
    origine TEXT NOT NULL DEFAULT 'manuale',
    creato TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS appartamenti_unici
    ON appartamenti (cantiere_id, COALESCE(pianta, ''), nome COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS appartamento_committenti (
    appartamento_id INTEGER NOT NULL REFERENCES appartamenti(id) ON DELETE CASCADE,
    persona_id INTEGER NOT NULL REFERENCES persone(id) ON DELETE CASCADE,
    PRIMARY KEY (appartamento_id, persona_id)
);
CREATE TABLE IF NOT EXISTS impostazioni (
    chiave TEXT PRIMARY KEY,
    valore TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attivita (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titolo TEXT NOT NULL,
    descrizione TEXT NOT NULL DEFAULT '',
    cantiere_id INTEGER REFERENCES cantieri(id) ON DELETE SET NULL,
    appartamento_id INTEGER REFERENCES appartamenti(id) ON DELETE SET NULL,
    creata TEXT NOT NULL,
    modificata TEXT NOT NULL,
    stato TEXT NOT NULL DEFAULT 'aperta',
    stato_modificato TEXT
);
CREATE TABLE IF NOT EXISTS attivita_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attivita_id INTEGER NOT NULL REFERENCES attivita(id) ON DELETE CASCADE,
    quando TEXT NOT NULL,
    persona_id INTEGER REFERENCES persone(id) ON DELETE SET NULL,
    chi TEXT NOT NULL,
    testo TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attivita_stati (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attivita_id INTEGER NOT NULL REFERENCES attivita(id) ON DELETE CASCADE,
    stato TEXT NOT NULL,
    quando TEXT NOT NULL,
    persona_id INTEGER REFERENCES persone(id) ON DELETE SET NULL,
    chi TEXT NOT NULL,
    nota TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS attivita_assegnazioni (
    attivita_id INTEGER NOT NULL REFERENCES attivita(id) ON DELETE CASCADE,
    persona_id INTEGER NOT NULL REFERENCES persone(id) ON DELETE CASCADE,
    PRIMARY KEY (attivita_id, persona_id)
);
CREATE TABLE IF NOT EXISTS attivita_allegati (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attivita_id INTEGER NOT NULL REFERENCES attivita(id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL,
    file TEXT NOT NULL,
    dimensione INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    caricato TEXT NOT NULL,
    persona_id INTEGER REFERENCES persone(id) ON DELETE SET NULL,
    chi TEXT NOT NULL DEFAULT 'Superuser',
    riferimento TEXT
);
CREATE TABLE IF NOT EXISTS raccolta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appartamento_id INTEGER NOT NULL REFERENCES appartamenti(id) ON DELETE CASCADE,
    percorso TEXT NOT NULL,
    file TEXT NOT NULL,
    dimensione INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    modificato_dispositivo TEXT,
    caricato TEXT NOT NULL,
    persona_id INTEGER REFERENCES persone(id) ON DELETE SET NULL,
    chi TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS raccolta_percorsi ON raccolta (appartamento_id, percorso COLLATE NOCASE);
"""

# Ruoli dal foglio "Ruoli" di documentazione/Attivita_e_ruoli_appartamento.xlsx (copiati in ruoli.json).
_RUOLI_FILE = Path(__file__).with_name("ruoli.json")

RUOLO_DL = "DL"
RUOLO_CC = "CC"

PERSONE_ESEMPIO = [
    ("Mario", "Rossi", RUOLO_DL),
    ("Luca", "Bianchi", RUOLO_CC),
    ("Giulia", "Verdi", "PR"),
    ("Paolo", "Neri", "EL"),
]

# Nome del cantiere -> altri membri della squadra oltre al Direttore dei lavori (Mario Rossi).
CANTIERI_ESEMPIO = {
    "Lungo Avisio": [("Luca", "Bianchi"), ("Paolo", "Neri")],
    "Residenza Le Querce": [("Luca", "Bianchi")],
    "Via Roma 12": [("Giulia", "Verdi")],
    "Scuola Primaria Trento Nord": [],
}

# Direttore dei lavori proposto quando si crea un cantiere.
DL_PREDEFINITO = ("Mario", "Rossi")

DURATA_SESSIONE = timedelta(days=30)

# Accesso alle pagine web di gestione.
SUPERUSER = "superuser"
DURATA_SESSIONE_ADMIN = timedelta(hours=12)


def _ora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def ruoli_da_file() -> list[dict]:
    return json.loads(_RUOLI_FILE.read_text(encoding="utf-8"))["ruoli"]


@contextmanager
def connessione():
    percorso = Path(config.DB_PATH)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(percorso)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _colonne(con: sqlite3.Connection, tabella: str) -> set[str]:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({tabella})")}


def _username_libero(con: sqlite3.Connection, nome: str, cognome: str, escludi_id: int | None = None) -> str:
    base = credenziali.username_base(nome, cognome)
    candidato, n = base, 2
    while con.execute("SELECT 1 FROM persone WHERE username = ? AND id IS NOT ?",
                      (candidato, escludi_id)).fetchone():
        candidato, n = f"{base}{n}", n + 1
    return candidato


def _registra_credenziali_iniziali(righe: list[tuple[str, str, str]]) -> None:
    """Le password generate in automatico (primo avvio o aggiornamento) finiscono in un file di testo,
    perché nel database c'è solo l'impronta e altrimenti nessuno le conoscerebbe."""
    if not righe:
        return
    f = Path(config.DB_PATH).with_name("credenziali_iniziali.txt")
    nuovo = not f.exists()
    with f.open("a", encoding="utf-8") as out:
        if nuovo:
            out.write("Password generate automaticamente dal server ARworksite.\n"
                      "Consegnale alle persone e poi cancella questo file.\n"
                      "Per crearne una nuova: pagina Cantieri > Persone > Nuova password.\n\n")
        out.write(f"--- {_iso(_ora())} ---\n")
        for persona, username, password in righe:
            out.write(f"{persona:<28} utente: {username:<20} password: {password}\n")
        out.write("\n")


def _id_persona(con: sqlite3.Connection, nome: str, cognome: str) -> int | None:
    r = con.execute("SELECT id FROM persone WHERE nome = ? AND cognome = ?", (nome, cognome)).fetchone()
    return r["id"] if r else None


def _aggiorna_ruoli(con: sqlite3.Connection) -> None:
    for i, r in enumerate(ruoli_da_file()):
        con.execute("""INSERT INTO ruoli (sigla, nome, descrizione, ordine) VALUES (?, ?, ?, ?)
                       ON CONFLICT(sigla) DO UPDATE SET nome = excluded.nome,
                       descrizione = excluded.descrizione, ordine = excluded.ordine""",
                    (r["sigla"], r["nome"], r["descrizione"], i))


def _migra_versioni_precedenti(con: sqlite3.Connection) -> None:
    """Archivi creati prima dei ruoli Excel: ruolo a testo libero e cantiere con un solo capocantiere."""
    colonne = _colonne(con, "persone")
    if "username" not in colonne:
        con.execute("ALTER TABLE persone ADD COLUMN username TEXT")
    if "password_hash" not in colonne:
        con.execute("ALTER TABLE persone ADD COLUMN password_hash TEXT")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS persone_username ON persone(username)")

    # Ruolo scritto per esteso ("Capocantiere", "Geometra"...) -> sigla della tabella ruoli.
    sigle = {r["sigla"] for r in con.execute("SELECT sigla FROM ruoli")}
    per_nome = {r["nome"].lower(): r["sigla"] for r in con.execute("SELECT sigla, nome FROM ruoli")}
    per_nome["direttore lavori"] = RUOLO_DL
    # Le persone di esempio della versione precedente prendono i ruoli nuovi (Mario Rossi = Direttore dei lavori).
    esempio = {(n, c): r for n, c, r in PERSONE_ESEMPIO}
    for p in con.execute("SELECT id, nome, cognome, ruolo FROM persone").fetchall():
        if p["ruolo"] not in sigle:
            ruolo = esempio.get((p["nome"], p["cognome"])) or per_nome.get((p["ruolo"] or "").strip().lower(), RUOLO_CC)
            con.execute("UPDATE persone SET ruolo = ? WHERE id = ?", (ruolo, p["id"]))

    # capocantiere_id del cantiere -> membro della squadra.
    if "capocantiere_id" in _colonne(con, "cantieri"):
        con.execute("""INSERT OR IGNORE INTO squadre (cantiere_id, persona_id, aggiunto)
                       SELECT id, capocantiere_id, creato FROM cantieri WHERE capocantiere_id IS NOT NULL""")
        con.execute("UPDATE cantieri SET capocantiere_id = NULL")

    # Cartella delle piante del cantiere (data/piante/<cartella>): resta la stessa se il cantiere cambia nome.
    if "cartella_piante" not in _colonne(con, "cantieri"):
        con.execute("ALTER TABLE cantieri ADD COLUMN cartella_piante TEXT")
    con.execute("UPDATE cantieri SET cartella_piante = nome WHERE cartella_piante IS NULL")
    # Indirizzo del cantiere (facoltativo; lo manda l'app iPad quando crea un cantiere).
    if "indirizzo" not in _colonne(con, "cantieri"):
        con.execute("ALTER TABLE cantieri ADD COLUMN indirizzo TEXT NOT NULL DEFAULT ''")
    if "appartamento_id" not in _colonne(con, "attivita"):
        con.execute("ALTER TABLE attivita ADD COLUMN appartamento_id INTEGER "
                    "REFERENCES appartamenti(id) ON DELETE SET NULL")
    # Stato dell'attività: quelle create prima che esistesse sono tutte aperte.
    colonne = _colonne(con, "attivita")
    if "stato" not in colonne:
        con.execute("ALTER TABLE attivita ADD COLUMN stato TEXT NOT NULL DEFAULT 'aperta'")
        con.execute("UPDATE attivita SET stato = 'aperta'")
    if "stato_modificato" not in colonne:
        con.execute("ALTER TABLE attivita ADD COLUMN stato_modificato TEXT")
    # Allegati: chi li ha caricati (superuser o persona dall'app) e a quale parete/stanza si riferiscono.
    colonne = _colonne(con, "attivita_allegati")
    if "persona_id" not in colonne:
        con.execute("ALTER TABLE attivita_allegati ADD COLUMN persona_id INTEGER REFERENCES persone(id) ON DELETE SET NULL")
    if "chi" not in colonne:
        con.execute("ALTER TABLE attivita_allegati ADD COLUMN chi TEXT NOT NULL DEFAULT 'Superuser'")
    if "riferimento" not in colonne:
        con.execute("ALTER TABLE attivita_allegati ADD COLUMN riferimento TEXT")


def inizializza() -> None:
    """Crea/aggiorna le tabelle; al primo avvio inserisce persone e 4 cantieri di esempio.
    Chi non ha ancora username o password li riceve qui."""
    nuove_credenziali: list[tuple[str, str, str]] = []
    with connessione() as con:
        con.executescript(SCHEMA)
        _aggiorna_ruoli(con)
        _migra_versioni_precedenti(con)

        if con.execute("SELECT COUNT(*) FROM persone").fetchone()[0] == 0:
            con.executemany("INSERT INTO persone (nome, cognome, ruolo) VALUES (?, ?, ?)", PERSONE_ESEMPIO)
            dl = _id_persona(con, *DL_PREDEFINITO)
            for nome_cantiere, altri in CANTIERI_ESEMPIO.items():
                cid = con.execute("INSERT INTO cantieri (nome, creato, cartella_piante) VALUES (?, ?, ?)",
                                  (nome_cantiere, _iso(_ora()), nome_cantiere)).lastrowid
                for pid in [dl] + [_id_persona(con, *a) for a in altri]:
                    con.execute("INSERT INTO squadre (cantiere_id, persona_id, aggiunto) VALUES (?, ?, ?)",
                                (cid, pid, _iso(_ora())))

        if con.execute("SELECT COUNT(*) FROM amministratori").fetchone()[0] == 0:
            password = credenziali.genera_password()
            con.execute("INSERT INTO amministratori (username, password_hash, creato) VALUES (?, ?, ?)",
                        (SUPERUSER, credenziali.hash_password(password), _iso(_ora())))
            nuove_credenziali.append(("Superuser (pagine web)", SUPERUSER, password))

        for p in con.execute("SELECT * FROM persone WHERE username IS NULL OR password_hash IS NULL").fetchall():
            username = p["username"] or _username_libero(con, p["nome"], p["cognome"], p["id"])
            password = credenziali.genera_password()
            con.execute("UPDATE persone SET username = ?, password_hash = ? WHERE id = ?",
                        (username, credenziali.hash_password(password), p["id"]))
            nuove_credenziali.append((f"{p['nome']} {p['cognome']}", username, password))
    _registra_credenziali_iniziali(nuove_credenziali)


# --- Ruoli -------------------------------------------------------------------

def elenco_ruoli() -> list[dict]:
    with connessione() as con:
        return [dict(r) for r in con.execute("SELECT sigla, nome, descrizione FROM ruoli ORDER BY ordine")]


def ruolo_esiste(sigla: str) -> bool:
    with connessione() as con:
        return con.execute("SELECT 1 FROM ruoli WHERE sigla = ?", (sigla,)).fetchone() is not None


# --- Persone -----------------------------------------------------------------

_SELECT_PERSONE = """
    SELECT p.id, p.nome, p.cognome, p.ruolo, r.nome AS ruolo_nome, p.username,
           (SELECT COUNT(*) FROM squadre s WHERE s.persona_id = p.id) AS cantieri
    FROM persone p LEFT JOIN ruoli r ON r.sigla = p.ruolo
"""


def elenco_persone() -> list[dict]:
    with connessione() as con:
        return [dict(r) for r in con.execute(_SELECT_PERSONE + " ORDER BY p.cognome, p.nome")]


def leggi_persona(id_persona: int) -> dict | None:
    with connessione() as con:
        r = con.execute(_SELECT_PERSONE + " WHERE p.id = ?", (id_persona,)).fetchone()
        return dict(r) if r else None


def crea_persona(nome: str, cognome: str, ruolo: str) -> tuple[dict, str]:
    """Restituisce la persona e la sua password in chiaro (l'unica volta che si vede)."""
    password = credenziali.genera_password()
    with connessione() as con:
        username = _username_libero(con, nome, cognome)
        nuovo = con.execute(
            "INSERT INTO persone (nome, cognome, ruolo, username, password_hash) VALUES (?, ?, ?, ?, ?)",
            (nome, cognome, ruolo, username, credenziali.hash_password(password))).lastrowid
    return leggi_persona(nuovo), password


def cambia_ruolo(id_persona: int, ruolo: str) -> dict | None:
    with connessione() as con:
        con.execute("UPDATE persone SET ruolo = ? WHERE id = ?", (ruolo, id_persona))
    return leggi_persona(id_persona)


def nuova_password(id_persona: int) -> str | None:
    """Genera una nuova password e chiude le sessioni aperte di quella persona."""
    password = credenziali.genera_password()
    with connessione() as con:
        if not con.execute("UPDATE persone SET password_hash = ? WHERE id = ?",
                           (credenziali.hash_password(password), id_persona)).rowcount:
            return None
        con.execute("DELETE FROM sessioni WHERE persona_id = ?", (id_persona,))
    return password


def imposta_password(username: str, password: str) -> dict | None:
    """Imposta una password scelta (es. «1234» per la demo) e chiude le sessioni aperte di quella persona.
    None se l'utente non esiste."""
    with connessione() as con:
        r = con.execute("SELECT id FROM persone WHERE username = ? COLLATE NOCASE", (username.strip(),)).fetchone()
        if not r:
            return None
        con.execute("UPDATE persone SET password_hash = ? WHERE id = ?", (credenziali.hash_password(password), r["id"]))
        con.execute("DELETE FROM sessioni WHERE persona_id = ?", (r["id"],))
    return leggi_persona(r["id"])


def elimina_persona(id_persona: int) -> bool:
    """Elimina la persona; esce anche dalle squadre dei cantieri e le sue sessioni vengono chiuse."""
    with connessione() as con:
        return con.execute("DELETE FROM persone WHERE id = ?", (id_persona,)).rowcount > 0


# --- Accesso -----------------------------------------------------------------

def accedi(username: str, password: str) -> tuple[str, str, dict] | None:
    """Se le credenziali sono giuste crea una sessione: (token, scadenza, persona)."""
    with connessione() as con:
        r = con.execute("SELECT id, password_hash FROM persone WHERE username = ? COLLATE NOCASE",
                        (username.strip(),)).fetchone()
        if not r or not credenziali.verifica_password(password, r["password_hash"]):
            # stesso tempo di calcolo anche se l'utente non esiste
            if not r:
                credenziali.verifica_password(password, credenziali.hash_password("x"))
            return None
        token = credenziali.nuovo_token()
        ora = _ora()
        scadenza = _iso(ora + DURATA_SESSIONE)
        con.execute("DELETE FROM sessioni WHERE scadenza < ?", (_iso(ora),))
        con.execute("INSERT INTO sessioni (token_hash, persona_id, creata, scadenza) VALUES (?, ?, ?, ?)",
                    (credenziali.hash_token(token), r["id"], _iso(ora), scadenza))
        persona_id = r["id"]
    return token, scadenza, leggi_persona(persona_id)


def persona_da_token(token: str) -> dict | None:
    with connessione() as con:
        r = con.execute("SELECT persona_id FROM sessioni WHERE token_hash = ? AND scadenza > ?",
                        (credenziali.hash_token(token), _iso(_ora()))).fetchone()
    return leggi_persona(r["persona_id"]) if r else None


def esci(token: str) -> None:
    with connessione() as con:
        con.execute("DELETE FROM sessioni WHERE token_hash = ?", (credenziali.hash_token(token),))


# --- Superuser (pagine web) --------------------------------------------------

def accedi_admin(username: str, password: str) -> tuple[str, str, dict] | None:
    with connessione() as con:
        r = con.execute("SELECT id, username, password_hash FROM amministratori WHERE username = ? COLLATE NOCASE",
                        ((username or "").strip(),)).fetchone()
        if not r or not credenziali.verifica_password(password or "", r["password_hash"]):
            if not r:
                credenziali.verifica_password(password or "", credenziali.hash_password("x"))
            return None
        token = credenziali.nuovo_token()
        ora = _ora()
        scadenza = _iso(ora + DURATA_SESSIONE_ADMIN)
        con.execute("DELETE FROM sessioni_admin WHERE scadenza < ?", (_iso(ora),))
        con.execute("INSERT INTO sessioni_admin (token_hash, admin_id, creata, scadenza) VALUES (?, ?, ?, ?)",
                    (credenziali.hash_token(token), r["id"], _iso(ora), scadenza))
        return token, scadenza, {"id": r["id"], "username": r["username"]}


def admin_da_token(token: str) -> dict | None:
    with connessione() as con:
        r = con.execute("""SELECT a.id, a.username FROM sessioni_admin s JOIN amministratori a ON a.id = s.admin_id
                           WHERE s.token_hash = ? AND s.scadenza > ?""",
                        (credenziali.hash_token(token), _iso(_ora()))).fetchone()
        return dict(r) if r else None


def esci_admin(token: str) -> None:
    with connessione() as con:
        con.execute("DELETE FROM sessioni_admin WHERE token_hash = ?", (credenziali.hash_token(token),))


def imposta_password_admin(username: str, password: str) -> None:
    """Crea l'amministratore se manca, altrimenti cambia la password e chiude le sue sessioni."""
    with connessione() as con:
        h = credenziali.hash_password(password)
        r = con.execute("SELECT id FROM amministratori WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        if r:
            con.execute("UPDATE amministratori SET password_hash = ? WHERE id = ?", (h, r["id"]))
            con.execute("DELETE FROM sessioni_admin WHERE admin_id = ?", (r["id"],))
        else:
            con.execute("INSERT INTO amministratori (username, password_hash, creato) VALUES (?, ?, ?)",
                        (username, h, _iso(_ora())))


# --- Cantieri e squadre ------------------------------------------------------

def _squadre(con: sqlite3.Connection, ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    segnaposto = ",".join("?" * len(ids))
    righe = con.execute(f"""
        SELECT s.cantiere_id, p.id, p.nome, p.cognome, p.ruolo, r.nome AS ruolo_nome
        FROM squadre s JOIN persone p ON p.id = s.persona_id LEFT JOIN ruoli r ON r.sigla = p.ruolo
        WHERE s.cantiere_id IN ({segnaposto})
        ORDER BY p.ruolo != 'DL', COALESCE(r.ordine, 999), p.cognome, p.nome""", ids).fetchall()
    out: dict[int, list[dict]] = {i: [] for i in ids}
    for r in righe:
        out[r["cantiere_id"]].append({k: r[k] for k in ("id", "nome", "cognome", "ruolo", "ruolo_nome")})
    return out


def _con_squadra(con: sqlite3.Connection, righe: list[sqlite3.Row]) -> list[dict]:
    squadre = _squadre(con, [r["id"] for r in righe])
    risultati = []
    for r in righe:
        squadra = squadre[r["id"]]
        dl = next((m for m in squadra if m["ruolo"] == RUOLO_DL), None)
        conta = con.execute("""SELECT (SELECT COUNT(*) FROM appartamenti WHERE cantiere_id = ?),
                                      (SELECT COUNT(*) FROM attivita WHERE cantiere_id = ?)""",
                            (r["id"], r["id"])).fetchone()
        risultati.append({"id": r["id"], "nome": r["nome"], "indirizzo": r["indirizzo"] or "", "creato": r["creato"],
                          "direttore_lavori": dl, "squadra": squadra,
                          "n_appartamenti": conta[0], "n_attivita": conta[1]})
    return risultati


def elenco_cantieri(persona_id: int | None = None) -> list[dict]:
    """Tutti i cantieri, oppure solo quelli in cui `persona_id` fa parte della squadra."""
    with connessione() as con:
        if persona_id is None:
            righe = con.execute("SELECT id, nome, indirizzo, creato FROM cantieri ORDER BY nome COLLATE NOCASE").fetchall()
        else:
            righe = con.execute("""SELECT c.id, c.nome, c.indirizzo, c.creato FROM cantieri c
                                   JOIN squadre s ON s.cantiere_id = c.id AND s.persona_id = ?
                                   ORDER BY c.nome COLLATE NOCASE""", (persona_id,)).fetchall()
        return _con_squadra(con, righe)


def leggi_cantiere(id_cantiere: int) -> dict | None:
    with connessione() as con:
        righe = con.execute("SELECT id, nome, indirizzo, creato FROM cantieri WHERE id = ?", (id_cantiere,)).fetchall()
        return _con_squadra(con, righe)[0] if righe else None


def id_dl_predefinito() -> int | None:
    with connessione() as con:
        return _id_persona(con, *DL_PREDEFINITO)


def crea_cantiere(nome: str, direttore_lavori_id: int | None, indirizzo: str = "") -> dict:
    """Crea il cantiere; `direttore_lavori_id` (se c'è) entra nella squadra. Dall'app si passa qui chi crea
    il cantiere, qualunque ruolo abbia: entrare nella squadra gli dà l'accesso a tutto il cantiere."""
    with connessione() as con:
        nuovo = con.execute("INSERT INTO cantieri (nome, creato, cartella_piante, indirizzo) VALUES (?, ?, ?, ?)",
                            (nome, _iso(_ora()), nome, indirizzo or "")).lastrowid
        if direttore_lavori_id is not None:
            con.execute("INSERT INTO squadre (cantiere_id, persona_id, aggiunto) VALUES (?, ?, ?)",
                        (nuovo, direttore_lavori_id, _iso(_ora())))
    return leggi_cantiere(nuovo)


def in_squadra(id_cantiere: int, id_persona: int) -> bool:
    with connessione() as con:
        return con.execute("SELECT 1 FROM squadre WHERE cantiere_id = ? AND persona_id = ?",
                           (id_cantiere, id_persona)).fetchone() is not None


def rinomina_cantiere(id_cantiere: int, nome: str) -> dict | None:
    with connessione() as con:
        con.execute("UPDATE cantieri SET nome = ? WHERE id = ?", (nome, id_cantiere))
    return leggi_cantiere(id_cantiere)


def aggiungi_in_squadra(id_cantiere: int, id_persona: int) -> bool:
    """False se la persona era già nella squadra."""
    with connessione() as con:
        return con.execute("INSERT OR IGNORE INTO squadre (cantiere_id, persona_id, aggiunto) VALUES (?, ?, ?)",
                           (id_cantiere, id_persona, _iso(_ora()))).rowcount > 0


def togli_da_squadra(id_cantiere: int, id_persona: int) -> bool:
    with connessione() as con:
        tolta = con.execute("DELETE FROM squadre WHERE cantiere_id = ? AND persona_id = ?",
                            (id_cantiere, id_persona)).rowcount > 0
        # chi esce dalla squadra non è più assegnato alle attività di quel cantiere
        con.execute("DELETE FROM attivita_assegnazioni WHERE persona_id = ? AND attivita_id IN "
                    "(SELECT id FROM attivita WHERE cantiere_id = ?)", (id_persona, id_cantiere))
        return tolta


def elimina_cantiere(id_cantiere: int) -> bool:
    with connessione() as con:
        return con.execute("DELETE FROM cantieri WHERE id = ?", (id_cantiere,)).rowcount > 0
