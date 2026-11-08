import pytest
from fastapi.testclient import TestClient

from app import appartamenti as app_db
from app import config, db, dxf
from app.main import app
from conftest import client_superuser, dxf_di_prova


@pytest.fixture()
def su(tmp_path, monkeypatch):
    piante = tmp_path / "piante"
    (piante / "Lungo Avisio").mkdir(parents=True)
    (piante / "Lungo Avisio" / "APP 3_V1.dxf").write_text(dxf_di_prova(("u.a.01", "u.a.02", "u.a.03", "u.a.04")))
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "PIANTE_DIR", piante)
    monkeypatch.setattr(config, "API_KEY", None)
    return client_superuser()


def ap(s, nome):
    return next(a for a in s["appartamenti"] if a["nome"] == nome)


def cantiere(su, nome="Lungo Avisio"):
    return next(c for c in su.get("/api/cantieri").json() if c["nome"] == nome)


def test_lettura_dxf(tmp_path):
    f = tmp_path / "p.dxf"
    f.write_text(dxf_di_prova(("u.a.03", "App. 1", "Appartamento n. 12", "Sup. 5 mq"), mtext=("{\\fArial|b0;Allog", "gio 7}")))
    assert [(a["nome"], a["etichetta"]) for a in dxf.appartamenti(f)] == [
        ("APP 1", "App. 1"), ("APP 3", "u.a.03"), ("APP 7", "Alloggio 7"), ("APP 12", "Appartamento n. 12")]
    # i testi dentro i blocchi (u.a.09) non contano
    assert "APP 9" not in [a["nome"] for a in dxf.appartamenti(f)]
    assert dxf.id_app("APP 3_V1 (1).dxf", "APP 3") == "dwg:APP 3_V1 (1)/APP 3"
    f.write_bytes(b"AutoCAD Binary DXF\r\n\x1a\x00")
    with pytest.raises(dxf.DxfNonLeggibile):
        dxf.appartamenti(f)


def test_appartamenti_dalla_pianta_e_committente_di_esempio(su):
    c = cantiere(su)
    assert c["n_appartamenti"] == 5
    s = su.get(f"/api/cantieri/{c['id']}/scheda").json()
    assert [a["nome"] for a in s["appartamenti"]] == ["Spazio comune", "APP 1", "APP 2", "APP 3", "APP 4"]
    comune = s["appartamenti"][0]
    assert comune["origine"] == "comune" and comune["pianta"] == "APP 3_V1.dxf"
    assert comune["id_app"] == "dwg:APP 3_V1/Spazio comune"
    app3 = ap(s, "APP 3")
    assert app3["id_app"] == "dwg:APP 3_V1/APP 3" and app3["origine"] == "pianta" and app3["etichetta"] == "u.a.03"
    assert [(p["nome"], p["cognome"], p["ruolo"]) for p in app3["committenti"]] == [("Lorenzo", "Orlandi", "CL")]
    assert [p["nome"] for p in s["piante"]] == ["APP 3_V1.dxf"] and s["piante_non_lette"] == []
    assert s["cantiere"]["direttore_lavori"]["nome"] == "Mario"


def test_carica_pianta_dalla_scheda(su):
    c = cantiere(su, "Via Roma 12")
    # senza planimetrie: solo lo spazio comune generico
    assert [(a["nome"], a["pianta"]) for a in su.get(f"/api/cantieri/{c['id']}/scheda").json()["appartamenti"]] == [
        ("Spazio comune", None)]
    r = su.post(f"/api/cantieri/{c['id']}/piante",
                files=[("file", ("Piano 1.dxf", dxf_di_prova(("App. 1", "App. 2")).encode(), "x/y")),
                       ("file", ("Piano 2.dwg", b"AC1032dati", "x/y"))])
    assert r.status_code == 201, r.text
    s = r.json()
    # uno spazio comune per planimetria (anche DWG); quello generico, vuoto, sparisce
    assert [(a["nome"], a["pianta"]) for a in s["appartamenti"]] == [
        ("Spazio comune", "Piano 1.dxf"), ("APP 1", "Piano 1.dxf"), ("APP 2", "Piano 1.dxf"), ("Spazio comune", "Piano 2.dwg")]
    assert s["piante_non_lette"][0]["nome"] == "Piano 2.dwg"
    assert su.post(f"/api/cantieri/{c['id']}/piante",
                   files=[("file", ("Piano 1.dxf", b"x", "x/y"))]).status_code == 409
    assert su.post(f"/api/cantieri/{c['id']}/piante", files=[("file", ("a.pdf", b"x", "x/y"))]).status_code == 400
    # rileggere non crea doppioni
    assert len(su.get(f"/api/cantieri/{c['id']}/scheda").json()["appartamenti"]) == 4


def test_pianta_caricata_dalla_pagina_piante_aggiorna_il_cantiere(su):
    c = cantiere(su, "Residenza Le Querce")
    su.post("/api/piante", data={"cantiere": "Residenza Le Querce"},
            files=[("file", ("PT.dxf", dxf_di_prova(("u.a.05",)).encode(), "x/y"))])
    assert cantiere(su, "Residenza Le Querce")["n_appartamenti"] == 2  # APP 5 + spazio comune


