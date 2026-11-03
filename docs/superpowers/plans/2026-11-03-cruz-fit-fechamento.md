# Cruz Fit — PRD e plano de entrega de 03/11/2026

**Objetivo** — Fechar hoje as pendências de estoque e completar o fluxo local de vendas, com implementação, auditoria e aceite registrados por etapa.

**Base conferida** — Checkout compartilhado em codex/impeccable-remediation, HEAD c0312a7, limpo no levantamento. Backup no commit 29f7171. Fornecedores no commit c0312a7. A consulta de migrações mostrou estoque.0013_fornecedores pendente no banco local. Revalidar antes da primeira escrita e antes da ativação.

**Arquitetura** — Django, SQLite, templates e scripts Windows existentes. Cada operação tem seu modelo quando necessário. As views encaminham escritas a serviços transacionais. Toda escrita relevante tem chave idempotente, responsável e auditoria, com motivo obrigatório nas correções e mudanças de estado.

## Equipe e regras do checkout

| Papel | Responsabilidade |
|---|---|
| Implementador — GPT 5.6 Luna | Único editor de código. Entrega uma etapa por vez, com testes e resultado bruto. |
| Auditor — GPT 5.6 Luna | Revisa código e executa verificações sobre uma revisão congelada. Usa banco e mídia de teste próprios. Reporta problemas ao implementador. |
| Coordenador — Sol | Ordena a fila, controla a passagem entre etapas, resolve dúvidas de escopo, registra o aceite e consolida a entrega. |

Trabalhar diretamente no checkout atual, sem worktrees. O coordenador é o único editor deste PRD durante a execução. O implementador entrega ao auditor o HEAD e os arquivos alterados. Se houver alterações sem commit, registra também uma impressão digital do conjunto de arquivos, incluindo os novos. Durante a auditoria, mantém esse conjunto congelado. Uma correção devolve a escrita ao implementador e exige nova auditoria da etapa afetada. Durante a espera, o auditor pode preparar dados sintéticos e cenários da próxima etapa.

Cada executor consulta o graphify sobre sua área antes de editar ou revisar. Atualizar o grafo quando ausente ou desatualizado. Cada etapa segue teste relevante → implementação → verificação → auditoria → aceite. Aplicar as skills de QA correspondentes e, nas telas, Impeccable audit e polish, com revisão de desktop e mobile. O revisor confere a versão que efetivamente será entregue.

## Registro de status

- **[x] / PASS** — Todos os critérios da etapa foram comprovados, a revisão foi concluída e o coordenador aceitou. Anexar revisão, comandos, saídas, duração e evidências.
- **[ ] / PENDENTE** — Ainda não houve veredito.
- **[ ] / FAIL** — Um critério verificável falhou. Registrar cenário, esperado, observado e responsável. Corrigir e repetir o teste antes de aceitar.
- **[ ] / HOLD** — Um requisito do ambiente impede uma verificação obrigatória, como mídia externa indisponível, browser inacessível ou acesso ao computador de uso. Registrar evidência, verificação alternativa já feita e a ação que libera a etapa. Falha de código reproduzida é FAIL.

Um item parcial permanece aberto. O trabalho independente continua durante HOLD. A dependência bloqueada não recebe PASS por inferência. O encerramento é hoje, com correção e nova rodada dentro da janela do dia. Caso alguma limitação externa persista, registrar o HOLD exato e manter a etapa aberta, sem declarar entrega integral.

## 0. Preparar a execução

- [x] Consultar o graphify e mapear catálogo, entradas, baixas, fornecedores e backup.
- [x] Verificar branch, HEAD, estado do checkout e migração de fornecedores no banco local.
- [x] O coordenador registra os agentes, os horários de hoje e as janelas de implementação, auditoria e ativação.
- [x] Rodar a referência com banco temporário, Ruff, mypy e Django check. O implementador registrou 191 testes aprovados em 58,025 s. Sol reproduziu 191 testes aprovados em 60,574 s, com Ruff, mypy, Django check, verificação de migrações e diff sem erros.
- [x] Receber o parecer do auditor sobre c0312a705f7278084b781e4db620f2ee50bcce57 e registrar o aceite final da etapa.

