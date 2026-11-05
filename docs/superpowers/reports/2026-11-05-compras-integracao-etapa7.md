# Compras — candidato de integração para revisão da Etapa 7

Data: 2026-11-05
Base da integração: `main` em `2a0d63edcafc37e46c7bd8b17cb8c7a29a148e1b`
Branch: `codex/compras-integracao`
Worktree: `C:\desenvolvimento\Contruct\.worktrees\compras-integracao`
Worktree auditada preservada: `C:\desenvolvimento\Contruct\.worktrees\compras`, branch `codex/compras`, HEAD `7c517e269f918f3aebf6a2537012c7bc399ee3c0`.

## Escopo aplicado

Os 27 arquivos do manifesto auditado `f4fb57d4a39332d1dd62b0b7e72627c7187a5f5cf1afe6246f0f75499559f853` foram aplicados à cópia isolada da `main`.

Verificação contra o manifesto auditado:

- 25 arquivos permanecem byte-a-byte idênticos.
- `usuarios/templates/home.html` diverge intencionalmente: mantém o dashboard da `main` e acrescenta um único atalho `Compras` em `nav.dashboard-shortcuts`.
- `compras/tests/test_interface.py` diverge intencionalmente: acrescenta os cenários de Home para gerente e vendedor.

O atalho aponta para `compras:lista`, cuja rota exige somente usuário autenticado. As rotas de escrita continuam protegidas por `gerente_required`; não houve relaxamento de autorização.

## Evidência da Home

Servidor local real, somente em `127.0.0.1:8766`, com SQLite e usuários descartáveis.

Gerente `gerente-ui`:

```text
Início | Cruz Fit — HTTP 200
link Compras -> http://127.0.0.1:8766/compras/
Atalhos: Cadastrar produto, Registrar entrada, Fornecedores, Compras
ação primária: Abrir caixa
```

Vendedor `vendedor-ui`:

```text
Início | Cruz Fit — HTTP 200
link Compras -> http://127.0.0.1:8766/compras/
Atalhos: Registrar entrada, Compras
ação primária: Ver caixa
```

O vendedor também abriu `/compras/` com HTTP 200. A tela preservou o dashboard da `main`; a captura visual confirmou o cartão de métricas, caixa, recebimentos, estoque e atalhos. As capturas gerente/vendedor foram realizadas no CUA e os dados foram descartáveis; nenhuma confirmação de compra ou alteração de estoque ocorreu no smoke visual.

## Validações executadas

Todas as execuções usaram `C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe`. Os testes de arquivo usaram temporários externos graváveis em `compras-integracao-qa`; o banco default da worktree não foi usado para dados persistentes.

Suíte específica de Compras, já com os dois cenários Home:

```text
manage.py test compras.tests --verbosity 1
Ran 34 tests in 60.846s
OK
ELAPSED_SECONDS=65.677
```

Migração histórica:

```text
manage.py test compras.tests.test_compatibilidade --verbosity 2
test_migration_preserves_legacy_inventory_and_sale_rows ... ok
Ran 1 test in 2.165s
OK
ELAPSED_SECONDS=7.622
```

Recebimento com entrada de estoque, replay, recebimento total e cancelamento:

```text
manage.py test compras.tests.test_interface.PurchaseInterfaceTests.test_recebimento_parcial_replay_total_e_cancelamento_preservam_saldos --verbosity 2
... ok
Ran 1 test in 1.335s
OK
ELAPSED_SECONDS=8.132
```

Suíte completa serial da combinação:

```text
manage.py test --verbosity 1
Ran 406 tests in 210.357s
OK
ELAPSED_SECONDS=215.606
```

Os 399 testes aprovados informados para o candidato anterior não foram reutilizados como total: a combinação integrada foi contada pelo runner e totalizou 406.

Migração em banco SQLite externo descartável:

```text
Operations to perform:
  Apply all migrations: admin, auth, caixa, compras, contenttypes, estoque, sessions, usuarios, vendas
...
Applying compras.0001_initial... OK
```

Checks no mesmo banco descartável migrado:

