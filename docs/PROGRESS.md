# Build progress

Spec: "Smart Real-Time Industrial Equipment Monitoring & Inspection System" (EquipWatch).
Each phase ends with the gate: `check`, `makemigrations --check --dry-run`, `migrate`, `test`, browser smoke test (both roles, 375 px width).

| # | Phase | Status |
|---|-------|--------|
| 1 | Setup, settings, base layout, auth, roles, error pages, seed users | Done |
| 2 | Equipment CRUD, thresholds, QR | Done |
| 3 | Readings, alerts, process_reading, ingest API, simulator | Done |
| 4 | Dashboard, live monitoring, WebSockets, polling fallback | Done |
| 5 | Alerts UI | Done |
| 6 | Inspections, photos, defects | Done |
| 7 | QR scanner page | Done |
| 8 | Maintenance tasks | Done |
| 9 | PDF reports | Done |
| 10 | PWA, polish, README, acceptance walkthrough | Done |

## Next step
All 10 phases are done and the section 12 walkthrough passes. Optional extras (spec section 14, in order):
rule-based risk score, CSV export of readings/alerts, z-score "statistical" anomaly flag, preventive-maintenance reminders.

## Acceptance walkthrough (section 12) - 2026-10-29

Run in headless Edge against `runserver` + `simulate_sensors` (port 8765, see DECISIONS.md).

| # | Item | Result |
|---|------|--------|
| 1 | Start the server and the simulator (VS Code tasks validated; same commands run) | PASS |
| 2 | Log in as Admin, then as Inspector | PASS |
| 3 | Create equipment (EQ-777) | PASS |
| 4 | Edit its thresholds (invalid pair rejected, valid saved) | PASS |
| 5 | Download and print its QR label | PASS |
| 6 | Readings arrive live on the dashboard (WebSocket, no reload) | PASS |
| 7 | FORCE_WARNING then FORCE_CRITICAL: one alert, escalated | PASS |
| 8 | Acknowledge (Inspector), resolve with notes (Admin); inspector resolve = 403 | PASS |
| 9 | Scan the printed label image -> right machine page | PASS |
| 10 | Ad-hoc inspection with a photo submitted -> FAIL + HIGH defect | PASS |
| 11 | Defect -> task -> assigned -> in progress -> completed; defect closed; health recomputed | PASS |
| 12 | Inspection PDF and health report PDF downloaded | PASS |
| 13 | Inspector blocked from admin URLs (403) and another inspector's report/page (404) | PASS |
| 14 | Phone-width (375 px) use: no horizontal scroll, no JS errors | PASS |
| 15 | `python manage.py test` | PASS (196 tests) |

## How to resume
1. `.venv\Scripts\activate`
2. `python manage.py migrate` then `python manage.py seed_demo`
3. Read this file and `docs/DECISIONS.md`, continue with the next phase.
