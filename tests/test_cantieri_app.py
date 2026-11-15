"""Cantieri creati dall'app iPad (RoomPlan): POST /api/cantieri e POST /api/cantieri/{id}/piante con il token."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from conftest import client_superuser


@pytest.fixture()
def su(tmp_path, monkeypatch):
    piante = tmp_path / "piante"
    piante.mkdir()
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "PIANTE_DIR", piante)
    monkeypatch.setattr(config, "API_KEY", None)
    return client_superuser()


def collega(su, nome, ruolo="EL"):
    """Persona nuova (in nessuna squadra) collegata dall'app: (client, intestazioni, persona)."""
    p = su.post("/api/persone", json={"nome": nome, "cognome": "Ipad", "ruolo": ruolo}).json()
    c = TestClient(app)
    d = c.post("/api/login", json={"username": p["username"], "password": p["password"]}).json()
    return c, {"Authorization": f"Bearer {d['token']}"}, d["persona"]


def dxf_roomplan(etichetta="App. 1") -> bytes:
    """DXF ASCII R12 in metri come quello dell'app iPad: contorno su SUP COMMERCIALI, «App. N»,
    locali con nome, «Sup. … mq» e «h …»."""
    r = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$INSUNITS", "70", "6",
         "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES",
         "0", "POLYLINE", "8", "SUP COMMERCIALI", "66", "1", "70", "1"]
    for x, y in ((0, 0), (8.5, 0), (8.5, 6.2), (0, 6.2)):
        r += ["0", "VERTEX", "8", "SUP COMMERCIALI", "10", str(x), "20", str(y)]
    r += ["0", "SEQEND", "8", "SUP COMMERCIALI"]
    if etichetta:
        r += ["0", "TEXT", "8", "TESTI", "10", "4", "20", "3", "40", "0.3", "1", etichetta]
    for nome, sup, x in (("Soggiorno", "Sup. 24,10 mq", 2), ("Bagno", "Sup. 4,80 mq", 7)):
        r += ["0", "TEXT", "8", "LOCALI", "10", str(x), "20", "2", "40", "0.2", "1", nome,
              "0", "TEXT", "8", "LOCALI", "10", str(x), "20", "1.7", "40", "0.15", "1", sup,
              "0", "TEXT", "8", "LOCALI", "10", str(x), "20", "1.4", "40", "0.15", "1", "h 2,70"]
    r += ["0", "ENDSEC", "0", "EOF"]
    return ("\r\n".join(r) + "\r\n").encode("ascii")


def carica(c, h, id_cantiere, nome, dati):
    return c.post(f"/api/cantieri/{id_cantiere}/piante", files={"file": (nome, dati, "application/dxf")}, headers=h)


def mio_cantiere(c, h, id_cantiere):
    return next((k for k in c.get("/api/miei-dati", headers=h).json()["cantieri"] if k["id"] == id_cantiere), None)


def test_crea_cantiere_e_compare_in_miei_dati_come_squadra(su):
    c, h, persona = collega(su, "Ivana")
    r = c.post("/api/cantieri", json={"nome": "Via Roma 3", "indirizzo": "Trento"}, headers=h)
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"id"} and isinstance(r.json()["id"], int)
    k = mio_cantiere(c, h, r.json()["id"])
    assert k is not None and k["nome"] == "Via Roma 3" and k["accesso"] == "squadra"
    assert [m["id"] for m in k["squadra"]] == [persona["id"]]
    # dalla pagina web si vede con l'indirizzo, e chi lo ha creato è nella squadra
    dett = su.get(f"/api/cantieri/{r.json()['id']}").json()
    assert dett["indirizzo"] == "Trento" and [m["nome"] for m in dett["squadra"]] == ["Ivana"]
    # indirizzo vuoto o assente va bene
    assert c.post("/api/cantieri", json={"nome": "Via Verdi 1", "indirizzo": ""}, headers=h).status_code == 201
    assert c.post("/api/cantieri", json={"nome": "Via Verdi 2"}, headers=h).status_code == 201


