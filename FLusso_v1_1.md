# Flusso ARworksite – server e app (v1.1)

**v1.1 – 8/11/2026 sera.** Sostituisce `FLusso_v1_0.md` (8/11/2026 pomeriggio). Novità rispetto alla v1.0:

- **Stato "Presa in carico"** tra Aperta e Da verificare (4 stati), con `in_carico` = chi l'ha presa; chi è assegnato prende in carico, rilascia, segna come fatta e annulla; chiudere e riaprire resta al DL (3.6).
- **Note e storico**: note libere sulle attività (`note[]`, `POST /api/mie-attivita/{id}/note`) oltre alle note dei cambi di stato; nell'app e nella pagina web "Note e storico (n)" con la cronologia completa (3.6).
- **Ogni attività è di un appartamento o di uno "Spazio comune"**: ogni planimetria ha il suo spazio comune (tutta la planimetria), un cantiere senza planimetrie ne ha uno generico; niente più attività "di tutto il cantiere" (§1, 3.5, §4 punto 6b).
- **Aggiornamento dal telefono in una chiamata**: foto/file/GLB + stato + nota + parete di riferimento (`POST /api/mie-attivita/{id}/aggiornamento`, 3.7).
- **Dati raccolti**: sincronizzazione della cartella `pareti/<DWG>/<APP n>/` con il server, in su e in giù, con conflitti ed eliminazioni (3.8).
- Allegati con autore e riferimento (`chi`, `riferimento`); dati di prova aggiornati (§6).

La stessa specifica, sempre aggiornata, è in `C:\c\ARworksite\ARworksite\documentazione\Server_login_e_dati_app.md`.

---

Questo documento descrive cosa deve fare l'app (modulo `shared`, Android e iOS) quando l'operatore fa il login: quali dati riceve dal server, cosa scaricare sul telefono e come mostrarlo; poi cosa **manda** al server: cambi di stato, note e allegati delle attività (3.6, 3.7) e i dati raccolti negli appartamenti (3.8). Server: `C:\c\ARworksiteServer` (FastAPI), versione dell'8/11/2026 sera (4 stati con presa in carico, note e storico, spazio comune, aggiornamenti dal telefono, dati raccolti).

## 1. Chi riceve cosa

Sul server una persona è assegnata in due modi:

| Assegnazione (pagina web) | Nel JSON | Cosa riceve l'app |
|---|---|---|
| È nella **squadra** del cantiere (scheda del cantiere → Squadra) | `accesso: "squadra"` | **Tutto il cantiere**: tutti gli appartamenti, tutte le piante DXF/DWG, la squadra completa |
| È **committente** di un appartamento (scheda → Appartamenti → Committente) | `accesso: "committente"` | **Solo quegli appartamenti** e la pianta che li contiene; della squadra solo il Direttore dei lavori |

Ogni planimetria ha anche uno **"Spazio comune"** (`origine: "comune"`), che sta nella lista degli appartamenti e rappresenta **tutta la planimetria** (vano scala, facciata, attività che valgono per tutti gli appartamenti). Un cantiere senza planimetrie ha uno spazio comune generico (`pianta` e `id_app` a `null`), che sparisce all'arrivo della prima planimetria se non è usato. Ogni attività è sempre di **un appartamento o di uno spazio comune**: non ci sono più attività "di tutto il cantiere".

Inoltre riceve le **attività assegnate** a lei (solo chi è nella squadra può riceverne), con gli allegati. Il **Direttore dei lavori** (ruolo `DL`) riceve **tutte** le attività dei cantieri della sua squadra, anche quelle non assegnate a lui, perché deve verificarle e chiuderle.

Il server applica le stesse regole anche ai download: con il token dell'app `/api/piante` elenca solo le piante assegnate e `/api/piante/{id}/file` risponde **404** per le altre. Se una persona viene tolta da una squadra, quel cantiere sparisce dalla risposta successiva e le sue attività di quel cantiere vengono tolte.

## 2. Flusso nell'app

```
Login (utente, password, ruolo, server)
  └─ POST /api/login ──► token + dati (stesso formato di /api/miei-dati)
       ├─ salva la sessione (sessione.json, già esistente)
       ├─ salva i dati così come arrivano (dati.json) + versione
       ├─ scarica/aggiorna le piante elencate in cantieri[].piante (sha256)
       ├─ scarica gli allegati delle attività (già esistente)
       ├─ invia la coda delle cose fatte offline (stati, allegati)        → 3.7
       ├─ allinea i dati raccolti di ogni appartamento (in su e in giù) → 3.8
       └─ Home: elenco dei cantieri → piante → appartamenti
Riapertura / "Aggiorna"
  └─ GET /api/miei-dati con If-None-Match: "<versione>"
       ├─ 304 → niente di nuovo, si usa dati.json
       ├─ 200 → si sostituisce dati.json e si riallineano piante e allegati
       ├─ 401 → sessione scaduta: si torna al login (dati locali restano)
       └─ rete assente → si lavora con l'ultima copia
```

