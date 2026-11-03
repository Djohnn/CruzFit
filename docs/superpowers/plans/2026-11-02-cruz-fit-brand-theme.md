# Cruz Fit Brand Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aplicar a paleta preta e dourada da marca Cruz Fit em todas as telas existentes e corrigir os problemas de clareza e acessibilidade apontados no critique da tela de login.

**Architecture:** A paleta será centralizada nos arquivos CSS carregados pelo template base, usando tokens CSS para superfícies, texto, ações, foco e estados sem remover as convenções semânticas de sucesso, aviso e erro. A imagem fornecida será adicionada ao diretório de arquivos estáticos e exibida no shell global; o login receberá apenas melhorias de estrutura, mensagens e navegação já existentes.

**Tech Stack:** Django 5, templates Django, Bootstrap 4.6, CSS estático, Django TestCase.

---

### Task 1: Fixar o contrato de marca e login em testes

**Files:**
- Modify: `test_startup.py`

- [ ] **Step 1: Add failing assertions for the approved brand contract**

Adicionar testes que leiam os templates/CSS reais e verifiquem: logo estática Cruz Fit no `base.html`, ausência dos links-placeholder `Disabled` e `href="#"`, tokens preto/dourado no CSS global, labels associadas por `for`/`id`, `required`, `autocomplete`, botão textual `Entrar` e mensagens de login renderizadas no próprio formulário.

- [ ] **Step 2: Run the focused tests and confirm the expected failures**

Run: `venv\Scripts\python.exe manage.py test test_startup.BrandAndLoginContractTests --verbosity 2`

Expected: FAIL porque a base ainda contém `href="#"`/`Disabled`, não possui logo estática nem tokens Cruz Fit, e o login ainda usa o submit sem texto e retorna `HttpResponse` cru.

### Task 2: Add the supplied Cruz Fit asset and global theme

**Files:**
- Create: `templates/static/usuarios/img/cruz-fit-logo.jpeg`
- Modify: `templates/base.html`
- Modify: `templates/static/usuarios/css/css.css`

- [ ] **Step 1: Copy the supplied logo into Django static files**

Copiar `C:\Users\johnn\Downloads\WhatsApp Image 2026-10-22 at 19.39.51.jpeg` para `templates/static/usuarios/img/cruz-fit-logo.jpeg`, preservando o arquivo original e usando `{% static %}` no template.

- [ ] **Step 2: Apply semantic Cruz Fit tokens and Bootstrap overrides**

Definir tokens para preto, superfícies elevadas, dourado primário/secundário, texto e estados. Aplicar os tokens a `body.fundo`, `.bg-nav`, links, `.form-control`, foco, `.btn-success`, `.btn-primary`, badges, tabelas, alertas, seleção de texto e scrollbar. Manter vermelho para ações destrutivas e textos/ícones de estado junto da cor.

- [ ] **Step 3: Update the shared shell without changing business routes**

Definir título padrão “Cruz Fit”, renderizar a logo com alt “Cruz Fit”, trocar o link-placeholder por `{% url 'login' %}` e remover o item `Disabled`. Manter o bloco de navegação colapsável e os scripts já existentes.

### Task 3: Apply the approved login structure and behavior

**Files:**
- Modify: `usuarios/templates/login.html`
- Modify: `usuarios/views.py`

- [ ] **Step 1: Implement the login markup from the approved prototype**

Manter `action`, `method="POST"`, `{% csrf_token %}`, nomes `email`/`senha` e o fluxo existente. Adicionar heading “Entrar no Cruz Fit”, labels `for`, IDs, `required`, `autocomplete`, placeholders corrigidos, botão textual “Entrar”, área de mensagens acessível e formulário com classe de layout.

- [ ] **Step 2: Keep authentication feedback in the form context**

No POST inválido, preservar o e-mail e renderizar `login.html` com mensagem de erro e status 401. No sucesso, autenticar e redirecionar para a rota existente `add_produto`; manter a resposta GET de usuário já autenticado compatível com o teste de regressão atual.

- [ ] **Step 3: Run the focused login tests**

Run: `venv\Scripts\python.exe manage.py test test_startup.BrandAndLoginContractTests --verbosity 2`

Expected: PASS.

### Task 4: Extend the same palette to inventory and vendor screens

**Files:**
- Modify: `templates/static/estoque/css/css.css`
- Modify: `estoque/templates/add_produto.html`
- Modify: `usuarios/templates/cadastrar_vendedor.html`

- [ ] **Step 1: Map page-specific surfaces to the shared tokens**

Atualizar `.bg-card`, `.bg-hr` e tabelas/cards para usar os tokens da base, sem trocar URLs, nomes de campos, métodos ou ações existentes.

- [ ] **Step 2: Replace ambiguous action styling and copy only where the existing action is unchanged**

Manter as ações `Enviar`, `Filtrar`, `Acessar` e `Excluir`, mas permitir que os overrides da paleta as apresentem de modo consistente. Corrigir apenas placeholders/textos de senha quando já fizerem parte dos controles existentes.

### Task 5: Verify the complete system

**Files:**
- No new product files.

- [ ] **Step 1: Run Django checks and migration check**

Run: `venv\Scripts\python.exe manage.py check`

Run: `venv\Scripts\python.exe manage.py makemigrations --check --dry-run`

- [ ] **Step 2: Run the full regression suite**

Run: `venv\Scripts\python.exe manage.py test --verbosity 2`

- [ ] **Step 3: Run the Impeccable detector on every real template and stylesheet**

Run: `.agents\skills\impeccable\scripts\impeccable.cmd detect --json templates/base.html usuarios/templates/login.html usuarios/templates/cadastrar_vendedor.html estoque/templates/add_produto.html estoque/templates/produto.html templates/static/usuarios/css/css.css templates/static/estoque/css/css.css`

Registrar qualquer aviso de resolução de `{% static %}` separadamente de findings reais.

- [ ] **Step 4: Verify the live login route and brand asset**

Confirmar HTTP 200 em `http://127.0.0.1:15001/auth/login/`, presença de “Cruz Fit”, ausência de “MECAJATO”/“Disabled”, carregamento do `/static/usuarios/img/cruz-fit-logo.jpeg` e renderização dos campos/CTA aprovados.