def test_cartella_resta_dopo_il_cambio_nome(su):
    c = cantiere(su)
    su.patch(f"/api/cantieri/{c['id']}", json={"nome": "Lungo Avisio - Lotto A"})
    s = su.get(f"/api/cantieri/{c['id']}/scheda").json()
    assert s["cartella_piante"] == "Lungo Avisio" and len(s["appartamenti"]) == 5


def test_appartamento_manuale_e_committenti(su):
    c = cantiere(su, "Via Roma 12")
    r = su.post(f"/api/cantieri/{c['id']}/appartamenti", json={"nome": " APP  7 "})
    assert r.status_code == 201 and r.json()["nome"] == "APP 7" and r.json()["origine"] == "manuale"
    a = r.json()
    assert su.post(f"/api/cantieri/{c['id']}/appartamenti", json={"nome": "app 7"}).status_code == 409
    anna = su.post("/api/persone", json={"nome": "Anna", "cognome": "Gialli", "ruolo": "CL"}).json()
    r = su.post(f"/api/appartamenti/{a['id']}/committenti", json={"persona_id": anna["id"]})
    assert [p["nome"] for p in r.json()["committenti"]] == ["Anna"]
    assert su.post(f"/api/appartamenti/{a['id']}/committenti", json={"persona_id": anna["id"]}).status_code == 409
    assert su.delete(f"/api/appartamenti/{a['id']}/committenti/{anna['id']}").json()["committenti"] == []
    assert su.delete(f"/api/appartamenti/{a['id']}").status_code == 204
    # quelli letti dalla pianta non si eliminano
    s = su.get(f"/api/cantieri/{cantiere(su)['id']}/scheda").json()
    assert su.delete(f"/api/appartamenti/{ap(s, 'APP 1')['id']}").status_code == 409
    # e neanche lo spazio comune
    assert su.delete(f"/api/appartamenti/{ap(s, 'Spazio comune')['id']}").status_code == 409


def test_attivita_con_appartamento(su):
    c = cantiere(su)
    s = su.get(f"/api/cantieri/{c['id']}/scheda").json()
    app3 = ap(s, "APP 3")
    paolo = next(p for p in su.get("/api/persone").json() if p["nome"] == "Paolo")
    r = su.post("/api/attivita", json={"titolo": "Tracce bagno", "appartamento_id": app3["id"], "persone_ids": [paolo["id"]]})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["cantiere"]["id"] == c["id"] and a["appartamento"]["nome"] == "APP 3"
    assert a["appartamento"]["id_app"] == "dwg:APP 3_V1/APP 3"
    s = su.get(f"/api/cantieri/{c['id']}/scheda").json()
    assert [x["titolo"] for x in s["attivita"]] == ["Tracce bagno"] and ap(s, "APP 3")["n_attivita"] == 1
    # appartamento di un altro cantiere: rifiutato
    altro = cantiere(su, "Via Roma 12")
    assert su.post("/api/attivita", json={"titolo": "X", "cantiere_id": altro["id"],
                                          "appartamento_id": app3["id"]}).status_code == 400
    # senza appartamento non si crea; non si può togliere; cambiare solo il cantiere non basta
    assert su.post("/api/attivita", json={"titolo": "X", "cantiere_id": c["id"]}).status_code == 422
    assert su.patch(f"/api/attivita/{a['id']}", json={"appartamento_id": None}).status_code == 400
    assert su.patch(f"/api/attivita/{a['id']}", json={"cantiere_id": altro["id"]}).status_code == 400
    # spostata sullo spazio comune di un altro cantiere (Paolo deve essere anche nella nuova squadra)
    su.post(f"/api/cantieri/{altro['id']}/squadra", json={"persona_id": paolo["id"]})
    comune = ap(su.get(f"/api/cantieri/{altro['id']}/scheda").json(), "Spazio comune")
    r = su.patch(f"/api/attivita/{a['id']}", json={"appartamento_id": comune["id"]})
    assert r.json()["cantiere"]["id"] == altro["id"] and r.json()["appartamento"]["nome"] == "Spazio comune"
    # l'app riceve l'id dell'appartamento
    su.patch(f"/api/attivita/{a['id']}", json={"cantiere_id": c["id"], "appartamento_id": app3["id"]})
    pw = su.post(f"/api/persone/{paolo['id']}/password").json()["password"]
    login = TestClient(app).post("/api/login", json={"username": "paolo.neri", "password": pw}).json()
    assert login["attivita"][0]["appartamento"]["id_app"] == "dwg:APP 3_V1/APP 3"


def test_cantiere_eliminato_porta_via_gli_appartamenti(su):
    c = cantiere(su)
    su.delete(f"/api/cantieri/{c['id']}")
    with db.connessione() as con:
        assert con.execute("SELECT COUNT(*) FROM appartamenti WHERE cantiere_id = ?", (c["id"],)).fetchone()[0] == 0


