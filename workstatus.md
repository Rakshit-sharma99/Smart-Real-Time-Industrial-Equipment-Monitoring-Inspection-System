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

- [x] **Checkpoint 2: Vision Model & Instance Segmentation Engine**
  - [x] Implement instance segmentation detector in `cctv/detectors.py` supporting `dining table`, `chair`, `person`, and tableware (`cup`, `bottle`, `bowl`, `fork`, `knife`, `spoon`).
  - [x] Extract polygon coordinates from table segmentation masks with simplification.
  - [x] Add structured `SceneDetections` and unit tests in `cctv/tests.py`.
  - [x] Git commit for Checkpoint 2.

- [x] **Checkpoint 3: Geometry & Capacity Analysis Engine**
  - [x] Implement table shape classification (Rectangular vs Circular) via circularity index ($4\pi A / P^2$) and vertex approximation.
  - [x] Implement chair-to-table spatial proximity mapping (buffer zones).
  - [x] Calculate per-table seating capacity and aggregate total room capacity.
  - [x] Write unit tests in `cctv/tests.py` verifying shape detection, chair assignment, and capacity calculations.
  - [x] Git commit for Checkpoint 3.

- [x] **Checkpoint 4: Automated State Transition Engine**
  - [x] Update state machine in `cctv/analysis.py`, `tables/services.py`, and `inspections/services.py` to allow AI camera detections to transition tables directly to `Ready / Vacant` when completely clear.
  - [x] Ensure `Occupied` state blocks reservation booking and associates assigned waiter name.
  - [x] Ensure `Clean the table` state alerts the assigned waiter with a cleaning task.
  - [x] Write unit tests verifying end-to-end automated state transitions.
  - [x] Git commit for Checkpoint 4.

- [x] **Checkpoint 5: Auto-Floor Calibration API & Camera Worker Integration**
  - [x] Create API endpoint and management command (`python manage.py auto_detect_tables`) for AI auto-detection of floor tables, shape, and capacity.
  - [x] Update `CameraRunner` in `cctv/management/commands/run_cameras.py` to stream detected shapes, chair counts, room capacity, and live states with auto-clean.
  - [x] Git commit for Checkpoint 5.

- [x] **Checkpoint 6: UI & Real-Time Dashboard Enhancements**
  - [x] Update Live CCTV console (`templates/cctv/console.html`, `static/js/cctv.js`):
    - Live overlays with shape indicator (circular vs rectangular), seat count, status badge, and assigned waiter name.
    - Top summary bar: Total Room Capacity, Occupied Seats, Available Seats, Total Tables.
    - One-click "Auto-Detect Tables via AI" button directly in the console and Zone Editor.
  - [x] Update Zone tables and floor views to reflect live capacity, shapes, and waiter info.
  - [x] Git commit for Checkpoint 6.

- [ ] **Checkpoint 7: Full Verification & Documentation**
  - [ ] Run full test suite (`manage.py test`).
  - [ ] Update `README.md` and `workstatus.md` with final documentation and usage guide.
  - [ ] Final Git commit.
