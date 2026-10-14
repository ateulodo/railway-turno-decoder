# Railway Turno Decoder

Applicazione web standalone per trasformare, nelle milestone future, PDF con
diagrammi dei turni ferroviari SGI in dati strutturati e riutilizzabili.
Il progetto è completamente indipendente da inAgenda: non ne usa codice,
database, backend o modelli. Un eventuale collegamento futuro avverrà tramite
un formato esportato.

## Stato: Milestone 3A

Sono disponibili upload sicuro, raw geometrico M2, classificazione strutturale,
regioni di riga e inspector. La M3A aggiunge assi temporali, mapping geometrico
x ↔ minuti ed endpoint di segmenti candidati, senza associazioni ferroviarie.
**Il decoder SGI non è ancora implementato.** Non si interpretano numeri treno,
stazioni, parentesi, accessori o significati di linee e tratteggi. Nessun OCR,
AI, database, coda, multiprocessing, export Excel o API per inAgenda.
Il raw M2 resta immutato e distinto dai risultati temporali derivati.

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
| `GET /jobs/{job_id}` | Riepilogo documento, tipi di pagina, righe e collegamenti inspector |
| `GET /jobs/{job_id}/pages/{page_index}` | Inspector della pagina, indice da zero |
| `GET /jobs/{job_id}/pages/{page_index}/image.png?scale=1.25` | Rendering PNG controllato e on-demand |
| `GET /jobs/{job_id}/pages/{page_index}/temporal.json` | M3A derivata on-demand, solo DUTY_DIAGRAM completati |

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
│   ├── raw_models.py
│   ├── errors.py
│   ├── jobs.py
│   ├── pdf/
│   │   ├── __init__.py
│   │   ├── reader.py
│   │   ├── geometry.py
│   │   ├── extractor.py
│   │   └── render.py
│   ├── parser/{__init__,page_classifier,row_detector}.py
│   ├── temporal/{models,tolerances,axis,segments,analysis,storage}.py
│   ├── rules/__init__.py
│   ├── validation/__init__.py
│   ├── export/__init__.py
│   └── web/
│       ├── __init__.py
│       ├── routes.py
│       ├── middleware.py
│       ├── views.py
│       ├── templates/{base,index,job,inspector}.html
│       └── static/{app.css,app.js,inspector.js,htmx.min.js,htmx.LICENSE}
├── scripts/{inspect_local,inspect_temporal}.py
└── tests/
    ├── conftest.py
    ├── unit/{test_config,test_reader,test_jobs,test_dependencies,test_geometry,test_temporal}.py
    └── integration/{test_web,test_stream_limit,test_raw_inspector,test_temporal_web}.py
```

- `main.py`: factory FastAPI, dipendenze applicative e gestione degli errori.
- `jobs.py`: ciclo di vita, copia a blocchi e persistenza filesystem; separato dal trasporto HTTP.
- `pdf/reader.py`: accesso a PyMuPDF e informazioni del documento; nessuna regola SGI.
- `models.py`: envelope del job e riepilogo PDF; non è il futuro modello normalizzato dei turni.
- `web/`: ricezione HTTP, template Jinja2, HTMX e asset locali.
- `raw_models.py`: schema raw indipendente dallo schema job.
- `pdf/geometry.py`: conversione di Point/Rect/Quad e primitive in JSON nostro.
- `pdf/extractor.py`: unica apertura e singolo passaggio pagine; testo e vettori.
- `pdf/render.py`: PNG on-demand con limiti di scala e pixel.
- `parser/`: classificazione e segmentazione geometrica pura; non importa PyMuPDF.
- `rules/`, `validation/`, `export/`: estensioni future, senza decoder anticipato.

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
   provenienza geometrica. Implementata nella Milestone 2, produce `raw.json`.
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
├── job.json
├── raw.json
└── raw_pages/<extraction_uuid>/<page_index>.json
```

