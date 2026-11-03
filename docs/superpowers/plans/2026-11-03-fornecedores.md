# Fornecedores — Implementation Plan

> Execução direta conforme pedido do usuário e escopo confirmado. Usar executing-plans, django-qa-testes, TDD e Impeccable. Sem delegação para modelo substituto, commits ou publicação.

**Goal:** cadastrar fornecedores e identificar a origem das entradas por compra sem alterar histórico anterior.

**Architecture:** modelos específicos no app estoque, serviços transacionais e idempotentes, views Django e templates existentes. Fornecedor na entrada com PROTECT e snapshot; nenhum modelo genérico com campo type.

**Tech Stack:** Django/SQLite, unittest/TestCase, HTML/CSS/JavaScript existentes.

## Tarefa 1 — cenários e serviços

Arquivos: `estoque/test_fornecedores.py`, `estoque/models.py`, `estoque/fornecedores.py`, `estoque/documentos.py`, `estoque/migrations/0013_*.py`.

- [x] Escrever testes Given/When/Then para cadastro, edição, inativação, auditoria, autenticação e replay conflitante.
- [x] Rodar `venv\Scripts\python.exe manage.py test estoque.test_fornecedores -v 2` e registrar falha esperada.
- [x] Implementar Fornecedor, FornecedorAlteracao e FornecedorInativacao; validar documento opcional, e-mail e nome; gerar migração sem aplicá-la ao banco real.
- [x] Reexecutar os testes de serviço até passar.

## Tarefa 2 — entrada de estoque

Arquivos: `estoque/services.py`, `estoque/models.py`, `estoque/views.py`, `estoque/templates/registrar_entrada.html`, testes de entrada existentes.

- [x] Testar compra sem fornecedor, fornecedor inativo, ajuste sem fornecedor, snapshots, PROTECT e replay após inativação.
- [x] Acrescentar FK nullable/snapshots à entrada e exigir fornecedor ativo para novas compras no serviço. Validar conteúdo da chave repetida antes de retornar resultado.
- [x] Atualizar fixtures dos testes antigos de compra com fornecedor sintético; manter testes negativos reais.
- [x] Incluir fornecedor na seleção, revisão e histórico, com select_related e consulta ao serviço de fornecedores.

## Tarefa 3 — interface de fornecedores

Arquivos: `estoque/fornecedor_forms.py`, `estoque/fornecedor_views.py`, `estoque/urls.py`, `estoque/templates/fornecedores/`, `templates/base.html`, CSS específico.

- [x] Testar rotas anônimas, cargo sem permissão, CSRF, cadastro/edição inválidos, inativação POST e pesquisa/histórico.
- [x] Implementar lista, cadastro, edição, detalhe e confirmação de inativação. Escritas apenas pelo serviço.
- [x] Preservar valores em erros; paginação com filtros; manter seleção de entrada em erro.
- [x] Revisar desktop/mobile e estados vazio, erro, sucesso e inativo com Impeccable audit/polish.

## Tarefa 4 — verificação e entrega

- [x] Verificar migração em banco sintético na versão anterior e história preservada.
- [x] Rodar `manage.py test -v 2`, `manage.py check`, `manage.py makemigrations --check --dry-run`, `python -m ruff check .`, `python -m mypy .` e `git diff --check` usando a venv.
- [x] Atualizar grafo estrutural e registrar resultados/limitações em `docs/FORNECEDORES.md`.
- [x] Revisar diffs e informar explicitamente que o banco real não recebeu migrações.

## Aceite

Um gerente cadastra fornecedor, registra compra, consulta produtos recebidos e inativa o cadastro. Nova compra com inativo falha sem mudar saldo. Compra antiga mantém nome/documento da época, mesmo após edição. Entradas anteriores à feature permanecem sem fornecedor. Replays não duplicam alterações ou saldo; conteúdo conflitante falha. Tests/Ruff/mypy devem passar, com output real.

Execução concluída em desenvolvimento em 03/11/2026. Evidências e ativação em `docs/FORNECEDORES.md`. Migração real pendente; nenhum commit/push/publicação.
