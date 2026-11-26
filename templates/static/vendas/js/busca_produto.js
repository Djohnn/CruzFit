window.bindProductSearch = function ({search, results, catalog, optionClass, formatPrice, status, addProduct, closeResults}) {
    function normalizeSearch(value) {
        return value.normalize('NFD')
            .replace(/\p{M}/gu, '')
            .toLocaleLowerCase('pt-BR')
            .replace(/[^\p{L}\p{N}]+/gu, ' ')
            .trim();
    }
    function barcodeQuery(value) {
        const trimmed = value.trim();
        return /^\d[\d\s.-]*$/.test(trimmed) ? trimmed.replace(/\D/g, '') : '';
    }
    function matchesNameAndColor(name, color, terms) {
        let matched = 0;
        for (let index = 0; index < name.length && matched < terms.length; index++) {
            let prefix = name[index];
            let end = index;
            while (terms[matched].startsWith(prefix) && prefix.length < terms[matched].length && end + 1 < name.length) {
                prefix += name[++end];
            }
            if (prefix.startsWith(terms[matched])) {
                matched++;
                index = end;
            }
        }
        return matched === terms.length || (matched > 0 && color.startsWith(terms.slice(matched).join(' ')));
    }
    const searchableCatalog = catalog.map(product => ({
        product,
        name: normalizeSearch([product.nome, product.modelo].filter(Boolean).join(' ')
            .replace(/(\p{Ll})(\p{Lu})/gu, '$1 $2')).split(' '),
        color: normalizeSearch(product.cor || ''),
        barcode: String(product.codigo_barras || ''),
    }));
    search.addEventListener('input', function () {
        const needle = normalizeSearch(search.value);
        const terms = needle.split(' ');
        const barcode = barcodeQuery(search.value);
        const matches = needle ? searchableCatalog.filter(entry =>
            (barcode && entry.barcode.startsWith(barcode))
            || matchesNameAndColor(entry.name, entry.color, terms)) : [];
        results.replaceChildren();
        results.scrollTop = 0;
        matches.forEach(({product}, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.id = search.id + '-result-' + index;
            button.className = optionClass;
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', 'false');
            const model = product.modelo ? ' · Modelo: ' + product.modelo : '';
            button.textContent = (product.label || product.nome) + model + ' · ' + formatPrice(product.preco) + ' · Estoque: ' + product.estoque;
            button.addEventListener('click', () => addProduct(product));
            results.appendChild(button);
        });
        results.hidden = matches.length === 0;
        search.setAttribute('aria-expanded', matches.length ? 'true' : 'false');
        search.removeAttribute('aria-activedescendant');
        status.textContent = needle && !matches.length ? 'Nenhum produto encontrado.' : 'Clique no produto para adicionar.';
    });
    search.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && search.value.trim()) {
            event.preventDefault();
            const barcode = barcodeQuery(search.value);
            const exact = barcode.length === 13 && searchableCatalog.find(entry => entry.barcode === barcode);
            if (exact) {
                addProduct(exact.product);
                return;
            }
        }
        if (event.key === 'Escape') { closeResults(); return; }
        if (results.hidden || !['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return;
        event.preventDefault();
        const options = Array.from(results.children);
        let index = options.findIndex(option => option.id === search.getAttribute('aria-activedescendant'));
        if (event.key === 'Enter') {
            if (index >= 0 || options.length === 1) options[Math.max(index, 0)].click();
            return;
        }
        index = event.key === 'ArrowDown' ? (index + 1) % options.length : (index <= 0 ? options.length - 1 : index - 1);
        options.forEach((option, i) => option.setAttribute('aria-selected', String(i === index)));
        search.setAttribute('aria-activedescendant', options[index].id);
        options[index].scrollIntoView({block: 'nearest'});
    });
};