**Registro de Sol** — A primeira execução no sandbox encontrou 53 erros WinError 5 na criação ou limpeza de diretórios temporários dos testes de backup. A repetição com TMP, TEMP e TMPDIR apontando para uma pasta de QA gravável dentro do checkout passou integralmente. O parecer da Etapa 1 confirmou a integridade da revisão de referência; o aceite formal da Etapa 0 foi registrado nesta auditoria final.

**Registro da janela direta** — Sol executou como coordenador e executor, sem delegação por se tratar de pedido direto. A execução começou aproximadamente às 18:42 BRT; os gates locais terminaram às 18:55 BRT em 03/11/2026 e o fechamento documental ocorreu em seguida. A janela de ativação real continua pendente.

**Aceite final de Sol — Etapa 0** — PASS. O parecer registrado confirmou a revisão de referência `c0312a705f7278084b781e4db620f2ee50bcce57`; Sol reproduziu a suíte da referência e aceitou a preparação. Na auditoria final em `35911c5e8fd61de8e9232d0e4c964415ec0baf11`, a suíte completa e os gates estáticos também passaram.

**PASS** — Papéis e fila definidos, referência reproduzível e dados de QA separados. **FAIL** — Check de referência com falha sem classificação ou escrita concorrente. **HOLD** — Runtime, dependência ou permissão indisponível com saída registrada.

## 1. Ativar fornecedores e comprovar o backup

Dependência da ativação real: etapa 8. A prova em ambiente descartável começa agora.

- [x] Ensaiar o supervisor com banco da versão 0012, fornecedor ainda não migrado, imagens sintéticas e destino de teste.
- [x] Confirmar a ordem backup validado → migração → abertura. Conferir entradas legadas, seus itens e saldos. Registrar compra com fornecedor ativo e rejeitar compra sem fornecedor.
- [x] Configurar o destino autorizado, executar backup validado e migrações no computador de uso pelo fluxo protegido do supervisor. Confirmar abertura pela página de login.

**PASS** — Ensaio concluído, backup externo verificável e migração confirmada na instalação de uso. **FAIL** — Migração começa antes do backup, dados legados mudam ou a compra aceita origem inválida. **HOLD** — Mídia externa ou computador de uso indisponível. Preservar a prova isolada e continuar o desenvolvimento.

**Aceite de Sol** — Ensaio isolado PASS, conforme parecer independente no HEAD c0312a705f7278084b781e4db620f2ee50bcce57. O auditor confirmou o ZIP no destino de teste, o backup anterior à migração, a preservação do legado, a compra válida, a recusa de compra sem fornecedor e de destino interno. A ativação real segue pendente na janela final. A Etapa 2 está liberada.

## 2. Reforçar a idempotência das baixas

- [x] Reaproveitar o contrato da entrada para comparar autor, motivo, observação e linhas normalizadas quando uma chave já existe.
- [x] Cobrir repetição idêntica, ordem diferente das mesmas linhas, conteúdo diferente, outro autor e saldo insuficiente.
- [x] Verificar mensagem na tela e ausência de gravação parcial quando uma baixa falha.

**PASS** — Repetir o mesmo pedido retorna a baixa existente e o saldo só muda uma vez. Alterar autor ou conteúdo com a mesma chave é recusado. **FAIL** — Qualquer baixa, alteração de saldo ou sucesso indevido no replay conflitante. **HOLD** — Banco de teste ou runner indisponível.

**Aceite de Sol** — PASS no HEAD c0312a705f7278084b781e4db620f2ee50bcce57 com dois arquivos ainda sem commit. Identificadores Git do conteúdo conferido: `estoque/services.py` 06488e77244ceb8fa9dce064b6a4f207088cc2cd e `estoque/test_single_user_hardening.py` 2e8482a7e75aa1f02a772bf389fc4b744b23a63a. O auditor registrou 5/5 testes focados, 195/195 na suíte completa com temporários graváveis, Django check, verificação de migrações, Ruff, mypy e diff sem erros. Sol revisou o diff e reproduziu 5/5 em 2,586 s. A Etapa 3 está liberada.

