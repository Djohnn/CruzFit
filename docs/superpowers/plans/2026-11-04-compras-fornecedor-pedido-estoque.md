# PRD e plano de implementação — Fornecedor, pedido e estoque

> Execução por agente implementador seguindo executing-plans, tarefa por tarefa. Auditor independente valida cada etapa. Este documento autoriza planejamento. A implementação começa após liberação do usuário.

**Objetivo:** permitir cadastrar um fornecedor, criar um pedido de compra e receber as mercadorias com entrada automática e rastreável no estoque.

**Arquitetura:** Django com templates e SQLite, conforme o Cruz Fit atual. Novo app compras com serviços de escrita e consultas próprias. Reutilizar o cadastro de fornecedores e o serviço de entrada de estoque existentes.

**Base:** main em 7c517e269f918f3aebf6a2537012c7bc399ee3c0.
**Branch:** codex/compras.
**Worktree:** C:\desenvolvimento\Contruct\.worktrees\compras.
**Estado:** Etapas 0–4 implementadas, corrigidas após auditoria e verificadas no candidato não commitado. Etapas 5–7 permanecem aguardando liberação.

## 1. Diagnóstico confirmado

- [x] Cadastro de fornecedores existente em estoque/models.py e estoque/fornecedores.py.
- [x] Entrada COMPRA exige fornecedor ativo e aumenta quantidades por estoque.services.registrar_entrada.
- [x] Entrada preserva nome/documento do fornecedor, responsável e chave idempotente.
- [x] Existem devoluções ao fornecedor vinculadas à compra original.
- [x] Graphify atualizado e consultado antes da implementação, com 1.615 nós e 4.729 arestas no candidato.
- [x] Inspeção das entidades, serviço e tela de entrada confirmou ausência de pedido de compra.
- [x] Pedido, itens encomendados, saldo a receber e recebimentos vinculados implementados em `compras`.

Referências da base: estoque/models.py (EntradaEstoque, Fornecedor), estoque/services.py (registrar_entrada, devolver_fornecedor), estoque/templates/registrar_entrada.html.

## 2. Jornada e decisões do primeiro escopo

1. Gerente cadastra ou seleciona fornecedor ativo.
2. Em Compras, cria rascunho com produtos existentes e ativos, quantidades e custos unitários.
3. Confirma o pedido e passa a acompanhar quantidades pendentes.
4. Ao chegar a mercadoria, informa o que recebeu e confirma o recebimento.
5. Uma única operação grava recebimento, entrada de estoque e vínculos. O saldo aumenta apenas pela quantidade efetivamente recebida.
6. Consulta o pedido para ver quantidades pedidas, recebidas e pendentes, recebimentos e entradas correspondentes.

Decisões propostas para esta versão, sujeitas à revisão do usuário:

- Operações de compra restritas ao gerente, usando a política já aplicada ao cadastro de fornecedores.
- Aceitar recebimentos parciais e totais. Excedentes são rejeitados.
- Produtos já devem estar cadastrados. Cadastro existente fica acessível por atalho.
- Quantidades em Decimal, com a mesma precisão e limites do estoque. Custos em Decimal com duas casas, positivos. Total calculado no servidor, com arredondamento monetário explícito por linha e soma.
- Registrar custo contratado no pedido e preservar seu snapshot no recebimento. Não atualizar automaticamente os preços do cadastro do produto nesta versão.
- Fornecedor e itens podem ser alterados somente no rascunho. Após confirmação, preservar os dados contratados.
- Inativação posterior do fornecedor bloqueia novos pedidos e recebimentos pendentes até reativação. Inativação do produto também bloqueia seu recebimento. A interface explica a pendência.
- Cancelar pedido confirmado sem recebimento encerra o pedido. Depois de recebimento parcial, cancelar somente o restante pendente, preservando entradas já realizadas.
- Recebimentos confirmados são imutáveis. Devolução usa o fluxo existente vinculado à entrada original. Uma devolução não reabre automaticamente o saldo a receber do pedido.
- Quantidade cancelada é separada da recebida e da recebível; em um pedido cancelado após recebimento parcial, o saldo recebível é zero sem alterar o histórico de recebimentos.
- Entradas manuais e compras legadas continuam consultáveis. Não inventar pedidos para dados antigos.

