"""API delle attività: gestione (superuser) e download degli allegati (superuser o persona assegnata)."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from . import attivita as att
from . import config, db
from .cantieri_api import Membro
from .sicurezza import _chiave_giusta, admin_corrente, archivio_pronto, solo_admin

router = APIRouter(prefix="/api", tags=["attività"], dependencies=[Depends(archivio_pronto), Depends(solo_admin)])
router_file = APIRouter(prefix="/api", tags=["attività"], dependencies=[Depends(archivio_pronto)])

mimetypes.add_type("model/gltf-binary", ".glb")
_NOME_NON_VALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# --- Modelli -----------------------------------------------------------------

def _titolo(v: str) -> str:
    v = " ".join((v or "").split())
    if not v:
        raise ValueError("il titolo non può essere vuoto")
    return v


class AttivitaIn(BaseModel):
    titolo: str = Field(max_length=150)
    descrizione: str = Field(default="", max_length=5000)
    cantiere_id: Optional[int] = Field(default=None, description="Facoltativo: cantiere a cui si riferisce")
    persone_ids: list[int] = Field(default_factory=list, description="Persone a cui è assegnata")
    _t = field_validator("titolo")(_titolo)

    @field_validator("descrizione")
    @classmethod
    def _d(cls, v: str) -> str:
        return (v or "").strip()


class AttivitaModifica(BaseModel):
    titolo: Optional[str] = Field(default=None, max_length=150)
    descrizione: Optional[str] = Field(default=None, max_length=5000)
    cantiere_id: Optional[int] = Field(default=None, description="null per togliere il cantiere")
    persone_ids: Optional[list[int]] = Field(default=None, description="Sostituisce tutti gli assegnatari")

    @field_validator("titolo")
    @classmethod
    def _t(cls, v):
        return None if v is None else _titolo(v)

    @field_validator("descrizione")
    @classmethod
    def _d(cls, v):
        return None if v is None else v.strip()


class Allegato(BaseModel):
    id: int
    nome: str
    tipo: str = Field(description="foto, scansione3d (GLB) o file")
    dimensione: int
    sha256: str
    caricato: str
    url_download: str


class CantiereRif(BaseModel):
    id: int
    nome: str


class Attivita(BaseModel):
    id: int
    titolo: str
    descrizione: str
    cantiere: Optional[CantiereRif]
    creata: str
    modificata: str
    assegnatari: list[Membro]
    allegati: list[Allegato]


class EsitoAllegati(BaseModel):
    caricati: list[Allegato]
    attivita: Attivita


def con_url(a: dict, request: Request) -> dict:
    """Aggiunge a ogni allegato l'indirizzo per scaricarlo."""
    return {**a, "allegati": [{**f, "url_download": str(request.url_for(
        "scarica_allegato", id_attivita=a["id"], id_allegato=f["id"]))} for f in a["allegati"]]}


def _attivita(id_attivita: int) -> dict:
    a = att.leggi(id_attivita)
    if not a:
        raise HTTPException(status_code=404, detail="Attività non trovata")
    return a


def _verifica_riferimenti(cantiere_id: Optional[int], persone_ids: Optional[list[int]]) -> list[int] | None:
    if cantiere_id is not None and not db.leggi_cantiere(cantiere_id):
        raise HTTPException(status_code=400, detail="Cantiere non trovato")
    if persone_ids is None:
        return None
    ids = list(dict.fromkeys(persone_ids))
    mancanti = set(ids) - att.persone_esistenti(ids)
    if mancanti:
        raise HTTPException(status_code=400, detail=f"Persone non trovate: {sorted(mancanti)}")
    return ids


# --- Gestione (superuser) ----------------------------------------------------

@router.get("/attivita", response_model=list[Attivita])
def elenco_attivita(request: Request, persona_id: Optional[int] = None):
    """Tutte le attività, le più recenti prima. Con `persona_id` solo quelle assegnate a quella persona."""
    return [con_url(a, request) for a in att.elenco(persona_id)]


@router.post("/attivita", response_model=Attivita, status_code=201)
def crea_attivita(dati: AttivitaIn, request: Request):
    """Crea un'attività e la assegna alle persone indicate. Gli allegati si aggiungono dopo con
    `POST /api/attivita/{id}/allegati`."""
    ids = _verifica_riferimenti(dati.cantiere_id, dati.persone_ids)
    return con_url(att.crea(dati.titolo, dati.descrizione, dati.cantiere_id, ids), request)


@router.get("/attivita/{id_attivita}", response_model=Attivita)
def leggi_attivita(id_attivita: int, request: Request):
    return con_url(_attivita(id_attivita), request)


@router.patch("/attivita/{id_attivita}", response_model=Attivita)
def modifica_attivita(id_attivita: int, dati: AttivitaModifica, request: Request):
    _attivita(id_attivita)
    ids = _verifica_riferimenti(dati.cantiere_id, dati.persone_ids)
    cambia_cantiere = "cantiere_id" in dati.model_fields_set
    return con_url(att.modifica(id_attivita, dati.titolo, dati.descrizione,
                                (cambia_cantiere, dati.cantiere_id), ids), request)


