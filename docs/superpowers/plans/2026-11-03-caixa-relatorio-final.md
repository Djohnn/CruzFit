# Caixa Cruz Fit — relatório de implementação para auditoria

Atualização pós-auditoria: 04/11/2026. Implementação mantida no worktree exclusivo solicitado; nenhuma ativação real foi executada.

## Estado do worktree

- Diretório: `C:\desenvolvimento\Contruct\.worktrees\caixa`
- Branch: `codex/caixa`
- HEAD: `b37c37b887ec399a2c590e8ff532f10070298954` (inalterado; sem commit)
- Estado: alterações rastreadas e arquivos novos do módulo Caixa, integração com Vendas e documentação aguardando revisão. Não houve commit ou push.
- Nenhum banco, mídia ou backup de produção foi alterado e o launcher de produção não foi executado. O SQLite descartável `qa-browser-caixa/visual.sqlite3` recebeu apenas registros sintéticos para QA; a mídia visual não mudou. O ensaio do supervisor usou instalação, banco, mídia e destino de backup separados em `%TEMP%`.

## Resultado por etapa

| Etapa | Status | Evidência/pendência |
|---|---|---|
| 0 — contrato e referência | PASS técnico; reauditoria pendente | O auditor independente confirmou separação Caixa/Vendas/Estoque, consumidor final e ausência de cadastro de clientes. |
| 1 — sessão de Caixa | PASS técnico após correção; reauditoria pendente | Corrigido o `SQLITE_BUSY` concorrente. Cinco disputas em SQLite de arquivo, dez conexões distintas, uma sessão aberta por rodada, uma resposta válida e um erro de negócio recuperável; replay posterior com a chave perdedora idempotente. |
| 2 — pagamentos e troco | PASS técnico | 13 testes focados e 23 testes de Caixa; dinheiro+Pix, Pix+cartão, as três formas, exatidão, troco só no dinheiro e replay. |
| 3 — venda/recebimento/estoque atômicos | PASS técnico | 34 testes integrados, incluindo concorrência e rollback. |
| 4 — tela de venda e comprovante | PASS técnico; QA mobile funcional PASS; desktop/reauditoria HOLD | 29 testes e fluxo visual móvel executado com vendas e comprovante sintéticos nesta retomada. |
| 5 — suprimento e sangria | PASS técnico | 13 focados, 72 regressivos e corrida de duas sangrias; histórico, filtros e saldo físico. |
| 6 — restituições e descontos | PASS técnico; QA mobile funcional PASS; desktop/reauditoria HOLD | 31 focados e 103 regressivos; restituição sintética nas formas originais exercitada no browser móvel. |
| 7 — fechamento e relatórios | PASS técnico; QA mobile funcional PASS; desktop/reauditoria HOLD | 16 testes de fechamento/concorrência; a sessão QA sintética foi fechada no browser móvel com diferença zero. |
| 8 — legado e migrações | PASS técnico; ativação HOLD | Backup, migrações e ciclo real completo backup → migrate → HTTP 200 → parada oficial → porta liberada → reinício/parada passaram em scratch. |
| 9 — QA integrado/offline | HOLD; auditoria complementar pendente | Fluxos automatizados e supervisor PASS; evidências anteriores de mobile/teclado preservadas. QA visual desktop residual e offline seletivo HOLD porque o CUA informou `Local browser use is disabled in Work settings`; não alterado. |
| 10 — ativação | HOLD operacional; não executada | Exige auditoria, liberação operacional, servidor parado e backup real validado em `E:\CruzFitBackup`. Nenhum teste ou resultado de ativação foi declarado aprovado. |

Os PASS técnicos e funcionais acima não equivalem a aceite independente. Em uma sessão anterior foi usado o Chrome integrado; nesta retomada final o CUA retornou `Browsers: Error: Local browser use is disabled in Work settings`, e nenhuma configuração foi alterada. Offline e QA visual desktop residual permanecem HOLD. O launcher Impeccable oferece detector, mas não `audit`/`polish`; veja a evidência e limites na atualização final abaixo.

## Correção após auditoria independente

**Causa raiz.** `abrir_sessao` já capturava `IntegrityError` para resolver colisões cobertas pelas constraints, mas deixava escapar o `OperationalError` de SQLite quando duas transações concorrentes liam a ausência de sessão e tentavam gravar. A constraint parcial mantinha no máximo uma sessão aberta, porém o perdedor chegava à view como erro bruto. A auditoria reproduziu `OperationalError: database is locked` em cinco rodadas.

**Correção.** `caixa.services._is_recoverable_sqlite_lock` agora reconhece apenas códigos nativos SQLite `SQLITE_BUSY`/`SQLITE_LOCKED` (incluindo códigos estendidos), verificando também que o backend é SQLite. A conversão para `CaixaOperationError` ocorre no `except` externo, depois de `transaction.atomic` reverter a transação; a causa é preservada com `raise ... from error`. Erros de outro backend, códigos SQLite diferentes e OperationalErrors sem causa SQLite identificável continuam sendo propagados. Não há espera fixa nem retry automático. O mesmo classificador substitui a antiga comparação ampla de strings nas escritas de corte, recebimento, fechamento, restituição, movimentos e finalização de venda.

**Cenários Given/When/Then.**

- Given um SQLite descartável em arquivo e uma conexão independente segurando escrita; When `abrir_sessao` tenta gravar; Then o operador recebe orientação para atualizar e repetir com a mesma chave, a causa contém `sqlite3.OperationalError("database is locked")`/`SQLITE_BUSY`, e nenhuma linha de sessão fica parcial.
- Given duas conexões Django independentes e duas chaves distintas; When abrem simultaneamente; Then uma operação cria a sessão e a outra recebe `CaixaOperationError`, sem erro bruto e com uma única sessão aberta.
- Given que o lock foi liberado; When a chamada perdedora repete chave e conteúdo; Then cria uma operação completa, e o próximo replay retorna a mesma sessão com `created=False`.
- Given uma `OperationalError("disk I/O error")`; When a abertura falha; Then a mesma instância da exceção é propagada.

