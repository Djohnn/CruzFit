# Relatório de implementação — Cruz Fit — 03/11/2026

## Revisão entregue

- Branch: `codex/impeccable-remediation`
- HEAD: `a6c5a13863758c17a14e2490454b9c9ff990f972`
- Checkout compartilhado, sem commit, push ou merge.
- Banco `db.sqlite3` e pasta `media/` reais não foram usados como fixture nem alterados.
- Banco e mídia da verificação visual: `C:\desenvolvimento\Contruct\qa-etapa4-20261103\qa.sqlite3` e `C:\desenvolvimento\Contruct\qa-etapa4-20261103\media`.

## Resumo da Etapa 4

Foi implementado um recorte read-only para operação e conferência:

- `/estoque/movimentacoes/`: histórico combinado de entradas e saídas, paginação de 20 registros, datas inclusivas, produto, tipo, motivo, responsável e fornecedor.
- `/estoque/relatorios/`: saldo atual, reposição e movimentação por produto/período.
- `/estoque/relatorios/exportar.csv`: CSV UTF-8 com BOM e separador `;`, usando os mesmos filtros da consulta de movimentação e cabeçalhos estáveis para planilhas.
- Textos exportados que começam com `=`, `+`, `-` ou `@` recebem prefixo seguro para não serem interpretados como fórmula.
- Histórico mantém referência, itens, quantidades, origem disponível, observação e o texto `Usuário removido` quando o responsável antigo foi excluído.
- Links de Histórico e Relatórios foram adicionados à navegação principal, com layout responsivo.

O ranking de vendas não foi implementado nesta etapa: depende dos modelos próprios da Etapa 5 e deverá ser integrado depois da venda, com o ajuste de cancelamentos/devoluções na Etapa 6.

## Matriz do PRD

- [ ] Etapa 0 — PENDENTE: referência e aceite formal do coordenador ainda precisam ser registrados no PRD.
- [ ] Etapa 1 — HOLD/PENDENTE: ativação real ainda aguarda destino externo configurado, backup real e migrações no computador de uso.
- [x] Etapa 2 — PASS aceito por Sol no PRD; nenhuma alteração desta entrega reabriu o critério.
- [x] Etapa 3 — PASS em desenvolvimento aceito por Sol no PRD; QA visual e migrações sintéticas já registrados.
- [ ] Etapa 4 — PENDENTE de auditoria/aceite: implementação, testes e QA sintético concluídos; revisão congelada entregue nesta mensagem.
- [ ] Etapa 5 — PENDENTE: não iniciada, aguardando a liberação da revisão da Etapa 4.
- [ ] Etapa 6 — PENDENTE: cancelamentos e devoluções, inclusive correção do ranking.
- [ ] Etapa 7 — PENDENTE: verificação offline completa.
- [ ] Etapa 8 — PENDENTE: integração final, ativação real e matriz de encerramento.

## Arquivos alterados nesta etapa

- `estoque/relatorios.py` — serviço read-only de filtros, normalização de movimentos, paginação, relatórios e geração de CSV.
- `estoque/views.py:347-438` — camada HTTP autenticada para histórico, relatórios e download CSV.
- `estoque/urls.py:14-17` — rotas `movimentacoes`, `relatorios` e `exportar_relatorio_csv`.
- `estoque/templates/movimentacoes.html` — filtros, tabela de histórico, paginação e exportação.
- `estoque/templates/relatorios.html` — abas de saldo/reposição/movimentação, filtros e tabelas.
- `templates/base.html:33-36` — navegação para Histórico e Relatórios.
- `templates/static/estoque/css/css.css:578-696` — layout, tabela, estados e responsividade das novas superfícies.
- `estoque/test_relatorios.py` — 9 cenários Given/When/Then focados.
- `docs/superpowers/plans/2026-11-03-cruz-fit-fechamento.md` — arquivo de plano já existente no checkout e preservado.

Não foram criadas migrações na Etapa 4. O `makemigrations --check --dry-run` confirmou que não há alteração de modelo pendente.

## Critérios exercitados

| Critério | Resultado | Evidência |
|---|---|---|
| Acesso anônimo ao histórico, relatório e CSV | PASS técnico | `test_anonymous_history_redirects_to_login` e `test_anonymous_reports_and_csv_redirect_to_login` |
| Paginação e filtros preservados | PASS técnico | 21 entradas, página 2, datas/produto/responsável/motivo preservados |
| Produto, responsável, motivo e fornecedor | PASS técnico | entrada com fornecedor e responsável excluído; snapshot e filtro confirmados |
| Histórico legado | PASS técnico | referência e `Usuário removido` permaneceram consultáveis |
| Saldo atual e reposição | PASS técnico | mínimo decimal, produto no limite e filtro apenas para ativos |
| Movimentação por período | PASS técnico | entradas e saídas agrupadas uma vez por produto |
| Filtro de produto na projeção de itens | PASS técnico | entrada e baixa com dois produtos; histórico, relatório e CSV mostram somente o produto filtrado |
| CSV compatível com planilhas | PASS técnico | BOM UTF-8, separador `;`, cabeçalhos estáveis |
| Proteção contra fórmula em texto | PASS técnico | `=SOMA(A1:A2)` exportado como `'=SOMA(A1:A2)` |
| Ranking de vendas | PENDENTE | depende da Etapa 5; cancelamentos/devoluções ficam na Etapa 6 |

## QA visual sintético

