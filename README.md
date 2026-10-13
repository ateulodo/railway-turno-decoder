# Railway Turno Decoder

Applicazione web standalone per trasformare, nelle milestone future, PDF con
diagrammi dei turni ferroviari SGI in dati strutturati e riutilizzabili.
Il progetto è completamente indipendente da inAgenda: non ne usa codice,
database, backend o modelli. Un eventuale collegamento futuro avverrà tramite
un formato esportato.

## Stato: Milestone 1

È disponibile il bootstrap web: caricamento sicuro di un PDF, job persistente,
lettura delle informazioni iniziali e pagina di verifica. **Il decoder SGI non
è ancora implementato.** Non sono interpretati treni, orari, minuti, accessori,
servizio vettura, periodicità, linee o giornate. Non vengono eseguiti OCR o AI;
non sono presenti database, code di elaborazione o esportazioni Excel.

## Requisiti e installazione locale

- Python 3.12 o superiore; ambiente verificato: **Python 3.14.5** su macOS Apple Silicon.
- Un virtual environment Python. Docker e Node non servono sul Mac.
- Le dipendenze e il codice non impongono un'architettura macOS/ARM64.

Dalla directory di progetto, crea il virtual environment solo se non esiste già:

```sh
cd railway-turno-decoder
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install --no-deps -e '.[dev]'
```

`pyproject.toml` dichiara le dipendenze dirette e gli intervalli compatibili;
`requirements.txt` e `requirements-dev.txt` fissano le versioni runtime e di test
verificate, comprese le dipendenze transitive. Aggiornare questi snapshot e
rieseguire i test quando si aggiornano le dipendenze. In sola esecuzione bastano
`requirements.txt` e `python -m pip install --no-deps -e .`.

FastAPI è supportata da 0.143 e Starlette è una dipendenza diretta da **1.8**
(prima della successiva major). Queste versioni minime coincidono con le
versioni verificate: Starlette 1.8 usa `httpx2` per il `TestClient` e chiude i
temporanei multipart anche quando lo stream fallisce. Le vecchie Starlette
che richiedono `httpx` sono escluse dai vincoli. Non sono state aggiornate le
versioni installate: gli snapshot rimangono quelli verificati. `packaging`, già
presente nello snapshot di sviluppo, è dichiarato esplicitamente perché usato
dai test di compatibilità delle dipendenze.

## Avvio locale

```sh
source .venv/bin/activate
uvicorn railway_turno_decoder.main:app --host 127.0.0.1 --port 8000 --reload
```

Apri [http://127.0.0.1:8000](http://127.0.0.1:8000).
Health endpoint: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).
Ferma il server con `Ctrl+C`.

| Endpoint | Comportamento |
| --- | --- |
| `GET /` | Modulo di upload con drag & drop e selezione manuale |
| `GET /health` | `{"status":"ok"}`; verifica che il processo risponda |
| `POST /jobs` | Un solo campo multipart `file`; crea e analizza il job |
| `GET /jobs/{job_id}` | Mostra il job persistito, anche dopo un riavvio |

L'upload ordinario termina con redirect `303` al risultato. HTMX usa
`HX-Redirect` al medesimo URL e mostra uno stato di caricamento. I rifiuti
restituiscono HTML leggibile con codici `400`, `413`, `415` o `422`; gli errori
interni o di storage restituiscono `500` o `503`. Un job fallito resta
consultabile tramite `GET`, con stato `failed`. Il form funziona anche senza
JavaScript; drag & drop e interazioni HTMX richiedono JavaScript.

