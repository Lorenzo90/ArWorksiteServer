"""Accesso dall'app: login con utente e password, poi i cantieri assegnati a quella persona."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel

from . import attivita as att
from . import db
from .attivita_api import Attivita, con_url
from .cantieri_api import Cantiere
from .sicurezza import archivio_pronto, verifica_chiave

router = APIRouter(prefix="/api", tags=["accesso app"],
                   dependencies=[Depends(verifica_chiave), Depends(archivio_pronto)])


class LoginIn(BaseModel):
    username: str
    password: str


class PersonaApp(BaseModel):
    id: int
    nome: str
    cognome: str
    ruolo: str
    ruolo_nome: Optional[str]
    username: str


class LoginOut(BaseModel):
    token: str
    scadenza: str
    persona: PersonaApp
    cantieri: list[Cantiere]
    attivita: list[Attivita]


class MieAttivita(BaseModel):
    persona: PersonaApp
    attivita: list[Attivita]


class MieiCantieri(BaseModel):
    persona: PersonaApp
    cantieri: list[Cantiere]


def persona_autenticata(authorization: Optional[str] = Header(default=None)) -> dict:
    """Legge `Authorization: Bearer <token>` (il token restituito da /api/login)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Accesso richiesto",
                            headers={"WWW-Authenticate": "Bearer"})
    persona = db.persona_da_token(authorization[7:].strip())
    if not persona:
        raise HTTPException(status_code=401, detail="Sessione scaduta o non valida: rifai il login",
                            headers={"WWW-Authenticate": "Bearer"})
    return persona


@router.post("/login", response_model=LoginOut)
def login(dati: LoginIn, request: Request):
    """Login della persona (es. `mario.rossi` + password). Restituisce un token valido 30 giorni
    i cantieri in cui la persona fa parte della squadra (con tutta la squadra di ognuno)
    e le attività assegnate a lei, con gli indirizzi per scaricare gli allegati."""
    esito = db.accedi(dati.username, dati.password)
    if not esito:
        raise HTTPException(status_code=401, detail="Utente o password errati")
    token, scadenza, persona = esito
    return {"token": token, "scadenza": scadenza, "persona": persona,
            "cantieri": db.elenco_cantieri(persona_id=persona["id"]),
            "attivita": [con_url(a, request) for a in att.elenco(persona["id"])]}


@router.get("/miei-cantieri", response_model=MieiCantieri)
def miei_cantieri(persona: dict = Depends(persona_autenticata)):
    """Cantieri della squadra di cui fa parte la persona del token (per aggiornare l'elenco senza rifare il login)."""
    return {"persona": persona, "cantieri": db.elenco_cantieri(persona_id=persona["id"])}


@router.get("/mie-attivita", response_model=MieAttivita)
def mie_attivita(request: Request, persona: dict = Depends(persona_autenticata)):
    """Attività assegnate alla persona del token, le più recenti prima. Gli allegati si scaricano con
    lo stesso token (`Authorization: Bearer`)."""
    return {"persona": persona, "attivita": [con_url(a, request) for a in att.elenco(persona["id"])]}


@router.post("/logout", status_code=204)
def logout(authorization: Optional[str] = Header(default=None),
           _: dict = Depends(persona_autenticata)):
    db.esci(authorization[7:].strip())
    return Response(status_code=204)
