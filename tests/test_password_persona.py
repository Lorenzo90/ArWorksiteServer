"""imposta_password_persona.py: password scelta per una persona dell'app."""
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from test_raccolta import su  # noqa: F401


def test_password_scelta(su):
    p = su.post("/api/persone", json={"nome": "Efrem", "cognome": "Franceschini", "ruolo": "CG"}).json()
    c = TestClient(app)
    token = c.post("/api/login", json={"username": "efrem.franceschini", "password": p["password"]}).json()["token"]
    assert db.imposta_password("Efrem.Franceschini", "1234")["id"] == p["id"]
    # la vecchia sessione è chiusa, la vecchia password non vale più, la nuova sì
    assert c.get("/api/miei-dati", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert c.post("/api/login", json={"username": "efrem.franceschini", "password": p["password"]}).status_code == 401
    assert c.post("/api/login", json={"username": "efrem.franceschini", "password": "1234"}).status_code == 200
    assert db.imposta_password("nessuno.qui", "1234") is None


def test_script(su, monkeypatch):
    su.post("/api/persone", json={"nome": "Efrem", "cognome": "Franceschini", "ruolo": "CG"})
    radice = Path(__file__).resolve().parent.parent
    env = {"ARWS_DB": str(config.DB_PATH), "ARWS_PIANTE_DIR": str(config.PIANTE_DIR), "PATH": ""}
    r = subprocess.run([sys.executable, "imposta_password_persona.py", "efrem.franceschini", "1234"],
                       cwd=radice, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "aggiornata" in r.stdout, r.stdout + r.stderr
    assert TestClient(app).post("/api/login", json={"username": "efrem.franceschini", "password": "1234"}).status_code == 200
    r = subprocess.run([sys.executable, "imposta_password_persona.py", "nessuno.qui", "1234"],
                       cwd=radice, env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "non trovato" in r.stdout
