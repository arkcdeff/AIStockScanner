(function () {
    function syncProviderFields(scope) {
        const select = scope.querySelector('.notification-provider-select');
        if (!select) return;
        scope.querySelectorAll('[data-provider-fields]').forEach(function (section) {
            const active = section.dataset.providerFields === select.value;
            section.hidden = !active;
            section.querySelectorAll('input, select, textarea').forEach(function (field) {
                field.disabled = !active;
            });
        });
    }

    function initialize(root) {
        const scope = root || document;
        scope.querySelectorAll('form').forEach(syncProviderFields);
        scope.querySelectorAll('.notification-provider-select').forEach(function (select) {
            if (select.dataset.providerBound) return;
            select.dataset.providerBound = 'true';
            select.addEventListener('change', function () {
                syncProviderFields(select.closest('form'));
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function () { initialize(document); });
    document.body.addEventListener('htmx:afterSwap', function (event) { initialize(event.detail.target); });
})();