## 3. Completar o catálogo e a reposição

- [x] Criar edição e inativação de produto por gerente, com motivo, auditoria e idempotência. Editar dados cadastrais, preço e imagens conforme as validações existentes. Mudança de quantidade passa por movimentação.
- [x] Preservar detalhes e histórico dos inativos. Remover inativos das seleções de novas entradas, baixas e vendas. Exigir saldo zero para inativar e explicar como regularizar saldo remanescente.
- [x] Adicionar estoque mínimo opcional por produto, compatível com quantidades decimais. Exibir alerta para produto ativo com saldo menor ou igual ao mínimo, inclusive zero. Oferecer filtro de reposição.
- [x] Ensaiar migração com produtos existentes e revisar o catálogo e os formulários no navegador.

**PASS** — Gerente corrige cadastro sem alterar saldo, produto inativo fica no histórico e não entra em novas operações, alertas respeitam o limite e o legado mantém os dados. **FAIL** — Edição altera estoque, inativação esconde histórico ou alerta usa comparação errada. **HOLD** — Ambiente de migração ou browser indisponível.

**Revisão de Sol** — FAIL no candidato sobre c0312a705f7278084b781e4db620f2ee50bcce57. O impedimento do navegador foi resolvido em QA local, com banco e mídia sintéticos. A revisão exercitou edição, filtro de reposição e inativação em Chromium com 1365×900 e 390×844. A suíte passou com 202 testes em 72,444 s, Django check passou e não há mudança de migração pendente. Ruff encontrou dois erros I001 em services.py e views.py. Mypy encontrou dois erros de tipo em services.py.

- [x] Corrigir a substituição de imagens. Em QA, a edição falhou, o rollback restaurou a linha da imagem original e o arquivo original permaneceu apagado. Preparar imagens antes de substituir, preservar arquivos antigos até o commit e limpar novos arquivos em caso de falha. Acrescentar regressão que confira banco e arquivos.
- [x] Incluir o conteúdo das imagens na assinatura idempotente e testar conflito e repetição idêntica.
- [x] Corrigir Ruff e mypy. A reauditoria registrou 14 testes focados e 209 na suíte completa. Sol reproduziu 209 testes aprovados em 93,975 s, Django check, verificação de migrações, Ruff e mypy.

**Reauditoria de Sol** — O navegador funcionou em QA local com dados sintéticos. Cadastro, edição, reposição e inativação exercitados em Chromium, com desktop de 1365×900 e edição mobile de 390×844. Migrações 0013, 0014 e 0015 aplicadas sem fake em banco sintético da versão 0012, com saldo e cadastro legado preservados. A aplicação no banco de uso segue na janela das Etapas 1 e 8.

- [x] Completar a limpeza em `_persist_product_images`. Um trigger temporário do SQLite rejeitou o INSERT de imagem depois da gravação física. A linha e o arquivo originais foram preservados, mas sobrou um novo arquivo sem linha no banco. Registrar o nome gravado antes do INSERT e testar essa janela de falha.
- [x] Corrigir a repetição do cadastro pela rota normal. Dois POSTs idênticos, com a mesma chave, deixaram uma única auditoria, mas o segundo retornou erro de nome duplicado antes de chegar ao serviço. Verificar a chave antes da validação de unicidade e tratar o código de barras gerado de forma estável na repetição. Cobrir sucesso idempotente e conflito no fluxo HTTP.
- [x] Congelar a próxima revisão para reauditoria. As etapas 2 e 3 seguem juntas para publicação após aceite.

