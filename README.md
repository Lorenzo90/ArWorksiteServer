# ARworksite Server (demo interna)

Servizio REST per condividere i dati di cantiere con l'app ARworksite. Per ora espone **le piante DXF/DWG in sola lettura**.

## Avvio (Windows)
1. Solo la prima volta, come amministratore: `apri_firewall.bat` (apre la porta 8000 sulle reti private).
2. Doppio clic su `avvia_server.bat`: crea `.venv`, installa le dipendenze e avvia su `http://<IP-del-PC>:8000`.
   Lo script stampa gli IPv4 del PC: dal telefono usa quello della stessa rete Wi-Fi.
3. Apri `http://<IP-del-PC>:8000/`: la prima pagina è l'elenco dei cantieri. `/docs` per provare le API.

## Piante
Due modi per aggiungerle:
- **Dalla scheda del cantiere**: trascina i file DXF/DWG nel riquadro *Appartamenti*. Se esiste già un file con lo stesso nome la pagina chiede se sostituirlo.
- **A mano**: copia i file in `data/piante/<cantiere>/`.

Le sottocartelle diventano il campo `cantiere`. Non serve riavviare: l'elenco viene letto a ogni richiesta.

## Accesso alle pagine web (superuser)
Le pagine `/` (= `/cantieri`), le schede dei cantieri e `/docs` richiedono il login su `http://<IP-del-PC>:8000/login` con l'utente **superuser**.
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
Le attività si creano dalla **scheda del cantiere** e sono sempre di **un appartamento o di uno spazio comune** (per un'attività di tutta la planimetria si sceglie lo spazio comune). Le attività create prima di questa regola sono andate sull'APP 3 del loro cantiere, o sullo spazio comune se il cantiere non ha un APP 3 e si assegnano **solo alle persone della squadra** di quel cantiere: il server rifiuta (400) chi non ne fa parte. Chi viene tolto dalla squadra perde le attività di quel cantiere. Allegati: **foto**, scansioni 3D `.glb` e altri file (pdf, dxf…), salvati in `data/attivita/<id>/`.

**Stato** (come nell'app): **Aperta** → **Presa in carico** (si vede *In carico a …*) → **Chiusa** (lavoro fatto, con foto e file). **Da verificare** lo mette il Direttore dei lavori o la pagina web per far ricontrollare: chi l'aveva aggiunge i file e la richiude. Nella scheda del cantiere si cambia dal menu colorato su ogni riga (riaprire chiede il motivo) e si filtra per stato. Dall'app chi è assegnato prende in carico, rilascia e chiude, ma non un'attività in carico a un altro; il **Direttore dei lavori** della squadra riceve tutte le attività del cantiere e può mettere qualsiasi stato.

**Dall'app**: la risposta di `POST /api/login` contiene anche `attivita` (quelle assegnate alla persona, più recenti prima). Per aggiornarle: `GET /api/mie-attivita` con `Authorization: Bearer <token>`. Ogni allegato ha `tipo` (`foto`, `scansione3d`, `file`), `sha256` e `url_download`, da scaricare con lo stesso token (ritorna 304 con `If-None-Match` se il file è già aggiornato). Chi non è assegnato all'attività non può scaricarne gli allegati.

## Scheda del cantiere
Dalla pagina *Cantieri* clic sul nome (o *Apri scheda →*): `http://<IP-del-PC>:8000/cantieri/<id>`. In una sola schermata:
- **Squadra**: aggiungi una persona già presente o crea una **Nuova persona** (entra subito nella squadra; la password compare una sola volta).
- **Appartamenti**: si popolano da soli quando carichi un **DXF** nel cantiere (trascinalo nel riquadro). Il server legge le etichette del disegno come fa l'app (`u.a.03`, `App. 3`, `Appartamento 3`, `Interno 3` → **APP 3**) e salva anche l'id usato dall'app (`dwg:<nome file>/APP 3`). Gli appartamenti non vengono mai cancellati quando ricarichi la pianta. I **DWG** non si possono leggere sul server: per quelli aggiungi gli appartamenti *a mano*.
- **Spazio comune**: ogni planimetria ne ha uno, creato in automatico, che vale per **tutta la planimetria** (vano scala, facciata…). Un cantiere senza planimetrie ne ha uno generico, che sparisce all'arrivo della prima planimetria se non è usato. Non si elimina.
- **Committenti** per appartamento (ruolo CL): scegli una persona o *Nuovo committente…*. Esempio già creato: **Lorenzo Orlandi** su APP 3 di Lungo Avisio (password in `data/credenziali_iniziali.txt`).
- **Attività** del cantiere, con *+ Nuova attività* (titolo, appartamento facoltativo, descrizione, persone, allegati).

Le piante del cantiere stanno in `data/piante/<nome cantiere>/`.

## Vista del cantiere (come nell'app)
Dalla scheda del cantiere *Vista del cantiere →* (o dall'elenco dei cantieri *Vista →*): `http://<IP-del-PC>:8000/cantieri/<id>/vista`.
- **A sinistra** gli appartamenti per planimetria (con lo *Spazio comune*), **al centro** la planimetria con i layer dell'app (rotella = zoom, trascina = sposta, doppio clic = adatta, ≡ = layer), **a destra** il pannello media.
- Clic su un **locale** → le sue pareti numerate come nel telefono (*Parete 1* in alto a sinistra, in senso orario); clic su una **parete** → i suoi dati; clic di nuovo sul locale → **Soffitto** o **Pavimento**.
- Il pannello mostra **Foto / Scansioni / File / Attività** del punto scelto, con i conteggi di tutto quello che sta sotto e le cartelle (locali, pareti, cartelle dei file) come nel pannello media dell'app; foto e scansioni si aprono a schermo intero (← → per scorrere), i GLB nel visualizzatore 3D.
- I dati sono quelli caricati dai telefoni (*Dati raccolti*) e gli allegati delle attività con il riferimento alla parete.
- Locali e pareti sono ricostruiti dal DXF con lo **stesso calcolo dell'app** (`app/pianta_modello.py`, traduzione di FloorPlanAnalyzer e RoomWallExtractor), quindi i nomi coincidono con le cartelle del telefono. La prima volta il calcolo richiede qualche secondo per appartamento e viene salvato in `data/modelli/`. Serve `numpy` (installato da `avvia_server.bat`); senza funziona lo stesso, più lentamente. I DWG non si leggono sul server: per la vista serve il DXF.
- In APP 3 di Lungo Avisio ci sono come esempio le foto e le due scansioni di esempio dell'app (autore «Esempio dell'app»).

## Dal telefono al server
- **Aggiornamento di un'attività**: `POST /api/mie-attivita/{id}/aggiornamento` (multipart) con foto/file/GLB, `stato`, `nota` e `riferimento` (id app della parete o stanza) in una sola chiamata. Gli allegati portano il nome di chi li ha caricati; nella scheda del cantiere si vedono sotto l'attività.
- **Dati raccolti** (cartella dell'app `pareti/<DWG>/<APP n>/`: scansioni, foto, file, report): il telefono confronta i file con `GET /api/appartamenti/{id}/raccolta` e carica solo quelli nuovi o cambiati con `PUT /api/appartamenti/{id}/raccolta/<percorso>` (`X-Sha256`). Carica chi è nella squadra; i committenti leggono. Un file caricato da un'altra persona non si sovrascrive (409) senza `sostituisci=true`. Sul server: `data/raccolta/<id appartamento>/`. Nella scheda del cantiere ogni appartamento mostra *Dati raccolti: n file* e *Vedi* per l'elenco.

## Accesso dall'app (utente e password)
Ogni persona ha un **utente** (`nome.cognome`, es. `mario.rossi`) e una **password** (per la demo `1234` per tutti; le persone nuove ne ricevono una casuale). Nel database c'è solo l'impronta della password (PBKDF2), mai il testo.

**Cosa riceve l'app al login** (`POST /api/login`, poi `GET /api/miei-dati` per aggiornare):
- i cantieri nella cui **squadra** c'è la persona → `accesso: "squadra"`, con **tutti** gli appartamenti e **tutte** le piante;
- gli appartamenti di cui la persona è **committente** → il loro cantiere con `accesso: "committente"`, **solo** quegli appartamenti e la pianta che li contiene (della squadra vede solo il DL);
- le attività assegnate, con gli allegati.

Ogni pianta ha `sha256` e `url_download`; ogni appartamento ha `id_app` (= id dell'appartamento nell'app) e `pianta_id`. `versione` cambia solo quando cambia qualcosa: con `If-None-Match: "<versione>"` `/api/miei-dati` risponde **304**. Con il token dell'app `/api/piante` e i download mostrano **solo le piante assegnate** (le altre: 404). Dettagli per lo sviluppo dell'app: `C:\c\ARworksite\ARworksite\documentazione\Server_login_e_dati_app.md`.

- **Login dall'app**: `POST /api/login` con `{"username": "mario.rossi", "password": "1234"}` → `token` (30 giorni) + i dati qui sopra. `POST /api/logout` chiude la sessione. Con token scaduto o password cambiata la risposta è 401.

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
| GET | `/api/miei-dati` | Tutto quello che è assegnato alla persona del token (come il login); 304 con `If-None-Match` |
| GET | `/api/miei-cantieri` | Solo i cantieri (stesso formato di `/api/miei-dati`) |
| GET | `/api/mie-attivita` | Attività assegnate alla persona del token |
| GET / POST | `/api/attivita` | Elenco (filtro `persona_id`) / nuova attività (persone solo della squadra del cantiere) (`titolo`, `descrizione`, `cantiere_id`, `appartamento_id`, `persone_ids`) — superuser |
| GET / PATCH / DELETE | `/api/attivita/{id}` | Dettaglio / modifica / eliminazione — superuser |
| POST | `/api/attivita/{id}/allegati` | Caricamento allegati (multipart, campi `file`) — superuser |
| POST | `/api/attivita/{id}/stato` | Cambia lo stato (`stato` = aperta / presa_in_carico / da_verificare / chiusa, `nota`) — superuser |
| POST | `/api/attivita/{id}/note` · `/api/mie-attivita/{id}/note` | Nota libera (`testo`) dal superuser / dall'app (assegnato o DL) |
| POST | `/api/mie-attivita/{id}/stato` | Cambia lo stato dall'app (token): assegnato: prende in carico, rilascia, chiude; DL qualsiasi |
| DELETE | `/api/attivita/{id}/allegati/{id_allegato}` | Elimina un allegato — superuser |
| GET | `/api/attivita/{id}/allegati/{id_allegato}/file` | Scarica un allegato (superuser o persona assegnata; `inline=true` per mostrarlo) |
| POST | `/api/logout` | Chiude la sessione del token |
| GET / POST | `/api/cantieri` | Elenco / nuovo cantiere (`nome`, `direttore_lavori_id` facoltativo → Mario Rossi) |
| GET / PATCH / DELETE | `/api/cantieri/{id}` | Dettaglio con squadra / rinomina / eliminazione |
| POST | `/api/cantieri/{id}/squadra` | Aggiunge una persona alla squadra (`persona_id`) |
| DELETE | `/api/cantieri/{id}/squadra/{persona_id}` | Toglie una persona dalla squadra |
| GET | `/api/cantieri/{id}/scheda` | Scheda: cantiere, piante, appartamenti con committenti, attività — superuser |
| POST | `/api/cantieri/{id}/piante` | Carica DXF/DWG nel cantiere e aggiorna gli appartamenti (`sostituisci=true` per sovrascrivere) |
| POST | `/api/cantieri/{id}/appartamenti` | Appartamento a mano (`nome`) |
| DELETE | `/api/appartamenti/{id}` | Elimina un appartamento aggiunto a mano |
| POST / DELETE | `/api/appartamenti/{id}/committenti[/{persona_id}]` | Aggiunge (`persona_id`) / toglie un committente |
| POST | `/api/mie-attivita/{id}/aggiornamento` | Dal telefono: allegati + stato in una chiamata (multipart) |
| GET | `/api/appartamenti/{id}/raccolta` | Elenco dei dati raccolti (304 con `If-None-Match`) |
| PUT / GET / DELETE | `/api/appartamenti/{id}/raccolta/<percorso>` | Carica (`X-Sha256`) / scarica / elimina un file raccolto |
| GET | `/api/piante/{id}/modello` | Planimetria letta come nell'app: layer, segmenti, appartamenti, locali e pareti (202 mentre si prepara) |
| GET | `/api/appartamenti/{id}/esplora` | Contenuto di un appartamento per locale/parete/soffitto/pavimento, con conteggi e attività |
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
