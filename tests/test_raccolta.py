import hashlib

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from conftest import client_superuser, dxf_di_prova


@pytest.fixture()
def su(tmp_path, monkeypatch):
    piante = tmp_path / "piante"
    (piante / "Lungo Avisio").mkdir(parents=True)
    (piante / "Lungo Avisio" / "APP 3_V1.dxf").write_text(dxf_di_prova(("u.a.01", "u.a.02", "u.a.03")))
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "PIANTE_DIR", piante)
    monkeypatch.setattr(config, "API_KEY", None)
    return client_superuser()


def entra(su, nome, ruolo="EL", squadra=True):
    persone = {p["nome"]: p for p in su.get("/api/persone").json()}
    p = persone.get(nome) or su.post("/api/persone", json={"nome": nome, "cognome": "Prova", "ruolo": ruolo}).json()
    avisio = next(c for c in su.get("/api/cantieri").json() if c["nome"] == "Lungo Avisio")
    if squadra:
        su.post(f"/api/cantieri/{avisio['id']}/squadra", json={"persona_id": p["id"]})
    pw = su.post(f"/api/persone/{p['id']}/password").json()["password"]
    c = TestClient(app)
    d = c.post("/api/login", json={"username": p["username"], "password": pw}).json()
    return c, d, {"Authorization": f"Bearer {d['token']}"}, avisio


def app3(d):
    k = next(x for x in d["cantieri"] if x["nome"] == "Lungo Avisio")
    return next(a for a in k["appartamenti"] if a["nome"] == "APP 3")


def put(c, h, aid, percorso, dati, **kw):
    return c.put(f"/api/appartamenti/{aid}/raccolta/{percorso}", content=dati,
                 headers={**h, "X-Sha256": hashlib.sha256(dati).hexdigest(), "Content-Type": "application/octet-stream"},
                 **kw)


def test_caricamento_e_sincronizzazione(su):
    c, d, h, _ = entra(su, "Anna")
    a = app3(d)
    assert a["raccolta"]["n_file"] == 0
    r = put(c, h, a["id"], "Soggiorno/Parete 2/1/wall.jpg", b"\xff\xd8immagine",
            params=None)
    assert r.status_code == 201 and r.json()["esito"] == "nuovo", r.text
    assert put(c, h, a["id"], "Soggiorno/Parete 2/1/wall.jpg", b"\xff\xd8immagine").json()["esito"] == "invariato"
    assert put(c, h, a["id"], "Soggiorno/Parete 2/1/metadata.json", b'{"w":3.2}').status_code == 201
    assert put(c, h, a["id"], "Soggiorno/Parete 2/1/metadata.json", b'{"w":3.3}').json()["esito"] == "aggiornato"
    elenco = c.get(f"/api/appartamenti/{a['id']}/raccolta", headers=h)
    assert elenco.status_code == 200 and elenco.json()["puoi_caricare"] is True
    assert [f["percorso"] for f in elenco.json()["file"]] == ["Soggiorno/Parete 2/1/metadata.json",
                                                              "Soggiorno/Parete 2/1/wall.jpg"]
    assert elenco.json()["file"][0]["chi"] == "Anna Prova"
    assert c.get(f"/api/appartamenti/{a['id']}/raccolta",
                 headers={**h, "If-None-Match": elenco.headers["etag"]}).status_code == 304
    assert elenco.json()["file"][0]["url_download"].endswith("/raccolta/Soggiorno/Parete%202/1/metadata.json")
    f = c.get(elenco.json()["file"][0]["url_download"], headers=h)
    assert f.status_code == 200 and f.content == b'{"w":3.3}'
    # il riepilogo arriva anche con i dati della persona
    assert app3(c.get("/api/miei-dati", headers=h).json())["raccolta"]["n_file"] == 2
    # la pagina web (superuser) vede tutto
    assert len(su.get(f"/api/appartamenti/{a['id']}/raccolta").json()["file"]) == 2


def test_controlli(su):
    c, d, h, _ = entra(su, "Anna")
    a = app3(d)
    base = f"/api/appartamenti/{a['id']}/raccolta/"
    assert c.put(base + "x.jpg", content=b"abc", headers={**h, "X-Sha256": "0" * 64}).status_code == 400  # impronta errata
    for brutto in (".foto_esempio", "Bagno/virus.exe", "a:b/c.jpg", "Bagno/.nascosto/x.jpg"):
        assert put(c, h, a["id"], brutto, b"x").status_code == 400, brutto
    from app import raccolta as rc
    for brutto in ("../fuori.jpg", "a/./b.jpg", "a//b.jpg", "", "a/b./c.jpg", " a/b.jpg", "x/" * 11 + "y.jpg"):
        with pytest.raises(rc.PercorsoNonValido):
            rc.normalizza(brutto)
    assert rc.normalizza("\\Soggiorno\\Parete 2\\1\\wall.jpg") == "Soggiorno/Parete 2/1/wall.jpg"
    assert put(c, h, a["id"], "vuoto.jpg", b"").status_code == 400
    assert TestClient(app).get(base[:-1]).status_code == 401


