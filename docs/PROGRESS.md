# Build progress

Spec: "Smart Real-Time Industrial Equipment Monitoring & Inspection System" (EquipWatch).
Each phase ends with the gate: `check`, `makemigrations --check --dry-run`, `migrate`, `test`, browser smoke test (both roles, 375 px width).

| # | Phase | Status |
|---|-------|--------|
| 1 | Setup, settings, base layout, auth, roles, error pages, seed users | Done |
| 2 | Equipment CRUD, thresholds, QR | Done |
| 3 | Readings, alerts, process_reading, ingest API, simulator | Done |
| 4 | Dashboard, live monitoring, WebSockets, polling fallback | Not started |
| 5 | Alerts UI | Not started |
| 6 | Inspections, photos, defects | Not started |
| 7 | QR scanner page | Not started |
| 8 | Maintenance tasks | Not started |
| 9 | PDF reports | Not started |
| 10 | PWA, polish, README, acceptance walkthrough | Not started |

## Next step
Finish phase 1 gate (browser smoke test), commit.

## How to resume
1. `.venv\Scripts\activate`
2. `python manage.py migrate` then `python manage.py seed_demo`
3. Read this file and `docs/DECISIONS.md`, continue with the next phase.