```text
System check identified no issues (0 silenced).
No changes detected
```

Qualidade:

```text
python -m ruff check . --no-cache
exit code 0
ELAPSED_SECONDS=0.520

python -m mypy . --cache-dir <qa>/mypy-cache-final
exit code 0
ELAPSED_SECONDS=4.633

git diff --check && git diff --cached --check
DIFF_CHECK_EXIT=0
ELAPSED_SECONDS=0.447
```

Uma execução sem elevação travou ao entrar nos testes de concorrência porque `graphify-out/test-tmp` da cópia tinha ACL sem escrita. A repetição com o diretório descartável e permissão apropriada passou integralmente; isso não alterou a worktree auditada.

Graphify:

```text
graphify query "Como receber_pedido integra pedido, entrada de estoque e estoque?"
Traversal: BFS depth=2 ... 93 nodes found
```

O grafo escopado identificou `receber_pedido`, `EntradaEstoque`, `PedidoCompra`, `_locked_products`, `_claim_order_version`, `_receipt_replay` e os testes de concorrência/rollback relacionados.

## Diff final

O diff da integração contém 27 arquivos auditados, com a alteração de Home limitada a uma linha e dois testes adicionais em `compras/tests/test_interface.py`. Não há migração nova além de `compras/migrations/0001_initial.py`; `makemigrations --check --dry-run` retornou `No changes detected`.

Arquivos de referência:

- `usuarios/templates/home.html` — dashboard da `main` + atalho Compras.
- `compras/tests/test_interface.py` — Home gerente/vendedor e fluxo HTTP.
- `docs/superpowers/reports/2026-11-05-compras-integracao-etapa7.md` — este registro.
- `docs/superpowers/reports/2026-11-05-compras-integracao-manifest.sha256` — manifesto da cópia integrada.

## Identidade e congelamento

```text
branch: codex/compras-integracao
HEAD:   2a0d63edcafc37e46c7bd8b17cb8c7a29a148e1b
commit novo: nenhum
push/merge/publicação: não executados
ativação em banco real: não executada
```

Manifesto integrado: `docs/superpowers/reports/2026-11-05-compras-integracao-manifest.sha256`.
SHA-256 do manifesto integrado: `8ded80aee81b10061e80fa0cc3491a4f79cb295f0cdb5169f50a5bcf58c8576f`.

## Veredito do relatório original

**PASS — pronto para revisão independente da Etapa 7.**

O candidato integrado preserva a Home atual, mantém a autorização de leitura/escrita, aplica o candidato auditado, passa a migração, fluxo de estoque, suíte específica, suíte completa serial e checks do projeto. A publicação na `main` e a ativação real continuam bloqueadas até autorização específica.

## Correção do complemento após reauditoria

Data: 2026-11-05. Esta seção substitui o veredito original para o complemento de testes da Home.

### Estado antes da correção

```text
branch: codex/compras-integracao
HEAD:   2a0d63edcafc37e46c7bd8b17cb8c7a29a148e1b
manifest SHA-256: 8ded80aee81b10061e80fa0cc3491a4f79cb295f0cdb5169f50a5bcf58c8576f
```

O status inicial continha somente o candidato integrado e seus documentos, todos já staged; nenhuma alteração foi descartada. A correção ficou restrita a `compras/tests/test_interface.py`, ao manifesto e a esta evidência.

### Correção aplicada

Os dois testes de Home agora usam `html.parser.HTMLParser` e coletam `href` somente quando o parser está dentro de `nav.dashboard-shortcuts`. Cada teste verifica `reverse('compras:lista')` na lista coletada. O cabeçalho global, que também contém Compras, não satisfaz mais a asserção.

Não houve alteração em template, view, rota, modelo, migração, serviço ou regra de autorização.

### Prova RED da regressão

Em cópia descartável de `2ae57d0baae26ce803c40a64e4bb35c860e5b970`, removi somente o link de Compras dentro de `nav.dashboard-shortcuts`, mantendo intacto o link global do cabeçalho. Os dois testes corrigidos falharam:

```text
test_home_do_gerente_exibe_atalho_de_compras ... FAIL
AssertionError: '/compras/' not found in ['/estoque/add_produto/', '/estoque/entrada/', '/estoque/fornecedores/']

test_home_do_vendedor_exibe_atalho_de_compras ... FAIL
AssertionError: '/compras/' not found in ['/estoque/entrada/']

Ran 2 tests in 2.155s

FAILED (failures=2)
```

O template foi restaurado na cópia descartável e os mesmos testes passaram:

```text
Found 2 test(s).
Ran 2 tests in 2.137s

OK
```

### Contratos de acesso

Na worktree autorizada, o teste corrigido do vendedor confirma a consulta da lista e foi executado junto com `test_vendedor_nao_pode_criar_pedido`:

```text
Found 2 test(s).
..
----------------------------------------------------------------------
Ran 2 tests in 2.039s

OK
```

Isso comprova vendedor autenticado → `/compras/` com HTTP 200 e vendedor → `/compras/novo/` com HTTP 403. O contrato do gerente continua coberto pelo teste de Home e pelas views já existentes.

### Validação final

Teste específico corrigido na worktree original, com o template publicado intacto:

```text
Found 2 test(s).
System check identified no issues (0 silenced).
..
----------------------------------------------------------------------
Ran 2 tests in 2.090s

OK
```

Suíte completa de Compras:

```text
Found 34 test(s).
System check identified no issues (0 silenced).
----------------------------------------------------------------------
Ran 34 tests in 55.650s

OK
```

Checks pertinentes:

```text
manage.py check
System check identified no issues (0 silenced).

manage.py makemigrations --check --dry-run
No changes detected

ruff check . --no-cache
All checks passed!

mypy . --cache-dir %TEMP%\compras-integracao-final-mypy
Success: no issues found in 69 source files

git diff --check
warning: in the working copy of 'compras/tests/test_interface.py', LF will be replaced by CRLF the next time Git touches it
DIFF_CHECK=OK
```

O aviso de line ending é informativo do Git; o check terminou com exit code 0.

### Diff contra a publicação

Base comparada: `2ae57d0baae26ce803c40a64e4bb35c860e5b970`, cuja árvore verificada é `eff126a875e20be5db10888b9543c4a32b939152`.

```text
 compras/tests/test_interface.py                    | 48 ++++++++++++++++++++
 docs/superpowers/reports/2026-11-05-compras-integracao-etapa7.md        | 160 +++++++++++++++++++++
 docs/superpowers/reports/2026-11-05-compras-integracao-manifest.sha256  | 31 +++++++
 3 files changed, 239 insertions(+)
```

O único arquivo de código divergente da publicação é `compras/tests/test_interface.py`. Os dois documentos são evidências de entrega; não há alteração de produção nem migração adicional.

### Manifesto e congelamento final

O manifesto foi atualizado com o hash do teste corrigido:

```text
compras/tests/test_interface.py
46ff8db3a74e45dbd1103f0ed28440808e2267aa37052c163676f1c1ba33e700
```

Após a atualização, a verificação completa dos 27 arquivos deve retornar `MANIFEST_VERIFY=OK`. O candidato permanece sem commit e sem push; integração na `main` e ativação real aguardam autorização.

Verificação observada:

```text
MANIFEST_VERIFY=OK
MANIFEST_ENTRIES=27
MANIFEST_SHA256=058200352324aa7d1ae81600af0490399a0bd4f270f7d53911fc652213ad0bd4
```

Estado final observado:

```text
branch: codex/compras-integracao
HEAD:   2a0d63edcafc37e46c7bd8b17cb8c7a29a148e1b
status: alterações staged do candidato e alterações unstaged somente no teste corrigido, relatório e manifesto
```

## Veredito da correção

**PASS — complemento corrigido e congelado para reauditoria.**

A prova RED demonstra que a regressão agora é detectada quando o atalho da Home é removido, mesmo com o cabeçalho global preservado. A prova GREEN, a suíte de Compras e os checks pertinentes passaram.