Banco, usuário e movimentos foram descartáveis. O usuário `qa.etapa4`, produtos `Camiseta QA reposição` e `Short QA disponível`, fornecedor `Fornecedor QA` e as movimentações foram criados somente no banco QA.

Capturas conferidas:

- Desktop saldo: `C:\desenvolvimento\Contruct\qa-etapa4-20261103\visual-relatorios-saldo-desktop-full.png`.
- Mobile histórico: `C:\desenvolvimento\Contruct\qa-etapa4-20261103\visual-movimentacoes-mobile-full.png`.
- Mobile movimentação: `C:\desenvolvimento\Contruct\qa-etapa4-20261103\visual-relatorios-movimentacoes-mobile-full.png`.

Na viewport mobile, a medição final foi:

```text
innerWidth=390
scrollWidth=390
bodyScrollWidth=390
```

O primeiro ensaio encontrou `scrollWidth=409` por causa do botão de exportação no cabeçalho. O CSS foi corrigido para empilhar o cabeçalho abaixo de 768 px e a medição final passou. A tabela larga mantém rolagem horizontal intencional dentro de `.table-responsive`.

O launcher instalado do Impeccable não oferece os subcomandos `audit` e `polish`; ambos retornaram `Unknown command: "audit"`. O comando disponível `impeccable detect` foi executado, retornou código 0 e registrou apenas que não conseguiu resolver links `{% static %}` para analisar cores; a revisão visual manual desktop/mobile foi executada como verificação complementar.

## Verificação — output bruto

### Testes focados

```text
Command: venv\Scripts\python.exe manage.py test estoque.test_relatorios -v 2
Found 9 test(s).
Ran 9 tests in 0.377s
OK
EXIT_CODE=0
```

### Suíte Django completa

```text
Command: venv\Scripts\python.exe manage.py test -v 2
Found 225 test(s).
Ran 225 tests in 90.179s
OK
Destroying test database for alias 'default' (...)
EXIT_CODE=0
```

### Checks

```text
Command: venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).
EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
EXIT_CODE=0

Command: venv\Scripts\ruff.exe check .
All checks passed!
EXIT_CODE=0

Command: venv\Scripts\mypy.exe .
Success: no issues found in 50 source files
EXIT_CODE=0

Command: git diff --check
(sem saída)
EXIT_CODE=0
```

### Impeccable e graphify

```text
Command: impeccable detect estoque\templates\movimentacoes.html estoque\templates\relatorios.html templates\static\estoque\css\css.css templates\base.html
1 file scanned with regex matching only (.css).
Rules that need a parsed page ... did not run on them; findings are an undercount.
EXIT_CODE=0

Command: graphify check-update .
EXIT_CODE=0

Command: graphify query "Etapa 4 histórico paginado filtros relatórios saldo reposição movimentação exportação CSV e dependências de estoque" --budget 2500
Traversal: BFS depth=2 ... 120 nodes found
EXIT_CODE=0

Command: graphify query "Resuma a Etapa 4 implementada: histórico paginado, filtros, relatórios de saldo/reposição/movimentação e exportação CSV; dependências atuais para a Etapa 5 de vendas." --budget 3000
Traversal: BFS depth=2 ... 119 nodes found
EXIT_CODE=0
```

## Falhas e pendências

- A auditoria/aceite independente da Etapa 4 ainda não ocorreu; Sol deve revisar esta revisão congelada antes da Etapa 5.
- O ranking continua sem venda própria e deverá ser implementado após a Etapa 5, com cancelamentos/devoluções na Etapa 6.
- O launcher do Impeccable não possui `audit`/`polish`; `detect` e QA visual manual foram usados, mas a limitação deve ser considerada pelo auditor.
- A ativação real continua pendente: não há configuração externa de backup, e `0013`, `0014` e `0015` permanecem pendentes no banco de uso. Responsável: Sol, na janela final, após escolher/configurar o pendrive ou HD externo.

## Estado do banco real

O banco real e a mídia real permaneceram intactos nesta etapa. Nenhum `migrate`, backup ou seed foi executado contra o `db.sqlite3` real; todos os ensaios usaram o runner Django com banco temporário ou o banco descartável `qa-etapa4-20261103`.

## Próximo gate

Manter este conjunto de arquivos congelado para a auditoria. Só após o parecer do auditor e a liberação de Sol iniciar a Etapa 5, adicionando venda com itens/preços/desconto/pagamento/baixa/comprovante e depois vinculando o ranking.

## Reauditoria — correção do filtro de itens por produto

### Causa e correção

O filtro SQL já restringia as movimentações que continham o produto solicitado, mas a projeção `_movement_items()` ainda iterava todos os itens pré-carregados de cada entrada/baixa. A correção passa `filters.produto_id` para `_entry_row()` e `_outflow_row()` e elimina da projeção qualquer item com outro produto. Histórico, `movement_report()` e `movement_csv()` continuam compartilhando `query_movements()`, portanto recebem a mesma regra. Sem filtro, `product_id=None` preserva todos os itens.

### Regressão adicionada

- `estoque/test_relatorios.py:198-275` — cria uma entrada e uma baixa com `Camiseta relatório` e `Short relatório`; valida por rota HTTP o histórico filtrado, o relatório filtrado, o CSV filtrado e, sem filtro, a presença dos dois produtos.
- `estoque/relatorios.py:166-177,188-216,258-271` — projeção de itens condicionada ao `produto_id` e propagação do filtro para entradas e baixas.

### TDD — output bruto

