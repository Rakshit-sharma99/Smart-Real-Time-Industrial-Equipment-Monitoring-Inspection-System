# EquipWatch → RestaurantSpy: audit and migration plan

Branch: `restaurantspy` (the EquipWatch version stays on `main`).

## 1. Audit of EquipWatch (before the change)

| App | What it owned | Verdict |
|---|---|---|
| `config` | settings, URLs, ASGI (ProtocolTypeRouter + AuthMiddlewareStack + AllowedHostsOriginValidator), `api_urls.py`, superuser-only admin site | **Keep**, update app list, tokens, throttling |
| `core` | layout, partials, permission mixins, navigation, context processor, PWA, error pages, `seed_demo`, dashboard | **Keep infrastructure**, rewrite roles/navigation/dashboard/seed |
| `accounts` | `UserProfile.role` (ADMIN/INSPECTOR), login/logout, user management | **Rework**: `StaffProfile` with 3 roles + restaurant link, login rate limiting |
| `equipment` | machines, thresholds, metrics, health, QR labels, `/e/<uuid>/` | **Replace** with `tables` (restaurant, sections, tables, status audit, QR) |
| `monitoring` | sensor readings, alert engine, simulator, ingest API, WebSocket consumer, charts | **Remove** sensors/alerts/simulator. The token-auth ingest pattern becomes the camera **detection API**; the consumer/broadcast becomes `core.realtime` |
| `inspections` | checklist templates, responses, photos, defects, scanner | **Replace** with table inspections (fixed questions), photos, camera detections. Photo processing and scanner page are reused |
| `maintenance` | tasks, one transition table, history | **Replace** with `tasks` (waiter tasks, same transition-table design) |
| `reports` | ReportLab PDFs with numbered canvas | **Keep the PDF machinery**, new analytics + reports |
| static/templates | design tokens, live.js (WS + polling), charts, scanner | Keep the patterns, rebrand, rewrite pages |

EquipWatch-only concepts removed entirely: equipment, thresholds, sensor readings, metrics, alerts, simulator, defects, maintenance tasks, health reports.

## 2. New architecture

| App | Models | Responsibility |
|---|---|---|
| `accounts` | `StaffProfile` | role (MANAGER / WAITER / RECEPTIONIST), restaurant link, staff management, login |
| `tables` | `Restaurant`, `RestaurantSection`, `RestaurantTable`, `TableStatusHistory` | table condition, the single state-change service, floor view, QR codes, audit log |
| `inspections` | `TableInspection`, `TableInspectionPhoto` | manual inspections (QR), task-completion checks, camera detection API |
| `tasks` | `WaiterTask`, `WaiterTaskHistory` | cleaning tasks, transition table, waiter dashboard |
| `reservations` | `Reservation`, `ReservationHistory` | time-based availability, conflict-free booking, reception desk |
| `notifications` | `Notification` | in-app notifications pushed in real time |
| `reports` | – | operational analytics + PDF export |
| `core` | – | layout, permissions, navigation, real-time bus (`core.realtime`, consumer), manager dashboard, PWA, seed |

### Table state (no redundant status column)
A table stores only facts: `is_occupied`, `cleanliness` (clean / dishes on table / needs cleaning), `is_blocked`, `is_active`.
The displayed status is computed, in this priority order:

`Blocked` → `Occupied` → `Cleaning in progress` (a task is in progress) → `Vacant – needs clearing` → `Cleaning required` → `Reserved` (a booking holds the table now) → `Ready`.

Every change goes through `tables.services.change_table_state()` (or `log_status_change()` for task/reservation-driven changes), which locks the row, writes a `TableStatusHistory` row and publishes a real-time event after commit.

### Real-time
WebSocket `ws/live/` (session auth). Groups: `restaurant-<id>` (table events), `restaurant-<id>-<role>` (task events → managers, reservation events → managers + receptionists), `user-<id>` (own tasks, notifications). Messages are small "something changed" events with an id (for de-duplication) and topics; pages then re-fetch server-rendered fragments that are permission-checked like any page. The same fragments are polled every 10 s when the socket is down.

## 3. Migration plan