`job.json` usa `schema_version: "0.1"`, UUID, nome originale, timestamp UTC,
dimensione della sorgente, stato, documento, warning, errori e `raw_summary` opzionale. Il documento
contiene numero pagine, metadata disponibili e, per ogni pagina, dimensioni
in punti, rotazione e presenza di testo. Le pagine sono numerate da 1; le
dimensioni sono quelle visibili **dopo** la rotazione. Il contenuto testuale
viene conservato, con geometria e provenance, nel raw separato.

La sequenza è `created → processing → completed`, oppure `failed` in caso di
errore. Un job completato richiede un documento e nessun errore; un job fallito
richiede almeno un errore. Il JSON viene scritto su un file temporaneo e
sostituito atomicamente. Il PDF viene copiato a blocchi di 64 KiB e rinominato
solo a copia terminata; i file parziali vengono rimossi in caso di errore.
Se fallisce la prima persistenza, viene tentata la rimozione della sola
directory UUID appena creata tramite `rmdir`: directory non vuote, symlink
e directory già esistenti non vengono cancellati. Un errore di cleanup viene
registrato senza mascherare l'errore originale. Non viene usata cancellazione
ricorsiva per i job. La sola generazione raw appena creata viene rimossa
ricorsivamente in caso di errore di scrittura. Se la persistenza di `failed` fallisce, viene restituito `503` e il
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
- Budget cooperativo verificato dopo l'apertura, durante hashing, prima/dopo
  testo e drawings, tra conversioni e pagine, dopo il parser e durante la
  persistenza raw: un superamento produce `422` e `failed`.
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

## Raw schema 1.0

**`raw.json` è una rappresentazione tecnica del PDF orientata alla provenance;
non è il formato finale esportabile dei turni.** Lo schema del job resta 0.1:
i job della Milestone 1 continuano a essere leggibili senza `raw_summary`.
La loro pagina invita a caricare nuovamente il documento per ottenere il raw.

| Livello | Campi principali |
| --- | --- |
| Documento | `raw_schema_version`, UUID di estrazione, SHA-256 dei byte, dimensione, `page_count`, metadata, `uniform_rotation` oppure null, unità `points`, `pages` |
| Pagina | `page_number` da 1, `page_index` da 0, dimensioni display, rotazione, MediaBox/CropBox, bounds e matrici, tipo e motivazioni, words, drawings, rows, warnings |
| Word | `word_index` stabile nella pagina, testo originale, bbox original/display, numeri block/line/word PyMuPDF |
| Drawing | indice, tipo, bbox, spessore, dash pattern originale, colori/opacità stroke/fill, cap/join, close/even-odd, seqno/layer, primitive e altri metadata JSON |
| Primitive | indice nel drawing, tipo nativo (`l`, `c`, `re`, `qu` ecc.), punti e bbox original/display, argomenti geometrici tipizzati e argomenti scalari preservati |
| Row | indice nella pagina, bbox original/display, marker raw, numero raw oppure null, anchor words, member words, drawing/item references e motivazioni |

`get_text("words", sort=False)` conserva l'ordine raw: non ordiniamo o fondiamo
parole vicine e non normalizziamo il testo memorizzato. Tutti gli indici sono
locali alla pagina; quelli delle primitive sono locali al drawing. Le coppie
`drawing_index`/`item_index` identificano le primitive esatte. I bbox delle
curve delimitano i punti di controllo; non sono una stima della sola curva.
I segmenti possono avere bbox con larghezza o altezza zero. `extra` conserva
metadata nativi ulteriori convertiti in valori JSON, senza oggetti PyMuPDF.
Nessun path di esecuzione, username o informazione della macchina viene
aggiunto al raw; i metadata provengono dal documento e possono essere riservati.

### Coordinate e rotazione

Tutte le misure sono in punti (72 punti = 1 pollice). `original_bbox` indica
lo spazio **PyMuPDF non ruotato**, con origine in alto a sinistra e relativo al
CropBox. Non coincide con le coordinate PDF native a origine in basso a sinistra.
`display_bbox` indica la pagina visibile dopo rotazione, origine in alto a
sinistra, x verso destra e y verso il basso.