**Evidências vermelho/verde.** Antes da correção, `etapa-2-exact-sqlite-busy-red.txt` terminou em erro com `django.db.utils.OperationalError: database is locked` (`SQLITE_BUSY`, exit 1); `etapa-2-opening-race-red.txt` registrou uma sessão criada e uma chamada concorrente com `OperationalError('database is locked')` (exit 1). Após a correção, `etapa-2-opening-lock-green.txt` registrou 3 testes OK em 7,232s (processo 12,027s), incluindo causa preservada, retry/replay e erro operacional não relacionado. `etapa-2-concurrency-suite.txt` registrou 6 testes OK em 16,914s (processo 21,031s). Todas as rodadas usaram arquivo SQLite e dez IDs de conexão distintos.

**Harness corrigido.** A investigação também encontrou que `override_settings(DATABASES=...)` não alterava o `settings_dict` da conexão que o test runner já havia inicializado. O teste dizia configurar arquivo, mas a conexão principal seguia usando SQLite compartilhado em memória. `caixa/tests/test_concorrencia.py` agora troca explicitamente a configuração do wrapper e do handler durante cada cenário, preserva o banco em memória do runner com uma conexão guardiã e restaura a configuração no `finally`. O ensaio de arquivo verifica o caminho via `PRAGMA database_list` antes de disputar o lock.

## Implementação e decisões

- Criado o app `caixa` com sessão única aberta por instalação, recebimentos, parcelas por forma, suprimento, sangria, pendências de restituição, pagamentos de restituição, fechamento e corte de ativação.
- Caixa coordena a venda; Vendas registra a operação comercial; Estoque registra baixa/devolução por seus serviços. Recebimento, venda e baixa são atômicos. A interface antiga de venda conduz ao fluxo integrado.
- Venda permanece para Consumidor final. Não foi criado cadastro de clientes.
- Pagamento aceita dinheiro, Pix, cartão e suas combinações. Parcelas consolidadas por forma precisam somar exatamente o total líquido; troco é calculado apenas sobre dinheiro.
- Desconto é distribuído em centavos por maiores restos, com desempate por ID do item. Devoluções usam o total líquido registrado e preservam o histórico legado.
- Cancelamento/devolução recebida cria pendência atômica. Restituição paga nas formas originais, exige autorização e confirmação manual de Pix/cartão; saída em dinheiro consome saldo físico. Não são inventados recebimentos para vendas anteriores ao corte.
- Fechamento grava snapshot por forma, dinheiro esperado/contado, diferença e justificativa. O snapshot impede edição e exclusão por `save`, `delete`, `QuerySet.update`, `QuerySet.delete` e `bulk_update`.
- `CorteCaixa` é permanente, único, auditado, imutável e idempotente. Após o corte, novas chamadas diretas ao serviço de Vendas são bloqueadas; o orquestrador de Caixa segue autorizado. A presença do corte não cria recebimentos retroativos.
- Nenhuma integração com adquirente, conciliação bancária, emissão fiscal ou operação a prazo foi adicionada.

## Evidências de comportamento

O cenário integrado `caixa.tests.test_fluxo_integrado` confirma, numa sessão sintética:

```text
Venda líquida: R$ 100,00
Dinheiro aplicado: R$ 40,00; entregue: R$ 50,00; troco: R$ 10,00
Pix: R$ 30,00; cartão: R$ 30,00
Estoque: 10 → 9 após venda → 10 após cancelamento
Replay com a mesma chave: uma venda e um recebimento, sem segunda baixa
Dinheiro esperado após fundo R$ 50 + venda R$ 40 + suprimento R$ 20 − sangria R$ 15 − restituição R$ 40: R$ 55,00
Fechamento: dinheiro contado R$ 55,00; diferença R$ 0,00
Totais líquidos por forma após restituição integral: dinheiro R$ 0,00; Pix R$ 0,00; cartão R$ 0,00
```

O exemplo de divergência da etapa 7 também foi exercitado: dinheiro esperado R$ 160,00, contado R$ 155,00, diferença −R$ 5,00 e justificativa obrigatória. O ensaio da etapa 8 migrou venda legada com desconto, cancelamento e devolução ida/volta, preservou IDs, vínculos, totais e saldo e não fabricou parcelas/recebimentos. O backup pré-migração foi restaurado em destino temporário e recebeu a migração Caixa pendente. O supervisor sintético confirmou `backup → migrate → runserver → readiness`; apenas o servidor foi simulado, e `migrate` foi executado no SQLite descartável.

## Saídas reais de verificação

Suíte completa pós-correção, executada no venv do projeto com banco de teste SQLite descartável:

```text
Found 361 test(s).
Ran 361 tests in 125.600s
OK
PROCESS_WALL_SECONDS=130.644
exit_code=0
```

QA focado da etapa 9:

```text
Ran 1 test in 0.218s
OK
PROCESS_WALL_SECONDS=3.904
exit_code=0

Found 39 test(s).
Ran 39 tests in 1.523s
OK
PROCESS_WALL_SECONDS=4.915
exit_code=0
```

Fechamento/concorrência e corte/legado:

```text
Found 16 test(s).
Ran 16 tests in 0.833s
OK
PROCESS_WALL_SECONDS=3.997
exit_code=0

Found 22 test(s).
Ran 22 tests in 4.784s
OK
PROCESS_WALL_SECONDS=9.090
exit_code=0
```

Checks pós-correção, com versões do ambiente registradas em `qa-browser-caixa/etapa-2-ambiente.txt`:

```text
Python: 3.14.0; Django: 5.0.6; django-role-permissions: 3.2.0; SQLite: 3.50.4
mypy: 2.4.1; Success: no issues found in 66 source files (1.095s; exit_code=0; inclui um settings temporário ignorado pelo Git; antes de criá-lo o scan do candidato cobria 65 fontes)
Ruff: 0.17.3; All checks passed! (0.443s; exit_code=0)
Django check: System check identified no issues (0 silenced). (1.886s; exit_code=0)
makemigrations --check --dry-run: No changes detected (1.835s; exit_code=0)
git diff --check: exit_code=0 (0.350s; apenas aviso LF→CRLF em vendas/templates/registrar_venda.html)
```

