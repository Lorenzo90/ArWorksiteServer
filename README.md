# ARworksite Server

Server REST (FastAPI + SQLite) dell'app **CasaXRays / ARworksite**: tiene cantieri, persone, squadre, piante DXF/DWG, attività e i dati raccolti dai telefoni (foto, scansioni delle pareti, file, note), e offre le pagine web di gestione per il superuser.

Riferimento completo di pagine, regole e API: [`RIFERIMENTO.md`](RIFERIMENTO.md). Flusso tra app e server: [`FLusso_v1_1.md`](FLusso_v1_1.md).

---

## Cosa fa il server

### 1. Accesso
- **App**: `POST /api/login` con utente (`nome.cognome`) e password → token valido 30 giorni (`Authorization: Bearer <token>`). Password salvate solo come impronta PBKDF2. `POST /api/logout` chiude la sessione.
- **Pagine web**: solo l'utente **superuser** (`/login`, cookie `HttpOnly` di 12 ore). La password iniziale è in `data/credenziali_iniziali.txt`, creata al primo avvio; si cambia con `imposta_password_superuser.bat` / `.py`.
- Facoltativa: `ARWS_API_KEY` per l'accesso tecnico alle API con l'intestazione `X-Api-Key`.

### 2. Cantieri, persone, squadre
- 16 ruoli (`app/ruoli.json`: DL, CC, CL committente, …). Ogni cantiere ha una **squadra** e un Direttore dei lavori.
- Ogni persona vede solo ciò che le spetta: i cantieri della sua squadra (tutti gli appartamenti) o, se committente, solo i suoi appartamenti.
- `GET /api/miei-dati` restituisce all'app tutto quello che le è assegnato (cantieri, appartamenti, piante, attività), con `versione` e risposta **304** se non è cambiato niente.
- L'app può creare un cantiere (`POST /api/cantieri`) e caricarne la pianta (`POST /api/cantieri/{id}/piante`): chi lo crea entra nella squadra.

### 3. Piante e appartamenti
- Piante DXF/DWG in `data/piante/<cantiere>/`, caricate dalla scheda del cantiere (trascinando i file) o copiate a mano. Ognuna ha `sha256`: l'app riscarica solo quelle cambiate (`If-None-Match` → 304).
- Dai **DXF** il server ricava gli appartamenti (`App. 3` → **APP 3**, `id_app = dwg:<file>/APP 3`) e, con lo stesso calcolo dell'app (`app/pianta_modello.py`), locali e pareti con gli stessi nomi delle cartelle del telefono. I DWG non si leggono sul server: per quelli gli appartamenti si aggiungono a mano.

### 4. Attività
- Create dalla scheda del cantiere, sempre su un appartamento o sullo spazio comune, assegnate a persone della squadra, con allegati (foto, GLB, PDF…).
- Quattro stati: **Aperta → Presa in carico → Chiusa**, più **Da verificare** (messo dal DL o dalla pagina web). Dall'app: `POST /api/mie-attivita/{id}/stato` e `POST /api/mie-attivita/{id}/aggiornamento` (stato, nota e file in una chiamata).

### 5. Dati raccolti dai telefoni
- Sincronizzazione file per file della cartella dell'appartamento (`pareti/<DWG>/<APP n>/` sul telefono → `data/raccolta/<id appartamento>/` sul server): `GET /api/appartamenti/{id}/raccolta` per l'elenco con `sha256`, `PUT`/`GET`/`DELETE …/raccolta/<percorso>` per i singoli file (`X-Sha256`).
- Scrive chi è in squadra, i committenti leggono soltanto. Un file di un'altra persona non si sovrascrive (409) senza `sostituisci=true`.
- **Condivisione per contenuto**: un file `<contenuto>.condivisione.json` dice chi può vederlo; agli altri il contenuto non viene elencato né dato (404). Nessun file = visibile a tutti.

### 6. Pagine web (superuser)
- `/cantieri`: cantieri, persone, squadre.
- `/cantieri/<id>`: scheda del cantiere (squadra, piante, appartamenti, committenti, attività).
- `/cantieri/<id>/vista`: la pianta come nell'app, con foto, scansioni, file e attività per locale/parete.
- `/docs`: API interattive.

---

## Installazione e avvio

Serve **Python 3.11 o più recente** (provato con 3.12). Le dipendenze sono in `requirements.txt` (FastAPI, Uvicorn, python-multipart, numpy).

