# Financeiro Etapa 3 — Contas a pagar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement despesas avulsas, obrigações únicas por `PedidoCompra` totalmente recebido, baixa financeira atômica e estorno que reabre a obrigação.

**Architecture:** O domínio permanece no app `financeiro`, com modelos próprios para obrigação, pagamento e estorno. A origem de compra é um `PedidoCompra` protegido e único; o resumo da origem é obtido por serviço de `compras`. Views Django ficam finas e chamam serviços transacionais com permissão, auditoria, assinatura e idempotência.

**Tech Stack:** Django 5.0.6, SQLite de teste em arquivo, Django `TestCase`/`TransactionTestCase`, templates Django, `Decimal`, Ruff e mypy.

---

### Task 1: Especificar os casos de aceite em testes vermelhos

**Files:**
- Create: `financeiro/tests/test_contas_pagar.py`
- Modify: `compras/tests/test_services.py` only if the new source-summary contract needs a focused Compras test

- [ ] **Step 1: Escrever cenários Given/When/Then para despesas e origem de compra**

Adicionar testes que expressem: despesa avulsa aberta/vencida não altera saldo; pedido com primeiro recebimento parcial não é elegível; segundo recebimento completa o pedido; geração da obrigação usa o pedido completo; replay retorna `created=False`; chave diferente no mesmo pedido não duplica; pagamento e estorno preservam saldo/histórico.

- [ ] **Step 2: Escrever o teste concorrente com duas conexões SQLite independentes**

Usar `TransactionTestCase`, cópia de banco em arquivo temporário, `override_settings(DATABASES=...)`, `close_old_connections()` em cada worker e duas chamadas sincronizadas de criação de obrigação para o mesmo `PedidoCompra`, com chaves diferentes. O teste deve exigir uma única obrigação e uma única auditoria financeira, aceitando uma criação e uma resposta idempotente/conflito de origem.

- [ ] **Step 3: Rodar somente os testes novos para comprovar RED**

Executar:

```powershell
$env:TEMP='C:\desenvolvimento\Contruct\.tmp-financeiro-etapa3-test'; $env:TMP=$env:TEMP; $env:TMPDIR=$env:TEMP
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test financeiro.tests.test_contas_pagar --verbosity 2
```

Esperado: falha por modelos/serviços/rotas ainda inexistentes, sem corrigir o teste para mascarar a ausência da funcionalidade. Guardar a saída RED no relatório.

### Task 2: Criar os modelos e a migração

**Files:**
- Modify: `financeiro/models.py`
- Create: `financeiro/migrations/0003_contaspagar_pagamentocontapagar_estornopagamentocontapagar.py` via `makemigrations`

- [ ] **Step 1: Adicionar `ContaPagar`**

Usar `DecimalField` positivo, `categoria`, `descricao`, `data_vencimento`, `situacao` (`ABERTA`, `PAGA`), `origem_pedido` `OneToOneField('compras.PedidoCompra', null=True, blank=True, on_delete=PROTECT)`, responsável, motivo, chave única, assinatura e timestamp. A constraint de origem única será reforçada no banco pelo `OneToOneField` e a criação avulsa manterá origem nula.

- [ ] **Step 2: Adicionar `PagamentoContaPagar` e `EstornoPagamentoContaPagar`**

`PagamentoContaPagar` terá `OneToOneField` para a obrigação, conta, débito `OneToOneField(LancamentoFinanceiro)`, data efetiva, valor, responsável, motivo, chave única e assinatura. `EstornoPagamentoContaPagar` terá `OneToOneField` para o pagamento, reversão `OneToOneField(LancamentoFinanceiro)`, responsável, motivo, chave única e assinatura. Todas as relações de histórico usarão `PROTECT`.

- [ ] **Step 3: Gerar e verificar a migração**

Executar `makemigrations financeiro`, revisar o arquivo gerado e rodar `migrate` somente no banco temporário. O teste de migração deve preservar o banco real intocado.

### Task 3: Expor o resumo de pedido recebido pelo serviço de Compras

**Files:**
- Modify: `compras/services.py`
- Modify: `compras/tests/test_services.py`

- [ ] **Step 1: Adicionar teste do contrato de origem**

O serviço deve retornar apenas pedido em estado `RECEBIDO`, `pedido.pk`, `pedido.public_id`, fornecedor e `total_contratado` em `Decimal`; estado parcial ou não confirmado deve lançar `PurchaseOperationError`. O teste deve provar que dois recebimentos parciais preservam o total e só o segundo, ao completar as quantidades, torna o pedido elegível.

- [ ] **Step 2: Implementar o serviço de leitura transacional**

Criar um DTO imutável e uma função de `compras.services` que bloqueie o `PedidoCompra` quando chamada dentro de transação, valide `RECEBIDO` e calcule o total sem acessar modelos de Compras diretamente na view Financeira.

- [ ] **Step 3: Rodar os testes de Compras e o RED da Etapa 3**

Executar `compras.tests.test_services` e `financeiro.tests.test_contas_pagar`; a parte de origem deve passar, enquanto os serviços Financeiros ainda permanecem RED até a Task 4.

### Task 4: Implementar serviços transacionais de contas a pagar

**Files:**
- Modify: `financeiro/services.py`
- Modify: `financeiro/queries.py` if a query de status precisar ser compartilhada
- Modify: `financeiro/tests/test_contas_pagar.py`

