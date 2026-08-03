document.addEventListener('DOMContentLoaded', function () {
    // Auto-refresh dashboard every 60 seconds
    setInterval(function () {
        const refreshBtn = document.querySelector('[hx-get="/dashboard"]');
        if (refreshBtn) {
            refreshBtn.click();
        }
    }, 60000);

    // Update market status badge
    const marketStatus = document.getElementById('market-status');
    if (marketStatus) {
        const statusText = marketStatus.textContent.trim().toLowerCase();
        if (statusText === 'open') {
            marketStatus.className = 'badge bg-success me-2';
        } else {
            marketStatus.className = 'badge bg-secondary me-2';
        }
    }

    // Update health badge
    const healthBadge = document.getElementById('system-health');
    if (healthBadge) {
        const healthText = healthBadge.textContent.trim().toLowerCase();
        if (healthText === 'healthy') {
            healthBadge.className = 'badge bg-success';
        } else {
            healthBadge.className = 'badge bg-danger';
        }
    }

    // HTMX error handling
    document.body.addEventListener('htmx:responseError', function (event) {
        console.error('HTMX error:', event.detail);
    });

    // Smooth scrolling for sidebar links
    document.querySelectorAll('.sidebar .nav-link').forEach(link => {
        link.addEventListener('click', function () {
            document.querySelectorAll('.sidebar .nav-link').forEach(l => l.classList.remove('active'));
            this.classList.add('active');
        });
    });
});
