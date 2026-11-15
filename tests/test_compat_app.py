"""Le richieste come le fa l'app (shared/server: ServerClient, ActivityLibrary, ApartmentDataSync)."""
import hashlib
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.main import app
from test_raccolta import entra, su  # noqa: F401  (fixture con la pianta di prova)


def multipart_ktor(campi: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    """submitFormWithBinaryData: Ktor unisce i due Content-Disposition con "; " e non mette Content-Type."""
    confine = "ktor-confine-123"
    corpo = b""
    for nome, file, dati in campi:
        corpo += (f"--{confine}\r\nContent-Disposition: form-data; name={nome}; filename=\"{file}\"\r\n"
                  f"Content-Length: {len(dati)}\r\n\r\n").encode() + dati + b"\r\n"
    corpo += f"--{confine}--\r\n".encode()
    return corpo, f"multipart/form-data; boundary={confine}"


def campi_attivita(a: dict) -> None:
    """Quello che legge ActivityLibrary.parseActivity (getValue = obbligatorio)."""
    for k in ("id", "titolo", "descrizione", "modificata", "assegnatari", "allegati", "stato", "storico_stati"):
        assert k in a, k
    for f in a["allegati"]:
        assert {"id", "nome", "tipo", "dimensione", "sha256"} <= set(f)
    for m in a["assegnatari"]:
        assert {"id", "nome", "cognome", "ruolo"} <= set(m)


def test_flusso_completo_dell_app(su):
    c, d, h, avisio = entra(su, "Anna")
    # siteData(): GET /api/miei-dati, con quello che legge parseSiteData
    d = c.get("/api/miei-dati", headers=h).json()
    assert {"id", "ruolo"} <= set(d["persona"])
    k = next(x for x in d["cantieri"] if x["nome"] == "Lungo Avisio")
    assert {"id", "nome", "squadra", "accesso", "appartamenti", "piante"} <= set(k)
    assert all({"id", "nome", "etichetta", "id_app", "pianta_id", "committente"} <= set(a) for a in k["appartamenti"])
    assert all({"id", "nome", "cantiere", "sha256"} <= set(p) for p in k["piante"])
    app3 = next(a for a in k["appartamenti"] if a["nome"] == "APP 3")

    att = su.post("/api/attivita", json={"titolo": "Tracce", "appartamento_id": app3["id"],
                                         "persone_ids": [d["persona"]["id"]]}).json()
    # uploadAttachments(): multipart con il campo "file" ripetuto, solo file
    corpo, tipo = multipart_ktor([("file", "IMG_1.jpg", b"\xff\xd81"), ("file", "verbale \"bis\".pdf".replace('"', ""), b"%PDF")])
    r = c.post(f"/api/mie-attivita/{att['id']}/aggiornamento", content=corpo, headers={**h, "Content-Type": tipo})
    assert r.status_code == 200, r.text
    assert [f["nome"] for f in r.json()["attivita"]["allegati"]] == ["IMG_1.jpg", "verbale bis.pdf"]
    # changeState() di sendPendingStates: aperta → chiusa passa da presa_in_carico (stepsTo)
    for passo in ("presa_in_carico", "chiusa"):
        r = c.post(f"/api/mie-attivita/{att['id']}/stato", json={"stato": passo, "nota": ""}, headers=h)
        assert r.status_code == 200, (passo, r.text)
    a = c.get("/api/miei-dati", headers=h).json()["attivita"][0]
    campi_attivita(a)
    assert a["stato"] == "chiusa" and len(a["allegati"]) == 2
    # syncAttachments(): scarica ogni allegato
    for f in a["allegati"]:
        assert c.get(f"/api/attivita/{a['id']}/allegati/{f['id']}/file", headers=h).status_code == 200

    # ApartmentDataSync: listCollected / putCollected / getCollected / deleteCollected
    base = f"/api/appartamenti/{app3['id']}/raccolta"
    lista = c.get(base, headers=h).json()
    assert lista["puoi_caricare"] is True and isinstance(lista["versione"], str) and lista["file"] == []

    def url(percorso):  # collectedUrl: encodeURLPathPart pezzo per pezzo
        return base + "/" + "/".join(quote(p, safe="") for p in percorso.split("/"))

    def put(percorso, dati, sostituisci=False):
        return c.put(url(percorso) + ("?sostituisci=true" if sostituisci else ""), content=dati,
                     headers={**h, "X-Sha256": hashlib.sha256(dati).hexdigest(),
                              "Content-Type": "application/octet-stream", "Content-Length": str(len(dati))})

    assert put("appartamento.json", b"{}").status_code == 201
    assert put("Soggiorno/Parete 2/1/wall.jpg", b"\xff\xd8w").status_code == 201
    assert put("Soggiorno/Parete 2/1/metadata.json", b'{"folderNumber":"1"}').status_code == 201
    assert put("Soggiorno/Parete 2/foto/SOggiorno_parete2 (2).jpg", b"\xff\xd8f").status_code == 201
    assert put("Soggiorno/Parete 2/file/Impianti/schema è.pdf", b"%PDF").status_code == 201
    assert put("appartamento.json", b'{"v":2}', sostituisci=True).status_code == 200
    lista = c.get(base, headers=h).json()
    assert {f["percorso"] for f in lista["file"]} == {
        "appartamento.json", "Soggiorno/Parete 2/1/wall.jpg", "Soggiorno/Parete 2/1/metadata.json",
        "Soggiorno/Parete 2/foto/SOggiorno_parete2 (2).jpg", "Soggiorno/Parete 2/file/Impianti/schema è.pdf"}
    assert all({"percorso", "sha256", "dimensione", "chi"} <= set(f) for f in lista["file"])
    assert c.get(url("Soggiorno/Parete 2/file/Impianti/schema è.pdf"), headers=h).content == b"%PDF"
    assert c.delete(url("Soggiorno/Parete 2/foto/SOggiorno_parete2 (2).jpg"), headers=h).status_code == 204
    # un altro telefono: 409 su un percorso già suo (ServerErrorException.status == 409)
    c2, _, h2, _ = entra(su, "Bruno")
    r = c2.put(url("Soggiorno/Parete 2/1/wall.jpg"), content=b"\xff\xd8altra",
               headers={**h2, "X-Sha256": hashlib.sha256(b"\xff\xd8altra").hexdigest()})
    assert r.status_code == 409
    # i dati aggiornati arrivano anche a lui, insieme al riepilogo in miei-dati
    assert len(c2.get(base, headers=h2).json()["file"]) == 4
    d2 = c2.get("/api/miei-dati", headers=h2).json()
    k2 = next(x for x in d2["cantieri"] if x["nome"] == "Lungo Avisio")
    assert next(a for a in k2["appartamenti"] if a["nome"] == "APP 3")["raccolta"]["n_file"] == 4
