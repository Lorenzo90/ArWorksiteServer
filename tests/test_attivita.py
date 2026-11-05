import pytest
from fastapi.testclient import TestClient

from app import attivita as att
from app import config
from app.main import app
from conftest import client_superuser


@pytest.fixture()
def su(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "API_KEY", None)
    return client_superuser()


def persona(su, nome):
    return next(p for p in su.get("/api/persone").json() if p["nome"] == nome)


def nuova_persona(su, nome, cognome, ruolo="CC"):
    return su.post("/api/persone", json={"nome": nome, "cognome": cognome, "ruolo": ruolo}).json()


def entra_app(nome_utente, password):
    c = TestClient(app)
    r = c.post("/api/login", json={"username": nome_utente, "password": password})
    assert r.status_code == 200, r.text
    return c, r.json(), {"Authorization": f"Bearer {r.json()['token']}"}


def test_crea_assegna_e_allegati(su, tmp_path):
    anna, bruno = nuova_persona(su, "Anna", "Gialli"), nuova_persona(su, "Bruno", "Blu", "EL")
    avisio = next(c for c in su.get("/api/cantieri").json() if c["nome"] == "Lungo Avisio")
    r = su.post("/api/attivita", json={"titolo": "  Verifica   tracce ", "descrizione": "Bagno e cucina\nentro venerdì",
                                       "cantiere_id": avisio["id"], "persone_ids": [anna["id"], bruno["id"], anna["id"]]})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["titolo"] == "Verifica tracce" and a["cantiere"]["nome"] == "Lungo Avisio"
    assert {p["nome"] for p in a["assegnatari"]} == {"Anna", "Bruno"}

    files = [("file", ("parete.JPG", b"\xff\xd8foto", "image/jpeg")),
             ("file", ("soggiorno.glb", b"glTF\x02\x00\x00\x00modello", "model/gltf-binary")),
             ("file", ("verbale.pdf", b"%PDF-1.4", "application/pdf"))]
    r = su.post(f"/api/attivita/{a['id']}/allegati", files=files)
    assert r.status_code == 201, r.text
    tipi = {f["nome"]: f["tipo"] for f in r.json()["caricati"]}
    assert tipi == {"parete.JPG": "foto", "soggiorno.glb": "scansione3d", "verbale.pdf": "file"}
    assert len(list((tmp_path / "attivita" / str(a["id"])).iterdir())) == 3
    assert not list((tmp_path / "attivita" / str(a["id"])).glob("*.tmp"))


def test_le_persone_assegnate_la_ricevono_al_login(su):
    anna, bruno, carla = (nuova_persona(su, "Anna", "Gialli"), nuova_persona(su, "Bruno", "Blu"),
                          nuova_persona(su, "Carla", "Viola"))
    a = su.post("/api/attivita", json={"titolo": "Foto pareti", "persone_ids": [anna["id"], bruno["id"]]}).json()
    su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", ("p.png", b"\x89PNGx", "image/png"))])

    _, d, _ = entra_app("anna.gialli", anna["password"])
    assert [x["titolo"] for x in d["attivita"]] == ["Foto pareti"]
    assert d["attivita"][0]["allegati"][0]["url_download"].startswith("http")
    c, d, h = entra_app("bruno.blu", bruno["password"])
    assert len(d["attivita"]) == 1
    assert [x["titolo"] for x in c.get("/api/mie-attivita", headers=h).json()["attivita"]] == ["Foto pareti"]
    _, d, _ = entra_app("carla.viola", carla["password"])
    assert d["attivita"] == []


def test_download_solo_per_chi_e_assegnato(su):
    anna, carla = nuova_persona(su, "Anna", "Gialli"), nuova_persona(su, "Carla", "Viola")
    a = su.post("/api/attivita", json={"titolo": "X", "persone_ids": [anna["id"]]}).json()
    f = su.post(f"/api/attivita/{a['id']}/allegati",
                files=[("file", ("m.glb", b"glTFdati", "x/y"))]).json()["caricati"][0]
    url = f["url_download"]

    c, _, h = entra_app("anna.gialli", anna["password"])
    r = c.get(url, headers=h)
    assert r.status_code == 200 and r.content == b"glTFdati" and "attachment" in r.headers["content-disposition"]
    assert r.headers["content-type"] == "model/gltf-binary"
    assert c.get(url, headers={**h, "If-None-Match": r.headers["etag"]}).status_code == 304
    assert "inline" in su.get(url + "?inline=true").headers["content-disposition"]

    c2, _, h2 = entra_app("carla.viola", carla["password"])
    assert c2.get(url, headers=h2).status_code == 404
    assert TestClient(app).get(url).status_code == 401


