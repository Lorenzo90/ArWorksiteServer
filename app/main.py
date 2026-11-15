"""API REST del servizio ARworksite (demo).

Avvio:  uvicorn app.main:app --host 0.0.0.0 --port 8000
Doc interattiva: http://<ip-pc>:8000/docs
"""
from __future__ import annotations

import html
import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import (__version__, accesso_api, appartamenti, appartamenti_api, attivita_api, cantieri_api, config, db, pacchetto,
               modelli, piante, raccolta_api, vista_api)
from .sicurezza import COOKIE, admin_corrente, admin_o_app, archivio_pronto, solo_admin

# La documentazione è servita più sotto, solo al superuser collegato.
app = FastAPI(title="ARworksite Server", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
              description="Servizio di condivisione dati di cantiere – demo interna.")


@app.on_event("startup")
def _avvio() -> None:
    # Al primo avvio crea l'archivio, il superuser e il file con le password iniziali.
    archivio_pronto()
    modelli.prepara_tutte()  # modelli delle planimetrie (pareti) in sottofondo


app.add_middleware(GZipMiddleware, minimum_size=2000)
app.include_router(cantieri_api.router)
app.include_router(accesso_api.router)
app.include_router(attivita_api.router)
app.include_router(attivita_api.router_file)
app.include_router(appartamenti_api.router)
app.include_router(raccolta_api.router)
app.include_router(vista_api.router)


class PiantaOut(BaseModel):
    id: str
    nome: str
    cantiere: Optional[str]
    formato: str
    dimensione: int
    modificato: str
    sha256: str
    url_download: str


class ElencoPiante(BaseModel):
    totale: int
    piante: list[PiantaOut]


class EsitoCaricamento(BaseModel):
    caricate: list[PiantaOut]


def _out(p: piante.Pianta, request: Request) -> PiantaOut:
    return PiantaOut(
        id=p.id, nome=p.nome, cantiere=p.cantiere, formato=p.formato,
        dimensione=p.dimensione, modificato=p.modificato, sha256=p.sha256,
        url_download=str(request.url_for("scarica_pianta", id_pianta=p.id)),
    )


_NOME_NON_VALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _nome_sicuro(nome: str, cosa: str) -> str:
    """Un solo componente di percorso, valido su Windows, senza '..' né nomi nascosti."""
    nome = (nome or "").strip().strip(".")
    nome = _NOME_NON_VALIDO.sub("_", nome)
    if not nome or nome.startswith("."):
        raise HTTPException(status_code=400, detail=f"{cosa} non valido")
    return nome[:150]


def _cartella_cantiere(cantiere: Optional[str]) -> Path:
    root = config.PIANTE_DIR
    if not cantiere or not cantiere.strip():
        return root
    return root / _nome_sicuro(cantiere, "Nome del cantiere")


@app.get("/api/health", tags=["servizio"])
def health():
    return {"stato": "ok", "versione": __version__}


def _visibili(persona: Optional[dict]) -> Optional[set[str]]:
    return None if persona is None else pacchetto.piante_consentite(persona["id"])


@app.get("/api/piante", response_model=ElencoPiante, tags=["piante"])
def elenco_piante(request: Request, cantiere: Optional[str] = None, formato: Optional[str] = None,
                  persona: Optional[dict] = Depends(admin_o_app)):
    """Elenco delle piante. `sha256` serve al client per sapere se la copia locale è aggiornata.
    Con il token dell'app solo le piante dei cantieri/appartamenti assegnati alla persona."""
    lista = piante.elenco()
    visibili = _visibili(persona)
    if visibili is not None:
        lista = [p for p in lista if p.id in visibili]
    if cantiere:
        lista = [p for p in lista if (p.cantiere or "").lower() == cantiere.lower()]
    if formato:
        lista = [p for p in lista if p.formato == formato.lower().lstrip(".")]
    return ElencoPiante(totale=len(lista), piante=[_out(p, request) for p in lista])


@app.post("/api/piante", response_model=EsitoCaricamento, status_code=201, tags=["piante"],
          dependencies=[Depends(solo_admin)])
