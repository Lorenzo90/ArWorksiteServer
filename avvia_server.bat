@echo off
REM Avvia il server ARworksite sulla rete locale (porta 8000).
REM Lascia aperta questa finestra: chiudendola il server si ferma.
cd /d "%~dp0"
if not exist .venv (
  echo Creo l'ambiente Python .venv ...
  python -m venv .venv || (echo Python non trovato nel PATH & pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip
)
REM Installa/aggiorna le dipendenze (veloce se sono gia' presenti).
.venv\Scripts\python -m pip install -q -r requirements.txt
echo.
echo Indirizzi di questo PC (usa l'IPv4 della rete Wi-Fi/LAN dal telefono):
ipconfig | findstr /R /C:"IPv4"
.venv\Scripts\python avvia.py
echo.
echo Il server si e' fermato. Il dettaglio e' in data\server.log
pause
