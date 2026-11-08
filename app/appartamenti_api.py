"""Scheda del cantiere: squadra, piante, appartamenti con committenti, attività (solo superuser)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator

from . import appartamenti as app_db
from . import attivita as att
from . import config, db
from .attivita_api import Attivita, con_url
from .cantieri_api import Cantiere, Membro
from .sicurezza import archivio_pronto, solo_admin

router = APIRouter(prefix="/api", tags=["appartamenti"], dependencies=[Depends(archivio_pronto), Depends(solo_admin)])


class Appartamento(BaseModel):
    id: int
    nome: str = Field(description='"APP 3"')
    numero: Optional[int]
    etichetta: Optional[str] = Field(description='Testo trovato nella pianta, es. "u.a.03"')
    pianta: Optional[str] = Field(description="File della pianta da cui viene (null se aggiunto a mano)")
    id_app: Optional[str] = Field(description='Id nell\'app: "dwg:<pianta>/APP 3"')
    origine: str = Field(description="pianta, manuale o comune (spazio comune: tutta la planimetria)")
    committenti: list[Membro]
    n_attivita: int
    raccolta: "RiepilogoRaccolta"


class RiepilogoRaccolta(BaseModel):
    n_file: int = 0
    dimensione: int = 0
    aggiornato: Optional[str] = None


Appartamento.model_rebuild()


class PiantaRif(BaseModel):
    id: str
    nome: str
    formato: str
    dimensione: int
    modificato: str
    url_download: str


class PianteNonLette(BaseModel):
    nome: str
    motivo: str


class Scheda(BaseModel):
    cantiere: Cantiere
    cartella_piante: str
    piante: list[PiantaRif]
    piante_non_lette: list[PianteNonLette]
    appartamenti: list[Appartamento]
    attivita: list[Attivita]


class AppartamentoIn(BaseModel):
    nome: str = Field(max_length=60, description='Es. "APP 5" o "Negozio piano terra"')

    @field_validator("nome")
    @classmethod
    def _n(cls, v: str) -> str:
        v = " ".join((v or "").split())
        if not v:
            raise ValueError("il nome non può essere vuoto")
        return v


class CommittenteIn(BaseModel):
    persona_id: int


def _cantiere(id_cantiere: int) -> dict:
    c = db.leggi_cantiere(id_cantiere)
    if not c:
        raise HTTPException(status_code=404, detail="Cantiere non trovato")
    return c


def _appartamento(id_appartamento: int) -> dict:
    a = app_db.leggi(id_appartamento)
    if not a:
        raise HTTPException(status_code=404, detail="Appartamento non trovato")
    return a


def _scheda(id_cantiere: int, request: Request, non_lette: list[dict] | None = None) -> dict:
    c = _cantiere(id_cantiere)
    if non_lette is None:
        non_lette = app_db.sincronizza(id_cantiere)["piante_non_lette"]
    return {
        "cantiere": c,
        "cartella_piante": app_db.cartella_di(id_cantiere),
        "piante": [{"id": p.id, "nome": p.nome, "formato": p.formato, "dimensione": p.dimensione,
                    "modificato": p.modificato,
                    "url_download": str(request.url_for("scarica_pianta", id_pianta=p.id))}
                   for p in app_db.piante_del_cantiere(id_cantiere)],
        "piante_non_lette": non_lette,
        "appartamenti": app_db.elenco(id_cantiere),
        "attivita": [con_url(a, request) for a in att.elenco() if (a["cantiere"] or {}).get("id") == id_cantiere],
    }


@router.get("/cantieri/{id_cantiere}/scheda", response_model=Scheda)
def scheda_cantiere(id_cantiere: int, request: Request):
    """Tutto il cantiere in una risposta: squadra, piante, appartamenti con committenti, attività.
    Rilegge le piante DXF della cartella del cantiere e aggiunge gli appartamenti nuovi."""
    return _scheda(id_cantiere, request)


@router.post("/cantieri/{id_cantiere}/piante", response_model=Scheda, status_code=201)
async def carica_pianta_cantiere(id_cantiere: int, request: Request,
                                 file: list[UploadFile] = File(..., description="Piante .dxf o .dwg"),
                                 sostituisci: bool = Form(default=False)):
    """Carica piante nella cartella del cantiere e ne legge gli appartamenti (solo DXF)."""
    _cantiere(id_cantiere)
    cartella = config.PIANTE_DIR / app_db.cartella_di(id_cantiere)
    limite = config.MAX_UPLOAD_MB * 1024 * 1024
    destinazioni = []
    for f in file:
        nome = app_db.nome_cartella(Path(f.filename or "").name)
        if Path(nome).suffix.lower() not in config.ESTENSIONI_PIANTE:
            raise HTTPException(status_code=400, detail=f"{nome}: sono ammessi solo file .dxf e .dwg")
        if (cartella / nome).exists() and not sostituisci:
            raise HTTPException(status_code=409, detail=f"{nome} è già tra le piante di questo cantiere")
        destinazioni.append((f, cartella / nome))
    cartella.mkdir(parents=True, exist_ok=True)
    for f, dest in destinazioni:
        tmp = cartella / f".upload-{uuid.uuid4().hex}.tmp"
        dimensione = 0
        try:
            with tmp.open("wb") as out:
                while blocco := await f.read(1 << 20):
                    dimensione += len(blocco)
                    if dimensione > limite:
                        raise HTTPException(status_code=413, detail=f"{dest.name}: supera {config.MAX_UPLOAD_MB} MB")
                    out.write(blocco)
            if dimensione == 0:
                raise HTTPException(status_code=400, detail=f"{dest.name}: file vuoto")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
    return _scheda(id_cantiere, request)


@router.post("/cantieri/{id_cantiere}/appartamenti", response_model=Appartamento, status_code=201)
def aggiungi_appartamento(id_cantiere: int, dati: AppartamentoIn):
    """Aggiunge un appartamento a mano (per esempio se la pianta è un DWG)."""
    _cantiere(id_cantiere)
    if any(a["nome"].lower() == dati.nome.lower() and a["origine"] == "manuale" for a in app_db.elenco(id_cantiere)):
        raise HTTPException(status_code=409, detail=f"{dati.nome} esiste già in questo cantiere")
    return app_db.crea_manuale(id_cantiere, dati.nome)


@router.delete("/appartamenti/{id_appartamento}", status_code=204)
def elimina_appartamento(id_appartamento: int):
    """Elimina un appartamento aggiunto a mano. Quelli letti da una pianta tornerebbero alla prossima lettura."""
    a = _appartamento(id_appartamento)
    if a["origine"] == "comune":
        raise HTTPException(status_code=409, detail="Lo spazio comune della planimetria non si elimina")
    if a["origine"] == "pianta":
        raise HTTPException(status_code=409, detail=f"{a['nome']} viene dalla pianta {a['pianta']}: non si elimina")
    app_db.elimina(id_appartamento)
    return Response(status_code=204)


@router.post("/appartamenti/{id_appartamento}/committenti", response_model=Appartamento)
def aggiungi_committente(id_appartamento: int, dati: CommittenteIn):
    """Aggiunge un committente all'appartamento (di solito una persona con ruolo CL)."""
    a = _appartamento(id_appartamento)
    p = db.leggi_persona(dati.persona_id)
    if not p:
        raise HTTPException(status_code=400, detail="Persona non trovata")
    if not app_db.aggiungi_committente(id_appartamento, p["id"]):
        raise HTTPException(status_code=409, detail=f"{p['nome']} {p['cognome']} è già committente di {a['nome']}")
    return app_db.leggi(id_appartamento)


@router.delete("/appartamenti/{id_appartamento}/committenti/{id_persona}", response_model=Appartamento)
def togli_committente(id_appartamento: int, id_persona: int):
    _appartamento(id_appartamento)
    if not app_db.togli_committente(id_appartamento, id_persona):
        raise HTTPException(status_code=404, detail="Non è committente di questo appartamento")
    return app_db.leggi(id_appartamento)
