// Click-to-copy for [data-copy-value] cells. Used by /operate/status
// configuration table; reusable for any future copy-row.

const CONFIRM_MS = 1100;

function announceCopied(button) {
  const original = button.querySelector(".copy-cell__hint");
  if (!original) return;
  const prior = original.textContent;
  original.textContent = "copied";
  button.dataset.copied = "1";
  setTimeout(() => {
    original.textContent = prior;
    delete button.dataset.copied;
  }, CONFIRM_MS);
}

async function copyValue(button) {
  const value = button.dataset.copyValue;
  if (!value) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
    } else {
      // Fallback for non-secure contexts (http://): synthesize a hidden
      // textarea + execCommand. Best-effort.
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    announceCopied(button);
  } catch (err) {
    button.dataset.copyError = String(err);
  }
}

document.querySelectorAll(".copy-cell").forEach((button) => {
  button.addEventListener("click", () => copyValue(button));
});
