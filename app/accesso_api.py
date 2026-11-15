"""Accesso dall'app: login con utente e password, poi tutto quello che è assegnato a quella persona
(cantieri della sua squadra, appartamenti di cui è committente, piante da scaricare, attività)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import attivita as att
from . import db, pacchetto
from . import raccolta as rc
from .attivita_api import Attivita, NotaIn, StatoIn, con_url, controlla_nomi, salva_allegati
from .appartamenti_api import RiepilogoRaccolta
from .cantieri_api import Membro
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


class PiantaApp(BaseModel):
    id: str
    nome: str = Field(description="Nome del file, es. «APP 3_V1.dxf»")
    cantiere: Optional[str] = Field(description="Cartella del cantiere sul server (come in /api/piante)")
    formato: str
    dimensione: int
    modificato: str
    sha256: str = Field(description="Se è uguale a quello della copia sul telefono non serve riscaricare")
    url_download: str


class AppartamentoApp(BaseModel):
    id: int
    nome: str = Field(description="«APP 3»: stesso nome della cartella che usa l'app")
    origine: str = Field(description="pianta, manuale o comune («Spazio comune»: tutta la planimetria)")
    numero: Optional[int]
    etichetta: Optional[str] = Field(description="Testo trovato nel disegno, es. «u.a.03»")
    id_app: Optional[str] = Field(description="Id dell'appartamento nell'app: dwg:<nome pianta>/APP 3")
    pianta_id: Optional[str] = Field(description="Pianta che contiene l'appartamento (vedi `piante`)")
    pianta: Optional[str]
    committente: bool = Field(description="La persona collegata è committente di questo appartamento")
    committenti: list[Membro]
    raccolta: RiepilogoRaccolta = Field(description="Dati raccolti sul server: dettaglio in /api/appartamenti/{id}/raccolta")


class CantiereApp(BaseModel):
    id: int
    nome: str
    accesso: Literal["squadra", "committente"] = Field(
        description="squadra = tutto il cantiere; committente = solo gli appartamenti indicati")
    direttore_lavori: Optional[Membro]
    squadra: list[Membro]
    appartamenti: list[AppartamentoApp]
    piante: list[PiantaApp]


class MieiDati(BaseModel):
    persona: PersonaApp
    versione: str = Field(description="Impronta dei dati: uguale = niente di nuovo. Usala in If-None-Match")
    generato: str
    cantieri: list[CantiereApp]
    attivita: list[Attivita]


class LoginOut(MieiDati):
    token: str
    scadenza: str


class MieAttivita(BaseModel):
    persona: PersonaApp
    attivita: list[Attivita]


class MieiCantieri(BaseModel):
    persona: PersonaApp
    cantieri: list[CantiereApp]


def _cantieri(persona: dict, request: Request) -> list[dict]:
    risultato = []
    for c in pacchetto.cantieri(persona["id"]):
        pi = [{"id": p.id, "nome": p.nome, "cantiere": p.cantiere, "formato": p.formato, "dimensione": p.dimensione,
               "modificato": p.modificato, "sha256": p.sha256,
               "url_download": str(request.url_for("scarica_pianta", id_pianta=p.id))} for p in c["piante"]]
        risultato.append({**c, "piante": pi})
    return risultato


def _miei_dati(persona: dict, request: Request) -> dict:
    persona = {k: persona[k] for k in ("id", "nome", "cognome", "ruolo", "ruolo_nome", "username")}
    dati = {"persona": persona, "cantieri": _cantieri(persona, request),
            "attivita": [con_url(a, request) for a in att.elenco_per_app(persona)]}
    dati = MieiDati(versione="", generato="", **dati).model_dump(exclude={"versione", "generato"})
    return {**dati, "versione": pacchetto.versione(dati),
            "generato": datetime.now(timezone.utc).isoformat(timespec="seconds")}


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
    """Login della persona (es. `mario.rossi` + password). Restituisce il token (30 giorni) e tutto
    quello che le è assegnato: i cantieri della sua squadra (con tutti gli appartamenti e le piante),
    gli appartamenti di cui è committente (solo quelli, con la loro pianta) e le sue attività."""
    esito = db.accedi(dati.username, dati.password)
    if not esito:
        raise HTTPException(status_code=401, detail="Utente o password errati")
    token, scadenza, persona = esito
    return {"token": token, "scadenza": scadenza, **_miei_dati(persona, request)}


@router.get("/miei-dati", response_model=MieiDati,
            responses={304: {"description": "Niente di nuovo rispetto a If-None-Match"}})
def miei_dati(request: Request, persona: dict = Depends(persona_autenticata),
              if_none_match: Optional[str] = Header(default=None)):
    """Gli stessi dati del login, senza rifarlo: da chiamare all'apertura dell'app e a ogni
    «aggiorna». Con `If-None-Match: "<versione>"` risponde 304 se non è cambiato niente."""
    dati = _miei_dati(persona, request)
    etag = f'"{dati["versione"]}"'
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(jsonable_encoder(MieiDati(**dati)), headers={"ETag": etag})


@router.get("/miei-cantieri", response_model=MieiCantieri)
def miei_cantieri(request: Request, persona: dict = Depends(persona_autenticata)):
    """Solo i cantieri (come in /api/miei-dati)."""
    return {"persona": persona, "cantieri": _cantieri(persona, request)}


@router.get("/mie-attivita", response_model=MieAttivita)
def mie_attivita(request: Request, persona: dict = Depends(persona_autenticata)):
    """Attività assegnate alla persona del token, le più recenti prima. Gli allegati si scaricano con
    lo stesso token (`Authorization: Bearer`)."""
    return {"persona": persona, "attivita": [con_url(a, request) for a in att.elenco_per_app(persona)]}


@router.post("/mie-attivita/{id_attivita}/stato", response_model=Attivita)
def cambia_stato(id_attivita: int, dati: StatoIn, request: Request, persona: dict = Depends(persona_autenticata)):
    """Cambia lo stato di un'attività dall'app.
    - chi è assegnato: `aperta` → `presa_in_carico` (e indietro), `presa_in_carico` → `chiusa` (fatto),
      `da_verificare` → `chiusa` (ricontrollata), ma non se l'ha in carico un'altra persona;
    - il Direttore dei lavori della squadra del cantiere: qualsiasi passaggio (chiude o riapre).
    403 se il passaggio non è permesso, 404 se la persona non vede l'attività, 409 se è già in quello stato."""
    a = _attivita_visibile(id_attivita, persona)
    _controlla_passaggio(a, persona, dati.stato)
    nuova = att.cambia_stato(id_attivita, dati.stato, _nome(persona), persona["id"], dati.nota.strip())
    return con_url(nuova, request)


