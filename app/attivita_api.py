"""API delle attività: gestione (superuser) e download degli allegati (superuser o persona assegnata)."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from . import appartamenti as app_db
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
    appartamento_id: int = Field(description="Appartamento, oppure lo «Spazio comune» della planimetria per "
                                             "un'attività di tutta la planimetria. Il cantiere viene da qui")
    cantiere_id: Optional[int] = Field(default=None, description="Facoltativo: se c'è deve essere quello dell'appartamento")
    persone_ids: list[int] = Field(default_factory=list, description="Persone a cui è assegnata (della squadra)")
    _t = field_validator("titolo")(_titolo)

    @field_validator("descrizione")
    @classmethod
    def _d(cls, v: str) -> str:
        return (v or "").strip()


class AttivitaModifica(BaseModel):
    titolo: Optional[str] = Field(default=None, max_length=150)
    descrizione: Optional[str] = Field(default=None, max_length=5000)
    cantiere_id: Optional[int] = Field(default=None, description="Facoltativo: deve essere quello dell'appartamento")
    appartamento_id: Optional[int] = Field(default=None, description="Sposta l'attività su un altro appartamento "
                                                                       "(anche di un altro cantiere)")
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
    persona_id: Optional[int] = Field(default=None, description="Chi l'ha caricato (null = superuser)")
    chi: str = "Superuser"
    riferimento: Optional[str] = Field(default=None, description="Id della parete/stanza/appartamento nell'app")
    url_download: str


class CantiereRif(BaseModel):
    id: int
    nome: str


class AppartamentoRif(BaseModel):
    id: int
    nome: str
    id_app: Optional[str] = Field(default=None, description="Id dell'appartamento nell'app (dwg:<pianta>/APP n)")


Stato = Literal["aperta", "presa_in_carico", "da_verificare", "chiusa"]


class Incaricato(BaseModel):
    persona_id: Optional[int]
    chi: str
    quando: str


class CambioStato(BaseModel):
    stato: Stato
    stato_nome: str
    quando: str
    persona_id: Optional[int] = Field(description="Chi l'ha cambiato (null = superuser dalla pagina web)")
    chi: str
    nota: str


class Nota(BaseModel):
    id: int
    quando: str
    persona_id: Optional[int] = Field(description="Chi l'ha scritta (null = superuser)")
    chi: str
    testo: str


class NotaIn(BaseModel):
    testo: str = Field(max_length=2000)

    @field_validator("testo")
    @classmethod
    def _t(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("la nota non può essere vuota")
        return v


class StatoIn(BaseModel):
    stato: Stato = Field(description="aperta, presa_in_carico, da_verificare o chiusa")
    nota: str = Field(default="", max_length=2000, description="Facoltativa: perché (es. motivo della riapertura)")


class Attivita(BaseModel):
    id: int
    titolo: str
    descrizione: str
    cantiere: Optional[CantiereRif]
    appartamento: Optional[AppartamentoRif] = None
    creata: str
    modificata: str
    stato: Stato = "aperta"
    stato_nome: str = "Aperta"
    stato_modificato: Optional[str] = None
    storico_stati: list[CambioStato] = []
    in_carico: Optional[Incaricato] = Field(default=None, description="Chi l'ha presa in carico (presa in carico / da verificare)")
    note: list[Nota] = Field(default=[], description="Note libere, dalla più vecchia. Le note dei cambi di stato "
                                                    "sono in storico_stati[].nota")
    autore: Optional[Membro] = Field(default=None, description="Chi ha fatto la segnalazione dall'app "
                                                               "(null = creata dalla pagina web)")
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


def _solo_squadra(cantiere_id: Optional[int], persone_ids: list[int]) -> None:
    """Un'attività si assegna solo a persone della squadra del suo cantiere."""
    if not persone_ids:
        return
    if cantiere_id is None:
        raise HTTPException(status_code=400, detail="Per assegnare l'attività scegli prima il cantiere")
    c = db.leggi_cantiere(cantiere_id)
    dentro = {m["id"] for m in c["squadra"]}
    fuori = [i for i in persone_ids if i not in dentro]
    if fuori:
        nomi = ", ".join(f"{p['nome']} {p['cognome']}" for p in (db.leggi_persona(i) for i in fuori) if p)
        raise HTTPException(status_code=400,
                            detail=f"Non sono nella squadra di «{c['nome']}»: {nomi}. Aggiungili prima alla squadra")


# --- Gestione (superuser) ----------------------------------------------------

@router.get("/attivita", response_model=list[Attivita])
def elenco_attivita(request: Request, persona_id: Optional[int] = None):
    """Tutte le attività, le più recenti prima. Con `persona_id` solo quelle assegnate a quella persona."""
    return [con_url(a, request) for a in att.elenco(persona_id)]


def _cantiere_e_appartamento(cantiere_id: Optional[int], appartamento_id: Optional[int]) -> Optional[int]:
    """Controlla che l'appartamento esista e sia del cantiere; senza cantiere usa quello dell'appartamento."""
    if appartamento_id is None:
        return cantiere_id
    ap = app_db.leggi(appartamento_id)
    if not ap:
        raise HTTPException(status_code=400, detail="Appartamento non trovato")
    if cantiere_id is None:
        return ap["cantiere_id"]
    if ap["cantiere_id"] != cantiere_id:
        raise HTTPException(status_code=400, detail=f"{ap['nome']} non appartiene a questo cantiere")
    return cantiere_id


