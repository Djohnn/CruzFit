document.addEventListener('DOMContentLoaded', function () {
    'use strict';
    const form = document.getElementById('sale-form');
    if (!form) return;
    const items = document.getElementById('sale-items');
    const payments = document.getElementById('payment-lines');
    const search = document.getElementById('checkout-search');
    const results = document.getElementById('checkout-results');
    const catalog = JSON.parse(document.getElementById('sale-product-catalog').textContent);
    const discountInput = document.getElementById('discount-input');
    const discountMode = document.getElementById('discount-mode');
    const itemTemplate = items.firstElementChild.cloneNode(true);
    const paymentTemplate = payments.firstElementChild.cloneNode(true);
    let itemIndex = items.children.length;
    let paymentIndex = payments.children.length;
    const reviewDialog = document.getElementById('sale-confirm-dialog');
    const navigation = document.querySelector('nav.navbar');
    if (navigation) {
        new ResizeObserver(() => {
            document.documentElement.style.setProperty('--checkout-nav-height', navigation.getBoundingClientRect().height + 'px');
        }).observe(navigation);
    }
    function units(value) {
        const match = String(value).replace(',', '.').match(/^(\d+)(?:\.(\d{0,2}))?$/);
        return match ? Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0')) : 0;
    }
    function roundedRatio(numerator, denominator) {
        const whole = Math.floor(numerator / denominator);
        const remainder = numerator % denominator;
        return whole + (remainder * 2 > denominator || (remainder * 2 === denominator && whole % 2) ? 1 : 0);
    }
    function money(value) { return (value / 100).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'}); }
    function setAmount(field, value) { field.value = (value / 100).toFixed(2); }
    function productById(id) { return catalog.find(product => String(product.id) === String(id)); }
    function invalidateReview() {
        if (reviewDialog?.open) reviewDialog.close();
        form.querySelectorAll('[name="revisao_confirmada"], [name="assinatura_revisao"]').forEach(field => { field.value = ''; });
        document.getElementById('confirm-sale-button')?.remove();
        const review = form.querySelector('.cash-server-review');
        if (review) review.hidden = true;
        form.querySelector('.checkout-receipt').hidden = false;
    }
    function cartTotals() {
        let subtotal = 0;
        items.querySelectorAll('[data-sale-row]').forEach(row => {
            const product = productById(row.querySelector('[name="produto"]').value);
            if (!product) return;
            const price = units(product.preco);
            const line = roundedRatio(price * units(row.querySelector('[name="quantidade"]').value), 100);
            row.querySelector('[data-unit-price]').textContent = money(price);
            row.querySelector('[data-line-total]').textContent = money(line);
            subtotal += line;
        });
        const entered = units(discountInput.value);
        const discount = discountMode.value === 'percentual' ? roundedRatio(subtotal * entered, 10000) : entered;
        discountInput.setCustomValidity(discountMode.value === 'percentual' && entered > 10000
            ? 'O desconto não pode superar 100%.' : discount > subtotal ? 'O desconto não pode superar o subtotal.' : '');
        setAmount(document.getElementById('sale-discount'), discount);
        return {subtotal, discount, total: Math.max(0, subtotal - discount)};
    }
    function paymentRows() { return Array.from(payments.children); }
    function applied(row) { return row.querySelector('select').value ? units(row.querySelector('[name="valor_aplicado"]').value) : 0; }
    function updateTotals(rebalance) {
        const totals = cartTotals();
        const rows = paymentRows();
        const selected = rows.filter(row => row.querySelector('select').value);
        if (rebalance && selected.length) {
            const last = selected[selected.length - 1];
            const others = selected.filter(row => row !== last).reduce((sum, row) => sum + applied(row), 0);
            setAmount(last.querySelector('[name="valor_aplicado"]'), Math.max(0, totals.total - others));
        }
        let paid = 0;
        let change = 0;
        let shortCash = false;
        rows.forEach(row => {
            const method = row.querySelector('select');
            const amount = row.querySelector('[name="valor_aplicado"]');
            const tendered = row.querySelector('[name="valor_entregue"]');
            const cash = method.value === 'DINHEIRO';
            method.setCustomValidity(method.value && rows.some(other => other !== row && other.querySelector('select').value === method.value)
                ? 'Esta forma já foi adicionada.' : '');
            tendered.closest('.form-group').hidden = !cash;
            amount.required = Boolean(method.value) && totals.total > 0;
            tendered.required = cash && totals.total > 0;
            amount.disabled = totals.total === 0;
            tendered.disabled = totals.total === 0;
            method.disabled = totals.total === 0;
            if (!method.value) {
                amount.value = '';
                tendered.value = '';
            } else if (!cash || tendered.dataset.manual !== 'true') {
                setAmount(tendered, applied(row));
            }
            if (method.value && totals.total > 0) {
                paid += applied(row);
                if (cash) {
                    change += Math.max(0, units(tendered.value) - applied(row));
                    shortCash = units(tendered.value) < applied(row);
                }
            }
        });
        Object.entries({...totals, paid, remaining: Math.max(0, totals.total - paid), change}).forEach(([key, value]) => {
            form.querySelectorAll('[data-summary="' + key + '"]').forEach(element => { element.textContent = money(value); });
        });
        document.getElementById('checkout-payment-status').textContent = paid > totals.total
            ? 'Os pagamentos ultrapassam o total em ' + money(paid - totals.total) + '. Ajuste os valores.'
            : shortCash ? 'O valor recebido em dinheiro é menor que o valor a pagar.'
            : totals.total === paid && selected.length ? 'Pagamento completo. Revise para confirmar.' : '';
        document.getElementById('checkout-empty').hidden = Boolean(items.querySelector('[data-sale-row]:not([hidden])'));
        document.getElementById('add-payment').disabled = rows.length >= 3;
        return totals;
    }
    function changed(rebalance) { invalidateReview(); updateTotals(rebalance); }
    function bindItem(row) {
        const product = productById(row.querySelector('[name="produto"]').value);
        row.hidden = !product;
        row.querySelector('[name="quantidade"]').disabled = !product;
        row.querySelector('[name="produto"]').disabled = !product;
        if (product) {
            row.querySelector('.sale-product-query').value = product.nome;
            row.querySelector('[data-selected-product]').textContent = product.codigo_barras ? 'Código: ' + product.codigo_barras : '';
        }
        row.querySelector('[name="quantidade"]').addEventListener('input', () => changed(true));
        row.querySelector('.sale-remove-button').addEventListener('click', function () { row.remove(); changed(true); });
    }
    function resetIds(row, index) {
        row.querySelectorAll('[id]').forEach(element => { element.id = element.id.replace(/-\d+$/, '-' + index); });
        row.querySelectorAll('label[for]').forEach(label => { label.htmlFor = label.htmlFor.replace(/-\d+$/, '-' + index); });
        row.querySelectorAll('[aria-describedby]').forEach(element => {
            element.setAttribute('aria-describedby', element.getAttribute('aria-describedby').replace(/-\d+$/, '-' + index));
        });
    }
    function closeResults() {
        results.hidden = true;
        search.setAttribute('aria-expanded', 'false');
        search.removeAttribute('aria-activedescendant');
    }
    function addProduct(product) {
        const existing = Array.from(items.children).find(row => row.querySelector('[name="produto"]').value === String(product.id));
        if (existing) {
            const quantity = existing.querySelector('[name="quantidade"]');
            setAmount(quantity, units(quantity.value) + 100);
        } else {
            const row = itemTemplate.cloneNode(true);
            resetIds(row, ++itemIndex);
            row.querySelector('[name="produto"]').value = product.id;
            row.querySelector('[name="quantidade"]').value = '1';
            items.appendChild(row);
            bindItem(row);
        }
        search.value = '';
        closeResults();
        document.getElementById('checkout-search-status').textContent = product.nome + ' adicionado ao carrinho.';
        changed(true);
        search.focus();
    }
    search.addEventListener('input', function () {
        const needle = search.value.trim().toLocaleLowerCase('pt-BR');
        const matches = needle ? catalog.filter(product => product.nome.toLocaleLowerCase('pt-BR').includes(needle)
            || String(product.codigo_barras || '').includes(needle)).slice(0, 8) : [];
        results.replaceChildren();
        matches.forEach((product, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.id = 'checkout-result-' + index;
            button.className = 'sale-search-option';
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', 'false');
            button.textContent = product.nome + ' · ' + money(units(product.preco)) + ' · Estoque: ' + product.estoque;
            button.addEventListener('click', () => addProduct(product));
            results.appendChild(button);
        });
        results.hidden = matches.length === 0;
        search.setAttribute('aria-expanded', matches.length ? 'true' : 'false');
        search.removeAttribute('aria-activedescendant');
        document.getElementById('checkout-search-status').textContent = needle && !matches.length ? 'Nenhum produto encontrado.' : 'Clique no produto para adicionar.';
    });
    search.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') { closeResults(); return; }
        if (results.hidden || !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
        event.preventDefault();
        const options = Array.from(results.children);
        let index = options.findIndex(option => option.id === search.getAttribute('aria-activedescendant'));
        if (event.key === 'Enter') { if (index >= 0) options[index].click(); return; }
        index = event.key === 'ArrowDown' ? (index + 1) % options.length : (index <= 0 ? options.length - 1 : index - 1);
        options.forEach((option, i) => option.setAttribute('aria-selected', String(i === index)));
        search.setAttribute('aria-activedescendant', options[index].id);
    });
    document.addEventListener('click', event => { if (!search.parentElement.contains(event.target)) closeResults(); });
    function fillRemaining(row) {
        const others = paymentRows().filter(other => other !== row).reduce((sum, other) => sum + applied(other), 0);
        setAmount(row.querySelector('[name="valor_aplicado"]'), Math.max(0, cartTotals().total - others));
    }
    function bindPayment(row) {
        const method = row.querySelector('select');
        const amount = row.querySelector('[name="valor_aplicado"]');
        const tendered = row.querySelector('[name="valor_entregue"]');
        if (tendered.value) tendered.dataset.manual = 'true';
        method.addEventListener('change', function () {
            if (method.value && (!amount.value || units(amount.value) === 0)) fillRemaining(row);
            tendered.dataset.manual = 'false';
            changed(false);
        });
        amount.addEventListener('input', () => changed(false));
        tendered.addEventListener('input', function () { tendered.dataset.manual = 'true'; changed(false); });
        row.querySelector('.payment-remaining-button').addEventListener('click', function () { fillRemaining(row); changed(false); });
        row.querySelector('.payment-remove-button').addEventListener('click', function () {
            if (paymentRows().length === 1) {
                method.value = '';
                amount.value = '';
                tendered.value = '';
                tendered.dataset.manual = 'false';
            } else row.remove();
            changed(true);
        });
    }
    function addPayment(method) {
        let row = paymentRows().find(candidate => !candidate.querySelector('select').value);
        if (!row) {
            if (paymentRows().length >= 3) return;
            row = paymentTemplate.cloneNode(true);
            resetIds(row, ++paymentIndex);
            row.querySelectorAll('input, select').forEach(field => { field.value = ''; });
            payments.appendChild(row);
            bindPayment(row);
        }
        if (method) {
            row.querySelector('select').value = method;
            row.querySelector('select').dispatchEvent(new Event('change'));
        }
        row.querySelector(method ? '[name="valor_aplicado"]' : 'select').focus();
        changed(false);
    }
    document.getElementById('add-payment').addEventListener('click', () => addPayment(''));
    document.querySelectorAll('[data-payment-method]').forEach(button => {
        button.addEventListener('click', function () {
            const row = paymentRows().find(candidate => candidate.querySelector('select').value === button.dataset.paymentMethod);
            if (row) row.querySelector('[name="valor_aplicado"]').focus();
            else addPayment(button.dataset.paymentMethod);
        });
    });
    [discountInput, discountMode].forEach(field => field.addEventListener('input', () => changed(true)));
    items.querySelectorAll('[data-sale-row]').forEach(bindItem);
    paymentRows().forEach(bindPayment);
    updateTotals(false);
    if (reviewDialog) {
        reviewDialog.showModal();
        document.getElementById('return-to-sale-button').addEventListener('click', () => reviewDialog.close());
        reviewDialog.addEventListener('close', () => document.getElementById('review-sale-button').focus());
    }
    form.addEventListener('submit', function (event) {
        if (event.submitter?.value === 'revisar' && reviewDialog && document.getElementById('confirm-sale-button')) {
            event.preventDefault();
            reviewDialog.showModal();
            return;
        }
        updateTotals(false);
        if (!items.querySelector('[data-sale-row]:not([hidden])')) {
            event.preventDefault();
            document.getElementById('checkout-search-status').textContent = 'Adicione pelo menos um produto.';
            search.focus();
            return;
        }
        if (!event.submitter || event.submitter.value !== 'confirmar') return;
        const button = document.getElementById('confirm-sale-button');
        const action = document.createElement('input');
        action.type = 'hidden';
        action.name = 'acao';
        action.value = 'confirmar';
        form.appendChild(action);
        button.removeAttribute('name');
        button.disabled = true;
        button.textContent = 'Registrando venda…';
        document.getElementById('return-to-sale-button').disabled = true;
    });
});
