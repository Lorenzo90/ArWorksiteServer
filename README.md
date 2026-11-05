# ARworksite Server (demo interna)

Servizio REST per condividere i dati di cantiere con l'app ARworksite. Per ora espone **le piante DXF/DWG in sola lettura**.

## Avvio (Windows)
1. Solo la prima volta, come amministratore: `apri_firewall.bat` (apre la porta 8000 sulle reti private).
2. Doppio clic su `avvia_server.bat`: crea `.venv`, installa le dipendenze e avvia su `http://<IP-del-PC>:8000`.
   Lo script stampa gli IPv4 del PC: dal telefono usa quello della stessa rete Wi-Fi.
3. Apri `http://<IP-del-PC>:8000/` per l'elenco delle piante, `/docs` per provare le API.

## Piante
Due modi per aggiungerle:
- **Dalla pagina web** `http://<IP-del-PC>:8000/`: trascina i file DXF/DWG (anche più di uno), scegli o scrivi il cantiere e premi *Carica*. Un file con lo stesso nome viene rifiutato, a meno di spuntare *Sostituisci*.
- **A mano**: copia i file in `data/piante/<cantiere>/`.

Le sottocartelle diventano il campo `cantiere`. Non serve riavviare: l'elenco viene letto a ogni richiesta.

## Accesso alle pagine web (superuser)
Le pagine `/` (Piante), `/cantieri` e `/docs` richiedono il login su `http://<IP-del-PC>:8000/login` con l'utente **superuser**.
- La password iniziale è in `data/credenziali_iniziali.txt` (creata al primo avvio).
- Per cambiarla: doppio clic su `imposta_password_superuser.bat` (chiede la nuova password; le sessioni aperte vengono chiuse, non serve riavviare).
- La sessione dura 12 ore (cookie `HttpOnly`); il pulsante *Esci* in alto la chiude.
- Le API di gestione (persone, cantieri, squadre, caricamento piante) rispondono solo al superuser collegato. L'app usa invece il proprio token: con quello può leggere e scaricare le piante (`GET /api/piante...`) e i propri cantieri, ma non modificare nulla.

## Cantieri, persone e ruoli
I **ruoli** sono i 16 del file `ARworksite/documentazione/Attivita_e_ruoli_appartamento.xlsx` (foglio *Ruoli*), copiati in `app/ruoli.json`: CL, GL, PR, **DL** (Direttore dei lavori), CSE, **CC** (Capocantiere), MU, EL, ID, TT, CG, MS, PO, SE, PI, FO. Se il file Excel cambia, aggiorna `app/ruoli.json` e riavvia: i nomi si aggiornano da soli.

Pagina `http://<IP-del-PC>:8000/cantieri`:
- **Nuovo cantiere**: nome + Direttore dei lavori scelto tra le persone con ruolo DL (proposto *Mario Rossi*). Via API, senza `direttore_lavori_id` il DL è Mario Rossi.
- **Squadra del cantiere**: ogni cantiere ha la sua squadra (DL in evidenza); si aggiungono altre persone dal menu (raggruppato per ruolo) e si tolgono con ×.
- **Persone**: ognuna ha un ruolo dall'elenco, modificabile dal menu nella riga. Eliminare una persona la toglie anche dalle squadre.

Al primo avvio vengono create Mario Rossi (DL), Luca Bianchi (CC), Giulia Verdi (PR), Paolo Neri (EL) e 4 cantieri di esempio con Mario come DL. Un archivio della versione precedente viene aggiornato da solo all'avvio (Mario diventa DL, il vecchio capocantiere entra nella squadra) senza cambiare le password. I dati stanno in `data/arworksite.db` (SQLite).

## Attività
Pagina `http://<IP-del-PC>:8000/attivita` (superuser):
- **Nuova attività**: titolo, descrizione, cantiere facoltativo e persone a cui assegnarla (più di una; *Aggiungi la squadra del cantiere* le seleziona tutte). Si possono allegare **foto** (jpg, png, webp, heic…), **scansioni 3D** `.glb` e **altri file** (pdf, dxf…), massimo `ARWS_MAX_UPLOAD_MB` ciascuno. Programmi e pagine web (.exe, .bat, .html…) non sono accettati.
- **Elenco**: anteprima delle foto e del modello 3D (visualizzatore model-viewer, serve internet sul PC che apre la pagina), aggiunta/eliminazione di allegati, *Modifica* (titolo, descrizione, cantiere, assegnatari), *Elimina*, filtro per persona.
- Gli allegati sono salvati in `data/attivita/<id>/`.

**Dall'app**: la risposta di `POST /api/login` contiene anche `attivita` (quelle assegnate alla persona, più recenti prima). Per aggiornarle: `GET /api/mie-attivita` con `Authorization: Bearer <token>`. Ogni allegato ha `tipo` (`foto`, `scansione3d`, `file`), `sha256` e `url_download`, da scaricare con lo stesso token (ritorna 304 con `If-None-Match` se il file è già aggiornato). Chi non è assegnato all'attività non può scaricarne gli allegati.

