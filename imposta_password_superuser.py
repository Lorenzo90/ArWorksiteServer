"""Imposta la password del superuser che entra nelle pagine web.

Uso:  python imposta_password_superuser.py           (la chiede, senza mostrarla)
      python imposta_password_superuser.py 1234      (la passa direttamente)
Le sessioni già aperte del superuser vengono chiuse. Non serve riavviare il server.
"""
import getpass
import sys

from app import db


def main() -> int:
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Nuova password del superuser: ")
        if password != getpass.getpass("Ripetila: "):
            print("Le due password non coincidono: nessuna modifica.")
            return 1
    if not password.strip():
        print("La password non può essere vuota.")
        return 1
    db.inizializza()
    db.imposta_password_admin(db.SUPERUSER, password)
    print(f"Password di «{db.SUPERUSER}» aggiornata.")
    if len(password) < 8:
        print("Attenzione: è corta. Va bene per una demo interna, non per un server raggiungibile da fuori.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