## 3. Limites do escopo

Inclui pedido, confirmação, cancelamento do saldo pendente, recebimentos, auditoria, acompanhamento e conexão com fornecedor/estoque.

Contas a pagar, pagamento ao fornecedor, integração com Caixa de vendas, importação fiscal/XML, emissão fiscal, cotação, frete, rateio de despesas e alteração automática de custo médio ficam para outra frente. A compra gera estoque e histórico, sem movimento financeiro.

## 4. Modelo de domínio e invariantes

App compras com entidades específicas:

- PedidoCompra: identificador público único, fornecedor protegido, snapshots de nome/documento, responsável, datas, observação, estado e versão de concorrência.
- ItemPedidoCompra: pedido, produto protegido, snapshot de identificação, quantidade e custo unitário contratado. Um produto por pedido, sem linhas duplicadas.
- RecebimentoCompra: pedido, responsável, data, referência documental opcional, chave idempotente, assinatura do payload e relação única com EntradaEstoque.
- ItemRecebimentoCompra: recebimento, item do pedido, quantidade recebida e custo contratado. Não aceitar item de outro pedido.
- CancelamentoPedidoCompra: pedido, autor, data, motivo e chave idempotente. Registra o encerramento das quantidades pendentes.
- EventoPedidoCompra: trilha append-only de criação, edição, confirmação, recebimento e cancelamento, com autor, data, operação e dados suficientes para auditoria.

Estados do pedido: RASCUNHO, CONFIRMADO, PARCIALMENTE_RECEBIDO, RECEBIDO e CANCELADO. Quantidade recebida deriva de recebimentos confirmados, nunca de edição manual do saldo.

Regras obrigatórias:

- Rascunho, confirmação e cancelamento de pendência não movimentam estoque.
- Recebido acumulado de cada item nunca ultrapassa quantidade pedida.
- Toda escrita relevante passa por compras.services. Views validam formulário e chamam serviços.
- Reusar os serviços de fornecedores e estoque. Não escrever diretamente Produto.quantidade nem criar entradas por acesso direto ao modelo de outro app.
- Estender de forma mínima as consultas públicas de fornecedor/produto se necessário. FKs entre domínios preservam integridade.
- Recebimento usa transaction.atomic envolvendo pedido, itens, recebimento e registrar_entrada. Falha em qualquer gravação ou auditoria reverte tudo.
- Cada mutação exige idempotency key. Mesmo payload e mesma chave retornam o resultado original. Mesma chave com outro autor, pedido, itens, quantidades, referência ou motivo é rejeitada.
- Normalizar ordem das linhas antes de assinar o payload. Não tornar ordem visual parte da identidade da operação.
- Versionar o pedido e usar atualização condicional/constraints adequadas ao SQLite. select_for_update sozinho não prova exclusão no SQLite.
- Tratar somente SQLITE_BUSY/SQLITE_LOCKED como conflito recuperável de negócio após sair da transação falha, seguindo o padrão do Caixa. Preservar causa. Outros erros de armazenamento continuam identificáveis.
- Recebimento simultâneo e cancelamento simultâneo não podem ultrapassar saldo ou confirmar estado contraditório.
- Proibir recebimento de rascunho, pedido cancelado ou totalmente recebido. Conferir permissões no serviço além da interface.
- Motivo obrigatório para alteração e cancelamento. Referência documental não se torna chave global de unicidade, pois pode faltar ou repetir em documentos diferentes.
- Uma tentativa confirmada gera uma única entrada. A tela encaminha pedidos para seu recebimento e deixa explícita a distinção entre compra avulsa e recebimento de pedido.

