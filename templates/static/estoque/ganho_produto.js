document.addEventListener('DOMContentLoaded', function () {
    const currency = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
    const percent = new Intl.NumberFormat('pt-BR', {maximumFractionDigits: 2});

    function parseMoney(value) {
        const normalized = value.trim().replace(',', '.');
        if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) return null;
        const parts = normalized.split('.');
        return Number(parts[0]) * 100 + Number((parts[1] || '').padEnd(2, '0'));
    }
    function updateGain(output) {
        const form = output.closest('form');
        const row = output.closest('[data-variation-row]');
        const defaultCost = form.querySelector('[name="ficha-preco_compra"], [name="preco_compra"]');
        const defaultPrice = form.querySelector('[name="ficha-preco_venda"], [name="preco_venda"]');
        const cost = row?.querySelector('[data-variation-field="preco_compra"]');
        const price = row?.querySelector('[data-variation-field="preco_venda"]');
        const purchase = parseMoney(cost?.value || defaultCost?.value || '');
        const sale = parseMoney(price?.value || defaultPrice?.value || '');
        const percentage = output.querySelector('[data-gain-percentage]');
        const amount = output.querySelector('[data-gain-amount]');
        if (purchase === null || sale === null) {
            percentage.textContent = '—';
            amount.textContent = 'Informe os preços de compra e venda.';
            return;
        }
        const gain = sale - purchase;
        percentage.textContent = purchase > 0 ? percent.format(gain / purchase * 100) + '%' : 'Não calculável com compra a R$ 0,00';
        amount.textContent = ' · ' + currency.format(gain / 100) + ' por peça, antes de despesas.';
    }
    document.querySelectorAll('form').forEach(function (form) {
        const update = () => form.querySelectorAll('[data-product-gain]').forEach(updateGain);
        if (!form.querySelector('[data-product-gain]')) return;
        form.addEventListener('input', update);
        const rows = form.querySelector('#variation-rows');
        if (rows) new MutationObserver(update).observe(rows, {childList: true});
        update();
    });
});