Os checks de Django usaram `validation_settings.py` ignorado pelo Git, que aponta o banco, mídia e estáticos para `graphify-out/test-tmp/caixa-validation`; nenhum banco real foi aberto. `mypy .` usou a configuração `pyproject.toml` do candidato, sem supressões novas. `git diff --check` não encontrou whitespace inválido; o único aviso foi normalização LF→CRLF do template de venda já modificado no candidato.

**Reconciliação dos erros de mypy da auditoria.** O relatório independente registra 287 erros em 10 arquivos/59 fontes, mas não informa executável, versão do mypy, comando completo nem saída bruta. Antes da criação do settings temporário, `C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe -m mypy .` retornou sucesso em 65 fontes; a última execução reproduzível retornou 66 porque incluiu `qa-browser-caixa/validation_settings.py`, ignorado pelo Git. O mesmo venv importa `rolepermissions` via `django-role-permissions==3.2.0`, enquanto o runtime da auditoria falhou nesse import. Também confirmei que o venv usa Django 5.0.6 e não possui `django-stubs`/`django-stubs-ext`; `pyproject.toml` não declara plugin, define Python 3.14, `ignore_missing_imports=true` e exclui testes/migrações. Isso comprova que o scan atual está limpo no ambiente do projeto; sem versão/saída do runtime da auditoria não atribuo os 287 erros exclusivamente à falta de dependência. Não adicionei ignore, cast ou supressão para obter sucesso.

Ensaio independente pós-auditoria de backup/restauração (`etapa-2-backup-restore.txt`):

```text
BACKUP_VALID=True
SUPERVISOR_ORDER=backup -> migrate -> runserver -> ready
PRE_MIGRATION_BACKUP_EXCLUDES_CUTOVER=True
RESTORED_SALE_ID=1; TOTAL=45; DISCOUNT=5
RESTORED_STOCK=9; LEGACY_RECEIPTS=0
CUTOVER_MIGRATION_APPLIED_AFTER_RESTORE=True
SYNTHETIC_MEDIA_RESTORED=True; PREVIOUS_TARGET_MEDIA_REPLACED=True
REAL_DATABASE_MEDIA_BACKUP_TOUCHED=False
EXIT_CODE=0; PROCESS_WALL_SECONDS=6.258
```

A primeira repetição falhou antes de criar a fixture porque o `TEMP` herdado apontava para diretório AppContainer sem permissão (`PermissionError: WinError 5`). Reexecutei com `TEMP` e `TMP` em `graphify-out/test-tmp` e o ensaio passou. O backup, destino, banco, mídia e fixtures eram temporários; o processo HTTP e o readiness foram simulados, enquanto criação/verificação/restauração do backup e `migrate` ocorreram de fato.

QA de Impeccable e assets:

```text
impeccable audit --target caixa/templates/caixa: Unknown command: "audit" (exit_code=1)
impeccable detect --json templates/static/caixa/css/caixa.css caixa/templates/caixa: []
stderr: detector não resolveu {% static 'caixa/css/caixa.css' %}; cores e custom properties incompletas
cua.getState(): Browsers: Error: Local browser use is disabled in Work settings
```

Busca por URLs externas em templates Caixa e `templates/base.html`: nenhum resultado. A página carrega o CSS local `templates/static/caixa/css/caixa.css`. Isso comprova ausência de dependência externa referenciada nos templates, não substitui a simulação visual de browser offline. O detector estático não substituiu `audit`, polish, browser visual nem QA offline.

Logs brutos pós-auditoria adicionais (diretório local ignorado pelo Git): `qa-browser-caixa/etapa-2-exact-sqlite-busy-red.txt`, `etapa-2-opening-race-red.txt`, `etapa-2-opening-lock-green.txt`, `etapa-2-concurrency-suite.txt`, `etapa-2-suite-completa.txt`, `etapa-2-ambiente.txt`, `etapa-2-mypy.txt`, `etapa-2-ruff.txt`, `etapa-2-django-check.txt`, `etapa-2-migration-drift.txt`, `etapa-2-diff-check.txt` e `etapa-2-backup-restore.txt`, além dos logs históricos citados acima.

## Arquivos alterados

Modificados:

- `construct/settings.py`, `construct/urls.py`
- `estoque/test_vendas.py`
- `templates/base.html`, `usuarios/templates/home.html`
- `vendas/models.py`, `vendas/services.py`, `vendas/views.py`
- `vendas/templates/registrar_venda.html`, `vendas/templates/venda_comprovante.html`, `vendas/templates/venda_detalhe.html`

Criados:

- App Caixa: `caixa/__init__.py`, `caixa/apps.py`, `caixa/models.py`, `caixa/services.py` (classificação SQLite BUSY/LOCKED e conversão recuperável nas escritas), `caixa/queries.py`, `caixa/views.py`, `caixa/urls.py`.
- Migrações: `caixa/migrations/0001_initial.py`, `0002_recebimentovenda_pagamentovenda_and_more.py`, `0003_sangriacaixa_suprimentocaixa_and_more.py`, `0004_liquido_e_restituicoes.py`, `0005_fechamentocaixa.py`, `0006_cortecaixa_cortecaixa_caixa_corte_unico_codigo.py` e `caixa/migrations/__init__.py`.
- Telas: `caixa/templates/caixa/abrir.html`, `fechamento_detalhe.html`, `fechar.html`, `historico.html`, `movimento.html`, `painel.html`, `restituicao.html`; CSS em `templates/static/caixa/css/caixa.css`.
- Testes: `caixa/tests/__init__.py`, `test_concorrencia.py` (vermelho/verde de SQLITE_BUSY, disputa de abertura, erro operacional não relacionado e harness de arquivo corrigido), `test_corte.py`, `test_fechamento.py`, `test_finalizacao.py`, `test_fluxo_integrado.py`, `test_interface.py`, `test_migracao_legado.py`, `test_movimentos.py`, `test_pagamentos.py`, `test_restituicoes.py`, `test_sessoes.py`.
- Migrações de Vendas: `vendas/migrations/0002_alter_venda_forma_pagamento.py`, `0003_liquido_e_restituicoes.py`.
- Planos e relatórios: `docs/superpowers/plans/2026-11-03-caixa.md`, `2026-11-03-caixa-inicio-relatorio.md` e este relatório.

