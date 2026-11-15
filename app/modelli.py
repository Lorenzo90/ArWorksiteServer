"""Modello di una planimetria per la pagina web: geometria, appartamenti, locali e pareti come nell'app.

Calcolare le pareti richiede qualche secondo per appartamento, quindi il modello si prepara una volta
in sottofondo e si salva in <archivio>/modelli/<sha256>.json: cambia il file, cambia lo sha256, si rifà.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import threading
import time
from pathlib import Path

from . import config, piante
from . import pianta_modello as pm

log = logging.getLogger("arworksite.modelli")
VERSIONE = 1

_coda: "queue.Queue[piante.Pianta]" = queue.Queue()
_in_corso: set[str] = set()
_errori: dict[str, str] = {}
_lock = threading.Lock()
_lavoratore: threading.Thread | None = None


def cartella() -> Path:
    return Path(config.DB_PATH).parent / "modelli"


def _file(p: piante.Pianta) -> Path:
    return cartella() / f"{p.sha256}.json"


def _r(v: float) -> float:
    return round(v, 3)  # millimetri


def calcola(p: piante.Pianta) -> dict:
    """Legge la planimetria e calcola tutto (lento: qualche secondo per appartamento)."""
    if p.formato != "dxf":
        raise pm.DxfNonLeggibile("Le piante DWG non si leggono sul server: carica anche il DXF")
    t0 = time.time()
    piano = pm.leggi_dxf(p.percorso)
    apps, titolo = pm.analizza(piano)
    visibili = pm.layer_visibili(piano)
    colori = pm.colori_layer(piano)
    segmenti = [[] for _ in piano.layers]
    s = piano.seg
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for i in range(piano.n_segmenti()):
        x1, y1, x2, y2 = s[i * 4], s[i * 4 + 1], s[i * 4 + 2], s[i * 4 + 3]
        segmenti[piano.seg_layer[i]].extend((_r(x1), _r(y1), _r(x2), _r(y2)))
        if visibili[piano.seg_layer[i]]:
            min_x, max_x = min(min_x, x1, x2), max(max_x, x1, x2)
            min_y, max_y = min(min_y, y1, y2), max(max_y, y1, y2)
    appartamenti = []
    for a in apps:
        pareti = pm.pareti_appartamento(piano, visibili, a) if a.locali else {}
        locali = []
        for k, loc in enumerate(a.locali):
            pp = pareti.get(k)
            locali.append({
                "nome": loc.nome, "tipo": loc.tipo, "sorgente": loc.sorgente,
                "area": round(loc.area, 2), "altezza": None if math.isnan(loc.altezza) else round(loc.altezza, 2),
                "x": _r(loc.x), "y": _r(loc.y),
                "pareti": None if pp is None else [
                    {"numero": w.numero, "nome": f"Parete {w.numero}", "x1": _r(w.x1), "y1": _r(w.y1),
                     "x2": _r(w.x2), "y2": _r(w.y2), "lunghezza": round(w.lunghezza(), 2),
                     "nx": round(w.nx, 4), "ny": round(w.ny, 4)} for w in pp],
            })
        lp = pm.punto_etichetta(a.poligono) if len(a.poligono) >= 6 else [a.lx, a.ly]
        appartamenti.append({
            "numero": a.numero, "nome": a.nome, "etichetta": a.etichetta,
            "poligono": [_r(v) for v in a.poligono],
            "area_perimetro": round(abs(pm.area_con_segno(a.poligono)), 2) if a.poligono else None,
            "area_locali": round(sum(l.area for l in a.locali), 2),
            "etichetta_xy": [_r(lp[0]), _r(lp[1])],
            "locali": locali,
        })
    if not math.isfinite(min_x):
        min_x = min_y = 0.0
        max_x = max_y = 1.0
    log.info("Modello di %s: %d segmenti, %d appartamenti in %.1fs", p.nome, piano.n_segmenti(), len(apps), time.time() - t0)
    return {
        "versione": VERSIONE, "pianta": p.nome, "sha256": p.sha256, "titolo": titolo,
        "riquadro": [_r(min_x), _r(min_y), _r(max_x), _r(max_y)],
        "layers": [{"nome": n, "colore": c, "visibile": v, "n_segmenti": len(seg) // 4}
                   for n, c, v, seg in zip(piano.layers, colori, visibili, segmenti)],
        "segmenti": segmenti,
        "appartamenti": appartamenti,
    }


def leggi(p: piante.Pianta) -> dict | None:
    f = _file(p)
    if not f.is_file():
        return None
    try:
        dati = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dati if dati.get("versione") == VERSIONE else None


def stato(p: piante.Pianta) -> tuple[str, dict | str | None]:
    """("pronto", modello) | ("in_preparazione", None) | ("errore", messaggio). Se manca lo prepara."""
    m = leggi(p)
    if m is not None:
        return "pronto", m
    prepara(p)
    if p.sha256 in _errori:
        return "errore", _errori[p.sha256]
    return "in_preparazione", None


def prepara(p: piante.Pianta) -> None:
    """Mette in coda il calcolo (una pianta alla volta, in sottofondo)."""
    global _lavoratore
    if p.formato != "dxf":
        _errori[p.sha256] = "Le piante DWG non si leggono sul server: carica anche il DXF"
        return
    if p.sha256 in _errori:
        return
    with _lock:
        if p.sha256 in _in_corso or _file(p).is_file():
            return
        _in_corso.add(p.sha256)
        _coda.put(p)
        if _lavoratore is None or not _lavoratore.is_alive():
            _lavoratore = threading.Thread(target=_lavora, name="modelli-piante", daemon=True)
            _lavoratore.start()


def prepara_tutte() -> None:
    for p in piante.elenco():
        if p.formato == "dxf" and leggi(p) is None:
            prepara(p)


def _lavora() -> None:
    while True:
        try:
            p = _coda.get(timeout=5)
        except queue.Empty:
            return
        try:
            modello = calcola(p)
            cartella().mkdir(parents=True, exist_ok=True)
            tmp = _file(p).with_suffix(".tmp")
            tmp.write_text(json.dumps(modello, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, _file(p))
        except pm.DxfNonLeggibile as e:
            _errori[p.sha256] = str(e)
        except Exception as e:  # una pianta rovinata non deve fermare le altre
            log.exception("Modello di %s non riuscito", p.nome)
            _errori[p.sha256] = f"Lettura non riuscita: {e}"
        finally:
            with _lock:
                _in_corso.discard(p.sha256)


def prepara_subito(p: piante.Pianta) -> dict:
    """Calcolo sincrono (test e strumenti)."""
    m = calcola(p)
    cartella().mkdir(parents=True, exist_ok=True)
    _file(p).write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return m
