# dj-data-room

Protected document "data room" for Django projects. Ships project- and
client-scoped document models with protected file storage (served outside
`MEDIA_ROOT` via nginx internal redirects), OTP-gated upload/preview/download
views (HTMX partials), watermarked PDF page previews, project ZIP export with
progress tracking, an indexing pipeline orchestrator (OCR markdown +
RAG chunk indexing via the `scribe` peer), client-entity curation flows and
a pluggable permission-policy framework.

Python package name: **`data_room`** (the repo name `dj-data-room` is only
the distribution name — app label, import path, DB tables and migrations
stay `data_room`).

## Installation

Installed by the host project as a Poetry git dependency (single lock
authority lives in the host):

```toml
[tool.poetry.dependencies]
dj-data-room = { git = "ssh://git@github.com/sostrowsk/dj-data-room.git", branch = "main" }
```

```python
INSTALLED_APPS = [
    ...
    "easy_thumbnails",
    "django_otp",            # views use otp_required
    "ai_router",             # peer, see below
    "progress",              # peer, see below
    "scribe",                # peer, see below
    "data_room.apps.DataRoomConfig",
]
```

```python
# host urls.py
path("data-room/", include("data_room.urls")),   # namespace "data_room"
```

The app ships models, migrations, views/urls, admin, templates (HTMX
partials), templatetags (`protected_document_tags`, `project_zip_tags`),
Celery tasks and management commands (`index_all_documents`,
`reset_vector_index`, `count_chunks`, `reextract_markdown`,
`bulk_upload_documents`). No static files.

## Peer requirements

data_room imports these Django apps at runtime but does **not** declare them
in `pyproject.toml` (the host pins all dj-* packages — single lock
authority). A Django system check (`data_room.E001`-`E003`) fails fast when
a peer is missing from `INSTALLED_APPS`:

| Peer app | Package | Used for |
| --- | --- | --- |
| `scribe` | dj-rag-db | `SCRIBE` facade + `DocumentChunk` (indexing pipeline, chunk counting, chunk debug view) |
| `progress` | dj-progress | pipeline/ZIP progress tracking (`TaskProgress`, websocket updates) |
| `ai_router` | dj-ai-router | `get_llm_client` + `llm_log` in the LLM client matcher (`services/client_matcher.py`) |

## Host contract

### Models (duck-typed, resolved via settings)

- **Project model** (`DATA_ROOM_PROJECT_MODEL`, default `project.Project`):
  FK target of both document models, `ProjectZip` and `ProjectCompanyLink`.
  Duck-typed attribute use: `pk`, `name`, `check_permissions(user)`
  (optional — `DefaultPolicy.can_access_project` falls back to staff-only
  without it), reverse accessors created by this package.
- **Client company model** (`DATA_ROOM_CLIENT_COMPANY_MODEL`, default
  `users.ClientCompany`): FK target of `ProtectedClientDocument` /
  `ProjectCompanyLink`; duck-typed use: `pk`, `company` (display name),
  `group` (optional holding/group FK used by the leasing policy only).
- **User**: standard `settings.AUTH_USER_MODEL` FKs (`user`, `author`).
  `data_room/models/choices.py` carries a frozen copy of the leasing
  `user_type` choices for the `author_type` snapshot field.

Both model settings are read at **model-definition time** for the FK
strings — see "Migrations note" below before overriding them.

### Permission policy

All view/queryset gates go through `data_room.policies.get_policy()`
(cached; resolved from `DATA_ROOM_PERMISSION_POLICY`). Subclass
`BaseDataRoomPolicy` (or `DefaultPolicy` = staff/superuser everything,
author may view+manage own documents):

`can_view_project_document`, `can_view_client_document`,
`can_access_project`, `can_manage_project_document`,
`can_manage_project_documents`, `can_manage_client_document`,
`can_download_original`, `can_curate_clients(user, document=None)`,
`filter_project_document_buckets(user, project, buckets, restrict_drafts=True)`,
`can_view_company_documents`, `get_author_scope(user, documents)`.

The leasing host configures
`DATA_ROOM_PERMISSION_POLICY = "leasing.policies.data_room.LeasingDataRoomPolicy"`
(role/holding-aware rules). `Protected*Document.check_permissions(user)`
remains as a delegation shim for duck-typed callers (ai_chat, decorators).

### Hooks (dotted-path settings, resolved call-time via `data_room/hooks.py`)

Explicit `None` disables a feature silently; an unresolvable path logs a
warning and disables it. Defaults point at the **leasing host** module
`ai_agents` — foreign hosts must set these explicitly (or to `None`):

| Setting | Default | Contract |
| --- | --- | --- |
| `DATA_ROOM_EXTRACTION_TASK` | `ai_agents.tasks.extract_document_data.extract_document_data_task` | Celery task; `.apply(args=[doc_id], kwargs={"user_id", "document_model", "pipeline_progress_id", "progress_range"})` (pipeline) and `.delay(doc_id, force=True)` (admin re-extract). `None` => pipeline uses the OCR markdown fallback, admin re-extract is a noop. |
| `DATA_ROOM_CLIENT_EXTRACTION_TASK` | `ai_agents.tasks.extract_clients.extract_clients_for_document_task` | Celery task; `.delay(document_id=..., user_id=..., document_model=...)`. `None` => manual trigger view is a noop. |
| `DATA_ROOM_ACCOUNTING_FRAMEWORKS_PROVIDER` | `ai_agents.services.frameworks.registry.get_framework_codes` | callable returning accounting-framework codes for model choices; unresolvable => frozen fallback list (keeps migrations byte-stable). |
| `DATA_ROOM_REDIS_LOCK_FACTORY` | `None` (lockless) | factory returning a redis client with `get_lock(name, timeout=..., blocking_timeout=...)`; used by indexing tasks/commands. leasing: `leasing.redis_client.get_redis_client_from_env`. |

