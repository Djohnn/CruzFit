# PRD — Variações de produto no Cruz Fit

**Prioridade P0. Cliente aguardando.** Base de referência: `main` em `8ed9a68e9e3501bfa24fde530b1de690f279fb4c`.

O cadastro atual exige nome único. O operador precisa cadastrar **Camiseta Dry Fit** uma vez e incluir as combinações de cor e tamanho sem digitar um nome diferente para cada peça.

## Fluxo do primeiro corte

1. Em **Cadastrar produto**, mantenha o cadastro simples e ofereça **Produto com variações**. O operador informa o nome e os dados comuns uma vez.
2. Na grade, **Adicionar variação** inclui uma linha com cor, tamanho, saldo inicial, preços e código de barras. Permita reaproveitar preços e alterá-los por linha. Use o gerador de códigos existente quando necessário.
3. Na consulta, uma ficha mostra o nome e as linhas da grade. O total da ficha é informativo. Novas combinações podem ser incluídas depois.
4. Na venda, compra, entrada e etiqueta, a busca pelo nome mostra as combinações e a leitura do código identifica uma única linha. Use o nome base e os campos separados de cor e tamanho. No comprovante, por exemplo, **Camiseta Dry Fit** e abaixo **Cor: Preta · Tamanho: M**.

## Regras e compatibilidade

- Adicione uma ficha de catálogo com vínculo opcional em `Produto`. Cada variação continua sendo um `Produto` com ID, código, saldo, preços e estado ativo próprios. A ficha nunca recebe movimentação de estoque.
- Garanta unicidade de grupo + cor + tamanho no banco, normalizando espaços e caixa. Exija os dois campos na grade do primeiro corte e reutilize a lista de tamanhos. Preserve a apresentação escolhida. O código de barras informado permanece único no catálogo.
- Separe o nome apresentado da identidade técnica. Permita o mesmo nome base nas variações e preserve a regra atual dos produtos simples. Revise consultas que assumem nome único. Preserve os slugs antigos e gere slugs únicos e estáveis para registros novos, sem depender da unicidade do nome.
- Reaproveite os serviços de cadastro e edição. Salve ficha e grade em uma transação, com validação no servidor, auditoria e proteção contra duplo envio. Em caso de erro, indique a linha e preserve os dados do formulário.
- A migração preserva IDs, códigos, saldos, preços, fotos, slugs e referências de vendas e entradas. Os produtos existentes continuam vendáveis. A migração não deve agrupar peças inferindo nomes nem regravar documentos históricos.

## Entrega posterior — Agrupar cadastros existentes

Na ação **Agrupar produtos existentes**, o gerente seleciona os registros e a ficha de destino, nova ou existente. A prévia mostra nome, cor, tamanho, código e saldo. Ao confirmar, o serviço vincula os registros existentes e preserva IDs, etiquetas e movimentações. Combinações repetidas ou incompletas bloqueiam a operação e indicam as linhas a revisar. Esta etapa pode vir depois do P0 para liberar o cadastro novo assim que estiver validado. Geração automática de combinações e outros tipos de variação ficam fora desta entrega.

## Checklist de execução

- [ ] **P0.1 — Modelo e migração.** Trabalhar em branch isolada da `main`. Em `estoque/models.py` e `estoque/migrations/`, criar ficha, vínculo e restrições. Aplicar a migração sobre dados da versão anterior e comparar IDs, valores e vínculos.
- [ ] **P0.2 — Cadastro e ficha.** Adaptar `estoque/forms.py`, `estoque/services.py`, `estoque/views.py` e templates de Estoque. Verificar cadastro simples, grade, inclusão posterior, duplicatas e gravação atômica.
- [ ] **P0.3 — Operação.** Conferir busca, seleção e exibição em Vendas, Caixa, Compras, entradas, baixas, consulta, etiquetas e relatórios. Movimentar e verificar reposição pelo ID da variação.
- [ ] **P0.4 — QA e atualização.** Rodar testes focados e suíte serial completa em dados descartáveis, Django check, migration drift, Ruff, mypy e diff check. Validar cadastro, venda e etiqueta no navegador. Ensaiar migração com base representativa e validar backup e restauração antes de propor a atualização do cliente.
- [ ] **P1 — Agrupamento manual.** Entregar seleção, prévia, validação de conflitos e auditoria do vínculo. Provar que etiquetas e histórico continuam acessíveis pelos IDs originais.

## Aceite

| Caso | Resultado esperado |
|---|---|
| Cadastro inicial | Digitar Camiseta Dry Fit uma vez, criar Preta M com 8, Preta G com 5 e Branca M com 3. Ver uma ficha com três linhas e total 16. |
| Venda por etiqueta | Ler a etiqueta da Preta M reduz apenas seu saldo de 8 para 7. Nome, cor e tamanho aparecem no comprovante. |
| Recebimento de compra | Receber 2 unidades da Preta G aumenta apenas seu saldo de 5 para 7. O pedido e a entrada identificam a combinação. |
| Inclusão posterior | Adicionar Azul P à ficha sem alterar as três linhas anteriores. |
| Erro e duplo envio | Combinação duplicada, código repetido ou linha inválida não deixam gravação parcial. Reenviar a mesma operação não cria duplicatas. |
| Migração | Produtos simples e cadastros separados continuam operando com seus códigos, saldos, fotos e histórico. |
| Agrupamento manual, P1 | Vincular registros à ficha sem mesclar saldos. Conflitos ficam visíveis antes de confirmar. |

Registre **PASS** com evidência, **FAIL** para comportamento incorreto ou **HOLD** com a limitação e o cenário pendente. Exija aceite dos critérios P0 antes de atualizar o cliente. Use o procedimento de backup externo validado antes da migração em produção.