```text
RED
Command: venv\Scripts\python.exe manage.py test estoque.test_relatorios.RelatoriosTests.test_product_filter_projects_only_matching_items_across_history_report_and_csv -v 2
Found 1 test(s).
FAIL
AssertionError: Items in the first set but not the second: 2
Ran 1 test in 0.074s
FAILED (failures=1)
EXIT_CODE=1

GREEN
Command: venv\Scripts\python.exe manage.py test estoque.test_relatorios.RelatoriosTests.test_product_filter_projects_only_matching_items_across_history_report_and_csv -v 2
Found 1 test(s).
test_product_filter_projects_only_matching_items_across_history_report_and_csv ... ok
Ran 1 test in 0.095s
OK
EXIT_CODE=0
```

### Verificação desta reauditoria — output bruto

```text
Command: venv\Scripts\python.exe manage.py test estoque.test_relatorios -v 2
Found 9 test(s).
Ran 9 tests in 0.377s
OK
EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py test -v 2
Found 225 test(s).
Ran 225 tests in 90.179s
OK
EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).
EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
EXIT_CODE=0

Command: venv\Scripts\ruff.exe check .
All checks passed!
EXIT_CODE=0

Command: venv\Scripts\mypy.exe .
Success: no issues found in 50 source files
EXIT_CODE=0

Command: git diff --check
(sem saída)
EXIT_CODE=0
```

### Estado da revisão para nova auditoria

- HEAD: `a6c5a13863758c17a14e2490454b9c9ff990f972`.
- Branch: `codex/impeccable-remediation`.
- Sem commit, push ou migração real.
- O checkout continua com as alterações não commitadas das Etapas anteriores e com a correção/teste desta reauditoria; nenhum arquivo existente foi revertido ou apagado.
- Banco `db.sqlite3` e mídia reais permanecem intactos; os testes usaram o banco temporário do Django.
- Resultado técnico desta correção: PASS; aceite da Etapa 4 continua pendente do auditor e de Sol.

## Etapa 5 — venda de balcão local

### Status do checkpoint

- [ ] Etapa 5 — PENDENTE de auditoria e aceite de Sol. A revisão foi congelada após os checks; Etapa 6 e Etapa 7 não foram iniciadas.
- HEAD: `502add827f1528bbdcdab87e1ac3b38d51cca734`.
- Branch: `codex/impeccable-remediation`.
- Sem commit, push ou migração no banco real.
- Banco `db.sqlite3` e mídia `media/` reais permaneceram intactos.
- Parecer técnico do implementador: PASS; parecer independente/aceite: PENDENTE.

### Entrega e uso

Foi implementado o fluxo autenticado de venda local:

1. Acesse `Vendas` e escolha `Nova venda`.
2. Busque produtos por nome ou código de barras, informe quantidades, revise subtotal/desconto/total e selecione Dinheiro, Pix ou Cartão.
3. Confirme o POST com chave de idempotência; a venda cria sua própria baixa transacional, congela os preços dos itens e registra o responsável.
4. Consulte a venda, o histórico, o ranking em `Relatórios` e o comprovante imprimível.

Desconto negativo, desconto acima do subtotal, quantidade inválida, estoque insuficiente, forma de pagamento inválida e chave ausente são rejeitados sem gravação parcial. Repetição idêntica, inclusive com itens em ordem diferente, retorna a venda existente; alteração de autor ou conteúdo entra em conflito.

### Arquivos alterados

- `estoque/models.py:264-333` — modelos `Venda` e `ItemVenda`, valores decimais, responsável, pagamento, assinatura e chave única.
- `estoque/migrations/0016_venda_itemvenda_itemvenda_unique_produto_por_venda.py:1-32` — migração sintética da venda e unicidade de produto por venda.
- `estoque/services.py:560-783` — assinatura, replay, validação e serviço transacional `registrar_venda`.
- `estoque/views.py:439-530` e `estoque/urls.py:18-21` — cadastro, listagem, detalhe e comprovante protegidos por login.
- `estoque/relatorios.py:437-477` e `estoque/templates/relatorios.html:14-105` — ranking de vendas e exportação CSV.
- `estoque/templates/registrar_venda.html:1-190` — busca, revisão e confirmação no formulário.
- `estoque/templates/vendas.html:1-73`, `venda_detalhe.html:1-79` e `venda_comprovante.html:1-83` — consulta, detalhe e impressão.
- `templates/base.html:36` e `templates/static/estoque/css/css.css:682-1091` — navegação, layout responsivo e impressão.
- `estoque/test_vendas.py:1-320` — 13 testes focados de serviço, HTTP, atomicidade, replay, ranking e comprovante.

### Critérios exercitados

- PASS — venda com dois itens, preços decimais, desconto e forma de pagamento.
- PASS — baixa de estoque vinculada na mesma transação e preservação de itens/preços.
- PASS — saldo insuficiente e desconto inválido sem alteração parcial.
- PASS — chave obrigatória, replay idempotente e conflito por autor/conteúdo.
- PASS — telas autenticadas, consulta da venda, histórico, ranking/CSV e comprovante imprimível.
- PASS — busca por nome/código, revisão visual da confirmação e layout desktop/mobile.
- PENDENTE — auditoria independente e aceite de Sol.
- NÃO INICIADA — Etapa 6 (cancelamentos/devoluções) e Etapa 7 (offline/assets locais), conforme o gate solicitado.

### QA visual sintético

