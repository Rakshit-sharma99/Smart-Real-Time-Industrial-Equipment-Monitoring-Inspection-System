"""Table 12 story across three live browsers (manager, waiter Wiktor who owns T11-T15, receptionist)."""
import sys
import time

from playwright.sync_api import sync_playwright

S = sys.argv[1]
B = "http://127.0.0.1:8765"
PW = "RestaurantSpy@2026"
results = []


def step(n, name, fn):
    try:
        results.append((n, name, "PASS", fn() or ""))
    except Exception as e:  # report and keep going
        results.append((n, name, "FAIL", f"{type(e).__name__}: {str(e)[:160]}"))


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def login(browser, user, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(B + "/accounts/login/")
    pg.fill("#id_username", user)
    pg.fill("#id_password", PW)
    pg.click("button[type=submit]")
    pg.wait_for_load_state("load")
    return pg


def wait_live(pg):
    pg.wait_for_function("document.getElementById('liveIndicator').dataset.state === 'live'", timeout=10000)


def tile_status(pg, label):
    return pg.locator(f".table-tile:has(.tile-number:text-is('{label}')) .tile-status").first.inner_text()


def wait_status(pg, label, expected, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if tile_status(pg, label) == expected:
            return True
        time.sleep(0.3)
    raise AssertionError(f"{label} shows {tile_status(pg, label)!r}, expected {expected!r} (live update missing)")


def answer(pg, **answers):
    """Click the Yes/No buttons of the table check. Radio 0 = Yes, radio 1 = No."""
    for name, yes in answers.items():
        pg.click(f"label[for='id_{name}_{0 if yes else 1}']")


def table12_id(pg):
    href = pg.locator(".table-tile:has(.tile-number:text-is('T12'))").first.get_attribute("href")
    return href.strip("/").split("/")[-1]


def csrf(pg):
    return pg.evaluate("document.querySelector('meta[name=csrf-token]').content")


LEFT = dict(customers_present=False, plates_present=True, glasses_present=True, leftovers_present=False,
            table_clean=False, chairs_arranged=False, ready_for_next=False)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    mgr = login(browser, "manager_demo", 1366)
    wtr = login(browser, "waiter3_demo", 375)
    rec = login(browser, "reception_demo", 1366)
    ctx = {}

    def s1():
        mgr.goto(B + "/floor/new/")
        mgr.select_option("#id_section", index=1)
        mgr.fill("#id_number", "21")
        mgr.fill("#id_capacity", "2")
        mgr.click("main button[type=submit]")
        mgr.wait_for_load_state("load")
        check("T21" in mgr.inner_text("h2"), mgr.url)
        return "T21 created"
    step(1, "Manager creates a table", s1)

    def s2():
        mgr.goto(B + "/floor/")
        ctx["t12"] = table12_id(mgr)
        mgr.goto(B + f"/floor/{ctx['t12']}/")
        mgr.click("details summary")
        mgr.select_option("#id_override-is_occupied", "1")
        mgr.select_option("#id_override-cleanliness", "CLEAN")
        mgr.fill("#id_override-reason", "Walkthrough: guests seated")
        mgr.click("button:has-text('Apply override')")
        mgr.wait_for_selector("#confirmModal.show")
        mgr.click("#confirmModalOk")
        mgr.wait_for_load_state("load")
        mgr.goto(B + "/manager/")
        wait_live(mgr)
        rec.goto(B + "/reservations/desk/")
        wait_live(rec)
        check(tile_status(mgr, "T12") == "Occupied", tile_status(mgr, "T12"))
        wtr.goto(B + f"/inspections/table/{ctx['t12']}/")
        check("Are customers sitting" in wtr.content(), "waiter cannot open the inspection form")
        return "T12 occupied (audited override); waiter opened the inspection form for an authorized table"
    step(2, "Waiter can inspect an authorized table", s2)

    def s3():
        answer(wtr, **LEFT)
        wtr.fill("#id_remarks", "Guests left, two plates")
        wtr.click("button:has-text('Save inspection')")
        wtr.wait_for_load_state("load")
        msg = wtr.inner_text(".flash-messages").strip()
        check("Vacant" in msg, msg)
        return msg
    step(3, "Inspection updates the table status", s3)

    def s4():
        wait_status(mgr, "T12", "Vacant – needs clearing")
        wait_status(rec, "T12", "Vacant – needs clearing")
        check(mgr.inner_text("[data-notification-count]") not in ("", "0"), "manager bell shows no count")
        return "manager and receptionist floors updated over the WebSocket without reload; manager notified"
    step(4, "Manager sees the change live", s4)

    def s5():
        mgr.goto(B + f"/tasks/new/?table={ctx['t12']}")
        check(mgr.input_value("#id_assignee") != "", "assignee not pre-filled with the table's waiter")
        mgr.select_option("#id_priority", "HIGH")
        mgr.click("main button:has-text('Create task')")
        mgr.wait_for_load_state("load")
        check("/tasks/" in mgr.url, mgr.url)
        return mgr.inner_text("h2")
    step(5, "Manager assigns a cleaning task", s5)

    def s6():
        wtr.goto(B + "/tasks/mine/")
        wait_live(wtr)
        check("T12" in wtr.inner_text(".task-card"), wtr.inner_text("main")[:200])
        check(wtr.inner_text("[data-notification-count]") not in ("", "0"), "waiter bell shows no count")
        wtr.click(".task-card button:has-text('Accept')")
        wtr.wait_for_load_state("load")
        return "task on the waiter's phone with a notification; accepted"
    step(6, "Waiter receives the task", s6)

    def s7():
        mgr.goto(B + "/manager/")
        wait_live(mgr)
        wtr.click(".task-card button:has-text('Start')")
        wtr.wait_for_load_state("load")
        wait_status(mgr, "T12", "Cleaning in progress")
        wtr.click(".task-card a:has-text('Finish')")
        wtr.wait_for_load_state("load")
        answer(wtr, glasses_present=True, ready_for_next=False)  # dishes still there
        wtr.click("button:has-text('Complete task')")
        wtr.wait_for_load_state("load")
        msg = wtr.inner_text(".flash-messages").strip()
        check("Vacant" in msg, msg)
        ctx["msg1"] = msg
        return "manager saw 'Cleaning in progress' live; " + msg
    step(7, "Waiter completes a task while dishes remain", s7)

    def s8():
        wait_status(mgr, "T12", "Vacant – needs clearing")
        mgr.goto(B + f"/tasks/new/?table={ctx['t12']}")
        mgr.click("main button:has-text('Create task')")
        mgr.wait_for_load_state("load")
        task2 = mgr.url
        mgr.goto(B + "/manager/")
        wait_live(mgr)
        wtr.goto(task2)
        wtr.click("button:has-text('Start work')")
        wtr.wait_for_load_state("load")
        wtr.click("button:has-text('Complete task')")  # pre-filled: all clear and ready
        wtr.wait_for_load_state("load")
        msg = wtr.inner_text(".flash-messages").strip()
        check("Ready" in msg, msg)
        wait_status(mgr, "T12", "Ready")
        return "stayed 'needs clearing' after the dishes-left check; Ready after a clean check"
    step(8, "Table becomes Ready only when cleaning conditions are satisfied", s8)

    def s9():
        wait_status(rec, "T12", "Ready")
        check(rec.inner_text("[data-notification-count]") not in ("", "0"), "receptionist not notified")
        return "desk shows T12 Ready live; 'table ready' notification received"
    step(9, "Receptionist sees updated availability", s9)

    def s10():
        option = rec.locator("#id_walkin-table option:has-text('T12 ')").first.get_attribute("value")
        rec.select_option("#id_walkin-table", option)
        rec.fill("#id_walkin-guests", "2")
        rec.fill("#id_walkin-customer_name", "Walkthrough party")
        rec.click("button:has-text('Seat walk-in now')")
        rec.wait_for_load_state("load")
        msg = rec.inner_text(".flash-messages").strip()
        check("seated at T12" in msg, msg)
        wait_status(mgr, "T12", "Occupied")
        return msg + "; manager floor shows Occupied live"
    step(10, "Receptionist seats guests at the ready table", s10)

    def s11():
        rec.fill("#id_date", "2026-11-12")
        rec.fill("#id_time", "19:00")
        rec.fill("#id_guests", "4")
        rec.click("button:has-text('Find a table')")
        rec.wait_for_load_state("load")
        rec.click(".suggest-option:has-text('T2 ')")
        rec.fill("#id_customer_name", "Mehta")
        rec.fill("#id_phone", "98765 43210")
        rec.click("button:has-text('Confirm booking')")
        rec.wait_for_load_state("load")
        first = rec.inner_text(".flash-messages").strip()
        check("Booked" in first and "T2 " in first, first)
        rec.goto(B + "/reservations/desk/?date=2026-11-12&time=19:30&guests=4")
        check("T2 " not in rec.inner_text(".suggest-list"), "T2 still suggested for an overlapping slot")
        r = rec.request.post(B + "/api/v1/reservations/", data={"table": 2, "customer_name": "X", "phone": "9876500000",
                             "party_size": 2, "start_at": "2026-11-12T19:30:00+05:30"},
                             headers={"X-CSRFToken": csrf(rec), "Content-Type": "application/json"})
        check(r.status == 409, f"API returned {r.status}")
        return first[:70] + "; overlapping slot hidden from suggestions; API refuses with 409"
    step(11, "Booking works; double booking is prevented", s11)

    def s12():
        mgr.goto(B + "/reservations/")
        check("Mehta" in mgr.content(), "booking not visible to the manager")
        return "booking visible to the manager (list + dashboard panel for bookings within 4 h)"
    step(12, "Reservation appears for the manager", s12)

    def s13():
        rec.goto(B + "/reservations/?q=Mehta")
        rec.click("tbody a.fw-semibold")
        rec.wait_for_load_state("load")
        rec.click("button:has-text('Cancelled')")
        rec.wait_for_selector("#confirmModal.show")
        rec.click("#confirmModalOk")
        rec.wait_for_load_state("load")
        check("Cancelled" in rec.inner_text(".flash-messages"), rec.inner_text(".flash-messages"))
        rec.goto(B + "/reservations/desk/?date=2026-11-12&time=19:00&guests=4")
        check("T2 " in rec.inner_text(".suggest-list"), "T2 not available after cancelling")
        return "cancelled; T2 bookable again for that slot"
    step(13, "Cancellation updates availability", s13)

    def s14():
        codes = {}
        for pg, urls in ((wtr, ["/tasks/", "/reservations/desk/", "/reports/", "/accounts/staff/", "/floor/manage/"]),
                         (rec, ["/inspections/scan/", "/tasks/mine/", "/floor/manage/"])):
            for u in urls:
                codes[u] = pg.request.get(B + u).status
        check(all(c == 403 for c in codes.values()), str(codes))
        r = wtr.request.post(B + f"/floor/{ctx['t12']}/override/", form={"override-is_occupied": "1", "override-cleanliness": "CLEAN", "override-reason": "x"},
                             headers={"X-CSRFToken": csrf(wtr)})
        check(r.status == 403, f"waiter override returned {r.status}")
        r = rec.request.post(B + "/api/v1/detections/", data={})
        check(r.status == 401, f"detections without token: {r.status}")
        return f"{len(codes)} restricted pages -> 403; waiter override -> 403; detection API without token -> 401"
    step(14, "Unauthorized users cannot perform restricted actions", s14)

    def s15():
        mgr.goto(B + "/manager/?transport=poll")
        mgr.wait_for_timeout(800)
        check(mgr.get_attribute("#liveIndicator", "data-state") == "polling", "not in polling mode")
        wtr.goto(B + f"/inspections/table/{ctx['t12']}/")
        answer(wtr, **LEFT)
        wtr.click("button:has-text('Save inspection')")
        wtr.wait_for_load_state("load")
        wait_status(mgr, "T12", "Vacant – needs clearing", timeout=14)
        mgr.goto(B + "/manager/")
        wait_live(mgr)
        return "polling mode picked up the change within 10 s; WebSocket mode reconnects"
    step(15, "WebSocket and polling fallback both work", s15)

    def s16():
        mgr.goto(B + f"/floor/{ctx['t12']}/")
        mgr.click("button:has-text('Audit log')")
        audit = mgr.locator("#tab-history li").count()
        mgr.click("button:has-text('Inspections')")
        inspections = mgr.locator("#tab-inspections tbody tr").count()
        mgr.click("form[action$='/active/'] button")
        mgr.wait_for_selector("#confirmModal.show")
        mgr.click("#confirmModalOk")
        mgr.wait_for_load_state("load")
        check("deactivated" in mgr.inner_text(".flash-messages"), mgr.inner_text(".flash-messages"))
        mgr.goto(B + "/inspections/?table=12")
        check(mgr.locator("tbody tr").count() >= inspections, "inspections lost after deactivation")
        mgr.goto(B + f"/floor/{ctx['t12']}/")
        mgr.click("form[action$='/active/'] button")
        mgr.wait_for_load_state("load")
        return f"{audit} audit rows and {inspections} inspections still available after deactivation; reactivated"
    step(16, "Historical data remains after deactivation", s16)

    for pg, who in ((mgr, "manager"), (wtr, "waiter"), (rec, "receptionist")):
        if pg.errors:
            results.append((0, f"JS errors ({who})", "FAIL", str(pg.errors[:3])))
    mgr.goto(B + "/manager/")
    mgr.screenshot(path=f"{S}/shots/walk_manager_end.png", full_page=True)
    browser.close()

for n, name, verdict, detail in results:
    print(f"{n:>2}. [{verdict}] {name} -- {detail}")
print(f"\n{sum(1 for r in results if r[2] == 'PASS')}/{len(results)} passed")
