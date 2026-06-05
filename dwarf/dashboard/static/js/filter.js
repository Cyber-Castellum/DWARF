/* Slice-14 shared client-side filter for single-select / table / per-row attribute pages.
 *
 * Used by:
 *   /operate/runs    (axis: status)
 *   /operate/targets (axis: implementation)
 *   /operate/bundles (axis: status)
 *
 * NOT used by /operate/scenarios — that page is multi-select over a <ul>
 * list (not a <table>) and uses row.hidden mechanics. It keeps its own
 * scenario-filter.js. Don't try to unify; the primitives diverge.
 *
 * Convention: pages render
 *   <table class="X-table" data-active-{axis}="">
 *     <tr data-{axis}="<value>">...</tr>
 *   <button class="pill" data-{axis}="<value>" aria-pressed="false">...</button>
 *   <p class="filter-count">N {label}</p>
 *
 * Single-select. Clicking a pill sets data-active-{axis} on the table
 * (CSS hides non-matching rows). Clicking the "all" pill (empty data-{axis})
 * clears the active filter. No URL state. Refresh resets.
 */
export function initFilter({table, axis, label}) {
  const tableEl = document.querySelector(table);
  if (!tableEl) return;
  const pills = document.querySelectorAll(".filter-pills .pill");
  const rows  = document.querySelectorAll(`${table} tbody tr`);
  const count = document.querySelector(".filter-count");
  const total = rows.length;
  const activeAttr = `data-active-${axis}`;
  const valueAttr = `data-${axis}`;

  function render(activeValue) {
    tableEl.setAttribute(activeAttr, activeValue);
    let visible = 0;
    rows.forEach((row) => {
      const v = row.getAttribute(valueAttr) || "";
      if (activeValue === "" || v === activeValue) visible += 1;
    });
    count.textContent = activeValue === ""
      ? `${total} ${label}`
      : `showing ${visible} of ${total}`;
    pills.forEach((pill) => {
      const slug = pill.getAttribute(valueAttr);
      pill.setAttribute("aria-pressed", String(slug === activeValue));
    });
  }

  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      const slug = pill.getAttribute(valueAttr) || "";
      render(slug);
    });
  });
}
