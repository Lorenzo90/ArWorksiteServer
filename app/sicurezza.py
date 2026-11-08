"""Controlli di accesso.

- Pagine web e API di gestione: sessione del superuser (cookie dopo il login su /login).
- App: token della persona (`Authorization: Bearer`, da /api/login).
- `ARWS_API_KEY` (facoltativa): chi manda `X-Api-Key` giusta ha accesso tecnico completo alle API.
"""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import Header, HTTPException, Request

from . import appartamenti, config, db

COOKIE = "arws_sessione"

_inizializzati: set[str] = set()
_lock = threading.Lock()


def archivio_pronto() -> None:
    """Crea tabelle e dati di esempio la prima volta che si usa un archivio."""
    chiave = str(config.DB_PATH)
    if chiave in _inizializzati:
        return
    with _lock:
        if chiave not in _inizializzati:
            db.inizializza()
            appartamenti.inizializza()
            _inizializzati.add(chiave)


def verifica_chiave(x_api_key: Optional[str] = Header(default=None)) -> None:
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="Chiave API mancante o errata")


def admin_corrente(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    archivio_pronto()
    return db.admin_da_token(token)


def _chiave_giusta(x_api_key: Optional[str]) -> bool:
    return bool(config.API_KEY) and x_api_key == config.API_KEY


def solo_admin(request: Request, x_api_key: Optional[str] = Header(default=None)) -> None:
    """API di gestione: solo il superuser collegato (o la chiave API tecnica)."""
    if admin_corrente(request) or _chiave_giusta(x_api_key):
        return
    raise HTTPException(status_code=401, detail="Accesso riservato al superuser: fai il login su /login")


def admin_o_app(request: Request, authorization: Optional[str] = Header(default=None),
                x_api_key: Optional[str] = Header(default=None)) -> dict | None:
    """Lettura delle piante: il superuser dalle pagine web (→ None, vede tutto) oppure l'app con il
    token della persona (→ la persona: vede solo le piante dei cantieri/appartamenti assegnati)."""
    archivio_pronto()
    if admin_corrente(request) or _chiave_giusta(x_api_key):
        return None
    if config.API_KEY:
        raise HTTPException(status_code=401, detail="Chiave API mancante o errata")
    if authorization and authorization.lower().startswith("bearer "):
        persona = db.persona_da_token(authorization[7:].strip())
        if persona:
            return persona
    raise HTTPException(status_code=401, detail="Accesso richiesto: login del superuser o token dell'app",
                        headers={"WWW-Authenticate": "Bearer"})
