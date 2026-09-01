/**
 * Two-pane layout: draggable gutter + per-pane collapse + persistence.
 *
 * The .two-pane container is a CSS grid with three tracks
 * (reading-pane, gutter, action-pane). User actions adjust those
 * tracks via CSS variables (--left-fr, --right-fr) or via
 * .left-collapsed / .right-collapsed classes that override the
 * template columns. Whatever the user picks is persisted to
 * localStorage so it survives refreshes and re-opens of the same
 * region.
 *
 * Layout key is taken from data-layout-key on the .two-pane container,
 * defaulted to "region". A single key is used across all regions on
 * purpose — your preferred layout is a personal preference, not a
 * per-region setting.
 */
(function () {
  "use strict";

  const STORE_PREFIX = "prep:layout:";

  function storeKey(layoutKey, suffix) {
    return STORE_PREFIX + layoutKey + ":" + suffix;
  }

  function readStored(layoutKey) {
    return {
      leftFr: localStorage.getItem(storeKey(layoutKey, "leftFr")),
      collapsed: localStorage.getItem(storeKey(layoutKey, "collapsed")),
    };
  }

  function applyWidth(container, leftFrRaw) {
    if (!leftFrRaw) return;
    const leftFr = parseFloat(leftFrRaw);
    if (!isFinite(leftFr) || leftFr <= 0 || leftFr >= 1) return;
    // CSS variables must carry the "fr" unit. <flex> inside calc() can't
    // be derived from a custom property in Chrome, so we attach the unit
    // here instead of letting the stylesheet do it.
    container.style.setProperty("--left-fr", leftFr + "fr");
    container.style.setProperty("--right-fr", 1 - leftFr + "fr");
  }

  function applyCollapsed(container, which) {
    container.classList.remove("left-collapsed", "right-collapsed");
    if (which === "left" || which === "right") {
      container.classList.add(which + "-collapsed");
    }
  }

  function setupGutterDrag(container, gutter, layoutKey) {
    let dragging = false;
    let containerRect = null;
    let gutterWidth = 0;

    function start(clientX) {
      if (
        container.classList.contains("left-collapsed") ||
        container.classList.contains("right-collapsed")
      ) {
        return;
      }
      dragging = true;
      containerRect = container.getBoundingClientRect();
      gutterWidth = gutter.offsetWidth;
      container.classList.add("is-dragging");
      gutter.classList.add("is-dragging");
    }

    function move(clientX) {
      if (!dragging) return;
      const offset = clientX - containerRect.left;
      const usable = containerRect.width - gutterWidth;
      // Clamp so neither pane can drag below ~15% of available width.
      const leftWidth = Math.max(usable * 0.15, Math.min(usable * 0.85, offset));
      const leftFr = leftWidth / usable;
      container.style.setProperty("--left-fr", leftFr + "fr");
      container.style.setProperty("--right-fr", 1 - leftFr + "fr");
    }

    function end() {
      if (!dragging) return;
      dragging = false;
      container.classList.remove("is-dragging");
      gutter.classList.remove("is-dragging");
      const raw = container.style.getPropertyValue("--left-fr").trim();
      // Store as a bare number so the localStorage value is portable; the
      // "fr" unit is reattached on the next page load by applyWidth.
      const leftFr = parseFloat(raw);
      if (isFinite(leftFr) && leftFr > 0 && leftFr < 1) {
        localStorage.setItem(storeKey(layoutKey, "leftFr"), String(leftFr));
      }
    }

    gutter.addEventListener("mousedown", (e) => {
      start(e.clientX);
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => move(e.clientX));
    document.addEventListener("mouseup", end);

    // Touch parity for trackpads / tablets.
    gutter.addEventListener("touchstart", (e) => {
      if (e.touches.length === 1) start(e.touches[0].clientX);
    });
    document.addEventListener("touchmove", (e) => {
      if (dragging && e.touches.length === 1) move(e.touches[0].clientX);
    });
    document.addEventListener("touchend", end);
  }

  function setupCollapseControls(container, layoutKey) {
    container.querySelectorAll("[data-collapse]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const which = btn.dataset.collapse;
        applyCollapsed(container, which);
        localStorage.setItem(storeKey(layoutKey, "collapsed"), which);
      });
    });
    container.querySelectorAll("[data-reopen]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        applyCollapsed(container, null);
        localStorage.removeItem(storeKey(layoutKey, "collapsed"));
      });
    });
  }

  function setupResetLink(container, layoutKey) {
    const link = document.querySelector("[data-reset-layout]");
    if (!link) return;
    link.addEventListener("click", (e) => {
      e.preventDefault();
      localStorage.removeItem(storeKey(layoutKey, "leftFr"));
      localStorage.removeItem(storeKey(layoutKey, "collapsed"));
      container.style.removeProperty("--left-fr");
      container.style.removeProperty("--right-fr");
      applyCollapsed(container, null);
    });
  }

  function init() {
    const container = document.querySelector(".two-pane");
    if (!container) return;
    const layoutKey = container.dataset.layoutKey || "region";
    const gutter = container.querySelector("[data-gutter]");

    const stored = readStored(layoutKey);
    applyWidth(container, stored.leftFr);
    applyCollapsed(container, stored.collapsed);

    if (gutter) setupGutterDrag(container, gutter, layoutKey);
    setupCollapseControls(container, layoutKey);
    setupResetLink(container, layoutKey);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
