import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from app import config, modelli
from app.main import app
from conftest import client_superuser


def dxf_due_locali() -> str:
    """La pianta di prova dell'app (TestPlans.twoRoomApartment) come DXF ASCII: perimetro su
    SUP COMMERCIALI (LWPOLYLINE chiusa), muri su «MURI SOPRA» (layer visibile all'inizio), testi."""
    g = ["0", "SECTION", "2", "ENTITIES"]

    def linea(x1, y1, x2, y2):
        g.extend(["0", "LINE", "8", "MURI SOPRA", "10", str(x1), "20", str(y1), "11", str(x2), "21", str(y2)])

    def testo(v, x, y, h):
        g.extend(["0", "TEXT", "8", "TESTI", "10", str(x), "20", str(y), "40", str(h), "1", v])

    g.extend(["0", "LWPOLYLINE", "8", "SUP COMMERCIALI", "70", "1",
              "10", "0", "20", "0", "10", "10", "20", "0", "10", "10", "20", "8", "10", "0", "20", "8"])
    for s in ((0, 0, 10, 0), (10, 0, 10, 8), (10, 8, 0, 8), (0, 8, 0, 0), (5, 0, 5, 8)):
        linea(*s)
    testo("App. 3", 1, 7.5, 0.3)
    testo("PIANO PRIMO", 5, 9, 0.5)
    testo("CUCINA", 2.5, 4.5, 0.3)
    testo("Sup. 40,00 mq", 2.5, 4.0, 0.2)
    testo("h 2,80", 2.5, 3.6, 0.2)
    testo("CAMERA", 7.5, 4.5, 0.3)
    testo("Sup. 40,00 mq", 7.5, 4.0, 0.2)
    g.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(g) + "\n"


@pytest.fixture()
def su(tmp_path, monkeypatch):
    piante = tmp_path / "piante"
    (piante / "Lungo Avisio").mkdir(parents=True)
    (piante / "Lungo Avisio" / "Casa.dxf").write_text(dxf_due_locali())
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "PIANTE_DIR", piante)
    monkeypatch.setattr(config, "API_KEY", None)
    return client_superuser()


def scheda(su):
    c = next(c for c in su.get("/api/cantieri").json() if c["nome"] == "Lungo Avisio")
    return su.get(f"/api/cantieri/{c['id']}/scheda").json()


def aspetta_modello(su, id_pianta):
    for _ in range(100):
        r = su.get(f"/api/piante/{id_pianta}/modello")
        if r.status_code != 202:
            return r
        time.sleep(0.1)
    raise AssertionError("modello mai pronto")


def test_modello_della_pianta(su):
    s = scheda(su)
    assert [a["nome"] for a in s["appartamenti"]] == ["Spazio comune", "APP 3"]
    r = aspetta_modello(su, s["piante"][0]["id"])
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["titolo"] == "PIANO PRIMO"
    vis = {l["nome"]: l["visibile"] for l in m["layers"]}
    assert vis["MURI SOPRA"] is True and vis["SUP COMMERCIALI"] is False
    ap = m["appartamenti"][0]
    assert ap["nome"] == "APP 3" and ap["area_perimetro"] == 80
    assert [l["nome"] for l in ap["locali"]] == ["Cucina", "Camera"]
    assert ap["locali"][0]["altezza"] == 2.8
    pareti = ap["locali"][0]["pareti"]
    assert [p["nome"] for p in pareti] == ["Parete 1", "Parete 2", "Parete 3", "Parete 4"]
    assert sorted(round(p["lunghezza"]) for p in pareti) == [5, 5, 8, 8]
    # salvato: la seconda volta arriva subito
    assert su.get(f"/api/piante/{s['piante'][0]['id']}/modello").status_code == 200
    assert TestClient(app).get(f"/api/piante/{s['piante'][0]['id']}/modello").status_code == 401


def test_pianta_dwg_non_leggibile(su, tmp_path):
    (tmp_path / "piante" / "Lungo Avisio" / "Altra.dwg").write_bytes(b"AC1032" + b"\0" * 100)
    p = next(x for x in scheda(su)["piante"] if x["nome"] == "Altra.dwg")
    r = su.get(f"/api/piante/{p['id']}/modello")
    assert r.status_code == 422 and "DWG" in r.json()["detail"]


