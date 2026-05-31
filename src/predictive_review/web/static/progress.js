/**
 * Form-submit progress indicator.
 *
 * Any form that submits via the browser's native flow (i.e. not HTMX)
 * gets a spinning circle prepended to whichever button the user clicked
 * to submit. The button stays styled "in progress" until the navigation
 * completes and the new page replaces it.
 *
 * HTMX forms are skipped — the composer already shows "thinking…" via
 * htmx-indicator, and double-instrumenting it would compete visually.
 *
 * `pointer-events: none` (via the .is-working class) prevents double-
 * submits without using `disabled = true`, which would strip the
 * submitter button's name/value from the form data — important for our
 * `<button name="action" value="...">` forms where the action value
 * tells the server which branch to take.
 */
(function () {
  "use strict";

  function isHtmxForm(form) {
    return (
      form.hasAttribute("hx-post") ||
      form.hasAttribute("hx-get") ||
      form.hasAttribute("hx-put") ||
      form.hasAttribute("hx-delete") ||
      form.hasAttribute("hx-patch")
    );
  }

  document.addEventListener(
    "submit",
    function (e) {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (isHtmxForm(form)) return;

      const submitter =
        e.submitter || form.querySelector('button[type="submit"], button:not([type])');
      if (!submitter || submitter.dataset.busy === "1") return;

      submitter.dataset.busy = "1";
      submitter.classList.add("is-working");
      const spinner = document.createElement("span");
      spinner.className = "btn-spinner";
      spinner.setAttribute("aria-hidden", "true");
      submitter.insertBefore(spinner, submitter.firstChild);
    },
    true,
  );
})();
