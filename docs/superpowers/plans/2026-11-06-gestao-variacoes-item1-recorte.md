# Gestão de variações — recorte do item 1

> **Para agentes:** executar as tarefas em ordem e manter o candidato `df5c96e64135237e007c6a67fa59040081b4ec84` intacto na worktree original.

**Objetivo:** produzir um candidato contendo somente o gerenciamento/conversão de variações do item 1 do PRD.

**Arquitetura:** preservar os atalhos do Estoque, o formulário protegido e o serviço transacional `converter_produto_em_ficha`, incluindo `ProdutoConversaoFicha`, auditoria, idempotência e rollback. Remover o payload transitório e toda a apresentação pós-salvamento criada para o item 2. A inclusão de variações posteriores continuará usando o fluxo já existente.

**Base:** `77e819973d9f846a3a9657e3c081ee585cd0b507`.

---

### Tarefa 1 — Registrar o recorte e a baseline

**Arquivos:**

- Criar: este plano e o relatório final do item 1.
- Revisar: `estoque/services.py`, `estoque/views.py`, `estoque/templates/produto.html`, testes e documentação do candidato auditado.

- [ ] Confirmar que a worktree original permanece em `df5c96e64135237e007c6a67fa59040081b4ec84` e comparar `git diff 77e8199..df5c96e` antes de editar.
- [ ] Classificar cada mudança como item 1, item 2 ou compartilhada; mudanças compartilhadas devem ser mantidas somente quando necessárias à conversão.

### Tarefa 2 — Remover o pós-salvamento do backend

**Arquivos:**

- Modificar: `estoque/services.py`.
- Modificar: `estoque/views.py`.

- [ ] Remover `FichaOperationResult.variacoes_criadas`, `variacao_ids` no payload de auditoria e o helper de replay criado exclusivamente para a confirmação recente.
- [ ] Manter `converter_produto_em_ficha`, seus helpers, a auditoria `ProdutoConversaoFicha`, a autorização de gerente/superusuário, motivo, chave, assinatura, bloqueio transacional e rollback.
- [ ] Remover `_queue_variation_confirmation`, o consumo da sessão, `variacoes_recentes` e `variacoes_recentes_ids`; a view de conversão deve redirecionar pelo `produto.slug` preservado.
- [ ] Manter o POST existente de `adicionar_variacao` sem criar payload novo.

### Tarefa 3 — Remover a UI e a instrumentação do item 2

**Arquivos:**

- Modificar: `estoque/templates/produto.html`.
- Restaurar à base: `estoque/templates/imprimir_etiquetas.html`.
- Manter: `estoque/templates/estoque.html`, `estoque/templates/converter_produto.html`, `estoque/forms.py`, `estoque/urls.py`.

- [ ] Restaurar a grade de produto à versão da base, removendo painel recente, EAN/nomes adicionados e links individuais de impressão.
- [ ] Remover o `data-testid` adicionado ao gerador de etiqueta; a rota e o gerador existentes continuam sem alteração.
- [ ] Manter apenas os atalhos do Estoque e a tela de confirmação prévia da conversão.

### Tarefa 4 — Separar testes, documentação e capturas

**Arquivos:**

- Modificar: `estoque/test_variacoes_produto.py` e `estoque/test_product_management.py`.
- Restaurar à base: `estoque/test_barcode_feature.py`.
- Remover: documentação, relatório, PRD e capturas exclusivos da entrega combinada.
- Criar: PRD/relatório do recorte em `docs/superpowers/`.

- [ ] Manter testes de preservação do Produto original, campos ausentes, autorização, replay, conflito, rollback, atalhos e confirmação prévia.
- [ ] Remover testes da confirmação pós-PRG, tabela recente, links individuais, EAN exposto e prévia específica do item 2.
- [ ] Não alterar os testes existentes da base que cobrem geração automática de EAN e impressão.

### Tarefa 5 — Validar o candidato item 1

**Dados:** banco e mídia descartáveis na nova worktree, usando o venv do projeto.

- [ ] Executar os testes focados do recorte e registrar a nova contagem.
- [ ] Validar POST direto como vendedor, confirmação obrigatória, preservação de ID/slug/EAN/saldo/preços/fotos/status/histórico, replay, conflito de chave, rollback e inclusão de uma segunda variação pelo fluxo existente.
- [ ] Executar a suíte serial e os checks do projeto em um único processo, sem publicar ou alterar `main`.
- [ ] Registrar SHA final, diff contra a base, arquivos removidos/separados, comandos, resultados e veredito PASS/FAIL/HOLD.
