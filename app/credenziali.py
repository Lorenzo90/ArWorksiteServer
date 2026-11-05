"""Password e token di accesso delle persone."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata

ITERAZIONI = 200_000
# Niente caratteri che si confondono (0/O, 1/l/I) perché la password si detta o si copia a mano.
_ALFABETO = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def genera_password(lunghezza: int = 10) -> str:
    while True:
        p = "".join(secrets.choice(_ALFABETO) for _ in range(lunghezza))
        # almeno una minuscola, una maiuscola e una cifra
        if re.search(r"[a-z]", p) and re.search(r"[A-Z]", p) and re.search(r"\d", p):
            return p


def hash_password(password: str) -> str:
    sale = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(sale), ITERAZIONI)
    return f"pbkdf2_sha256${ITERAZIONI}${sale}${h.hex()}"


def verifica_password(password: str, memorizzata: str | None) -> bool:
    if not memorizzata:
        return False
    try:
        algoritmo, iterazioni, sale, atteso = memorizzata.split("$")
        if algoritmo != "pbkdf2_sha256":
            return False
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(sale), int(iterazioni))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(h.hex(), atteso)


def nuovo_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Nel database si salva solo l'impronta del token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def username_base(nome: str, cognome: str) -> str:
    """'Mario', 'De Rossi' -> 'mario.derossi' (senza accenti né spazi)."""
    def pulisci(s: str) -> str:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]", "", s.lower())
    base = ".".join(p for p in (pulisci(nome), pulisci(cognome)) if p)
    return base or "utente"
