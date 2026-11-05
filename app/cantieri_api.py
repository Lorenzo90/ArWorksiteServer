"""API di ruoli, persone, cantieri e squadre."""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from . import db
from .sicurezza import archivio_pronto, solo_admin

router = APIRouter(prefix="/api", dependencies=[Depends(archivio_pronto), Depends(solo_admin)])


def _testo(v: str) -> str:
    v = " ".join((v or "").split())
    if not v:
        raise ValueError("non può essere vuoto")
    return v


# --- Modelli -----------------------------------------------------------------

class Ruolo(BaseModel):
    sigla: str
    nome: str
    descrizione: str


class PersonaIn(BaseModel):
    nome: str = Field(max_length=60)
    cognome: str = Field(max_length=60)
    ruolo: str = Field(default=db.RUOLO_CC, description="Sigla del ruolo (vedi /api/ruoli), es. DL, CC, EL")
    _pulisci = field_validator("nome", "cognome")(_testo)


class PersonaModifica(BaseModel):
    ruolo: str = Field(description="Sigla del ruolo")


class Persona(BaseModel):
    id: int
    nome: str
    cognome: str
    ruolo: str
    ruolo_nome: Optional[str]
    username: str
    cantieri: int


class PersonaConPassword(Persona):
    password: str = Field(description="Mostrata solo adesso: il server conserva solo l'impronta")


class NuovaPassword(BaseModel):
    username: str
    password: str


class Membro(BaseModel):
    id: int
    nome: str
    cognome: str
    ruolo: str
    ruolo_nome: Optional[str]


class CantiereIn(BaseModel):
    nome: str = Field(max_length=120)
    direttore_lavori_id: Optional[int] = Field(default=None, description="Se assente: Mario Rossi")
    _pulisci = field_validator("nome")(_testo)


class CantiereModifica(BaseModel):
    nome: str = Field(max_length=120)
    _pulisci = field_validator("nome")(_testo)


class MembroIn(BaseModel):
    persona_id: int


class Cantiere(BaseModel):
    id: int
    nome: str
    creato: str
    direttore_lavori: Optional[Membro]
    squadra: list[Membro]


def _verifica_ruolo(sigla: str) -> str:
    sigla = (sigla or "").strip().upper()
    if not db.ruolo_esiste(sigla):
        raise HTTPException(status_code=400, detail=f"Ruolo «{sigla}» sconosciuto: vedi /api/ruoli")
    return sigla


def _persona(id_persona: int) -> dict:
    p = db.leggi_persona(id_persona)
    if not p:
        raise HTTPException(status_code=400, detail="Persona non trovata")
    return p


def _cantiere(id_cantiere: int) -> dict:
    c = db.leggi_cantiere(id_cantiere)
    if not c:
        raise HTTPException(status_code=404, detail="Cantiere non trovato")
    return c


# --- Ruoli -------------------------------------------------------------------

@router.get("/ruoli", response_model=list[Ruolo], tags=["ruoli"])
def elenco_ruoli():
    """Ruoli del file Attivita_e_ruoli_appartamento.xlsx (foglio Ruoli), nell'ordine del file."""
    return db.elenco_ruoli()


# --- Persone -----------------------------------------------------------------

@router.get("/persone", response_model=list[Persona], tags=["persone"])
def elenco_persone():
    return db.elenco_persone()


@router.post("/persone", response_model=PersonaConPassword, status_code=201, tags=["persone"])
def crea_persona(dati: PersonaIn):
    """Aggiunge una persona con username e password generati. La password compare solo in questa risposta."""
    ruolo = _verifica_ruolo(dati.ruolo)
    try:
        persona, password = db.crea_persona(dati.nome, dati.cognome, ruolo)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"{dati.nome} {dati.cognome} è già nell'elenco")
    return {**persona, "password": password}


@router.patch("/persone/{id_persona}", response_model=Persona, tags=["persone"])
def modifica_persona(id_persona: int, dati: PersonaModifica):
    """Cambia il ruolo della persona (vale in tutte le squadre di cui fa parte)."""
    if not db.leggi_persona(id_persona):
        raise HTTPException(status_code=404, detail="Persona non trovata")
    return db.cambia_ruolo(id_persona, _verifica_ruolo(dati.ruolo))


