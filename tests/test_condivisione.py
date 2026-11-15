"""Condivisione per persona dei dati raccolti (file .condivisione.json / condivisione.json dell'app)."""
import json
import logging

from fastapi.testclient import TestClient

from app.main import app
from test_raccolta import app3, entra, put, su  # noqa: F401  (fixture con la pianta di prova)

FOTO = "Soggiorno/Parete 2/foto/IMG_1731.jpg"
SCANSIONE = "Soggiorno/Parete 2/3"


def condivisione(*persone, autore=None) -> bytes:
    return json.dumps({"persone": list(persone), "autore": autore if autore is not None else persone[0],
                       "modificato": 1794739513528}).encode()


def percorsi(c, h, aid) -> set[str]:
    r = c.get(f"/api/appartamenti/{aid}/raccolta", headers=h)
    assert r.status_code == 200, r.text
    return {f["percorso"] for f in r.json()["file"]}


def due_persone(su):
    ca, da, ha, _ = entra(su, "Anna")
    cb, db_, hb, _ = entra(su, "Bruno")
    return (ca, ha, da["persona"]["id"]), (cb, hb, db_["persona"]["id"]), app3(da)["id"]


def foto_di_anna(ca, ha, aid, *persone):
    assert put(ca, ha, aid, FOTO, b"\xff\xd8foto").status_code == 201
    assert put(ca, ha, aid, FOTO + ".annotazioni.json", b'{"note":[]}').status_code == 201
    assert put(ca, ha, aid, FOTO + ".condivisione.json", condivisione(*persone)).status_code == 201


def test_prova_1_2_3_foto_condivisa_tolta_e_rimessa(su):
    (ca, ha, anna), (cb, hb, bruno), aid = due_persone(su)
    tre = {FOTO, FOTO + ".annotazioni.json", FOTO + ".condivisione.json"}
    # 1. solo Anna: Bruno non la riceve (né la foto, né le note, né la condivisione)
    foto_di_anna(ca, ha, aid, anna)
    assert tre <= percorsi(ca, ha, aid)
    assert not tre & percorsi(cb, hb, aid)
    for p in tre:
        assert cb.get(f"/api/appartamenti/{aid}/raccolta/{p}", headers=hb).status_code == 404
    # 2. Anna la condivide anche con Bruno: la riceve con le note
    assert put(ca, ha, aid, FOTO + ".condivisione.json", condivisione(anna, bruno)).status_code == 200
    assert tre <= percorsi(cb, hb, aid)
    assert cb.get(f"/api/appartamenti/{aid}/raccolta/{FOTO}", headers=hb).content == b"\xff\xd8foto"
    versione_con = cb.get(f"/api/appartamenti/{aid}/raccolta", headers=hb).json()["versione"]
    # 3. Anna toglie Bruno: al suo aggiornamento sparisce (e la versione cambia, niente 304)
    assert put(ca, ha, aid, FOTO + ".condivisione.json", condivisione(anna)).status_code == 200
    assert not tre & percorsi(cb, hb, aid)
    r = cb.get(f"/api/appartamenti/{aid}/raccolta", headers={**hb, "If-None-Match": f'"{versione_con}"'})
    assert r.status_code == 200


def test_prova_4_senza_file_la_vedono_tutti(su):
    (ca, ha, anna), (cb, hb, bruno), aid = due_persone(su)
    assert put(ca, ha, aid, "Soggiorno/Parete 2/1/wall.jpg", b"\xff\xd8vecchia").status_code == 201
    assert put(ca, ha, aid, "Soggiorno/Parete 2/1/metadata.json", b"{}").status_code == 201
    assert {"Soggiorno/Parete 2/1/wall.jpg", "Soggiorno/Parete 2/1/metadata.json"} <= percorsi(cb, hb, aid)
    # «Tutti del cantiere»: l'app cancella il file di condivisione e il contenuto torna visibile
    foto_di_anna(ca, ha, aid, anna)
    assert ca.delete(f"/api/appartamenti/{aid}/raccolta/{FOTO}.condivisione.json", headers=ha).status_code == 204
    assert FOTO in percorsi(cb, hb, aid)


def test_scansione_tutta_la_cartella(su):
    (ca, ha, anna), (cb, hb, bruno), aid = due_persone(su)
    for nome, dati in (("wall.jpg", b"\xff\xd8s"), ("metadata.json", b"{}"), ("note.json", b"[]")):
        assert put(ca, ha, aid, f"{SCANSIONE}/{nome}", dati).status_code == 201
    assert put(ca, ha, aid, f"{SCANSIONE}/condivisione.json", condivisione(anna)).status_code == 201
    assert put(ca, ha, aid, "Soggiorno/Parete 2/4/wall.jpg", b"\xff\xd8altra").status_code == 201
    visti = percorsi(cb, hb, aid)
    assert not {p for p in visti if p.startswith(SCANSIONE + "/")}
    assert "Soggiorno/Parete 2/4/wall.jpg" in visti  # «Parete 2/3» non copre «Parete 2/30» né «Parete 2/4»
    assert cb.get(f"/api/appartamenti/{aid}/raccolta/{SCANSIONE}/wall.jpg", headers=hb).status_code == 404
    # niente modifiche né file nuovi dentro una scansione non condivisa
    assert put(cb, hb, aid, f"{SCANSIONE}/wall.jpg", b"x", params={"sostituisci": "true"}).status_code == 403
    assert put(cb, hb, aid, f"{SCANSIONE}/nuovo.jpg", b"x").status_code == 403
    assert cb.delete(f"/api/appartamenti/{aid}/raccolta/{SCANSIONE}/wall.jpg", headers=hb).status_code == 403
    assert put(cb, hb, aid, f"{SCANSIONE}/condivisione.json", condivisione(bruno),
               params={"sostituisci": "true"}).status_code == 403
    # maiuscole diverse: stessa cartella (i percorsi della raccolta non le distinguono)
    assert cb.get(f"/api/appartamenti/{aid}/raccolta/soggiorno/parete 2/3/wall.jpg", headers=hb).status_code == 404


