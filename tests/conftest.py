from fastapi.testclient import TestClient

from app import db
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
