/* AIStockScanner — dashboard page behaviors */
document.addEventListener('DOMContentLoaded', function () {
    /* Auto-refresh dashboard every 60 seconds */
    setInterval(function () {
        var btn = document.querySelector('[data-refresh="dashboard"]');
        if (btn) btn.click();
    }, 60000);

    /* HTMX error logging */
    document.body.addEventListener('htmx:responseError', function (e) {
        console.error('HTMX error:', e.detail);
    });
});
