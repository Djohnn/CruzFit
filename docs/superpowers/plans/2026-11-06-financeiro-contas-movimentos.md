# Financeiro — Contas e movimentos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Etapas 0 e 1 do Financeiro do Cruz Fit on base `222c45f`, with dated opening balances, append-only financial movements, transactional transfers, reversals, idempotency, audit, manager authorization, and responsive server-rendered UI.

**Architecture:** Add a bounded `financeiro` Django app. Concrete business models (`AporteFinanceiro`, `TransferenciaFinanceira`, `EstornoFinanceiro`) own their operation semantics and create append-only `LancamentoFinanceiro` ledger rows inside services. Django function views expose protected POST actions because this repository has no DRF/ViewSets; all business writes remain in services.

**Tech Stack:** Django 5, SQLite disposable test database, Python `Decimal`, Django templates, existing Bootstrap/static shell, `unittest.mock` only for the induced transfer-failure test, Ruff, mypy.

---

### Task 1: Freeze Etapa 0 baseline and disposable validation setup

**Files:**
- Create: `validation_financeiro_settings.py` (temporary, ignored; remove before final handoff)
- Create: `graphify-out/test-tmp/financeiro/` runtime artifacts only
- Test: existing `caixa/tests`, `compras/tests`, `vendas/tests`

- [ ] **Step 1: Confirm provenance and clean branch state**

Run from `C:\desenvolvimento\Contruct\.worktrees\financeiro`:

```powershell
git branch --show-current
git rev-parse HEAD
git status --short --branch
git show --no-patch --format=fuller HEAD
```

Expected: branch `codex/financeiro`, HEAD `222c45fb3c2e3413637a7e9a40d4526e61d78078`, only the committed design/plan files afterward.

- [ ] **Step 2: Run the focused baseline on the disposable test database**

Run:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'validation_financeiro_settings'
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test caixa.tests compras.tests vendas.tests usuarios.test_dashboard --verbosity 1
```

Expected: existing focused suite passes; record the raw runner output and duration in the final report. If a pre-existing failure appears, classify it before implementation and do not alter unrelated code.

- [ ] **Step 3: Record the cutover contract**

Document in the final report: `data_corte_financeiro` is inclusive, the opening balance is the balance at the start of that date, dates before it are rejected without writes, and operational revenue queries exclude opening balances, aportes, transfers, and reversals.

### Task 2: Add failing model/service tests for accounts and cutover

**Files:**
- Create: `financeiro/__init__.py`
- Create: `financeiro/tests/__init__.py`
- Create: `financeiro/tests/test_services.py`
- Create: `financeiro/tests/test_views.py`

- [ ] **Step 1: Write the RED tests**

Cover these Gherkin cases in `TransactionTestCase`/`TestCase`:

```gherkin
Given a gerente and a new account with balance 500.00 and cutover 2026-11-06
When the account is created through the service
Then its available balance is 500.00 and operational revenue is 0.00

Given an account with cutover 2026-11-06
When an aporte is requested for 2026-11-05
Then the service raises a clear date error and creates no operation, ledger, or audit row

Given an account with cutover 2026-11-06
When an aporte is requested for 2026-11-06
Then the credit is accepted and the balance increases by the exact Decimal amount

Given a vendedor
When the vendedor posts directly to each financial write URL
Then every response is 403 and no financial row is created
```

- [ ] **Step 2: Run the focused RED test**

Run:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'validation_financeiro_settings'
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test financeiro.tests.test_services financeiro.tests.test_views --verbosity 2
```

Expected: import/model/URL failures because `financeiro` is not implemented yet. Fix only test setup errors until the failure represents the missing feature.

### Task 3: Implement account, ledger, concrete operation, and audit models

**Files:**
- Create: `financeiro/apps.py`
- Create: `financeiro/models.py`
- Create: `financeiro/migrations/0001_initial.py` via `makemigrations`
- Modify: `cruzfit/settings.py`

- [ ] **Step 1: Implement the model contract**

