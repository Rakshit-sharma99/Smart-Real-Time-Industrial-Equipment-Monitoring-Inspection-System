# RestaurantSpy

**Live CCTV-based restaurant table monitoring.** A Django web app that watches the dining room through its cameras, knows which tables are occupied, vacant or waiting to be cleaned, sends waiters to clean them, and keeps the booking desk in sync with the floor in real time.

A customer leaves Table 12. A waiter (or a camera service) records that the table is empty but still has dishes on it, so it shows **Vacant – needs clearing** on every screen at once. The manager assigns a cleaning task; the waiter sees it on their phone, starts it (the table shows **Cleaning in progress**), clears and cleans, and confirms the table check. Only when the check says the table is clear, clean, arranged and ready does it become **Ready**, and at that moment the receptionist at the entrance sees Table 12 free and can seat the next guests or book it.

> **What the camera can and cannot do.** The video worker reads real streams (RTSP/IP cameras, webcams, or a demo video file) with OpenCV and runs a detector on every sample. Out of the box that is OpenCV's built-in HOG **people** detector, so cameras decide **occupancy**; dishes are detected only if you install a dish-capable model (`pip install ultralytics`, COCO tableware classes). No camera ever marks a table *Ready*: a waiter or manager confirms that. Everything the console shows is labelled LIVE, DEMO VIDEO, DISCONNECTED or "AI detection: not running"; nothing is faked.

This repository's `main` branch holds the previous product (EquipWatch, an equipment-monitoring system). RestaurantSpy was built from it on the `restaurantspy` branch; `docs/TRANSFORMATION.md` records the audit and what was reused.

| | |
|---|---|
| Stack | Python 3.11–3.13, Django 5.2 LTS, Django REST Framework, Channels 4 + Daphne 4, SQLite (WAL), ReportLab, Pillow, qrcode |
| Front end | Server-rendered Django templates, Bootstrap 5.3, Bootstrap Icons, Chart.js 4, html5-qrcode 2.3.8 (CDN, pinned, with SRI) |
| Runs on | Windows from VS Code, with no paid services, Redis, Docker or Node |

---

## 1. Setup

