# Cruz Fit Reauditoria Blockers Implementation Plan

> **For agentic workers:** Execute this plan inline with test-first checkpoints. Do not apply migrations to the real local database without an explicit backup.

**Goal:** Corrigir os bloqueios de migração, login, idempotência, precisão numérica, separação de serviços e qualidade estática encontrados na reauditoria.

**Architecture:** Manter o Cruz Fit local e de usuário único. Views continuarão responsáveis por HTTP, redirecionamentos e mensagens; regras de estoque e escritas de usuários serão centralizadas em `services.py`. Migrações serão verificadas no banco temporário do runner e aplicadas ao banco real somente mediante backup explícito.

**Tech Stack:** Django 5, SQLite local, `django.test.TestCase`, DecimalField, Ruff e mypy.

---

### Task 1: Migração 0011 e documentação operacional

**Files:**
- Modify: `docs/LOCAL_DEVELOPMENT.md`
- Create: `estoque/test_migrations.py`

- [ ] Escrever teste que confirme `estoque.0011_baixaestoque_chave_idempotencia_baixaestoque_motivo_and_more` registrado no `MigrationRecorder` do banco temporário.
- [ ] Executar o teste e confirmar o comportamento no banco temporário.
- [ ] Documentar backup do `db.sqlite3`, `python manage.py migrate` e só depois `runserver 127.0.0.1:8000`.

### Task 2: Login com identificador único

**Files:**
- Modify: `usuarios/views.py`
- Modify: `usuarios/management/commands/criar_conta_cruz_fit.py` se necessário para rejeitar identificador duplicado.
- Test: `estoque/test_single_user_hardening.py`

- [ ] Escrever testes para login por identificador único, nome duplicado rejeitado e ausência de `.first()` na resolução de `first_name`.
- [ ] Remover a consulta ambígua `first_name__iexact(...).first()`.
- [ ] Aceitar login por username único e, por compatibilidade, por `first_name` somente quando houver exatamente um resultado.
- [ ] Exibir erro controlado quando houver zero ou múltiplos candidatos.

### Task 3: Idempotência obrigatória

**Files:**
- Modify: `estoque/services.py`
- Modify: `estoque/views.py`
- Modify: `test_startup.py` para incluir chaves nos POSTs existentes.
- Test: `estoque/test_single_user_hardening.py`

- [ ] Escrever testes de entrada e baixa sem chave, esperando rejeição e nenhuma alteração de estoque.
- [ ] Mover a validação da chave para o serviço e rejeitar chave vazia; UUID só poderá ser gerado para preencher o formulário GET.
- [ ] Preservar teste de repetição da mesma chave sem duplicar histórico.

### Task 4: Quantidades decimais

**Files:**
- Modify: `estoque/models.py`
- Modify: `estoque/views.py`
- Create: migration `estoque/migrations/0012_*.py`
- Test: `estoque/test_single_user_hardening.py`

- [ ] Escrever teste que confirme `Produto`, `ItemEntradaEstoque` e `ItemBaixaEstoque` usam `DecimalField`.
- [ ] Converter quantidades para `DecimalField` com duas casas e ajustar parsing dos serviços.
- [ ] Gerar migração sem aplicar no banco real e verificar `makemigrations --check --dry-run`.

### Task 5: Serviços de estoque e usuários

**Files:**
- Create: `estoque/services.py`
- Create: `usuarios/services.py`
- Modify: `estoque/views.py`
- Modify: `usuarios/views.py`
- Test: `estoque/test_single_user_hardening.py`

- [ ] Escrever testes HTTP que comprovem entrada, baixa, criação e exclusão legada continuam funcionando com as regras atuais.
- [ ] Extrair transações e escritas de estoque para funções de serviço.
- [ ] Extrair criação/exclusão de usuários para funções de serviço.
- [ ] Manter views limitadas a autenticação HTTP, parsing superficial, mensagens e redirects.

### Task 6: Qualidade estática e verificação final

**Files:**
- Create or modify: `mypy.ini` somente se necessário para o projeto local.
- Modify: `requirements-dev.txt` somente se já existir; instalação primária será na `venv`.

- [ ] Instalar Ruff e mypy na `venv` de desenvolvimento.
- [ ] Executar `ruff check .` e `mypy .`.
- [ ] Corrigir todos os erros encontrados.
- [ ] Executar os comandos finais solicitados e registrar o output bruto.
