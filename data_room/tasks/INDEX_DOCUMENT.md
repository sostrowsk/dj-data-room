# `index_document.py` — Dokument-Indexierung

Celery-Tasks und Indexer-Klasse fuer die Volltextindexierung von Dokumenten in Milvus (Vektordatenbank).
Wird nach dem Upload ausgeloest und macht Dokumente fuer AI-Suche, InfoMemo und Chat verfuegbar.

## Ueberblick

```mermaid
graph LR
    UPLOAD[Upload-View] -->|delay| IQD[index_queried_documents_task]
    CRON[Celery Beat] -->|periodisch| PPD[process_pending_documents_task]
    PPD -->|delay| IQD

    IQD -->|pro Dokument, gestaffelt| IDT[index_document_task]
    IDT --> DI[DocumentIndexer.process_document]

    CRON2[Celery Beat] -->|periodisch| DSD[detect_stuck_documents_task]
    CRON3[Celery Beat] -->|periodisch| CIC[count_indexed_chunks_task]
```

## Celery-Tasks

### `index_queried_documents_task` — Batch-Einstiegspunkt

Wird direkt vom Upload-View aufgerufen. Verarbeitet eine Liste von Dokument-IDs.

```mermaid
flowchart TD
    START([IDs + user_id + model_name]) --> LOCK{Redis-Lock<br/>erwerben?}

    LOCK -->|nein| RETRY[Retry mit<br/>exponentiellem Backoff]
    LOCK -->|Lock nicht verfuegbar| NOLOCK[Warnung, weiter ohne Lock]
    LOCK -->|ja| PENDING

    PENDING[Alle Dokumente auf<br/>status=pending setzen] --> LOOP[[Fuer jedes Dokument]]

    LOOP --> FILECHECK{Datei<br/>vorhanden?}
    FILECHECK -->|nein| FAIL1[Status → failed]
    FILECHECK -->|ja| QUEUE[index_document_task.apply_async<br/>countdown = n * 3s]

    QUEUE --> NEXT{Naechstes?}
    FAIL1 --> NEXT
    NEXT -->|ja| LOOP
    NEXT -->|nein| RELEASE[Lock freigeben]
```

**Staffelung**: Jeder Task wird um `n * 3 Sekunden` verzoegert, um das System nicht zu ueberlasten.

**Redis-Lock**: `indexing:all_documents` mit 1h Timeout und Auto-Renewal alle 30s. Verhindert parallele Batch-Laeufe.

### `index_document_task` — Einzeldokument-Task

Prueft Parallelitaet und delegiert an `DocumentIndexer`.

```mermaid
flowchart TD
    START([document_id + user_id]) --> CHECK{Mehr als 2<br/>Dokumente aktiv?}

    CHECK -->|ja, retries < 5| RETRY[Retry mit Backoff<br/>max 30 min + Jitter]
    CHECK -->|ja, retries >= 5| FAIL[Status → failed]
    CHECK -->|nein| PROCESS[DocumentIndexer.process_document]

    PROCESS -->|Erfolg| OK[status: success]
    PROCESS -->|Exception| ERR[status: error<br/>Fehler geloggt]
```

**Parallelitaetslimit**: Max. 2 Dokumente gleichzeitig in `processing/chunking/indexing` (beide Modelle zusammen).

### `process_pending_documents_task` — Geplanter Batch-Lauf

Periodischer Task (Celery Beat). Sucht alle Dokumente mit `indexing_status=pending` und uebergibt sie an `index_queried_documents_task`.

### `detect_stuck_documents_task` — Haengende Dokumente erkennen

Periodischer Task. Findet Dokumente, die zu lange in einem aktiven Status stecken (via `check_stuck_documents`), und loggt Details.

### `count_indexed_chunks_task` — Chunk-Zaehler aktualisieren

Zaehlt fuer alle Projekte und Dokumente die tatsaechlich in Milvus vorhandenen Chunks und aktualisiert `indexed_chunks` in der DB.

## `DocumentIndexer` — Kernklasse

### Statusuebergaenge

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> processing : initialize()
    processing --> chunking : _process_and_index()
    chunking --> indexing : Chunks erstellt
    indexing --> indexed : Erfolgreich
    indexed --> processing : Re-Indexierung

    processing --> failed : Fehler
    chunking --> failed : Fehler
    indexing --> failed : Fehler
    failed --> processing : Erneuter Versuch