Prerequisites: Python 3.11–3.13 (`py --version`), VS Code with the Python extension, internet access for the CDN files.

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py seed_demo
```

Edit `.env`: set `SECRET_KEY` (generate with `python -c "import secrets; print(secrets.token_urlsafe(50))"`), `DETECTION_API_TOKEN` (any secret), and `SITE_BASE_URL` / `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` with your LAN IP if phones will use the app. The database file is `restaurantspy.sqlite3` (so it never collides with the `main` branch's `db.sqlite3`).

Optional: `python manage.py createsuperuser` for `/admin/` (superusers only).

## 2. Running

```powershell
python manage.py runserver        # or the VS Code task "Run server"
```

Open <http://127.0.0.1:8000>. Ctrl+C stops it. The server handles pages, the API and WebSockets (Daphne).

For Live CCTV, run the **video worker** in a second terminal (VS Code task "Run camera worker"):

```powershell
python manage.py run_cameras            # all active cameras; --camera ID for one; --once to test
```

It never runs inside a web request: it reads the cameras, runs the detector every few seconds, smooths the result over time and posts frames + results to the server. Stop it with Ctrl+C; while it is stopped the console says DISCONNECTED and no table changes.

### Demo accounts (`python manage.py seed_demo`, DEBUG only; `--reset` wipes and recreates)

Restaurant **The Copper Fork**: 3 sections, 20 tables, a live floor, today's bookings, a week of history and one camera ("Main hall" = your first webcam, device 0, with zones for T1-T4; change the source under Settings > Cameras & AI). Password for all: `DEMO_PASSWORD` from `.env` (default `RestaurantSpy@2026`).

| Account | Role | Starts on |
|---|---|---|
| `manager_demo` | Manager | Manager dashboard |
| `waiter_demo`, `waiter2_demo`, `waiter3_demo`, `waiter4_demo` | Waiters (each responsible for 5 tables; T11–T15 belong to `waiter3_demo`) | My tasks |
| `reception_demo`, `reception2_demo` | Receptionists | Booking desk |

## 3. The three roles

Each role has its own short menu. Manager: **Overview, Live CCTV, Tables, Waiter Tasks, Reservations, Staff, Settings**. Waiter: **My tasks, Scan table QR, Settings**. Receptionist: **Booking desk, Reservations, Settings**. Table checks, QR codes and history live on each table's page; reports are a link on the Overview; notifications are the bell.

| Action | Manager | Waiter | Receptionist |
|---|---|---|---|
| Floor view, table details, notifications, own profile | ✓ | ✓ | ✓ |
| Create / edit / deactivate / block tables, override a table's state (audited), assign responsible waiters | ✓ | ✗ | ✗ |
| Inspect a table | any table | own tables, unassigned tables, tables of own tasks | ✗ |
| Create, assign, reassign, re-prioritise waiter tasks | ✓ | ✗ | ✗ |
| Accept / start / progress-note / complete tasks | ✓ | own tasks | ✗ |
| See customer names and phone numbers | ✓ | ✗ | ✓ |
| Booking desk, create / update / cancel reservations, seat walk-ins | ✓ | ✗ | ✓ |
| Staff management, restaurant settings, sections, reports and PDFs | ✓ | ✗ | ✗ |
| QR codes: view / download / print | ✓ / ✓ / ✓ | view | view |

Every rule is enforced on the server (role checks plus restaurant scoping on every query). Hidden buttons are a convenience, not the protection: a waiter opening `/tasks/` gets **403**, another restaurant's table gives **404**.

## 4. Table state

A table stores only facts: `is_occupied`, `cleanliness` (clean / dishes on table / needs cleaning), `is_blocked`, `is_active`. The displayed status is computed, in this priority order:

`Blocked` → `Occupied` → `Cleaning in progress` (a task is in progress) → `Vacant – needs clearing` → `Cleaning required` → `Reserved` (a booking holds the table now) → `Ready`

A vacant table with dishes on it is never Ready. Every change goes through `tables.services`, which locks the row, writes a `TableStatusHistory` audit row, sends notifications and publishes a real-time event after the transaction commits.

**Inspections** answer seven questions (customers seated, plates, glasses, leftovers, surface clean, chairs arranged, ready for next). Contradictions are rejected (a table cannot be "ready" with plates on it). Completing a waiter task records the same check, so the table becomes Ready only when the waiter confirms it really is.

**Camera detections** (`POST /api/v1/detections/`, token auth) can report people and dishes. They are stored always and applied only above `DETECTION_MIN_CONFIDENCE` (0.6). A camera can never declare a table clean: dishes gone moves the table to *Cleaning required*, and a person confirms Ready. Try it with the server running:

```powershell
python manage.py send_test_detection --table 4 --people 0 --dishes 1
```

**Reservations** occupy a time slot `[start, end)` on one table (default 90 minutes). Two active bookings of the same table can never overlap; the check and the insert run in one locked transaction, and the API answers **409** on a conflict. A future booking does not block the table now: it shows as *Reserved* only during the hold window (30 minutes before the start, until the end). Statuses: Pending → Confirmed → Arrived → Seated → Completed, plus Cancelled and No show. Seating needs a clean table; "Guests left" marks it for clearing. An inspection that finds the table empty also completes its seated booking.

**Waiter tasks** (`WT-0001`…): Open → Assigned → In progress → Completed, with accept, progress notes, reassignment and priority changes; every action is logged in `WaiterTaskHistory`. Deadlines default to the restaurant's cleaning deadline; overdue tasks notify managers and the waiter once (checked when dashboards refresh, or via `python manage.py check_overdue_tasks` from a scheduler).

## 5. Live CCTV

**Settings > Cameras & AI** holds the cameras: name, source (`rtsp://user:pass@host/stream`, an `http(s)://` URL, a webcam index such as `0`, or a video file path = **demo**), detection interval, minimum confidence and how many consecutive samples must agree before a table changes. Credentials are stored on the server and shown masked everywhere.

