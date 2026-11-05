"""Avvia il server e scrive tutto (anche gli errori di avvio) anche in data/server.log."""
import copy
import os
import socket
import sys
import traceback
from datetime import datetime
from pathlib import Path

CARTELLA = Path(__file__).resolve().parent
LOG = CARTELLA / "data" / "server.log"
PORTA = int(os.environ.get("ARWS_PORTA", "8000"))


def scrivi(testo: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(testo + "\n")


def porta_libera(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def main() -> int:
    os.chdir(CARTELLA)
    sys.path.insert(0, str(CARTELLA))
    scrivi(f"\n=== Avvio {datetime.now():%Y-%m-%d %H:%M:%S} · Python {sys.version.split()[0]} · porta {PORTA} ===")
    if not porta_libera(PORTA):
        msg = (f"La porta {PORTA} è già occupata: probabilmente il server è già aperto in un'altra finestra "
               f"(oppure un altro programma usa la porta {PORTA}). Chiudi l'altra finestra e riprova.")
        print(msg)
        scrivi("ERRORE: " + msg)
        return 1
    try:
        import uvicorn
        from uvicorn.config import LOGGING_CONFIG
        import app.main  # noqa: F401  (errori nel codice emergono qui, con il dettaglio nel log)
    except Exception:
        dettaglio = traceback.format_exc()
        print(dettaglio)
        scrivi("ERRORE all'avvio:\n" + dettaglio)
        return 1

    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["formatters"]["file"] = {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}
    cfg["handlers"]["file"] = {"class": "logging.FileHandler", "filename": str(LOG),
                               "encoding": "utf-8", "formatter": "file"}
    cfg["loggers"]["uvicorn"]["handlers"].append("file")
    cfg["loggers"]["uvicorn.access"]["handlers"].append("file")
    print(f"\nApri nel browser:  http://localhost:{PORTA}   (da altri dispositivi: http://<IPv4 qui sopra>:{PORTA})\n")
    try:
        uvicorn.run("app.main:app", host="0.0.0.0", port=PORTA, log_config=cfg)
    except Exception:
        scrivi("ERRORE durante l'esecuzione:\n" + traceback.format_exc())
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
