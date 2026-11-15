"""Configurazione da variabili d'ambiente (tutte facoltative)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Cartella delle piante: ogni sottocartella è un cantiere (es. data/piante/Lungo Avisio/*.dxf).
PIANTE_DIR = Path(os.environ.get("ARWS_PIANTE_DIR", BASE_DIR / "data" / "piante")).resolve()

# Se impostata, le API /api/* richiedono l'intestazione "X-Api-Key: <valore>".
API_KEY = os.environ.get("ARWS_API_KEY") or None

ESTENSIONI_PIANTE = {".dxf", ".dwg"}

# Dimensione massima di un file caricato (MB).
MAX_UPLOAD_MB = int(os.environ.get("ARWS_MAX_UPLOAD_MB", "200"))

# Pianta caricata dall'app (iPad, POST /api/cantieri/{id}/piante con il token): limite più basso (MB).
MAX_UPLOAD_APP_MB = int(os.environ.get("ARWS_MAX_UPLOAD_APP_MB", "50"))

# Archivio SQLite di persone e cantieri.
DB_PATH = Path(os.environ.get("ARWS_DB", BASE_DIR / "data" / "arworksite.db")).resolve()
