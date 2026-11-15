"""API dei dati raccolti negli appartamenti (scansioni, foto, file, report del telefono)."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from . import config
from . import raccolta as rc
from .sicurezza import admin_o_app, archivio_pronto

router = APIRouter(prefix="/api", tags=["dati raccolti"], dependencies=[Depends(archivio_pronto)])


class FileRaccolto(BaseModel):
    percorso: str = Field(description="Relativo alla cartella dell'appartamento, es. «Soggiorno/Parete 2/1/wall.jpg»")
    dimensione: int
    sha256: str
    modificato_dispositivo: Optional[str] = Field(description="Data del file sul telefono (X-Modificato)")
    caricato: str
    persona_id: Optional[int]
    chi: str
    url_download: str


class AppartamentoRif(BaseModel):
    id: int
    nome: str
    id_app: Optional[str]


class Raccolta(BaseModel):
    appartamento: AppartamentoRif
    versione: str
    puoi_caricare: bool
    file: list[FileRaccolto]


class EsitoCaricamento(BaseModel):
    esito: str = Field(description="nuovo, aggiornato o invariato")
    file: FileRaccolto


def _accesso(id_appartamento: int, persona: Optional[dict], serve: str) -> dict:
    a = rc.accesso(id_appartamento, persona)
    if not a or not a["leggere"]:
        raise HTTPException(status_code=404, detail="Appartamento non trovato")
    if not a[serve]:
        raise HTTPException(status_code=403, detail="Solo la squadra del cantiere può caricare i dati raccolti")
    return a


def _percorso(percorso: str) -> str:
    try:
        return rc.normalizza(percorso)
    except rc.PercorsoNonValido as e:
        raise HTTPException(status_code=400, detail=str(e))


def _con_url(f: dict, id_appartamento: int, request: Request) -> dict:
    # percorso codificato (spazi, lettere accentate): "Parete 2" → "Parete%202"
    base = str(request.url_for("scarica_raccolto", id_appartamento=id_appartamento, percorso="_"))[:-1]
    return {**f, "url_download": base + quote(f["percorso"], safe="/")}


@router.get("/appartamenti/{id_appartamento}/raccolta", response_model=Raccolta,
            responses={304: {"description": "Niente di nuovo rispetto a If-None-Match"}})
def elenco(id_appartamento: int, request: Request, persona: Optional[dict] = Depends(admin_o_app),
           if_none_match: Optional[str] = Header(default=None)):
    """Tutti i file raccolti nell'appartamento (da tutti i telefoni), con impronta e autore. Il telefono
    confronta con i suoi: carica quelli nuovi o cambiati, scarica quelli degli altri.
    Mancano i contenuti con un file di condivisione che non comprende la persona (vedi `raccolta.py`).
    Con `If-None-Match: "<versione>"` risponde 304 se non è cambiato niente."""
    a = _accesso(id_appartamento, persona, "leggere")
    file = rc.elenco_per(id_appartamento, persona)
    versione = rc.versione(file)
    etag = f'"{versione}"'
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    corpo = Raccolta(appartamento=a["appartamento"], versione=versione, puoi_caricare=a["caricare"],
                     file=[_con_url(f, id_appartamento, request) for f in file])
    return JSONResponse(jsonable_encoder(corpo), headers={"ETag": etag})


@router.put("/appartamenti/{id_appartamento}/raccolta/{percorso:path}", response_model=EsitoCaricamento,
            responses={201: {"description": "File nuovo"}, 409: {"description": "Caricato da un'altra persona"}})
async def carica(id_appartamento: int, percorso: str, request: Request,
                 persona: Optional[dict] = Depends(admin_o_app),
                 x_sha256: str = Header(description="SHA-256 del contenuto (esadecimale)"),
                 x_modificato: Optional[str] = Header(default=None, description="Data del file sul telefono (ISO 8601)"),
                 sostituisci: bool = False):
    """Carica un file nel percorso dato (corpo = contenuto del file, `application/octet-stream`).
    - stesso contenuto già presente → 200 `invariato` (si può ripetere senza problemi);
    - file nuovo → 201 `nuovo`; file già caricato dalla stessa persona → 200 `aggiornato`;
    - file caricato da un'altra persona → 409, a meno di `?sostituisci=true`;
    - `X-Sha256` diverso dal contenuto ricevuto → 400 (trasferimento rovinato: riprovare);
    - contenuto non condiviso con la persona → 403 (il file di condivisione lo cambiano anche autore e DL)."""
    a = _accesso(id_appartamento, persona, "caricare")
    percorso = _percorso(percorso)
    if not rc.condivisioni(id_appartamento).puo_modificare(percorso, persona, a["dl"]):
        raise HTTPException(status_code=403, detail="Questo contenuto non è condiviso con te: non puoi modificarlo")
    atteso = (x_sha256 or "").strip().lower()
    esistente = rc.leggi(id_appartamento, percorso)
    if esistente and esistente["sha256"] == atteso:
        return JSONResponse(jsonable_encoder({"esito": "invariato", "file": _con_url(esistente, id_appartamento, request)}))
    mio = persona["id"] if persona else None
    if esistente and esistente["persona_id"] != mio and not sostituisci:
        raise HTTPException(status_code=409, detail=f"«{percorso}» è già stato caricato da {esistente['chi']}: "
                                                    "usa un altro percorso o sostituisci=true")
    dest = rc.cartella(id_appartamento)
    dest.mkdir(parents=True, exist_ok=True)
    interno = f"{uuid.uuid4().hex}{os.path.splitext(percorso)[1].lower()}"
    tmp = dest / f".upload-{uuid.uuid4().hex}.tmp"
    h, dimensione, limite = hashlib.sha256(), 0, config.MAX_UPLOAD_MB * 1024 * 1024
    try:
        with tmp.open("wb") as out:
            async for blocco in request.stream():
                dimensione += len(blocco)
                if dimensione > limite:
                    raise HTTPException(status_code=413, detail=f"Il file supera {config.MAX_UPLOAD_MB} MB")
                h.update(blocco)
                out.write(blocco)
        if dimensione == 0:
            raise HTTPException(status_code=400, detail="File vuoto")
        if h.hexdigest() != atteso:
            raise HTTPException(status_code=400, detail="X-Sha256 non corrisponde al contenuto ricevuto: riprova")
        os.replace(tmp, dest / interno)
    finally:
        if tmp.exists():
            tmp.unlink()
    riga, vecchio = rc.registra(id_appartamento, percorso, interno, dimensione, atteso, x_modificato, persona)
    if vecchio:
        (dest / vecchio).unlink(missing_ok=True)
    corpo = {"esito": "aggiornato" if esistente else "nuovo", "file": _con_url(riga, id_appartamento, request)}
    return JSONResponse(jsonable_encoder(corpo), status_code=200 if esistente else 201)


@router.get("/appartamenti/{id_appartamento}/raccolta/{percorso:path}", name="scarica_raccolto",
            responses={200: {"content": {"application/octet-stream": {}}}, 304: {"description": "Non modificato"}})
def scarica(id_appartamento: int, percorso: str, persona: Optional[dict] = Depends(admin_o_app),
            inline: bool = False, if_none_match: Optional[str] = Header(default=None)):
    """Scarica un file raccolto. Con `If-None-Match: "<sha256>"` risponde 304 se è uguale."""
    _accesso(id_appartamento, persona, "leggere")
    r = rc.leggi(id_appartamento, _percorso(percorso))
    if r and not rc.condivisioni(id_appartamento).vede(r["percorso"], persona):
        r = None  # non condiviso con questa persona: come se non esistesse
    file = rc.cartella(id_appartamento) / r["file"] if r else None
    if not r or not file.is_file():
        raise HTTPException(status_code=404, detail="File non trovato")
    etag = f'"{r["sha256"]}"'
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    nome = r["percorso"].rsplit("/", 1)[-1]
    media = mimetypes.guess_type(nome)[0] or "application/octet-stream"
    mostra = inline and (media.startswith("image/") or nome.lower().endswith((".glb", ".json", ".pdf")))
    return FileResponse(file, media_type=media, filename=nome, headers={"ETag": etag},
                        content_disposition_type="inline" if mostra else "attachment")


@router.delete("/appartamenti/{id_appartamento}/raccolta/{percorso:path}", status_code=204)
def elimina(id_appartamento: int, percorso: str, persona: Optional[dict] = Depends(admin_o_app)):
    """Elimina un file raccolto: chi l'ha caricato, il Direttore dei lavori della squadra o il superuser.
    Solo se il contenuto è condiviso con la persona (403). Un file di condivisione lo può eliminare chiunque
    veda il contenuto, il suo autore o il DL: eliminarlo = condividere con tutti."""
    a = _accesso(id_appartamento, persona, "caricare")
    percorso = _percorso(percorso)
    r = rc.leggi(id_appartamento, percorso)
    if not r:
        raise HTTPException(status_code=404, detail="File non trovato")
    if not rc.condivisioni(id_appartamento).puo_modificare(r["percorso"], persona, a["dl"]):
        raise HTTPException(status_code=403, detail="Questo contenuto non è condiviso con te: non puoi eliminarlo")
    condivisione = rc.bersaglio_condivisione(r["percorso"]) is not None
    if persona and r["persona_id"] != persona["id"] and not a["dl"] and not condivisione:
        raise HTTPException(status_code=403, detail=f"Lo può eliminare solo {r['chi']} o il Direttore dei lavori")
    rc.elimina(id_appartamento, r["percorso"])
    return Response(status_code=204)
