const settlement = document.querySelector('.finance-card-settlement');

if (settlement) {
    const rows = [...settlement.querySelectorAll('[data-parcela]')];
    const account = settlement.querySelector('[name="conta_id"]');
    const register = settlement.querySelector('[data-registrar]');
    const summary = settlement.querySelector('[data-resumo]');
    const totalGross = settlement.querySelector('[data-total-bruto]');
    const totalFees = settlement.querySelector('[data-total-taxas]');
    const totalNet = settlement.querySelector('[data-total-liquido]');
    const currency = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});

    function cents(value) {
        const raw = value.trim();
        const normalized = /^\d{1,3}(?:\.\d{3})+,\d{1,2}$/.test(raw)
            ? raw.replaceAll('.', '') : raw;
        if (!/^\d+(?:[,.]\d{1,2})?$/.test(normalized)) return null;
        const [whole, fraction = ''] = normalized.split(/[,.]/);
        const result = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
        return Number.isSafeInteger(result) ? result : null;
    }

    function money(value) {
        return currency.format(value / 100);
    }

    function updatePreview() {
        let gross = 0;
        let fees = 0;
        let selected = 0;
        let invalid = false;

        for (const row of rows) {
            const checkbox = row.querySelector('[name="recebiveis"]');
            const feeInput = row.querySelector('[data-taxa]');
            const netValue = row.querySelector('[data-liquido]');
            const amount = cents(row.dataset.bruto);
            const fee = cents(feeInput.value);
            const problem = fee === null ? 'Informe a taxa em reais, com até duas casas decimais.'
                : fee > amount ? 'A taxa não pode superar o valor bruto desta parcela.' : '';
            feeInput.setCustomValidity(checkbox.checked ? problem : '');
            netValue.textContent = problem ? '—' : money(amount - fee);

            if (checkbox.checked) {
                selected++;
                gross += amount;
                if (problem) invalid = true;
                else fees += fee;
            }
        }

        const net = gross - fees;
        totalGross.textContent = money(gross);
        totalFees.textContent = invalid ? '—' : money(fees);
        totalNet.textContent = invalid ? '—' : money(net);
        register.disabled = !selected || invalid || net <= 0;

        if (!selected) summary.textContent = 'Selecione as parcelas para conferir o depósito.';
        else if (invalid) summary.textContent = 'Confira as taxas das parcelas selecionadas.';
        else if (net <= 0) summary.textContent = 'O total líquido precisa ser maior que zero.';
        else if (!account.value) summary.textContent = 'Selecione a conta de destino.';
        else summary.textContent = 'Confira o depósito em ' + account.selectedOptions[0].textContent.trim() + '.';
    }

    settlement.addEventListener('input', updatePreview);
    updatePreview();
}
