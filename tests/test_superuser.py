import re

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.sicurezza import COOKIE
from conftest import PASSWORD_SUPERUSER_TEST, client_superuser


@pytest.fixture(autouse=True)
def archivio(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "API_KEY", None)
    monkeypatch.setattr(config, "PIANTE_DIR", tmp_path / "piante")
    (tmp_path / "piante").mkdir()
    (tmp_path / "piante" / "a.dxf").write_text("0\nEOF\n")


def test_superuser_creato_con_password_nel_file(tmp_path):
    anonimo = TestClient(app)
    anonimo.get("/login")
    testo = (tmp_path / "credenziali_iniziali.txt").read_text(encoding="utf-8")
    pw = re.search(r"utente: superuser\s+password: (\S+)", testo).group(1)
    r = anonimo.post("/login", data={"username": "superuser", "password": pw}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cantieri"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


@pytest.mark.parametrize("percorso", ["/", "/cantieri", "/docs"])
def test_pagine_rimandano_al_login(percorso):
    r = TestClient(app).get(percorso, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/login?dopo={percorso}"
    assert client_superuser().get(percorso).status_code == 200


def test_login_errato():
    client_superuser()  # imposta la password nota
    c = TestClient(app)
    r = c.post("/login", data={"username": "superuser", "password": "sbagliata"}, follow_redirects=False)
    assert r.status_code == 401 and "Utente o password errati" in r.text and COOKIE not in c.cookies
    r = c.post("/login", data={"username": "mario.rossi", "password": PASSWORD_SUPERUSER_TEST}, follow_redirects=False)
    assert r.status_code == 401
    assert c.get("/api/cantieri").status_code == 401


def test_api_di_gestione_solo_superuser():
    anonimo = TestClient(app)
    assert anonimo.get("/api/persone").status_code == 401
    assert anonimo.post("/api/cantieri", json={"nome": "X"}).status_code == 401
    assert anonimo.post("/api/piante", files=[("file", ("b.dxf", b"x", "x/y"))]).status_code == 401
    assert anonimo.get("/openapi.json").status_code == 401
    su = client_superuser()
    assert su.get("/api/persone").status_code == 200
    assert su.post("/api/cantieri", json={"nome": "X"}).status_code == 201
    assert su.get("/openapi.json").json()["info"]["title"] == "ARworksite Server"


def test_app_legge_le_piante_con_il_suo_token():
    su = client_superuser()
    nuovo = su.post("/api/persone", json={"nome": "Anna", "cognome": "Gialli", "ruolo": "CC"}).json()
    app_client = TestClient(app)
    assert app_client.get("/api/piante").status_code == 401
    token = app_client.post("/api/login", json={"username": "anna.gialli", "password": nuovo["password"]}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    elenco = app_client.get("/api/piante", headers=h)
    assert elenco.status_code == 200 and elenco.json()["totale"] == 1
    assert app_client.get(elenco.json()["piante"][0]["url_download"], headers=h).status_code == 200
    # il token dell'app non apre la gestione
    assert app_client.get("/api/persone", headers=h).status_code == 401
    assert app_client.post("/api/piante", headers=h, files=[("file", ("c.dxf", b"x", "x/y"))]).status_code == 401


def test_logout_e_nuova_password():
    su = client_superuser()
    r = su.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert su.get("/cantieri", follow_redirects=False).status_code == 303

    su = client_superuser()
    db.imposta_password_admin(db.SUPERUSER, "nuova-password")  # chiude le sessioni aperte
    assert su.get("/api/persone").status_code == 401
    c = TestClient(app)
    assert c.post("/login", data={"username": "SuperUser", "password": "nuova-password"},
                  follow_redirects=False).status_code == 303


@pytest.mark.parametrize("dopo, atteso", [("/", "/"), ("/docs", "/docs"), ("//esempio.com", "/cantieri"),
                                           ("https://esempio.com", "/cantieri"), ("/\\esempio.com", "/cantieri")])
def test_niente_rinvii_verso_altri_siti(dopo, atteso):
    client_superuser()
    r = TestClient(app).post("/login", data={"username": "superuser", "password": PASSWORD_SUPERUSER_TEST,
                                             "dopo": dopo}, follow_redirects=False)
    assert r.headers["location"] == atteso


def test_pagina_di_login_escapa_i_valori():
    r = TestClient(app).get('/login?dopo=/"><script>x</script>')
    assert "<script>x" not in r.text
