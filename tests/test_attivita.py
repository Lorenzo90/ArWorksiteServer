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


def avisio(su):
    return next(c for c in su.get("/api/cantieri").json() if c["nome"] == "Lungo Avisio")


def in_squadra(su, *persone, cantiere=None):
    cid = (cantiere or avisio(su))["id"]
    for p in persone:
        assert su.post(f"/api/cantieri/{cid}/squadra", json={"persona_id": p["id"]}).status_code == 200
    return cid


def sc(su, cid=None):
    """Spazio comune del cantiere (senza planimetrie è quello generico): ogni attività ne ha uno."""
    cid = cid or avisio(su)["id"]
    return next(a["id"] for a in su.get(f"/api/cantieri/{cid}/scheda").json()["appartamenti"] if a["origine"] == "comune")


def entra_app(nome_utente, password):
    c = TestClient(app)
    r = c.post("/api/login", json={"username": nome_utente, "password": password})
    assert r.status_code == 200, r.text
    return c, r.json(), {"Authorization": f"Bearer {r.json()['token']}"}


def test_crea_assegna_e_allegati(su, tmp_path):
    anna, bruno = nuova_persona(su, "Anna", "Gialli"), nuova_persona(su, "Bruno", "Blu", "EL")
    cid = in_squadra(su, anna, bruno)
    r = su.post("/api/attivita", json={"titolo": "  Verifica   tracce ", "descrizione": "Bagno e cucina\nentro venerdì",
                                       "appartamento_id": sc(su, cid), "persone_ids": [anna["id"], bruno["id"], anna["id"]]})
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
    cid = in_squadra(su, anna, bruno, carla)
    a = su.post("/api/attivita", json={"titolo": "Foto pareti", "appartamento_id": sc(su, cid),
                                       "persone_ids": [anna["id"], bruno["id"]]}).json()
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
    cid = in_squadra(su, anna, carla)
    a = su.post("/api/attivita", json={"titolo": "X", "appartamento_id": sc(su, cid), "persone_ids": [anna["id"]]}).json()
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
    cid = in_squadra(su, anna, bruno)
    a = su.post("/api/attivita", json={"titolo": "X", "persone_ids": [anna["id"]], "appartamento_id": sc(su, cid)}).json()
    r = su.patch(f"/api/attivita/{a['id']}", json={"persone_ids": [bruno["id"]], "titolo": "Y"})
    assert [p["nome"] for p in r.json()["assegnatari"]] == ["Bruno"] and r.json()["titolo"] == "Y"
    assert r.json()["cantiere"]["id"] == cid  # non toccato
    # l'appartamento non si toglie
    assert su.patch(f"/api/attivita/{a['id']}", json={"appartamento_id": None}).status_code == 400
    _, d, _ = entra_app("anna.gialli", anna["password"])
    assert d["attivita"] == []
    assert su.patch(f"/api/attivita/{a['id']}", json={"persone_ids": [999]}).status_code == 400


def test_validazioni(su):
    assert su.post("/api/attivita", json={"titolo": "  "}).status_code == 422
    assert su.post("/api/attivita", json={"titolo": "X", "appartamento_id": 999}).status_code == 400
    assert su.post("/api/attivita", json={"titolo": "X"}).status_code == 422  # serve l'appartamento
    a = su.post("/api/attivita", json={"titolo": "X", "appartamento_id": sc(su)}).json()
    for nome in ("virus.exe", "pagina.html", "script.BAT"):
        assert su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", (nome, b"x", "x/y"))]).status_code == 400
    assert su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", ("vuoto.jpg", b"", "x/y"))]).status_code == 400
    r = su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", ("../../fuori.pdf", b"x", "x/y"))])
    assert r.status_code == 201 and r.json()["caricati"][0]["nome"] == "fuori.pdf"
    assert su.post("/api/attivita/999/allegati", files=[("file", ("a.jpg", b"x", "x/y"))]).status_code == 404


def test_elimina_allegato_e_attivita(su, tmp_path):
    a = su.post("/api/attivita", json={"titolo": "X", "appartamento_id": sc(su)}).json()
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
    a = su.post("/api/attivita", json={"titolo": "X", "appartamento_id": sc(su, in_squadra(su, anna)),
                                       "persone_ids": [anna["id"]]}).json()
    su.delete(f"/api/persone/{anna['id']}")
    assert su.get(f"/api/attivita/{a['id']}").json()["assegnatari"] == []


def test_solo_superuser_gestisce(su):
    anna = nuova_persona(su, "Anna", "Gialli")
    _, _, h = entra_app("anna.gialli", anna["password"])
    c = TestClient(app)
    assert c.get("/api/attivita").status_code == 401
    assert c.post("/api/attivita", json={"titolo": "X"}, headers=h).status_code == 401
    assert c.get("/attivita", follow_redirects=False).status_code == 303
    r = su.get("/attivita", follow_redirects=False)  # pagina tolta: rimanda ai cantieri
    assert r.status_code == 303 and r.headers["location"] == "/cantieri"