async def carica_piante(request: Request,
                        file: list[UploadFile] = File(..., description="Uno o più file .dxf/.dwg"),
                        cantiere: Optional[str] = Form(default=None),
                        sostituisci: bool = Form(default=False)):
    """Carica una o più piante nella cartella del cantiere. Un file con lo stesso nome viene
    sovrascritto solo con `sostituisci=true` (altrimenti 409)."""
    cartella = _cartella_cantiere(cantiere)
    limite = config.MAX_UPLOAD_MB * 1024 * 1024

    # Controlli su tutti i file prima di scrivere qualcosa.
    destinazioni: list[tuple[UploadFile, Path]] = []
    for f in file:
        nome = _nome_sicuro(Path(f.filename or "").name, "Nome del file")
        if Path(nome).suffix.lower() not in config.ESTENSIONI_PIANTE:
            raise HTTPException(status_code=400, detail=f"{nome}: sono ammessi solo file .dxf e .dwg")
        dest = cartella / nome
        if dest.exists() and not sostituisci:
            raise HTTPException(status_code=409, detail=f"{nome} esiste già in questo cantiere")
        destinazioni.append((f, dest))
    if len({d for _, d in destinazioni}) != len(destinazioni):
        raise HTTPException(status_code=400, detail="Due file con lo stesso nome nello stesso caricamento")

    cartella.mkdir(parents=True, exist_ok=True)
    scritti: list[Path] = []
    for f, dest in destinazioni:
        tmp = cartella / f".upload-{uuid.uuid4().hex}.tmp"
        dimensione = 0
        try:
            with tmp.open("wb") as out:
                while blocco := await f.read(1 << 20):
                    dimensione += len(blocco)
                    if dimensione > limite:
                        raise HTTPException(status_code=413,
                                            detail=f"{dest.name}: supera {config.MAX_UPLOAD_MB} MB")
                    out.write(blocco)
            if dimensione == 0:
                raise HTTPException(status_code=400, detail=f"{dest.name}: file vuoto")
            os.replace(tmp, dest)
            scritti.append(dest)
        finally:
            if tmp.exists():
                tmp.unlink()

    if cantiere and cantiere.strip():
        appartamenti.sincronizza_cartella(cartella.name)  # legge gli appartamenti del cantiere
    tutte = {p.percorso.resolve(): p for p in piante.elenco()}
    return EsitoCaricamento(caricate=[_out(tutte[d.resolve()], request) for d in scritti if d.resolve() in tutte])


@app.get("/api/piante/{id_pianta}", response_model=PiantaOut, tags=["piante"])
def dettaglio_pianta(id_pianta: str, request: Request, persona: Optional[dict] = Depends(admin_o_app)):
    p = piante.trova(id_pianta)
    visibili = _visibili(persona)
    if not p or (visibili is not None and p.id not in visibili):
        raise HTTPException(status_code=404, detail="Pianta non trovata")
    return _out(p, request)


@app.get("/api/piante/{id_pianta}/file", tags=["piante"], name="scarica_pianta",
         responses={200: {"content": {"application/octet-stream": {}}}, 304: {"description": "Non modificata"}})
def scarica_pianta(id_pianta: str, if_none_match: Optional[str] = Header(default=None),
                   persona: Optional[dict] = Depends(admin_o_app)):
    """Scarica il file. Con `If-None-Match: "<sha256>"` risponde 304 se il client ha già questa versione.
    Con il token dell'app solo le piante assegnate alla persona (le altre rispondono 404)."""
    p = piante.trova(id_pianta)
    visibili = _visibili(persona)
    if not p or (visibili is not None and p.id not in visibili):
        raise HTTPException(status_code=404, detail="Pianta non trovata")
    etag = f'"{p.sha256}"'
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers={"ETag": etag})
    media = "image/vnd.dxf" if p.formato == "dxf" else "image/vnd.dwg"
    return FileResponse(p.percorso, media_type=media, filename=p.nome, headers={"ETag": etag})


# --- Pagine web (solo superuser) ----------------------------------------------

