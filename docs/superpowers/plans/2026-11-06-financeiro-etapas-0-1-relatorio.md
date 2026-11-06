# Relatório de auditoria — Financeiro, Etapas 0 e 1

Data: 2026-11-06
Branch: `codex/financeiro`
Worktree: `C:\desenvolvimento\Contruct\.worktrees\financeiro`
Base usada: `222c45fb3c2e3413637a7e9a40d4526e61d78078` (`feat: gerenciar e converter variacoes no estoque`)

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
- Este relatório e a cópia do PRD com os checkboxes comprovados.

## Baseline e verificação

Baseline focado antes da implementação: `154 testes em 49.347s — OK`, usando Caixa funcional, Compras, Vendas/estornos e dashboard. O teste de concorrência do baseline já travava no primeiro caso com SQLite/processos concorrentes.

Comandos finais e evidências:

```text
manage.py test financeiro.tests --keepdb --verbosity 1
Ran 13 tests in 13.844s
OK

manage.py test [suíte funcional serial: Caixa, Compras, Vendas, Estoque, dashboard, Financeiro]
Ran 167 tests in 73.598s
OK

manage.py check
System check identified no issues (0 silenced).

manage.py makemigrations --check --dry-run
No changes detected

python -m ruff check . --no-cache
All checks passed!

mypy . --exclude-gitignore
Success: no issues found in 77 source files

git diff --check
Sem erros de whitespace; o Git apenas informou conversão LF/CRLF no documento de plano.
```

Foi feita uma tentativa da suíte completa (`439 testes descobertos`). Ela parou sem progresso nos testes de concorrência; o módulo isolado `caixa.tests.test_concorrencia` travou já no primeiro caso. A execução ampla sem esses dois módulos de concorrência terminou com `1 failure / 79 errors`, todos em áreas legadas/ambientais (permissão de diretórios temporários de mídia, fixture de etiqueta padrão ausente e testes de backup/runtime). Esse gate fica HOLD; não há traceback em `financeiro`.

## Interface

- Desktop: painel e histórico validados no navegador em viewport `1280×720`.
- Mobile: painel, detalhe da conta e formulário de aporte validados em viewport `390×844`; cartões, ações e campos permaneceram acessíveis sem overflow horizontal visível.
- Fluxo descartável exercitado: login do gerente sintético, cadastro de Conta A com R$ 500,00 em 06/11/2026 e aporte de R$ 50,00; a tela mostrou saldo R$ 550,00, receita operacional R$ 0,00 e o histórico append-only.
- Impeccable: `context` foi executado. A instalação local não possui os subcomandos `audit` e `polish` (`Unknown command`), e o detector não conseguiu resolver o stylesheet Django dinâmico; portanto a auditoria/polish automatizados ficam HOLD, compensados nesta etapa pela validação visual real e pelos testes de views.

## Critérios

| Critério | Status | Evidência |
|---|---|---|
| Etapa 0: base, worktree, Graphify e descartáveis | PASS | Proveniência acima; branch isolado e ambiente temporário registrados. |
| Saldo inicial datado, `Decimal` e receita operacional separada | PASS | Serviços/modelos e teste de saldo inicial; receita permanece R$ 0,00. |
| Véspera rejeitada sem gravação; dia do corte aceito | PASS | Testes com `2026-11-05` e `2026-11-06`; mensagem clara e contagens preservadas. |
| Transferência R$ 80: R$ 500/R$ 100 → R$ 420/R$ 180 | PASS | Teste de serviço, duas pernas de livro e saldo total R$ 600,00. |
| Replay equivalente sem duplicar | PASS | Testes de replay em serviço e interface. |
| Chave reutilizada com dados/tipo diferente rejeitada | PASS | Teste regressivo global de idempotência; sem aporte parcial. |
| Falha entre débito/crédito faz rollback total | PASS | Teste com falha simulada na segunda gravação; zero lançamento parcial. |
| Aporte e estorno preservam histórico | PASS | Testes de serviço/interface; lançamento original permanece e inverso é vinculado. |
| Permissão server-side e vendedor sem escrita | PASS | Serviços e POSTs diretos retornam `403` para vendedor. |
| Interface desktop/mobile | PASS | Navegador real em 1280×720 e 390×844. |
| Suíte serial funcional | PASS | 167 testes, `OK`. |
| Suíte completa legada/concor­rência | HOLD | Bloqueio ambiental reproduzido no baseline; 439 testes não concluíram. |
| Impeccable audit/polish | HOLD | Subcomandos indisponíveis na instalação local. |

Conclusão: candidato da Etapa 1 funcionalmente PASS e pronto para auditoria, com os dois HOLDs ambientais explicitamente registrados. Merge, push, ativação da data real e integração com o cliente continuam fora deste trabalho.