def test_tipo_da_nome():
    assert att.tipo_da_nome("A.HEIC") == "foto" and att.tipo_da_nome("x.glb") == "scansione3d"
    assert att.tipo_da_nome("pianta.dxf") == "file"


def test_solo_persone_della_squadra(su):
    anna, bruno = nuova_persona(su, "Anna", "Gialli"), nuova_persona(su, "Bruno", "Blu")
    cid = in_squadra(su, anna)
    r = su.post("/api/attivita", json={"titolo": "X", "appartamento_id": sc(su, cid), "persone_ids": [anna["id"], bruno["id"]]})
    assert r.status_code == 400 and "Bruno Blu" in r.json()["detail"]
    assert su.post("/api/attivita", json={"titolo": "X", "persone_ids": [anna["id"]]}).status_code == 422
    a = su.post("/api/attivita", json={"titolo": "X", "appartamento_id": sc(su, cid), "persone_ids": [anna["id"]]}).json()
    assert su.patch(f"/api/attivita/{a['id']}", json={"persone_ids": [bruno["id"]]}).status_code == 400
    # spostarla in un cantiere dove Anna non c'è: rifiutato
    altro = next(c for c in su.get("/api/cantieri").json() if c["nome"] == "Via Roma 12")
    r = su.patch(f"/api/attivita/{a['id']}", json={"appartamento_id": sc(su, altro["id"])})
    assert r.status_code == 400 and "Anna" in r.json()["detail"]
    # chi esce dalla squadra perde le attività del cantiere
    su.delete(f"/api/cantieri/{cid}/squadra/{anna['id']}")
    assert su.get(f"/api/attivita/{a['id']}").json()["assegnatari"] == []