## Accesso dall'app (utente e password)
Ogni persona ha un **utente** (`nome.cognome`, es. `mario.rossi`) e una **password** di 10 caratteri generata dal server. Nel database c'è solo l'impronta della password (PBKDF2), mai il testo.

- **Dove trovo le password?** Quelle create in automatico (primo avvio o aggiornamento del server) sono in `data/credenziali_iniziali.txt`: consegnale e poi cancella il file. Per una persona nuova la password compare nella pagina *Cantieri* appena la aggiungi; *Nuova password* ne crea un'altra (la vecchia smette di funzionare e l'app deve rifare il login).
- **Login dall'app**: `POST /api/login` con `{"username": "mario.rossi", "password": "..."}`. Risposta: `token` (valido 30 giorni), `persona` (con il ruolo) e `cantieri` = i cantieri nella cui squadra c'è quella persona, ognuno con la squadra completa.
- **Aggiornare l'elenco** senza rifare il login: `GET /api/miei-cantieri` con l'intestazione `Authorization: Bearer <token>`. `POST /api/logout` chiude la sessione. Con token scaduto o password cambiata la risposta è 401.

## API
| Metodo | Percorso | Descrizione |
|---|---|---|
| GET | `/api/health` | Stato del servizio |
| GET | `/api/piante?cantiere=&formato=` | Elenco piante (`id`, `nome`, `cantiere`, `formato`, `dimensione`, `modificato`, `sha256`, `url_download`) |
| POST | `/api/piante` | Caricamento (multipart): uno o più campi `file`, `cantiere` facoltativo, `sostituisci=true` per sovrascrivere. Errori: 400 formato/nome non valido o file vuoto, 409 file già presente, 413 file troppo grande |
| GET | `/api/piante/{id}` | Dettaglio di una pianta |
| GET | `/api/ruoli` | I 16 ruoli del file Excel |
| GET / POST | `/api/persone` | Elenco / aggiunta persona (`nome`, `cognome`, `ruolo` = sigla) |
| PATCH / DELETE | `/api/persone/{id}` | Cambia ruolo / elimina persona |
| POST | `/api/persone/{id}/password` | Genera una nuova password (restituita una sola volta) |
| POST | `/api/login` | Login dell'app → token + cantieri assegnati alla persona |
| GET | `/api/miei-cantieri` | Cantieri nella cui squadra c'è la persona del token (`Authorization: Bearer`) |
| GET | `/api/mie-attivita` | Attività assegnate alla persona del token |
| GET / POST | `/api/attivita` | Elenco (filtro `persona_id`) / nuova attività (`titolo`, `descrizione`, `cantiere_id`, `persone_ids`) — superuser |
| GET / PATCH / DELETE | `/api/attivita/{id}` | Dettaglio / modifica / eliminazione — superuser |
| POST | `/api/attivita/{id}/allegati` | Caricamento allegati (multipart, campi `file`) — superuser |
| DELETE | `/api/attivita/{id}/allegati/{id_allegato}` | Elimina un allegato — superuser |
| GET | `/api/attivita/{id}/allegati/{id_allegato}/file` | Scarica un allegato (superuser o persona assegnata; `inline=true` per mostrarlo) |
| POST | `/api/logout` | Chiude la sessione del token |
| GET / POST | `/api/cantieri` | Elenco / nuovo cantiere (`nome`, `direttore_lavori_id` facoltativo → Mario Rossi) |
| GET / PATCH / DELETE | `/api/cantieri/{id}` | Dettaglio con squadra / rinomina / eliminazione |
| POST | `/api/cantieri/{id}/squadra` | Aggiunge una persona alla squadra (`persona_id`) |
| DELETE | `/api/cantieri/{id}/squadra/{persona_id}` | Toglie una persona dalla squadra |
| GET | `/api/piante/{id}/file` | Download del file. Con `If-None-Match: "<sha256>"` risponde **304** se il client ha già quella versione |

L'`id` è stabile finché il file non cambia nome/cartella; `sha256` cambia quando cambia il contenuto.

## Configurazione (variabili d'ambiente, facoltative)
- `ARWS_PIANTE_DIR`: cartella delle piante (predefinita `data/piante`).
- `ARWS_DB`: file SQLite di persone e cantieri (predefinito `data/arworksite.db`).
- `ARWS_MAX_UPLOAD_MB`: dimensione massima di un file caricato (predefinita 200).
- `ARWS_API_KEY`: se impostata, le API `/api/*` (tranne health) richiedono l'intestazione `X-Api-Key`; la pagina web chiede la chiave e la ricorda nel browser.

## Test
```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```