def test_modifica_assegnatari(su):
    anna, bruno = nuova_persona(su, "Anna", "Gialli"), nuova_persona(su, "Bruno", "Blu")
    a = su.post("/api/attivita", json={"titolo": "X", "persone_ids": [anna["id"]],
                                       "cantiere_id": su.get("/api/cantieri").json()[0]["id"]}).json()
    r = su.patch(f"/api/attivita/{a['id']}", json={"persone_ids": [bruno["id"]], "titolo": "Y"})
    assert [p["nome"] for p in r.json()["assegnatari"]] == ["Bruno"] and r.json()["titolo"] == "Y"
    assert r.json()["cantiere"] is not None  # non toccato
    r = su.patch(f"/api/attivita/{a['id']}", json={"cantiere_id": None})
    assert r.json()["cantiere"] is None
    _, d, _ = entra_app("anna.gialli", anna["password"])
    assert d["attivita"] == []
    assert su.patch(f"/api/attivita/{a['id']}", json={"persone_ids": [999]}).status_code == 400


def test_validazioni(su):
    assert su.post("/api/attivita", json={"titolo": "  "}).status_code == 422
    assert su.post("/api/attivita", json={"titolo": "X", "cantiere_id": 999}).status_code == 400
    a = su.post("/api/attivita", json={"titolo": "X"}).json()
    for nome in ("virus.exe", "pagina.html", "script.BAT"):
        assert su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", (nome, b"x", "x/y"))]).status_code == 400
    assert su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", ("vuoto.jpg", b"", "x/y"))]).status_code == 400
    r = su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", ("../../fuori.pdf", b"x", "x/y"))])
    assert r.status_code == 201 and r.json()["caricati"][0]["nome"] == "fuori.pdf"
    assert su.post("/api/attivita/999/allegati", files=[("file", ("a.jpg", b"x", "x/y"))]).status_code == 404


def test_elimina_allegato_e_attivita(su, tmp_path):
    a = su.post("/api/attivita", json={"titolo": "X"}).json()
    caricati = su.post(f"/api/attivita/{a['id']}/allegati",
                       files=[("file", ("a.jpg", b"1", "x/y")), ("file", ("b.jpg", b"2", "x/y"))]).json()["caricati"]
    cartella = tmp_path / "attivita" / str(a["id"])
    assert su.delete(f"/api/attivita/{a['id']}/allegati/{caricati[0]['id']}").status_code == 204
    assert len(list(cartella.iterdir())) == 1
    assert su.get(caricati[0]["url_download"]).status_code == 404
    assert su.delete(f"/api/attivita/{a['id']}").status_code == 204
    assert not cartella.exists()
    assert su.get(f"/api/attivita/{a['id']}").status_code == 404


def test_persona_eliminata_esce_dalle_attivita(su):
    anna = nuova_persona(su, "Anna", "Gialli")
    a = su.post("/api/attivita", json={"titolo": "X", "persone_ids": [anna["id"]]}).json()
    su.delete(f"/api/persone/{anna['id']}")
    assert su.get(f"/api/attivita/{a['id']}").json()["assegnatari"] == []


def test_solo_superuser_gestisce(su):
    anna = nuova_persona(su, "Anna", "Gialli")
    _, _, h = entra_app("anna.gialli", anna["password"])
    c = TestClient(app)
    assert c.get("/api/attivita").status_code == 401
    assert c.post("/api/attivita", json={"titolo": "X"}, headers=h).status_code == 401
    assert c.get("/attivita", follow_redirects=False).status_code == 303
    assert su.get("/attivita").status_code == 200


def test_tipo_da_nome():
    assert att.tipo_da_nome("A.HEIC") == "foto" and att.tipo_da_nome("x.glb") == "scansione3d"
    assert att.tipo_da_nome("pianta.dxf") == "file"