O servidor foi iniciado somente com `qa_settings_stage5.py`, banco `qa-etapa5-20261103\qa.sqlite3` e mídia sintética fora do checkout. A execução local confirmou login, busca por `Camiseta QA Venda`, seleção, Pix, quantidade 2, desconto R$ 5, revisão, confirmação, detalhe e comprovante. Foram capturadas:

- `C:\desenvolvimento\Contruct\qa-etapa5-20261103\venda-desktop.png` — 1365x900.
- `C:\desenvolvimento\Contruct\qa-etapa5-20261103\venda-mobile.png` — 390x844.
- `C:\desenvolvimento\Contruct\qa-etapa5-20261103\venda-detalhe-desktop.png` — detalhe.
- `C:\desenvolvimento\Contruct\qa-etapa5-20261103\comprovante-desktop.png` — comprovante.

O daemon exigiu execução autorizada fora do sandbox; o servidor foi encerrado depois do ensaio. A validação offline/rede externa fica para a Etapa 7.

### Verificação — output bruto

```text
graphify query "Etapa 5 venda local, baixa transacional, comprovante, ranking e relatórios: resuma os fluxos alterados e dependências" --budget 2500
Traversal: BFS depth=2 | Start: ['Fluxos', '5. Entregar venda de balcão local', '5. Tarefas do agente, em ordem', 'BaixaEstoque', 'LOCAL_DEVELOPMENT.md', '4. Criar histórico de movimentações e relatórios'] | 152 nodes found
[!] TRUNCATED: showing 93 of 152 nodes
EXIT_CODE=0

venv\Scripts\python.exe manage.py test estoque.test_vendas estoque.test_relatorios -v 2
Found 22 test(s).
Ran 22 tests in 0.616s
OK
EXIT_CODE=0

venv\Scripts\python.exe manage.py test -v 2
Found 238 test(s).
Ran 238 tests in 90.112s
OK
EXIT_CODE=0

venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).
EXIT_CODE=0

venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
EXIT_CODE=0

venv\Scripts\ruff.exe check .
All checks passed!
EXIT_CODE=0

venv\Scripts\mypy.exe .
Success: no issues found in 50 source files
EXIT_CODE=0

git diff --check
warning: LF will be replaced by CRLF for estoque/relatorios.py, estoque/services.py and estoque/templates/relatorios.html
EXIT_CODE=0
```

O primeiro `manage.py test -v 2` sem redirecionar temporários terminou com `FAILED (errors=66)` por `PermissionError: [WinError 5]` no `TemporaryDirectory` protegido pelo sandbox. Após definir `TEMP`, `TMP` e `TMPDIR` para `C:\desenvolvimento\Contruct\qa-etapa5-20261103\tmp`, a mesma suíte passou integralmente; nenhum erro de produto persistiu.

### Estado da revisão congelada

```text
git status --short
 M docs/superpowers/plans/2026-11-03-cruz-fit-fechamento.md
 M estoque/models.py
 M estoque/relatorios.py
 M estoque/services.py
 M estoque/templates/relatorios.html
 M estoque/urls.py
 M estoque/views.py
 M templates/base.html
 M templates/static/estoque/css/css.css
?? estoque/migrations/0016_venda_itemvenda_itemvenda_unique_produto_por_venda.py
?? estoque/templates/registrar_venda.html
?? estoque/templates/venda_comprovante.html
?? estoque/templates/venda_detalhe.html
?? estoque/templates/vendas.html
?? estoque/test_vendas.py
```

Os artefatos de configuração temporária foram removidos da raiz do checkout. O banco/mídia sintéticos usados no ensaio permanecem fora do projeto para evidência; nenhum dado real foi usado como fixture ou alterado.

## Etapa 6 — reversões e correções transacionais

- [ ] Etapa 6 — PASS técnico, pendente de auditoria independente e aceite de Sol. Revisão examinada: `bac20406ab51c112d247e4dfd4f993435708bfd1` com diff não commitado no checkout compartilhado. Evidência: 22 testes focados, 251 testes completos, `check`, migrações, Ruff, mypy e `git diff --check` com código 0. Parecer do auditor: PENDENTE.

### Entrega e uso

Foram criados fluxos autenticados, exclusivos do gerente, com motivo, chave de idempotência, responsável, data, vínculo histórico e transação atômica:

1. Na venda, use `Cancelar venda` para cancelamento integral. O sistema cria entrada compensatória `DEVOLUCAO`, preserva a venda e seus itens e rejeita cancelamento após devolução parcial.
2. Na venda, use `Devolver itens`, informe somente as quantidades devolvidas e deixe as demais em branco. O sistema registra devolução parcial por item, conserva o preço histórico, limita o acumulado ao vendido e restaura o saldo.
3. No histórico de uma baixa avulsa, use `Corrigir baixa`. O sistema cria uma entrada compensatória vinculada, sem aceitar vendas ou devoluções de fornecedor como baixa avulsa.
4. Na entrada de compra, use `Devolver compra`. O sistema exige fornecedor, valida quantidade elegível e saldo físico, cria baixa explícita `DEVOLUCAO` e preserva a compra original.
5. Reenvios com a mesma chave, autor e conteúdo retornam a operação existente. Mudança de autor, motivo, itens ou quantidades entra em conflito sem nova alteração de saldo.

### Arquivos alterados

