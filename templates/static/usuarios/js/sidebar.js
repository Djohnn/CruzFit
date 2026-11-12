(() => {
    "use strict";

    const sidebar = document.querySelector("#appSidebar");
    const nav = document.querySelector("#appPrimaryNav");
    const pinButton = document.querySelector("#sidebarPin");
    const mobileCloseButton = document.querySelector("#sidebarMobileClose");
    const mobileOpenButton = document.querySelector("#sidebarMobileOpen");
    const scrim = document.querySelector("#sidebarScrim");
    const liveMessage = document.querySelector("#sidebarLiveMessage");
    const pinStorageKey = "cruzfit-sidebar-pinned";

    if (!sidebar || !nav || !pinButton || !mobileOpenButton || !scrim) return;

    let pinned = false;
    let hoverExpanded = false;
    let focusInside = false;
    let mobileOpen = false;
    let lastFocusedBeforeMobile = null;

    const isMobile = () => window.matchMedia("(max-width: 760px)").matches;
    const isOpenForInteraction = () => pinned || hoverExpanded || focusInside || mobileOpen;

    function announce(message) {
        if (!liveMessage) return;
        liveMessage.textContent = "";
        window.setTimeout(() => { liveMessage.textContent = message; }, 20);
    }

    function syncSidebar() {
        const expanded = isOpenForInteraction();
        sidebar.classList.toggle("is-expanded", expanded);
        sidebar.classList.toggle("is-pinned", pinned);
        document.body.classList.toggle("sidebar-pinned", pinned);
        pinButton.setAttribute("aria-pressed", String(pinned));
        pinButton.setAttribute("aria-label", pinned ? "Desafixar navegação" : "Fixar navegação aberta");
        pinButton.dataset.tooltip = pinned ? "Desafixar navegação" : "Fixar navegação aberta";
    }

    function setGroupOpen(group, open) {
        const toggle = group?.querySelector(".nav-group-toggle");
        const submenu = group?.querySelector(".nav-submenu");
        if (!toggle || !submenu) return;
        toggle.setAttribute("aria-expanded", String(open));
        submenu.hidden = !open;
        group.classList.toggle("is-open", open);
    }

    function focusableInSidebar() {
        return [...sidebar.querySelectorAll("a[href], button:not([disabled])")].filter((element) => {
            return !element.closest("[hidden]") && element.offsetParent !== null;
        });
    }

    function expandForHover() {
        if (!isMobile() && !pinned) {
            hoverExpanded = true;
            syncSidebar();
        }
    }

    function collapseIfIdle() {
        window.setTimeout(() => {
            if (!isMobile() && !pinned && !focusInside) {
                hoverExpanded = false;
                syncSidebar();
            }
        }, 130);
    }

    function openMobile() {
        if (!isMobile() || mobileOpen) return;
        lastFocusedBeforeMobile = document.activeElement;
        mobileOpen = true;
        sidebar.classList.add("is-mobile-open", "is-expanded");
        scrim.hidden = false;
        mobileOpenButton.setAttribute("aria-expanded", "true");
        document.body.style.overflow = "hidden";
        window.requestAnimationFrame(() => mobileCloseButton?.focus());
        announce("Menu aberto. Use Tab para navegar e Escape para fechar.");
    }

    function closeMobile() {
        if (!mobileOpen) return;
        mobileOpen = false;
        sidebar.classList.remove("is-mobile-open");
        scrim.hidden = true;
        mobileOpenButton.setAttribute("aria-expanded", "false");
        document.body.style.overflow = "";
        if (lastFocusedBeforeMobile && typeof lastFocusedBeforeMobile.focus === "function") {
            lastFocusedBeforeMobile.focus();
        }
        announce("Menu fechado.");
        syncSidebar();
    }

    function restoreCurrentGroup() {
        const active = sidebar.querySelector(".nav-link.is-active");
        const currentGroup = active?.closest(".nav-group");
        sidebar.querySelectorAll(".nav-group").forEach((group) => {
            const submenu = group.querySelector(".nav-submenu");
            if (!submenu) return;
            if (group === currentGroup || group.classList.contains("is-current")) {
                setGroupOpen(group, true);
            }
        });
    }

    function handleNavArrow(event) {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        const items = focusableInSidebar().filter((item) => {
            return item.classList.contains("nav-link") || item.classList.contains("nav-group-toggle");
        });
        const index = items.indexOf(document.activeElement);
        if (index < 0 || !items.length) return;
        event.preventDefault();
        let next = index;
        if (event.key === "ArrowDown") next = (index + 1) % items.length;
        if (event.key === "ArrowUp") next = (index - 1 + items.length) % items.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = items.length - 1;
        items[next]?.focus();
    }

    function keepFocusInside(event) {
        if (!mobileOpen || event.key !== "Tab") return;
        const items = focusableInSidebar();
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    sidebar.addEventListener("pointerenter", expandForHover);
    sidebar.addEventListener("pointerleave", collapseIfIdle);
    sidebar.addEventListener("focusin", () => {
        focusInside = true;
        if (!isMobile()) {
            hoverExpanded = true;
            syncSidebar();
        }
    });
    sidebar.addEventListener("focusout", () => {
        window.setTimeout(() => {
            focusInside = sidebar.contains(document.activeElement);
            if (!focusInside) collapseIfIdle();
        }, 0);
    });
    nav.addEventListener("keydown", handleNavArrow);
    sidebar.addEventListener("keydown", keepFocusInside);

    pinButton.addEventListener("click", () => {
        pinned = !pinned;
        hoverExpanded = pinned;
        try { window.localStorage.setItem(pinStorageKey, String(pinned)); } catch (_) { /* storage is optional */ }
        syncSidebar();
        announce(pinned ? "Navegação fixada aberta." : "Navegação desafixada.");
    });

    sidebar.querySelectorAll(".nav-group-toggle").forEach((toggle) => {
        toggle.addEventListener("click", () => {
            if (!isMobile() && !isOpenForInteraction()) {
                hoverExpanded = true;
                syncSidebar();
            }
            const group = toggle.closest(".nav-group");
            const open = toggle.getAttribute("aria-expanded") !== "true";
            setGroupOpen(group, open);
            announce(`${group.dataset.group} ${open ? "aberto" : "fechado"}.`);
        });
        toggle.addEventListener("keydown", (event) => {
            const group = toggle.closest(".nav-group");
            if (event.key === "ArrowRight") {
                event.preventDefault();
                setGroupOpen(group, true);
            }
            if (event.key === "ArrowLeft") {
                event.preventDefault();
                setGroupOpen(group, false);
            }
        });
    });

    mobileOpenButton.addEventListener("click", openMobile);
    mobileCloseButton?.addEventListener("click", closeMobile);
    scrim.addEventListener("click", closeMobile);
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && mobileOpen) closeMobile();
    });
    window.addEventListener("resize", () => {
        if (!isMobile() && mobileOpen) closeMobile();
    });

    try {
        pinned = window.localStorage.getItem(pinStorageKey) === "true";
    } catch (_) {
        pinned = false;
    }
    restoreCurrentGroup();
    syncSidebar();
})();