_CARTELLA = Path(__file__).parent


def _pagina(nome: str, impostazioni: dict) -> str:
    return (_CARTELLA / nome).read_text(encoding="utf-8").replace(
        "/*IMPOSTAZIONI*/{}", json.dumps(impostazioni).replace("<", "\\u003c"))


def _al_login(request: Request) -> RedirectResponse:
    return RedirectResponse(f"/login?dopo={request.url.path}", status_code=303)


def _destinazione_sicura(dopo: Optional[str]) -> str:
    """Solo percorsi di questo server (niente //altro-sito o http://...)."""
    if dopo and dopo.startswith("/") and not dopo.startswith("//") and "\\" not in dopo:
        return dopo
    return "/cantieri"


def _pagina_login(errore: str = "", dopo: str = "/cantieri", utente: str = "", stato: int = 200) -> HTMLResponse:
    testo = (_CARTELLA / "login.html").read_text(encoding="utf-8")
    testo = (testo.replace("{{VERSIONE}}", __version__)
                  .replace("{{ERRORE}}", f'<p class="errore">{html.escape(errore)}</p>' if errore else "")
                  .replace("{{DOPO}}", html.escape(dopo))
                  .replace("{{UTENTE}}", html.escape(utente)))
    return HTMLResponse(testo, status_code=stato, headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def pagina_login(request: Request, dopo: Optional[str] = None):
    dopo = _destinazione_sicura(dopo)
    archivio_pronto()
    if admin_corrente(request):
        return RedirectResponse(dopo, status_code=303)
    return _pagina_login(dopo=dopo)


@app.post("/login", include_in_schema=False)
def esegui_login(username: str = Form(default=""), password: str = Form(default=""),
                 dopo: str = Form(default="/cantieri")):
    archivio_pronto()
    dopo = _destinazione_sicura(dopo)
    esito = db.accedi_admin(username, password)
    if not esito:
        return _pagina_login("Utente o password errati.", dopo, username, stato=401)
    token, _, _ = esito
    r = RedirectResponse(dopo, status_code=303)
    r.set_cookie(COOKIE, token, max_age=int(db.DURATA_SESSIONE_ADMIN.total_seconds()),
                 httponly=True, samesite="lax", path="/")
    return r


@app.post("/logout", include_in_schema=False)
def esegui_logout(request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        archivio_pronto()
        db.esci_admin(token)
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE, path="/")
    return r


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/cantieri", response_class=HTMLResponse, include_in_schema=False)
def pagina_cantieri(request: Request):
    admin = admin_corrente(request)
    if not admin:
        return _al_login(request)
    return _pagina("cantieri.html", {"versione": __version__, "chiave": False, "utente": admin["username"]})


@app.get("/cantieri/{id_cantiere}", response_class=HTMLResponse, include_in_schema=False)
def pagina_scheda_cantiere(id_cantiere: int, request: Request):
    admin = admin_corrente(request)
    if not admin:
        return _al_login(request)
    return _pagina("cantiere.html", {"versione": __version__, "utente": admin["username"],
                                     "maxMb": config.MAX_UPLOAD_MB, "cantiere": id_cantiere})


@app.get("/cantieri/{id_cantiere}/vista", response_class=HTMLResponse, include_in_schema=False)
def pagina_vista_cantiere(id_cantiere: int, request: Request):
    admin = admin_corrente(request)
    if not admin:
        return _al_login(request)
    return _pagina("vista.html", {"versione": __version__, "utente": admin["username"], "cantiere": id_cantiere})


@app.get("/attivita", include_in_schema=False)
def vecchia_pagina_attivita():
    return RedirectResponse("/cantieri", status_code=303)


@app.get("/openapi.json", include_in_schema=False)
def schema_api(request: Request):
    if not admin_corrente(request):
        return JSONResponse({"detail": "Accesso riservato al superuser"}, status_code=401)
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
def documentazione(request: Request):
    if not admin_corrente(request):
        return _al_login(request)
    return get_swagger_ui_html(openapi_url="/openapi.json", title="ARworksite Server · API")
