/**
 * Pop the context view into a separate browser window (dual-monitor mode).
 *
 * The engineer clicks "Pop context": a chromeless window opens with just the
 * hunk + locked hypothesis + model's reading (to drag to a second monitor),
 * AND the original window collapses its left reading-pane so the chat/action
 * pane takes the whole window. The point is hunk on one monitor, chat on the
 * other — not the hunk duplicated in both.
 *
 * The button is a toggle. Clicking it again (or closing the popup) brings the
 * context back into the main window. State is keyed per region and persisted
 * in sessionStorage so it survives the full-page reloads that happen mid-
 * session (engage / reconcile / dialogue-exit forms all reload the region);
 * navigating to a different region starts expanded. Reusing the same window
 * name per region means the popup is reused, never piled up.
 *
 * Context is static after reveal, so the popup needs no live updates.
 */
(function () {
  "use strict";

  const FEATURES = "popup=yes,width=720,height=900,scrollbars=yes";
  const POP_LABEL = "📋 Pop context to new window";
  const UNPOP_LABEL = "⤺ Bring context back";

  let popup = null;
  let watchTimer = null;

  function key(name) {
    return "prep-popped:" + name;
  }
  function pane() {
    return document.querySelector(".two-pane");
  }
  function collapse() {
    const p = pane();
    if (p) p.classList.add("context-popped");
  }
  function expand() {
    const p = pane();
    if (p) p.classList.remove("context-popped");
  }
  function setLabel(btn, popped) {
    btn.textContent = popped ? UNPOP_LABEL : POP_LABEL;
    btn.setAttribute("aria-pressed", popped ? "true" : "false");
  }
  function stopWatch() {
    if (watchTimer) {
      clearInterval(watchTimer);
      watchTimer = null;
    }
  }
  // If the engineer closes the popup by hand, bring the pane back here.
  function startWatch(btn, name) {
    stopWatch();
    watchTimer = setInterval(function () {
      if (!popup || popup.closed) {
        stopWatch();
        popup = null;
        expand();
        setLabel(btn, false);
        sessionStorage.removeItem(key(name));
      }
    }, 500);
  }

  function pop(btn, name) {
    popup = window.open(btn.dataset.popUrl, name, FEATURES);
    if (popup) popup.focus();
    collapse();
    setLabel(btn, true);
    sessionStorage.setItem(key(name), "1");
    startWatch(btn, name);
  }

  function unpop(btn, name) {
    stopWatch();
    // We may have lost the handle across a reload — re-acquire by name, then
    // close it (window.open with an empty url returns the existing named
    // window without navigating it).
    if (!popup || popup.closed) {
      try {
        popup = window.open("", name);
      } catch (e) {
        popup = null;
      }
    }
    if (popup && !popup.closed) popup.close();
    popup = null;
    expand();
    setLabel(btn, false);
    sessionStorage.removeItem(key(name));
  }

  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-pop-url]");
    if (!btn) return;
    e.preventDefault();
    const name = btn.dataset.popName || "prep-context";
    if (sessionStorage.getItem(key(name)) === "1") {
      unpop(btn, name);
    } else {
      pop(btn, name);
    }
  });

  // Re-apply the popped state after a full-page reload of the SAME region:
  // collapse the pane, re-summon the (reused) context window, and resume
  // watching so closing it still restores the pane. Runs after parse because
  // this script is deferred.
  document.addEventListener("DOMContentLoaded", function () {
    const btn = document.querySelector("[data-pop-url]");
    if (!btn) return;
    const name = btn.dataset.popName || "prep-context";
    if (sessionStorage.getItem(key(name)) !== "1") return;
    collapse();
    setLabel(btn, true);
    // Same name -> reuses/reloads the existing popup, no pile-up. Don't steal
    // focus back to it on every reload.
    popup = window.open(btn.dataset.popUrl, name, FEATURES);
    startWatch(btn, name);
  });
})();