Graphify foi atualizado depois da correção: 1.818 nós, 4.299 arestas e 127 comunidades. A consulta pós-atualização reconectou `abrir_sessao`, o classificador SQLite, a view e os ensaios de concorrência.

## Pendências para liberação

1. Entregar o estado congelado desta branch à reauditoria independente; ainda não há aceite após a correção.
2. Habilitar um browser local autorizado e repetir QA visual: desktop, mobile real/emulado, teclado, mensagens, erro/preservação de campos, foco e modo sem rede com o servidor local acessível. O CUA desta execução foi desabilitado por Work settings.
3. Somente depois da auditoria e da liberação operacional, seguir o procedimento da Etapa 10 com sistema parado, backup validado em `E:\CruzFitBackup`, migrações pelo supervisor e conferência antes da primeira sessão.
4. A Etapa 10 e a chamada de `ativar_corte_caixa` não foram executadas contra instalação real.

## Comandos para reproduzir o ambiente pós-auditoria

Interpretador do projeto: `C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe`. Ambiente verificado no arquivo local `qa-browser-caixa/validation_settings.py` (ignorado pelo Git): configura banco e pastas de mídia/estáticos dentro de `graphify-out/test-tmp/caixa-validation`. O arquivo não contém dados; para recriá-lo, usar:

```python
from pathlib import Path
from construct.settings import *

_TEMP_ROOT = Path(__file__).resolve().parents[1] / "graphify-out" / "test-tmp" / "caixa-validation"
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": str(_TEMP_ROOT / "checks.sqlite3"),
    "TEST": {"NAME": str(_TEMP_ROOT / "tests.sqlite3")},
}}
MEDIA_ROOT = str(_TEMP_ROOT / "media")
STATIC_ROOT = str(_TEMP_ROOT / "static")
```

Em PowerShell, a partir do worktree:

```powershell
$caixaPython = 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe'
$caixaRoot = 'C:\desenvolvimento\Contruct\.worktrees\caixa'
$tempRoot = Join-Path $caixaRoot 'graphify-out\test-tmp'
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:TMPDIR = $tempRoot
$env:PYTHONPATH = "$caixaRoot\qa-browser-caixa;$caixaRoot"
$env:DJANGO_SETTINGS_MODULE = 'validation_settings'
& $caixaPython manage.py test construct.backup.test_runtime --verbosity 1 --settings=validation_settings
& $caixaPython manage.py test --verbosity 1 --settings=validation_settings
& $caixaPython manage.py check --settings=validation_settings
& $caixaPython manage.py makemigrations --check --dry-run --settings=validation_settings
& $caixaPython -m ruff check .
& $caixaPython -m mypy . --exclude-gitignore
git diff --check
```

Versões confirmadas: Python 3.14.0, Django 5.0.6, `django-role-permissions` 3.2.0, SQLite 3.50.4, mypy 2.4.1 e Ruff 0.17.3. O venv importa `rolepermissions`; `django-stubs` e `django-stubs-ext` não estão instalados e o `pyproject.toml` não configura plugin mypy.

## Complemento anterior de evidências — Etapa 9 (supersedido em 04/11/2026)

### Identidade e segurança do runtime visual

O usuário autenticou a aba Chrome integrada ID `3`, tab `410924624`. A URL aberta foi `http://127.0.0.1:15002/caixa/`. O listener da porta é PID `4664`, pai `21676`, ambos identificados como Python; horários de criação 09:58:57 e 09:58:56 de 04/11/2026. A consulta do processo não retornou executável, linha de comando nem proprietário. O arquivo local `qa-browser-caixa/settings_local.py` declara banco `qa-browser-caixa/visual.sqlite3` e mídia `qa-browser-caixa/media`, mas a configuração não pôde ser associada ao PID. Por isso, worktree, settings, banco e mídia usados pelo servidor ativo ficam **NOT_PROVEN**. Nenhuma operação de escrita foi enviada.

O checkout continua em `C:\desenvolvimento\Contruct\.worktrees\caixa`, branch `codex/caixa`, HEAD `b37c37b887ec399a2c590e8ff532f10070298954`. Esse dado confirma o checkout inspecionado, não a origem do processo HTTP.

### Resultados individuais no browser

| Cenário | Status | Resultado observado |
|---|---|---|
| Painel Caixa, GET em viewport mobile | **PASS parcial** | Emulação real por capability viewport em 390×844. Métricas: `innerWidth=390`, `innerHeight=844`, `documentElement.scrollWidth=380`, `body.scrollWidth=380`; sem overflow horizontal do documento. A tela mostrava Sessão #1 aberta e ações do Caixa. Captura CUA apareceu inline, sem mecanismo disponível para exportá-la como arquivo. |
| Abertura de sessão | **HOLD** | A sessão #1 já estava aberta e identificada como `QA Caixa`; a origem do banco não foi comprovada, então não criei outra. |
| Vendas dinheiro+Pix, Pix+cartão, três formas e troco no dinheiro | **HOLD** | Nenhuma venda enviada. |
| Comprovante com parcelas e Consumidor final | **HOLD** | Não gerado. |
| Suprimento, sangria, restituição, fechamento | **HOLD** | Não executados. |
| Validações e preservação de campos | **HOLD** | Não exercitados por formulário. |
| Tab/Shift+Tab e foco no painel | **PASS parcial** | A árvore acessível nomeou os links; Tab percorreu a navegação, Shift+Tab voltou. `Consulta` teve `:focus-visible=true` e estava visível no viewport. A navegação tem área rolável própria (`scrollWidth=828`, `clientWidth=348`); o documento não apresenta rolagem horizontal. |
| Enter/Escape, parcelas, confirmação e modais | **HOLD** | Não testados nos formulários de negócio. |
| Assets observados | **PASS estático** | CSS, JavaScript e logo da tela apontaram para `127.0.0.1:15002/static/...`; imagens `chrome-extension://` pertencem à extensão do browser. Não constitui prova offline. |

