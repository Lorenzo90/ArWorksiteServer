import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from conftest import client_superuser


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "API_KEY", None)
    return client_superuser()


def password_iniziali(tmp_path, client=None):
    """Legge le password generate al primo avvio dal file credenziali_iniziali.txt."""
    if client is not None:
        client.get("/api/health"), client.get("/api/persone")  # l'archivio nasce alla prima richiesta
    f = tmp_path / "credenziali_iniziali.txt"
    testo = f.read_text(encoding="utf-8") if f.exists() else ""
    tutte = dict(re.findall(r"utente: (\S+)\s+password: (\S+)", testo))
    tutte.pop("superuser", None)  # quella delle pagine web, non di una persona
    return tutte


def test_password_iniziali_per_ogni_persona(client, tmp_path):
    persone = client.get("/api/persone").json()
    pw = password_iniziali(tmp_path)
    assert {p["username"] for p in persone} == set(pw) == {"mario.rossi", "luca.bianchi", "giulia.verdi", "paolo.neri",
                                                                  "lorenzo.orlandi"}
    assert all(len(v) == 10 for v in pw.values())
    # nel database c'è solo l'impronta
    con = sqlite3.connect(tmp_path / "test.db")
    hashes = [r[0] for r in con.execute("SELECT password_hash FROM persone")]
    assert all(h.startswith("pbkdf2_sha256$") for h in hashes)
    assert not any(v in h for v in pw.values() for h in hashes)