def test_lorenzo_non_torna_se_eliminato(su, tmp_path):
    lorenzo = next(p for p in su.get("/api/persone").json() if p["nome"] == "Lorenzo")
    su.delete(f"/api/persone/{lorenzo['id']}")
    app_db.inizializza()
    assert not any(p["nome"] == "Lorenzo" for p in su.get("/api/persone").json())


def test_pagina_scheda(su):
    c = cantiere(su)
    assert su.get(f"/cantieri/{c['id']}").status_code == 200
    assert TestClient(app).get(f"/cantieri/{c['id']}", follow_redirects=False).status_code == 303
    assert TestClient(app).get(f"/api/cantieri/{c['id']}/scheda").status_code == 401


def _entra(su, nome):
    p = next(p for p in su.get("/api/persone").json() if p["nome"] == nome)
    pw = su.post(f"/api/persone/{p['id']}/password").json()["password"]
    c = TestClient(app)
    r = c.post("/api/login", json={"username": p["username"], "password": pw})
    assert r.status_code == 200, r.text
    return c, r.json(), {"Authorization": f"Bearer {r.json()['token']}"}


def test_login_committente_riceve_solo_il_suo_appartamento(su):
    c, d, h = _entra(su, "Lorenzo")
    assert [x["nome"] for x in d["cantieri"]] == ["Lungo Avisio"]
    k = d["cantieri"][0]
    assert k["accesso"] == "committente"
    assert [a["nome"] for a in k["appartamenti"]] == ["APP 3"] and k["appartamenti"][0]["committente"] is True
    assert k["appartamenti"][0]["id_app"] == "dwg:APP 3_V1/APP 3"
    assert [m["ruolo"] for m in k["squadra"]] == ["DL"]  # della squadra vede solo il DL
    assert [(p["nome"], p["cantiere"]) for p in k["piante"]] == [("APP 3_V1.dxf", "Lungo Avisio")]
    assert k["appartamenti"][0]["pianta_id"] == k["piante"][0]["id"]
    r = c.get(k["piante"][0]["url_download"], headers=h)
    assert r.status_code == 200 and b"u.a.03" in r.content


def test_login_squadra_riceve_tutto_il_cantiere(su):
    c, d, h = _entra(su, "Paolo")
    k = next(x for x in d["cantieri"] if x["nome"] == "Lungo Avisio")
    assert k["accesso"] == "squadra" and len(k["appartamenti"]) == 5 and len(k["piante"]) == 1
    assert k["appartamenti"][0]["origine"] == "comune"
    assert not any(a["committente"] for a in k["appartamenti"])
    assert len(k["squadra"]) > 1


def test_miei_dati_e_versione(su):
    c, d, h = _entra(su, "Paolo")
    r = c.get("/api/miei-dati", headers=h)
    assert r.status_code == 200 and r.json()["versione"] == d["versione"]
    assert r.headers["etag"] == f'"{d["versione"]}"'
    assert c.get("/api/miei-dati", headers={**h, "If-None-Match": r.headers["etag"]}).status_code == 304
    # nuova attività per Paolo: la versione cambia
    paolo = d["persona"]
    comune = ap(su.get(f"/api/cantieri/{cantiere(su)['id']}/scheda").json(), "Spazio comune")
    su.post("/api/attivita", json={"titolo": "Nuova", "appartamento_id": comune["id"], "persone_ids": [paolo["id"]]})
    r2 = c.get("/api/miei-dati", headers={**h, "If-None-Match": r.headers["etag"]})
    assert r2.status_code == 200 and r2.json()["versione"] != d["versione"]
    assert [a["titolo"] for a in r2.json()["attivita"]] == ["Nuova"]
    # nuovo appartamento: la versione cambia ancora
    su.post(f"/api/cantieri/{cantiere(su)['id']}/appartamenti", json={"nome": "Box 1"})
    assert c.get("/api/miei-dati", headers=h).json()["versione"] != r2.json()["versione"]
    assert TestClient(app).get("/api/miei-dati").status_code == 401


def test_attivita_vecchie_su_app3_o_spazio_comune(su):
    from app import appartamenti as app_db
    avisio, roma = cantiere(su), cantiere(su, "Via Roma 12")
    with db.connessione() as con:  # come le attività create prima di questa versione
        for t, cid in (("Vecchia Avisio", avisio["id"]), ("Vecchia Roma", roma["id"])):
            con.execute("INSERT INTO attivita (titolo, cantiere_id, creata, modificata) VALUES (?, ?, 'x', 'x')", (t, cid))
        con.execute("DELETE FROM impostazioni WHERE chiave = 'attivita_su_appartamenti'")
    app_db.inizializza()
    per_titolo = {a["titolo"]: a for a in su.get("/api/attivita").json()}
    assert per_titolo["Vecchia Avisio"]["appartamento"]["nome"] == "APP 3"
    assert per_titolo["Vecchia Roma"]["appartamento"]["nome"] == "Spazio comune"
    assert per_titolo["Vecchia Roma"]["stato"] == "aperta"


def test_nuovo_cantiere_ha_lo_spazio_comune(su):
    c = su.post("/api/cantieri", json={"nome": "Cantiere Nuovo"}).json()
    assert c["n_appartamenti"] == 1