- [ ] **Step 1: Implementar criação de despesa avulsa**

Criar `criar_conta_pagar` com descrição, categoria, valor, vencimento, motivo e chave. Validar gerente/superusuário dentro do serviço, normalizar `Decimal`, assinar todos os dados, rejeitar chave conflitante, gravar auditoria e não criar `LancamentoFinanceiro`.

- [ ] **Step 2: Implementar geração por `PedidoCompra`**

Criar `criar_conta_pagar_de_compra` usando `compras.services` dentro de `transaction.atomic()`, bloquear o pedido, exigir estado `RECEBIDO`, validar que o valor confirmado é exatamente o total contratado, salvar `origem_pedido`, e tratar `IntegrityError`/lock com replay ou erro de origem já utilizada. Uma chamada posterior com outra chave deve retornar erro sem escrever.

- [ ] **Step 3: Implementar listagem de aberta, vencida e paga**

Adicionar query determinística que classifique `ABERTA` com vencimento futuro/igual como aberta, `ABERTA` vencida como vencida e `PAGA` como paga. A situação persistida continua sendo apenas aberta/paga; vencida é uma classificação de consulta.

- [ ] **Step 4: Implementar baixa integral**

Criar `pagar_conta_pagar` para exigir obrigação aberta, valor igual ao devido, conta ativa e data efetiva no corte ou depois. Dentro da mesma transação, bloquear obrigação e conta, criar débito e pagamento, atualizar situação para `PAGA`, registrar auditoria e retornar o pagamento existente em replay equivalente. Uma segunda chave após pagamento deve ser rejeitada sem alterações.

- [ ] **Step 5: Implementar estorno do pagamento**

Criar `estornar_pagamento_conta_pagar` para bloquear pagamento/obrigação/conta, validar data de movimento, criar lançamento de crédito vinculado ao débito original, criar `EstornoPagamentoContaPagar`, reabrir a obrigação e auditar tudo em uma transação. Replay equivalente retorna o estorno original; outra chave rejeita segundo estorno. Inserir um ponto de falha testável antes da conclusão e confirmar rollback integral.

- [ ] **Step 6: Rodar GREEN dos serviços**

Reexecutar `financeiro.tests.test_contas_pagar` e os testes financeiros anteriores. O aceite mínimo deve mostrar saldo inicial de R$ 500, obrigação sem alteração do saldo, pagamento em R$ 380, replay sem novo débito, estorno em R$ 500 e obrigação reaberta.

### Task 5: Implementar a interface mínima de contas a pagar

**Files:**
- Modify: `financeiro/views.py`
- Modify: `financeiro/urls.py`
- Modify: `financeiro/templates/financeiro/painel.html`
- Create or modify: `financeiro/templates/financeiro/conta_pagar_form.html`, `financeiro/templates/financeiro/conta_pagar_detalhe.html`
- Modify: `templates/static/financeiro/css/financeiro.css`
- Modify: `financeiro/tests/test_views.py`

- [ ] **Step 1: Adicionar rotas protegidas por gerente**

Criar rotas para nova despesa, gerar obrigação de pedido, pagar e estornar. Cada POST usará CSRF, `gerente_required` e serviço correspondente; vendedor receberá `403` em acesso direto.

- [ ] **Step 2: Exibir filtros e ações no painel**

Adicionar seções para abertas, vencidas e pagas, com valor, vencimento, origem, situação e ações condicionais. O painel deve manter o saldo e receita operacional inalterados até a baixa.

- [ ] **Step 3: Adicionar testes HTTP**

Cobrir renderização, POST de gerente para despesa/pagamento/estorno, geração após compra completa e bloqueio de vendedor. Os testes devem verificar mensagens, redirecionamento, estado e contagens.

- [ ] **Step 4: Validar desktop/mobile**

Subir apenas o runtime descartável, abrir painel e formulários em viewport desktop e 390×844. Registrar evidência; se navegador não estiver disponível, manter HOLD separado do Impeccable.

### Task 6: Executar verificações e atualizar evidências

**Files:**
- Modify: `docs/superpowers/plans/2026-11-06-financeiro-fechamento-mensal-prd.md`
- Create: `docs/superpowers/plans/2026-11-07-financeiro-etapa3-relatorio.md`

- [ ] **Step 1: Rodar testes focados, concorrência e recorte serial**

Registrar comandos completos, contagens e saída real. Incluir comparação com o baseline de 48 testes e separar falhas pré-existentes.

- [ ] **Step 2: Rodar checks do projeto**

Executar `manage.py check`, `makemigrations --check --dry-run`, Ruff, mypy e `git diff --check`. Usar banco/mídia/temporários descartáveis e não tocar `db.sqlite3` real.

- [ ] **Step 3: Registrar HOLDs separados**

Manter a suíte ampla e o Impeccable como critérios independentes, com evidência do comando indisponível ou da falha. QA visual mobile terá veredito próprio.

- [ ] **Step 4: Atualizar somente os checkboxes comprovados do PRD**

Marcar Etapa 3 apenas se todos os critérios técnicos tiverem evidência; deixar Etapa 4 e Etapa 5 inalteradas. O relatório final deve conter branch, worktree, SHA de base, novo SHA, arquivos, comandos, resultados e PASS/FAIL/HOLD por critério.
