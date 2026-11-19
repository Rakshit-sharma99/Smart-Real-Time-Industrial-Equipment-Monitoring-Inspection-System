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

---

# Phase 2 (2026-11-09): simplification + live CCTV redesign

## Audit of the first RestaurantSpy version

| Found | Decision |
|---|---|
| 11 sidebar items for the manager (Dashboard, Booking desk, Floor, Waiter tasks, Reservations, Inspections, Scan table QR, Reports, Tables & QR codes, Staff, Notifications, Settings) | **7**: Overview, Live CCTV, Tables, Waiter Tasks, Reservations, Staff, Settings. Floor merges into Overview; checks and QR codes move into table details; Reports is a link on Overview; Notifications is the bell. |
| 9 KPI tiles on the dashboard | **4**: Occupied, Available, Needs cleaning, Reserved |
| "Latest inspections" panel on the dashboard | removed (history lives on the table page) |
| Floor filters (section/status/guests) on the dashboard | removed from Overview; kept on the booking desk where they matter |
| Waiter sidebar: My tasks, Floor, Inspections, Scan, Notifications, Settings | **3**: My tasks, Scan table QR, Settings |
| Receptionist sidebar: Booking desk, Floor, Reservations, Notifications, Settings | **3**: Booking desk, Reservations, Settings |
| Task list "waiter progress" card and 3 filters | kept the filters, dropped the progress card (reports cover it) |
| Reports page: 14 figures, chart, two PDFs | kept as a secondary page linked from Overview; no change to figures |
| "Inspection" wording everywhere | user-facing wording is **Table check**; code names unchanged |
| No camera feature at all; detections only via a bare API | new **cctv** app: cameras, table zones, background video worker, detector layer, live console, zone editor |

Backend kept unchanged: table state rule, tasks, reservations, notifications, audit logs, security.

## CCTV architecture

```
run_cameras (separate process)  --cv2.VideoCapture-->  RTSP / HTTP / file / webcam
   | every N seconds: grab a frame, run the detector, map detections to table zones,
   | temporal smoothing (a zone must agree for K samples before a change is published)
   |  POST /api/v1/cameras/<id>/frame/   (token)  JPEG + per-zone results
   v
Django: caches the frame (30 s) and the zone results, applies confirmed changes
        through inspections.services.record_detection(), publishes "tables" events
   v
Live CCTV console: refreshes the frame at 1 fps, draws the zones as an SVG overlay
        with each table's current status, shows LIVE / DEMO / DISCONNECTED / AI unavailable
```

Honesty rules built in: a camera that stops sending frames never changes any table; the
detector reports what it can detect (`person` with OpenCV's built-in HOG detector; dishes
only when a dish-capable model such as Ultralytics YOLO is installed); file sources are
always labelled DEMO; RTSP credentials are stored server-side and never sent to the browser.

## Phase 2 acceptance (2026-11-09)

Server on port 8765, worker `run_cameras --url http://127.0.0.1:8765`, camera "Main hall" = the PC's webcam
(device 0, 640x480, OpenCV HOG people detector). Headless Edge for the browser checks.

| # | Item | Result |
|---|------|--------|
| 1 | CCTV video connects | PASS - webcam opened by the worker; camera ONLINE after the first frame |
| 2 | Video appears in the manager dashboard | PASS - Live CCTV console shows the frame (640 px) refreshing at 1 fps; Overview shows a thumbnail |
| 3 | Table regions can be configured | PASS - zone editor: 4 seeded zones drawn; a 5th zone for T5 drawn by clicking and saved |
| 4 | Detections map to the correct table | PASS - unit test (`assign_to_zones`): person -> zone 1, tableware -> zone 2, outside -> none |
| 5 | Occupancy changes detected consistently | PASS (logic) - `ZoneTracker` publishes only after 3 agreeing samples; one passer-by frame changes nothing. Live: 0 people seen in the test room, so no live occupancy change could be shown |
| 6 | Vacant tables with dishes are not marked Ready | PASS - API test: dishes seen -> needs clearing, dishes gone -> cleaning required, never Ready |
| 7 | Manager receives table updates | PASS - WebSocket fragment refresh + notification (walkthrough 4) |
| 8 | Manager assigns a waiter | PASS - from the Attention list (quick assign) and the task form (walkthrough 5) |
| 9 | Waiter receives the task | PASS - phone board + notification (walkthrough 6) |
| 10 | Waiter completes cleaning | PASS (walkthrough 7-8) |
| 11 | Table readiness updated correctly | PASS - stays "needs clearing" with dishes left; Ready only after a clean check |
| 12 | Receptionist sees the new availability | PASS - live desk + "table ready" notification (walkthrough 9) |
| 13 | Receptionist reserves the table | PASS - walk-in seated; booking created; conflicts refused (walkthrough 10-11) |
| 14 | Manager sees the reservation immediately | PASS (walkthrough 12) |
| 15 | Camera disconnection does not mark tables vacant | PASS - worker stopped: console DISCONNECTED after the stale window, zero table changes (also a unit test) |
| 16 | Manual override works and is recorded | PASS - override with reason appears in the audit log (walkthrough 2 + 16) |

`python manage.py test`: 80 tests, OK. Smoke: 3 roles x 2 widths over the redesigned pages, all pass.

**Not claimed:** dish detection with the built-in detector (it detects people only; the UI says "dishes not detectable"),
and live person detection in this room (the webcam saw nobody during the run). RTSP was not tested against a
real IP camera here; OpenCV's `VideoCapture` handles RTSP URLs and the worker treats them like any source.