Use `DecimalField(max_digits=14, decimal_places=2)`, positive check constraints, protected foreign keys, and unique idempotency keys. `ContaFinanceira` stores `nome`, `saldo_inicial`, `data_corte_financeiro`, `ativo`, creator, reason, key, signature, and timestamps. `LancamentoFinanceiro` stores `conta`, `direcao` (`CREDITO`/`DEBITO`), amount, business date, description, reason, responsible user, and nullable `estorno_de` self-reference. Each concrete operation owns a one-to-one relation to its ledger row(s). `TransferenciaFinanceira` owns explicit `debito` and `credito` rows. `EventoAuditoriaFinanceiro` stores action, actor, reason, key, signature, and JSON payload.

- [ ] **Step 2: Register the app and generate migration**

Add `'financeiro'` to `INSTALLED_APPS`, then run:

```powershell
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py makemigrations financeiro
```

Expected: one initial migration containing only the new app models.

- [ ] **Step 3: Run the account/cutover tests GREEN**

Run the Task 2 command. Expected: account creation, Decimal balance, inclusive cutover, audit and seller authorization tests pass.

### Task 4: Implement transactional services with idempotency and audit

**Files:**
- Create: `financeiro/services.py`
- Modify: `financeiro/tests/test_services.py`

- [ ] **Step 1: Implement shared service validation**

Provide `_require_manager`, `_money`, `_reason`, `_key`, `_business_date`, `_signature`, and `_replay` helpers. `_business_date` must reject `movement_date < account.data_corte_financeiro` with `FinanceOperationError('A data do movimento não pode ser anterior à data de corte financeiro.')` before entering the write block.

- [ ] **Step 2: Implement `criar_conta_financeira` and `registrar_aporte`**

Both require active manager/superuser, reason, and idempotency key. A replay with matching actor/signature returns `created=False`; a conflicting payload raises `FinanceOperationError`. Aporte creates exactly one credit ledger row and one audit event in one atomic block.

- [ ] **Step 3: Add RED/GREEN tests for transfer, replay, and rollback**

Add:

```gherkin
Given account A has 500.00 and account B has 100.00
When gerente transfers 80.00 on the cutover date
Then A is 420.00, B is 180.00, the consolidated total is unchanged, and operational revenue is unchanged

Given a completed transfer with key k
When the same actor repeats the same payload with key k
Then the existing transfer is returned and no second ledger/audit rows exist

Given key k was used with another amount or account
When the transfer is retried
Then the service rejects it and balances stay unchanged

Given the debit row succeeds and credit-row creation raises
When the transfer is executed
Then the operation, debit, credit, and audit rows are all rolled back
```

For the induced failure, patch the second `LancamentoFinanceiro.objects.create` call only; assert the exception and all related counts/balances are unchanged.

- [ ] **Step 4: Implement `transferir_entre_contas`**

Inside one `transaction.atomic()`, lock/read both accounts consistently, create the operation, debit and credit rows, then audit. The operation must validate distinct active accounts and preserve the total balance. Catch uniqueness races only to return an equivalent replay; never swallow a non-equivalent conflict.

- [ ] **Step 5: Implement `estornar_lancamento`**

Require a manager, reason, key, eligible original ledger row, and a date on/after the account cutover. Create `EstornoFinanceiro` plus one inverse ledger row atomically. Keep the original immutable and reject a second reversal of the same original. Add tests proving the history contains both original and reversal.

- [ ] **Step 6: Run service tests GREEN**

