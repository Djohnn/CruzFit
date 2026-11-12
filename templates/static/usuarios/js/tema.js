(() => {
    "use strict";

    const storageKey = "cruzfit-theme";
    let theme = "dark";
    try {
        if (localStorage.getItem(storageKey) === "light") theme = "light";
    } catch (_) {
        // The default still works when browser storage is unavailable.
    }

    function applyTheme() {
        document.body.classList.toggle("theme-light", theme === "light");
        document.body.classList.toggle("theme-dark", theme === "dark");
        document.querySelectorAll("[data-cruzfit-theme]").forEach((button) => {
            const selected = button.dataset.cruzfitTheme === theme;
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", String(selected));
        });
    }

    applyTheme();
    document.addEventListener("DOMContentLoaded", () => {
        applyTheme();
        document.querySelectorAll("[data-cruzfit-theme]").forEach((button) => {
            button.addEventListener("click", () => {
                theme = button.dataset.cruzfitTheme;
                applyTheme();
                try {
                    localStorage.setItem(storageKey, theme);
                } catch (_) {
                    // The choice still applies to this page.
                }
            });
        });
    });
})();
