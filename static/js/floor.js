/* Floor screen: the state counts and section tabs filter the floor. The choice survives live refreshes. */
(function () {
  "use strict";

  const screen = document.getElementById("floorScreen");
  if (!screen) return;
  const KEY = "rs-floor-filter";
  let filter = { state: "", section: "" };
  try { filter = Object.assign(filter, JSON.parse(sessionStorage.getItem(KEY) || "{}")); } catch (e) { /* private mode */ }

  function save() {
    try { sessionStorage.setItem(KEY, JSON.stringify(filter)); } catch (e) { /* private mode */ }
  }

  function apply() {
    let shown = 0;
    screen.querySelectorAll(".table-tile").forEach(function (tile) {
      const visible = (!filter.state || tile.dataset.state === filter.state) &&
        (!filter.section || tile.dataset.section === filter.section);
      tile.hidden = !visible;
      if (visible) shown += 1;
    });
    screen.querySelectorAll("[data-filter-state]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.filterState === filter.state));
    });
    screen.querySelectorAll("[data-filter-section]").forEach(function (tab) {
      tab.setAttribute("aria-selected", String(tab.dataset.filterSection === filter.section));
    });
    const none = screen.querySelector(".floor-none");
    if (none) none.hidden = shown > 0;
  }

  screen.addEventListener("click", function (event) {
    const stateButton = event.target.closest("[data-filter-state]");
    const sectionTab = event.target.closest("[data-filter-section]");
    if (stateButton) {
      filter.state = filter.state === stateButton.dataset.filterState ? "" : stateButton.dataset.filterState;
    } else if (sectionTab) {
      filter.section = sectionTab.dataset.filterSection;
    } else if (event.target.closest("[data-filter-clear]")) {
      filter = { state: "", section: "" };
    } else {
      return;
    }
    save();
    apply();
  });
  screen.addEventListener("rs:updated", apply);
  apply();

  // A drawer marked data-auto-open (search results, a failed booking) opens on load.
  const drawer = document.querySelector(".offcanvas[data-auto-open]");
  if (drawer && window.bootstrap) window.bootstrap.Offcanvas.getOrCreateInstance(drawer).show();
})();