def test_due_telefoni_stesso_percorso(su):
    c1, d1, h1, _ = entra(su, "Anna")
    c2, d2, h2, _ = entra(su, "Bruno")
    a = app3(d1)
    assert put(c1, h1, a["id"], "Bagno/Parete 1/1/wall.jpg", b"anna").status_code == 201
    r = put(c2, h2, a["id"], "Bagno/Parete 1/1/wall.jpg", b"bruno")
    assert r.status_code == 409 and "Anna Prova" in r.json()["detail"]
    assert put(c2, h2, a["id"], "Bagno/Parete 1/1/wall.jpg", b"bruno", params={"sostituisci": "true"}).status_code == 200
    # Bruno non è DL: non può eliminare un file di Anna... ora il file è suo, quindi sì
    assert c2.delete(f"/api/appartamenti/{a['id']}/raccolta/Bagno/Parete 1/1/wall.jpg", headers=h2).status_code == 204
    assert put(c1, h1, a["id"], "Bagno/foto/IMG_1.jpg", b"anna2").status_code == 201
    assert c2.delete(f"/api/appartamenti/{a['id']}/raccolta/Bagno/foto/IMG_1.jpg", headers=h2).status_code == 403


def test_committente_legge_ma_non_carica(su):
    c, d, h, _ = entra(su, "Anna")
    a = app3(d)
    put(c, h, a["id"], "Cucina/report.json", b"{}")
    cl, dl, hl, _ = entra(su, "Lorenzo", squadra=False)
    assert app3(dl)["committente"] is True
    assert len(cl.get(f"/api/appartamenti/{a['id']}/raccolta", headers=hl).json()["file"]) == 1
    assert put(cl, hl, a["id"], "Cucina/mia.jpg", b"x").status_code == 403
    # un appartamento non suo: non esiste per lui
    altro = next(x for x in d["cantieri"][0]["appartamenti"] if x["nome"] == "APP 1")
    assert cl.get(f"/api/appartamenti/{altro['id']}/raccolta", headers=hl).status_code == 404


def test_aggiornamento_attivita_con_foto_e_stato(su):
    c, d, h, avisio = entra(su, "Anna")
    a = app3(d)
    anna = d["persona"]
    att = su.post("/api/attivita", json={"titolo": "Tracce",
                                         "appartamento_id": a["id"], "persone_ids": [anna["id"]]}).json()
    url = f"/api/mie-attivita/{att['id']}/aggiornamento"
    rif = a["id_app"] + "/Bagno/Parete 1"
    assert c.post(f"/api/mie-attivita/{att['id']}/stato", headers=h, json={"stato": "presa_in_carico"}).status_code == 200
    r = c.post(url, headers=h, data={"stato": "chiusa", "nota": "Fatto", "riferimento": rif},
               files=[("file", ("p1.jpg", b"\xff\xd81", "image/jpeg")), ("file", ("p2.jpg", b"\xff\xd82", "image/jpeg"))])
    assert r.status_code == 200, r.text
    x = r.json()["attivita"]
    assert x["stato"] == "chiusa" and len(r.json()["caricati"]) == 2
    assert {(f["chi"], f["riferimento"]) for f in x["allegati"]} == {("Anna Prova", rif)}
    assert c.get(x["allegati"][0]["url_download"], headers=h).status_code == 200
    # passaggio non permesso: non salva neanche i file
    r = c.post(url, headers=h, data={"stato": "aperta"}, files=[("file", ("p3.jpg", b"3", "image/jpeg"))])
    assert r.status_code == 403
    assert len(su.get(f"/api/attivita/{att['id']}").json()["allegati"]) == 2
    # solo foto, senza stato
    r = c.post(url, headers=h, files=[("file", ("p3.jpg", b"3", "image/jpeg"))])
    assert r.status_code == 200 and r.json()["attivita"]["stato"] == "chiusa"
    assert c.post(url, headers=h, files=[("file", ("x.exe", b"3", "x/y"))]).status_code == 400
    # solo una nota: diventa una nota libera; niente di niente: 400
    r = c.post(url, headers=h, data={"nota": "Manca una presa in bagno"})
    assert r.status_code == 200 and r.json()["attivita"]["note"][-1]["testo"] == "Manca una presa in bagno"
    assert c.post(url, headers=h, data={"nota": "  "}).status_code == 400