Un solo giro di rete per i metadati (`/api/miei-dati`); poi solo i file che servono.

## 3. API

Indirizzo base: quello scritto nel login (es. `http://192.168.1.50:8000`). Tutte le richieste dopo il login hanno l'intestazione `Authorization: Bearer <token>`.

### 3.1 `POST /api/login`

Richiesta:
```json
{ "username": "lorenzo.orlandi", "password": "1234" }
```

Risposte: **200** con i dati qui sotto, **401** `{"detail": "Utente o password errati"}`.

Il server non riceve il ruolo scelto nella schermata di login: l'app continua a confrontarlo con `persona.ruolo` / `persona.ruolo_nome` (`Role.matches`). Lorenzo Orlandi deve scegliere **Cliente / committente** (sigla `CL`).

### 3.2 `GET /api/miei-dati`

Stessi dati del login senza `token` e `scadenza`. Risponde con l'intestazione `ETag: "<versione>"`; con `If-None-Match: "<versione>"` risponde **304** senza corpo se non è cambiato niente (cantieri, squadre, appartamenti, committenti, piante e loro sha256, attività, allegati).

### 3.3 Esempio: committente (Lorenzo Orlandi)

```json
{
  "token": "lHwfLIcjiG36iqFcMxDM3_lQNGLpf2dwphuvUGpVgBM",
  "scadenza": "2026-12-08T14:36:34+00:00",
  "persona": { "id": 5, "nome": "Lorenzo", "cognome": "Orlandi",
               "ruolo": "CL", "ruolo_nome": "Cliente / committente", "username": "lorenzo.orlandi" },
  "versione": "7b98facfef66d45e8391128afb295e51",
  "generato": "2026-11-08T14:36:34+00:00",
  "cantieri": [
    {
      "id": 1,
      "nome": "Lungo Avisio",
      "accesso": "committente",
      "direttore_lavori": { "id": 1, "nome": "Mario", "cognome": "Rossi", "ruolo": "DL", "ruolo_nome": "Direttore dei lavori" },
      "squadra": [ { "id": 1, "nome": "Mario", "cognome": "Rossi", "ruolo": "DL", "ruolo_nome": "Direttore dei lavori" } ],
      "appartamenti": [
        {
          "id": 3,
          "nome": "APP 3",
          "origine": "pianta",
          "numero": 3,
          "etichetta": "u.a.03",
          "id_app": "dwg:APP 3_V1_20022026_ESECUTIVO (1)/APP 3",
          "pianta_id": "47534b3de2abc804",
          "pianta": "APP 3_V1_20022026_ESECUTIVO (1).dxf",
          "committente": true,
          "committenti": [ { "id": 5, "nome": "Lorenzo", "cognome": "Orlandi", "ruolo": "CL", "ruolo_nome": "Cliente / committente" } ]
        }
      ],
      "piante": [
        {
          "id": "47534b3de2abc804",
          "nome": "APP 3_V1_20022026_ESECUTIVO (1).dxf",
          "cantiere": "Lungo Avisio",
          "formato": "dxf",
          "dimensione": 830777,
          "modificato": "2026-11-08T14:36:33+00:00",
          "sha256": "78560a68bc418e77538f9e65b903ef6146f44fee66e2831a257c10dde8c12d42",
          "url_download": "http://192.168.1.50:8000/api/piante/47534b3de2abc804/file"
        }
      ]
    }
  ],
  "attivita": []
}
```

### 3.4 Esempio: squadra (Giulia Verdi, Mario Rossi…)

Stessa struttura, con `"accesso": "squadra"`, tutti gli appartamenti del cantiere (`Spazio comune`, `APP 1` … `APP 4`, `committente: false` se la persona non ne è committente), tutte le piante e la squadra completa. Le attività hanno il riferimento all'appartamento:

```json
"attivita": [
  {
    "id": 1,
    "titolo": "Verifica tracce bagno",
    "descrizione": "Controllare le tracce dell'impianto elettrico",
    "cantiere": { "id": 1, "nome": "Lungo Avisio" },
    "appartamento": { "id": 3, "nome": "APP 3", "id_app": "dwg:APP 3_V1_20022026_ESECUTIVO (1)/APP 3" },
    "creata": "2026-11-08T14:36:33+00:00",
    "modificata": "2026-11-08T15:20:10+00:00",
    "stato": "da_verificare",
    "stato_nome": "Da verificare",
    "stato_modificato": "2026-11-08T15:20:10+00:00",
    "storico_stati": [
      { "stato": "presa_in_carico", "stato_nome": "Presa in carico", "quando": "2026-11-08T09:02:00+00:00",
        "persona_id": 4, "chi": "Paolo Neri", "nota": "" },
      { "stato": "da_verificare", "stato_nome": "Da verificare", "quando": "2026-11-08T15:20:10+00:00",
        "persona_id": 4, "chi": "Paolo Neri", "nota": "Fatto, vedi foto" }
    ],
    "in_carico": { "persona_id": 4, "chi": "Paolo Neri", "quando": "2026-11-08T09:02:00+00:00" },
    "note": [
      { "id": 2, "quando": "2026-11-08T12:10:00+00:00", "persona_id": 4, "chi": "Paolo Neri",
        "testo": "La traccia del lavabo è più bassa di 5 cm, la sistemo domani" }
    ],
    "assegnatari": [ { "id": 4, "nome": "Paolo", "cognome": "Neri", "ruolo": "EL", "ruolo_nome": "Elettricista" } ],
    "allegati": [
      { "id": 7, "nome": "parete.jpg", "tipo": "foto", "dimensione": 245000, "sha256": "…",
        "caricato": "2026-11-08T15:20:09+00:00", "persona_id": 4, "chi": "Paolo Neri",
        "riferimento": "dwg:APP 3_V1_20022026_ESECUTIVO (1)/APP 3/Bagno/Parete 1",
        "url_download": "http://192.168.1.50:8000/api/attivita/1/allegati/7/file" }
    ]
  }
]
```

### 3.5 Campi