def test_esplora_come_pannello_app(su):
    s = scheda(su)
    app3 = next(a for a in s["appartamenti"] if a["nome"] == "APP 3")
    aspetta_modello(su, s["piante"][0]["id"])

    def put(percorso, dati):
        r = su.put(f"/api/appartamenti/{app3['id']}/raccolta/{percorso}", content=dati,
                   headers={"X-Sha256": hashlib.sha256(dati).hexdigest()})
        assert r.status_code in (200, 201), r.text

    put("appartamento.json", b"{}")
    put("foto/IMG_1.jpg", b"\xff\xd8a")
    put("Cucina/report.json", b"{}")
    put("Cucina/Parete 2/1/wall.jpg", b"\xff\xd8b")
    put("Cucina/Parete 2/1/metadata.json", b'{"name":"Scansione 1","widthMeters":5.0,"heightMeters":2.8}')
    put("Cucina/Parete 2/foto/IMG_2.jpg", b"\xff\xd8c")
    put("Cucina/Parete 2/foto/IMG_3.png", b"\x89PNGd")
    put("Cucina/Parete 2/file/Impianti/Elettrico/schema.pdf", b"%PDF")
    put("Cucina/Soffitto/foto/IMG_4.jpg", b"\xff\xd8e")
    put("Bagno vecchio/foto/IMG_5.jpg", b"\xff\xd8f")  # locale che non c'è più nella pianta: si vede lo stesso
    # allegato di un'attività con riferimento alla parete
    a = su.post("/api/attivita", json={"titolo": "Tracce", "appartamento_id": app3["id"]}).json()
    su.post(f"/api/attivita/{a['id']}/allegati", files=[("file", ("prova.jpg", b"\xff\xd8g", "image/jpeg"))])

    r = su.get(f"/api/appartamenti/{app3['id']}/esplora")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["modello"] == "pronto" and d["appartamento"]["id_app"] == "dwg:Casa/APP 3"
    nodi = {n["percorso"]: n for n in d["nodi"]}
    radice = nodi[""]
    assert radice["figli"] == ["Bagno vecchio", "Camera", "Cucina"]
    assert radice["conteggi"] == {"scansioni": 1, "foto": 5, "file": 1, "allegati": 0}
    assert [f["percorso"] for f in radice["foto"]] == ["foto/IMG_1.jpg"]
    assert [f["percorso"] for f in radice["altri"]] == ["appartamento.json"]
    cucina = nodi["Cucina"]
    assert cucina["figli"] == ["Cucina/Parete 1", "Cucina/Parete 2", "Cucina/Parete 3", "Cucina/Parete 4",
                               "Cucina/Soffitto", "Cucina/Pavimento"]
    assert cucina["conteggi"]["foto"] == 3 and cucina["dal_modello"] is True
    p2 = nodi["Cucina/Parete 2"]
    assert p2["tipo"] == "parete" and p2["conteggi"] == {"scansioni": 1, "foto": 2, "file": 1, "allegati": 0}
    sc = p2["scansioni"][0]
    assert sc["cartella"] == "1" and sc["larghezza"] == 5.0 and sc["immagine"]["url_download"].endswith("/1/wall.jpg")
    assert p2["file"][0]["sottocartella"] == "Impianti/Elettrico"
    assert nodi["Cucina/Soffitto"]["tipo"] == "superficie" and nodi["Cucina/Soffitto"]["conteggi"]["foto"] == 1
    assert nodi["Camera/Parete 3"]["conteggi"]["foto"] == 0  # vuota ma presente (dal modello)
    assert nodi["Bagno vecchio"]["dal_modello"] is False
    assert [x["titolo"] for x in d["attivita"]] == ["Tracce"]


def test_pagina_vista(su):
    assert TestClient(app).get("/cantieri/1/vista", follow_redirects=False).status_code == 303
    r = su.get("/cantieri/1/vista")
    assert r.status_code == 200 and "Vista cantiere" in r.text and '"cantiere": 1' in r.text