def _nome(persona: dict) -> str:
    return f"{persona['nome']} {persona['cognome']}"


def _attivita_visibile(id_attivita: int, persona: dict) -> dict:
    a = att.leggi(id_attivita)
    if not a or not att.visibile_a(a, persona):
        raise HTTPException(status_code=404, detail="Attività non trovata")
    return a


def _controlla_passaggio(a: dict, persona: dict, stato: str) -> None:
    if stato not in att.STATI:
        raise HTTPException(status_code=422, detail=f"Stato «{stato}» sconosciuto: aperta, presa_in_carico, da_verificare, chiusa")
    if a["stato"] == stato:
        raise HTTPException(status_code=409, detail=f"L'attività è già «{att.STATI[stato]}»")
    if att.e_dl_del_cantiere(a, persona):
        return
    incaricato = a.get("in_carico")
    if incaricato and incaricato.get("persona_id") not in (None, persona["id"]):
        raise HTTPException(status_code=403, detail=f"L'attività è in carico a {incaricato['chi']}")
    permessi = att.PASSAGGI_AUTORE if att.e_autore(a, persona) else att.PASSAGGI_ASSEGNATARIO
    if (a["stato"], stato) not in permessi:
        raise HTTPException(status_code=403,
                            detail="Passaggio non permesso: chi è assegnato prende in carico e chiude; "
                                   "riaprire o mettere «Da verificare» spetta al Direttore dei lavori")


class EsitoAggiornamento(BaseModel):
    caricati: list[int] = Field(description="Id dei nuovi allegati")
    attivita: Attivita


@router.post("/mie-attivita/{id_attivita}/aggiornamento", response_model=EsitoAggiornamento)
async def aggiorna_attivita(id_attivita: int, request: Request,
                            persona: dict = Depends(persona_autenticata),
                            stato: Optional[str] = Form(default=None, description="Facoltativo: nuovo stato"),
                            nota: str = Form(default="", max_length=2000),
                            riferimento: Optional[str] = Form(default=None, max_length=400,
                                                              description="Id app della parete/stanza/appartamento"),
                            file: Optional[list[UploadFile]] = File(default=None,
                                                                    description="Foto, GLB, PDF... (campo ripetibile)")):
    """Aggiornamento dal telefono in una sola chiamata (multipart): allegati e/o cambio di stato.
    Esempio tipico: l'elettricista manda le foto della parete e `stato=da_verificare`.
    Prima si controlla tutto (permessi, passaggio di stato, nomi dei file), poi si salvano i file e solo alla
    fine si cambia lo stato: se un file non passa non cambia niente. Gli allegati portano il nome di chi li
    ha caricati e `riferimento`."""
    a = _attivita_visibile(id_attivita, persona)
    file = [f for f in (file or []) if f.filename]
    if stato:
        _controlla_passaggio(a, persona, stato)
    if not file and not stato and not nota.strip():
        raise HTTPException(status_code=400, detail="Niente da aggiornare: manda dei file, lo stato o una nota")
    controlla_nomi(file)
    caricati = await salva_allegati(id_attivita, file, persona["id"], _nome(persona),
                                    (riferimento or "").strip() or None) if file else []
    if stato:
        att.cambia_stato(id_attivita, stato, _nome(persona), persona["id"], nota.strip())
    elif nota.strip():  # senza cambio di stato la nota diventa una nota libera
        att.aggiungi_nota(id_attivita, nota.strip(), _nome(persona), persona["id"])
    return {"caricati": [c["id"] for c in caricati], "attivita": con_url(att.leggi(id_attivita), request)}


