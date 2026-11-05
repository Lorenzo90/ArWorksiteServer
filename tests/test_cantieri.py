import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from conftest import client_superuser


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "API_KEY", None)
    return client_superuser()


def persona(client, nome):
    return next(p for p in client.get("/api/persone").json() if p["nome"] == nome)


def test_ruoli_dal_file_excel(client):
    ruoli = client.get("/api/ruoli").json()
    assert len(ruoli) == 16
    assert [r["sigla"] for r in ruoli][:6] == ["CL", "GL", "PR", "DL", "CSE", "CC"]
    assert next(r for r in ruoli if r["sigla"] == "DL")["nome"] == "Direttore dei lavori"


def test_dati_iniziali(client):
    mario = persona(client, "Mario")
    assert mario["ruolo"] == "DL" and mario["ruolo_nome"] == "Direttore dei lavori"
    cantieri = client.get("/api/cantieri").json()
    assert len(cantieri) == 4
    assert all(c["direttore_lavori"]["id"] == mario["id"] for c in cantieri)
    avisio = next(c for c in cantieri if c["nome"] == "Lungo Avisio")
    assert [m["ruolo"] for m in avisio["squadra"]] == ["DL", "CC", "EL"]  # ordine del file Excel
    assert mario["cantieri"] == 4


def test_nuovo_cantiere_con_mario_dl_di_default(client):
    r = client.post("/api/cantieri", json={"nome": "  Condominio   Via Verdi "})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["nome"] == "Condominio Via Verdi"
    assert c["direttore_lavori"]["nome"] == "Mario" and len(c["squadra"]) == 1


def test_dl_deve_avere_il_ruolo_giusto(client):
    luca = persona(client, "Luca")  # capocantiere
    r = client.post("/api/cantieri", json={"nome": "X", "direttore_lavori_id": luca["id"]})
    assert r.status_code == 400 and "Direttore dei lavori" in r.json()["detail"]
    assert client.post("/api/cantieri", json={"nome": "X", "direttore_lavori_id": 999}).status_code == 400
    anna = client.post("/api/persone", json={"nome": "Anna", "cognome": "Gialli", "ruolo": "DL"}).json()
    r = client.post("/api/cantieri", json={"nome": "Capannone", "direttore_lavori_id": anna["id"]})
    assert r.json()["direttore_lavori"]["nome"] == "Anna"


def test_squadra(client):
    c = client.post("/api/cantieri", json={"nome": "Capannone"}).json()
    luca, paolo = persona(client, "Luca"), persona(client, "Paolo")
    r = client.post(f"/api/cantieri/{c['id']}/squadra", json={"persona_id": luca["id"]})
    assert r.status_code == 200 and [m["ruolo"] for m in r.json()["squadra"]] == ["DL", "CC"]
    client.post(f"/api/cantieri/{c['id']}/squadra", json={"persona_id": paolo["id"]})
    assert client.post(f"/api/cantieri/{c['id']}/squadra", json={"persona_id": luca["id"]}).status_code == 409
    r = client.delete(f"/api/cantieri/{c['id']}/squadra/{luca['id']}")
    assert [m["nome"] for m in r.json()["squadra"]] == ["Mario", "Paolo"]
    assert client.delete(f"/api/cantieri/{c['id']}/squadra/{luca['id']}").status_code == 404
    mario = persona(client, "Mario")
    r = client.delete(f"/api/cantieri/{c['id']}/squadra/{mario['id']}")
    assert r.json()["direttore_lavori"] is None


def test_nomi_duplicati_e_vuoti(client):
    assert client.post("/api/cantieri", json={"nome": "lungo avisio"}).status_code == 409
    assert client.post("/api/cantieri", json={"nome": "   "}).status_code == 422
    assert client.post("/api/persone", json={"nome": "Mario", "cognome": "Rossi"}).status_code == 409


def test_rinomina_ed_elimina_cantiere(client):
    primo = client.get("/api/cantieri").json()[0]
    r = client.patch(f"/api/cantieri/{primo['id']}", json={"nome": "Nuovo nome"})
    assert r.json()["nome"] == "Nuovo nome" and r.json()["squadra"] == primo["squadra"]
    assert client.delete(f"/api/cantieri/{primo['id']}").status_code == 204
    assert len(client.get("/api/cantieri").json()) == 3
    assert client.delete(f"/api/cantieri/{primo['id']}").status_code == 404


def test_persone_e_ruoli(client):
    r = client.post("/api/persone", json={"nome": "Anna", "cognome": "Gialli", "ruolo": "id"})
    assert r.status_code == 201 and r.json()["ruolo"] == "ID" and r.json()["ruolo_nome"] == "Idraulico"
    assert client.post("/api/persone", json={"nome": "Bo", "cognome": "Bi", "ruolo": "XX"}).status_code == 400
    anna = r.json()
    r = client.patch(f"/api/persone/{anna['id']}", json={"ruolo": "TT"})
    assert r.json()["ruolo_nome"] == "Termotecnico"
    assert client.patch(f"/api/persone/{anna['id']}", json={"ruolo": "XX"}).status_code == 400


def test_elimina_persona_la_toglie_dalle_squadre(client):
    luca = persona(client, "Luca")
    assert luca["cantieri"] == 2
    assert client.delete(f"/api/persone/{luca['id']}").status_code == 204
    for c in client.get("/api/cantieri").json():
        assert luca["id"] not in [m["id"] for m in c["squadra"]]


def test_chiave(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "segreta")
    anonimo = TestClient(app)
    assert anonimo.get("/api/cantieri").status_code == 401
    assert anonimo.get("/api/cantieri", headers={"X-Api-Key": "segreta"}).status_code == 200


def test_pagina(client):
    r = client.get("/cantieri")
    assert r.status_code == 200 and "Direttore dei lavori" in r.text
