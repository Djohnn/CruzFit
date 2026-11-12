document.addEventListener('DOMContentLoaded', function () {
    'use strict';
    const form = document.getElementById('exchange-form');
    if (!form) return;

    const catalog = JSON.parse(document.getElementById('exchange-product-catalog').textContent);
    const query = document.getElementById('exchange-product-query');
    const results = document.getElementById('exchange-product-results');
    const destinationLines = document.getElementById('exchange-destination-lines');
    const destinationEmpty = document.getElementById('exchange-destination-empty');
    const payments = document.getElementById('exchange-payment-lines');
    const paymentTemplate = payments.firstElementChild.cloneNode(true);
    const destinationTemplate = destinationLines.firstElementChild.cloneNode(true);
    const money = value => Number(value).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'});

    function productById(id) {
        return catalog.find(product => String(product.id) === String(id));
    }

    function syncDestination(row) {
        const id = row.querySelector('[name="produto_substituto"]').value;
        const product = productById(id);
        row.hidden = !product;
        row.querySelector('[data-selected-product]').textContent = product
            ? product.nome + ' · ' + money(product.preco)
            : 'Selecione um produto';
        row.querySelector('[data-selected-stock]').textContent = product
            ? 'Estoque disponível: ' + product.estoque
            : '';
        row.querySelector('[name="quantidade_substituto"]').disabled = !product;
        row.querySelector('[name="quantidade_substituto"]').required = Boolean(product);
        destinationEmpty.hidden = Array.from(destinationLines.querySelectorAll('[data-destination-row]'))
            .some(candidate => !candidate.hidden);
    }

    function resetDestinationRow(row) {
        row.querySelector('[name="produto_substituto"]').value = '';
        row.querySelector('[name="quantidade_substituto"]').value = '';
        syncDestination(row);
    }

    destinationLines.querySelectorAll('[data-destination-row]').forEach(row => {
        syncDestination(row);
        row.querySelector('.exchange-remove-button').addEventListener('click', function () {
            resetDestinationRow(row);
        });
    });

    function closeResults() {
        results.hidden = true;
        query.setAttribute('aria-expanded', 'false');
        query.removeAttribute('aria-activedescendant');
    }

    function addProduct(product) {
        let row = Array.from(destinationLines.querySelectorAll('[data-destination-row]'))
            .find(candidate => candidate.querySelector('[name="produto_substituto"]').value === String(product.id));
        if (row) {
            const quantity = row.querySelector('[name="quantidade_substituto"]');
            quantity.value = (Number(quantity.value || 0) + 1).toFixed(2);
        } else {
            row = Array.from(destinationLines.querySelectorAll('[data-destination-row]'))
                .find(candidate => !candidate.querySelector('[name="produto_substituto"]').value);
            if (!row) {
                row = destinationTemplate.cloneNode(true);
                destinationLines.appendChild(row);
                row.querySelector('.exchange-remove-button').addEventListener('click', function () {
                    resetDestinationRow(row);
                });
            }
            row.querySelector('[name="produto_substituto"]').value = product.id;
            row.querySelector('[name="quantidade_substituto"]').value = '1.00';
        }
        syncDestination(row);
        query.value = '';
        closeResults();
        document.getElementById('exchange-search-status').textContent = product.nome + ' adicionado aos substitutos.';
        query.focus();
    }

    query.addEventListener('input', function () {
        const needle = query.value.trim().toLocaleLowerCase('pt-BR');
        const matches = needle
            ? catalog.filter(product => product.nome.toLocaleLowerCase('pt-BR').includes(needle)
                || String(product.codigo).includes(needle)).slice(0, 8)
            : [];
        results.replaceChildren();
        matches.forEach((product, index) => {
            const option = document.createElement('button');
            option.type = 'button';
            option.className = 'exchange-search-option';
            option.id = 'exchange-result-' + index;
            option.setAttribute('role', 'option');
            option.textContent = product.nome + ' · ' + money(product.preco) + ' · estoque ' + product.estoque;
            option.addEventListener('click', () => addProduct(product));
            results.appendChild(option);
        });
        results.hidden = matches.length === 0;
        query.setAttribute('aria-expanded', matches.length ? 'true' : 'false');
        document.getElementById('exchange-search-status').textContent = needle && !matches.length
            ? 'Nenhum produto encontrado.' : 'Escolha um produto para adicionar.';
    });
    query.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            closeResults();
            return;
        }
        if (results.hidden || !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
        event.preventDefault();
        const options = Array.from(results.children);
        let index = options.findIndex(option => option.id === query.getAttribute('aria-activedescendant'));
        if (event.key === 'Enter') {
            if (index >= 0) options[index].click();
            return;
        }
        index = event.key === 'ArrowDown' ? (index + 1) % options.length : (index <= 0 ? options.length - 1 : index - 1);
        options.forEach((option, optionIndex) => option.setAttribute('aria-selected', String(optionIndex === index)));
        query.setAttribute('aria-activedescendant', options[index].id);
    });
    document.addEventListener('click', event => {
        if (!query.parentElement.contains(event.target)) closeResults();
    });

    form.querySelectorAll('[data-source-row]').forEach(row => {
        const checkbox = row.querySelector('input[type="checkbox"]');
        const quantity = row.querySelector('[name^="quantidade_origem_"]');
        checkbox.addEventListener('change', function () {
            quantity.disabled = !checkbox.checked;
            if (!checkbox.checked) quantity.value = '';
        });
    });

    document.getElementById('exchange-add-payment').addEventListener('click', function () {
        const row = paymentTemplate.cloneNode(true);
        row.querySelectorAll('input, select').forEach(field => { field.value = ''; });
        row.querySelector('.exchange-remove-button').addEventListener('click', () => row.remove());
        payments.appendChild(row);
        row.querySelector('select').focus();
    });
    payments.querySelectorAll('[data-payment-row] .exchange-remove-button').forEach(button => {
        button.addEventListener('click', function () {
            const rows = payments.querySelectorAll('[data-payment-row]');
            if (rows.length === 1) {
                rows[0].querySelectorAll('input, select').forEach(field => { field.value = ''; });
            } else {
                button.closest('[data-payment-row]').remove();
            }
        });
    });

    const dialog = document.querySelector('.exchange-review-dialog');
    const closeReview = dialog?.querySelector('[data-close-exchange-review]');
    if (dialog && closeReview) {
        closeReview.addEventListener('click', function () {
            dialog.close();
            document.querySelector('[name="acao"][value="revisar"]')?.focus();
        });
        if (dialog.open && typeof dialog.showModal === 'function') {
            dialog.close();
            dialog.showModal();
        }
    }
});
