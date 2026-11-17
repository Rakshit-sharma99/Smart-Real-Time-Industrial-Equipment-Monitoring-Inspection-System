# Project Work Status & Checklist

## Feature: AI Real-Time CCTV Table, Shape, Capacity & Automated State Monitoring

### Objective
Enhance the RestaurantSpy CCTV & monitoring system to:
1. Automatically detect tables and generate boundary polygons via AI (eliminating manual polygon drawing).
2. Classify table geometry (rectangular vs. circular).
3. Map nearby chairs to tables to dynamically calculate per-table seating capacity and total restaurant room capacity.
4. Automatically reflect real-time status:
   - Customer seated $\rightarrow$ **Occupied** (blocks table from reservations, displays assigned waiter).
   - Leftovers/dishes & no customer $\rightarrow$ **Clean the table** (needs clearing, notifies assigned waiter).
   - Completely clear/blank table $\rightarrow$ **Vacant / Ready** (automatically updated without manual staff sign-off).
5. Display live room capacity, per-table capacity, shapes, and waiter info on the Manager CCTV console and floor dashboards in real-time.

---

### Checkpoints Checklist

- [x] **Checkpoint 1: Setup Tracking & AI Dependencies**
  - [x] Create and maintain `workstatus.md`.
  - [x] Add `ultralytics` to `requirements.txt` and install in `.venv` (with lightweight model support and offline fallback).
  - [x] Git commit for Checkpoint 1.

- [ ] **Checkpoint 2: Vision Model & Instance Segmentation Engine**
  - [ ] Implement instance segmentation detector in `cctv/detectors.py` supporting `dining table`, `chair`, `person`, and tableware (`cup`, `bottle`, `bowl`, `fork`, `knife`, `spoon`).
  - [ ] Extract polygon coordinates from table segmentation masks.
  - [ ] Add mock/test fixture capability so tests run deterministically without GPU or internet.
  - [ ] Git commit for Checkpoint 2.

- [ ] **Checkpoint 3: Geometry & Capacity Analysis Engine**
  - [ ] Implement table shape classification (Rectangular vs Circular) via circularity index ($4\pi A / P^2$) and vertex approximation (`approxPolyDP`).
  - [ ] Implement chair-to-table spatial proximity mapping (buffer zones).
  - [ ] Calculate per-table seating capacity and aggregate total room capacity.
  - [ ] Write unit tests in `cctv/tests.py` verifying shape detection, chair assignment, and capacity calculations.
  - [ ] Git commit for Checkpoint 3.

- [ ] **Checkpoint 4: Automated State Transition Engine**
  - [ ] Update state machine in `cctv/analysis.py`, `tables/services.py`, and `inspections/services.py` to allow AI camera detections to transition tables directly to `Ready / Vacant` when completely clear.
  - [ ] Ensure `Occupied` state blocks reservation booking and associates assigned waiter name.
  - [ ] Ensure `Clean the table` state alerts the assigned waiter with a cleaning task.
  - [ ] Write unit tests verifying end-to-end automated state transitions.
  - [ ] Git commit for Checkpoint 4.

- [ ] **Checkpoint 5: Auto-Floor Calibration API & Camera Worker Integration**
  - [ ] Create API endpoint / command for "Auto-Detect Floor Layout & Tables" from a camera frame.
  - [ ] Update `CameraRunner` in `cctv/management/commands/run_cameras.py` to stream detected shapes, chair counts, room capacity, and live states.
  - [ ] Git commit for Checkpoint 5.

- [ ] **Checkpoint 6: UI & Real-Time Dashboard Enhancements**
  - [ ] Update Live CCTV console (`templates/cctv/console.html`, `static/js/cctv.js`):
    - Live overlays with shape indicator, seat count, status badge, and assigned waiter name.
    - Top summary bar: Total Capacity, Occupied Seats, Available Seats, Total Tables.
  - [ ] Update Floor View and Booking Desk to display dynamic table capacity and shape icons.
  - [ ] Git commit for Checkpoint 6.

- [ ] **Checkpoint 7: Full Verification & Documentation**
  - [ ] Run full test suite (`manage.py test`).
  - [ ] Update `README.md` and `workstatus.md` with final documentation and usage guide.
  - [ ] Final Git commit.