1. Remove `equipment`, `monitoring`, `maintenance` and the old `inspections` code and migrations.
2. New apps with fresh `0001_initial` migrations (the EquipWatch schema has no production data; the old dev database was backed up outside the repo).
3. Rebuild modules in this order, running tests after each: foundation (roles, branding, real-time bus, notifications) → tables → inspections → tasks → reservations → manager dashboard + reports → seed data, README, full browser walkthrough.

## 4. Decisions made during the build

- **Status is computed, never stored.** `RestaurantTable` keeps facts only; `compute_status()` is the single rule, and `live_tables()` annotates the two time-dependent inputs (task in progress, booking hold) in one query.
- **Reserved is time-based.** A table shows Reserved only inside the hold window (`reservation_hold_minutes` before start, until end). Availability for a slot is checked against overlapping active bookings, so future bookings never block the table now.
- **Cameras cannot make a table Ready.** Detections set occupancy and dishes; "dishes gone" moves a table to Cleaning required. A person confirms readiness (inspection or task check).
- **Task completion = table check.** The waiter answers the same seven questions; the resulting inspection decides the table state. "Completed" never forces Ready.
- **Waiter inspection scope**: own tables, unassigned tables, and tables of their open tasks. Receptionists never inspect.
- **Waiters never see customer names/phones**: reservation details are shown only to managers and receptionists, and real-time events carry no customer data (clients re-fetch permission-checked fragments).
- **Real-time**: one WebSocket per user joined to restaurant / role / user groups; events are small and de-duplicated by id; polling fallback every 10 s; a 60 s refresh while live keeps time-based states (Reserved, overdue) current.
- **Overdue checks without a scheduler**: run at most once a minute per restaurant when a dashboard refreshes (cache lock), plus the `check_overdue_tasks` command for cron.
- **Login lock-out**: 5 failures per username+IP → 15 minutes (process-local cache).
- **Form prefixes** (`override-`, `block-`, `walkin-`) keep field ids unique when several forms share a page.
- **Multi-restaurant ready**: every model hangs off `Restaurant`; `request.restaurant` (middleware) scopes every query. The UI manages one restaurant per staff member; superusers without a restaurant see the first one.
- **Database file** is `restaurantspy.sqlite3` so the `main` branch's EquipWatch database is untouched. Fresh `0001_initial` migrations (no production data existed).
- **Demo seed** writes a week of history directly (timestamps in the past) and builds "today" through the real services, so the live floor is consistent with the rules.

## 5. Acceptance run (2026-11-09)

Server `runserver 127.0.0.1:8765` (port 8000 was held by another process on the build machine). Three headless Edge sessions: `manager_demo` (1366 px), `waiter3_demo` (375 px), `reception_demo` (1366 px).

| # | Item | Result |
|---|------|--------|
| 1 | Manager creates a table (T21) | PASS |
| 2 | Waiter inspects an authorized table (T12) | PASS |
| 3 | Inspection updates the table (Vacant – needs clearing) | PASS |
| 4 | Manager and receptionist floors update live, manager notified | PASS |
| 5 | Manager assigns a cleaning task (waiter pre-filled) | PASS |
| 6 | Waiter receives the task on the phone, with notification | PASS |
| 7 | Waiter starts (table shows Cleaning in progress live) and completes with dishes left | PASS (table stays "needs clearing") |
| 8 | Table becomes Ready only after a clean check | PASS |
| 9 | Receptionist sees T12 Ready live, with notification | PASS |
| 10 | Receptionist seats a walk-in at T12; manager sees Occupied live | PASS |
| 11 | Booking created; overlapping slot hidden; API refuses with 409 | PASS |
| 12 | Reservation visible to the manager | PASS |
| 13 | Cancellation frees the slot | PASS |
| 14 | Unauthorized actions refused (7 pages 403, override 403, detection API 401) | PASS |
| 15 | Polling fallback updates within 10 s; WebSocket reconnects | PASS |
| 16 | 15 audit rows and 10 inspections remain after deactivation | PASS |

`python manage.py test`: 62 tests, OK. Page smoke test: 72 checks over 3 roles × 2 widths, all pass.