### Operação offline e restauração do browser

O CDP integrado recusou `Fetch.enable` com `urlPattern:"*"` porque o padrão alcançava a resposta `Document`, reservada à navegação gerenciada pelo Browser Use. Padrões explícitos de sub-recursos foram aceitos, mas a recarga terminou com `Raw CDP is unavailable while Browser Use is resolving a paused document response`. O registro capturou somente a navegação local para `/caixa/`, sem confirmar o evento de carregamento e sem provar bloqueio externo seletivo. Cache e service worker tiveram bypass temporário solicitado; as chamadas de restauração foram executadas no bloco `finally`, mas não foi possível confirmar o estado após a desconexão do debugger. O reset de viewport também falhou com `Debugger is not attached to the tab with id: 410924624`. A configuração final do browser fica **NOT_VERIFIED**. Offline completo permanece **HOLD**.

### Ensaio de supervisor separado

As tentativas em scratch usaram uma instalação e destino de backup descartáveis e executaram backup/migrações, mas não provaram HTTP/readiness próprios. O ensaio run2 tentou a porta 8767, então ocupada pelo processo independente PID `46048`; esse processo não foi tocado por nós. O runtime run2 criou PID `43984` e filhos `30300` e `7264`. O comando oficial de parada limpou o registro, mas uma inspeção intermediária ainda listou `30300`/`7264`. A tentativa específica de encerrar a árvore por `taskkill /PID 30300 /T /F` foi rejeitada pela revisão automática por limite de uso; não foi executada nem contornada por outra via. A inspeção final desta sessão não encontrou nenhum desses PIDs nem listener em 8767, portanto não foi executado outro encerramento. A porta 15002 continuava pertencendo ao listener PID `4664`. O resultado de supervisor HTTP real e readiness fica **HOLD**.

### Fechamento para auditoria complementar

Não houve mudança de código de produto, banco, mídia ou configuração do processo HTTP do usuário. Nenhuma correção foi aplicada. As duas atualizações documentais passam a integrar o candidato e alteram a árvore de arquivos a revisar; HEAD/branch continuam iguais, sem commit, push, merge ou ativação. Solicita-se auditoria complementar sobre a árvore atualizada. Etapa 10 permanece bloqueada.

## Retomada final da Etapa 9 — 04/11/2026

**Resultado consolidado: Etapa 9 HOLD para auditoria complementar.** QA mobile e teclado passaram no navegador integrado com registros sintéticos. A operação offline seletiva permanece HOLD porque o controle do browser interrompeu a navegação ao resolver uma resposta pausada. O supervisor executou backup, migrações, HTTP e readiness reais em instalação descartável (**PASS de inicialização**); o comando oficial de parada não reconheceu seu próprio processo (**FAIL**). Nenhum aceite independente foi atribuído.

### Aba, runtime visual e viewport

Controle retomado na aba Chrome integrada 410924624, browser ID 3, em http://127.0.0.1:15002/caixa/. Após a desconexão anterior, a aba respondeu novamente à leitura da árvore de acessibilidade e exibiu Caixa sem sessão aberta. O usuário forneceu a proveniência da sessão do servidor: worktree C:\desenvolvimento\Contruct\.worktrees\caixa, settings settings_local, banco C:\desenvolvimento\Contruct\.worktrees\caixa\qa-browser-caixa\visual.sqlite3, mídia C:\desenvolvimento\Contruct\.worktrees\caixa\qa-browser-caixa\media, comando manage.py runserver 127.0.0.1:15002 --noreload --settings=settings_local, Django 5.0.6 e “System check identified no issues.” O PID 5820 era do shell de diagnóstico; a porta respondeu pelo listener observado PID 22672. A identidade do servidor é associada à evidência direta do usuário, corroborada pela porta e pela tela; a linha de comando do PID não foi exposta independentemente nesta ferramenta.

O teste responsivo usou override real do viewport no browser, não redimensionamento da captura. Em 390×844, innerWidth=390, innerHeight=844, documentElement.scrollWidth=380 e body.scrollWidth=380; não houve rolagem horizontal do documento. A navegação tem rolagem própria. Ao terminar, o viewport foi restaurado para 1600×675, innerWidth=1600 e largura de documento/body de 1600.

### Cenários executados e resultado individual

| Cenário | Resultado | Resultado observado |
|---|---|---|
| Painel e estado inicial | **PASS** | Painel carregou em viewport móvel e mostrou estado correto de sessão. |
| Encerramento da sessão QA inicial | **PASS** | Sessão #1: esperado e contado R$ 240,00. A tentativa com R$ 239,00 sem justificativa mostrou validação e preservou o valor; correção para R$ 240,00 concluiu o fechamento. |
| Abertura de sessão | **PASS** | Sessão #2 aberta com fundo sintético de R$ 100,00. |
| Venda dinheiro + Pix e troco | **PASS** | Venda #3, total R$ 100,00: dinheiro aplicado R$ 40,00, entregue R$ 50,00, troco R$ 10,00; Pix R$ 60,00. |
| Venda Pix + cartão | **PASS** | Venda #4: Pix R$ 40,00 e cartão R$ 60,00. Uma terceira parcela foi adicionada e removida antes da confirmação. |
| Venda nas três formas | **PASS** | Venda #5: dinheiro aplicado R$ 30,00, entregue R$ 40,00 e troco R$ 10,00; Pix R$ 30,00; cartão R$ 40,00. |
| Validação e preservação de parcelas | **PASS** | Soma de R$ 99,00 foi rejeitada; os valores permaneceram no formulário, foram corrigidos para R$ 100,00 e a venda foi confirmada uma única vez. |
| Comprovante e Consumidor final | **PASS** | /vendas/3/comprovante/ exibiu Consumidor final e parcelas separadas, incluindo aplicado, entregue e troco da parcela em dinheiro e o Pix. |
| Suprimento e sangria | **PASS** | Registrados valores sintéticos de +R$ 25,00 e −R$ 10,00, cada um com justificativa. |
| Restituição | **PASS** | Um item da venda #5 foi devolvido; restituição de R$ 100,00 rateada às formas originais: cartão R$ 40,00, dinheiro R$ 30,00 e Pix R$ 30,00, com justificativa e confirmação manual. |
| Fechamento | **PASS** | Sessão #2 fechada com esperado/contado R$ 155,00 e diferença zero; snapshot: dinheiro R$ 40,00, Pix R$ 100,00, cartão R$ 60,00. |
| Teclado, foco e nomes acessíveis | **PASS** | Tab e Shift+Tab percorreram a ordem e retornaram; foco visível com contorno dourado; árvore de acessibilidade expôs nomes/labels dos controles. Enter ativou fluxos aplicáveis; Escape no formulário de fechamento não enviou nem alterou dados. Inclusão/remoção de parcela e confirmação única foram verificadas. |
| Modais | **N/A** | Os fluxos percorridos não abriram modal; entrada/saída por teclado não se aplicou. |
| Capturas | **EVIDÊNCIA INLINE** | CUA capturou painel, comprovante da venda #3, fechamento #2, foco de teclado e o estado final restaurado do painel sem sessão. As capturas não foram exportadas para arquivos locais; permanecem como evidência inline desta conversa. |