| Campo | Note per l'app |
|---|---|
| `versione` | Da salvare con i dati e rimandare in `If-None-Match` (tra virgolette). |
| `cantieri[].accesso` | `squadra` = tutto il cantiere; `committente` = solo gli appartamenti elencati. |
| `cantieri[].piante[].cantiere` | Cartella del cantiere sul server: l'app salva in `piante/<cantiere>/<nome>` (come fa già `PlanLibrary`). |
| `piante[].sha256` | Versione del file. Uguale a quello del file locale = non riscaricare. |
| `piante[].id` | Per il download `GET /api/piante/{id}/file`. L'app costruisce l'indirizzo da `id` e dal server del login, come fa già, e non usa `url_download`, così il token va solo a quel server. |
| `appartamenti[].id_app` | **Uguale a `WallIds.dwgApartmentId(<nome DWG senza estensione>, "APP n")`**. È la chiave per collegare l'appartamento del server a quello trovato nella pianta (`FloorPlanAnalyzer`) e alla cartella `pareti/<DWG>/<APP n>/`. |
| `appartamenti[].origine` | `pianta` (letto dal DXF), `manuale` (aggiunto dalla pagina web) o `comune` (spazio comune: tutta la planimetria, sempre primo della sua planimetria). |
| Spazio comune | `nome: "Spazio comune"`, `id_app: "dwg:<nome DWG>/Spazio comune"` (cartella dell'app `pareti/<DWG>/Spazio comune/`, per le pareti e i dati delle parti comuni), `numero` ed `etichetta` `null`. |
| `appartamenti[].nome` | `"APP n"`, come `WallIds.dwgApartmentFolderName`. Gli appartamenti aggiunti a mano sul server (es. "Box 1", o per piante DWG) hanno `numero`, `etichetta`, `id_app` e `pianta_id` a `null`. |
| `appartamenti[].pianta_id` | Pianta che contiene l'appartamento (chiave in `piante[].id`). |
| `appartamenti[].committente` | `true` se la persona collegata è committente di quell'appartamento. |
| `attivita[].appartamento` | **Sempre presente** (`id`, `nome`, `id_app`). Con `id_app` l'app mostra l'attività nell'appartamento giusto; se è uno spazio comune l'attività vale per tutta la planimetria. |
| `allegati[].tipo` | `foto`, `scansione3d` (GLB), `file`. |
| `allegati[].chi`, `persona_id`, `riferimento` | Chi l'ha caricato ("Superuser" dalla pagina web) e l'id app della parete/stanza/appartamento a cui si riferisce (3.7). |
| `appartamenti[].raccolta` | `{n_file, dimensione, aggiornato}`: riepilogo dei dati raccolti sul server (3.8). |
| `attivita[].stato` | `aperta`, `presa_in_carico`, `da_verificare`, `chiusa` (vedi 3.6). `stato_nome` è il testo da mostrare. |
| `attivita[].note` | Note libere `{id, quando, persona_id, chi, testo}`, dalla più vecchia. Le note scritte cambiando stato sono in `storico_stati[].nota`. |
| `attivita[].in_carico` | `{persona_id, chi, quando}` di chi l'ha presa in carico, mentre è `presa_in_carico` o `da_verificare`; altrimenti `null`. |
| `attivita[].storico_stati` | Cambi di stato dal più vecchio: chi (`chi`, `persona_id`; `null` = superuser dalla pagina web), quando, nota facoltativa. Vuoto se l'attività non ha mai cambiato stato. |

### 3.6 Stato dell'attività

```
aperta ──"Prendo in carico"──► presa_in_carico ──"Chiudi" (foto, file, nota)──► chiusa
  ▲  ◄────────"Rilascia"──────────┘                                              │  ▲
  │                                     server (superuser o DL) "Da verificare"  ▼  │ "Chiudi" (altri file)
  │                                                                      da_verificare
  └──────────────────────── DL / superuser "Riapri" ◄────────────────────────────────
```
(aggiornato l'8/11/2026 sera: chi lavora chiude da sé; "Da verificare" vuol dire che il server la
rimanda indietro, e la mette solo il server partendo da chiusa)

`POST /api/mie-attivita/{id}/stato` con il token dell'app:
```json
{ "stato": "presa_in_carico", "nota": "" }
```

| Chi | Passaggi permessi |
|---|---|
| Persona assegnata all'attività | `aperta` ↔ `presa_in_carico`; `presa_in_carico` → `chiusa`; `da_verificare` → `chiusa`. Se l'attività è in carico a un'altra persona, solo quella la chiude o la rilascia (403 "in carico a …") |
| Direttore dei lavori della squadra del cantiere | Qualsiasi (anche `chiusa` → `da_verificare`, riapre) |
| Superuser (pagina web, `POST /api/attivita/{id}/stato`) | Qualsiasi |

Risposte: **200** con l'attività aggiornata (stesso formato di `attivita[]`), **403** passaggio non permesso (es. l'elettricista prova a chiudere senza averla presa in carico, o a rimetterla da verificare), **404** attività non visibile per quella persona, **409** già in quello stato, **422** stato sconosciuto. Ogni cambio finisce nello storico e cambia la `versione` dei dati, quindi gli altri telefoni lo vedono al prossimo `/api/miei-dati`.

Nell'app:
- assegnatario, attività `aperta`: pulsante **"Prendo in carico"** (→ `presa_in_carico`); `presa_in_carico`: **"Chiudi"** (→ `chiusa`, con foto, file e nota facoltativa nella stessa chiamata, `aggiornamento` 3.7); `da_verificare`: aggiunge altri file e **"Chiudi"** di nuovo;
- DL: su `chiusa` **"Da verificare"** (chiede il motivo → `nota`) e **"Riapri"**;
- se l'attività ha più assegnati, `in_carico` dice chi l'ha presa ("In carico a Paolo Neri"): gli altri vedono il nome e non la riprendono (409);
- colori: Aperta blu, Presa in carico viola, Da verificare arancio, Chiusa verde (titolo barrato), come nella pagina web;
- senza rete: il cambio si mette in coda e si invia al prossimo collegamento; se il server risponde 403/409 si scarta e si rilegge `/api/miei-dati`.

