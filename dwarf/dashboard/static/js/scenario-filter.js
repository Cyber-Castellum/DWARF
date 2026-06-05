/* Slice-3 client-side family filter for /operate/scenarios.
 * Reads data-family on rows and pills, toggles row.hidden, updates
 * the count caption. No URL state. Refresh resets.
 */
const pills = document.querySelectorAll(".pill");
const rows  = document.querySelectorAll(".scenario-row");
const count = document.querySelector(".filter-count");
const total = rows.length;
const active = new Set();  // empty == "all"

function render() {
  let visible = 0;
  rows.forEach((row) => {
    const family = row.getAttribute("data-family");
    const show = active.size === 0 || active.has(family);
    row.hidden = !show;
    if (show) visible += 1;
  });
  count.textContent = active.size === 0
    ? `${total} scenarios`
    : `showing ${visible} of ${total}`;
  pills.forEach((pill) => {
    const fam = pill.getAttribute("data-family");
    const pressed = fam === "" ? active.size === 0 : active.has(fam);
    pill.setAttribute("aria-pressed", String(pressed));
  });
}

pills.forEach((pill) => {
  pill.addEventListener("click", () => {
    const fam = pill.getAttribute("data-family");
    if (fam === "") {
      active.clear();
    } else if (active.has(fam)) {
      active.delete(fam);
    } else {
      active.add(fam);
    }
    render();
  });
});

render();
