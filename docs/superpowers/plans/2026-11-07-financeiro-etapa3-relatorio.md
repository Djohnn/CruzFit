# Relatório de auditoria — Financeiro Etapa 3

Data da execução: 2026-11-07

Branch: `codex/financeiro`

Worktree: `C:\desenvolvimento\Contruct\.worktrees\financeiro`

Base solicitada: `0490b0b9bf5060d3e0b4e564e09b2a1e3e623d6a`

Commit de implementação: `8115069` (`feat: implement contas a pagar`)

## Escopo e origem única

O desenho aprovado usa `PedidoCompra` como a origem única da obrigação. A função de integração em `compras.services.resumo_pedido_para_financeiro()` bloqueia o fluxo enquanto o pedido não está em `RECEBIDO`, isto é, totalmente recebido. O valor confirmado pelo gerente precisa ser igual ao total contratado. A unicidade é materializada no banco por `OneToOneField`/restrição única em `ContaPagar.origem_pedido`.

Com duas entregas parciais, a primeira deixa o pedido em `PARCIALMENTE_RECEBIDO` e não o lista como elegível. A segunda completa as quantidades; só então o pedido aparece para conferência e geração da obrigação. Chaves iguais são replays; chaves diferentes para o mesmo pedido são rejeitadas pela origem única. O teste de concorrência usa duas conexões SQLite independentes e confirma uma única conta.

Cada entrega separada pelo fornecedor continua fora do escopo; a origem financeira permanece o pedido completo.

## Preparação e Graphify

O grafo foi materializado somente na cópia descartável:

`C:\desenvolvimento\Contruct\.tmp-financeiro-etapa3-graphify-7f5a1b`

Extração: `graphify extract ... --code-only --no-cluster` — 2.126 nós e 6.610 arestas.

Consultas realizadas:

- `compras.receber_pedido()` e o caminho de recebimento para confirmar que o estado `RECEBIDO` é produzido apenas após a última parcela;
- `LancamentoFinanceiro` e os serviços de lançamento/estorno para reutilizar o débito financeiro e preservar o histórico;
- fluxos de Caixa, Compras, permissões, auditoria e idempotência para manter os serviços transacionais e o corte já adotados.

Banco, mídia e temporários usados nos testes foram descartáveis em:

`C:\desenvolvimento\Contruct\.tmp-financeiro-etapa3-test`

Baseline antes da implementação: `manage.py test financeiro compras.tests.test_services compras.tests.test_concorrencia --verbosity 1` — 48/48, OK, 67,299 s.

## Evidência TDD

O primeiro teste funcional foi executado antes da implementação:

```text
Found 9 test(s).
FAILED (failures=8, errors=1)
```

As falhas eram esperadas: os três modelos e os serviços de contas a pagar ainda não existiam. Após os modelos, migração, serviços, views e interface, o conjunto novo ficou verde: 12/12 testes de serviço e 3/3 testes de view.

## Implementação entregue

- `compras/services.py`: resumo transacional de um `PedidoCompra` elegível, sem acesso direto do Financeiro aos detalhes de recebimento.
- `financeiro/models.py` e `financeiro/migrations/0003_contapagar_pagamentocontapagar_and_more.py`: `ContaPagar`, `PagamentoContaPagar` e `EstornoPagamentoContaPagar`, com valores `Decimal`, origem protegida, histórico e chaves/assinaturas de idempotência.
- `financeiro/services.py`: criação avulsa e por compra, listagem aberta/vencida/paga, baixa integral, estorno com reabertura, auditoria, permissão de gerente/superusuário, corte inclusivo e rollback transacional. Retentativas limitadas tratam apenas contenção SQLite recuperável.
- `financeiro/views.py`, `financeiro/urls.py`, templates e CSS: painel, cadastro de despesa, geração por compra recebida, baixa e estorno. As views são apenas orquestração; as escritas passam pelos serviços.
- `financeiro/tests/test_contas_pagar.py` e `financeiro/tests/test_views.py`: aceite de origem única, parcelas, concorrência, idempotência, rollback, corte, permissão e interface.

Diff do commit de implementação: 11 arquivos, 1.768 linhas adicionadas e 5 removidas. A documentação deste relatório e a atualização do checkbox do PRD serão registradas em commit separado.

## Comandos e resultados