@router.delete("/attivita/{id_attivita}", status_code=204)
def elimina_attivita(id_attivita: int):
    """Elimina l'attività con tutti i suoi allegati."""
    if not att.elimina(id_attivita):
        raise HTTPException(status_code=404, detail="Attività non trovata")
    return Response(status_code=204)


def _nome_sicuro(nome: str) -> str:
    nome = _NOME_NON_VALIDO.sub("_", Path(nome or "").name).strip().strip(".")
    if not nome or nome.startswith("."):
        raise HTTPException(status_code=400, detail="Nome del file non valido")
    return nome[:150]


@router.post("/attivita/{id_attivita}/allegati", response_model=EsitoAllegati, status_code=201)
async def carica_allegati(id_attivita: int, request: Request,
                          file: list[UploadFile] = File(..., description="Foto, scansioni 3D .glb o altri file")):
    """Aggiunge uno o più allegati. Il tipo viene dall'estensione: foto (jpg, png, webp, heic…),
    scansione3d (.glb), altrimenti file. Programmi e pagine web (.exe, .bat, .html…) non sono accettati."""
    _attivita(id_attivita)
    nomi = [_nome_sicuro(f.filename) for f in file]
    for n in nomi:
        if Path(n).suffix.lower() in att.ESTENSIONI_VIETATE:
            raise HTTPException(status_code=400, detail=f"{n}: questo tipo di file non è ammesso")
    cartella = att.cartella_allegati(id_attivita)
    cartella.mkdir(parents=True, exist_ok=True)
    limite = config.MAX_UPLOAD_MB * 1024 * 1024
    caricati = []
    for f, nome in zip(file, nomi):
        interno = f"{uuid.uuid4().hex}{Path(nome).suffix.lower()}"
        tmp = cartella / f".upload-{uuid.uuid4().hex}.tmp"
        h, dimensione = hashlib.sha256(), 0
        try:
            with tmp.open("wb") as out:
                while blocco := await f.read(1 << 20):
                    dimensione += len(blocco)
                    if dimensione > limite:
                        raise HTTPException(status_code=413, detail=f"{nome}: supera {config.MAX_UPLOAD_MB} MB")
                    h.update(blocco)
                    out.write(blocco)
            if dimensione == 0:
                raise HTTPException(status_code=400, detail=f"{nome}: file vuoto")
            os.replace(tmp, cartella / interno)
        finally:
            if tmp.exists():
                tmp.unlink()
        caricati.append(att.registra_allegato(id_attivita, nome, interno, dimensione, h.hexdigest()))
    a = con_url(att.leggi(id_attivita), request)
    per_id = {x["id"]: x for x in a["allegati"]}
    return {"caricati": [per_id[c["id"]] for c in caricati], "attivita": a}


@router.delete("/attivita/{id_attivita}/allegati/{id_allegato}", status_code=204)
def elimina_allegato(id_attivita: int, id_allegato: int):
    if not att.elimina_allegato(id_attivita, id_allegato):
        raise HTTPException(status_code=404, detail="Allegato non trovato")
    return Response(status_code=204)


# --- Download (superuser oppure persona assegnata) -------------------------

@router_file.get("/attivita/{id_attivita}/allegati/{id_allegato}/file", name="scarica_allegato",
                 responses={200: {"content": {"application/octet-stream": {}}}, 304: {"description": "Non modificato"}})
def scarica_allegato(id_attivita: int, id_allegato: int, request: Request, inline: bool = False,
                     authorization: Optional[str] = Header(default=None),
                     x_api_key: Optional[str] = Header(default=None),
                     if_none_match: Optional[str] = Header(default=None)):
    """Scarica un allegato. Dall'app serve `Authorization: Bearer <token>` di una persona a cui l'attività
    è assegnata. `inline=true` lo mostra nel browser invece di scaricarlo (foto, GLB).
    Con `If-None-Match: "<sha256>"` risponde 304 se il file non è cambiato."""
    if not (admin_corrente(request) or _chiave_giusta(x_api_key)):
        persona = None
        if authorization and authorization.lower().startswith("bearer "):
            persona = db.persona_da_token(authorization[7:].strip())
        if not persona:
            raise HTTPException(status_code=401, detail="Accesso richiesto", headers={"WWW-Authenticate": "Bearer"})
        if not att.assegnata_a(id_attivita, persona["id"]):
            raise HTTPException(status_code=404, detail="Allegato non trovato")
    trovato = att.allegato(id_attivita, id_allegato)
    if not trovato or not trovato[1].is_file():
        raise HTTPException(status_code=404, detail="Allegato non trovato")
    info, percorso = trovato
    etag = f'"{info["sha256"]}"'
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    media = mimetypes.guess_type(info["nome"])[0] or "application/octet-stream"
    mostra = inline and info["tipo"] in ("foto", "scansione3d")
    return FileResponse(percorso, media_type=media, filename=info["nome"], headers={"ETag": etag},
                        content_disposition_type="inline" if mostra else "attachment")
