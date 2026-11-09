
document.querySelectorAll('[data-generate-url][aria-controls]').forEach(function (barcodeButton) {
    const barcodeInput = document.getElementById(barcodeButton.getAttribute('aria-controls'));
    const barcodeFeedback = barcodeButton.closest('.barcode-field')?.querySelector('.barcode-feedback');
    if (!barcodeInput || !barcodeFeedback) return;
    const showBarcodeFeedback = function (message, className) {
        barcodeFeedback.className = className;
        barcodeFeedback.textContent = message;
        barcodeFeedback.hidden = false;
    };

    barcodeButton.addEventListener('click', async function () {
        barcodeButton.disabled = true;
        barcodeButton.setAttribute('aria-busy', 'true');
        showBarcodeFeedback('Gerando código...', 'barcode-feedback');

        try {
            const response = await fetch(barcodeButton.dataset.generateUrl, {
                method: 'GET',
                headers: {'Accept': 'application/json'},
            });
            let data = {};
            try {
                data = await response.json();
            } catch (error) {
                data = {};
            }

            if (!response.ok) {
                throw new Error(data.error || 'Não foi possível gerar o código.');
            }
            if (
                typeof data.codigo_barras !== 'string'
                || !/^\d{13}$/.test(data.codigo_barras)
            ) {
                throw new Error('O servidor retornou um código inválido.');
            }

            barcodeInput.value = data.codigo_barras;
            showBarcodeFeedback(
                'Código gerado. Confirme os dados antes de salvar.',
                'barcode-feedback barcode-feedback-success',
            );
            barcodeInput.focus();
        } catch (error) {
            showBarcodeFeedback(
                error.message || 'Não foi possível gerar o código.',
                'alert alert-danger barcode-feedback',
            );
        } finally {
            barcodeButton.disabled = false;
            barcodeButton.removeAttribute('aria-busy');
        }
    });
});
