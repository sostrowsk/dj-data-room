# dj-data-room

Django app package `data_room` (app label, import path, DB tabellen bleiben
`data_room`). Host-Projekte pinnen dieses Repo als Poetry-git-Dependency auf
`main` — jeder Push auf main ist sofort releasebar.

## TDD-Regeln (Pflicht)

- **Test zuerst, RED bestaetigen, dann implementieren, GREEN bestaetigen.**
- Bugfix = Regressionstest, der den Bug reproduziert und VOR dem Fix failt.
- Reine Moves: Import-Smoke-Tests.
- Tests laufen aus dem Host-Projekt: `pytest --pyargs data_room.tests`
  (das Package hat keine eigene Settings-/pytest-Infrastruktur; einige Tests
  nutzen Host-Factories/-Apps und setzen den leasing-Host voraus).
- LLM-/Netzwerk-Calls IMMER mocken — kein Test darf echte Provider-APIs
  treffen.

## Architektur-Regeln

- Keine Imports aus Host-Apps (users, project, leasing, ai_agents, pages,
  history, ...). Host-Models NUR lazy ueber `data_room/conf.py`
  (`DATA_ROOM_PROJECT_MODEL`, `DATA_ROOM_CLIENT_COMPANY_MODEL`), Host-Tasks
  und -Services NUR ueber `data_room/hooks.py` (dotted-path Settings),
  Permissions NUR ueber `data_room/policies.py`
  (`DATA_ROOM_PERMISSION_POLICY`). Der AST-Waechter
  `data_room/tests/test_packaging_guards.py` erzwingt das.
- Peer-Apps `scribe`, `progress` und `ai_router` duerfen direkt importiert
  werden — System-Check-gesichert (`data_room.E001`-`E003` in
  `data_room/apps.py`), aber NICHT in pyproject deklariert (nur der Host
  pinnt dj-* Packages).
- **Migrations-Byte-Stabilitaet:** Aenderungen duerfen keine neuen
  Migrationen im Host erzeugen (`makemigrations --check --dry-run` muss im
  Host clean bleiben). Modul-Level-Settings-FKs nicht "dynamisieren".
- Permission-Logik nie "verbessern" — die Host-Policy (leasing) ist die
  Paritaets-Referenz; `DefaultPolicy` bleibt bewusst minimal (staff/author).
- Settings-Katalog im README aktuell halten, wenn neue Settings dazukommen.