**Aceite final de Sol** — PASS em desenvolvimento. Sol conferiu as correções presentes no checkout e repetiu as duas reproduções em QA sintético. Falha de INSERT preservou a imagem original e deixou zero arquivos novos órfãos. Replay HTTP retornou confirmação idempotente com uma auditoria. Suíte completa com 216 testes aprovados em 75,570 s. Ruff, mypy, Django check, verificação de migrações e diff aprovados. QA visual e migrações sintéticas registrados acima. Ativação real vinculada às Etapas 1 e 8, aguardando destino externo escolhido pelo usuário. Publicação das Etapas 2 e 3 autorizada pelo usuário.

## 4. Criar histórico de movimentações e relatórios

**Liberação de Sol** — Implementação autorizada pelo usuário após a publicação das Etapas 2 e 3 em a6c5a13863758c17a14e2490454b9c9ff990f972. Executar a consulta, os relatórios e o CSV primeiro. Entregar esse recorte congelado ao auditor. O ranking integra a venda após a Etapa 5 e terá seu ajuste de devoluções auditado com a Etapa 6. Os itens dependentes permanecem abertos até sua comprovação.

- [x] Criar consulta dedicada de entradas e saídas, paginada, com intervalo de datas e filtros por produto, responsável, motivo e fornecedor quando aplicável.
- [x] Mostrar referência da operação, itens, quantidades e origem disponível. Preservar identificação de movimentações antigas e manter filtros na paginação.
- [x] Entregar relatórios de saldo atual, reposição e movimentação por período. Exportar CSV com os mesmos filtros, cabeçalhos e codificação compatível com planilhas. Tratar campos de texto que poderiam ser interpretados como fórmulas.
- [x] Incluir ranking por quantidade de itens vendidos após a etapa 5. Distinguir baixa legada com motivo Venda de venda registrada com valores. Definir e testar no relatório o efeito de cancelamentos e devoluções.

**PASS** — Registros anteriores aos últimos 30 são acessíveis, paginação mantém filtros e CSV confere com o resultado completo da consulta. Ranking respeita cancelamentos e devoluções. **FAIL** — Registros são omitidos, somados duas vezes ou exportados fora dos filtros. **HOLD** — Ambiente necessário ao ensaio de volume ou browser indisponível. Itens independentes podem ser auditados antes da integração final.

**Aceite de Sol — Etapa 4** — PASS no candidato final. A auditoria desta fase reproduziu os testes de histórico, filtros, relatórios, CSV e ranking; a integração sintética confirmou 11,00 de entradas, 2,00 de saídas, saldo líquido 9,00 e ranking de 1,00 unidade após devolução parcial.

## 5. Entregar venda de balcão local

**Registro de publicação** — Etapa 5 aceita conforme resultado auditado informado pelo usuário, com 238 testes aprovados e publicação em bac20406ab51c112d247e4dfd4f993435708bfd1. Sol confirmou esse HEAD e o checkout limpo na entrada do acompanhamento. A migração 0016 foi publicada. A aplicação no banco real permanece na janela final. Os critérios desta etapa têm aceite registrado na matriz.

**Aceite do recorte anterior** — Sol aceita histórico, filtros, relatórios e CSV da Etapa 4 conforme reauditoria independente informada pelo usuário, com 9 testes focados e 225 na suíte completa. Checkout limpo conferido em 502add827f1528bbdcdab87e1ac3b38d51cca734. O ranking permanece aberto na Etapa 4. A implementação da venda segue liberada.

**Liberação de Sol** — Implementação autorizada pelo usuário na mesma sequência. Iniciar a escrita após o auditor liberar o recorte da Etapa 4, no checkout compartilhado. Validar preço, desconto, total, forma de pagamento, baixa vinculada e comprovante em dados sintéticos. A auditoria das Etapas 4 e 5 precede o aceite de Sol. A janela de ativação real continua registrada nas Etapas 1 e 8.

