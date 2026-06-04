/**
 * Pop the context view into a separate browser window.
 *
 * Used by the dual-monitor workflow: engineer clicks "Pop context",
 * a new window opens with just the hunk + locked hypothesis + model's
 * reading, and they drag it to their second monitor. Reusing the same
 * window name per region means the SAME context window is reused when
 * the engineer clicks the button again — it won't pile up extras.
 *
 * Context is static after reveal so this popup needs no live updates.
 */
(function () {
  "use strict";

  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-pop-url]");
    if (!btn) return;
    e.preventDefault();
    const url = btn.dataset.popUrl;
    const name = btn.dataset.popName || "prep-context";
    // popup=yes opens a chromeless window the user can position freely.
    const features = "popup=yes,width=720,height=900,scrollbars=yes";
    const w = window.open(url, name, features);
    if (w) w.focus();
  });
})();
