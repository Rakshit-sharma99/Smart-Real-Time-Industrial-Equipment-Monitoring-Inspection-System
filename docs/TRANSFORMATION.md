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
