# QA — Candidato de gestão de variações, item 1

## Identificação

- Base: `77e819973d9f846a3a9657e3c081ee585cd0b507`.
- Candidato de origem preservado: `df5c96e64135237e007c6a67fa59040081b4ec84`, branch `codex/variacoes-etiquetas`.
- Nova branch: `codex/variacoes-item1`.
- Worktree: `C:\desenvolvimento\Contruct\.worktrees\variacoes-item1-20261106`.
- Banco e mídia usados na validação: descartáveis na nova worktree.

Graphify foi consultado antes do recorte na worktree auditada sobre `FichaProduto`, `Produto`, `estoque`, `converter_produto_em_ficha`, `ProdutoConversaoFicha`, `converter_produto`, `adicionar_variacoes_ficha` e `imprimir_etiquetas`. O grafo existente registrava 2.192 nós e 5.326 arestas.

## Recorte realizado

Mantidos no item 1:

- atalhos do Estoque para produto simples e produto já vinculado;
- tela de confirmação prévia, confirmação explícita e campos ausentes de cor/tamanho;
- `ProdutoConversaoFicha`, migração `0021`, `converter_produto_em_ficha` e sua transação;
- preservação do registro original, motivo, auditoria, autorização server-side, idempotência, conflito de chave e rollback;
- inclusão posterior de variações pelo serviço e fluxo já existentes.

Separados do novo candidato:

- payload de sessão e IDs para confirmação pós-salvamento;
- `FichaOperationResult.variacoes_criadas`, `variacao_ids` e replay específico da confirmação recente;
- painel/tabela de variações recentes, destaque transitório, mudanças de nome/EAN/links individuais em `produto.html`;
- seletor novo no template de etiqueta;
- testes, relatório, PRD, plano/design combinados e todas as capturas do item 2.

Verificação por diff confirmou que `estoque/templates/produto.html`, `estoque/templates/imprimir_etiquetas.html` e `estoque/test_barcode_feature.py` estão iguais à base. Assim, a geração automática de EAN e a impressão existentes em `77e8199` permanecem disponíveis sem o bloco do item 2.

O diff final contra `77e819973d9f846a3a9657e3c081ee585cd0b507` contém somente os arquivos de formulário/modelo/migração/serviço/view/URL/template de conversão, os testes do item 1 e esta documentação do recorte. Os arquivos combinados de plano/design/PRD/QA e todas as capturas do candidato auditado foram removidos desta nova branch; continuam preservados na worktree original.

## Critérios do item 1

- PASS — gerente/superusuário vê os dois atalhos; vendedor recebe `403` em GET e POST direto.
- PASS — confirmação prévia é obrigatória e o formulário solicita somente dados ausentes.
- PASS — o produto original continua com o mesmo ID, slug, EAN, saldo, preços, status, foto e histórico de cadastro/entrada/baixa, vinculado como primeira variação.
- PASS — motivo e chave são obrigatórios; auditoria própria é criada; replay equivalente retorna a mesma ficha; conflito de chave é rejeitado.
- PASS — falha após criação da ficha deixa o produto no estado anterior e remove a auditoria.
- PASS — produto já vinculado não é convertido novamente.
- PASS — segunda variação continua sendo incluída pelo fluxo existente, sem payload de confirmação novo.

## Comandos e resultados

Banco descartável:

```text
manage.py migrate --noinput
... estoque.0021_produtoconversaoficha... OK
```

Testes do recorte:

```text
manage.py test estoque.test_variacoes_produto estoque.test_product_management --verbosity 1
Ran 38 tests in 46.769s
OK
```

Testes do item 1 mais regressões-base de EAN/impressão:

```text
manage.py test estoque.test_variacoes_produto estoque.test_product_management estoque.test_barcode_feature --verbosity 1
Found 85 test(s).
Ran 85 tests in 71.725s
OK
```

Suíte serial completa, sem concorrência:

```text
Found 427 test(s).
System check identified no issues (0 silenced).
Ran 427 tests in 308.352s
OK
```

Checks:

```text
manage.py check: System check identified no issues (0 silenced).
manage.py makemigrations --check --dry-run: No changes detected
ruff check estoque: All checks passed!
mypy estoque: Success: no issues found in 17 source files
git diff --check: sem saída, exit 0
```

## Veredito

| Critério | Veredito |
|---|---|
| Recorte somente do item 1 | PASS |
| Conversão, preservação e inclusão existente | PASS |
| Permissões, confirmação prévia, auditoria e idempotência | PASS |
| Rollback e conflito de chave | PASS |
| Testes focados e regressões-base | PASS |
| Suíte serial e checks do projeto | PASS |

Publicação, merge na `main` e atualização do cliente permanecem fora do escopo. A worktree original auditada não foi editada.
