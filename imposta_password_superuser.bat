@echo off
REM Cambia la password del superuser delle pagine web.
cd /d "%~dp0"
if not exist .venv (echo Avvia prima avvia_server.bat una volta & pause & exit /b 1)
.venv\Scripts\python imposta_password_superuser.py
pause