### Operação offline e estado restaurado

Cache foi limpo antes do ensaio offline. O bloqueio seletivo foi configurado para falhar somente sub-recursos externos (Stylesheet, Script, Image, Font, XHR, Fetch, Other), preservando localhost/127.0.0.1; a navegação principal não foi bloqueada. Após a regra ser aceita, a ferramenta informou “Raw CDP is unavailable while Browser Use is resolving a paused document response” durante a navegação local. Login, venda mista, comprovante e fechamento não foram concluídos com o bloqueio ativo; portanto, assets locais observados não elevam o resultado offline acima de **HOLD**. Não foi usado modo offline total nem a rede do computador foi desligada.

Configuração restaurada: padrões Fetch vazios, cache não desabilitado, bypass de service worker desligado e domínio Network desabilitado. A aba reportou navigator.onLine=true, sem controller de service worker e zero registrations. Não houve mudança de Work settings.

### Prova separada do supervisor HTTP real

Ensaio em %TEMP%\caixa-etapa9-runtime-20261104-run4\runtime-install, banco SQLite db.sqlite3 vazio, mídia vazia, settings construct.settings da cópia, destino de backup próprio %TEMP%\caixa-etapa9-runtime-20261104-run4\backup-target, installation ID 193821074382427d8ae856234ac95cb0. A porta 15001 estava livre; o servidor do usuário em 15002 não foi interrompido.

O comando executado foi python -m construct.backup.cli runtime start --reason “QA descartavel Etapa 9 supervisor” --operation-id qa-caixa-20261104-runtime-final. O fluxo real criou e verificou o pacote de backup de 761 bytes (Pacote válido), aplicou migrações reais de Vendas e Caixa (vendas.0002, vendas.0003, caixa.0001–caixa.0006) e iniciou runserver 127.0.0.1:15001 --noreload. A readiness real retornou HTTP 200 em /auth/login/; o shell Django confirmou DB em ...run4/runtime-install/db.sqlite3 e DB_INTEGRITY=ok. Pelo browser integrado foi aberto http://localhost:15001/auth/login/, autenticado usuário sintético qa_stage9 do banco descartável e carregado /caixa/, que mostrou “Nenhuma sessão aberta”. **Startup, backup, migrações, identidade do banco e readiness: PASS.**

Identidade observada: supervisor PID 1380, runtime.json PID 20384, cadeia 1380 → 21428 → 20384 → 15428; listener 15428 na porta 15001.

**FAIL — parada oficial.** python -m construct.backup.cli runtime stop respondeu “Falha: Processo registrado não pertence a esta instalação” e deixou o listener ativo. A causa reproduzida está em construct/backup/runtime.py::_is_owned_process: a verificação compara raiz esperada com command_line/project_root, mas o comando manage.py runserver ... é relativo e omite a raiz; o snapshot Windows expõe project_root como a própria linha de comando. Assim, o processo registrado é recusado. Impacto: a parada oficial pode deixar runtime e porta ocupados. Não houve correção. Depois de revalidar que a árvore pertencia exclusivamente ao ensaio descartável, taskkill /PID 20384 /T /F encerrou PIDs 15428 e 20384; os pais saíram, a porta 15001 ficou livre e a porta 15002 do usuário continuou atendendo HTTP 200 pelo PID 22672.

### Candidato, arquivos e pendências

Nesta retomada não houve edição de código-fonte nem correção silenciosa; nenhum teste técnico foi repetido. A suíte auditada de 361 testes e os checks anteriores continuam como evidência histórica do PASS técnico. Os arquivos documentais alterados nesta atualização são docs/superpowers/plans/2026-11-03-caixa.md e este relatório. O banco de QA visual recebeu registros sintéticos; mídia QA permaneceu inalterada. Instalação, banco, mídia e backup do supervisor ficaram isolados em %TEMP%. Nenhum processo criado para o ensaio permanece ativo.

Verificação documental: git diff --check terminou com código 0 e reportou somente o aviso de normalização LF→CRLF em vendas/templates/registrar_venda.html. Como os dois documentos são arquivos novos não rastreados no candidato, foram examinados separadamente; nenhum contém whitespace final. A suíte técnica não foi repetida porque esta retomada alterou apenas documentação.

Branch/HEAD mantidos em codex/caixa / b37c37b887ec399a2c590e8ff532f10070298954; a documentação atualizada integra a árvore candidata a revisar. Não houve commit, push, merge, ativação real ou alteração do servidor do usuário. Impeccable foi consultado: o launcher local oferece somente detect; audit e polish não são suportados. O detector anterior não encontrou findings primários ([]), o que não substitui auditoria visual completa. Pendências: desktop visual completo, fluxo offline com bloqueio seletivo, Impeccable audit/polish e auditoria independente permanecem HOLD; parada oficial do supervisor permanece FAIL até correção e reteste. Etapa 10 segue bloqueada.

