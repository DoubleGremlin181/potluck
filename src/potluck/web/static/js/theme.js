/* Theme toggle (light/dark) using DaisyUI data-theme attribute */
(function () {
  const STORAGE_KEY = "potluck-theme";
  const LIGHT = "potluck-light";
  const DARK = "potluck-dark";

  function getPreferred() {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? DARK : LIGHT;
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(STORAGE_KEY, theme);
    const icon = document.getElementById("theme-icon");
    if (icon) icon.textContent = theme === DARK ? "\u2600" : "\u263E";
  }

  apply(getPreferred());

  window.toggleTheme = function () {
    const current = document.documentElement.getAttribute("data-theme");
    apply(current === DARK ? LIGHT : DARK);
  };
})();
