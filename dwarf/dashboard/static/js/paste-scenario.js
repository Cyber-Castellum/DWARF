// Paste-a-scenario tile on /operate/scenarios. POSTs raw YAML to
// /api/scenario/paste; surfaces validation result inline.

function getToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get("token") || "dwarf";
}

const textarea = document.querySelector("#paste-scenario-yaml");
const output = document.querySelector("[data-paste-output]");
const submit = document.querySelector("[data-paste-submit]");
const clear = document.querySelector("[data-paste-clear]");

function showOutput(report, ok) {
  if (!output) return;
  output.hidden = false;
  output.dataset.state = ok ? "ok" : "error";
  if (typeof report === "string") {
    output.textContent = report;
  } else {
    output.textContent = JSON.stringify(report, null, 2);
  }
}

async function submitPaste() {
  if (!textarea) return;
  const yaml = textarea.value;
  if (!yaml.trim()) {
    showOutput("Paste some YAML first.", false);
    return;
  }
  submit.disabled = true;
  try {
    const url = `/api/scenario/paste?token=${encodeURIComponent(getToken())}`;
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "text/yaml" },
      body: yaml,
    });
    let report;
    try { report = await response.json(); } catch { report = await response.text(); }
    showOutput(report, response.ok);
  } catch (err) {
    showOutput(`[error] ${String(err)}`, false);
  } finally {
    submit.disabled = false;
  }
}

if (submit) submit.addEventListener("click", submitPaste);
if (clear) clear.addEventListener("click", () => {
  if (textarea) textarea.value = "";
  if (output) { output.hidden = true; output.textContent = ""; }
});
