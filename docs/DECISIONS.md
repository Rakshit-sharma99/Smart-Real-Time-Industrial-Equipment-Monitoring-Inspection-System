# Decisions log

One line per decision that the build spec did not cover. Newest at the bottom.

- **Project folder**: built in the open VS Code workspace `R:\Django project` (empty), not the spec's placeholder path `C:\Users\...\Projects\equipment-monitor` (that path didn't exist and section 0 had been left unfilled).
- **Section 0 defaults**: Python 3.13.0, git yes (one commit per phase), checkpoints continuous, app name EquipWatch, college/team names left blank (set `COLLEGE_NAME` / `TEAM_NAMES` in `.env`).
- **Time zone**: `TIME_ZONE` is read from `.env` (default `UTC`); this machine's `.env` uses `Asia/Kolkata` (Windows reported India Standard Time).
- **Global login requirement**: Django 5.1's `LoginRequiredMiddleware` is enabled as a safety net; public views (login, ingest API, PWA files) are explicitly marked `@login_not_required`. Role checks are still done per view.
- **Django admin = superusers only**: a custom `AdminSite.has_permission` (config/apps.py) so the `is_staff` flag alone never grants `/admin/`.
- **CSRF cookie HttpOnly**: JavaScript reads the CSRF token from a `<meta name="csrf-token">` tag instead of the cookie.
- **Tests**: `manage.py test` switches to the MD5 password hasher and ERROR-level logging (settings `TESTING` flag) to keep the suite fast and readable. The running app is unaffected.
- **Logging**: plain `FileHandler` (no rotation) for `logs/app.log`, because rotating a file held open by two processes (server + simulator) fails on Windows.
- **Settings landing page**: `/settings/` sends admins to the users page (later the simulator page) and inspectors to their profile; settings sub-pages share a tab bar.
- **Admin self-protection**: an admin cannot deactivate their own account or remove their own Admin role (prevents lock-out).
- **Demo user names** are fictional placeholders (Asha Admin, Ivan Inspector, Isha Inspector).
- **Sim modes affect one metric**: a machine's sim mode drives its *primary* metric (by type: compressor/pump -> pressure, CNC/conveyor -> vibration, boiler/cooling tower/other -> temperature); the other two metrics stay NORMAL. One mode change = one clear alert in the demo.
- **Readings are server-timestamped**: the ingest API does not accept `recorded_at`; `process_reading()` uses the server clock. Source is always SIMULATED for the simulator token (DEVICE is reserved).
- **Ingest response**: 201 with `{"accepted", "rejected"}` when at least one reading is stored; 400 with the standard error shape when none are.
- **Measurements must be >= 0** (all three metrics), consistent with the upper-limit-only simplification.
- **Simulator "last reading received"** comes from `SimulatorSettings.last_ingest_at`, updated by the ingest view (backfilled seed readings do not count).
- **Seed backfill** is generated in NORMAL mode and written directly (no alerts); one resolved historical alert is added for EQ-003 so alert history is not empty. `--reset` deletes all equipment (cascading readings/alerts) and the demo users.
- **Alert on_delete**: readings and alerts cascade with equipment (equipment is never hard-deleted in the UI; only superusers can delete in /admin/).
- **Live updates on every page**: `static/js/live.js` is loaded globally, so the top-bar dot is real everywhere and new-alert toasts appear on any page. Pages opt in to live DOM updates with data attributes (`.js-live-status`, `[data-live-value]`, `[data-live-time]`, `[data-kpi]`).
- **Polling fallback**: when the socket closes, live.js polls `/api/v1/equipment/status/` + `/api/v1/dashboard/summary/` (+ page chart pollers) every 10 s and retries the socket with backoff (2 s -> 30 s). `?transport=poll` forces polling for demos; `EquipWatch.live.close()` drops the socket from DevTools.
- **WebSocket summary is user-agnostic**: broadcast KPI counts are equipment health + open alerts only. Per-user counts (inspections, tasks) come from page load and the summary API.
- **Offline in the browser**: a machine badge turns OFFLINE client-side when its last reading is older than `OFFLINE_AFTER_SECONDS`; KPI counts are refreshed from the API every 30 s while live.
- **Charts use a category x-axis** (formatted times) so no Chart.js date adapter is needed; gaps in data are not drawn to scale.
- **Measurements are rounded to 2 decimals** in `process_reading()` so stored, broadcast and API values match.
- **Alerts list defaults to open alerts** (ACTIVE + ACKNOWLEDGED); "All statuses" shows history. The same filter form drives `GET /api/v1/alerts/`.
- **Alert API conflicts return 409** (e.g. acknowledging an alert that is not ACTIVE, resolving a resolved alert); missing resolution notes return 400.
- **Inline acknowledge** buttons on the alerts list return to the same filtered page (`next` is checked with `url_has_allowed_host_and_scheme`).
- **Sidebar alert badge** shows the open-alert count and updates live from the WebSocket summary.
- **Browser 404 checks**: with DEBUG=True Django shows its technical 404 page, so the custom 404 template is verified by the test suite (which runs with DEBUG=False), not in the dev browser.