## 5. Superfícies e arquivos previstos

Criar:

- compras/apps.py, models.py e migrations/ — domínio e constraints.
- compras/services.py — criar/editar rascunho, confirmar, receber e cancelar.
- compras/queries.py — listagem, detalhe e pendências.
- compras/forms.py, views.py e urls.py — formulários, autorização e rotas Django.
- compras/templates/compras/ — lista, formulário, detalhe, recebimento e cancelamento.
- compras/tests/ — regras, rotas, integração, concorrência e migração.
- templates/static/compras/css/compras.css — estilos restritos às telas de compras.

Alterar apenas quando necessário:

- cruzfit/settings.py e cruzfit/urls.py — registro e montagem /compras/.
- estoque/fornecedores.py — consulta pública por serviço, se faltar.
- estoque/services.py — integração de entrada mantendo contrato existente.
- estoque/templates/fornecedores/ — atalho Pedidos do fornecedor.
- estoque/templates/registrar_entrada.html — encaminhamento explícito para pedido ou compra avulsa.
- templates/base.html e usuarios/templates/home.html — atalho Compras, sem reformular a navegação global.

Rotas propostas: /compras/, /compras/novo/, /compras/<id>/, /compras/<id>/editar/, /compras/<id>/confirmar/, /compras/<id>/receber/ e /compras/<id>/cancelar/. Escritas por POST com CSRF. A stack atual usa views Django, sem introdução de DRF nesta frente.

## 6. Etapas e critérios de aceite

Usar [ ] para pendente e [x] somente após evidência. Cada etapa recebe PASS, FAIL ou HOLD. HOLD exige identificar o limite do ambiente e o que falta comprovar. FAIL exige defeito reproduzível. PASS técnico e aceite operacional são registrados separadamente.

### Etapa 0 — Preparação e referência — PASS

- [x] Criar worktree própria a partir da main.
- [x] Levantar o fluxo existente e registrar este PRD.
- [x] Conferir branch, SHA, status e regras locais antes da implementação.
- [x] Consultar Graphify sobre compras/entrada e registrar dependências.
- [x] Executar referência de testes e checks no venv do projeto, com temporários graváveis e dados descartáveis.

Aceite: referência reproduzível e dados reais preservados. Status atual: PASS técnico; `check`, migration drift, Ruff, mypy e 367 testes passaram com o venv do projeto e dados descartáveis. A primeira tentativa sem elevação foi HOLD por permissões do ambiente.

### Etapa 1 — Domínio e migrações — PASS (revalidado após auditoria)

- [x] Implementar entidades, estados, precisão, constraints e proteção de vínculos.
- [x] Aplicar migrações em banco descartável contendo dados legados representativos.
- [x] Verificar preservação de fornecedor, produtos, entradas e vendas existentes.

Aceite: PASS técnico. `compras/migrations/0001_initial.py` aplica em banco de teste, `makemigrations --check --dry-run` retorna `No changes detected`, a criação de pedido não altera estoque e `LegacyCompatibilityTests.test_migration_preserves_legacy_inventory_and_sale_rows` prepara fixtures antes de `compras.0001`, comprova o recorder antes/depois e preserva IDs, linhas, saldo e venda legados sem `--fake`.

### Etapa 2 — Pedido e confirmação — PASS

- [x] Implementar serviços de criar/editar rascunho e confirmar.
- [x] Validar permissão, fornecedor/produto ativo, itens, precisão, limites, custos e versão.
- [x] Implementar replay e rejeição de chave com payload divergente.
- [x] Registrar snapshots e eventos de auditoria.

Aceite: PASS técnico. Testes de serviço confirmam snapshots, auditoria, autorização, validações, replay e conflito de chave; confirmação não movimenta estoque; rascunho vazio é persistível e sua confirmação é rejeitada.

### Etapa 3 — Recebimento integrado — PASS (revalidado após auditoria)