Si usano `page.rotation_matrix` e `page.derotation_matrix`, senza formule
speciali per il documento. `pdf_to_original_matrix` collega lo spazio PDF
nativo (user space, origine convenzionale in basso a sinistra) allo spazio
PyMuPDF non ruotato relativo al CropBox, lo stesso di `original_bbox`.
La proprietà `page.transformation_matrix` di PyMuPDF 1.28.2 perde la traslazione
del CropBox sulle pagine ruotate: per questo si legge la matrice ufficiale con
rotazione temporaneamente zero, ripristinando la rotazione in `finally`, entro
il lock nativo e senza salvare il PDF. I bbox corretti non vengono adattati
alla proprietà difettosa. L'inversa calcolata consente original → PDF;
la composizione con `rotation_matrix` consente PDF → original → display.
Non sono aggiunti campi inversi o nuovi campi allo schema raw.
I test partono da punti PDF nativi noti e verificano trasformazione, round-trip
e composizione con CropBox traslato nelle quattro rotazioni.
MediaBox/CropBox sono i valori nativi delle rispettive proprietà:
non vengono equiparati implicitamente al rettangolo display. I quattro angoli
dei rettangoli sono trasformati nelle primitive; il bbox trasformato racchiude
la geometria. Test sintetici coprono 0/90/180/270 gradi, anche con CropBox traslato.
Riferimenti: [coordinate delle pagine PyMuPDF](https://pymupdf.readthedocs.io/en/latest/page.html),
[primitive vettoriali](https://pymupdf.readthedocs.io/en/latest/recipes-drawing-and-graphics.html).

### Classificazione deterministica e conservativa

Ordine delle regole, senza numeri pagina fissi:

1. `TRAIN_PERIODICITY_NOTES`: heading geometricamente contiguo sulle note di
   periodicità nella fascia superiore (18%), con matching di parole intere.
   Una vista derivata raggruppa le parole per centro verticale (tolleranza
   `max(0.5 pt, 30% della minore altezza)` rispetto a un anchor fisso) e le
   ordina per x display; grandi spazi orizzontali separano i gruppi. Sono
   ammessi gap da -25% a 150% della minore altezza dei word adiacenti.
   Accenti e punteggiatura sono normalizzati solo per il matching. Parole
   sparse non bastano e l'ordine raw non viene modificato. Le motivazioni
   referenziano gli indici raw originali anche quando l'emissione è permutata.
2. `INDEX`: campi strutturali planning/profilo/turno/data/pubblicazione nella
   fascia superiore (18% dell'altezza).
3. `DUTY_DIAGRAM`: turno e profilo nella fascia superiore, almeno tre fra le
   etichette Lav/Cct/Km/Not/Rip, marker nel margine sinistro e almeno due
   segmenti orizzontali coerenti. Le etichette sono segnali di layout.
4. `UNKNOWN`: segnali insufficienti. Nessuna classificazione forzata.

Le motivazioni contengono codici e riferimenti raw. Queste euristiche descrivono
layout compatibili; non certificano l'identità o il significato di una pagina.

### Segmentazione delle righe

Solo sulle pagine `DUTY_DIAGRAM`, regex `^[LMXGVSD]{1,7}$` più posizione nel
margine sinistro (12% della larghezza), fascia verticale 4–95%, allineamento
alla colonna più a sinistra e supporto di primitive orizzontali. Non occorre
una singola linea lunga: segmenti collineari brevi costituiscono supporto.
Un segmento deve essere lungo almeno 1,5% della pagina, con tolleranza verticale
`max(0.5 pt, 0.2% altezza)` e raggiungere oltre il 15% della larghezza.

Gli anchor sono ordinati verticalmente. Il bordo preferito di ogni banda è
mezzo marker sopra il bordo superiore, ma viene limitato inferiormente dal
bordo inferiore dell'anchor precedente (4% della pagina per il primo). Così
altezze differenti non possono invertire i confini o tagliare l'anchor precedente.
La banda finisce all'inizio della successiva. L'ultima usa il passo mediano dei
bordi ordinati (25% dell'altezza con un solo marker) e viene estesa almeno fino
a mezzo marker dopo l'ultimo anchor, entro il 95% della pagina.
Prima di usare la ricerca binaria si verificano confini strettamente monotoni,
bande positive e contenimento completo degli anchor. Anchor sovrapposti o non
contenibili producono un warning esplicito di layout ambiguo e nessuna riga
per quella pagina, senza attribuire il rifiuto a un falso difetto di supporto.
Le bande valide sono consecutive e non sovrapposte, associate ai marker originali.
I word appartengono alla banda che ne contiene il centro verticale (intervalli
semiaperti); le primitive sono referenziate per intersezione del loro bbox.
Una primitiva può quindi appartenere a più bande senza essere duplicata nel raw.

Un numero è assegnato soltanto se esiste un unico token ASCII di 1–4 cifre sotto
il marker nella stessa banda e nella colonna stretta di sinistra. Altrimenti
rimane null. Marker sovrapposti o bande senza supporto generano warning; tutti
i token originali restano disponibili. Varianti dello stesso numero rimangono
righe distinte: non vengono aggregate per numero o marker. Il numero e il marker
sono stringhe strutturali, senza identità ferroviaria dedotta.

L'estrazione visita le pagine una volta e non effettua ricerche quadratiche
sull'intero documento. Per pagina usa ordinamento dei marker e ricerca binaria
per word e intervalli dei vettori: O(W log R + V log R + K), oltre all'ordinamento,
dove K è il numero effettivo di riferimenti alle bande; la vista header richiede
inoltre O(H log H) per H parole nella fascia superiore. Non è promesso un limite
rigido di memoria: geometria molto complessa può produrre molti oggetti.

### Persistenza raw e inspector

Le pagine vengono serializzate una per volta. Le copie per pagina sono prima
scritte in una nuova generazione UUID; `raw.json` viene pubblicato con replace
atomico di un temporaneo esclusivo nella medesima directory. Un errore prima
del replace conserva il raw precedente e tenta il cleanup della nuova
generazione. `job.json` viene completato soltanto dopo la pubblicazione raw.
Non è una transazione fra file e non garantisce durabilità a un power loss:
se fallisce la successiva scrittura del job, può esistere un raw valido insieme
a un job fallito. L'inspector richiede sempre un job completato.

Le copie consentono lettura di una sola pagina senza caricare tutto il raw.
La generazione referenziata nel riepilogo impedisce di mescolare pagine di
estrazioni diverse. Le vecchie generazioni complete sono conservate; non viene
implementata garbage collection. Il raw canonico più le copie richiedono circa
il doppio dello spazio JSON, oltre al PDF. Tutti sono dati riservati locali.

La pagina job mostra distribuzione dei tipi e righe; l'inspector mostra PNG,
bande SVG, bbox dei token e dei drawing attivabili, selezione tramite click o
menu e dettagli original/display del token. Il dettaglio delle righe include
provenance e motivazioni. Non è un editor. I drawing sono visualizzati come bbox;
le primitive fedeli restano nel raw, senza interpretazione degli stili. Per
rendere visibili i bbox degeneri dei segmenti, il solo overlay usa un minimo
di 0.5 pt; le coordinate raw rimangono inalterate.

Il rendering avviene soltanto su richiesta, senza cache permanente o rendering
preventivo. Scala finita **0.25–2**, default **1.25**, massimo **8.000.000 pixel**
verificati prima di allocare il pixmap RGB; riusa il lock nativo e il budget
cooperativo. Lo stesso limite nativo di timeout/memoria si applica al rendering.
UUID canonico, indice valido rispetto al job e al PDF, rifiuto di symlink per
sorgente, copie e directory di generazione. Nessun endpoint accetta path o
serve direttamente `source.pdf`/`raw.json`. HTML autoescaped; JSON embedded con
`tojson`; JavaScript usa `textContent` per i token. Nessun asset esterno.

### Validazione locale opzionale

I test ordinari usano solo PDF sintetici. Per un PDF locale autorizzato,
ignorato da Git, è disponibile un probe separato:

```sh
python scripts/inspect_local.py --pdf samples/local.pdf --data-dir data/local-validation
```

Se il file è assente stampa `SKIPPED` e termina con successo. Se presente,
esegue la pipeline normale, salva un job locale (anche la sorgente sotto
`data/`) e stampa conteggi e durata. Non modifica l'originale. Per aprirlo
nell'inspector avvia l'app con la stessa `DATA_DIR`. Non è parte dei test CI;
non contiene codici, pagine o token specifici di documenti aziendali. I controlli
canonici di documenti riservati restano in strumenti locali ignorati, senza
copiare PDF nei test o riprodurli nella documentazione.

Limitazioni: euristiche di layout da verificare su altri documenti; nessun OCR;
nessuna gestione semantica di tabelle, treni o minuti; CropBox/rotazioni verificati
su casi sintetici, non su ogni possibile PDF; nessun timeout nativo coercibile;
nessuna ripresa automatica dei job interrotti. Un risultato senza warning non
costituisce una certificazione semantica di correttezza.

## M3A: interpretazione geometrico-temporale

`railway-turno-decoder.temporal`, schema **0.1**, è un modello derivato separato.
Non è il JSON normalizzato dei turni e non cambia il raw schema 1.0 né il job
schema 0.1. I moduli `temporal/` consumano esclusivamente coordinate display e
riferimenti M2; non importano PyMuPDF e non consultano la rotazione originale.

### Rilevamento dell'asse

Per ogni row si raggruppano geometricamente token ASCII numerici di 1–2 cifre
con valore 0–24, escludendo gli anchor strutturali della row. Non basta il
valore numerico: servono almeno tre label allineate, una sequenza crescente di
ore srotolata oltre mezzanotte, span sufficiente rispetto alla larghezza dei
glifi e copertura vettoriale orizzontale vicina alla linea delle label.

Ipotesi deterministiche sono generate da coppie vicine; ogni ipotesi deve
spiegare almeno il 65% delle label della linea. Si ammettono gap fino a quattro
ore per tick mancanti. Outlier numerici sono esclusi conservando i riferimenti.
Solo dopo la verifica del reticolo orario si calcola il fit affine e se ne
controlla il residual massimo. Due assi diversi ugualmente supportati danno
AMBIGUOUS. Nessuna regola dipende da pagina, servizio o nome del documento.

La ricerca è locale e limitata a 128 parole per linea e quattro vicini per
coppia. Il costo delle ipotesi è O(R · K² · P) per pagina, con K limitato e
P=4; non si confrontano token o segmenti di pagine diverse. Il matching delle
label usa un indice x locale alla row. Il budget cooperativo è controllato
tra row, ipotesi e segmenti; restano i limiti nativi e di memoria M2 già descritti.

### Mapping e mezzanotte

`absolute_minutes = minutes_per_point * display_x + intercept_minutes`.
L'origine di calendario è fittizia: giorno zero è la mezzanotte precedente
la prima ora osservata dell'asse. Il primo `24` equivale a 00 sul giorno zero;
nei successivi anchor il passaggio 23 → 24/0 → 1 produce valori crescenti,
per esempio 1380 → 1440 → 1500. Non si applica modulo 24 ai minuti interni.
`origin_x` e `origin_absolute_minutes` identificano il primo anchor; gli endpoint
espongono anche `minutes_from_axis_origin`. `TimeMapping.x_at()` implementa
l'inversa. Nessuna data reale viene dedotta.

HH:MM è una visualizzazione arrotondata al minuto più vicino; il valore
continuo resta conservato. `rounding_residual_minutes` rende esplicita la
perdita di precisione. `day_offset` accompagna la visualizzazione arrotondata;
`crosses_midnight` usa invece i minuti geometrici non arrotondati. L'header
start/end del documento non corregge mai mapping o segmenti.

### Segmenti ed endpoint

Si considerano solo primitive `l` con due punti, prevalentemente orizzontali,
contenute nella row e nell'estensione misurata dell'asse. La soglia minima di
lunghezza equivale a un minuto secondo il mapping. Baseline dell'asse, bordi
verticali, frammenti insignificanti e primitive fuori regione sono esclusi.
Le curve non sono convertite automaticamente in segmenti temporali.

Ogni candidato conserva riferimento drawing/item, estremi originali del path
nello spazio display, estremi normalizzati sinistra/destra, stili raw e motivi
di rilevamento. Spessore e dash pattern non assegnano tipi operativi.
Gli endpoint derivano da x; nessuna label sostituisce il tempo geometrico.

Le label numeriche 0–59 vicine sono evidenze locali: si controllano distanza
orizzontale dagli edge del bbox e distanza verticale dall'endpoint. Sono
escluse le label dell'asse e gli anchor della row. Tutte le evidenze conservano
word index, bbox, distanze e residual rispetto al tempo geometrico. Una label
è compatibile solo se il residual, sommato al massimo residual dell'asse,
è strettamente inferiore a mezzo minuto. Più label compatibili rimangono ambigue.

| Stato endpoint | Significato |
| --- | --- |
| EXACT | Unica label numerica geometricamente vicina e temporalmente compatibile; non significa errore fisico nullo |
| DERIVED | Mapping valido, senza evidenza numerica vicina; tempo interamente geometrico |
| AMBIGUOUS | Label in conflitto o più evidenze compatibili; il tempo geometrico rimane disponibile e non viene corretto |
| INVALID | Endpoint fuori dall'estensione ammessa o struttura non valida |

Una row senza asse determinabile è AMBIGUOUS e non riceve segmenti inventati.
Una row con asse valido può essere AMBIGUOUS per i suoi endpoint. Gli anchor
orari espongono label raw, candidato numerico, punto display, minuti srotolati,
word index e residual. I risultati referenziano generazione raw, SHA-256 del
PDF, pagina, row, anchor della row e primitive. Non contengono copie delle
words/drawings complete o percorsi locali.

La validazione strutturale della row controlla tutti e quattro i bordi:
`0 <= left < right <= page.width`, `0 <= top < bottom <= page.height`.
Un bbox malformato produce INVALID senza correggere il raw. I riferimenti di
supporto dell'asse e le motivazioni correlate sono ordinati per drawing/item;
gli ordini geometrici di anchor, endpoint e path restano distinti.

### Tolleranze e limiti

Le tolleranze sono centralizzate in `temporal/tolerances.py` e registrate nel
risultato. Allineamento delle label: `max(0.6 pt, 35% altezza del glifo)`;
residual massimo dell'asse 0.35 minuti; copertura vettoriale minima 40% dello
span. Questi vincoli verificano allineamento, spaziatura e supporto distinti.
Le soglie geometriche dei segmenti sono 0.5 pt o pendenza massima 3%.
Il matching nearby usa 25% del passo orario in x e 30% dell'altezza row in y.
La compatibilità temporale è separata dalle distanze geometriche. I probe
locali mostrano label orarie equispaziate e residual molto inferiori al limite;
i test sintetici verificano rumore, tick mancanti/spuri e conflitti. Non viene
applicata alcuna eccezione per accomodare gli outlier.

### Inspector e persistenza

L'inspector aggiunge toggle indipendenti per asse, segmenti e label degli
endpoint, filtro per row e click su anchor/segmenti per provenance e dettagli.
I colori distinguono stati deterministici, non attività. I dettagli sono
serializzati con `tojson` e mostrati con `textContent`, come il raw M2.

L'analisi e la cache sono **on-demand per pagina**, senza cambiare il ciclo
upload M2 e senza analizzare tutte le pagine per aprirne una. Il percorso è:

```text
data/jobs/<uuid>/temporal/<raw_generation>/<algorithm_and_tolerances_hash>/<page_index>.json
```

Il digest comprende versione dell'algoritmo e tolleranze. Futuri cambi logici
devono aggiornare la versione per invalidare cache precedenti. Scrittura con
temporaneo esclusivo e replace atomico; nessuna modifica a `raw.json`, copies
M2 o stato del job. Generazioni diverse non condividono cache. UUID/indici e
symlink sono controllati anche sul nuovo percorso. Cache corrotta o mismatch
produce errore controllato 503 nell'endpoint JSON; l'HTML conserva l'inspector
M2 mostrando che M3A non è disponibile. Il JSON usa Content-Type dedicato,
`no-store` e `nosniff`. I job incompleti e le pagine non diagramma non sono
analizzati. Non esistono endpoint con path forniti dal browser.

Prima di utilizzare una cache si verifica anche la corrispondenza 1:1 delle
righe con il RawPage caricato: indici, bbox e anchor sorgenti devono coincidere.
Ogni word/drawing/item referenziato, incluse le motivazioni, deve esistere e
appartenere alla row sorgente. Label, punti e stili copiati devono corrispondere
agli oggetti raw. Gli invarianti annidati verificano asse/mapping/residual,
endpoint/path, label selezionata fra le candidate, stati e rollover, senza
rifare la ricerca geometrica o introdurre regole ferroviarie. Le incoerenze
producono lo stesso errore storage controllato. La verifica è locale: usa set
per l'appartenenza, ricerca binaria degli anchor e ordinamento per la mediana
del supporto. Non confronta ogni riferimento con l'intera pagina e non duplica
il raw.

Il campo additivo `page_type` riporta la classificazione M2. Le vecchie cache
0.1, persistite esclusivamente per DUTY_DIAGRAM, restano leggibili anche senza
questo campo e con support refs non canonicalizzati, senza riscrittura o
ricalcolo. Schema e versione dell'algoritmo restano invariati perché mapping,
tolleranze e criteri di rilevamento non cambiano.

Per analizzare tutte le pagine di un job M2 locale già presente:

```sh
python scripts/inspect_temporal.py --data-dir data --job-id UUID_DEL_JOB
```

Il comando usa le copie per pagina, senza rileggere o copiare il PDF, e stampa
statistiche, residual e riferimenti agli outlier. È opzionale e indipendente
dalla suite CI. Output e cache devono restare nelle directory ignorate.
Le misure locali M2/M3A vengono mantenute separate nei probe QA riservati.

`duty_pages_analyzed` conta le pagine DUTY_DIAGRAM effettivamente analizzate,
anche senza row; `duty_pages_without_rows` evidenzia queste ultime. `duty_pages`
rimane un alias compatibile del primo contatore. Le pagine di altre classi
non vengono conteggiate come diagrammi per la sola assenza di righe.

Limiti deliberati: inferenza geometrica di layout supportati, precisione della
geometria PDF, soli segmenti rettilinei prevalentemente orizzontali, label
sovrapposte o spostate lasciate AMBIGUOUS, nessun collegamento segmento/treno,
nessuna interpretazione degli accessori o estensione tramite header.

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
python -m compileall -q src tests scripts
python -m pip check
python -m pip wheel --no-deps --wheel-dir dist .
git diff --check
git diff --stat
git status --short --branch
```

La suite comprende inoltre trasformazioni con CropBox, raw round-trip, primitive
line/curve/rect/quad, tutti i page type, varianti e ambiguità, provenance,
scrittura raw fallita e precedente preservato, inspector, escaping, symlink,
indici e limiti scala/pixel.

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