@router.post("/mie-attivita/{id_attivita}/note", response_model=Attivita, status_code=201)
def aggiungi_nota(id_attivita: int, dati: NotaIn, request: Request, persona: dict = Depends(persona_autenticata)):
    """Nota libera dal telefono (chi è assegnato o il DL del cantiere), senza cambiare lo stato."""
    _attivita_visibile(id_attivita, persona)
    return con_url(att.aggiungi_nota(id_attivita, dati.testo, _nome(persona), persona["id"]), request)


@router.post("/mie-segnalazioni", response_model=Attivita, status_code=201)
async def nuova_segnalazione(request: Request,
                             persona: dict = Depends(persona_autenticata),
                             appartamento_id: int = Form(description="Id dell'appartamento sul server"),
                             descrizione: str = Form(max_length=5000),
                             titolo: str = Form(default="", max_length=200,
                                                description="Facoltativo: se vuoto, l'inizio della descrizione"),
                             persone_ids: str = Form(default="",
                                                     description="Id separati da virgola; vuoto = tutto il cantiere"),
                             riferimento: Optional[str] = Form(default=None, max_length=400,
                                                               description="Id app della parete/stanza + percorso"),
                             file: Optional[list[UploadFile]] = File(default=None,
                                                                     description="Foto con le note, file... (ripetibile)")):
    """Segnalazione dal telefono: un contenuto (foto, scansione, file) con una descrizione, mandato ad alcune
    persone del cantiere. Diventa un'attività con autore = chi la manda e assegnatari = le persone scelte
    (vuoto = la squadra del cantiere e i committenti dell'appartamento). Stati come le attività; l'autore
    la vede sempre e può anche riaprirla.
    404 se la persona non vede l'appartamento, 422 descrizione vuota o persone fuori dal cantiere."""
    descrizione = descrizione.strip()
    if not descrizione:
        raise HTTPException(status_code=422, detail="La descrizione non può essere vuota")
    acc = rc.accesso(appartamento_id, persona)
    if not acc or not acc["leggere"]:
        raise HTTPException(status_code=404, detail="Appartamento non trovato")
    ap = acc["appartamento"]
    possibili = _persone_dell_appartamento(appartamento_id, ap["cantiere_id"])
    try:
        scelti = {int(x) for x in persone_ids.replace(";", ",").split(",") if x.strip()}
    except ValueError:
        raise HTTPException(status_code=422, detail="persone_ids: id non validi")
    fuori = scelti - possibili - {persona["id"]}
    if fuori:
        raise HTTPException(status_code=422, detail=f"Persone non del cantiere: {sorted(fuori)}")
    assegnati = (scelti or possibili) - {persona["id"]}
    file = [f for f in (file or []) if f.filename]
    controlla_nomi(file)
    if not titolo.strip():
        prima = descrizione.splitlines()[0]
        titolo = prima if len(prima) <= 80 else prima[:79] + "…"
    nuova = att.crea(titolo.strip(), descrizione, ap["cantiere_id"], sorted(assegnati), appartamento_id,
                     autore_id=persona["id"])
    try:
        if file:
            await salva_allegati(nuova["id"], file, persona["id"], _nome(persona),
                                 (riferimento or "").strip() or None)
    except Exception:
        att.elimina(nuova["id"])  # niente a metà: se un file non passa la segnalazione non resta
        raise
    return con_url(att.leggi(nuova["id"]), request)


def _persone_dell_appartamento(id_appartamento: int, id_cantiere: int) -> set[int]:
    """La squadra del cantiere e i committenti dell'appartamento."""
    with db.connessione() as con:
        squadra = {r[0] for r in con.execute("SELECT persona_id FROM squadre WHERE cantiere_id = ?", (id_cantiere,))}
        committenti = {r[0] for r in con.execute(
            "SELECT persona_id FROM appartamento_committenti WHERE appartamento_id = ?", (id_appartamento,))}
    return squadra | committenti


@router.post("/logout", status_code=204)
def logout(authorization: Optional[str] = Header(default=None),
           _: dict = Depends(persona_autenticata)):
    db.esci(authorization[7:].strip())
    return Response(status_code=204)