- [x] Implementar recebimento parcial e total via serviço de entrada existente.
- [x] Vincular pedido, itens, recebimento e entrada em uma transação.
- [x] Exibir saldos a receber e estado derivado corretamente.
- [x] Provar rollback após falha na entrada, em item ou auditoria.
- [x] Provar replay sem segunda entrada e sem duplicação de saldo.

Aceite: PASS técnico. `test_recebimento_parcial_totaliza_estoque_e_calcula_estado` prova estoque inicial 2, pedido 10, recebimento 4 e depois 6, saldo final 12; `test_recebimento_acima_do_saldo_rejeita_integralmente` rejeita 7 sem gravação parcial; testes de falha na entrada e auditoria confirmam rollback; replay não cria segunda entrada. `quantidade_pedida`, `quantidade_recebida`, `quantidade_cancelada`, `quantidade_recebivel` e o alias histórico `quantidade_pendente` usam a mesma regra do modelo e da consulta.

### Etapa 4 — Cancelamento, concorrência e legado — PASS (revalidado após auditoria)

- [x] Implementar cancelamento com motivo e preservação do recebido.
- [x] Provar disputa de dois recebimentos sobre o mesmo saldo com conexões independentes em SQLite baseado em arquivo.
- [x] Provar disputa entre cancelamento e recebimento, edições concorrentes e confirmação repetida.
- [x] Provar conflito SQLite recuperável e preservação de outros OperationalError.
- [x] Validar devolução existente sobre uma entrada gerada por pedido.
- [x] Manter compra avulsa e dados legados operacionais.

Aceite: PASS técnico. `compras.tests.test_concorrencia` usa SQLite em arquivo e conexões independentes para recebimentos, cancelamento/recebimento, edições com versão esperada, confirmações repetidas e `SQLITE_BUSY`; `test_operational_error_nao_relacionado_a_lock_permanece_identificavel` preserva erro de armazenamento distinto. Devolução existente e compatibilidade legada passaram, sem saldo duplicado ou recebimento acima do pedido.

### Correções da auditoria independente — PASS técnico

- [x] Bloquear alteração e exclusão de recebimentos e itens por `save()`, `delete()`, `QuerySet.update()`, `QuerySet.delete()` e `bulk_update()`.
- [x] Bloquear reescrita/exclusão de eventos e inclusão fora do contexto autorizado de serviço, incluindo operações em lote.
- [x] Separar quantidade pedida, recebida, cancelada e recebível nas APIs do modelo e da consulta.
- [x] Reestruturar o teste de legado para criar fixtures com `compras.0001` não aplicada e verificar o recorder antes/depois.

Alcance das proteções: são guardas no ORM da aplicação para os modelos de histórico. A criação de recebimentos, itens e eventos só é liberada pelo contexto interno usado pelos serviços transacionais. As proteções cobrem as operações ORM testadas, relações `PROTECT` para exclusões indiretas e não alegam proteção contra SQL direto executado fora do ORM; isso exigiria mecanismo de banco e evidência próprios.

### Etapa 5 — Interface — PASS

Status: PASS técnico — rotas server-rendered, forms, templates responsivos e atalhos contextuais foram implementados. O aceite independente ainda é uma atividade posterior da auditoria.

- [x] Lista com fornecedor, data, estado e filtros de fornecedor, estado e período.
- [x] Formulário com produtos e custos, validação por campo e preservação dos valores em erro.
- [x] Detalhe com quantidades pedidas/recebidas/canceladas/recebíveis/pendentes, totais e histórico.
- [x] Recebimento com quantidade pendente visível, confirmação explícita e link para a entrada gerada.
- [x] Atalhos entre fornecedor, pedido e entrada correspondente.
- [x] Menu Compras e acesso pela Home, preservando as outras telas.
- [x] Aplicar Impeccable às novas superfícies: contexto, detector e revisão manual foram executados; `detect` retornou `[]` no CSS e os avisos de stylesheet Django foram registrados.

