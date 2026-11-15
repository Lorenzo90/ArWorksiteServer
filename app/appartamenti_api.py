"""Scheda del cantiere: squadra, piante, appartamenti con committenti, attività (solo superuser)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import appartamenti as app_db
from . import attivita as att
from . import config, db, piante
from .attivita_api import Attivita, con_url
from .cantieri_api import Cantiere, Membro
from .sicurezza import admin_o_app, archivio_pronto, solo_admin

router = APIRouter(prefix="/api", tags=["appartamenti"], dependencies=[Depends(archivio_pronto), Depends(solo_admin)])
# Caricamento piante: pagina web (superuser) e app (token); il controllo è nella funzione.
router_app = APIRouter(prefix="/api", tags=["appartamenti"], dependencies=[Depends(archivio_pronto)])


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


async def _scrivi_pianta(f: UploadFile, dest: Path, limite_mb: int) -> None:
    """Scrive in un file temporaneo e lo rinomina solo alla fine: una pianta a metà non resta mai sul disco."""
    tmp = dest.parent / f".upload-{uuid.uuid4().hex}.tmp"
    limite = limite_mb * 1024 * 1024
    dimensione = 0
    try:
        with tmp.open("wb") as out:
            while blocco := await f.read(1 << 20):
                dimensione += len(blocco)
                if dimensione > limite:
                    raise HTTPException(status_code=413, detail=f"{dest.name}: supera {limite_mb} MB")
                out.write(blocco)
        if dimensione == 0:
            raise HTTPException(status_code=400, detail=f"{dest.name}: file vuoto")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


class PiantaCaricata(BaseModel):
    id: str = Field(description="Id della pianta, come in /api/piante")


@router_app.post("/cantieri/{id_cantiere}/piante", response_model=Scheda, status_code=201,
                 responses={201: {"description": "Superuser: la scheda del cantiere. App (token): {\"id\": \"<id pianta>\"}"}})
async def carica_pianta_cantiere(id_cantiere: int, request: Request,
                                 file: list[UploadFile] = File(..., description="Piante .dxf o .dwg"),
                                 sostituisci: bool = Form(default=False),
                                 persona: Optional[dict] = Depends(admin_o_app)):
    """Carica piante nella cartella del cantiere e ne legge gli appartamenti (solo DXF).
    - **Pagina web (superuser)**: uno o più file; un file con lo stesso nome solo con `sostituisci=true` (409).
    - **App (`Authorization: Bearer <token>`)**: un solo file nel campo `file`, chi è nella squadra del cantiere.
      Il nome del file resta quello mandato (l'app ritrova così i suoi dati in `pareti/<nome>/`); se esiste già
      viene sostituito, così un nuovo tentativo dopo un errore basta da solo. Crea l'appartamento della pianta
      («App. N» nel DXF, APP 1 se non c'è). Risposta 201 `{"id": "<id pianta>"}`.
      401 senza token, 404 cantiere inesistente, 403 senza accesso, 422 non DXF/DWG, 413 oltre
      ARWS_MAX_UPLOAD_APP_MB (50 MB)."""
    if persona is not None:
        return await _carica_dall_app(id_cantiere, file, persona)
    _cantiere(id_cantiere)
    cartella = config.PIANTE_DIR / app_db.cartella_di(id_cantiere)
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
        await _scrivi_pianta(f, dest, config.MAX_UPLOAD_MB)
    return _scheda(id_cantiere, request)


async def _carica_dall_app(id_cantiere: int, file: list[UploadFile], persona: dict) -> JSONResponse:
    _cantiere(id_cantiere)
    if not db.in_squadra(id_cantiere, persona["id"]):
        raise HTTPException(status_code=403, detail="Non fai parte della squadra di questo cantiere")
    file = [f for f in file if f.filename]
    if len(file) != 1:
        raise HTTPException(status_code=422, detail="Manda un solo file nel campo «file», con il suo nome")
    f = file[0]
    nome = app_db.nome_cartella(Path(f.filename.replace("\\", "/")).name)
    if Path(nome).suffix.lower() not in config.ESTENSIONI_PIANTE:
        raise HTTPException(status_code=422, detail=f"{nome}: sono ammessi solo file .dxf e .dwg")
    cartella = config.PIANTE_DIR / app_db.cartella_di(id_cantiere)
    cartella.mkdir(parents=True, exist_ok=True)
    dest = cartella / nome
    await _scrivi_pianta(f, dest, config.MAX_UPLOAD_APP_MB)
    app_db.sincronizza(id_cantiere)  # appartamenti dal testo «App. N», spazio comune della planimetria
    app_db.assicura_appartamento(id_cantiere, nome)
    p = next((p for p in piante.elenco() if p.percorso.resolve() == dest.resolve()), None)
    if p is None:  # non dovrebbe succedere: il file è appena stato scritto
        raise HTTPException(status_code=500, detail="Pianta salvata ma non trovata nell'elenco")
    return JSONResponse(status_code=201, content=PiantaCaricata(id=p.id).model_dump())


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