def test_login_restituisce_i_cantieri_assegnati(client, tmp_path):
    pw = password_iniziali(tmp_path, client)
    r = client.post("/api/login", json={"username": "mario.rossi", "password": pw["mario.rossi"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["persona"]["nome"] == "Mario" and d["persona"]["ruolo"] == "DL" and d["token"]
    assert len(d["cantieri"]) == 4
    avisio = next(c for c in d["cantieri"] if c["nome"] == "Lungo Avisio")
    assert {m["nome"] for m in avisio["squadra"]} == {"Mario", "Luca", "Paolo"}

    r = client.post("/api/login", json={"username": "Luca.Bianchi ", "password": pw["luca.bianchi"]})
    assert r.json()["persona"]["ruolo_nome"] == "Capocantiere"
    assert [c["nome"] for c in r.json()["cantieri"]] == ["Lungo Avisio", "Residenza Le Querce"]

    r = client.post("/api/login", json={"username": "giulia.verdi", "password": pw["giulia.verdi"]})
    assert [c["nome"] for c in r.json()["cantieri"]] == ["Via Roma 12"]

    nuovo = client.post("/api/persone", json={"nome": "Anna", "cognome": "Gialli", "ruolo": "PI"}).json()
    r = client.post("/api/login", json={"username": "anna.gialli", "password": nuovo["password"]})
    assert r.json()["cantieri"] == []


def test_login_errato(client, tmp_path):
    pw = password_iniziali(tmp_path, client)
    assert client.post("/api/login", json={"username": "mario.rossi", "password": "sbagliata"}).status_code == 401
    assert client.post("/api/login", json={"username": "nessuno", "password": pw["mario.rossi"]}).status_code == 401
    assert client.post("/api/login", json={"username": "luca.bianchi", "password": pw["mario.rossi"]}).status_code == 401


def test_token_e_miei_cantieri(client, tmp_path):
    pw = password_iniziali(tmp_path, client)
    token = client.post("/api/login", json={"username": "mario.rossi", "password": pw["mario.rossi"]}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/miei-cantieri", headers=h)
    assert r.status_code == 200 and len(r.json()["cantieri"]) == 4
    # tolto dalla squadra, il cantiere sparisce dall'elenco di Mario; aggiunto, ricompare
    mario = r.json()["persona"]
    primo = r.json()["cantieri"][0]
    client.delete(f"/api/cantieri/{primo['id']}/squadra/{mario['id']}")
    assert len(client.get("/api/miei-cantieri", headers=h).json()["cantieri"]) == 3
    client.post(f"/api/cantieri/{primo['id']}/squadra", json={"persona_id": mario["id"]})
    assert len(client.get("/api/miei-cantieri", headers=h).json()["cantieri"]) == 4

    assert client.get("/api/miei-cantieri").status_code == 401
    assert client.get("/api/miei-cantieri", headers={"Authorization": "Bearer falso"}).status_code == 401
    assert client.post("/api/logout", headers=h).status_code == 204
    assert client.get("/api/miei-cantieri", headers=h).status_code == 401


def test_nuova_persona_e_nuova_password(client, tmp_path):
    r = client.post("/api/persone", json={"nome": "Nicolò", "cognome": "Dal Bò"})
    assert r.status_code == 201
    p = r.json()
    assert p["username"] == "nicolo.dalbo" and len(p["password"]) == 10
    token = client.post("/api/login", json={"username": "nicolo.dalbo", "password": p["password"]}).json()["token"]

    r = client.post(f"/api/persone/{p['id']}/password")
    nuova = r.json()["password"]
    assert nuova != p["password"]
    # vecchia password e vecchie sessioni non valgono più
    assert client.post("/api/login", json={"username": "nicolo.dalbo", "password": p["password"]}).status_code == 401
    assert client.get("/api/miei-cantieri", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/api/login", json={"username": "nicolo.dalbo", "password": nuova}).status_code == 200
    assert "password" not in client.get("/api/persone").json()[0]


def test_username_omonimi(client):
    a = client.post("/api/persone", json={"nome": "Anna", "cognome": "Rossi"}).json()
    b = client.post("/api/persone", json={"nome": "Anna", "cognome": "Rossì"}).json()
    assert a["username"] == "anna.rossi" and b["username"] == "anna.rossi2"


def test_migrazione_database_esistente(tmp_path, monkeypatch):
    """Un archivio creato dalla versione precedente (senza username/password) viene aggiornato."""
    percorso = tmp_path / "vecchio.db"
    con = sqlite3.connect(percorso)
    con.executescript("""
        CREATE TABLE persone (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, cognome TEXT NOT NULL,
            ruolo TEXT NOT NULL DEFAULT 'Capocantiere', UNIQUE (nome, cognome));
        CREATE TABLE cantieri (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
            capocantiere_id INTEGER REFERENCES persone(id) ON DELETE RESTRICT, creato TEXT NOT NULL);
        INSERT INTO persone (nome, cognome, ruolo) VALUES ('Mario', 'Rossi', 'Capocantiere');
        INSERT INTO cantieri (nome, capocantiere_id, creato) VALUES ('Mio cantiere', 1, '2026-11-04T20:00:00+00:00');
    """)
    con.commit(); con.close()
    monkeypatch.setattr(config, "DB_PATH", percorso)
    db.inizializza()
    pw = password_iniziali(tmp_path)
    assert list(pw) == ["mario.rossi"]
    esito = db.accedi("mario.rossi", pw["mario.rossi"])
    # Mario Rossi diventa Direttore dei lavori; il capocantiere del cantiere entra nella squadra
    assert esito and esito[2]["ruolo"] == "DL"
    assert [c["nome"] for c in db.elenco_cantieri(esito[2]["id"])] == ["Mio cantiere"]
    db.inizializza()  # una seconda volta non rigenera nulla
    assert list(password_iniziali(tmp_path)) == ["mario.rossi"]
    assert db.elimina_persona(esito[2]["id"])  # il vecchio vincolo sul capocantiere non blocca più


def test_migrazione_archivio_di_esempio_precedente(tmp_path, monkeypatch):
    """L'archivio creato dalla versione con il solo capocantiere: Mario Rossi diventa DL e le password restano."""
    percorso = tmp_path / "arworksite.db"
    con = sqlite3.connect(percorso)
    con.executescript("""
        CREATE TABLE persone (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, cognome TEXT NOT NULL,
            ruolo TEXT NOT NULL DEFAULT 'Capocantiere', username TEXT, password_hash TEXT, UNIQUE (nome, cognome));
        CREATE TABLE cantieri (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
            capocantiere_id INTEGER REFERENCES persone(id) ON DELETE RESTRICT, creato TEXT NOT NULL);
        CREATE TABLE sessioni (token_hash TEXT PRIMARY KEY, persona_id INTEGER NOT NULL REFERENCES persone(id)
            ON DELETE CASCADE, creata TEXT NOT NULL, scadenza TEXT NOT NULL);
    """)
    from app import credenziali
    pw = {}
    for nome, cognome, ruolo in [("Mario", "Rossi", "Capocantiere"), ("Luca", "Bianchi", "Capocantiere"),
                                 ("Giulia", "Verdi", "Capocantiere"), ("Paolo", "Neri", "Geometra"),
                                 ("Anna", "Gialli", "Capocantiere")]:
        u = f"{nome.lower()}.{cognome.lower()}"
        pw[u] = credenziali.genera_password()
        con.execute("INSERT INTO persone (nome, cognome, ruolo, username, password_hash) VALUES (?, ?, ?, ?, ?)",
                    (nome, cognome, ruolo, u, credenziali.hash_password(pw[u])))
    for n in ["Lungo Avisio", "Residenza Le Querce", "Via Roma 12", "Scuola Primaria Trento Nord"]:
        con.execute("INSERT INTO cantieri (nome, capocantiere_id, creato) VALUES (?, 1, '2026-11-04T20:46:54+00:00')", (n,))
    con.commit(); con.close()

    monkeypatch.setattr(config, "DB_PATH", percorso)
    db.inizializza()
    ruoli = {p["username"]: p["ruolo"] for p in db.elenco_persone()}
    assert ruoli == {"mario.rossi": "DL", "luca.bianchi": "CC", "giulia.verdi": "PR", "paolo.neri": "EL",
                     "anna.gialli": "CC"}
    cantieri = db.elenco_cantieri()
    assert len(cantieri) == 4 and all(c["direttore_lavori"]["nome"] == "Mario" for c in cantieri)
    # nessuna password rigenerata: valgono quelle di prima
    assert password_iniziali(tmp_path) == {}
    esito = db.accedi("mario.rossi", pw["mario.rossi"])
    assert esito and len(db.elenco_cantieri(esito[2]["id"])) == 4
    assert db.accedi("luca.bianchi", pw["luca.bianchi"]) and db.elenco_cantieri(db.accedi("luca.bianchi", pw["luca.bianchi"])[2]["id"]) == []