## Atualização final: correção do supervisor e conclusão técnica — 04/11/2026

**Estado entregue.** Worktree `C:\desenvolvimento\Contruct\.worktrees\caixa`, branch `codex/caixa`, HEAD `b37c37b887ec399a2c590e8ff532f10070298954`. Candidato sem commit, push ou ativação real; Etapa 10 bloqueada. Esta seção supersede os status antigos de falha de parada do supervisor. Nenhuma aceitação do auditor independente foi inferida.

### Causa raiz, impacto e solução

O supervisor recusava seu próprio processo iniciado com `manage.py runserver` relativo porque o snapshot Windows preenchia `project_root` com `command_line`; a comparação de raiz então não podia provar a instalação dona do processo. A parada oficial retornava `RuntimeOperationError: Processo registrado não pertence a esta instalação` e deixava servidor/porta ativos. Também foi observado que o launcher do venv pode criar um filho direto que realmente possui o listener; encerrar somente o PID do launcher não garantia a liberação da porta.

A correção vincula estado de runtime, instalação, executável absoluto, comando exato, argumentos, PID, processo pai e instante de criação. Estados legados relativos são aceitos apenas se raiz/instalação, executável, linha de comando e antiguidade do processo os identificarem sem ambiguidade. A identificação do listener considera somente filho direto validado. A parada revalida a identidade antes de terminar handles individuais; não encerra árvores, não confia apenas em PID/nome/porta e recusa processo de outra instalação, PID reutilizado, metadados obsoletos e listeners não registrados. Erros de `OSError` mantêm a causa original. O teste de regressão foi escrito e executado antes da correção; reproduziu a recusa indevida do próprio comando legado. Após a correção, o caso passou.

### Arquivos desta correção

- `construct/backup/runtime.py` — identidade v2, compatibilidade legacy estrita, validação de filho e parada segura.
- `construct/backup/windows.py` — evidências de PID pai/creation time, filhos diretos, liveness e encerramento por handle validado.
- `construct/backup/cli.py` — identidade de instalação propagada aos comandos de parada/manutenção.
- `construct/backup/test_runtime.py` — regressão e proteções contra PID reutilizado, instalação alheia, listener estrangeiro e launcher do venv.
- `caixa/services.py` — mensagem consistente de atualização/repetição com a mesma chave para a corrida de negócio de abertura de sessão, coberta por teste.

Os demais arquivos listados na seção “Arquivos alterados” pertencem à implementação Caixa/Vendas existente preservada nesta árvore. Não houve edição de interface nesta correção.

### Teste vermelho/verde e evidência de ciclo real

Antes da implementação, `test_stop_recognizes_registered_process_with_relative_manage_py_command` falhou: a parada lançou `RuntimeOperationError: Processo registrado não pertence a esta instalação` para o próprio processo lançado de `manage.py runserver`. Depois da implementação, o conjunto `construct.backup.test_runtime` passou: 15 testes, `Ran 15 tests in 0.403s`, `OK`; duração total do comando 2.163s.

Uma suíte intermediária também expôs uma falha concorrente no contrato da abertura de sessão: o segundo chamador recebia o resultado de negócio “já existe sessão” sem a orientação de atualizar e repetir com a mesma chave. A mensagem do serviço foi alinhada ao caminho de bloqueio recuperável; a repetição do teste de corrida e a suíte completa final passaram. Essa correção não introduz retry automático nem altera a regra de sessão única.

Transcript completo do ensaio: `qa-browser-caixa/supervisor-cycle-a4e69c56.transcript.txt`.

```text
Instalação: graphify-out/test-tmp/supervisor-cycle-a4e69c56/install (scratch)
Backup: válido; verificação oficial respondeu “Pacote válido”.
Migrações: executadas pelo supervisor no SQLite descartável.
1º ciclo: START_PID=32616; REGISTERED_PID=25732; LISTENER_PID=18992; LISTENER_PARENT=25732; HTTP=200.
Parada oficial: exit=0; START_SUPERVISOR_EXIT=0; STATE=cleared; LISTENER=free.
2º ciclo: START_PID=25996; REGISTERED_PID=24648; LISTENER_PID=35624; LISTENER_PARENT=24648; HTTP=200.
2ª parada oficial: exit=0; START_SUPERVISOR_EXIT=0; STATE=cleared; LISTENER=free.
Processo alheio: PID=30648; parada oficial exit=1; PRESERVED=True; LISTENER_OWNER=30648.
PORT_15002_AFTER=22672; unchanged=True.
CYCLE_RESULT=PASS; BACKUP=MIGRATE=HTTP200=OFFICIAL_STOP=PORT_FREE=RESTART_STOP=FOREIGN_REFUSED
```

Isso comprova processos e resposta HTTP reais em scratch, limpeza de metadados, liberação da porta, segundo ciclo e proteção contra processo alheio. O transcript registra timeout de algumas sondagens iniciais enquanto o servidor subia; o readiness final em cada ciclo foi 200. O processo alheio foi encerrado somente por handle exato da fixture criada pelo ensaio após a prova de preservação. A pasta scratch (DB, mídia e backup isolados) foi removida ao final. Nenhuma ação foi feita no servidor do usuário em `127.0.0.1:15002`.

### Verificações finais do candidato

Interpretador e pacotes vieram do venv do projeto, não do Python global: `C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe`; Python 3.14.0, Django 5.0.6, SQLite 3.50.4, Ruff 0.17.3 e mypy 2.4.1. O banco e os caminhos Django de validação estavam limitados a `graphify-out/test-tmp/caixa-validation`. A suíte usa o invólucro Python que fixa `tempfile.tempdir` para o `TEMP` descartável:

```powershell
$caixaRoot = 'C:\desenvolvimento\Contruct\.worktrees\caixa'
$caixaPython = 'C:\desenvolvimento\Contruct\Project_construct\venv\Scripts\python.exe'
$tempRoot = Join-Path $caixaRoot 'graphify-out\test-tmp'
$env:TEMP = $tempRoot; $env:TMP = $tempRoot; $env:TMPDIR = $tempRoot
$env:PYTHONPATH = "$caixaRoot\qa-browser-caixa;$caixaRoot"
$env:DJANGO_SETTINGS_MODULE = 'validation_settings'; $env:PYTHONIOENCODING = 'utf-8'
$runner = "import os,runpy,tempfile; tempfile.tempdir=os.environ['TEMP']; runpy.run_path('manage.py',run_name='__main__')"
& $caixaPython -c $runner test construct.backup.test_runtime --verbosity 1 --settings=validation_settings
& $caixaPython -c $runner test caixa.tests.test_concorrencia --verbosity 1 --settings=validation_settings
& $caixaPython -c $runner test --verbosity 1 --settings=validation_settings
& $caixaPython manage.py check --settings=validation_settings
& $caixaPython manage.py makemigrations --check --dry-run --settings=validation_settings
& $caixaPython -m ruff check .
& $caixaPython -m mypy . --exclude-gitignore
git diff --check
```

Saídas observadas após as últimas alterações de código:

```text
Supervisor focado: 15 tests; Ran 15 tests in 0.403s; OK; exit 0; duração do comando 2.163s.
Corrida de abertura: Ran 1 test in 4.072s; OK; exit 0; duração do comando 9.528s.
Suíte completa: Found 367 test(s); Ran 367 tests in 125.095s; OK; exit 0; duração do comando 2:10.954.
Ruff 0.17.3: All checks passed!; exit 0; 0.4285s.
mypy 2.4.1: Success: no issues found in 59 source files; exit 0; 1.9079s.
Django: System check identified no issues (0 silenced).; exit 0; 1.9164s.
Migration drift: No changes detected; exit 0; 2.2224s.
git diff --check: exit 0; 0.189s; aviso de normalização LF→CRLF em vendas/templates/registrar_venda.html.
```

`mypy .` sem opção inclui cópias de QA ignoradas pelo Git e pode emitir erro de módulo duplicado (`manage`/`caixa`); o caminho comprovado para analisar as fontes candidatas sem suprimir erros é `mypy . --exclude-gitignore`. Não foram adicionados ignores, casts ou supressões para forçar sucesso. O venv não contém `django-stubs`/`django-stubs-ext`, e o projeto não configura plugin mypy.

### QA visual, offline, ferramenta e status

Nesta retomada, `cua.getState()` retornou `Browsers: Error: Local browser use is disabled in Work settings`. Configurações não foram alteradas. Nenhuma navegação, interceptação, alteração de cache/service worker/viewport ou nova captura foi feita. As evidências anteriores de fluxo mobile e teclado permanecem preservadas; desktop tem evidência anterior de venda/comprovante e movimentos, mas ainda não cobre restituição e telas/detalhes de fechamento. Operação offline com cache frio e bloqueio somente externo, preservando localhost, continua sem prova: login, venda mista, comprovante e fechamento precisam ser executados em browser integrado disponível.

O skill Impeccable foi lido e seu contexto carregado; o launcher anuncia `detect`, mas não os comandos `audit` nem `polish`. A execução permitida do detector em templates/área Caixa terminou com exit 0, mas alertou que não resolveu os caminhos Django `{% static %}` e que apenas regras CSS foram escaneadas; HTML/DOM, contraste e responsividade não foram integralmente avaliados. Isso não equivale a auditoria visual limpa. Não houve código de UI modificado nesta correção.

Graphify foi consultado de forma escopada antes da investigação do supervisor e novamente antes da alteração do serviço de abertura concorrente. A atualização integral `graphify update . --no-cluster` foi tentada, permaneceu por mais de 12 minutos sem saída e foi interrompida; o timestamp do grafo não avançou. Portanto não se declara grafo atualizado nesta entrega.

| Critério | Status |
|---|---|
| Teste vermelho da identificação relativa do próprio `manage.py` | PASS — falha observada antes da correção |
| Testes de identidade do processo, instalação, estado legado, stale metadata e launcher | PASS — 15/15 |
| Backup descartável, migrações, servidor HTTP real, readiness 200, parada oficial, porta livre e restart | PASS — transcript citado |
| Recusa segura e preservação de processo alheio | PASS — exit 1, processo/porta preservados durante a prova |
| Testes focados e suíte completa | PASS — 367 testes |
| Ruff, mypy, Django check, migration drift e diff check | PASS — saídas e tempos acima |
| QA mobile/teclado anterior | PASS preservado; UI não alterada nesta correção |
| QA desktop residual de restituição e fechamento | HOLD — browser local indisponível |
| QA offline seletivo | HOLD — browser local indisponível; nenhuma tentativa alegada |
| Impeccable audit/polish | HOLD de ferramenta — comandos não fornecidos |
| Atualização completa Graphify | HOLD técnico — atualização sem saída por >12 min, cancelada |
| Auditoria independente da árvore congelada | HOLD — ainda não executada/aceita |
| Etapa 10, corte e ativação real | HOLD — proibidos nesta execução |

O worktree permanece sem commit/push/merge. O manifesto `docs/superpowers/plans/2026-11-03-caixa-manifesto.sha256` identifica o candidato submetido à reauditoria.

## Aceite da Etapa 9 — 04/11/2026

Sol encerrou a Etapa 9 como PASS com exceção autorizada pelo usuário. A reauditoria confirmou 54/54 hashes antes e depois, 367 testes, checks, dois ciclos reais do supervisor, preservação de processo alheio, restituição e fechamento em desktop e o fluxo offline com cache frio e localhost acessível.

Para esta entrega, o usuário aceitou a inspeção no navegador integrado em substituição ao Impeccable audit/polish e a extração e consulta escopadas do Graphify em substituição à extração integral. O manifesto original registra a versão auditada, anterior a este adendo e à atualização do PRD. A Etapa 10 segue condicionada à liberação operacional.

[Conferência do manifesto](C:/Users/johnn/.codex/visualizations/2026/11/04/01a1a7b2-bef4-7410-982f-1e88ad041620/manifest-final.txt) · [Suíte independente](C:/Users/johnn/.codex/visualizations/2026/11/04/01a1a7b2-bef4-7410-982f-1e88ad041620/suite.txt)
