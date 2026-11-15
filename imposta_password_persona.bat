@echo off
REM Cambia la password di una persona che entra dall'app.
REM Esempio: imposta_password_persona.bat efrem.franceschini 1234
cd /d "%~dp0"
if not exist .venv (echo Avvia prima avvia_server.bat una volta & pause & exit /b 1)
.venv\Scripts\python imposta_password_persona.py %*
pause