@router.post("/persone/{id_persona}/password", response_model=NuovaPassword, tags=["persone"])
def rigenera_password(id_persona: int):
    """Genera una nuova password (la vecchia smette di funzionare e le sessioni aperte vengono chiuse)."""
    password = db.nuova_password(id_persona)
    if password is None:
        raise HTTPException(status_code=404, detail="Persona non trovata")
    return {"username": db.leggi_persona(id_persona)["username"], "password": password}


@router.delete("/persone/{id_persona}", status_code=204, tags=["persone"])
def elimina_persona(id_persona: int):
    """Elimina la persona: esce dalle squadre dei cantieri e non può più accedere."""
    if not db.elimina_persona(id_persona):
        raise HTTPException(status_code=404, detail="Persona non trovata")
    return Response(status_code=204)


# --- Cantieri ----------------------------------------------------------------

@router.get("/cantieri", response_model=list[Cantiere], tags=["cantieri"])
def elenco_cantieri():
    return db.elenco_cantieri()


@router.get("/cantieri/{id_cantiere}", response_model=Cantiere, tags=["cantieri"])
def leggi_cantiere(id_cantiere: int):
    return _cantiere(id_cantiere)


@router.post("/cantieri", response_model=Cantiere, status_code=201, tags=["cantieri"])
def crea_cantiere(dati: CantiereIn):
    """Crea un cantiere con il suo Direttore dei lavori. Senza `direttore_lavori_id` è Mario Rossi."""
    dl_id = dati.direttore_lavori_id if dati.direttore_lavori_id is not None else db.id_dl_predefinito()
    if dl_id is not None:
        p = _persona(dl_id)
        if p["ruolo"] != db.RUOLO_DL:
            raise HTTPException(status_code=400,
                                detail=f"{p['nome']} {p['cognome']} non ha il ruolo Direttore dei lavori")
    try:
        return db.crea_cantiere(dati.nome, dl_id)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Esiste già un cantiere chiamato «{dati.nome}»")


@router.patch("/cantieri/{id_cantiere}", response_model=Cantiere, tags=["cantieri"])
def rinomina_cantiere(id_cantiere: int, dati: CantiereModifica):
    _cantiere(id_cantiere)
    try:
        return db.rinomina_cantiere(id_cantiere, dati.nome)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"Esiste già un cantiere chiamato «{dati.nome}»")


@router.delete("/cantieri/{id_cantiere}", status_code=204, tags=["cantieri"])
def elimina_cantiere(id_cantiere: int):
    """Elimina il cantiere e la sua squadra. Le piante caricate per quel cantiere restano sul disco."""
    if not db.elimina_cantiere(id_cantiere):
        raise HTTPException(status_code=404, detail="Cantiere non trovato")
    return Response(status_code=204)


# --- Squadre -----------------------------------------------------------------

@router.post("/cantieri/{id_cantiere}/squadra", response_model=Cantiere, tags=["squadre"])
def aggiungi_in_squadra(id_cantiere: int, dati: MembroIn):
    """Aggiunge una persona alla squadra del cantiere, con il suo ruolo."""
    c = _cantiere(id_cantiere)
    p = _persona(dati.persona_id)
    if not db.aggiungi_in_squadra(id_cantiere, p["id"]):
        raise HTTPException(status_code=409, detail=f"{p['nome']} {p['cognome']} è già nella squadra di «{c['nome']}»")
    return db.leggi_cantiere(id_cantiere)


@router.delete("/cantieri/{id_cantiere}/squadra/{id_persona}", response_model=Cantiere, tags=["squadre"])
def togli_da_squadra(id_cantiere: int, id_persona: int):
    _cantiere(id_cantiere)
    if not db.togli_da_squadra(id_cantiere, id_persona):
        raise HTTPException(status_code=404, detail="La persona non è nella squadra di questo cantiere")
    return db.leggi_cantiere(id_cantiere)