- `estoque/models.py:480-708` — modelos próprios `VendaCancelamento`, `VendaDevolucao`, `ItemVendaDevolucao`, `BaixaCorrecao`, `ItemBaixaCorrecao`, `DevolucaoFornecedor` e `ItemDevolucaoFornecedor`, com vínculos protegidos, auditoria, motivo, responsável, data e chave.
- `estoque/migrations/0017_reversoes_venda_baixa_fornecedor.py:1-133` — alteração de motivo e criação das estruturas de reversão.
- `estoque/services.py:811-1247` — validação, assinatura, replay, bloqueio de saldo, compensações e quatro serviços transacionais.
- `estoque/relatorios.py:31-58,150-391,437-521` — origem vinculada no histórico/CSV e ranking líquido após cancelamentos/devoluções.
- `estoque/views.py:101-116,577-705` — adaptação HTTP manager-only, seleção de linhas positivas e ações protegidas por POST/CSRF.
- `estoque/urls.py:20-23` — rotas das quatro ações.
- `estoque/templates/cancelar_venda.html`, `devolver_venda.html`, `corrigir_baixa.html`, `devolver_fornecedor.html` — telas operacionais com motivo e chave.
- `estoque/templates/venda_detalhe.html:37-48`, `movimentacoes.html:31-67` — ações e origem histórica.
- `templates/static/estoque/css/css.css:1094-1110` — estilo das telas de reversão.
- `estoque/test_reversoes.py:1-485` — contrato, serviços, atomicidade, idempotência, histórico, ranking, CSV e rotas HTTP.

### Critérios exercitados

- PASS — cancelamento integral e preservação da venda original.
- PASS — devolução parcial com preço histórico e limite cumulativo.
- PASS — correção de baixa avulsa com entrada compensatória.
- PASS — devolução ao fornecedor com limite elegível e saldo físico.
- PASS — motivo e chave obrigatórios, autor/conteúdo conflitantes e replay idempotente.
- PASS — item de outra operação, venda com retorno prévio e saldo insuficiente rejeitados sem alteração parcial.
- PASS — histórico, ranking líquido e CSV exibem as reversões e preservam a origem.
- PASS — tela de devolução ignora linhas não selecionadas, sem enviar quantidades zero ao serviço estrito.
- PENDENTE — auditoria independente e aceite de Sol.
- NÃO INICIADA — Etapa 7 de assets locais e verificação com rede externa bloqueada.

### Verificação — output bruto

```text
graphify check-update .
EXIT_CODE=0

graphify extract . --code-only --no-cluster --max-workers 2
[graphify extract] incremental scan of C:\desenvolvimento\Contruct\Project_construct
[graphify extract] --code-only: skipping 49 non-code file(s) (47 docs, 0 papers, 2 images) — no LLM extraction
[graphify extract] 23 code, 0 docs, 0 papers, 0 images changed; 63 unchanged; 0 deleted
[graphify extract] AST extraction on 23 code files...
[graphify extract] wrote C:\desenvolvimento\Contruct\Project_construct\graphify-out\graph.json — 1128 nodes, 3083 edges (no clustering)
EXIT_CODE=0

graphify query "cancelar_venda devolver_venda corrigir_baixa devolver_fornecedor VendaCancelamento VendaDevolucao BaixaCorrecao DevolucaoFornecedor historical movement ranking CSV HTTP" --budget 3000
Traversal: BFS depth=2; nodes including reversal models, services, reports and routes returned
EXIT_CODE=0

venv\Scripts\python.exe manage.py test estoque.test_reversoes estoque.test_relatorios -v 2
Found 22 test(s).
Ran 22 tests in 0.786s
OK
EXIT_CODE=0

venv\Scripts\python.exe manage.py test -v 2
Found 251 test(s).
Ran 251 tests in 83.101s
OK
EXIT_CODE=0

venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).
EXIT_CODE=0

venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
EXIT_CODE=0

venv\Scripts\ruff.exe check .
All checks passed!
EXIT_CODE=0

venv\Scripts\mypy.exe .
Success: no issues found in 50 source files
EXIT_CODE=0

git diff --check
warning: in the working copy of 'estoque/relatorios.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'estoque/services.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'estoque/templates/movimentacoes.html', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'estoque/templates/venda_detalhe.html', LF will be replaced by CRLF the next time Git touches it
EXIT_CODE=0
```

O primeiro ensaio completo antes da correção da tela passou com 250 testes. A regressão adicionada para dois itens e uma quantidade em branco passou isoladamente (`1/1`) e a suíte final passou com 251 testes. Os testes criaram banco temporário em memória e não usaram `db.sqlite3`, `media/` ou backups reais como fixture.

### Estado da revisão congelada

```text
git rev-parse HEAD
bac20406ab51c112d247e4dfd4f993435708bfd1

git branch --show-current
codex/impeccable-remediation

git status --short
 M docs/superpowers/plans/2026-11-03-cruz-fit-fechamento.md
 M estoque/models.py
 M estoque/relatorios.py
 M estoque/services.py
 M estoque/templates/movimentacoes.html
 M estoque/templates/venda_detalhe.html
 M estoque/urls.py
 M estoque/views.py
 M templates/static/estoque/css/css.css
?? estoque/migrations/0017_reversoes_venda_baixa_fornecedor.py
?? estoque/templates/cancelar_venda.html
?? estoque/templates/corrigir_baixa.html
?? estoque/templates/devolver_fornecedor.html
?? estoque/templates/devolver_venda.html
?? estoque/test_reversoes.py
```

Não houve commit, push, merge ou migração real. A Etapa 7 permanece aberta para execução posterior com assets locais e rede externa bloqueada.

## Reauditoria — ações de reversão no histórico

