# Financeiro Etapa 3 — Contas a pagar

## Objetivo

Adicionar o ciclo de contas a pagar do Cruz Fit sem misturar obrigação com movimento de caixa: registrar despesas avulsas, gerar uma única obrigação para cada `PedidoCompra` totalmente recebido, pagar a obrigação com débito financeiro atômico e estornar esse pagamento reabrindo a obrigação sem apagar o histórico.

## Decisões aprovadas

- A origem de compra da obrigação é `PedidoCompra`, com unicidade garantida no banco. `RecebimentoCompra` é apenas o caminho que torna o pedido elegível.
- Um pedido parcialmente recebido não gera obrigação. Dois recebimentos parciais continuam associados ao mesmo pedido; somente o estado `RECEBIDO` habilita a geração.
- O valor da obrigação é conferido pelo gerente e deve corresponder ao total contratado do pedido quando a obrigação é criada. O vencimento é informado separadamente.
- Despesas avulsas não têm origem de compra e podem ser abertas, vencidas e pagas.
- A baixa da obrigação é integral nesta etapa. O valor informado deve ser igual ao valor da obrigação; pagamentos repetidos após a baixa são rejeitados.
- A data de vencimento é uma data da obrigação. A data efetiva do pagamento é um movimento de saldo e precisa ser igual ou posterior ao corte inclusivo da conta escolhida.
- O estorno do pagamento é uma operação própria, vinculada ao pagamento e ao débito original, que cria a reversão e reabre a obrigação na mesma transação.

## Modelo e fluxo

O app `financeiro` terá modelos específicos para o domínio:

- `ContaPagar`: descrição, categoria, valor, vencimento, situação, origem opcional `PedidoCompra`, responsável, motivo, chave, assinatura e auditoria de criação.
- `PagamentoContaPagar`: obrigação, conta financeira, data efetiva, valor, débito `LancamentoFinanceiro`, responsável, motivo, chave e assinatura.
- `EstornoPagamentoContaPagar`: pagamento, reversão `LancamentoFinanceiro`, responsável, motivo, chave e assinatura.

`ContaPagar.origem_pedido` será `OneToOneField` protegido e não nulo apenas para obrigações de compra; a despesa avulsa usará origem nula. A regra de exatamente uma origem de compra e a unicidade no pedido ficarão em constraints e validação de serviço.

O serviço de Compras fornecerá um resumo somente-leitura do pedido recebido, incluindo estado e total contratado. O serviço Financeiro chamará esse contrato dentro da própria transação, bloqueará o pedido e criará a obrigação; não haverá acesso da view a modelos de outro módulo nem lógica financeira na view.

Todos os serviços de escrita exigirão gerente ou superusuário no servidor, motivo e chave de idempotência. A assinatura inclui ator, origem, valores e datas. O replay equivalente retorna o objeto original com `created=False`; a mesma chave com assinatura diferente gera conflito; constraints e locks protegem chaves diferentes e chamadas simultâneas.

## Transações e falhas

- Criação de obrigação e auditoria são atômicas.
- Pagamento cria o `PagamentoContaPagar` e o débito financeiro em uma única transação, protegendo a obrigação e a conta.
- Estorno cria `EstornoPagamentoContaPagar`, lançamento de reversão e reabertura da obrigação na mesma transação.
- Em SQLite, contenção recuperável terá tentativas limitadas que reiniciam a transação inteira. Erros operacionais não relacionados a contenção permanecerão identificáveis.

## Interface

O painel Financeiro exibirá obrigações abertas, vencidas e pagas, sem reduzir saldo ao registrar uma obrigação. Haverá ações de gerente para criar despesa avulsa, gerar obrigação de pedido recebido, pagar e estornar pagamento. As views apenas validam entrada HTTP, aplicam autorização de rota e chamam serviços.

## Verificação

Os testes cobrirão: obrigação antes/depois de dois recebimentos parciais, unicidade por pedido com chaves diferentes e duas conexões SQLite, despesa sem efeito no saldo, pagamento idempotente/conflitante/duplicado, rollback entre baixa e débito, estorno com reabertura e histórico, corte na véspera/no dia e POST direto por vendedor. A interface será exercitada nos testes de view e em desktop/mobile quando houver navegador disponível.
