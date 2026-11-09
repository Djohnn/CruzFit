(() => {
    const form = document.querySelector('.close-form[data-esperado]');
    if (!form) return;

    const contado = form.elements.dinheiro_contado;
    const motivo = form.elements.justificativa;
    const resultado = document.getElementById('close-cash-difference');
    const esperado = Math.round(Number(form.dataset.esperado) * 100);
    const moeda = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });

    function atualizarConferencia() {
        const valor = contado.value.trim();
        const partes = /^(\d{1,10})(?:[,.](\d{1,2}))?$/.exec(valor);
        contado.setCustomValidity('');
        motivo.required = false;
        resultado.removeAttribute('data-divergencia');
        if (!partes) {
            resultado.textContent = valor ? 'Digite um valor com até duas casas decimais.' : '';
            contado.setCustomValidity(resultado.textContent);
            return;
        }

        const centavos = Number(partes[1]) * 100 + Number((partes[2] || '').padEnd(2, '0'));
        const diferenca = centavos - esperado;
        if (diferenca === 0) {
            resultado.textContent = 'O dinheiro contado bate com o esperado.';
            return;
        }

        const valorDiferenca = moeda.format(Math.abs(diferenca) / 100);
        const descricao = diferenca < 0 ? 'Falta' : 'Sobra de';
        resultado.textContent = `${descricao} ${valorDiferenca} na gaveta. Confira a contagem e informe o motivo.`;
        resultado.setAttribute('data-divergencia', '');
        motivo.required = true;
    }

    contado.addEventListener('input', atualizarConferencia);
    atualizarConferencia();
})();