def test_stato_aperta_da_verificare_chiusa(su):
    anna, bruno = nuova_persona(su, "Anna", "Gialli", "EL"), nuova_persona(su, "Bruno", "Blu", "EL")
    cid = in_squadra(su, anna, bruno)
    a = su.post("/api/attivita", json={"titolo": "Tracce", "appartamento_id": sc(su, cid), "persone_ids": [anna["id"]]}).json()
    assert a["stato"] == "aperta" and a["stato_nome"] == "Aperta" and a["storico_stati"] == []
    c, _, h = entra_app("anna.gialli", anna["password"])
    # l'operatore assegnato: non chiude senza prenderla in carico, non la mette da verificare
    url = f"/api/mie-attivita/{a['id']}/stato"
    assert c.post(url, json={"stato": "chiusa"}, headers=h).status_code == 403
    assert c.post(url, json={"stato": "da_verificare"}, headers=h).status_code == 403
    assert c.post(url, json={"stato": "finita"}, headers=h).status_code == 422
    # la prende in carico e la chiude con una nota
    assert c.post(url, json={"stato": "presa_in_carico"}, headers=h).status_code == 200
    r = c.post(url, json={"stato": "chiusa", "nota": "Fatto, vedi foto"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["stato"] == "chiusa" and r.json()["storico_stati"][-1]["chi"] == "Anna Gialli"
    assert c.post(url, json={"stato": "chiusa"}, headers=h).status_code == 409
    # da chiusa non la riapre, non la rimette da verificare
    assert c.post(url, json={"stato": "da_verificare"}, headers=h).status_code == 403
    assert c.post(url, json={"stato": "aperta"}, headers=h).status_code == 403
    # chi non è assegnato non la vede
    c2, d2, h2 = entra_app("bruno.blu", bruno["password"])
    assert d2["attivita"] == []
    assert c2.post(f"/api/mie-attivita/{a['id']}/stato", json={"stato": "aperta"}, headers=h2).status_code == 404
    # il DL della squadra (Mario Rossi) la riceve anche se non assegnato e la rimette da verificare
    mario = persona(su, "Mario")
    pw = su.post(f"/api/persone/{mario['id']}/password").json()["password"]
    c3, d3, h3 = entra_app("mario.rossi", pw)
    assert [x["titolo"] for x in d3["attivita"]] == ["Tracce"]
    r = c3.post(url, json={"stato": "da_verificare", "nota": "Manca la presa"}, headers=h3)
    assert r.status_code == 200 and r.json()["stato"] == "da_verificare"
    # Anna la vede da verificare, ancora a suo nome, e la richiude
    x = c.get("/api/miei-dati", headers=h).json()["attivita"][0]
    assert x["stato"] == "da_verificare" and x["in_carico"]["chi"] == "Anna Gialli"
    assert c.post(url, json={"stato": "chiusa", "nota": "Presa montata"}, headers=h).status_code == 200
    # il superuser la riapre; lo storico tiene tutto
    r = su.post(f"/api/attivita/{a['id']}/stato", json={"stato": "aperta", "nota": "Da rifare"})
    assert [(x["stato"], x["chi"]) for x in r.json()["storico_stati"]] == [
        ("presa_in_carico", "Anna Gialli"), ("chiusa", "Anna Gialli"), ("da_verificare", "Mario Rossi"),
        ("chiusa", "Anna Gialli"), ("aperta", "Superuser")]
    assert r.json()["storico_stati"][2]["nota"] == "Manca la presa"
    assert c.get("/api/miei-dati", headers=h).json()["attivita"][0]["stato"] == "aperta"


def test_migrazione_stato_attivita_esistenti(tmp_path, monkeypatch):
    import sqlite3
    from app import db
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "vecchio.db")
    con = sqlite3.connect(tmp_path / "vecchio.db")
    con.executescript("""CREATE TABLE attivita (id INTEGER PRIMARY KEY AUTOINCREMENT, titolo TEXT NOT NULL,
        descrizione TEXT NOT NULL DEFAULT '', cantiere_id INTEGER, creata TEXT NOT NULL, modificata TEXT NOT NULL);
        INSERT INTO attivita (titolo, creata, modificata) VALUES ('Vecchia', '2026-11-01', '2026-11-01');""")
    con.commit(); con.close()
    db.inizializza()
    assert [(a["titolo"], a["stato"]) for a in att.elenco()] == [("Vecchia", "aperta")]


def test_presa_in_carico(su):
    anna, bruno = nuova_persona(su, "Anna", "Gialli", "EL"), nuova_persona(su, "Bruno", "Blu", "EL")
    cid = in_squadra(su, anna, bruno)
    a = su.post("/api/attivita", json={"titolo": "Tracce", "appartamento_id": sc(su, cid),
                                       "persone_ids": [anna["id"], bruno["id"]]}).json()
    assert a["in_carico"] is None
    c, _, h = entra_app("anna.gialli", anna["password"])
    url = f"/api/mie-attivita/{a['id']}/stato"
    r = c.post(url, json={"stato": "presa_in_carico"}, headers=h)
    assert r.status_code == 200, r.text
    x = r.json()
    assert x["stato"] == "presa_in_carico" and x["stato_nome"] == "Presa in carico" and x["in_carico"]["chi"] == "Anna Gialli"
    # Bruno la vede già presa: non la riprende (stesso stato), non la chiude né la rilascia
    c2, d2, h2 = entra_app("bruno.blu", bruno["password"])
    assert d2["attivita"][0]["in_carico"]["chi"] == "Anna Gialli"
    assert c2.post(url, json={"stato": "presa_in_carico"}, headers=h2).status_code == 409
    r = c2.post(url, json={"stato": "chiusa"}, headers=h2)
    assert r.status_code == 403 and "Anna Gialli" in r.json()["detail"]
    assert c2.post(url, json={"stato": "aperta"}, headers=h2).status_code == 403
    # rilascia → aperta, nessun incaricato; Bruno la prende e la chiude
    x = c.post(url, json={"stato": "aperta"}, headers=h).json()
    assert x["stato"] == "aperta" and x["in_carico"] is None
    assert c2.post(url, json={"stato": "presa_in_carico"}, headers=h2).status_code == 200
    x = c2.post(url, json={"stato": "chiusa"}, headers=h2).json()
    # chiusa: in_carico non si mostra più
    assert x["stato"] == "chiusa" and x["in_carico"] is None
    # il superuser la rimette da verificare: torna a nome di Bruno, che la richiude
    x = su.post(f"/api/attivita/{a['id']}/stato", json={"stato": "da_verificare"}).json()
    assert x["in_carico"]["chi"] == "Bruno Blu"
    assert c.post(url, json={"stato": "chiusa"}, headers=h).status_code == 403
    assert c2.post(url, json={"stato": "chiusa"}, headers=h2).status_code == 200


def test_note_e_storico(su):
    anna, bruno = nuova_persona(su, "Anna", "Gialli", "EL"), nuova_persona(su, "Bruno", "Blu", "EL")
    cid = in_squadra(su, anna, bruno)
    a = su.post("/api/attivita", json={"titolo": "Tracce", "appartamento_id": sc(su, cid), "persone_ids": [anna["id"]]}).json()
    assert a["note"] == []
    c, _, h = entra_app("anna.gialli", anna["password"])
    r = c.post(f"/api/mie-attivita/{a['id']}/note", json={"testo": "  Serve la scala alta  "}, headers=h)
    assert r.status_code == 201 and r.json()["stato"] == "aperta"
    assert [(n["chi"], n["testo"]) for n in r.json()["note"]] == [("Anna Gialli", "Serve la scala alta")]
    x = su.post(f"/api/attivita/{a['id']}/note", json={"testo": "Scala in magazzino"}).json()
    assert [n["chi"] for n in x["note"]] == ["Anna Gialli", "Superuser"]
    assert su.post(f"/api/attivita/{a['id']}/note", json={"testo": " "}).status_code == 422
    # chi non vede l'attività non scrive note
    c2, _, h2 = entra_app("bruno.blu", bruno["password"])
    assert c2.post(f"/api/mie-attivita/{a['id']}/note", json={"testo": "x"}, headers=h2).status_code == 404
    # le note arrivano all'app; quelle dei cambi di stato restano nello storico
    c.post(f"/api/mie-attivita/{a['id']}/stato", json={"stato": "presa_in_carico", "nota": "Comincio domani"}, headers=h)
    d = c.get("/api/miei-dati", headers=h).json()["attivita"][0]
    assert len(d["note"]) == 2 and d["storico_stati"][-1]["nota"] == "Comincio domani"