def test_chi_vede_puo_cambiare_la_condivisione(su):
    (ca, ha, anna), (cb, hb, bruno), aid = due_persone(su)
    foto_di_anna(ca, ha, aid, anna, bruno)
    # Bruno la vede: può cambiarla (l'ultimo vince con sostituisci) e rimetterla a «tutti»
    assert put(cb, hb, aid, FOTO + ".condivisione.json", condivisione(bruno, anna, autore=anna)).status_code == 409
    assert put(cb, hb, aid, FOTO + ".condivisione.json", condivisione(bruno, anna, autore=anna),
               params={"sostituisci": "true"}).status_code == 200
    assert cb.delete(f"/api/appartamenti/{aid}/raccolta/{FOTO}.condivisione.json", headers=hb).status_code == 204
    # la foto resta di Anna: Bruno non la elimina (regola di prima)
    assert cb.delete(f"/api/appartamenti/{aid}/raccolta/{FOTO}", headers=hb).status_code == 403


def test_autore_e_dl_cambiano_sempre_la_condivisione(su):
    (ca, ha, anna), (cb, hb, bruno), aid = due_persone(su)
    cm, dm, hm, _ = entra(su, "Mario")  # Direttore dei lavori di Lungo Avisio
    foto_di_anna(ca, ha, aid, anna)
    assert FOTO not in percorsi(cm, hm, aid)
    assert cm.get(f"/api/appartamenti/{aid}/raccolta/{FOTO}", headers=hm).status_code == 404
    assert put(cm, hm, aid, FOTO, b"x", params={"sostituisci": "true"}).status_code == 403
    # il DL non la vede ma ne può cambiare la condivisione
    assert put(cm, hm, aid, FOTO + ".condivisione.json", condivisione(anna, dm["persona"]["id"], autore=anna),
               params={"sostituisci": "true"}).status_code == 200
    assert FOTO in percorsi(cm, hm, aid)
    # l'autore anche se nel file non c'è più tra le persone (resta sempre dentro)
    assert put(ca, ha, aid, FOTO + ".condivisione.json", json.dumps({"persone": [bruno], "autore": anna}).encode(),
               params={"sostituisci": "true"}).status_code == 200
    assert FOTO in percorsi(ca, ha, aid) and FOTO in percorsi(cb, hb, aid)
    assert FOTO not in percorsi(cm, hm, aid)


def test_committente_stessa_regola(su):
    (ca, ha, anna), _, aid = due_persone(su)
    persone = {p["nome"]: p for p in su.get("/api/persone").json()}
    lorenzo = persone["Lorenzo"]  # committente di APP 3 (esempio)
    pw = su.post(f"/api/persone/{lorenzo['id']}/password").json()["password"]
    cl = TestClient(app)
    hl = {"Authorization": f"Bearer {cl.post('/api/login', json={'username': lorenzo['username'], 'password': pw}).json()['token']}"}
    foto_di_anna(ca, ha, aid, anna)
    assert FOTO not in percorsi(cl, hl, aid)
    assert put(ca, ha, aid, FOTO + ".condivisione.json", condivisione(anna, lorenzo["id"])).status_code == 200
    assert FOTO in percorsi(cl, hl, aid)


def test_file_illeggibile_vale_per_tutti(su, caplog):
    (ca, ha, anna), (cb, hb, bruno), aid = due_persone(su)
    assert put(ca, ha, aid, FOTO, b"\xff\xd8foto").status_code == 201
    with caplog.at_level(logging.WARNING, logger="arworksite"):
        for i, rovinato in enumerate((b"{non json", b'{"persone": "tutti"}', b'{"autore": 1}')):
            assert put(ca, ha, aid, FOTO + ".condivisione.json", rovinato).status_code in (200, 201), i
            assert FOTO in percorsi(cb, hb, aid), i
    assert "Condivisione illeggibile" in caplog.text


def test_superuser_e_vista_esplora(su):
    (ca, ha, anna), (cb, hb, bruno), aid = due_persone(su)
    foto_di_anna(ca, ha, aid, anna)
    assert FOTO in {f["percorso"] for f in su.get(f"/api/appartamenti/{aid}/raccolta").json()["file"]}
    assert "IMG_1731" in su.get(f"/api/appartamenti/{aid}/esplora").text
    assert "IMG_1731" in ca.get(f"/api/appartamenti/{aid}/esplora", headers=ha).text
    assert "IMG_1731" not in cb.get(f"/api/appartamenti/{aid}/esplora", headers=hb).text