HTMX **2.0.8** è incluso localmente in `web/static/htmx.min.js`, insieme alla
licenza Zero-Clause BSD. Non vengono usati CDN o font esterni. Origine:
[repository ufficiale HTMX, tag v2.0.8](https://github.com/bigskysoftware/htmx/tree/v2.0.8).

## Configurazione

La configurazione è centralizzata in `config.py`, con validazione Pydantic.

| Variabile | Default | Significato |
| --- | --- | --- |
| `DATA_DIR` | `./data` | Directory di persistenza; i job vanno nella sottodirectory `jobs` |
| `MAX_UPLOAD_SIZE` | `20971520` | Dimensione massima del singolo PDF, in byte (20 MiB) |
| `MAX_PDF_PAGES` | `1000` | Massimo numero di pagine; verificato prima di caricare la prima pagina |
| `MAX_PDF_ANALYSIS_SECONDS` | `60` | Budget cooperativo di ispezione in secondi, dopo l'acquisizione del lock PDF |

I percorsi relativi si riferiscono alla directory di lavoro del processo.
I limiti di byte e pagine devono essere interi strettamente positivi; il budget
deve essere un numero finito strettamente positivo. Valori non validi
impediscono l'avvio. Il limite predefinito di 1.000 pagine permette documenti
con oltre 400 pagine. Esempio:

```sh
DATA_DIR=./data MAX_UPLOAD_SIZE=10485760 MAX_PDF_PAGES=1000 MAX_PDF_ANALYSIS_SECONDS=60 uvicorn railway_turno_decoder.main:app --host 127.0.0.1 --port 8000
```

`.env.example` è un promemoria: l'app non carica automaticamente file `.env`.
Esporta le variabili nel terminale. In Compose `.env` può fornire il valore
interpolato dei tre limiti; `DATA_DIR` nel container è `/app/data`.

## Architettura e struttura

```text
railway-turno-decoder/
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── README.md
├── .gitignore
├── .env.example
├── Dockerfile
├── compose.yaml
├── .dockerignore
├── samples/.gitkeep
├── data/.gitkeep
├── src/railway_turno_decoder/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── errors.py
│   ├── jobs.py
│   ├── pdf/
│   │   ├── __init__.py
│   │   ├── reader.py
│   │   └── geometry.py
│   ├── parser/__init__.py
│   ├── rules/__init__.py
│   ├── validation/__init__.py
│   ├── export/__init__.py
│   └── web/
│       ├── __init__.py
│       ├── routes.py
│       ├── middleware.py
│       ├── views.py
│       ├── templates/{base,index,job}.html
│       └── static/{app.css,app.js,htmx.min.js,htmx.LICENSE}
└── tests/
    ├── conftest.py
    ├── unit/{test_config,test_reader,test_jobs,test_dependencies}.py
    └── integration/{test_web,test_stream_limit}.py
```

- `main.py`: factory FastAPI, dipendenze applicative e gestione degli errori.
- `jobs.py`: ciclo di vita, copia a blocchi e persistenza filesystem; separato dal trasporto HTTP.
- `pdf/reader.py`: accesso a PyMuPDF e informazioni del documento; nessuna regola SGI.
- `models.py`: envelope del job e riepilogo PDF; non è il futuro modello normalizzato dei turni.
- `web/`: ricezione HTTP, template Jinja2, HTMX e asset locali.
- `geometry.py`, `parser/`, `rules/`, `validation/`, `export/`: punti di estensione documentati, senza implementazioni anticipate.

L'analisi avviene nella stessa richiesta, usando il thread pool di Starlette per
non bloccare l'event loop durante I/O e lettura. Tutte le operazioni PyMuPDF
sono serializzate con un lock di processo, dall'apertura alla chiusura del
documento, per evitare chiamate concorrenti alla libreria. Il comando Docker
usa un solo worker Uvicorn. PyMuPDF non deve essere usato concorrentemente
tramite thread in modo non supportato. Il lock serializza le analisi soltanto
all'interno del processo e **non è una soluzione generale per un deployment
multi-worker**: prima di introdurre più worker/processi bisognerà rivalutare
esplicitamente il modello di elaborazione. Non esistono worker di elaborazione o code gestite
dall'applicazione. I log Python registrano UUID, stato ed errori tecnici; non
viene registrato intenzionalmente il testo del documento.

### Pipeline futura

```text
PDF → estrazione geometrica RAW → segmentazione turni/giornate
    → decoder SGI / DECODED DATA → modello NORMALIZED DATA
    → validazione → verifica/correzione → export
```

I livelli resteranno distinti:

1. **RAW EXTRACTION**: testo, coordinate, bbox, rotazioni, primitive, stili e
   provenienza geometrica. La Milestone 2 introdurrà `raw.json` con words,
   drawings, spessori, dash pattern e coordinate originali/normalizzate rispetto
   alla rotazione. Nessuna di queste estrazioni è implementata ora.
2. **DECODED DATA**: interpretazione SGI del raw, con riferimenti alla provenienza.
3. **NORMALIZED DATA**: dati indipendenti dal documento, consumabili dagli exporter.

Dato originale, interpretazione automatica e correzione manuale dovranno restare
conservabili separatamente. Il riepilogo attuale non sostituisce questi livelli.
JSON, CSV, Excel e un eventuale formato consumabile da inAgenda saranno
milestone successive.

## Upload, persistenza e schema

Ogni PDF che supera i controlli preliminari riceve un **UUID v4 generato dal
server**. Il nome originale è solo metadata e non costruisce mai percorsi.

```text
data/jobs/<uuid>/
├── source.pdf
└── job.json
```

`job.json` usa `schema_version: "0.1"`, UUID, nome originale, timestamp UTC,
dimensione della sorgente, stato, documento, warning ed errori. Il documento
contiene numero pagine, metadata disponibili e, per ogni pagina, dimensioni
in punti, rotazione e presenza di testo. Le pagine sono numerate da 1; le
dimensioni sono quelle visibili **dopo** la rotazione. Il contenuto testuale
non viene conservato in questa milestone.

La sequenza è `created → processing → completed`, oppure `failed` in caso di
errore. Un job completato richiede un documento e nessun errore; un job fallito
richiede almeno un errore. Il JSON viene scritto su un file temporaneo e
sostituito atomicamente. Il PDF viene copiato a blocchi di 64 KiB e rinominato
solo a copia terminata; i file parziali vengono rimossi in caso di errore.
Se fallisce la prima persistenza, viene tentata la rimozione della sola
directory UUID appena creata tramite `rmdir`: directory non vuote, symlink
e directory già esistenti non vengono cancellati. Un errore di cleanup viene
registrato senza mascherare l'errore originale. Non viene usata cancellazione
ricorsiva. Se la persistenza di `failed` fallisce, viene restituito `503` e il
precedente JSON valido può conservare lo stato `created` o `processing`.

La sicurezza dell'upload comprende:

- Un solo file `.pdf`, nessun campo aggiuntivo e nessun UUID scelto dal client.
- Apertura e verifica `is_pdf` tramite PyMuPDF; estensione e MIME non sono prove del contenuto.
- Limite del corpo HTTP durante la ricezione, anche con `Content-Length` assente o falso:
  `MAX_UPLOAD_SIZE + 64 KiB` di overhead multipart. Il limite esatto del file viene
  verificato dopo il parsing e nuovamente durante la copia.
- Spooling temporaneo gestito da Starlette: non viene letto l'intero upload in RAM.
- Job e lookup basati su UUID canonici; rifiuto di symlink nelle directory job e nel JSON letto.
- Escaping HTML automatico di filename e metadata.
- Errori espliciti per PDF vuoti, corrotti e protetti da password. PDF riparabili
  da PyMuPDF sono accettati con un warning; documenti senza testo segnalano un warning.
- Limite pagine verificato subito dopo l'apertura, prima di caricare pagine:
  un superamento produce errore controllato `422` e job `failed`.
- Budget cooperativo verificato dopo l'apertura, prima/dopo ogni pagina e
  dopo la costruzione del riepilogo: un superamento produce `422` e `failed`.
- Nessuna esecuzione del PDF, apertura di allegati o passaggio dei contenuti alla shell.

I rifiuti preliminari (estensione, dimensione, multipart) non creano job.
Gli errori di analisi creano un job `failed`; se il filesystem non permette
neppure di scrivere il JSON, l'app restituisce `503` e non dichiara un successo.
Un'interruzione del processo può lasciare un job `created` o `processing`:
questa milestone non implementa ripresa automatica. `/health` è un controllo
di vitalità, non una verifica di spazio o permessi di storage.

### Limiti deliberati del budget cooperativo

Il tempo viene misurato con un clock monotono dopo aver acquisito il lock:
l'attesa per un'altra analisi non consuma il budget di ispezione. I controlli
avvengono tra operazioni e non interrompono una chiamata nativa già in corso.
**Non è un timeout coercibile e non protegge da crash nativi o OOM.** Anche
l'apertura e l'analisi di una singola pagina possono superare tempo o memoria
prima che il codice Python riprenda il controllo. Una deadline cooperativa e
il limite pagine riducono il lavoro successivo, senza garantire un tetto alla
memoria o alla durata complessiva della richiesta.

Isolamento in processi, limiti rigidi delle risorse e gestione robusta degli
arresti restano rimandati a una futura fase di hardening, prima di ampliare il
modello di elaborazione o accettare upload non fidati. Non sono stati introdotti
multiprocessing, worker esterni, sandbox o code in questa milestone.

## PDF reali e Git

Inserisci eventuali PDF reali di sviluppo in `samples/`. Tutto il contenuto di
`samples/` e `data/` è ignorato da Git, **eccetto i rispettivi `.gitkeep`**.
Queste directory sono escluse anche dal contesto Docker. Non usare `git add -f`
su PDF o job. Se scegli un'altra `DATA_DIR` dentro il repository, aggiungila
prima a `.gitignore` e `.dockerignore`.

## Test e controlli

```sh
source .venv/bin/activate
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m pip check
git diff --check
git diff --stat
git status --short --branch
```

I test generano piccoli PDF sintetici con PyMuPDF: non usano documenti aziendali.
Verificano endpoint, upload, UUID, directory e JSON, metadata, rotazioni,
persistenza, filename ostili, escaping, limiti durante lo streaming ed errori
di elaborazione/scrittura. I test di hardening verificano inoltre limiti pagine,
budget con clock controllato (senza attese), compatibilità dichiarata
Starlette/TestClient, atomic replace fallito, scrittura parziale del PDF,
prima persistenza fallita e cleanup sicuro, impossibilità di salvare `failed`,
symlink di `job.json` e isolamento di due upload concorrenti con il lock attuale.
Overflow e disconnessione sono verificati anche dopo lo spooling effettivo
su disco, forzando i temporanei dentro la directory pytest del repository.
Il test di concorrenza non certifica la thread-safety generale della libreria.
I temporanei pytest restano in `.pytest-tmp/`,
ignorata da Git. Ruff controlla stile, import e problemi Python comuni;
`python -m ruff format .` applica la formattazione.

PyMuPDF 1.28.2 può emettere warning di deprecazione dei binding SWIG con Python
3.14 (`SwigPyPacked`, `SwigPyObject`, `swigvarlink`). Non vengono nascosti;
verificare il riepilogo pytest durante gli aggiornamenti delle dipendenze.

## Docker: preparazione per Debian x86_64

**Docker non è richiesto sul Mac.** I file sono predisposti per il miniserver;
la build e l'esecuzione effettive dell'immagine devono essere validate lì.
L'immagine usa `python:3.14-slim`, installa solo le dipendenze runtime e il
package, include template/asset ed esegue il servizio come UID/GID **10001**.

La porta interna è `8000`; Compose pubblica esclusivamente
**`127.0.0.1:8090:8000`**, con persistenza **`./data:/app/data`**.
Il healthcheck esegue `GET /health` tramite la libreria standard Python.
`0.0.0.0` nel comando Uvicorn è l'ascolto **interno al container**; la porta
pubblicata sull'host resta vincolata a loopback.

Comandi da eseguire **solo in futuro sul miniserver**, dalla directory clonata:

```sh
# Una tantum: il bind mount deve essere scrivibile dall'utente del container.
mkdir -p data/jobs
sudo chown -R 10001:10001 data/jobs

docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 decoder
curl --fail http://127.0.0.1:8090/health
```

Per aggiornamenti futuri: `git pull`, poi `docker compose up -d --build`.
Il flusso previsto è Mac → repository Git privato → miniserver Debian →
Docker Compose → loopback `8090` → Cloudflare Tunnel → browser.
Questa milestone non configura Cloudflare, non esegue deploy e non crea
repository remoti, commit o push.