@router.post("/attivita", response_model=Attivita, status_code=201)
def crea_attivita(dati: AttivitaIn, request: Request):
    """Crea un'attività e la assegna alle persone indicate. Gli allegati si aggiungono dopo con
    `POST /api/attivita/{id}/allegati`."""
    ids = _verifica_riferimenti(dati.cantiere_id, dati.persone_ids)
    cantiere_id = _cantiere_e_appartamento(dati.cantiere_id, dati.appartamento_id)
    _solo_squadra(cantiere_id, ids)
    return con_url(att.crea(dati.titolo, dati.descrizione, cantiere_id, ids, dati.appartamento_id), request)


@router.get("/attivita/{id_attivita}", response_model=Attivita)
def leggi_attivita(id_attivita: int, request: Request):
    return con_url(_attivita(id_attivita), request)


@router.patch("/attivita/{id_attivita}", response_model=Attivita)
def modifica_attivita(id_attivita: int, dati: AttivitaModifica, request: Request):
    attuale = _attivita(id_attivita)
    ids = _verifica_riferimenti(dati.cantiere_id, dati.persone_ids)
    campi = dati.model_fields_set
    if "appartamento_id" in campi and dati.appartamento_id is None:
        raise HTTPException(status_code=400, detail="Un'attività è sempre di un appartamento o di uno spazio comune")
    appartamento_id = dati.appartamento_id if "appartamento_id" in campi else (attuale["appartamento"] or {}).get("id")
    if appartamento_id is None:
        raise HTTPException(status_code=400, detail="Scegli l'appartamento o lo spazio comune dell'attività")
    cantiere_id = dati.cantiere_id if "cantiere_id" in campi else None  # None = quello dell'appartamento
    nuovo_cantiere = _cantiere_e_appartamento(cantiere_id, appartamento_id)
    _solo_squadra(nuovo_cantiere, ids if ids is not None else [m["id"] for m in attuale["assegnatari"]])
    return con_url(att.modifica(id_attivita, dati.titolo, dati.descrizione, (True, nuovo_cantiere), ids,
                                (True, appartamento_id)), request)


@router.post("/attivita/{id_attivita}/stato", response_model=Attivita)
def cambia_stato_attivita(id_attivita: int, dati: StatoIn, request: Request):
    """Cambia lo stato dall'amministrazione (superuser): qualsiasi passaggio. Resta nello storico."""
    attuale = _attivita(id_attivita)
    if attuale["stato"] == dati.stato:
        raise HTTPException(status_code=409, detail=f"L'attività è già «{att.STATI[dati.stato]}»")
    return con_url(att.cambia_stato(id_attivita, dati.stato, "Superuser", None, dati.nota.strip()), request)


@router.post("/attivita/{id_attivita}/note", response_model=Attivita, status_code=201)
def aggiungi_nota(id_attivita: int, dati: NotaIn, request: Request):
    """Aggiunge una nota (superuser) senza cambiare lo stato."""
    _attivita(id_attivita)
    return con_url(att.aggiungi_nota(id_attivita, dati.testo, "Superuser"), request)


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
    caricati = await salva_allegati(id_attivita, file)
    a = con_url(att.leggi(id_attivita), request)
    per_id = {x["id"]: x for x in a["allegati"]}
    return {"caricati": [per_id[c["id"]] for c in caricati], "attivita": a}


def controlla_nomi(file: list[UploadFile]) -> list[str]:
    nomi = [_nome_sicuro(f.filename) for f in file]
    for n in nomi:
        if Path(n).suffix.lower() in att.ESTENSIONI_VIETATE:
            raise HTTPException(status_code=400, detail=f"{n}: questo tipo di file non è ammesso")
    return nomi


async def salva_allegati(id_attivita: int, file: list[UploadFile], persona_id: int | None = None,
                         chi: str = "Superuser", riferimento: str | None = None) -> list[dict]:
    """Salva i file (a pezzi, con impronta) e li registra come allegati dell'attività."""
    nomi = controlla_nomi(file)
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
        caricati.append(att.registra_allegato(id_attivita, nome, interno, dimensione, h.hexdigest(),
                                              persona_id, chi, riferimento))
    return caricati


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
    è assegnata (o del Direttore dei lavori del cantiere). `inline=true` lo mostra nel browser invece di scaricarlo (foto, GLB).
    Con `If-None-Match: "<sha256>"` risponde 304 se il file non è cambiato."""
    if not (admin_corrente(request) or _chiave_giusta(x_api_key)):
        persona = None
        if authorization and authorization.lower().startswith("bearer "):
            persona = db.persona_da_token(authorization[7:].strip())
        if not persona:
            raise HTTPException(status_code=401, detail="Accesso richiesto", headers={"WWW-Authenticate": "Bearer"})
        a = att.leggi(id_attivita)
        if not a or not att.visibile_a(a, persona):
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
