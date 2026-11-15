"""Contenuto di un appartamento come lo mostra il pannello media dell'app (PlanMediaPanel / WallStore).

I file sono quelli caricati dai telefoni (raccolta), con i percorsi della cartella dell'app
pareti/<DWG>/<APP n>/: appartamento → locali → pareti ("Parete N"), "Soffitto", "Pavimento".
In ogni livello, come in WallStore:
- foto: i file immagine dentro foto/ (solo quelli diretti);
- file: tutto quello che sta dentro file/, anche nelle sottocartelle create dall'utente;
- scansioni: le cartelle numerate <n>/ con wall.jpg (o wall.png) e metadata.json;
e i conteggi comprendono tutto quello che sta sotto (countTree / countScansTree).
I locali e le pareti vengono anche dal modello della planimetria, così si vedono pure quelli ancora vuoti.
"""
from __future__ import annotations

import json
import re

from . import raccolta as rc

FOTO, FILE = "foto", "file"
SOFFITTO, PAVIMENTO = "Soffitto", "Pavimento"
_PARETE = re.compile(r"^Parete (\d+)$")
_IMMAGINI = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp")
_VIETATI_CARTELLA = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def _numerica(nome: str) -> bool:
    return nome.isdigit()


def _superficie(nome: str) -> bool:
    return bool(_PARETE.match(nome)) or nome in (SOFFITTO, PAVIMENTO)


def _contenuto(nome: str) -> bool:
    return nome in (FOTO, FILE) or _numerica(nome)


def _nome_cartella_valido(nome: str) -> bool:
    """WallIds.sanitizeFolderName(name) == name."""
    v = _VIETATI_CARTELLA.sub("_", nome).strip()
    while v.endswith("."):
        v = v[:-1].strip()
    return v == nome and v not in ("", "..")


def _ordine_superficie(nome: str) -> int:
    if nome == SOFFITTO:
        return 2 ** 31 - 2
    if nome == PAVIMENTO:
        return 2 ** 31 - 1
    m = _PARETE.match(nome)
    return int(m.group(1)) if m else 0


def _nodo(percorso: str, nome: str, tipo: str) -> dict:
    return {"percorso": percorso, "nome": nome, "tipo": tipo, "figli": [],
            "foto": [], "file": [], "scansioni": {}, "allegati": [], "altri": [],
            "dal_modello": False}


def albero(file_raccolti: list[dict], locali_modello: list[dict] | None, cartella_file) -> dict[str, dict]:
    """{percorso: nodo}. `file_raccolti` come raccolta.elenco() (con url_download), `locali_modello` dal modello
    ({nome, pareti:[{nome}]}) o None, `cartella_file(nome_interno)` → Path del contenuto (per metadata.json)."""
    nodi: dict[str, dict] = {"": _nodo("", "", "appartamento")}

    def assicura(percorso: str) -> dict:
        if percorso in nodi:
            return nodi[percorso]
        parti = percorso.split("/")
        tipo = "locale" if len(parti) == 1 else ("parete" if _PARETE.match(parti[-1]) else "superficie")
        n = _nodo(percorso, parti[-1], tipo)
        nodi[percorso] = n
        padre = assicura("/".join(parti[:-1]))
        if percorso not in padre["figli"]:
            padre["figli"].append(percorso)
        return n

    for loc in locali_modello or []:
        n = assicura(loc["nome"])
        n["dal_modello"] = True
        for w in loc.get("pareti") or []:
            assicura(f'{loc["nome"]}/{w["nome"]}')["dal_modello"] = True
        for s in (SOFFITTO, PAVIMENTO):
            assicura(f'{loc["nome"]}/{s}')["dal_modello"] = True

    for f in file_raccolti:
        parti = f["percorso"].split("/")
        k = 0  # livello: 0 appartamento, 1 locale, 2 superficie
        nodo = nodi[""]
        while True:
            if k >= len(parti) - 1:  # file direttamente nella cartella del nodo (appartamento.json, report.json)
                nodo["altri"].append(f)
                break
            p = parti[k]
            if p == FOTO:
                if k == len(parti) - 2 and parti[-1].lower().endswith(_IMMAGINI):
                    nodo["foto"].append(f)
                else:
                    nodo["altri"].append(f)
                break
            if p == FILE:
                nodo["file"].append({**f, "sottocartella": "/".join(parti[k + 1:-1])})
                break
            if _numerica(p):
                sc = nodo["scansioni"].setdefault(p, {"cartella": p, "file": {}})
                sc["file"]["/".join(parti[k + 1:])] = f
                break
            if k == 0 and _nome_cartella_valido(p):
                nodo = assicura(p)
            elif k == 1 and _superficie(p):
                nodo = assicura("/".join(parti[:2]))
            else:
                nodo["altri"].append(f)
                break
            k += 1

    for n in nodi.values():
        # scansioni: solo le cartelle con l'immagine (come WallStore.loadRoomCaptures)
        scansioni = []
        for numero in sorted(n["scansioni"], key=int):
            sc = n["scansioni"][numero]
            img = sc["file"].get("wall.jpg") or sc["file"].get("wall.png")
            meta = sc["file"].get("metadata.json")
            dati = {}
            if meta is not None:
                try:
                    dati = json.loads(cartella_file(meta["file"]).read_text(encoding="utf-8"))
                except (OSError, ValueError, KeyError, TypeError):
                    dati = {}
            scansioni.append({
                "cartella": numero, "immagine": img, "metadata": meta,
                "nome": dati.get("name") or f"Scansione {numero}",
                "larghezza": dati.get("widthMeters"), "altezza": dati.get("heightMeters"),
                "creata": dati.get("createdAt"), "marker": dati.get("markerId"),
                "chi": (img or meta or {}).get("chi"),
            })
        n["scansioni"] = scansioni
        n["foto"].sort(key=lambda f: f.get("modificato_dispositivo") or f["caricato"], reverse=True)
        n["file"].sort(key=lambda f: f["percorso"].lower())
        if n["tipo"] == "appartamento":
            n["figli"].sort(key=lambda p: p.lower())
        elif n["tipo"] == "locale":
            n["figli"].sort(key=lambda p: _ordine_superficie(p.split("/")[-1]))
    _conta(nodi, "")
    return nodi


def _conta(nodi, percorso) -> dict:
    n = nodi[percorso]
    tot = {"scansioni": len(n["scansioni"]), "foto": len(n["foto"]), "file": len(n["file"])}
    for figlio in n["figli"]:
        sub = _conta(nodi, figlio)
        for k in tot:
            tot[k] += sub[k]
    n["conteggi"] = tot
    return tot


def assegna_allegati(nodi: dict[str, dict], attivita: list[dict], id_app: str | None) -> None:
    """Allegati delle attività con `riferimento` dentro questo appartamento: vanno sul nodo indicato."""
    if not id_app:
        return
    base = id_app + "/"
    for a in attivita:
        for f in a["allegati"]:
            rif = f.get("riferimento") or ""
            if rif == id_app:
                percorso = ""
            elif rif.startswith(base):
                percorso = rif[len(base):]
            else:
                continue
            while percorso not in nodi and "/" in percorso:
                percorso = percorso.rsplit("/", 1)[0]
            if percorso not in nodi:
                percorso = ""
            nodi[percorso]["allegati"].append({**f, "attivita_id": a["id"], "attivita": a["titolo"]})
    def conta(p):
        n = nodi[p]
        t = len(n["allegati"]) + sum(conta(c) for c in n["figli"])
        n["conteggi"]["allegati"] = t
        return t
    conta("")
