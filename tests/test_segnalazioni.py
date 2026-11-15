"""Segnalazioni dal telefono: contenuto + descrizione mandato ad alcune persone del cantiere."""
from test_raccolta import app3, entra, su  # noqa: F401  (fixture con la pianta di prova)


def segnala(c, h, aid, descrizione="Crepa sopra la porta", persone="", file=None, **altro):
    files = [("file", f) for f in (file or [])]
    data = {"appartamento_id": str(aid), "descrizione": descrizione, "persone_ids": persone, **altro}
    return c.post("/api/mie-segnalazioni", data=data, files=files or None, headers=h)


def miei(c, h):
    return {a["id"]: a for a in c.get("/api/mie-attivita", headers=h).json()["attivita"]}


def test_segnalazione_ad_alcune_persone(su):
    ca, da, ha, _ = entra(su, "Anna")
    cb, db_, hb, _ = entra(su, "Bruno")
    cc, dc, hc, _ = entra(su, "Carlo")
    aid = app3(da)["id"]
    r = segnala(ca, ha, aid, persone=str(db_["persona"]["id"]),
                file=[("IMG_1.jpg", b"\xff\xd8foto", "image/jpeg")],
                riferimento="dwg:APP 3_V1/APP 3/Soggiorno/Parete 2|Soggiorno/Parete 2/foto/IMG_1.jpg")
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["autore"]["id"] == da["persona"]["id"]
    assert [p["id"] for p in s["assegnatari"]] == [db_["persona"]["id"]]
    assert s["titolo"] == "Crepa sopra la porta" and s["stato"] == "aperta"
    assert s["appartamento"]["id"] == aid
    assert s["allegati"][0]["nome"] == "IMG_1.jpg" and s["allegati"][0]["riferimento"].endswith("IMG_1.jpg")
    # la vedono l'autore e Bruno, non Carlo
    assert s["id"] in miei(ca, ha) and s["id"] in miei(cb, hb) and s["id"] not in miei(cc, hc)
    assert any(a["id"] == s["id"] for a in cb.get("/api/miei-dati", headers=hb).json()["attivita"])
    assert cc.post(f"/api/mie-attivita/{s['id']}/stato", json={"stato": "presa_in_carico"}, headers=hc).status_code == 404


def test_vuoto_tutto_il_cantiere_e_controlli(su):
    ca, da, ha, _ = entra(su, "Anna")
    cb, db_, hb, _ = entra(su, "Bruno")
    _, dx, _, _ = entra(su, "Estraneo", squadra=False)
    aid = app3(da)["id"]
    s = segnala(ca, ha, aid).json()
    ids = {p["id"] for p in s["assegnatari"]}
    assert db_["persona"]["id"] in ids and da["persona"]["id"] not in ids
    assert segnala(ca, ha, aid, descrizione="   ").status_code == 422
    assert segnala(ca, ha, aid, persone=str(dx["persona"]["id"])).status_code == 422
    assert segnala(ca, ha, 99999).status_code == 404
    assert segnala(ca, ha, aid, file=[("virus.exe", b"MZ", "application/octet-stream")]).status_code == 400
    assert len([a for a in miei(ca, ha).values() if a["autore"]]) == 1  # quella col file vietato non resta


def test_stati_autore_e_assegnatario(su):
    ca, da, ha, _ = entra(su, "Anna")
    cb, db_, hb, _ = entra(su, "Bruno")
    aid = app3(da)["id"]
    sid = segnala(ca, ha, aid, persone=str(db_["persona"]["id"])).json()["id"]
    stato = lambda c, h, x: c.post(f"/api/mie-attivita/{sid}/stato", json={"stato": x}, headers=h).status_code
    assert stato(cb, hb, "presa_in_carico") == 200
    assert stato(ca, ha, "chiusa") == 403          # in carico a Bruno
    assert stato(cb, hb, "chiusa") == 200
    assert stato(cb, hb, "aperta") == 403          # chi è assegnato non riapre
    assert stato(ca, ha, "aperta") == 200          # l'autore sì
    assert stato(ca, ha, "da_verificare") == 403
