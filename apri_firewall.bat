@echo off
REM Da eseguire UNA volta come amministratore: apre la porta 8000 in ingresso sulle reti private.
netsh advfirewall firewall add rule name="ARworksite Server 8000" dir=in action=allow protocol=TCP localport=8000 profile=private
pause
