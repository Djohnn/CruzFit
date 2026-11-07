# Relatório de auditoria — Financeiro, Etapas 0 e 1

Data: 2026-11-06
Branch: `codex/financeiro`
Worktree: `C:\desenvolvimento\Contruct\.worktrees\financeiro`
Base usada: `222c45fb3c2e3413637a7e9a40d4526e61d78078` (`feat: gerenciar e converter variacoes no estoque`)
Código funcional congelado: `27e08c289f4927e0f7ae6483ee1501922b467b23` (`fix: serializa estornos concorrentes`); relatório e evidências acompanham o commit local final desta worktree.

## Proveniência e preparação

- `main` local apontava para `222c45fb3c2e3413637a7e9a40d4526e61d78078`.
- `origin/main` apontava para `77e819973d9f846a3a9657e3c081ee585cd0b507`.
- A referência local estava um commit à frente da remota, diferença esperada e confirmada antes da criação da worktree. Nenhuma outra base foi escolhida.
- O Graphify foi consultado antes da implementação. O mapa de integração registrado foi:
  - Caixa: serviços, sessões, movimentos, fechamento e restituições; depósitos futuros devem entrar por serviço cruzado, não por acesso direto aos modelos.
  - Vendas: `registrar_venda`, cancelamentos, devoluções e estornos; Pix/cartão permanecem nas próximas etapas.
  - Compras: recebimento, cancelamento, permissões e serviços; contas a pagar permanecem na Etapa 3.
  - Permissões: regra de gerente ativo/superusuário em `cruzfit.roles` e `gerente_required`; o Financeiro também valida a regra no serviço.
  - Auditoria/idempotência: padrões de `caixa.services`, `estoque.services` e `vendas.services`; o Financeiro usa assinatura do payload, evento auditável e transação atômica.
- Validação descartável: banco SQLite, `MEDIA_ROOT`, static root, cache do mypy e servidor local foram usados em `C:\Users\johnn\.codex\visualizations\2026\11\06\01a1b22c-4afc-7f23-98e1-e390cdb31c9c\financeiro-test`. O diretório `graphify-out\test-tmp` da worktree também foi criado somente para os testes. Nenhum dado real foi usado.
- Corte: o saldo inicial é a fotografia na abertura da `data_corte_financeiro`, antes do primeiro movimento. O serviço rejeita qualquer data anterior antes de criar lançamento, operação ou auditoria; a data do corte é inclusiva. Assim, movimentos antigos não são recontados como créditos/debitos após a ativação. A data real de implantação não foi escolhida.

## Entrega

- Novo app `financeiro`, com contas, saldo inicial separado, livro append-only de lançamentos, aportes, transferências com débito/crédito explícitos, estornos vinculados e eventos de auditoria.
- Valores e saldos usam `Decimal`; saldo inicial, aportes e transferências ficam fora da receita operacional da Etapa 1.
- Escritas passam por serviços transacionais com validação server-side de gerente/superusuário, motivo obrigatório, chave de idempotência, assinatura do payload e auditoria.
- Reenvio equivalente retorna a operação existente. Reutilização da chave, inclusive entre tipos de operação distintos, é rejeitada.
- Transferências bloqueiam as duas contas em ordem determinística e gravam as duas pernas na mesma transação; falha entre débito e crédito desfaz tudo.
- Interface server-rendered responsiva: painel, cadastro, saldo, histórico, aporte, transferência e estorno. O vendedor recebe `403` nas escritas diretas.
- Correções desta reauditoria: `estornar_lancamento` verifica chave e assinatura dentro da transação, antes de rejeitar um lançamento já estornado; a proteção `select_for_update()` e a validação de segundo estorno permanecem dentro da transação. Em SQLite, contenções identificadas exclusivamente por `SQLITE_BUSY`/`SQLITE_LOCKED` reiniciam a transação inteira por no máximo cinco tentativas, com backoff curto; outros erros de banco são relançados.
- A regressão concorrente usa duas conexões independentes e chamadas sincronizadas. O resultado comprovado é um estorno e um lançamento de reversão, com um retorno `created=True`, outro `created=False` e saldo final de R$ 500,00.
- Não foram implementados Pix, cartão, integração com Caixa, contas a pagar, fechamento mensal ou ativação real.

