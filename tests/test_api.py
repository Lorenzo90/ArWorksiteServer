from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from conftest import client_superuser


@pytest.fixture()
def client(tmp_path, monkeypatch):
    (tmp_path / "Cantiere A").mkdir()
    (tmp_path / "Cantiere A" / "APP 3 (1).dxf").write_text("0\nSECTION\n0\nEOF\n")
    (tmp_path / "pianta.dwg").write_bytes(b"AC1032")
    (tmp_path / "note.txt").write_text("ignorato")
    monkeypatch.setattr(config, "PIANTE_DIR", tmp_path)
    monkeypatch.setattr(config, "API_KEY", None)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "db" / "test.db")
    return client_superuser()


def test_elenco(client):
    r = client.get("/api/piante")
    assert r.status_code == 200
    d = r.json()
    assert d["totale"] == 2
    nomi = {p["nome"]: p for p in d["piante"]}
    assert nomi["APP 3 (1).dxf"]["cantiere"] == "Cantiere A"
    assert nomi["pianta.dwg"]["formato"] == "dwg"
    assert client.get("/api/piante?formato=dxf").json()["totale"] == 1
    assert client.get("/api/piante?cantiere=cantiere a").json()["totale"] == 1


def test_download_e_etag(client):
    p = next(x for x in client.get("/api/piante").json()["piante"] if x["formato"] == "dxf")
    r = client.get(p["url_download"])
    assert r.status_code == 200
    assert r.content.startswith(b"0\nSECTION")
    assert r.headers["etag"] == f'"{p["sha256"]}"'
    assert "attachment" in r.headers["content-disposition"]
    r2 = client.get(p["url_download"], headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304


def test_404(client):
    assert client.get("/api/piante/inesistente/file").status_code == 404


def test_chiave(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "segreta")
    anonimo = TestClient(app)
    assert anonimo.get("/api/piante").status_code == 401
    assert anonimo.get("/api/piante", headers={"X-Api-Key": "segreta"}).status_code == 200
    assert client.get("/api/health").status_code == 200


def test_caricamento(client, tmp_path):
    r = client.post("/api/piante", data={"cantiere": "Nuovo cantiere"},
                    files=[("file", ("APP 5.dxf", b"0\nSECTION\n", "application/octet-stream")),
                           ("file", ("APP 6.DWG", b"AC1032", "application/octet-stream"))])
    assert r.status_code == 201, r.text
    assert {p["nome"] for p in r.json()["caricate"]} == {"APP 5.dxf", "APP 6.DWG"}
    assert (tmp_path / "Nuovo cantiere" / "APP 5.dxf").read_bytes() == b"0\nSECTION\n"
    assert client.get("/api/piante?cantiere=Nuovo cantiere").json()["totale"] == 2
    assert not list((tmp_path / "Nuovo cantiere").glob("*.tmp"))


def test_caricamento_duplicato_e_sostituzione(client, tmp_path):
    f = lambda c: [("file", ("APP 3 (1).dxf", c, "application/octet-stream"))]
    r = client.post("/api/piante", data={"cantiere": "Cantiere A"}, files=f(b"nuovo"))
    assert r.status_code == 409
    r = client.post("/api/piante", data={"cantiere": "Cantiere A", "sostituisci": "true"}, files=f(b"nuovo"))
    assert r.status_code == 201
    assert (tmp_path / "Cantiere A" / "APP 3 (1).dxf").read_bytes() == b"nuovo"


def test_caricamento_rifiutati(client, tmp_path):
    assert client.post("/api/piante", files=[("file", ("virus.exe", b"x", "x/y"))]).status_code == 400
    assert client.post("/api/piante", files=[("file", ("vuoto.dxf", b"", "x/y"))]).status_code == 400
    r = client.post("/api/piante", data={"cantiere": "../fuori"}, files=[("file", ("a.dxf", b"x", "x/y"))])
    assert r.status_code == 201
    assert (tmp_path / "_fuori" / "a.dxf").exists() or (tmp_path / "fuori" / "a.dxf").exists()
    assert not (tmp_path.parent / "fuori").exists()
    r = client.post("/api/piante", files=[("file", ("../../evil.dxf", b"x", "x/y"))])
    assert r.status_code == 201 and (tmp_path / "evil.dxf").exists()


def test_caricamento_con_chiave(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "segreta")
    files = [("file", ("k.dxf", b"x", "x/y"))]
    anonimo = TestClient(app)
    assert anonimo.post("/api/piante", files=files).status_code == 401
    assert anonimo.post("/api/piante", files=files, headers={"X-Api-Key": "segreta"}).status_code == 201


def test_pagina(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Aggiungi piante" in r.text and '"cantieri": ["Cantiere A"]' in r.text
