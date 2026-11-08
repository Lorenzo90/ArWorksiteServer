"""Lettura minima di un DXF ASCII: trova le etichette degli appartamenti.

Stessa regola dell'app (shared/FloorPlanAnalyzer.kt, APARTMENT_LABEL): testi come "u.a.03", "App. 3",
"Appartamento 3", "Alloggio 3", "Interno 3" danno l'appartamento numero 3, che l'app chiama "APP 3".
Si leggono solo i testi del disegno (sezione ENTITIES), non quelli dentro le definizioni dei blocchi.
I DWG non si leggono qui (formato binario): per quelli gli appartamenti si aggiungono a mano.
"""
from __future__ import annotations

import re
from pathlib import Path

ETICHETTA = re.compile(
    r"(?iu)^\s*(?:u\.?\s*[ai]\.?|app\.?|appartamento|alloggio|unit[aà](?:\s+abitativa)?|interno|int\.?)"
    r"\s*(?:n\.?|n°|nr\.?)?\s*0*(\d+)\s*$")
# Codici di formattazione MTEXT: con argomento fino a ";" (\fArial|b0; \H2.5x; ...),
# interruttori senza argomento (\L \l \O \o \K \k), a capo / spazi (\P \N \~) e graffe.
_FORMATO_MTEXT = re.compile(r"\\[ACcFfHQTWpS][^;]*;|\\[LlOoKkPN~]|[{}]")
_VIETATI_CARTELLA = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


class DxfNonLeggibile(ValueError):
    pass


def _testo_file(percorso: Path) -> str:
    dati = percorso.read_bytes()
    if dati.startswith(b"AutoCAD Binary DXF"):
        raise DxfNonLeggibile("DXF binario: salvalo come DXF ASCII")
    try:
        return dati.decode("utf-8")
    except UnicodeDecodeError:
        return dati.decode("cp1252", errors="replace")  # DXF fino alla versione 2004


def pulisci_mtext(testo: str) -> str:
    return " ".join(_FORMATO_MTEXT.sub(" ", testo).split())


def testi(percorso: Path) -> list[str]:
    """Testi (TEXT, MTEXT, ATTRIB) della sezione ENTITIES, nell'ordine del file."""
    righe = _testo_file(percorso).splitlines()
    risultato: list[str] = []
    sezione = None
    in_attesa_nome_sezione = False
    tipo = None
    parti: list[str] = []

    def chiudi():
        if tipo in ("TEXT", "MTEXT", "ATTRIB") and parti:
            risultato.append(pulisci_mtext("".join(parti)) if tipo == "MTEXT" else "".join(parti).strip())

    for i in range(0, len(righe) - 1, 2):
        codice, valore = righe[i].strip(), righe[i + 1]
        if codice == "0":
            if sezione == "ENTITIES":
                chiudi()
            tipo, parti = valore.strip(), []
            if tipo == "SECTION":
                in_attesa_nome_sezione = True
            elif tipo == "ENDSEC":
                sezione = None
        elif codice == "2" and in_attesa_nome_sezione:
            sezione, in_attesa_nome_sezione = valore.strip(), False
        elif sezione == "ENTITIES" and codice in ("1", "3"):
            # MTEXT: i pezzi lunghi arrivano con codice 3, l'ultimo con codice 1
            parti.append(valore)
    return risultato


def appartamenti(percorso: Path) -> list[dict]:
    """[{numero, nome: "APP n", etichetta}] ordinati per numero, senza doppioni."""
    visti: dict[int, str] = {}
    for t in testi(percorso):
        m = ETICHETTA.match(t)
        if m:
            visti.setdefault(int(m.group(1)), t.strip())
    return [{"numero": n, "nome": f"APP {n}", "etichetta": visti[n]} for n in sorted(visti)]


def id_app(nome_file: str, nome_appartamento: str) -> str:
    """Id usato dall'app: "dwg:<nome del file senza estensione>/APP n" (WallIds.dwgApartmentId)."""
    def cartella(v: str) -> str:
        v = _VIETATI_CARTELLA.sub("_", v).strip()
        while v.endswith("."):
            v = v[:-1].strip()
        return v if v and v != ".." else "_"
    return "dwg:" + cartella(Path(nome_file).stem) + "/" + cartella(nome_appartamento)
