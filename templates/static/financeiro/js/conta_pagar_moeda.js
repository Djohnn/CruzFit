document.querySelectorAll('[data-valor-monetario]').forEach(field => {
    field.addEventListener('blur', () => {
        const raw = field.value.trim();
        const grouped = /^\d{1,3}(?:\.\d{3})+,\d{1,2}$/;
        const decimal = /^\d+(?:[,.]\d{1,2})?$/;
        if (!grouped.test(raw) && !decimal.test(raw)) return;

        const normalized = grouped.test(raw) ? raw.replaceAll('.', '') : raw;
        const [whole, cents = ''] = normalized.split(/[,.]/);
        const integer = whole.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, '.');
        field.value = integer + ',' + cents.padEnd(2, '0');
    });
});