**Note e storico**: `POST /api/mie-attivita/{id}/note` con `{"testo": "…"}` (chi è assegnato o il DL; 201 con l'attività aggiornata, 404 se non la vede, 422 se vuota) aggiunge una nota senza cambiare stato. Nell'app ogni attività ha "Note e storico (n)" (n = note libere + cambi di stato con nota) che apre la cronologia, dalla più vecchia: *Attività creata* (`creata`), ogni elemento di `storico_stati` ("Paolo Neri → Presa in carico" con la nota sotto) e ogni elemento di `note`, ordinati per data; in fondo il campo "Scrivi una nota…". Senza rete la nota va nella coda (punto 9 di §4).

### 3.7 Aggiornamento di un'attività dal telefono (stato + allegati)

`POST /api/mie-attivita/{id}/aggiornamento`, **multipart/form-data**, token dell'app:

| Campo | Obbligatorio | Note |
|---|---|---|
| `file` | no (ripetibile) | Foto, GLB, PDF… Stesse regole della pagina web: niente programmi/pagine web, massimo `ARWS_MAX_UPLOAD_MB` (200 MB) ciascuno |
| `stato` | no | `aperta` / `presa_in_carico` / `da_verificare` / `chiusa`, con le regole di 3.6 |
| `nota` | no | Con `stato` va nello storico dello stato; senza `stato` diventa una nota libera (`note[]`) |
| `riferimento` | no | Id app di dove è stata fatta la foto: `dwg:<DWG>/<APP n>` (appartamento), `…/<stanza>` o `…/<stanza>/Parete N` — lo stesso id di `WallCaptureStore.EXTRA_ROOM_ID` |

Serve almeno `file`, `stato` o `nota` (altrimenti 400). Il server controlla **prima** permessi, passaggio di stato e nomi dei file, poi salva i file e solo alla fine cambia lo stato: con un 403/409/400 non resta niente a metà. Risposta 200:
```json
{ "caricati": [12, 13], "attivita": { "...": "come in attivita[], con i nuovi allegati e lo stato" } }
```
Uso tipico: dal menu della parete *"Segna l'attività come fatta"* → scegli le foto della parete (`<parete>/foto/`) → una chiamata con `stato=da_verificare`, `riferimento=<id parete>` e le foto. Si può anche mandare solo foto (senza `stato`), per esempio il DL che aggiunge la foto del problema riaprendo.

### 3.8 Dati raccolti negli appartamenti (sincronizzazione della cartella `pareti/`)

Ogni appartamento del server corrisponde alla cartella dell'app `pareti/<DWG>/<APP n>/` (`id_app` = `dwgApartmentId`). Il server conserva una copia di quella cartella, file per file, con **percorso relativo all'appartamento** uguale a quello del telefono, per esempio:

```
appartamento.json
Soggiorno/report.json
Soggiorno/Parete 2/1/wall.jpg
Soggiorno/Parete 2/1/metadata.json
Soggiorno/Parete 2/foto/IMG_20261108_101500.jpg
Soggiorno/Parete 2/file/Verbale/verbale.pdf
```

| Chiamata | Cosa fa |
|---|---|
| `GET /api/appartamenti/{id}/raccolta` | Elenco di tutti i file sul server (di tutti i telefoni): `percorso`, `sha256`, `dimensione`, `chi`, `persona_id`, `caricato`, `modificato_dispositivo`, `url_download`; più `versione` e `puoi_caricare`. `If-None-Match: "<versione>"` → 304 |
| `PUT /api/appartamenti/{id}/raccolta/<percorso>` | Carica un file. Corpo = contenuto (`application/octet-stream`), intestazioni `X-Sha256: <sha256 esadecimale>` (obbligatoria) e `X-Modificato: <data ISO del file>` (facoltativa). Il percorso va codificato (`Parete%202`) |
| `GET /api/appartamenti/{id}/raccolta/<percorso>` | Scarica un file (`If-None-Match: "<sha256>"` → 304) |
| `DELETE /api/appartamenti/{id}/raccolta/<percorso>` | Elimina: chi l'ha caricato o il DL della squadra |

Risposte del `PUT`: **201** `{"esito": "nuovo"}`, **200** `"aggiornato"` (era già tuo) o `"invariato"` (stesso sha256: si può ripetere senza danni), **409** il percorso è già di un'altra persona (vedi sotto), **400** percorso non valido, file vuoto o `X-Sha256` diverso dal contenuto (trasferimento rovinato: riprovare), **403** la persona non è nella squadra (i committenti possono solo leggere), **413** troppo grande.

Percorsi accettati: fino a 10 livelli, senza `..`, `.`, cartelle o file nascosti (`.foto_esempio`, `.scansioni_esempio` e i file di stato della sincronizzazione **non si mandano**), niente caratteri vietati da Windows (`\ : * ? " < > |`), niente programmi.

**Algoritmo nell'app** (per ogni appartamento con `accesso == "squadra"`, dopo `/api/miei-dati`; per il committente solo il passo 3, in sola lettura):

1. `GET …/raccolta` (con la `versione` dell'ultima volta: se 304 e niente è cambiato sul telefono, finito).
2. **In su**: per ogni file locale (saltando nascosti e `*.tmp`) calcola lo sha256; se sul server manca o è diverso e l'ultima versione sul server è tua → `PUT`.
3. **In giù**: ogni file del server che manca sul telefono (o con sha256 diverso e non modificato sul telefono dall'ultima sincronizzazione) → download nello stesso percorso, con la stessa verifica sha256 delle piante (`replaceWithDownload`). Così ogni telefono vede scansioni, foto e report degli altri.
4. **Eliminazioni**: l'app tiene in `<appartamento>/.sync.json` l'elenco `percorso → sha256` dell'ultima sincronizzazione riuscita. File che c'era, l'hai caricato tu e ora manca sul telefono → `DELETE`. File che c'era e ora manca sul server → si cancella dal telefono.
5. **Conflitti (409)**: due telefoni con lo stesso percorso.
   - scansioni `<parete>/<n>/`: prima di caricare una scansione nuova l'app rinumera la cartella locale al primo `n` libero sia sul telefono sia nell'elenco del server, poi carica (le scansioni non si fondono mai);
   - foto e file: il nome contiene già data e ora; se c'è comunque un 409 si aggiunge `_<username>` prima dell'estensione;
   - file condivisi (`report.json`, `appartamento.json`): si scarica la versione del server, si uniscono le spunte (unione delle attività con spunta: una spunta vince sull'assenza), si ricarica con `?sostituisci=true`.
6. Si salva `.sync.json` e la `versione`.

Prima il `PUT` dei `metadata.json`/`wall.jpg` di una scansione, poi gli altri file; i file grandi (GLB) per ultimi. Senza rete non si perde niente: al giro successivo l'algoritmo ritrova le differenze da solo, non serve una coda per i dati raccolti.

### 3.10 Segnalazioni dal telefono (15/11/2026)

Nell'app la lista delle attività è diventata la lista delle **segnalazioni**: un contenuto (foto o scansione con le note e misure disegnate sopra, oppure un file) con una **descrizione**, mandato ad alcune persone del cantiere. Sul server una segnalazione **è un'attività** con `autore`: stati, note, storico e allegati sono quelli di 3.6–3.7.

`POST /api/mie-segnalazioni`, **multipart/form-data**, token dell'app:

| Campo | Obbligatorio | Note |
|---|---|---|
| `appartamento_id` | sì | Id dell'appartamento sul server (`cantieri[].appartamenti[].id`) |
| `descrizione` | sì | Non vuota, max 5000 caratteri |
| `titolo` | no | Se vuoto: la prima riga della descrizione (max 80 caratteri) |
| `persone_ids` | no | Id separati da virgola (squadra del cantiere o committenti dell'appartamento); **vuoto = tutti** (squadra + committenti). L'autore può esserci: viene tolto dagli assegnatari |
| `riferimento` | no | Id app di dove è stato fatto il contenuto (come in 3.7) |
| `file` | no (ripetibile) | Stesse regole degli allegati |

Risposte: **201** con l'attività (come `attivita[]`, con `autore`), **404** appartamento non visibile alla persona, **422** descrizione vuota o persone fuori dal cantiere, **400** file non ammesso (la segnalazione non resta).

Regole:
- l'attività ha `autore_id` (colonna nuova, `null` per quelle create dalla pagina web) e nel JSON `autore {id, nome, cognome, ruolo, ruolo_nome}`;
- la ricevono gli assegnatari, **l'autore** e il DL della squadra (`/api/miei-dati`, `/api/mie-attivita`);
- stati: gli assegnatari come in 3.6; l'autore come un assegnatario e in più **riapre** (`chiusa` → `aperta`); il DL tutto.

Nell'app (`server/PendingReports.kt`): la segnalazione si salva subito sul telefono in `attivita/<utente>/da_inviare/segnalazioni/<ms>/` e parte con «Aggiorna stato online», prima degli allegati e degli stati. Le nuove degli altri arrivano con la sincronizzazione (anche in background ogni 15 minuti su Android) e danno una notifica (`ReportSeen`: `segnalazioni_viste.json`).

### 3.9 Errori

| Risposta | Significato | Cosa fa l'app |
|---|---|---|
| 401 su `/api/login` | Utente o password errati | Messaggio nella schermata di login |
| 401 dopo il login | Token scaduto (30 giorni), password cambiata o logout | Torna al login; i dati locali restano |
| 404 su un download | Pianta o allegato non più assegnati (o eliminati) | Salta il file e lo toglie dalla copia locale |
| 304 su `/api/miei-dati` | Niente di nuovo | Usa `dati.json` |
| Rete assente / timeout | Server spento o rete diversa | Lavora con l'ultima copia, stato "offline"; le modifiche partono al giro successivo |
| 403 su un cambio di stato o un `PUT` | Passaggio non permesso / persona non nella squadra | Si toglie dalla coda e si rilegge `/api/miei-dati` |
| 409 su un `PUT` della raccolta | Percorso già caricato da un'altra persona | Regole di conflitto in 3.8 |
| 400 «X-Sha256 non corrisponde» | File arrivato rovinato | Si riprova una volta, poi al giro successivo |

## 4. Cosa cambiare nell'app (codice esistente)

Il login e la sincronizzazione ci sono già (`ServerClient`, `SignIn.syncSite`, `PlanLibrary`, `ActivityLibrary`, `SessionStore`). Le modifiche:

1. **`ServerClient.siteData()`**: una sola chiamata `GET /api/miei-dati` al posto di `miei-cantieri` + `mie-attivita`, con `If-None-Match`. Restituisce il testo oppure "invariato" sul 304. `/api/miei-cantieri` e `/api/mie-attivita` restano sul server, quindi le versioni vecchie dell'app continuano a funzionare.
2. **Modello dati** (`ActivityLibrary.parseSiteData` o un nuovo `SiteLibrary`): `ServerSite` riceve `access` (`SQUADRA` / `COMMITTENTE`), `apartments: List<ServerApartment>` e `plans: List<ServerPlan>`; `ServerActivity` riceve `apartment: ApartmentRef?` (`id`, `name`, `appId`). Il file salvato contiene la risposta intera più `versione`.
3. **Piante**: `PlanLibrary.sync` prende le piante da `data.sites.flatMap { it.plans }` invece che da `GET /api/piante` + `isPlanOfSites`. Il percorso locale resta `piante/<cantiere>/<nome>` e la verifica resta quella dello sha256 (`replaceWithDownload`). In più si **cancellano le piante scaricate che non sono più assegnate**, solo dentro `filesDir/piante/`: **mai** toccare `pareti/` (le acquisizioni dell'operatore restano anche se il cantiere non è più suo).
4. **Home / scelta pianta** (`DwgSelectionActivity`, `PlanSelectionScreen`): le piante scaricate raggruppate per cantiere (`cantieri[].nome`), con il numero di appartamenti assegnati.
5. **Committente** (`accesso == "committente"`): in `FloorPlanActivity` / `ApartmentPlanActivity` si possono aprire **solo** gli appartamenti la cui `dwgApartmentId` è tra gli `id_app` ricevuti. Gli altri si vedono grigi nella pianta e non si possono toccare. Con `accesso == "squadra"` tutto come oggi.
6. **Appartamenti senza pianta** (`id_app == null`, aggiunti a mano o spazio comune generico): si elencano nella Home del cantiere ma non si aprono sulla pianta.
6b. **Spazio comune**: nella scelta dell'appartamento (`FloorPlanActivity`, `PlanSelectionScreen`) è la prima voce di ogni planimetria, "Spazio comune · tutta la planimetria"; si apre la pianta intera (nessun appartamento evidenziato) e i dati vanno in `pareti/<DWG>/Spazio comune/` (`WallIds.dwgApartmentId(dwg, "Spazio comune")`), sincronizzati come un appartamento (3.8). Il committente non lo riceve (a meno che non gli venga assegnato).
7. **Stato** (`ServerActivity.state`, `stateHistory`): in `ActivitiesScreen` badge colorato, filtro Aperte / Prese in carico / Da verificare / Chiuse, "In carico a …" e i pulsanti della tabella 3.6 (in base a `persona.ruolo == "DL"` e agli assegnatari). Nuovo metodo `ServerClient.changeState(activityId, state, note)` → `POST /api/mie-attivita/{id}/stato`.
8. **Attività per appartamento**: in `ApartmentPlanScreen` (menu Info dell'appartamento) e in `ActivitiesScreen` si mostra il nome dell'appartamento (`appartamento.nome`) e si filtra con `id_app`. Nell'appartamento si mostrano **le sue attività più quelle dello spazio comune della sua planimetria** (con l'etichetta "Spazio comune").
9. **Coda delle modifiche** (`shared/server/Outbox`, file `outbox.json` nella cartella privata): cambi di stato, note e aggiornamenti delle attività fatti senza rete, inviati in ordine al primo `syncSite` riuscito; ogni voce tiene i percorsi delle foto da allegare. Nuovi metodi `ServerClient.addNote(id, text)` → `POST /api/mie-attivita/{id}/note` e `ServerClient.updateActivity(id, state?, note, reference?, files)` → `POST /api/mie-attivita/{id}/aggiornamento` (multipart con Ktor `submitFormWithBinaryData`).
10. **`ApartmentDataSync`** (nuovo, `shared/server`): l'algoritmo di 3.8 sopra `WallStore` (le cartelle sono già quelle), con `ServerClient.listCollected / putCollected / getCollected / deleteCollected`. Il `PUT` va fatto in streaming (Ktor `setBody` con un `OutgoingContent` che legge da okio), non caricando il file in memoria. Si chiama da `syncSite` dopo le piante.
11. **Interfaccia**: nella Home e nel menu Info dell'appartamento "Dati sul server: n file · aggiornato il …" (da `appartamenti[].raccolta`) e lo stato dell'ultima sincronizzazione (in corso / fatto / file in attesa); nelle liste di foto e scansioni un segno su ciò che viene da un altro telefono (`chi`).
12. **Login**: invariato (`SignIn`, `Role.matches`). Dopo il login si salvano subito i dati ricevuti, così la Home si apre senza una seconda chiamata.

## 5. Test da scrivere (`shared`, `FakeFileSystem`)

- Parsing di una risposta da committente e di una da squadra (gli esempi qui sopra), con `id_app` / `pianta_id` / `appartamento` a `null`.
- 304: i dati locali non cambiano e nessun file viene riscaricato.
- Una pianta tolta dalla risposta viene cancellata da `piante/`, mentre `pareti/` resta intatta.
- `id_app` del server = `WallIds.dwgApartmentId("APP 3_V1_20022026_ESECUTIVO (1)", "APP 3")`.
- Committente: è permesso solo l'appartamento assegnato.
- Spazio comune: è la prima voce della planimetria; le sue attività compaiono anche in ogni appartamento della stessa planimetria.
- 401 su `/api/miei-dati` → `NotAuthorizedException`.
- Note: cronologia ordinata (creazione + storico + note libere); nota inviata offline arriva al ritorno della rete.
- Stato: parsing di `stato` / `storico_stati` / `in_carico` (4 stati); pulsanti giusti per assegnatario e DL; 403 e 409 non rompono la lista; cambio fatto offline inviato al ritorno della rete.
- Aggiornamento: multipart con 2 foto + `stato` + `riferimento`; la coda sopravvive al riavvio dell'app.
- Raccolta (server finto): file nuovo → `PUT`; uguale → niente; file di un altro telefono → scaricato; file eliminato sul telefono → `DELETE`; eliminato sul server → tolto dal telefono; 409 su `Parete 2/1/` → rinumerata in `Parete 2/2/`; i file nascosti non partono mai.

## 6. Dati di prova (demo)

Password **`1234`** per tutte le persone (le persone create dopo ricevono una password casuale, mostrata nella pagina web).

| Utente | Ruolo da scegliere | Cosa riceve oggi (8/11/2026 sera) |
|---|---|---|
| `mario.rossi` | Direttore dei lavori | Tutti e 5 i cantieri (squadra); Lungo Avisio con Spazio comune + APP 1–4 e la pianta, gli altri con il solo Spazio comune generico; tutte le attività dei suoi cantieri |
| `giulia.verdi` | Progettista / architetto | Lungo Avisio (squadra), Spazio comune + APP 1–4 e la pianta |
| `francesca.zanin` | Cliente / committente | Lungo Avisio (squadra, quindi tutto il cantiere) e l'attività "Pulire per terra" |
| `lorenzo.orlandi` | Cliente / committente | Lungo Avisio (committente), solo APP 3 e la sua pianta |
| `paolo.neri` | Elettricista | Nessun cantiere; l'attività "Raccogliere Traccie" (assegnata prima della regola "solo la squadra") |
| `luca.bianchi` | Capocantiere | Niente finché non viene messo in una squadra |

Le assegnazioni si cambiano dalla pagina web `http://<IP-del-PC>:8000/` → cantiere → *Apri scheda*. Per provare senza l'app, da un PC sulla stessa rete:
```
curl -X POST http://<IP-del-PC>:8000/api/login -H "Content-Type: application/json" -d "{\"username\":\"lorenzo.orlandi\",\"password\":\"1234\"}"
curl http://<IP-del-PC>:8000/api/miei-dati -H "Authorization: Bearer <token>"
```
Lo sha256 e gli id dell'esempio cambiano con il file vero; la struttura no.
