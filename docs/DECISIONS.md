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