## Arquivos alterados

- `cruzfit/settings.py`, `cruzfit/urls.py`, `templates/base.html`
- `financeiro/apps.py`, `models.py`, `services.py`, `queries.py`, `views.py`, `urls.py`
- `financeiro/migrations/0001_initial.py`
- `financeiro/templates/financeiro/{painel,detalhe,conta_form,movimento_form}.html`
- `templates/static/financeiro/css/financeiro.css`
- `financeiro/tests/test_services.py`, `financeiro/tests/test_views.py`
- `docs/superpowers/specs/2026-11-06-financeiro-contas-movimentos-design.md`
- `docs/superpowers/plans/2026-11-06-financeiro-contas-movimentos.md`
- `docs/superpowers/plans/2026-11-06-financeiro-fechamento-mensal-prd.md`
- Este relatório e a cópia do PRD com os checkboxes comprovados.

## Baseline e verificação

Baseline focado antes da implementação: `154 testes em 49.347s — OK`, usando Caixa funcional, Compras, Vendas/estornos e dashboard. O teste de concorrência do baseline já travava no primeiro caso com SQLite/processos concorrentes. A reauditoria anterior confirmou também `170/170` no recorte funcional; o recorte atual soma dois testes de regressão e, por isso, tem 172 casos.

Comandos finais e evidências:

```text
manage.py test financeiro.tests --settings=validation_financeiro_settings --keepdb --verbosity 1
Ran 18 tests in 19.449s
OK

$labels=@('caixa.tests.test_corte','caixa.tests.test_fechamento','caixa.tests.test_finalizacao','caixa.tests.test_fluxo_integrado','caixa.tests.test_interface','caixa.tests.test_migracao_legado','caixa.tests.test_movimentos','caixa.tests.test_pagamentos','caixa.tests.test_restituicoes','caixa.tests.test_sessoes','compras.tests.test_compatibilidade','compras.tests.test_interface','compras.tests.test_services','estoque.test_vendas','estoque.test_reversoes','usuarios.test_dashboard','financeiro.tests')
manage.py test $labels --settings=validation_financeiro_settings --noinput --verbosity 0
Ran 172 tests in 79.278s
OK

manage.py check
System check identified no issues (0 silenced).

manage.py makemigrations --check --dry-run
No changes detected

python -m ruff check . --no-cache
All checks passed!

mypy . --exclude-gitignore --cache-dir C:\Users\johnn\.codex\visualizations\2026\11\06\01a1b22c-4afc-7f23-98e1-e390cdb31c9c\financeiro-test\mypy-cache
Success: no issues found in 77 source files

git diff --check
Exit 0; apenas avisos de conversão LF/CRLF dos arquivos editados.
```

Regressão TDD do estorno:

```text
RED — antes da correção concorrente:
Ran 1 test in 0.897s
FAILED (failures=1)
  OperationalError('database is locked') em uma das duas conexões SQLite sincronizadas

GREEN — depois da correção concorrente:
Ran 1 test in 0.890s
OK
```

Os três casos de idempotência do estorno continuam cobertos pelo recorte de serviços (15 testes OK): replay equivalente retorna `created=False` e preserva contagens/saldo; payload diferente rejeita a chave sem gravar; outra chave rejeita o segundo estorno sem gravar. A nova regressão concorrente comprovou duas conexões independentes, exatamente um estorno, um lançamento de reversão, retornos `created=True`/`created=False` e saldo final de R$ 500,00. O teste adicional de erro não-lock confirmou que `OperationalError` alheio à contenção continua identificável.

Comparação de reauditoria do recorte amplo que exclui `caixa.tests.test_concorrencia` e `compras.tests.test_concorrencia`: a base `222c45f` e o candidato reproduziram `78` eventos de erro e `50` identificadores únicos, nos mesmos casos legados/ambientais. A suíte completa continua em HOLD; não há evidência de falha nova em `financeiro` nesse recorte. A execução funcional limpa do candidato, separada acima, terminou em 172/172.

