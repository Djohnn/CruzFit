# Financeiro Etapa 2 — Recebimentos: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar Pix confirmado e cartão a receber ao Financeiro, preservando o Caixa, os eventos de Vendas, o corte inclusivo, a idempotência e os estornos vinculados.

**Architecture:** Cada pagamento de cartão do Caixa terá um `RecebivelCartao` único. Uma `DepositoCartao` agrupa a liquidação manual e suas linhas `LiquidacaoCartao`, com uma única entrada líquida no livro Financeiro. Um `RecebimentoPixFinanceiro` cria o crédito ligado ao `PagamentoVenda` original. Restituições do Caixa acionam `EstornoRecebimentoCartao`: antes da liquidação cancelam o recebível; depois da liquidação criam débito vinculado ao pagamento restituído.

**Tech Stack:** Django 5, SQLite descartável, Django ORM, `Decimal`, serviços transacionais, templates server-rendered e `unittest.mock` apenas para rollback simulado.

---

### Task 1: Modelar recebíveis, liquidações, Pix e estornos

**Files:**
- Modify: `financeiro/models.py`
- Create: `financeiro/migrations/0002_depositocartao_recebimentopixfinanceiro_and_more.py`
- Test: `financeiro/tests/test_services.py`

- [x] Escrever testes RED para Pix, recebível de cartão, liquidação por parcela e estorno antes/depois da liquidação.
- [x] Implementar os modelos com relações protegidas, `DecimalField`, constraints e chaves únicas por pagamento de origem.
- [x] Gerar a migração e verificar `makemigrations --check --dry-run`.

### Task 2: Implementar serviços financeiros transacionais

**Files:**
- Modify: `financeiro/services.py`
- Modify: `financeiro/queries.py`
- Test: `financeiro/tests/test_services.py`

- [x] Implementar registro idempotente dos recebíveis de cartão.
- [x] Implementar confirmação Pix com data de corte inclusiva e proteção por pagamento de origem.
- [x] Implementar liquidação em lote com bruto, taxas, líquido, conta, data e referência; impedir parcela já liquidada.
- [x] Implementar cancelamento antes da liquidação e débito vinculado após liquidação.
- [x] Cobrir conflitos, concorrência SQLite, pagamento misto, rollback de lote e permissões.

### Task 3: Integrar Caixa e Vendas sem alterar dinheiro físico

**Files:**
- Modify: `caixa/services.py`
- Test: `caixa/tests/test_pagamentos.py`, `financeiro/tests/test_services.py`

- [x] Criar recebíveis de cartão durante o recebimento do Caixa.
- [x] Acionar o cancelamento financeiro nos eventos de reversão e o débito financeiro nas restituições efetivadas.
- [x] Demonstrar que dinheiro permanece no Caixa e que Pix/cartão só afetam o Financeiro pelos serviços próprios.

### Task 4: Entregar interface mínima

**Files:**
- Modify: `financeiro/views.py`, `financeiro/urls.py`, `financeiro/queries.py`
- Modify: `financeiro/templates/financeiro/painel.html`, `financeiro/templates/financeiro/detalhe.html`
- Create: `financeiro/templates/financeiro/recebimento_form.html`
- Test: `financeiro/tests/test_views.py`

- [x] Exibir recebíveis pendentes e links para confirmar Pix/liquidar cartão.
- [x] Exigir gerente/superusuário no servidor e manter vendedor em `403`.
- [ ] Validar fluxo em desktop e mobile no banco descartável. (Desktop PASS; emulação de viewport mobile indisponível nesta sessão.)

### Task 5: Verificação, relatório e congelamento

**Files:**
- Modify: `docs/superpowers/plans/2026-11-06-financeiro-fechamento-mensal-prd.md`
- Modify: `docs/superpowers/plans/2026-11-06-financeiro-etapas-0-1-relatorio.md`

- [x] Rodar focados, recorte serial, Django check, drift de migrações, Ruff, mypy e `git diff --check`.
- [x] Registrar suíte ampla e Impeccable separadamente, mantendo os HOLDs anteriores.
- [x] Atualizar apenas o checkbox da Etapa 2 com evidência e congelar o candidato sem merge/push.
