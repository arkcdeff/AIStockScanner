/* ============================================================
   AIStockScanner Shell — sidebar, theme, mobile
   ============================================================ */
(function () {
    'use strict';

    var SIDEBAR_KEY = 'aistockscanner_sidebar_collapsed';
    var THEME_KEY = 'aistockscanner_theme';

    /* ---- Sidebar collapse ---- */
    function getSidebarCollapsed() {
        return localStorage.getItem(SIDEBAR_KEY) === 'true';
    }

    function setSidebarCollapsed(val) {
        localStorage.setItem(SIDEBAR_KEY, val ? 'true' : 'false');
    }

    function applySidebarState() {
        var sidebar = document.getElementById('appSidebar');
        var main = document.getElementById('appMain');
        var icon = document.getElementById('sidebarToggleIcon');
        if (!sidebar || !main) return;
        var collapsed = getSidebarCollapsed();
        sidebar.classList.toggle('collapsed', collapsed);
        main.classList.toggle('sidebar-collapsed', collapsed);
        if (icon) {
            icon.className = collapsed ? 'bi bi-chevron-right' : 'bi bi-chevron-left';
        }
    }

    window.toggleSidebar = function () {
        setSidebarCollapsed(!getSidebarCollapsed());
        applySidebarState();
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
            var inst = bootstrap.Tooltip.getInstance(el);
            if (inst) inst.dispose();
        });
        if (!getSidebarCollapsed()) {
            initTooltips();
        }
    };

    /* ---- Theme ---- */
    function getTheme() {
        return localStorage.getItem(THEME_KEY) || 'dark';
    }

    function setTheme(theme) {
        localStorage.setItem(THEME_KEY, theme);
    }

    function applyTheme() {
        var theme = getTheme();
        var effective = theme;
        if (theme === 'system') {
            effective = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
        }
        document.body.setAttribute('data-bs-theme', effective);
        var icon = document.getElementById('themeIcon');
        if (icon) {
            icon.className = effective === 'dark' ? 'bi bi-sun' : 'bi bi-moon';
        }
    }

    window.toggleTheme = function () {
        var current = getTheme();
        var next;
        if (current === 'light') next = 'dark';
        else if (current === 'dark') next = 'system';
        else next = 'light';
        setTheme(next);
        applyTheme();
    };

    /* ---- Tooltips ---- */
    function initTooltips() {
        if (!window.bootstrap || !bootstrap.Tooltip) return;
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
            if (!bootstrap.Tooltip.getInstance(el)) {
                new bootstrap.Tooltip(el);
            }
        });
    }

    /* ---- HTMX re-init ---- */
    document.body.addEventListener('htmx:afterSwap', function () {
        initTooltips();
    });

    document.body.addEventListener('htmx:responseError', function (event) {
        var target = event.detail && event.detail.target;
        if (!target) return;
        target.innerHTML = '<div class="state-panel state-panel-danger" role="alert">' +
            '<i class="bi bi-exclamation-octagon"></i><div><div class="state-panel-title">Unable to load this section</div>' +
            '<div class="state-panel-copy">The request failed. Check the selected inputs and try again.</div></div></div>';
    });

    /* ---- Init ---- */
    document.addEventListener('DOMContentLoaded', function () {
        applySidebarState();
        applyTheme();
        initTooltips();

        /* System theme listener */
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
            if (getTheme() === 'system') applyTheme();
        });
    });
})();
