# Other Pages Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediar as três páginas auditadas sem trocar as rotas existentes: cadastro/listagem de produtos, detalhe somente leitura e cadastro/administração de vendedores.

**Architecture:** Reutilizar o shell e tokens Cruz Fit já aprovados. Erros de POST/GET voltam por redirect com mensagens Django e dados não sensíveis preservados na sessão, evitando respostas técnicas cruas. O detalhe de produto usa `get_object_or_404`/estado amigável e não simula edição enquanto não existe persistência.

**Tech Stack:** Django 5, templates Django, Bootstrap 4.6, CSS estático, Django TestCase.

---

### Task 1: Characterization tests

**Files:**
- Modify: `test_startup.py`

- [ ] Add failing tests for accessible product/vendor templates, empty states, confirmation, read-only product detail, friendly missing-product response, and message-based validation redirects.
- [ ] Run the focused tests and confirm expected failures before production edits.

### Task 2: Product flows

**Files:**
- Modify: `estoque/views.py`
- Modify: `estoque/templates/add_produto.html`
- Modify: `estoque/templates/produto.html`
- Create: `templates/404.html`

- [ ] Preserve existing routes and POST/CSRF while adding contextual messages, session-backed form preservation, image validation, Cruz Fit watermark, empty/filter states and accessible fields.
- [ ] Make product detail read-only and return a friendly 404 for missing slugs.

### Task 3: Vendor administration

**Files:**
- Modify: `usuarios/views.py`
- Modify: `usuarios/templates/cadastrar_vendedor.html`

- [ ] Preserve existing create/delete endpoints while returning validation errors through messages and preserving typed fields.
- [ ] Add semantic labels, responsive table, empty state and native delete confirmation.

### Task 4: Shared visual polish and verification

**Files:**
- Modify: `templates/static/usuarios/css/css.css`
- Modify: `templates/static/estoque/css/css.css`

- [ ] Add responsive section/form/table states and explicit focus/disabled/invalid styling using existing brand tokens.
- [ ] Run Django checks, migrations check, full tests, detector and live route checks.