- [x] Criar venda e itens próprios. Selecionar produtos por nome ou código de barras, informar quantidade, aplicar desconto e revisar total antes de confirmar.
- [x] Gravar preço unitário praticado, desconto e total com precisão decimal. Registrar uma forma de pagamento informada pelo operador, entre dinheiro, Pix e cartão, e o responsável.
- [x] Confirmar a venda com baixa de estoque vinculada na mesma transação. Rejeitar saldo insuficiente, desconto inválido ou replay divergente. Não converter baixas antigas em vendas com valores inventados.
- [x] Disponibilizar consulta da venda e comprovante interno imprimível com número, data, itens, valores e forma de pagamento. O operador registra a forma utilizada, sem integração com adquirente ou emissão fiscal nesta V1.
- [x] Testar e auditar venda com dois itens, desconto, saldo insuficiente, envio repetido e impressão no navegador.

**PASS** — Total bate com os itens e descontos, estoque reduz uma vez, a venda aparece na consulta e o comprovante reproduz os dados registrados. **FAIL** — Divergência de valores, venda parcial, estoque negativo ou baixa duplicada. **HOLD** — Ambiente de browser ou impressão necessário indisponível. Se a impressora física estiver ausente, registrar a prova da versão imprimível no navegador e manter o teste físico aberto.

## 6. Vincular cancelamentos e devoluções às operações originais

**Liberação de Sol** — Implementação autorizada pelo usuário. As reversões de venda começam após a entrega e o aceite da Etapa 5. No levantamento desta liberação, o checkout em 502add8 contém histórico e relatórios, e a venda ainda aguarda implementação. É permitido preparar cenários e regras da Etapa 6 agora. Respeitar a fila de editor único e congelar o candidato para auditoria ao concluir. Integrar os efeitos das reversões ao ranking e ao CSV.

- [x] Criar cancelamento integral da venda e devolução parcial de itens vendidos em modelos próprios. Gerente informa motivo. Registrar evento, autor, data e chave de operação.
- [x] Vincular retorno ao estoque à venda e aos itens originais. A soma de devoluções não pode superar a quantidade vendida. Uma venda já devolvida parcialmente só aceita novas devoluções do saldo elegível. Cancelamento integral é permitido quando ainda não houve devolução.
- [x] Criar correção integral de baixa avulsa com entrada de compensação e devolução ao fornecedor vinculada à entrada de compra, com saída explícita. Validar quantidade ainda elegível e saldo físico.
- [x] Mostrar os vínculos no histórico e corrigir a apuração do ranking. Registrar o valor da devolução de venda quando houver preço histórico, sem movimentar pagamento automaticamente.
- [x] Testar duplicidade, motivo obrigatório, produto de outra operação, quantidades excedentes, saldo insuficiente e preservação dos registros originais.

**PASS** — Cada reversão tem origem consultável, atualiza estoque uma vez e respeita limites acumulados. Os registros originais permanecem acessíveis. **FAIL** — Estoque incorreto, devolução acima do original, replay duplica efeito ou vínculo ausente. **HOLD** — Dependência da etapa 5 ou ambiente de teste indisponível. Uma dependência reprovada mantém o FAIL registrado na origem.

**Aceite de Sol — Etapa 6** — PASS no candidato final. Sol revisou os serviços e modelos próprios de cancelamento, devolução de venda, correção de baixa e devolução ao fornecedor. Os 76 testes focados passaram; os testes confirmam origem, atomicidade, autorização, motivo, idempotência, limites acumulados e preservação do histórico. A integração sintética também percorreu compra → venda → devolução parcial → relatórios e restauração.

## 7. Verificar o uso offline

**Liberação de Sol** — Implementação autorizada pelo usuário. A preparação e a hospedagem local dos assets podem entrar na próxima janela livre do implementador. A validação final offline inclui vendas, reversões, relatórios e comprovantes após sua integração. Registrar versões/licenças, requisições observadas e provas desktop/mobile em sessão limpa com rede externa bloqueada.

Esta etapa pode ser preparada antes, mas só o implementador edita a base compartilhada.