| Verificação | Comando | Resultado |
|---|---|---|
| Focado final | `venv\\Scripts\\python.exe manage.py test financeiro.tests.test_contas_pagar financeiro.tests.test_services financeiro.tests.test_views compras.tests.test_services compras.tests.test_concorrencia --verbosity 1` | PASS — 63/63, 87,518 s |
| Recorte serial | `venv\\Scripts\\python.exe manage.py test caixa.tests.test_corte caixa.tests.test_fechamento caixa.tests.test_finalizacao caixa.tests.test_fluxo_integrado caixa.tests.test_interface caixa.tests.test_migracao_legado caixa.tests.test_movimentos caixa.tests.test_pagamentos caixa.tests.test_restituicoes caixa.tests.test_sessoes compras.tests.test_compatibilidade compras.tests.test_interface compras.tests.test_services estoque.test_vendas estoque.test_reversoes usuarios.test_dashboard financeiro.tests --parallel 1 --verbosity 1` | PASS — 196/196, 85,598 s |
| Suíte ampla | `venv\\Scripts\\python.exe manage.py test --parallel 1 --verbosity 1` | PASS — 469/469, 265,156 s |
| Django check | `venv\\Scripts\\python.exe manage.py check` | PASS — no issues |
| Drift de migrações | `venv\\Scripts\\python.exe manage.py makemigrations --check --dry-run` | PASS — no changes detected |
| Ruff | `venv\\Scripts\\ruff.exe check compras\\services.py financeiro\\models.py financeiro\\services.py financeiro\\views.py financeiro\\urls.py financeiro\\tests\\test_contas_pagar.py financeiro\\tests\\test_views.py` | PASS — all checks passed |
| mypy | `venv\\Scripts\\mypy.exe compras\\services.py financeiro\\models.py financeiro\\services.py financeiro\\views.py` | PASS — no issues in 4 source files |
| Higiene do diff | `git diff --check` | PASS — somente avisos de normalização LF/CRLF do checkout |

## Critérios de aceite

| Critério | Veredito | Evidência |
|---|---|---|
| Despesa avulsa não reduz o saldo; abertas, vencidas e pagas são listadas | PASS | Teste de obrigação de R$ 120 com conta em R$ 500; saldo permanece R$ 500 antes da baixa e o painel separa as três situações. |
| Pedido com duas entregas só gera obrigação após recebimento completo | PASS | Teste `test_purchase_obligation_only_becomes_available_after_complete_receipt`; primeira parcela rejeitada, segunda torna o pedido elegível. |
| Uma única obrigação por `PedidoCompra`, inclusive chaves diferentes e concorrência | PASS | Restrição única no banco, replay equivalente, conflito por segunda chave e duas conexões SQLite independentes; contagem final igual a 1. |
| Baixa integral atômica, replay, conflito de assinatura e segundo pagamento | PASS | Saldo R$ 500 → R$ 380 uma única vez; débito e pagamento na mesma transação; falha simulada após o débito deixa saldo e estado em R$ 500/aberta. |
| Estorno vinculado, reabertura e histórico | PASS | Saldo R$ 380 → R$ 500; obrigação reaberta; débito e crédito permanecem; replay não cria novo estorno; segunda chave é rejeitada; rollback preserva o estado pago. |
| Decimal, corte inclusivo, auditoria e permissões no servidor | PASS | Valores normalizados em `Decimal`; movimento na véspera é rejeitado e no corte é aceito; auditoria é criada na transação; POST direto por vendedor retorna 403 e o serviço também rejeita o ator. |
| Interface automatizada | PASS | 3/3 testes de view, incluindo painel, fluxo gerente e POST direto por vendedor. |
| Validação visual desktop/mobile | HOLD | O navegador disponível retornou `ERR_CONNECTION_TIMED_OUT` para o servidor descartável em `127.0.0.1:15023` e `localhost:15023`; não há evidência visual suficiente para PASS. |

## Impeccable e HOLDs preservados

- Impeccable `audit`: HOLD — comando indisponível no ambiente; retorno observado: `Unknown command: audit`.
- Impeccable `polish`: HOLD — comando indisponível no ambiente; retorno observado: `Unknown command: polish`.
- O detector estático disponível retornou lista vazia, com aviso de resolução de stylesheet; isso não substitui audit/polish.
- O HOLD histórico de suíte ampla e emulação mobile da Etapa 2 permanece registrado no PRD e no relatório anterior. Nesta execução, a suíte ampla atual foi reexecutada e passou 469/469; o resultado novo não apaga o registro histórico.

## Estado para auditoria

O candidato de código está congelado no commit `8115069`, sem push, merge ou implantação. O commit documental seguinte conterá somente este relatório e a atualização do checkbox da Etapa 3 no PRD. A Etapa 4 — fechamento mensal — e a integração na `main` continuam fora do escopo.
