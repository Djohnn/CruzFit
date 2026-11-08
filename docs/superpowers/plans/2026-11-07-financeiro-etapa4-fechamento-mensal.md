# Financeiro Etapa 4 — Fechamento mensal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Implement an auditable monthly financial preview, classification, confirmation, version history and explicit reopening for Cruz Fit.

**Architecture:** A pure preview service reconstructs the end-of-month position from dated financial events instead of current vigente flags. Confirmation stores an immutable version snapshot and per-account balances; reopening ends the current version and records a separate audited event before a new version can be confirmed. A conditional database uniqueness constraint permits only one vigente version per month.

**Tech Stack:** Django 5, SQLite disposable tests, Decimal, transaction.atomic, select_for_update, existing Financeiro services/models, Django templates and existing Financeiro CSS.

---

### Task 1: Write failing acceptance tests

**Files:**
- Create: financeiro/tests/test_fechamento_mensal.py
- Modify: financeiro/tests/test_views.py

- [ ] Step 1: Add the service fixture and Gherkin-derived tests before production code.

Create FechamentoMensalServiceTests(TestCase) with a gerente, vendedor, one account with saldo inicial 500.00 and cutover 2026-11-01, plus helpers that create operational credits, payables, payments, refunds, card receivables, settlements and cancellations through existing services. Add tests named:

    def test_preview_classifies_900_1050_1100_and_keeps_open_payable_visible(self): ...
    def test_preview_reconstructs_payment_and_refund_by_effective_dates(self): ...
    def test_preview_keeps_card_receivable_until_deposit_date(self): ...
    def test_preview_signature_changes_when_same_total_has_different_ledger(self): ...
    def test_confirmation_rejects_stale_preview_without_writing(self): ...
    def test_confirmation_replay_returns_same_version(self): ...
    def test_reopening_preserves_version_and_new_confirmation_increments_version(self): ...
    def test_seller_cannot_confirm_or_reopen(self): ...

The first test must assert Mal for 900, No limite for 1050 and Bem for 1100, with the same open R$ 1,000 obligation present in each preview and coverage independent from payment state.

The historical test must create a R$ 1,000 payable due in November, pay it in December, refund it in January, and assert: November open, December paid, January reopened. Add a card approved in November and deposited in December; November must list it as receivable.

- [ ] Step 2: Add confirmation concurrency and UI regression tests.

Create FechamentoMensalConcurrencyTests(TransactionTestCase) using two independent SQLite connections and a Barrier. Both workers confirm the same month with different keys and the same preview signature. Assert one version, one vigente=True, one successful result and one business conflict/replay result, with no duplicate account snapshots.

Add FechamentoMensalViewTests covering:

    def test_manager_sees_preview_with_separate_coverage_obligations_and_balance(self): ...
    def test_manager_confirms_reopens_and_sees_history(self): ...
    def test_vendor_direct_posts_are_forbidden(self): ...

- [ ] Step 3: Run only the new tests and capture RED.

Run:

    $env:TEMP='C:\desenvolvimento\Contruct\.tmp-financeiro-etapa4-test'
    $env:TMP=$env:TEMP
    $env:TMPDIR=$env:TEMP
    & 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test financeiro.tests.test_fechamento_mensal financeiro.tests.test_views.FechamentoMensalViewTests --verbosity 1

Expected RED: missing FechamentoMensal models/services and missing URLs, not a test collection error.

### Task 2: Add versioned closing models and migration

**Files:**
- Modify: financeiro/models.py
- Create: financeiro/migrations/0005_fechamentomensal_and_more.py

- [ ] Step 1: Add the immutable snapshot models.

Add FechamentoMensal with mes_referencia, versao, situacao, vigente, margem, entradas_operacionais_liquidas, obrigacoes_vencimento_mes, pagamentos_efetivos, obrigacoes_pagas, obrigacoes_abertas, vencidas_anteriores, cartao_a_receber, cobertura, classificacao, assinatura_previa, responsavel, motivo, chave_idempotencia, assinatura, confirmado_em and criado_em.

Add FechamentoMensalConta with fechamento, conta, saldo_inicial, saldo_final and a uniqueness constraint for (fechamento, conta).

Add ReaberturaFechamentoMensal with fechamento, responsavel, motivo, chave_idempotencia, assinatura and criada_em. Use a one-to-one link to allow one explicit reabertura per confirmed version and preserve each reabertura as a separate audited row.

Use choices CONFIRMADO/REABERTO and classifications MAL/NO_LIMITE/BEM. Enforce positive/non-negative monetary constraints and a conditional unique constraint on FechamentoMensal(mes_referencia) where vigente=True.

- [ ] Step 2: Generate and inspect the migration.

Run venv\Scripts\python.exe manage.py makemigrations financeiro, inspect the generated operations, and ensure it contains the conditional unique constraint and protected foreign keys. Do not use the real database.

- [ ] Step 3: Run the new tests.

Run the focused command from Task 1. Expected result: model import failures are gone; service/view assertions remain RED.

### Task 3: Implement historical preview and stable signature

**Files:**
- Modify: financeiro/services.py
- Create or modify: financeiro/queries.py

- [ ] Step 1: Add immutable DTOs and month helpers.

Implement:

    @dataclass(frozen=True)
    class FechamentoContaPreview: ...

    @dataclass(frozen=True)
    class FechamentoMensalPreview: ...

    def periodo_mensal(month: object) -> tuple[date, date]: ...
    def previsualizar_fechamento_mensal(*, month: object, margin: object) -> FechamentoMensalPreview: ...

Normalize the month to its first day and margin to Decimal('0.01'). Reject invalid months or negative margins with FinanceOperationError.