Evidência: `compras.tests.test_interface` contém 10 cenários Given/When/Then e passou; o navegador local comprovou desktop, viewport 390x844, foco por teclado, erro de recebimento acima do saldo com campos preservados, estado real parcial e navegação somente em `127.0.0.1`. A operação usa somente os serviços de Compras e Estoque já auditados.

### Etapa 6 — QA e auditoria independente — HOLD

Status: HOLD — QA focado e visual PASS, mas a suíte completa não atingiu aceite: a execução serial em modo verboso reproduziu erros em testes legados de imagens/produtos fora de Compras e excedeu o tempo esperado nos testes de concorrência; a execução paralela falhou ao clonar o SQLite por `OperationalError: unable to open database file`. A reauditoria independente permanece pendente.

- [x] Derivar cenários Given/When/Then e executar testes focados: 10 testes de interface (`Ran 10 tests in 13.212s`, `OK`) e 17 testes de serviços/compatibilidade (`Ran 17 tests in 23.241s`, `OK`).
- [ ] Executar suíte completa com resultado limpo: serial encontrou erros legados de imagens/produtos e não concluiu dentro do tempo operacional; paralelo falhou na criação dos bancos clonados por permissão do diretório.
- [x] Ensaiar fluxo com estoque inicial 2, pedido 10, recebimento 4 (saldo 6), replay (saldo 6), recebimento 6 (saldo 12). O teste HTTP passou e a regressão de serviço existente também permanece verde.
- [x] Ensaiar cancelamento parcial sem estorno, fornecedor inativo, produto inativo, permissão negada e itens acima do saldo. Serviços e interface cobrem esses caminhos; a tentativa HTTP de 7 sobre saldo 6 retornou erro claro sem escrita.
- [x] Executar QA visual em 390px e desktop, foco, erros, lista vazia e campos longos/validados nos forms; capturas e snapshot acessível foram preservados no relatório.
- [x] Validar navegação e recebimento com internet externa indisponível e servidor local acessível: requests observados foram somente `127.0.0.1` e assets locais.
- [x] Congelar candidato com SHA, diff e manifesto atual. Auditor reproduz evidências e registra parecer independente.

Aceite: HOLD operacional por suíte completa não limpa e por reauditoria independente ainda não realizada. Testes usam o venv do Project_construct e bancos temporários; nenhum dado real foi usado.

### Etapa 7 — Integração e entrega — HOLD (relatório preparado)

Status: HOLD — relatório e manifesto preparados para auditoria; commit, push, integração na main, migração real e ativação continuam explicitamente aguardando aceite e autorização específica.

- [x] Gerar relatório final com arquivos, decisões, comandos/saídas, resultados e pendências.
- [ ] Obter aceite e integrar na main após revisão do diff completo.
- [ ] Publicar no remoto quando autorizado.
- [ ] Para ativação real, parar o sistema, validar destino externo conectado, criar e verificar backup de banco/mídia.
- [ ] Aplicar migrações reais na janela autorizada e conferir inicialização e integridade.
- [ ] Registrar orientação de atualização e rollback compatível com as migrações.

Aceite: integração, publicação e ativação com seus próprios registros. Nenhuma ativação real é consequência automática do PASS técnico.

## 7. Instruções para o implementador

Ler este PRD integralmente e os serviços existentes antes de editar. Usar somente a worktree codex/compras. A main é a referência de comparação. Orientar-se pelo Graphify, manter diffs restritos à frente e registrar decisões divergentes antes de implementá-las.

Priorizar testes de serviços e integração. Usar E2E para a jornada de usuário e concorrência real com conexões independentes para disputas SQLite. Não substituir prova operacional por mocks de HTTP.

Ao concluir, entregar relatório com etapas [x]/[ ], PASS/FAIL/HOLD, arquivos alterados, migrações, comandos e saídas brutas, ambiente, comportamento observado, limitações e referência exata do candidato para auditoria.