**Live CCTV** (managers) shows the latest frame at about one frame per second with every table zone drawn over it and labelled `T12 - OCCUPIED`, `T2 - FREE`, `T5 - NEEDS CLEARING`, `T7 - CLEANING`, plus the people (and dishes, if detectable) counted in each zone. Camera picker, pause, full screen, last-frame time, detector name, and a clear status: LIVE, DEMO VIDEO, DISCONNECTED or not started.

**Table zones**: on the camera's picture, pick a table, click the corners of its area (3+ points), *Finish zone*, *Save*. Zones are stored per camera in normalised coordinates, so they survive restarts and resolution changes.

**How a detection becomes a table status** (`cctv/analysis.py`, `cctv/services.py`, `inspections/services.py`):

1. The worker grabs a frame and runs the detector; each detection's anchor point (hips for a person, centre for tableware) is matched to the zone it falls in.
2. A zone's observation (occupied? dishes?) must repeat for `frames_to_confirm` samples before it is published, so people walking past, a brief occlusion or one bad frame change nothing.
3. A confirmed change is recorded as a **camera check** on the table (source CAMERA, with confidence and camera name) and applied through the same state rule as a staff check: occupied/vacant, and *dishes remaining* becomes *cleaning required* when the dishes disappear. Never *clean*, never *Ready*.
4. If the stream drops, the worker reports DISCONNECTED and tables keep their last known state until a person or a reconnected camera says otherwise. Managers can always override, with a reason, and the audit log records the source of every change (camera, staff check, task, booking, override).

## 6. Real-time synchronisation

