# Cruz Fit Single-User Hardening Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with test-first development. Preserve existing local changes; do not commit, push, publish, or alter the real `db.sqlite3`.

**Goal:** Harden the local-only Cruz Fit workflow for one authenticated gerente, protecting internal pages, preserving stock history, validating data and images, and covering the requested regressions.

**Architecture:** Keep the existing Django views, SQLite database, templates, and rolepermissions compatibility layer. Add narrow decorators and model/form validation at the current boundaries, use a simple per-request idempotency token for duplicate stock submissions, and add audit metadata to stock operations without introducing multi-tenancy or distributed infrastructure.

**Tech Stack:** Django 5.0.6, custom `Users` model, SQLite, Django `TestCase`, Pillow, vanilla templates/CSS.

---

### Task 1: Protect internal routes and make logout POST-only

**Files:**
- Modify: `estoque/views.py`, `estoque/urls.py`, `usuarios/views.py`, `usuarios/urls.py`
- Modify: `templates/base.html`, `usuarios/templates/home.html`
- Test: `test_startup.py`, `estoque/test_barcode_feature.py`

- [ ] Add failing tests for anonymous access to estoque, produto, entrada, baixa, cadastro and modelos de etiqueta; GET logout rejection; POST logout success.
- [ ] Run the focused auth tests and confirm failures identify missing decorators/method handling.
- [ ] Apply `@login_required(login_url='login')` to every internal view, including `produto`; use `@require_GET`/`@require_POST` where each view has one method; implement logout with `auth.logout(request)` and CSRF-backed POST.
- [ ] Replace navigation logout links with POST forms and remove vendor-management links from the primary navigation.
- [ ] Re-run focused auth tests.

### Task 2: Restrict legacy vendor endpoints to the principal administrator

**Files:**
- Modify: `usuarios/views.py`, `usuarios/urls.py`, `usuarios/templates/home.html`, `usuarios/templates/cadastrar_vendedor.html`
- Test: `test_startup.py`

- [ ] Add failing tests proving non-superusers cannot create/delete vendor accounts, the principal cannot delete itself, staff/superusers cannot be deleted, and the last administrative account cannot be deleted.
- [ ] Run the tests and capture the expected failures.
- [ ] Add a small `principal_admin_required` guard based on `is_superuser`; keep old routes only for compatibility and enforce the deletion invariants.
- [ ] Re-run the user-management tests and verify the main navigation no longer exposes the legacy flow.

### Task 3: Enforce product price integrity and safe profit calculation

**Files:**
- Modify: `estoque/models.py`, `estoque/forms.py`, `estoque/migrations/`
- Test: `estoque/test_barcode_feature.py`, `estoque/tests.py`

- [ ] Add failing tests for negative prices, zero/negative stock where applicable, zero purchase price, and `Produto.lucro()` with zero purchase price.
- [ ] Run the tests to confirm the model/form gaps.
- [ ] Convert purchase/sale price fields to compatible `DecimalField`s with non-negative validators and update forms; make `lucro()` return a safe value when purchase price is zero instead of dividing.
- [ ] Generate a data-preserving migration and run `makemigrations --check --dry-run` without touching the real database.
- [ ] Re-run product/model tests against Django’s temporary test database.

### Task 4: Harden stock operations with audit metadata and duplicate-submit protection

**Files:**
- Modify: `estoque/models.py`, `estoque/views.py`, `estoque/templates/registrar_entrada.html`, `estoque/templates/registrar_baixa.html`
- Modify: `estoque/migrations/`
- Test: `estoque/tests.py`, `estoque/test_barcode_feature.py`

- [ ] Add Given/When/Then tests for authenticated entry/withdrawal, insufficient balance, zero/negative quantities, invalid prices, audit user/reason/observation, and repeated identical submission.
- [ ] Run the stock tests and confirm the missing protections fail.
- [ ] Add nullable responsible-user and reason/observation fields to movement headers; preserve `transaction.atomic()` and `select_for_update()`; reject non-positive/non-finite quantities; store a hidden per-form operation key and reject a duplicate key for the same operation/user.
- [ ] Re-run stock tests and verify history remains queryable.

### Task 5: Validate image uploads without breaking Cruz Fit watermarking

**Files:**
- Modify: `estoque/forms.py`, `estoque/views.py`
- Test: `test_startup.py`, `estoque/tests.py`

- [ ] Add failing tests for unsupported type, oversized image, excessive image dimensions/count, and valid image processing with the existing Cruz Fit watermark.
- [ ] Run the image tests and confirm invalid uploads do not return 500.
- [ ] Validate extension/content, byte size, dimensions and maximum count before Pillow processing; keep the current resize and watermark path for accepted images.
- [ ] Re-run image tests with temporary uploaded files only.

### Task 6: Make local development configuration explicit

**Files:**
- Modify: `construct/settings.py`, `README.md` or `LEIA-ME-EXECUCAO.txt`
- Test: `test_startup.py`

- [ ] Add failing settings-contract tests for local hosts and development-only deployment assumptions.
- [ ] Set `ALLOWED_HOSTS` to `localhost`, `127.0.0.1`, and `[::1]`; keep local HTTP/session behavior compatible and document that the server must bind to `127.0.0.1` and is not for internet exposure.
- [ ] Re-run settings tests.

### Task 7: Full regression and quality gates

**Files:** Existing files only.

- [ ] Derive and run the complete requested Given/When/Then suite using Django’s temporary database.
- [ ] Run `graphify --help` and the graph query against the temporary graph path; do not add generated graph files.
- [ ] Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test --verbosity 2`, `git diff --check`, and `git status --short`.
- [ ] Report raw output, unavailable tools, preserved worktree state, and the fact that no commit/push/publication occurred.
