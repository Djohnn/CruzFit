document.addEventListener('DOMContentLoaded', function () {
    const output = document.querySelector('[data-product-gain]');
    if (!output) return;
    const form = output.closest('form');
    const cost = form.querySelector('[name="preco_compra"]');
    const price = form.querySelector('[name="preco_venda"]');
    const percentage = output.querySelector('[data-gain-percentage]');
    const amount = output.querySelector('[data-gain-amount]');
    const currency = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});
    const percent = new Intl.NumberFormat('pt-BR', {maximumFractionDigits: 2});

    function parseMoney(value) {
        const normalized = value.trim().replace(',', '.');
        if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) return null;
        const parts = normalized.split('.');
        return Number(parts[0]) * 100 + Number((parts[1] || '').padEnd(2, '0'));
    }
    function updateGain() {
        const purchase = parseMoney(cost.value);
        const sale = parseMoney(price.value);
        if (purchase === null || sale === null) {
            percentage.textContent = '—';
            amount.textContent = 'Informe os preços de compra e venda.';
            return;
        }
        const gain = sale - purchase;
        percentage.textContent = purchase > 0 ? percent.format(gain / purchase * 100) + '%' : 'Não calculável com compra a R$ 0,00';
        amount.textContent = ' · ' + currency.format(gain / 100) + ' por peça, antes de despesas.';
    }
    cost.addEventListener('input', updateGain);
    price.addEventListener('input', updateGain);
    updateGain();
});
