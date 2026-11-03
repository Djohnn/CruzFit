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
