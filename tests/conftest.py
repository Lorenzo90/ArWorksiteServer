import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.sicurezza import archivio_pronto

PASSWORD_SUPERUSER_TEST = "prova-superuser"


def client_superuser() -> TestClient:
    """Client già collegato come superuser (cookie di sessione)."""
    archivio_pronto()
    db.imposta_password_admin(db.SUPERUSER, PASSWORD_SUPERUSER_TEST)
    c = TestClient(app)
    r = c.post("/login", data={"username": "superuser", "password": PASSWORD_SUPERUSER_TEST},
               follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


@pytest.fixture(autouse=True)
def piante_isolate(tmp_path, monkeypatch):
    """Ogni test parte senza piante (i test che ne hanno bisogno impostano la propria cartella)."""
    monkeypatch.setattr(config, "PIANTE_DIR", tmp_path / "piante_test")


def dxf_di_prova(etichette_entities=("u.a.01", "u.a.02"), mtext=None, etichette_blocchi=("u.a.09",)) -> str:
    """DXF ASCII minimo: testi nella sezione ENTITIES (letti) e in BLOCKS (ignorati)."""
    righe = ["0", "SECTION", "2", "HEADER", "0", "ENDSEC", "0", "SECTION", "2", "BLOCKS"]
    for t in etichette_blocchi:
        righe += ["0", "TEXT", "8", "0", "10", "0", "20", "0", "1", t]
    righe += ["0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for i, t in enumerate(etichette_entities):
        righe += ["0", "TEXT", "8", "TESTI ESTERNI", "10", str(i * 10), "20", "0", "1", t]
    righe += ["0", "TEXT", "8", "0", "10", "0", "20", "0", "1", "BAGNO"]
    if mtext:
        righe += ["0", "MTEXT", "8", "0", "10", "0", "20", "0", "3", mtext[0], "1", mtext[1]]
    righe += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(righe) + "\n"