- [ ] Correção entregue e tecnicamente PASS; pendente de reauditoria e aceite de Sol. A revisão permanece sem commit no mesmo HEAD `bac20406ab51c112d247e4dfd4f993435708bfd1`.

### Alterações

- `estoque/relatorios.py:42-59,224-285` — cada linha normalizada informa se permite corrigir uma baixa avulsa ou devolver uma compra; vínculos de venda, devolução e correção tornam a ação indisponível.
- `estoque/templates/movimentacoes.html:111-163` — coluna de ações com links condicionais para `Corrigir baixa` e `Devolver ao fornecedor`.
- `estoque/views.py:774-778` — histórico de entradas carrega devoluções vinculadas sem alterar dados reais.
- `estoque/templates/registrar_entrada.html:120-151` — ação de fornecedor somente para compra com fornecedor e sem devolução já registrada.
- `templates/static/estoque/css/css.css:646-657` — agrupamento responsivo dos controles de ação.
- `estoque/test_reversoes.py:415-490` — regressões HTTP para presença, ausência por vínculo e ocultação após execução.

### RED/GREEN

```text
RED — manage.py test estoque.test_reversoes.ReversalServiceTests.test_movement_history_shows_only_eligible_correction_action estoque.test_reversoes.ReversalServiceTests.test_entry_history_shows_supplier_return_only_once_for_purchase -v 2
Found 2 test(s).
Ran 2 tests in 0.187s
FAILED (failures=2)
Falhas esperadas: links de ação ainda não existiam.
EXIT_CODE=1

GREEN — mesmo comando após a implementação
Found 2 test(s).
Ran 2 tests in 0.174s
OK
EXIT_CODE=0
```

### QA visual sintético

Servidor temporário em `127.0.0.1:15003`, banco copiado e migrado em `C:\desenvolvimento\Contruct\qa-etapa6-links-20261103\qa.sqlite3`, usuário sintético `qa.etapa4`. Nenhum banco ou mídia real foi usado.

- Desktop `1365x900`: histórico de movimentações e entradas exibiram as ações elegíveis.
- Mobile `390x844`: layout responsivo e snapshots de acessibilidade confirmaram os links; tabelas permanecem em contêiner horizontal, conforme o padrão existente.
- Capturas: `C:\desenvolvimento\Contruct\qa-etapa6-links-20261103\movimentacoes-desktop.png`, `registrar-entrada-desktop.png`, `movimentacoes-mobile-full.png` e `registrar-entrada-mobile-full.png`.
- Detector disponível do Impeccable contra as rotas locais: código 0, sem achados emitidos. Os comandos `audit/polish` não existem nesta versão local; `detect` foi a verificação disponível.

### Verificação final — output bruto

```text
venv\Scripts\python.exe manage.py test estoque.test_reversoes estoque.test_relatorios -v 2
Found 24 test(s).
Ran 24 tests in 1.101s
OK
EXIT_CODE=0

venv\Scripts\python.exe manage.py test -v 2
Found 253 test(s).
Ran 253 tests in 84.121s
OK
EXIT_CODE=0

venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).
EXIT_CODE=0

venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
EXIT_CODE=0

venv\Scripts\ruff.exe check .
All checks passed!
EXIT_CODE=0

venv\Scripts\mypy.exe .
Success: no issues found in 50 source files
EXIT_CODE=0

git diff --check
EXIT_CODE=0
Avisos somente de conversão LF/CRLF em arquivos já modificados.
```

Migrações reais, commit, push e publicação continuam pendentes e não foram executados.

## Etapa 7 — uso offline com assets locais

### Status do checkpoint

- [ ] Etapa 7 — PASS técnico, pendente de auditoria independente e aceite de Sol.
- Revisão examinada: `607124eabc5dca519dd681446dcc16cc9fa8fec4`.
- Branch: `codex/impeccable-remediation`.
- Diff não commitado, sem commit, push, merge ou migração real.
- Banco `db.sqlite3`, mídia `media/` e backups reais não foram usados como fixture nem alterados.

### Entrega e uso

O shell global passou a carregar Bootstrap, jQuery Slim e Bootstrap Bundle a partir de `static/` local. Em uma instalação nova, basta executar as migrações antes do servidor conforme o runbook existente; o `collectstatic` sintético confirmou que os arquivos são encontrados e publicados.

O fluxo operacional continua sendo acessado pelo localhost após login: catálogo/estoque, etiquetas, fornecedores, entradas, baixas, vendas, reversões, histórico, relatórios e comprovante. Nenhum CDN é necessário em runtime. As URLs de origem, versões, licenças MIT e hashes estão documentadas em `docs/DEPENDENCIAS_OFFLINE.md`; elas servem apenas como referência de manutenção e não são carregadas pelo navegador.

### Assets versionados localmente

| Arquivo | Versão/licença | SHA-256 |
|---|---|---|
| `templates/static/vendor/bootstrap/css/bootstrap.min.css` | Bootstrap 4.6.2 / MIT | `F886516F3D41E9E7BD994C7F7A39A89CAFAE9483F90396CB0DDEAFE8D1EA5E72` |
| `templates/static/vendor/jquery/jquery.slim.min.js` | jQuery 3.5.1 / MIT | `E3E5F35D586C0E6A9A9D7187687BE087580C40A5F8D0E52F0C4053BBC25C98DB` |
| `templates/static/vendor/bootstrap/js/bootstrap.bundle.min.js` | Bootstrap 4.6.2 / MIT | `19126B874A32753D42C12DFA6C17892BFD93820A5A5100BA1B34DA4D07599B49` |

