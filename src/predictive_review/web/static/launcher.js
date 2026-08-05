/**
 * Launcher commit-card body expand/collapse.
 *
 * Each commit card has a "▸ Show body" button that toggles the body's
 * `hidden` attribute. Uses event delegation so HTMX-loaded fragments
 * work too — the commit list is re-rendered when the engineer changes
 * the repo path and clicks Reload, and freshly-loaded cards have their
 * toggles wired without us having to re-bind.
 */
(function () {
  "use strict";

  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".commit-expand");
    if (!btn) return;

    const id = btn.getAttribute("aria-controls");
    const body = id ? document.getElementById(id) : null;
    if (!body) return;

    const expanded = btn.getAttribute("aria-expanded") === "true";
    if (expanded) {
      body.setAttribute("hidden", "");
      btn.setAttribute("aria-expanded", "false");
      btn.textContent = btn.dataset.collapseLabel || "▸ Show body";
    } else {
      body.removeAttribute("hidden");
      btn.setAttribute("aria-expanded", "true");
      btn.textContent = btn.dataset.expandLabel || "▾ Hide body";
    }
  });
})();
