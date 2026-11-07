# PRD — Financeiro e fechamento mensal do Cruz Fit

## Objetivo

Responder se o estabelecimento pagou as próprias contas com dinheiro gerado pela operação. O Financeiro controla contas, recebíveis, despesas e fechamento. O Caixa mantém a operação diária de vendas e conferência.

## Regras de negócio

- Conta financeira tem saldo inicial datado e histórico. Saldo atual = saldo inicial + créditos − débitos. O saldo inicial não é receita do período.
- Pix confirmado credita a conta de destino na data da confirmação. A venda no cartão aprovada fica em A receber. A liquidação credita o valor líquido na conta escolhida, com data, bruto, taxas e referência da operadora. Liquidação com várias vendas deve identificar cada parcela e não permitir creditá-la duas vezes.
- Dinheiro recebido permanece no Caixa. Depósito é transferência Caixa → conta, com os dois lados vinculados. A transferência não muda o resultado da operação.
- Aporte, empréstimo e uso de saldo anterior aparecem na disponibilidade, separados dos recebimentos operacionais. Estorno reduz o resultado na data em que o dinheiro sai. Cancelamento antes de liquidar cancela o recebível. Reutilizar os eventos existentes de venda e estorno.
- Contas a pagar têm categoria, vencimento, valor, situação e origem. Compras recebidas devem permitir gerar a obrigação vinculada à origem uma única vez, com valor e vencimento conferidos pelo gerente. Despesas avulsas são registradas manualmente. Pagar uma conta debita a conta escolhida. Registrar conta e pagar são ações distintas.
- Valores em Decimal, datas de negócio no fuso da loja, trilha de auditoria, permissões de gerente ou superusuário e chaves de idempotência para escritas. Correções financeiras por eventos de estorno vinculados ao original.

## Fechamento mensal

Exibir saldo inicial e final por conta, entradas operacionais líquidas, contas com vencimento no mês, pagamentos efetivos do mês, contas em aberto, vencidas de períodos anteriores e cartão a receber.

Separar os indicadores:

1. **Cobertura do mês** = entradas operacionais líquidas efetivamente recebidas no mês − total das contas válidas com vencimento no mês, inclusive as em aberto. Classificar como **Fechou mal** se negativo, **No limite** se zero ou abaixo da margem de segurança configurada em reais, **Fechou bem** se positivo e pelo menos igual à margem. Aportes, empréstimos, saldo inicial e transferências ficam fora do cálculo.
2. **Situação dos pagamentos** mostra quanto das contas do mês foi pago e quanto segue aberto, mesmo quando a cobertura é positiva. Mostrar os desembolsos efetivos e o saldo final em separado.

O gerente confere a prévia e confirma o fechamento. O histórico guarda totais, critério e margem usados, responsável e data. Correções de mês fechado exigem reabertura auditada e novo fechamento, preservando a versão anterior.

## Etapas

- [x] **0 — Preparação.** Criar worktree a partir da base de integração confirmada, registrar SHA e mapear Caixa, Vendas, Compras e permissões pelo Graphify. Usar banco e mídia descartáveis. Definir corte de ativação para que saldos iniciais e lançamentos antigos não sejam contados duas vezes. **Evidência:** `codex/financeiro` em `C:\desenvolvimento\Contruct\.worktrees\financeiro`, base `222c45fb3c2e3413637a7e9a40d4526e61d78078`; Graphify consultado; validação usa banco/mídia temporários; corte inclusivo documentado no design e nos testes.
- [x] **1 — Contas e movimentos.** Implementar contas, saldos iniciais, transferências, aportes e estornos. Aceite: cada transferência debita e credita atomicamente, conserva o saldo total e não aparece como receita. **Evidência:** serviços transacionais, auditoria, permissão de gerente/superusuário e idempotência implementados; testes cobrem véspera e dia do corte, replay, conflito, rollback, aporte, estorno, concorrência SQLite com duas conexões e vendedor; interface validada em 1280×720 e 390×844 no banco descartável.
- [ ] **2 — Recebimentos.** Vincular vendas e estornos existentes, creditar Pix confirmado e registrar cartão a receber com liquidação manual. Aceite: Pix de R$ 50 credita uma vez. Venda de R$ 100 no cartão em novembro, liquidada em dezembro com taxa de R$ 3, gera R$ 97 no saldo e no resultado de dezembro.
- [ ] **3 — Contas a pagar.** Implementar despesas avulsas, obrigação vinculada à compra e baixa por pagamento. Aceite: repetir a operação não duplica a obrigação ou o débito. Contas abertas e vencidas aparecem sem reduzir saldo antes do pagamento.
- [ ] **4 — Fechamento.** Implementar prévia, classificação, confirmação, histórico e reabertura auditada. Aceite com margem de R$ 100 e contas de R$ 1.000: entradas operacionais de R$ 900 resultam em Mal, R$ 1.050 em No limite e R$ 1.100 em Bem. Uma conta aberta continua visível em qualquer classificação.
- [ ] **5 — QA e auditoria.** Cobrir virada do mês, pagamento misto, estorno, duplicidade, rollback e permissões. Rodar testes focados, suíte serial, Django check, drift de migrações, Ruff e mypy. Validar desktop/mobile e migração em cópia descartável. Registrar PASS, FAIL ou HOLD por critério, com comandos e evidências.

## Fora desta entrega

Lucro contábil e CMV, integração bancária ou com adquirente, conciliação automática e publicação no cliente. Integração na main e migração real seguem após auditoria e autorização.
