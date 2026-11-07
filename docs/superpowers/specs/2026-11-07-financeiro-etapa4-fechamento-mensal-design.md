# Financeiro Etapa 4 — Fechamento mensal

## Objetivo

Implementar prévia, classificação, confirmação, histórico e reabertura auditada do fechamento mensal financeiro, sem misturar cobertura operacional, situação das obrigações e saldo disponível.

## Limites

O fechamento usa os serviços e eventos financeiros existentes. Não altera o Caixa, não reprocessa vendas e não cria novos recebimentos ou pagamentos. A confirmação grava uma fotografia auditável do demonstrativo; a consulta histórica reconstrói o estado na data de referência usando as datas efetivas dos eventos.

## Reconstrução histórica

Cada mês é interpretado no intervalo inclusivo entre o primeiro e o último dia do mês. Consultas as-of não usam o campo atual `vigente` como fonte suficiente:

- pagamentos de contas a pagar entram pela `data_pagamento`;
- estornos de pagamentos entram pela `data_movimento` do estorno;
- liquidações de cartão entram pela `data_deposito`;
- cancelamentos de recebíveis entram pela `cancelado_em`;
- Pix e demais créditos/debitos usam `LancamentoFinanceiro.data_movimento`.

Para uma obrigação, a posição no fim do mês é reconstruída pela sequência de eventos até a data de referência. Assim, uma conta paga em dezembro e estornada em janeiro aparece aberta em novembro, paga em dezembro e reaberta em janeiro. Um pagamento posterior não altera retroativamente a prévia de novembro.

O saldo inicial de cada conta é o saldo informado na abertura do corte, acrescido dos lançamentos anteriores ao início do mês que sejam aceitos pelo corte. O saldo final considera os lançamentos até o fim do mês. Aportes, empréstimos e transferências participam do saldo disponível, mas não das entradas operacionais nem da cobertura.

## Demonstrativo e classificação

A prévia separa:

- saldo inicial e final por conta;
- entradas operacionais líquidas efetivamente recebidas;
- total das obrigações com vencimento no mês;
- pagamentos efetivos do mês;
- obrigações do mês pagas até a referência;
- obrigações do mês ainda abertas;
- vencidas de períodos anteriores ainda abertas;
- cartão a receber no fim do mês.

```text
cobertura = entradas_operacionais_liquidas
            - obrigacoes_validas_com_vencimento_no_mes
```

Classificação:

- cobertura negativa: `Mal`;
- cobertura zero ou positiva abaixo da margem: `No limite`;
- cobertura positiva igual ou superior à margem: `Bem`.

Uma prévia pode ser `Bem` enquanto exibe contas abertas; cobertura e situação de obrigações são indicadores independentes.

## Assinatura da prévia

O serviço monta uma assinatura determinística contendo, em ordem estável:

- mês e margem;
- contas financeiras consideradas e seus saldos iniciais;
- lançamentos relevantes, com identificador, conta, direção, valor, data e vínculos;
- obrigações, vencimentos, valores e origens;
- pagamentos e estornos com suas datas efetivas;
- recebíveis, liquidações e cancelamentos com seus identificadores, valores e datas;
- totais derivados do demonstrativo.

A assinatura não depende apenas dos totais. Trocar um lançamento mantendo o mesmo total produz outra assinatura e invalida a prévia.

Na confirmação, o serviço recalcula a prévia e a assinatura dentro da transação, sob as proteções de concorrência aplicáveis. Assinatura divergente exige nova conferência e não cria versão.

## Modelo de versões

`FechamentoMensal` representa uma versão confirmada de um mês. A versão guarda o mês, número sequencial, margem, classificação, totais do demonstrativo, assinatura da prévia, responsável, motivo, chave de idempotência, assinatura da operação e datas de confirmação.

`FechamentoMensalConta` guarda o saldo inicial e final de cada conta naquela versão.

`ReaberturaFechamentoMensal` registra cada reabertura com versão original, responsável, motivo, chave e assinatura. A reabertura preserva todos os valores, margem e classificação da versão, registra auditoria e encerra `vigente` naquela versão. O próximo fechamento insere uma nova versão; a anterior não é apagada nem sobrescrita.

O banco garante uma única versão vigente por mês. Confirmações concorrentes com a mesma chave e dados retornam a versão existente; chaves ou assinaturas conflitantes são rejeitadas.

Correções retroativas que afetem um mês fechado só podem ocorrer depois de reabertura explícita e auditada.

## Serviços

- `previsualizar_fechamento_mensal(month, margin)` retorna DTO imutável com o demonstrativo e a assinatura.
- `confirmar_fechamento_mensal(actor, month, margin, preview_signature, reason, idempotency_key)` recalcula, compara e grava a versão em transação.
- `reabrir_fechamento_mensal(actor, fechamento, reason, idempotency_key)` encerra a vigência e registra a reabertura em transação.
- As escritas exigem gerente ou superusuário, motivo e chave; registram auditoria e tratam reenvios equivalentes.

## Interface

A área de fechamento mensal mostra mês e margem, prévia, cobertura, classificação, situação das obrigações, saldo por conta, confirmação, versões históricas e reabertura. Os POSTs são protegidos no servidor; vendedor recebe 403 mesmo acessando a URL diretamente. A disposição responsiva será validada em desktop e mobile.

## Aceite

- R$ 900 de entradas com margem R$ 100 e R$ 1.000 de obrigações: `Mal`.
- R$ 1.050: `No limite`.
- R$ 1.100: `Bem`.
- A mesma obrigação aberta aparece nos três cenários, sem alterar a cobertura.
- Pagamento em dezembro e estorno em janeiro reconstroem novembro, dezembro e janeiro corretamente.
- Cartão aprovado em novembro e liquidado em dezembro permanece a receber em novembro.
- Troca de lançamento com total preservado invalida a assinatura.
- Reabertura conserva a versão anterior e o novo fechamento recebe outra versão.
- Replay, conflito, concorrência, vendedor e falhas transacionais são cobertos.