Pages mark the parts that must stay current as *live fragments*. A WebSocket (`ws/live/`, session-authenticated, scoped to the user's restaurant, role and person) delivers small events such as `{"type": "table.changed", "topics": ["tables"]}`; the browser then re-fetches those fragments from ordinary permission-checked views, so the socket never carries customer data to someone who may not see it. Events carry an id, so duplicates are dropped, and an older response never overwrites a newer one.

If the socket fails, the fragments are polled every 10 s and the top bar shows **Live updates: polling**; the socket is retried with back-off. Test it with DevTools → Network → WS, or add `?transport=poll` to any URL, or run `RestaurantSpy.live.close()` in the console.

## 7. QR codes

Every table has a QR code encoding `{SITE_BASE_URL}/t/<public uuid>/`. Opening it requires a staff login of the same restaurant: staff who may inspect go straight to the inspection form, others to the table page. Holding a QR code grants nothing. Managers download single PNGs or print a sheet (**Tables & QR codes**). The scanner page (**Scan table QR**) uses the camera, a photo of the code, or the typed table number.

Camera notes: the live camera works on `http://localhost` and `https://`. On a phone using the PC's LAN address (plain http), browsers block the camera; use "Scan from photo" or type the table number.

## 8. Reports

**Reports** (managers) shows a 1–31 day period: parties and guests seated, walk-ins, cancellations, no-shows, average reservation waiting time, cleaning tasks, average cleaning and response time, late tasks, inspections, per-waiter statistics, a daily summary with weekly totals, peak occupancy per hour and inspections by source. Each figure is tagged **Measured** (timed or counted from recorded events) or **Estimate** (table utilisation, with its assumption printed). PDF export: the operations report and a per-table history (inspections, audit log, tasks, bookings).

## 9. API (`/api/v1/`)

| Method | Path | Who |
|---|---|---|
| POST | `detections/` | any camera/vision service: one table result (`Authorization: Token <DETECTION_API_TOKEN>`, 120/min) |
| POST | `cameras/<id>/frame/` and `cameras/<id>/status/` | the video worker: latest frame + zone results, or a lost stream |
| GET | `floor/` | staff |
| GET | `tables/lookup/?q=T12` | staff |
| GET | `reservations/availability/?start_at=…&guests=4` | manager, receptionist |
| POST | `reservations/` | manager, receptionist (30/min, 409 on conflict) |
| GET | `notifications/` · POST `notifications/read/` | staff |

Browser calls use the session cookie plus the CSRF token. Errors are always `{"error": "...", "details": {...}}`.

## 10. Security

Session auth (HttpOnly cookies, 8 h), CSRF on every form and API POST, login lock-out after 5 failed attempts per username and IP (15 minutes), role checks and restaurant scoping on every view and WebSocket, object-level checks (own tasks, inspectable tables), input validation in forms and services, photo uploads validated with Pillow (JPEG/PNG/WebP, 5 MB, EXIF stripped, UUID file names, served only through a permission-checked view), audit logs for tables, tasks and reservations, locked transactions for booking and task completion, DRF throttling on the detection and booking endpoints, custom 403/404/500 pages, logs in `logs/app.log`.

## 11. Testing

```powershell
python manage.py test          # 80 tests
```

CCTV (`cctv/tests.py`): zone matching, temporal smoothing, the detector interface on a real frame, the worker API (token, JPEG validation, confirmed changes to table status, offline never changes tables), zone editor validation, camera settings, manager-only console. Business rules (`tables/tests_state.py`): status priority, inspections, camera detections, task workflow and permissions, reservation conflicts, time-based availability, hold window, cancellation, seating rules. Pages and APIs (`core/tests.py`): role matrix for all three roles, login lock-out, the complete Table 12 story through the real pages, overrides and audit log, history after deactivation, booking and double booking, customer-data isolation, detection token, floor and lookup APIs, CSRF, WebSocket groups (anonymous rejected; events reach the right restaurant and roles), seed command, PWA files.

The 16 acceptance items from the specification were also run in three live browsers (manager, waiter, receptionist) against the dev server; all pass. The script is `docs/acceptance_walkthrough.py` (needs Playwright and Edge; run with the server on port 8765 after `seed_demo --reset`).

## 12. Troubleshooting

| Problem | Fix |
|---|---|
| PowerShell blocks `activate` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call `.venv\Scripts\python.exe manage.py …` directly (the VS Code tasks do). |
| Port 8000 in use | `netstat -ano \| findstr :8000`, close the owner, or `python manage.py runserver 8001` (add the origin to `CSRF_TRUSTED_ORIGINS`). |
| "database is locked" | WAL mode with a 20 s timeout is on; close DB browser tools and run a single server. |
| Live dot stays amber ("polling") | `daphne` must be first in `INSTALLED_APPS` (it is); check the terminal says *Starting ASGI/Daphne*; check the host is in `ALLOWED_HOSTS`. |
| Camera blocked | Allow it in the site settings, close other apps using it, or scan from a photo / type the table number. |
| Live CCTV says "Waiting for the first frame" | Start `python manage.py run_cameras`; check its terminal for "Cannot open the video source" (wrong URL or index, or the camera is used by another app). |
| Console says DISCONNECTED | The worker stopped or the stream dropped; tables are unchanged. Fix the source and restart the worker. |
| Zero people detected although someone sits at the table | The built-in HOG detector needs the whole body roughly upright and well lit; a top-down or far camera may miss seated people. Lower *minimum confidence*, move the camera, or install `ultralytics` for a stronger model. |
| Detection client says token rejected | Server and client read the same `.env`; restart the server after editing it. |

## 13. Production notes

`DEBUG=False` with a real `SECRET_KEY` (the app refuses to start without one), HTTPS with secure cookies and HSTS, PostgreSQL, the Redis channel layer (`channels-redis`) for several server processes, `collectstatic` behind Nginx/WhiteNoise, private object storage for photos, a scheduler for `check_overdue_tasks`, run the video worker as a service (one per camera group) with a GPU-capable detector, and keep camera credentials in a secrets store.

## 14. Layout

```
config/          settings, urls, api_urls, asgi, wsgi
core/            layout, permissions, middleware (request.restaurant), realtime bus + consumer, manager dashboard, PWA, seed_demo
accounts/        StaffProfile (role, restaurant), login with lock-out, staff management
tables/          Restaurant, sections, tables, status rule + audit log, floor views, QR codes
inspections/     table inspections, photos, detection API, scanner page
tasks/           waiter tasks, transition table, history, waiter dashboard
reservations/    bookings, availability, booking desk, APIs
notifications/   in-app notifications
cctv/            cameras, table zones, detector layer, video worker (run_cameras), live console, zone editor
reports/         analytics (measured vs estimate) and PDFs
templates/       base layout, partials/, one folder per app   static/  css, js, icons
docs/            TRANSFORMATION.md (audit, plan, decisions, acceptance run)
```