```

Ungueltige Uebergaenge (z.B. `chunking → processing`) werden in `initialize()` abgefangen.

### Verarbeitungspipeline

```mermaid
flowchart TD
    subgraph initialize
        INIT[Dokument laden] --> STATUSCHECK{Status<br/>gueltig?}
        STATUSCHECK -->|processing/chunking/indexing| ABORT[Abbruch:<br/>bereits aktiv]
        STATUSCHECK -->|pending/indexed/failed| SETPROC[Status → processing]
    end

    subgraph process_document
        SETPROC --> PROGRESS[ProgressTracker erstellen]
        PROGRESS --> HEALTH

        subgraph "Schritt 1: Milvus-Verbindung"
            HEALTH[Health-Check<br/>max 3 Versuche] --> MILVUS{Gesund?}
            MILVUS -->|nein| HEALTHFAIL[RuntimeError]
            MILVUS -->|ja| VSINIT[Vector Store initialisieren<br/>max 3 Versuche + Jitter]
        end

        VSINIT -->|10%| STEP2

        subgraph "Schritt 2: Verarbeitung + Indexierung"
            STEP2[Status → chunking] -->|15%| PDF[scribe.process_pdf<br/>PDF → Chunks]
            PDF -->|50%| DOCLIST{Chunks<br/>vorhanden?}
            DOCLIST -->|nein| INDEXED1[Status → indexed]
            DOCLIST -->|ja| SETIDX[Status → indexing]
            SETIDX --> ASYNC[scribe.add_documents_to_collection<br/>async, batch_size=100]
            ASYNC -->|50-90%| INDEXED2[Status → indexed]
        end

        INDEXED2 -->|90%| STEP3

        subgraph "Schritt 3: Nachbearbeitung"
            STEP3[Token-Zaehlung<br/>tiktoken] -->|95%| CHAIN{Client-<br/>Dokument?}
            CHAIN -->|ja| EXTRACT[extract_clients_for_document_task<br/>via Celery]
            CHAIN -->|nein| DONE
            EXTRACT --> DONE([100% — Fertig])
        end
    end

    style ABORT fill:#f66
    style HEALTHFAIL fill:#f66
```

### Fortschrittsanzeige

| Prozent | Phase |
|---------|-------|
| 10% | Vector Store initialisiert |
| 15% | PDF-Verarbeitung gestartet |
| 50% | PDF verarbeitet und in Chunks zerlegt |
| 50–90% | Batch-Indexierung (linear innerhalb dieses Bereichs) |
| 90% | Indexierung abgeschlossen |
| 95% | Token-Zaehlung |
| 100% | Fertig |

### Retry- und Backoff-Strategie

| Ebene | Max. Versuche | Backoff | Jitter |
|-------|---------------|---------|--------|
| Milvus Health-Check | 3 | 2s → 4s → 8s (max 30s) | nein |
| Vector Store Init | 3 | 2s → 4s → 8s (max 30s) | ja (±10%) |
| `index_document_task` Parallelitaet | 5 | 60s → 120s → ... (max 30 min) | ja (±25%) |
| `index_queried_documents_task` Lock | 5 | 60s → 120s → 240s → ... | nein |

### Fehlerbehandlung

```mermaid
flowchart TD
    ERR[Exception] --> LOG[Fehler + Stacktrace loggen]
    LOG --> PROG{ProgressTracker<br/>vorhanden?}
    PROG -->|ja| PROGFAIL[Task als fehlgeschlagen markieren]
    PROG -->|nein| STATUS

    PROGFAIL --> STATUS{Dokument<br/>vorhanden?}
    STATUS -->|ja| ORM[update_document_status → failed]
    STATUS -->|nein| END

    ORM -->|Exception| EMERGENCY[_emergency_status_update<br/>direktes SQL-UPDATE]
    ORM -->|ok| END
    EMERGENCY --> END([Ende])
```

Drei Eskalationsstufen:
1. **ORM**: `update_document_status(doc, "failed")` — Standardweg
2. **Emergency SQL**: Direktes `UPDATE`-Statement als Fallback wenn ORM fehlschlaegt
3. **Logging**: Fehler wird immer protokolliert, auch wenn Status-Update scheitert

### Collection-Benennung

| Dokumenttyp | Milvus-Collection |
|------------|-------------------|
| `ProtectedProjectDocument` | `project_{project.id}` |
| `ProtectedClientDocument` | `client_{client.id}` |

### Client-Extraktion (nur ProtectedClientDocument)

Nach erfolgreicher Indexierung wird `extract_clients_for_document_task` als Celery-Task gestartet,
sofern `client_extraction_status` in `[None, "", "pending", "failed", "skipped"]` liegt.
Dies extrahiert Mandanteninformationen aus dem Dokumentinhalt.