def test_qualsiasi_ruolo_puo_creare(su):
    for nome, ruolo in (("Carla", "CL"), ("Pino", "PO"), ("Dario", "DL")):
        c, h, _ = collega(su, nome, ruolo)
        r = c.post("/api/cantieri", json={"nome": f"Cantiere di {nome}"}, headers=h)
        assert r.status_code == 201, (ruolo, r.text)
        assert mio_cantiere(c, h, r.json()["id"])["accesso"] == "squadra"


def test_senza_token_401_mai_404(su):
    anonimo = TestClient(app)
    assert anonimo.post("/api/cantieri", json={"nome": "X"}).status_code == 401
    assert anonimo.post("/api/cantieri", json={"nome": "X"},
                        headers={"Authorization": "Bearer sbagliato"}).status_code == 401
    # 401 anche se il corpo è sbagliato o il cantiere non esiste: il token si controlla prima
    assert anonimo.post("/api/cantieri", json={}).status_code == 401
    assert anonimo.post("/api/cantieri/999/piante",
                        files={"file": ("a.dxf", dxf_roomplan(), "x/y")}).status_code == 401
    c, h, _ = collega(su, "Ivana")
    assert c.post("/api/logout", headers=h).status_code == 204
    assert c.post("/api/cantieri", json={"nome": "X"}, headers=h).status_code == 401


def test_nome_vuoto_o_mancante_422(su):
    c, h, _ = collega(su, "Ivana")
    for corpo in ({"nome": "   "}, {"nome": ""}, {}, {"indirizzo": "Trento"}):
        assert c.post("/api/cantieri", json=corpo, headers=h).status_code == 422, corpo


def test_nome_gia_usato_409(su):
    c, h, _ = collega(su, "Ivana")
    assert c.post("/api/cantieri", json={"nome": "lungo avisio"}, headers=h).status_code == 409


def test_carica_pianta(su):
    c, h, _ = collega(su, "Ivana")
    cid = c.post("/api/cantieri", json={"nome": "Via Roma 3", "indirizzo": "Trento"}, headers=h).json()["id"]
    dati = dxf_roomplan()
    r = carica(c, h, cid, "Via Roma 3.dxf", dati)
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"id"} and isinstance(r.json()["id"], str)
    id_pianta = r.json()["id"]

    # /api/piante con lo sha256 giusto e il nome del file conservato
    elenco = c.get("/api/piante", headers=h).json()["piante"]
    p = next(x for x in elenco if x["id"] == id_pianta)
    assert p["nome"] == "Via Roma 3.dxf" and p["formato"] == "dxf"
    assert p["sha256"] == hashlib.sha256(dati).hexdigest()
    # si scarica identica
    f = c.get(f"/api/piante/{id_pianta}/file", headers=h)
    assert f.status_code == 200 and f.content == dati
    # e compare in cantieri[].piante di /api/miei-dati
    k = mio_cantiere(c, h, cid)
    assert [(x["id"], x["nome"], x["sha256"]) for x in k["piante"]] == [(id_pianta, "Via Roma 3.dxf", p["sha256"])]
    # sul server il file ha il nome mandato dall'app
    assert (config.PIANTE_DIR / "Via Roma 3" / "Via Roma 3.dxf").read_bytes() == dati


def test_appartamento_con_id_app_e_raccolta(su):
    c, h, _ = collega(su, "Ivana")
    cid = c.post("/api/cantieri", json={"nome": "Via Roma 3"}, headers=h).json()["id"]
    id_pianta = carica(c, h, cid, "Via Roma 3.dxf", dxf_roomplan("App. 1")).json()["id"]
    k = mio_cantiere(c, h, cid)
    app1 = next(a for a in k["appartamenti"] if a["origine"] == "pianta")
    assert app1["nome"] == "APP 1" and app1["id_app"] == "dwg:Via Roma 3/APP 1"
    assert app1["pianta_id"] == id_pianta and app1["etichetta"] == "App. 1"
    assert [a["nome"] for a in k["appartamenti"]] == ["Spazio comune", "APP 1"]
    # «Aggiorna dati» nell'app: la raccolta dell'appartamento risponde
    assert c.get(f"/api/appartamenti/{app1['id']}/raccolta", headers=h).status_code == 200


