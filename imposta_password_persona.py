"""Imposta la password di una persona che entra dall'app (utente nome.cognome).

Uso:  python imposta_password_persona.py efrem.franceschini         (la chiede, senza mostrarla)
      python imposta_password_persona.py efrem.franceschini 1234    (la passa direttamente)
Le sessioni già aperte di quella persona vengono chiuse (l'app chiede di nuovo il login). Non serve riavviare il server.
"""
import getpass
import sys

from app import db


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else input("Utente (es. efrem.franceschini): ").strip()
    if len(sys.argv) > 2:
        password = sys.argv[2]
    else:
        password = getpass.getpass(f"Nuova password di {username}: ")
        if password != getpass.getpass("Ripetila: "):
            print("Le due password non coincidono: nessuna modifica.")
            return 1
    if not password.strip():
        print("La password non può essere vuota.")
        return 1
    db.inizializza()
    persona = db.imposta_password(username, password)
    if persona is None:
        print(f"Utente «{username}» non trovato. Gli utenti sono nella pagina Cantieri > Persone.")
        return 1
    print(f"Password di «{persona['username']}» ({persona['nome']} {persona['cognome']}) aggiornata.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
