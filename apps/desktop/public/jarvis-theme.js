// Blocking first-paint bootstrap; runs before stylesheets or React.
(() => {
  const root = document.documentElement;
  let mode = "dark";
  try {
    const cached = JSON.parse(localStorage.getItem("jarvis.theme.resolved") || "null");
    const saved = localStorage.getItem("terax-ui-theme-shadow") || localStorage.getItem("jarvis.theme");
    mode = saved || cached?.mode || "dark";
    if (mode === "system") mode = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    if (mode !== "light") mode = "dark";
    if (cached?.mode === mode && cached.variables) {
      for (const [key, value] of Object.entries(cached.variables)) {
        if (/^--[a-z-]+$/.test(key) && typeof value === "string" && CSS.supports("color", value)) root.style.setProperty(key, value);
      }
    }
  } catch { /* Do not block startup on damaged storage. */ }
  root.classList.add(mode);
  root.dataset.theme = mode;
  root.style.colorScheme = mode;
  root.style.backgroundColor = "var(--background, " + (mode === "dark" ? "#191919" : "#fafafa") + ")";
})();
