"""Cosa scarica l'app di una persona: i cantieri e gli appartamenti che le sono assegnati.

Due modi di essere assegnati:
- **squadra** del cantiere → tutto il cantiere: tutti gli appartamenti e tutte le piante;
- **committente** di un appartamento → solo quell'appartamento (e la pianta che lo contiene),
  dentro un cantiere di cui la persona non fa parte della squadra.
"""
from __future__ import annotations

import hashlib
import json

from . import appartamenti, db, piante


def _appartamenti_da_committente(persona_id: int) -> dict[int, set[int]]:
    """{cantiere_id: {appartamento_id, ...}} dove la persona è committente."""
    with db.connessione() as con:
        righe = con.execute("""SELECT a.cantiere_id, a.id FROM appartamento_committenti c
                               JOIN appartamenti a ON a.id = c.appartamento_id
                               WHERE c.persona_id = ?""", (persona_id,)).fetchall()
    risultato: dict[int, set[int]] = {}
    for cid, aid in righe:
        risultato.setdefault(cid, set()).add(aid)
    return risultato


def cantieri(persona_id: int) -> list[dict]:
    """Cantieri visibili alla persona, ognuno con `accesso`, `appartamenti` e `piante` (oggetti Pianta)."""
    da_committente = _appartamenti_da_committente(persona_id)
    risultato = []
    for c in db.elenco_cantieri(persona_id=persona_id):
        risultato.append(_componi(c, "squadra", appartamenti.elenco(c["id"]),
                                  appartamenti.piante_del_cantiere(c["id"]), da_committente.get(c["id"], set())))
    gia = {c["id"] for c in risultato}
    for cid, miei in da_committente.items():
        if cid in gia:
            continue
        c = db.leggi_cantiere(cid)
        if not c:
            continue
        aps = [a for a in appartamenti.elenco(cid) if a["id"] in miei]
        nomi_piante = {a["pianta"].lower() for a in aps if a["pianta"]}
        pi = [p for p in appartamenti.piante_del_cantiere(cid) if p.nome.lower() in nomi_piante]
        # al committente non si mostra la squadra, solo il Direttore dei lavori come riferimento
        c = {**c, "squadra": [c["direttore_lavori"]] if c["direttore_lavori"] else []}
        risultato.append(_componi(c, "committente", aps, pi, miei))
    return sorted(risultato, key=lambda c: c["nome"].lower())


def _componi(c: dict, accesso: str, aps: list[dict], pi: list[piante.Pianta], miei: set[int]) -> dict:
    per_nome = {p.nome.lower(): p for p in pi}
    for a in aps:
        p = per_nome.get((a["pianta"] or "").lower())
        a["pianta_id"] = p.id if p else None
        a["committente"] = a["id"] in miei  # la persona è committente di questo appartamento
    return {**c, "accesso": accesso, "appartamenti": aps, "piante": pi}


def piante_consentite(persona_id: int) -> set[str]:
    return {p.id for c in cantieri(persona_id) for p in c["piante"]}


def versione(dati: dict) -> str:
    """Impronta del contenuto: cambia se cambia qualcosa che l'app deve riscaricare."""
    testo = json.dumps(dati, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()[:32]