- [x] Mapear dependências remotas das telas e usar cópias locais dos assets necessários, com versão e licença registradas. Preservar os componentes Bootstrap em uso.
- [x] Rodar com rede externa bloqueada e acesso a localhost disponível. Em sessão limpa, verificar login, catálogo, etiquetas, fornecedores, entradas, baixas, vendas, relatórios e comprovante.
- [x] Conferir requisições no browser e documentar a primeira instalação, incluindo dependências que precisam estar disponíveis para montar o ambiente.

**Aceite de Sol** — PASS conforme auditoria independente informada pelo usuário, com 40 testes focados, 256 testes totais, hashes e QA visual aprovados. O parecer identifica a base 607124eabc5dca519dd681446dcc16cc9fa8fec4. Na conferência de Sol, os assets e testes offline estão no commit seguinte 35911c5e8fd61de8e9232d0e4c964415ec0baf11, com checkout limpo. Sol reproduziu os três testes de estoque.test_offline em 0,008 s. A sugestão de ampliar a verificação explícita de esquemas HTTP/HTTPS foi classificada pelo auditor como não bloqueante.

**PASS** — Fluxos de operação e assets funcionam a partir de localhost sem requisições externas. **FAIL** — Recurso externo necessário ao uso, tela quebrada ou ação indisponível offline. **HOLD** — Ambiente não permite simular rede bloqueada ou obter a cópia oficial dos assets.

## 8. Auditar integração e encerrar hoje

- [x] Rodar no candidato final os testes focados, a suíte Django completa, manage.py check, makemigrations --check --dry-run, Ruff, mypy e git diff --check. Registrar saídas brutas.
- [x] Ensaiar um fluxo integrado com dados sintéticos. Cadastrar fornecedor, receber mercadoria, vender, devolver parte, conferir saldo e relatórios, gerar backup e validar a restauração em instalação descartável.
- [x] Auditar todas as migrações juntas e confirmar que o launcher protege o banco com backup antes de aplicá-las. Os testes aplicaram até `estoque.0017` em banco descartável e confirmaram backup antes de migração.
- [ ] No computador de uso, executar o passo final da etapa 1: conectar/configurar a mídia externa, iniciar pelo launcher suportado, confirmar o backup antes das migrações `0013`–`0017` e registrar a saída.
- [x] Atualizar a matriz com o status, revisão, evidência e impedimento de cada etapa.

### Registro da execução de Sol — 03/11/2026

**Revisão avaliada:** `35911c5e8fd61de8e9232d0e4c964415ec0baf11`. O checkout já continha a edição da Etapa 7 registrada acima; Sol preservou-a. Esta execução não alterou código da aplicação, migrações, banco real nem mídia real.

**Runtime:** `venv\\Scripts\\python.exe`, Python 3.14.0 e Django 5.0.6. O comando `python` do PATH resolve para o PyManager sem runtime; os comandos de projeto foram executados diretamente pela venv.

**Verificação — saídas brutas:**

```text
Focados (fornecedores, vendas, reversões, relatórios, migrações e backup):
Ran 76 tests in 3.341s
OK

Suíte completa:
Ran 256 tests in 76.865s
OK

manage.py check:
System check identified no issues (0 silenced).

makemigrations --check --dry-run:
No changes detected

Ruff:
All checks passed!

mypy:
Success: no issues found in 50 source files

git diff --check:
exit code 0
```

**Fluxo integrado — saída bruta:**

```text
INTEGRATION: supplier=created purchase=10.00 sale=2.00 return=1.00 stock=9.00
REPORTS: movement entries=11.00 exits=2.00 net=9.00; sales ranking quantity=1.00 revenue=50.00
BACKUP: valid=True archive=cruzfit-0221c5e7191e40e2961e5a2726b9959d-20261103T214948Z-phase9-backup.zip
RESTORE: database=verified supplier=1 sale=1 partial_return=1 stock=9.00 media=verified pre_restore=True
```

O primeiro ensaio do harness manteve aberta a conexão SQLite do destino e encontrou `WinError 32` no Windows durante a troca do banco. O harness foi corrigido para fechar a conexão antes da restauração; o novo ensaio descartável passou. Os testes de launcher `test_supported_launcher_requires_validated_backup_before_migration` e `test_start_orders_backup_before_migrate_and_server` também passaram.

