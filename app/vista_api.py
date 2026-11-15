"""API della vista del cantiere: modello della planimetria e contenuto di un appartamento."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from . import appartamenti as app_db
from . import attivita as att
from . import modelli, pacchetto, piante
from . import raccolta as rc
from . import vista
from .attivita_api import con_url
from .raccolta_api import _con_url
from .sicurezza import admin_o_app, archivio_pronto

router = APIRouter(prefix="/api", tags=["vista del cantiere"], dependencies=[Depends(archivio_pronto)])


def _pianta_visibile(id_pianta: str, persona: Optional[dict]) -> piante.Pianta:
    p = piante.trova(id_pianta)
    if not p or (persona is not None and p.id not in pacchetto.piante_consentite(persona["id"])):
        raise HTTPException(status_code=404, detail="Pianta non trovata")
    return p


@router.get("/piante/{id_pianta}/modello",
            responses={202: {"description": "In preparazione: riprovare tra qualche secondo"},
                       422: {"description": "Pianta non leggibile sul server (DWG, DXF binario…)"}})
def modello_pianta(id_pianta: str, persona: Optional[dict] = Depends(admin_o_app)):
    """La planimetria letta come nell'app: layer (colore, visibili all'inizio), segmenti per layer,
    appartamenti con perimetro e locali, e per ogni locale le pareti numerate ("Parete N").
    La prima volta il calcolo richiede qualche secondo: risponde 202 finché non è pronto."""
    p = _pianta_visibile(id_pianta, persona)
    stato, dati = modelli.stato(p)
    if stato == "pronto":
        return JSONResponse(dati, headers={"ETag": f'"{p.sha256}"', "Cache-Control": "private, max-age=300"})
    if stato == "errore":
        raise HTTPException(status_code=422, detail=dati)
    return JSONResponse({"stato": "in_preparazione"}, status_code=202, headers={"Retry-After": "3"})


def _pianta_appartamento(ap: dict) -> piante.Pianta | None:
    if not ap.get("pianta"):
        return None
    for p in app_db.piante_del_cantiere(ap["cantiere_id"]):
        if p.nome.lower() == ap["pianta"].lower():
            return p
    return None


@router.get("/appartamenti/{id_appartamento}/esplora")
def esplora(id_appartamento: int, request: Request, persona: Optional[dict] = Depends(admin_o_app)):
    """Tutto quello che c'è in un appartamento, ordinato come nel pannello media dell'app:
    `nodi` = appartamento (""), locali ("Soggiorno"), pareti e superfici ("Soggiorno/Parete 2",
    "Soggiorno/Soffitto"), ognuno con foto, file, scansioni, allegati delle attività che lo riguardano
    e i conteggi di tutto quello che sta sotto. Più le attività dell'appartamento e dello spazio comune
    della sua planimetria."""
    acc = rc.accesso(id_appartamento, persona)
    if not acc or not acc["leggere"]:
        raise HTTPException(status_code=404, detail="Appartamento non trovato")
    ap = app_db.leggi(id_appartamento)
    p = _pianta_appartamento(ap)
    stato_modello, locali = "nessuna_pianta", None
    if p is not None:
        stato_modello, m = modelli.stato(p)
        if stato_modello == "pronto":
            trovato = next((x for x in m["appartamenti"] if x["nome"].lower() == ap["nome"].lower()), None)
            locali = trovato["locali"] if trovato else []
    file = [_con_url(f, id_appartamento, request) for f in rc.elenco_per(id_appartamento, persona)]
    nodi = vista.albero(file, locali, lambda interno: rc.cartella(id_appartamento) / interno)

    # attività dell'appartamento + quelle dello spazio comune della stessa planimetria
    ids = {id_appartamento}
    if ap["origine"] != "comune":
        for x in app_db.elenco(ap["cantiere_id"]):
            if x["origine"] == "comune" and (x["pianta"] or "") == (ap["pianta"] or ""):
                ids.add(x["id"])
    attivita = [a for a in att.elenco() if a["appartamento"] and a["appartamento"]["id"] in ids]
    if persona is not None:
        attivita = [a for a in attivita if att.visibile_a(a, persona)]
    attivita = [con_url(a, request) for a in attivita]
    vista.assegna_allegati(nodi, attivita, ap.get("id_app"))

    corpo = {
        "appartamento": {"id": ap["id"], "nome": ap["nome"], "origine": ap["origine"], "id_app": ap.get("id_app"),
                         "pianta": ap.get("pianta"), "pianta_id": p.id if p else None,
                         "cantiere_id": ap["cantiere_id"]},
        "modello": stato_modello,
        "puoi_caricare": acc["caricare"],
        "nodi": list(nodi.values()),
        "attivita": attivita,
    }
    return JSONResponse(jsonable_encoder(corpo))