## Interface

- Desktop: painel e histórico validados no navegador em viewport `1280×720`.
- Mobile: painel, detalhe/histórico e formulário de estorno validados no navegador in-app em viewport `390×844`; cartões, ações e campos permaneceram acessíveis sem overflow horizontal visível.
- Fluxo descartável exercitado: login do gerente sintético, cadastro de Conta A com R$ 500,00 em 06/11/2026 e aporte de R$ 50,00; a tela mostrou saldo R$ 550,00, receita operacional R$ 0,00 e o histórico append-only.
- Impeccable: `context` foi executado. `audit` e `polish` foram consultados separadamente e ambos retornaram `Unknown command`; a instalação local é v4.6.0 e informou v4.8.0 disponível. Nenhuma atualização foi executada. Portanto, audit/polish automatizados ficam HOLD, separadamente do QA visual, que passou.
- O CLI agent-browser não abriu por falta de permissão no socket padrão; o navegador in-app do Codex estava disponível e foi usado para o QA visual mobile.

## Critérios

| Critério | Status | Evidência |
|---|---|---|
| Etapa 0: base, worktree, Graphify e descartáveis | PASS | Proveniência acima; branch isolado e ambiente temporário registrados. |
| Saldo inicial datado, `Decimal` e receita operacional separada | PASS | Serviços/modelos e teste de saldo inicial; receita permanece R$ 0,00. |
| Véspera rejeitada sem gravação; dia do corte aceito | PASS | Testes com `2026-11-05` e `2026-11-06`; mensagem clara e contagens preservadas. |
| Transferência R$ 80: R$ 500/R$ 100 → R$ 420/R$ 180 | PASS | Teste de serviço, duas pernas de livro e saldo total R$ 600,00. |
| Replay equivalente sem duplicar | PASS | Testes de replay em serviço e interface. |
| Replay de estorno com mesma chave e payload | PASS | Regressão GREEN: retorna estorno original com `created=False`, sem novas linhas ou alteração de saldo. |
| Estorno com mesma chave e payload diferente | PASS | Regressão GREEN: conflito de idempotência, sem gravação. |
| Estorno com outra chave após estorno existente | PASS | Regressão GREEN: rejeita segundo estorno, sem gravação. |
| Chave reutilizada com dados/tipo diferente rejeitada | PASS | Teste regressivo global de idempotência; sem aporte parcial. |
| Falha entre débito/crédito faz rollback total | PASS | Teste com falha simulada na segunda gravação; zero lançamento parcial. |
| Aporte e estorno preservam histórico | PASS | Testes de serviço/interface; lançamento original permanece e inverso é vinculado. |
| Permissão server-side e vendedor sem escrita | PASS | Serviços e POSTs diretos retornam `403` para vendedor. |
| Interface desktop/mobile | PASS | Navegador real em 1280×720 e 390×844. |
| Concorrência de estorno no SQLite | PASS | RED com `database is locked`; GREEN com duas conexões, um estorno, uma reversão, `created=True`/`created=False` e saldo R$ 500,00. |
| Erros de banco não relacionados à contenção | PASS | Teste preserva e relança o `OperationalError` original. |
| Suíte serial funcional | PASS | 172 testes, `OK`; 170/170 é a contagem da reauditoria anterior, antes dos dois testes novos. |
| Suíte completa legada/concor­rência | HOLD | Comparação base/candidato no recorte amplo sem os dois módulos de concorrência: 78 eventos de erro e 50 identificadores únicos em ambos; a suíte completa permanece fora do PASS. |
| Impeccable audit/polish | HOLD | `audit` e `polish` indisponíveis (`Unknown command`); atualização v4.8.0 não executada. |

Conclusão: candidato da Etapa 1 funcionalmente PASS e pronto para auditoria, com os dois HOLDs ambientais explicitamente registrados. Merge, push, ativação da data real e integração com o cliente continuam fora deste trabalho.