def test_numero_dell_appartamento_dal_testo_o_1(su):
    c, h, _ = collega(su, "Ivana")
    cid = c.post("/api/cantieri", json={"nome": "Edifici"}, headers=h).json()["id"]
    assert carica(c, h, cid, "Edificio B.dxf", dxf_roomplan("App. 2")).status_code == 201
    assert carica(c, h, cid, "Senza etichetta.dxf", dxf_roomplan(None)).status_code == 201
    ids = {a["id_app"] for a in mio_cantiere(c, h, cid)["appartamenti"] if a["origine"] == "pianta"}
    assert ids == {"dwg:Edificio B/APP 2", "dwg:Senza etichetta/APP 1"}


def test_nuovo_tentativo_basta_richiamare_le_piante(su):
    """Dopo un errore l'app richiama solo /api/cantieri/{id}/piante con lo stesso file: deve andare."""
    c, h, _ = collega(su, "Ivana")
    cid = c.post("/api/cantieri", json={"nome": "Via Roma 3"}, headers=h).json()["id"]
    primo = carica(c, h, cid, "Via Roma 3.dxf", dxf_roomplan()).json()["id"]
    r = carica(c, h, cid, "Via Roma 3.dxf", dxf_roomplan())
    assert r.status_code == 201 and r.json()["id"] == primo
    k = mio_cantiere(c, h, cid)
    assert len(k["piante"]) == 1 and len(k["appartamenti"]) == 2  # niente doppioni


def test_cantiere_inesistente_404(su):
    c, h, _ = collega(su, "Ivana")
    assert carica(c, h, 9999, "a.dxf", dxf_roomplan()).status_code == 404


def test_senza_accesso_403(su):
    c, h, _ = collega(su, "Ivana")
    cid = c.post("/api/cantieri", json={"nome": "Via Roma 3"}, headers=h).json()["id"]
    altro, h2, _ = collega(su, "Bruno")
    assert carica(altro, h2, cid, "Via Roma 3.dxf", dxf_roomplan()).status_code == 403
    avisio = next(k for k in su.get("/api/cantieri").json() if k["nome"] == "Lungo Avisio")
    assert carica(c, h, avisio["id"], "x.dxf", dxf_roomplan()).status_code == 403
    assert not (config.PIANTE_DIR / "Via Roma 3").exists() or not list((config.PIANTE_DIR / "Via Roma 3").iterdir())


def test_file_non_dxf_422(su):
    c, h, _ = collega(su, "Ivana")
    cid = c.post("/api/cantieri", json={"nome": "Via Roma 3"}, headers=h).json()["id"]
    for nome in ("pianta.pdf", "pianta.usdz", "pianta"):
        assert carica(c, h, cid, nome, b"dati").status_code == 422, nome
    # campo «file» mancante
    assert c.post(f"/api/cantieri/{cid}/piante", data={"altro": "x"}, headers=h).status_code == 422
    assert mio_cantiere(c, h, cid)["piante"] == []


def test_file_troppo_grande_413(su, monkeypatch):
    c, h, _ = collega(su, "Ivana")
    cid = c.post("/api/cantieri", json={"nome": "Via Roma 3"}, headers=h).json()["id"]
    monkeypatch.setattr(config, "MAX_UPLOAD_APP_MB", 0)
    assert carica(c, h, cid, "Via Roma 3.dxf", dxf_roomplan()).status_code == 413
    assert mio_cantiere(c, h, cid)["piante"] == []
    assert list((config.PIANTE_DIR / "Via Roma 3").iterdir()) == []  # nessun file a metà


def test_superuser_invariato(su):
    """Dalla pagina web le due API rispondono come prima (cantiere completo, scheda)."""
    r = su.post("/api/cantieri", json={"nome": "Capannone"})
    assert r.status_code == 201 and r.json()["direttore_lavori"]["nome"] == "Mario"
    r = su.post(f"/api/cantieri/{r.json()['id']}/piante", files={"file": ("P.dxf", dxf_roomplan(), "x/y")})
    assert r.status_code == 201 and "appartamenti" in r.json()