Run:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'validation_financeiro_settings'
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test financeiro.tests.test_services --verbosity 2
```

Expected: all service scenarios pass with raw output captured for the final report.

### Task 5: Add queries, URLs, protected views, and responsive UI

**Files:**
- Create: `financeiro/queries.py`
- Create: `financeiro/views.py`
- Create: `financeiro/urls.py`
- Create: `financeiro/templates/financeiro/painel.html`
- Create: `financeiro/templates/financeiro/conta_form.html`
- Create: `financeiro/templates/financeiro/movimento_form.html`
- Create: `templates/static/financeiro/css/financeiro.css`
- Modify: `cruzfit/urls.py`
- Modify: `templates/base.html`

- [ ] **Step 1: Implement read queries**

Expose account list with calculated Decimal balances, account detail with append-only history, and a zero operational-revenue summary explicitly labeled for Etapa 1. Keep query functions free of writes.

- [ ] **Step 2: Implement protected views and POST action routes**

Use the existing login/manager pattern. Required routes:

```text
/financeiro/                         painel
/financeiro/contas/nova/             criar_conta
/financeiro/contas/<int:pk>/         detalhe
/financeiro/aportes/                 aporte
/financeiro/transferencias/          transferencia
/financeiro/estornos/<int:pk>/       estorno
```

All POSTs carry CSRF, reason, business date and generated idempotency key. Views call services only and convert service errors to form messages/status 400. A vendedor receives 403 before form rendering for write actions.

- [ ] **Step 3: Implement the minimum responsive interface**

Use the existing shell, high-contrast finance surface, semantic headings, accessible labels, focus states, table overflow, and a single-column mobile layout at `max-width: 767.98px`. Show account cards, balance, date cutover, history rows, and clear separation between available balance and operational revenue.

- [ ] **Step 4: Run view tests GREEN**

Run:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'validation_financeiro_settings'
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test financeiro.tests.test_views --verbosity 2
```

Expected: authenticated manager can render/use the interface and seller direct POSTs are denied.

### Task 6: Verify UI and full quality gates

**Files:**
- Modify: `docs/superpowers/plans/2026-11-06-financeiro-contas-movimentos.md` (check completed steps only)
- Modify: `docs/superpowers/plans/2026-11-06-financeiro-fechamento-mensal-prd.md` (only Etapas 0/1 checkboxes with evidence)
- Create: `docs/superpowers/plans/2026-11-06-financeiro-etapas-0-1-relatorio.md`

- [ ] **Step 1: Run Impeccable context/audit/polish for the changed surface**

Run from the worktree:

```powershell
& 'C:\Users\johnn\.agents\skills\impeccable\scripts\impeccable.cmd' context
& 'C:\Users\johnn\.agents\skills\impeccable\scripts\impeccable.cmd' audit --target financeiro/templates/financeiro/painel.html
& 'C:\Users\johnn\.agents\skills\impeccable\scripts\impeccable.cmd' polish --target financeiro/templates/financeiro/painel.html
```

If the launcher lacks a requested subcommand, record the exact HOLD and continue with direct visual inspection; do not claim the command passed.

- [ ] **Step 2: Run real browser checks in disposable runtime**

Start a local server against disposable SQLite/media, sign in as a synthetic manager, verify desktop rendering and the 390×844 mobile layout, then exercise account creation, transfer and history. Confirm the seller direct POST receives 403. Capture screenshots or browser output and stop the server afterward.

- [ ] **Step 3: Run required automated gates serially**

Run each command separately and retain raw output:

```powershell
$env:DJANGO_SETTINGS_MODULE = 'validation_financeiro_settings'
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test financeiro.tests --verbosity 2
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test caixa compras vendas usuarios.test_dashboard --verbosity 1
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py check
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py makemigrations --check --dry-run
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' -m ruff check .
& 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' -m mypy . --exclude-gitignore
git diff --check
```

- [ ] **Step 4: Review scope and PRD evidence**

Confirm `main`, other worktrees, real `db.sqlite3`, and real `media` were not changed. Update only Etapa 0 and Etapa 1 checkboxes when the corresponding evidence is present. Leave Etapas 2–5 unchecked. Report branch, worktree, base SHA, files, commands, raw results, cutover proof, desktop/mobile status, and PASS/FAIL/HOLD for every minimum acceptance criterion.

- [ ] **Step 5: Commit the candidate without push/merge**

After all verification output is fresh:

```powershell
git add financeiro cruzfit/settings.py cruzfit/urls.py templates/base.html templates/static/financeiro docs/superpowers/plans/2026-11-06-financeiro-fechamento-mensal-prd.md docs/superpowers/plans/2026-11-06-financeiro-etapas-0-1-relatorio.md
git commit -m "feat: adicionar contas e movimentos financeiros"
```

Do not push, merge, run client activation, or touch production data.