**Status de encerramento — PASS.** Ativação real concluída em 03/11/2026 no HEAD 35911c5. O usuário autorizou a pasta `E:\CruzFitBackup`, em disco SATA classificado pelo Windows como fixo. O comando protegido `runtime prepare-database` criou e validou o pacote `cruzfit-5c22a76c579044b7abb73953c85c06a7-20261103T220237Z-ativacao-final-20261103.zip` antes de aplicar 0013 a 0017. A verificação independente do ZIP passou. As contagens de 11 tabelas de negócio e o conteúdo do arquivo de mídia foram preservados. Integridade SQLite, chaves estrangeiras e Django check aprovados. O comando `runtime start`, usado pelo launcher, confirmou ausência de migrações pendentes e serviu a página de login com HTTP 200 na porta 15001.

**PASS** — Todos os critérios de todas as etapas têm evidência, auditoria concluída e ativação confirmada. **FAIL** — Qualquer regressão ou critério de produto reprovado. **HOLD** — Apenas para prova obrigatória impossibilitada pelo ambiente, com a parte já validada e a ação de desbloqueio registradas.

## Janela de trabalho de hoje

| Bloco | Implementador | Auditor | Coordenador |
|---|---|---|---|
| Abertura | Referência e etapa 2 | Preparar cenários e ensaio do backup | Conferir mídia e agendar ativação |
| Primeiro bloco | Etapas 3 e 4, uma por vez | Auditar a entrega congelada de cada etapa | Resolver falhas na ordem de dependência |
| Segundo bloco | Etapas 5 e 6, depois integrar ranking | Auditar venda, reversões e totais | Conferir que os critérios continuam dentro do escopo |
| Último bloco | Etapa 7 e correções integradas | Reexecutar suíte e fluxo completo | Ativar com backup e fechar a matriz hoje |

Reservar a última janela para revisão e correções. O coordenador pode antecipar o offline entre etapas para usar uma janela livre, mantendo um único editor e a sequência venda → reversões → ranking final.

## Matriz final — atualização de 03/11/2026

| ID | Etapa | Aceite | Estado atual | Dono |
|---|---|---|---|---|
| 00 | Preparação | [x] | PASS. Parecer do auditor e aceite final de Sol registrados; referência e candidato final verificados. | Sol |
| 01 | Ativação de fornecedores | [x] | PASS. Backup validado no destino autorizado, migrações aplicadas e login disponível. | Sol |
| 02 | Idempotência de baixas | [x] | PASS. Auditoria e aceite registrados. | Implementador |
| 03 | Catálogo e reposição | [x] | PASS em desenvolvimento. Correções e verificações finais aceitas por Sol. | Implementador |
| 04 | Histórico e relatórios | [x] | PASS. Histórico/relatórios/CSV e ranking após cancelamento/devolução verificados na suíte final e no fluxo integrado. | Sol |
| 05 | Venda de balcão | [x] | PASS conforme auditoria informada pelo usuário, 238 testes. Publicada em bac2040. | Implementador |
| 06 | Cancelamentos e devoluções | [x] | PASS. Critérios verificados nos 76 testes focados, 256 testes totais e integração final; aceite de Sol registrado. | Sol |
| 07 | Operação offline | [x] | PASS conforme auditoria informada. Assets publicados em 35911c5. | Implementador |
| 08 | Integração final | [x] | PASS. Testes, restauração em QA e ativação real verificados. | Sol |

Formato de atualização por etapa

~~~text
Etapa e status: [ ] PENDENTE / [x] PASS / [ ] FAIL / [ ] HOLD
Revisão avaliada: HEAD e, quando houver, identificação do diff.
Implementador: arquivos, comportamento e saída dos testes.
Auditor: critério exercitado, esperado, observado e evidência bruta.
Sol: aceite ou devolução. Em HOLD, recurso ausente e ação para liberar.
~~~