### Testes derivados em Given/When/Then

- Given rede externa bloqueada; When renderizar o shell; Then todos os links de CSS/JS usam `{% static %}` local.
- Given uma instalação local; When procurar os três assets obrigatórios; Then cada arquivo existe, tem conteúdo não vazio e não contém importação executável remota.
- Given um banco sintético com compra, baixa, venda, devolução, cancelamento, correção e devolução a fornecedor; When navegar no localhost; Then todas as telas autenticadas respondem e exibem os dados preservados.

### Evidência integrada sintética

Ambiente descartável: `C:\desenvolvimento\Contruct\qa-etapa7-20261103\qa.sqlite3`, `media\`, `staticfiles\` e `tmp\`. O banco foi migrado até `estoque.0017` e populado com usuário `qa.etapa7`, fornecedor e três produtos. O seed registrou compra, devolução ao fornecedor, baixa avulsa, correção, venda com dois itens, devolução parcial, segunda venda e cancelamento integral.

Saldos após o seed e as reversões:

```text
Produto 1 — Camiseta Offline QA: 11.00
Produto 2 — Legging Offline QA: 6.00
Produto 3 — Top Offline QA: 6.00
```

O servidor QA escutou somente em `127.0.0.1:15007`. Com bloqueio seletivo de HTTPS e dos domínios CDN, o navegador autenticou e percorreu `/`, `/estoque/`, `/estoque/etiquetas/modelos/`, `/estoque/fornecedores/`, `/estoque/entrada/`, `/estoque/baixa/`, `/estoque/vendas/`, `/estoque/movimentacoes/`, os relatórios de saldo/ranking/movimentação, o comprovante e as três telas de reversão.

Capturas descartáveis:

- Desktop: `C:\desenvolvimento\Contruct\qa-etapa7-20261103\home-desktop.png`, `estoque-desktop.png`, `relatorio-ranking-desktop.png`, `comprovante-desktop.png`.
- Mobile: `C:\desenvolvimento\Contruct\qa-etapa7-20261103\home-mobile.png`, `estoque-mobile.png`, `relatorio-movimentacoes-mobile.png`, `comprovante-mobile.png`.

### Verificação de rede — output bruto

```text
agent-browser --session cruzfit-offline network requests --clear
agent-browser --session cruzfit-offline open http://127.0.0.1:15007/
agent-browser --session cruzfit-offline open http://127.0.0.1:15007/estoque/
agent-browser --session cruzfit-offline open http://127.0.0.1:15007/estoque/relatorios/?relatorio=ranking
agent-browser --session cruzfit-offline open http://127.0.0.1:15007/estoque/vendas/1/comprovante/

ALL REQUESTS: somente GET/POST para http://127.0.0.1:15007 e
/static/vendor/bootstrap/css/bootstrap.min.css,
/static/vendor/jquery/jquery.slim.min.js,
/static/vendor/bootstrap/js/bootstrap.bundle.min.js e estáticos locais.

EXTERNAL URL FILTER
No requests captured

CDN FILTER
No requests captured
EXIT_CODE=0
```

O modo offline global do navegador também foi tentado, mas bloqueou o próprio localhost após o POST de login; por isso a evidência final usa bloqueio seletivo dos esquemas/domínios externos, mantendo localhost disponível, conforme o critério do PRD.

### Verificação — output bruto, duração e código

```text
Command: venv\Scripts\python.exe manage.py test estoque.test_offline estoque.test_vendas estoque.test_reversoes estoque.test_relatorios -v 2
Found 40 test(s).
Ran 40 tests in 1.204s
OK
Duration: 4.078s | EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py test -v 2
Found 256 test(s).
Ran 256 tests in 77.397s
OK
Destroying test database for alias 'default' (...)
Duration: 77.397s | EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).
Duration: 1.958s | EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
Duration: 2.340s | EXIT_CODE=0

Command: venv\Scripts\ruff.exe check .
All checks passed!
Duration: 0.573s | EXIT_CODE=0

Command: venv\Scripts\mypy.exe .
Success: no issues found in 50 source files
Duration: 1.956s | EXIT_CODE=0