- [ ] Step 2: Implement event-date reconstruction.

For each active account, calculate opening and ending balances from saldo_inicial plus signed LancamentoFinanceiro rows before the month start and through the month end. Use operational ledger relations and data_movimento for net operating entries.

For payables, build a stable event list from each obligation, all historical payments and their linked refunds. Determine the obligation state at month end from events up to that date; do not use only PagamentoContaPagar.vigente. Count obligations by due date, payments by effective payment date, previous overdue items by due date and end-of-month state.

For cards, include approved receivables by data_aprovacao unless cancellation or deposit effective dates are on/before the reference end. Include card settlement entries by deposit date.

- [ ] Step 3: Build the signature from ordered source rows.

Serialize month, margin, account rows, ledger IDs and fields, payable/payment/refund IDs and fields, card IDs and dates, and derived totals in deterministic sorted order. Hash the serialized payload with the existing _signature helper. Include row identity and dates so replacing one ledger while preserving totals changes the signature.

- [ ] Step 4: Run preview tests and confirm GREEN.

Run:

    & 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe' manage.py test financeiro.tests.test_fechamento_mensal.FechamentoMensalServiceTests --verbosity 1

Expected result: historical, classification and signature tests pass; confirmation/reopening tests remain RED.

### Task 4: Implement confirmation and audited reopening

**Files:**
- Modify: financeiro/services.py
- Modify: financeiro/models.py only if transaction fields need adjustment

- [ ] Step 1: Implement confirmation with stale-preview protection.

Implement:

    def confirmar_fechamento_mensal(*, actor, month, margin, preview_signature, reason, idempotency_key) -> FechamentoMensalResult: ...

Require a manager/superuser, reason and idempotency key. Within transaction.atomic(), check an exact-key replay first, lock existing versions for the month, recompute the preview and compare the signature. Reject stale data before creating any snapshot. If a vigente version exists, reject a different operation until explicit reopening. Create the next version number, account snapshots and audit event atomically. Retry only recoverable SQLite lock errors.

- [ ] Step 2: Implement reopening.

Implement:

    def reabrir_fechamento_mensal(*, actor, fechamento, reason, idempotency_key) -> ReaberturaFechamentoMensalResult: ...

Lock the vigente version, return the original reabertura on equivalent replay, reject changed signatures, preserve all confirmed totals/margin/classification, set vigente=False and situacao='REABERTO', create the reabertura row and audit event in one transaction. A different key cannot reopen an already reaberta version.

- [ ] Step 3: Run service tests.

Run the focused Etapa 4 test module. Expected result: confirmation, replay, stale rejection, reabertura and versioning pass.

### Task 5: Add manager interface

**Files:**
- Modify: financeiro/views.py
- Modify: financeiro/urls.py
- Create: financeiro/templates/financeiro/fechamento_form.html
- Create: financeiro/templates/financeiro/fechamento_historico.html
- Modify: financeiro/templates/financeiro/painel.html
- Modify: templates/static/financeiro/css/financeiro.css
- Modify: financeiro/tests/test_views.py

- [ ] Step 1: Add manager-only routes.

Add GET/POST preview/confirmation and POST reopening routes under financeiro/fechamentos/. All write routes use gerente_required and delegate to services; sellers receive 403 on direct POST.

- [ ] Step 2: Render separate indicators.

The page must show separate cards/sections for Cobertura, Situação das obrigações and Saldo disponível, plus per-account opening/ending balances, paid/open/overdue obligations, receivables and version history. An open account must remain visible even when classification is Bem.

- [ ] Step 3: Run view tests.

Run manage.py test financeiro.tests.test_fechamento_mensal.FechamentoMensalViewTests financeiro.tests.test_fechamento_mensal --verbosity 1. Expected: all interface assertions pass.

### Task 6: Validate migration, browser and Impeccable separately

**Files:**
- Create only disposable settings/database files under C:\desenvolvimento\Contruct\.tmp-financeiro-etapa4-test

- [ ] Step 1: Validate migration from a disposable database.

Migrate a temporary SQLite database, create a confirmed version, apply the new migration and verify the version and account snapshots survive. No real database or media path may be touched.

- [ ] Step 2: Validate desktop and mobile UI.

Start the disposable server, open the monthly preview in the browser, confirm a month, reopen it, confirm the next version, and inspect separate coverage/obligation/balance sections at desktop and narrow mobile viewport. If the browser cannot access the local server, record HOLD with the exact error; do not claim visual PASS from automated tests.

- [ ] Step 3: Run Impeccable detector and commands separately.

Run the detector once on changed templates/CSS. Attempt audit and polish independently. Record tool availability/HOLD separately from visual QA; do not update the tool in this scope.

### Task 7: Final verification and audit report

**Files:**
- Modify: docs/superpowers/plans/2026-11-06-financeiro-fechamento-mensal-prd.md only for the Etapa 4 checkbox when evidence supports it
- Create: docs/superpowers/plans/2026-11-07-financeiro-etapa4-relatorio.md

- [ ] Step 1: Run final focused and serial tests.

Run focused Financeiro/Compras tests, the established functional serial slice with --parallel 1, and the complete suite with --parallel 1.

- [ ] Step 2: Run project checks.

Run manage.py check, manage.py makemigrations --check --dry-run, Ruff, mypy and git diff --check.

- [ ] Step 3: Write evidence report.

Record base SHA, implementation/report SHAs, worktree, Graphify copy, disposable paths, RED/GREEN evidence, test counts/times, migration output, browser result, Impeccable result and PASS/FAIL/HOLD per criterion.

- [ ] Step 4: Commit and freeze.

Commit implementation separately from documentation, verify clean status and final git diff --check, and do not push, merge or deploy.
