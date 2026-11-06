"use strict";
// 在樣式表載入前套用偏好，避免深色頁面先閃現淺色背景。
(() => {
  const key = "local-transcript.theme";
  const normalize = value => ["light", "dark", "system"].includes(value) ? value : "system";
  const system = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  let preference = "system", control = null;
  try { preference = normalize(window.localStorage.getItem(key)); } catch { /* 儲存被封鎖仍可切換。 */ }
  function apply() {
    document.documentElement.dataset.theme = preference === "system" ? (system?.matches ? "dark" : "light") : preference;
    document.documentElement.dataset.themePreference = preference;
    if (control) control.value = preference;
  }
  apply();
  const systemChanged = () => { if (preference === "system") apply(); };
  if (system?.addEventListener) system.addEventListener("change", systemChanged);
  else if (system?.addListener) system.addListener(systemChanged);
  window.addEventListener("storage", event => {
    if (event.key !== key && event.key !== null) return;
    preference = normalize(event.key === null ? null : event.newValue);
    apply();
  });
  function bind() {
    control = document.getElementById("theme");
    if (!control) return;
    control.value = preference;
    control.addEventListener("change", () => {
      preference = normalize(control.value);
      apply();
      try { window.localStorage.setItem(key, preference); } catch { /* 僅本次頁面套用。 */ }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, {once: true});
  else bind();
})();