Command: git diff --check
(sem saída)
Duration: 0.356s | EXIT_CODE=0
```

### Arquivos alterados nesta etapa

- `templates/base.html:10,58-59` — substituição das três dependências CDN por referências ao static local.
- `templates/static/vendor/bootstrap/css/bootstrap.min.css` — asset local Bootstrap 4.6.2.
- `templates/static/vendor/jquery/jquery.slim.min.js` — asset local jQuery Slim 3.5.1.
- `templates/static/vendor/bootstrap/js/bootstrap.bundle.min.js` — asset local Bootstrap Bundle 4.6.2.
- `estoque/test_offline.py:1-53` — regressões de shell local, inventário de assets e ausência de imports remotos executáveis.
- `docs/DEPENDENCIAS_OFFLINE.md:1-25` — versões, licenças, origens e hashes para manutenção offline.

### Critérios e pendências

- PASS técnico — assets locais encontrados e coletados.
- PASS técnico — login e fluxos autenticados operam com localhost enquanto chamadas HTTPS/CDN são bloqueadas.
- PASS técnico — catálogo, etiquetas, fornecedores, entrada, baixa, vendas, reversões, relatórios e comprovante foram verificados com dados sintéticos.
- PASS técnico — suíte Django e gates estáticos concluídos com código 0.
- PENDENTE — auditoria independente e aceite formal de Sol.
- PENDENTE operacional — backup externo, migrações `0013`–`0017` e ativação no banco real continuam sob responsabilidade de Sol na janela final.

## Estado real após a Etapa 7

Não houve alteração, migração, backup ou restauração do banco real. A mídia real e a pasta `backups/` permaneceram fora do ensaio. A verificação de rede, banco, mídia, capturas e `collectstatic` usou somente o ambiente descartável `qa-etapa7-20261103`. O checkout permanece sem commit/push/merge.

## Reauditoria — rotas e publicação do módulo de vendas

### Correções realizadas

- `construct/urls.py:13` mantém o mount canônico `/vendas/` apontando para `vendas.urls`; `estoque/urls.py:20` mantém `/estoque/vendas/` como alias de compatibilidade.
- `templates/base.html:36`, `vendas/views.py:50,143,177` e os seis templates em `vendas/templates/` usam nomes namespaced `vendas:*`, evitando que a navegação de uma montagem volte para a outra.
- Os templates de vendas permanecem exclusivamente em `vendas/templates/`; o Django os encontra com `APP_DIRS=True`.
- `estoque/test_sales_boundary.py:24-101` cobre `get_template('vendas.html')`, resolução de ambas as montagens, GET autenticado de lista/nova/detalhe/comprovante/cancelamento/devolução e os redirects canônicos.
- `estoque/test_vendas.py` e `estoque/test_reversoes.py` foram ajustados para esperar o mount canônico nos redirects.
- `LEIA-ME-EXECUCAO.txt` documenta a URL canônica e o procedimento para eliminar servidor stale.

### Regressão reproduzida e confirmação

O servidor antigo foi identificado por processo, comando e diretório:

```text
PID: 41644
ExecutablePath: C:\Users\johnn\AppData\Local\Python\pythoncore-3.14-64\python.exe
CommandLine: C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe manage.py runserver 127.0.0.1:15001 --noreload
```

Depois de encerrá-lo, uma instância QA nova foi iniciada a partir de `C:\desenvolvimento\Contruct\Project_construct`, com banco descartável `C:\desenvolvimento\Contruct\qa-sales-runtime-20261103\qa.sqlite3`. Os dois mounts responderam `302` sem autenticação e `200` após login sintético; `/vendas/` renderizou `Vendas registradas` e o template foi carregado sem `TemplateDoesNotExist`.

### Migração

- `estoque.0018_remove_itemvendadevolucao_item_venda_and_more` remove somente o estado histórico de vendas do app `estoque`, preservando as tabelas físicas.
- `vendas.0001_initial` recria o estado dos modelos no app `vendas` sobre as mesmas tabelas físicas e move os ContentTypes para `vendas`, preservando IDs, permissões e registros.
- `estoque/test_sales_boundary.py:102-151` executa a transição para `estoque.0017`, verifica venda/item/devolução e permissões, e retorna o banco temporário ao estado mais recente.
- Nenhuma migração foi aplicada ao banco real. A execução real de `0018` e `vendas.0001` continua exigindo backup externo e janela de ativação.

### Verificação desta correção — output bruto

```text
RED
Command: venv\Scripts\python.exe manage.py test estoque.test_sales_boundary.SalesRouteTests.test_sales_templates_use_canonical_sales_mount_for_follow_up_links -v 2
Found 1 test(s).
FAIL — Couldn't find 'href="/vendas/nova/"' in response
EXIT_CODE=1

GREEN
Command: venv\Scripts\python.exe manage.py test estoque.test_sales_boundary estoque.test_vendas estoque.test_reversoes -v 2
Found 34 test(s).
Ran 34 tests in 3.346s
OK
EXIT_CODE=0
```

### Publicação e pendência de integração

O código do módulo `vendas/`, a migração `estoque/0018` e o teste de fronteira estão presentes no checkout compartilhado, mas continuam não rastreados porque a revisão ainda não foi commitada. `git ls-tree HEAD vendas/` continua vazio por essa razão. Não criei commit, push ou publicação automática. Para tornar a entrega reproduzível no remoto, Sol deve revisar este diff, incluir os arquivos novos no commit conjunto das Etapas 5–7 e publicar a revisão aprovada.

Estado: correção funcional PASS; publicação remota PENDENTE por ausência deliberada de commit/push.

### Checks finais após a correção

```text
Command: venv\Scripts\python.exe manage.py test -v 2
Found 262 test(s).
Ran 262 tests in 91.878s
OK
EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py check
System check identified no issues (0 silenced).
EXIT_CODE=0

Command: venv\Scripts\python.exe manage.py makemigrations --check --dry-run
No changes detected
EXIT_CODE=0

Command: venv\Scripts\ruff.exe check .
All checks passed!
EXIT_CODE=0

Command: venv\Scripts\mypy.exe .
Success: no issues found in 55 source files
EXIT_CODE=0

Command: git diff --check
warning: LF will be replaced by CRLF for LEIA-ME-EXECUCAO.txt and docs/RELATORIO_IMPLEMENTACAO_2026-11-03.md
DIFF_CHECK_EXIT=0
```

### Migrações no QA descartável

```text
Command: venv\Scripts\python.exe manage.py showmigrations estoque vendas
estoque ... [X] 0018_remove_itemvendadevolucao_item_venda_and_more
vendas ... [X] 0001_initial
EXIT_CODE=0
```

Essas marcações são exclusivamente do banco `qa-sales-runtime-20261103`; o banco real continua sem alteração e não foi usado nos testes.