### Signals

- `data_room.signals.project_zip_downloaded` — sent on ZIP download with
  kwargs `user`, `project`. The leasing host connects a history receiver;
  hosts without a history feature simply don't connect.

### Templates

Package templates extend/include host templates and tag libraries:

- `base.html` and `base_container.html` must exist (host base layout).
- `_bootstrap4_form.html` (form rendering include).
- templatetag library `lucide_tags` (`{% icon "name" %}`) must be loadable
  (leasing: `my_forms` app, later dj-base-project).
- URL name `DATA_ROOM_PROJECT_DETAIL_URL` (default `project:detail`,
  reversible with `kwargs={"pk": ...}`) for redirects back to the host's
  project page; `data_room/project_zip_progress.html` additionally links
  `project:detail` literally.

### Celery

Tasks are autodiscovered (`data_room.tasks`). Recommended beat schedule
(leasing values):

| Task | Schedule |
| --- | --- |
| `data_room.tasks.index_document.process_pending_documents_task` | every minute |
| `data_room.tasks.index_document.detect_stuck_documents_task` | every 10 minutes |
| `data_room.tasks.index_document.count_indexed_chunks_task` | hourly |

### Protected media serving

Files are stored under `PROTECTED_MEDIA_ROOT` (outside `MEDIA_ROOT`) via
`data_room.storage.ProtectedFileField` and served by the host's nginx
`internal` location (`PROTECTED_MEDIA_LOCATION_PREFIX` + `X-Accel-Redirect`)
— in `DEBUG` Django streams the files itself.

## Settings catalog

### Required (no defaults — `django.conf.settings` attribute access)

| Setting | Used for |
| --- | --- |
| `AUTH_USER_MODEL` | `user`/`author` FKs |
| `PROTECTED_MEDIA_ROOT` | storage location for all protected files |
| `PROTECTED_MEDIA_URL` | base URL of the protected location (default leasing: `/protected/`) |
| `PROTECTED_MEDIA_SERVER` | `"nginx"` => `X-Accel-Redirect` responses, else direct streaming |
| `PROTECTED_MEDIA_LOCATION_PREFIX` | nginx internal location prefix (e.g. `/internal`) |
| `PROTECTED_MEDIA_AS_DOWNLOADS` | `Content-Disposition: attachment` toggle |
| `DEFAULT_MODEL_DATA_ROOM` | LLM model id for the client matcher (`ai_router.get_llm_client`) |
| `LOGIN_URL` | anonymous redirects (URL name or path, resolved via `resolve_url`) |

### Optional (read via `getattr`, defaults in parentheses)

| Setting | Default | Used for |
| --- | --- | --- |
| `DATA_ROOM_PERMISSION_POLICY` | `data_room.policies.DefaultPolicy` | permission policy class (dotted path) |
| `DATA_ROOM_PROJECT_MODEL` | `project.Project` | host project model (`app_label.Model`) |
| `DATA_ROOM_CLIENT_COMPANY_MODEL` | `users.ClientCompany` | host client-company model |
| `DATA_ROOM_PROJECT_DETAIL_URL` | `project:detail` | URL name for project-detail redirects |
| `DATA_ROOM_WATERMARK_FONT` | `None` (PIL default font) | TTF path for preview watermarks (Pillow >= 10.1 required for the sized default-font fallback) |
| `DATA_ROOM_EXTRACTION_TASK` | see hooks | LLM extraction task |
| `DATA_ROOM_CLIENT_EXTRACTION_TASK` | see hooks | client extraction task |
| `DATA_ROOM_ACCOUNTING_FRAMEWORKS_PROVIDER` | see hooks | accounting framework codes |
| `DATA_ROOM_REDIS_LOCK_FACTORY` | `None` (lockless) | distributed lock for indexing tasks |
| `CELERY_TASK_ALWAYS_EAGER` | `False` | eager-mode branch in the upload view |

## Migrations note (foreign hosts)

The shipped migrations pin leasing host labels: `0001_initial` depends on
the `project` app and `users.ClientCompany`, `0028` on further host state.
For the leasing host this is byte-identical to the pre-extraction state
(no new migrations, `makemigrations --check` stays clean).

Foreign hosts that override `DATA_ROOM_PROJECT_MODEL` /
`DATA_ROOM_CLIENT_COMPANY_MODEL` get different FK deconstructions and MUST
provide their own migration set, e.g.:

```python
MIGRATION_MODULES = {"data_room": "myhost.migrations_data_room"}
```

(then `makemigrations data_room` once in the host). The same applies to
hosts whose project/client-company apps are not literally named
`project`/`users`.

## System dependencies

- **poppler** (`pdftoppm`) — `pdf2image` page rendering for previews.
- **PyMuPDF** wheels bundle MuPDF; no extra system package needed.

## Tests

Tests live in the package and run from the host (no own settings/pytest
infrastructure):

```bash
pytest --pyargs data_room.tests
```

Note: the test suite uses leasing host factories/apps (`project`, `users`,
`ai_agents`, `pages`) and the packaging guard discovers host apps under the
host's `BASE_DIR` — it is a leasing-host suite by design (plan contract
"Tests laufen ueber leasing"). The pure-unit tests (policies, hooks, conf,
schemas, storage) also pass in foreign hosts.

## Development workflow (leasing host)

```bash
# in the host repo: use the local checkout instead of the git dep
poetry run pip install -e ../dj-data-room   # NOTE: poetry install reverts this

# release: commit + push here, then in the host
poetry update dj-data-room
```

Every push to `main` is immediately consumable by the host
(`branch = "main"` git dependency).