### A. PC Windows sulla rete locale (demo)
1. Una sola volta, **come amministratore**: `apri_firewall.bat` (apre la porta 8000 in ingresso sulle reti private).
2. Doppio clic su `avvia_server.bat`: crea `.venv`, installa le dipendenze, stampa gli IPv4 del PC e avvia il server su `http://<IP-del-PC>:8000`. Tenere aperta la finestra: chiudendola il server si ferma.
3. Aprire `http://localhost:8000/login` ed entrare come `superuser` con la password di `data/credenziali_iniziali.txt`.
4. Nell'app inserire `http://<IP-del-PC>:8000` come indirizzo del server (telefono sulla stessa Wi-Fi).

> L'app permette HTTP in chiaro **solo nelle build di debug**. Con una build di release serve HTTPS (vedi B).

### B. Server Linux esposto in rete (HTTPS)
```bash
sudo useradd --system --create-home --home-dir /opt/arworksite arworksite
sudo -u arworksite git clone https://github.com/Lorenzo90/ArWorksiteServer.git /opt/arworksite/server
cd /opt/arworksite/server
sudo -u arworksite python3 -m venv .venv
sudo -u arworksite .venv/bin/pip install -r requirements.txt
```

Servizio systemd `/etc/systemd/system/arworksite.service`:
```ini
[Unit]
Description=ARworksite Server
After=network.target

[Service]
User=arworksite
WorkingDirectory=/opt/arworksite/server
Environment=ARWS_PORTA=8000
ExecStart=/opt/arworksite/server/.venv/bin/python avvia.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now arworksite
sudo cat /opt/arworksite/server/data/credenziali_iniziali.txt   # password iniziale del superuser
```

Davanti al server mettere un reverse proxy con HTTPS, sulla stessa macchina. Esempio con **Caddy** (certificato automatico):
```
cantiere.example.com {
    request_body {
        max_size 250MB
    }
    reverse_proxy 127.0.0.1:8000
}
```
Con nginx: `proxy_pass http://127.0.0.1:8000;`, `client_max_body_size 250m;`, `proxy_set_header Host $host;` e `proxy_set_header X-Forwarded-Proto $scheme;` (le `url_download` restituite all'app vengono costruite dall'indirizzo della richiesta, quindi il proxy deve passare host e schema; Uvicorn accetta queste intestazioni da `127.0.0.1`).

Note:
- `avvia.py` ascolta su `0.0.0.0`: con il proxy chiudere la porta 8000 dall'esterno nel firewall (lasciare aperte solo 80/443).
- Usare **un solo processo** (niente `--workers`): l'archivio è SQLite e la preparazione delle piante gira in sottofondo nel processo.
- Il primo calcolo di locali e pareti di una pianta richiede qualche secondo per appartamento; poi resta in `data/modelli/`.

### Configurazione (variabili d'ambiente, tutte facoltative)
| Variabile | Predefinito | Uso |
|---|---|---|
| `ARWS_PORTA` | `8000` | Porta di `avvia.py` |
| `ARWS_PIANTE_DIR` | `data/piante` | Cartella delle piante |
| `ARWS_DB` | `data/arworksite.db` | Archivio SQLite |
| `ARWS_MAX_UPLOAD_MB` | `200` | File massimo caricato |
| `ARWS_MAX_UPLOAD_APP_MB` | `50` | Pianta massima caricata dall'app |
| `ARWS_API_KEY` | — | Se impostata, le API chiedono `X-Api-Key` |

### Dati, backup e aggiornamento
Tutto lo stato è nella cartella **`data/`** (esclusa da git):
- `arworksite.db` persone, cantieri, squadre, attività, sessioni;
- `credenziali_iniziali.txt` password generate (da proteggere o cancellare dopo averle annotate);
- `piante/`, `attivita/`, `raccolta/`, `modelli/` (quest'ultima si può rigenerare);
- `server.log` log di avvio e richieste.

Backup: copiare `data/` a server fermo (oppure `sqlite3 data/arworksite.db ".backup backup.db"` più una copia delle cartelle).

Aggiornamento: fermare il server, `git pull`, `pip install -r requirements.txt`, riavviare. Le modifiche all'archivio vengono applicate da sole all'avvio.

### Test
```
python -m pip install -r requirements-dev.txt
python -m pytest
```
