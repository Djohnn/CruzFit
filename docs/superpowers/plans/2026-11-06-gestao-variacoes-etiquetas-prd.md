# PRD — Gerenciar variações e imprimir etiquetas

**Objetivo:** permitir que o gerente encontre a ficha pelo Estoque, adicione uma cor ou tamanho e imprima a etiqueta da variação recém-salva.

**Arquitetura:** reutilizar `FichaProduto`, `Produto`, o serviço de cadastro de variações e a impressão existente. Converter um produto simples vinculando o próprio registro a uma ficha em transação, com auditoria e idempotência.

**Escopo:** somente itens 1 e 2. Cadastro de tamanhos pela interface e Trocas ficam fora desta entrega.

## Etapa 0 — Preparação

- [ ] Criar worktree separada a partir da `origin/main` atual e registrar SHA, branch e status. Consultar Graphify sobre fichas, estoque e etiquetas. Se indisponível, registrar a limitação e mapear os pontos de entrada no código.
- [ ] Preparar banco e mídia descartáveis. Usar o venv do projeto. Reproduzir cadastro com EAN em branco pelos dois fluxos e conferir o valor persistido. Se houver defeito na geração, capturar a regressão antes de corrigi-lo.

## Etapa 1 — Acesso e conversão

- [ ] Na lista de Estoque, disponibilizar **Gerenciar variações** para o gerente/superusuário em itens já vinculados, apontando para a seção da ficha. Em produto simples, disponibilizar **Transformar em produto com variações**, com confirmação explícita.
- [ ] Na conversão, usar o produto existente como primeira variação. Mostrar cor e tamanho atuais e pedir os dados que estiverem faltando. Criar uma ficha própria, sem agrupar por nome. Preservar ID, slug, EAN existente, saldo, preços, fotos, status e referências de compras, vendas e movimentações.
- [ ] Exigir motivo e chave de idempotência. Revalidar permissão no servidor. Reenvio equivalente retorna a ficha original, alteração de dados com a mesma chave é rejeitada e falha parcial desfaz a operação.
- [ ] Validar que gerente/superusuário consegue converter e adicionar uma segunda variação. Vendedor não consegue executar a escrita por POST direto. Produto já vinculado não é convertido novamente.

## Etapa 2 — Confirmação e etiqueta

- [ ] Após criar uma ficha ou adicionar uma variação, exibir confirmação com nome, cor, tamanho, código persistido e ação **Imprimir etiqueta** para cada variação criada naquela operação. Em lote, cada ação deve apontar para a própria variação.
- [ ] Reutilizar a rota de etiquetas por slug e as permissões existentes. Abrir a prévia por ação do operador. Manter EAN informado válido, gerar código único quando vazio e rejeitar duplicidade com mensagem clara.
- [ ] Validar em navegador que a prévia da etiqueta mostra o EAN da variação selecionada. Atualizar a página ou reenviar a operação não pode duplicar produtos nem trocar códigos.

## Etapa 3 — Aceite e entrega

- [ ] Rodar regressões focadas, suíte serial, Django check, verificação de migrações, Ruff, mypy e `git diff --check`. Se houver migração, validar em cópia com dados legados.
- [ ] Validar desktop e mobile com gerente e vendedor, usando dados descartáveis. Registrar evidências da lista, conversão, confirmação e prévia de etiqueta.
- [ ] Entregar relatório com SHA de base, diff, testes, evidências e veredito por critério. **PASS** = comprovado, **FAIL** = falha reproduzida, **HOLD** = limitação identificada com condição de desbloqueio. Marcar `[x]` apenas nos itens comprovados.

Pontos iniciais de leitura: `estoque/views.py` (cadastro, detalhe, inclusão e etiquetas), `estoque/services.py` (serviços de ficha), `estoque/models.py` (identidade e vínculos), `estoque/templates/add_produto.html`, `estoque/templates/produto.html` e `estoque/test_variacoes_produto.py`. Localizar o template da lista pela view e confirmar os arquivos antes de editar.

Publicação na main e aplicação no PC do cliente são etapas separadas.
