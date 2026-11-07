# Reauditoria — pagamento de obrigação reaberta

Data: 2026-11-07

Branch: `codex/financeiro`

Worktree: `C:\desenvolvimento\Contruct\.worktrees\financeiro`

Base confirmada: `54ad0b6e586cd28ffe7df6832c93c1e38c7c41fb`

Commit da correção: `87c4d574db2ea53b139704ae3a529128cb3d0a66`

## Causa raiz

`PagamentoContaPagar.conta_pagar` era `OneToOneField`. Depois de `pagar → estornar`, o serviço reabria a obrigação, mas o pagamento antigo continuava ocupando a unicidade física. O segundo pagamento falhava em:

```text
sqlite3.IntegrityError: UNIQUE constraint failed: financeiro_pagamentocontapagar.conta_pagar_id
financeiro.services.FinanceOperationError: Essa conta a pagar já foi paga.
```

## Correção

- `PagamentoContaPagar.conta_pagar` passou a `ForeignKey`, preservando todos os pagamentos históricos.
- O campo `vigente` identifica o pagamento integral atual.
- A restrição `financeiro_pagamento_conta_pagar_vigente_unico` garante no banco no máximo um pagamento vigente por obrigação.
- O estorno marca o pagamento original como histórico e reabre a obrigação na mesma transação do crédito de reversão.
- O pagamento consulta a obrigação bloqueada e somente cria baixa quando ela está aberta e sem pagamento vigente.
- A migração `0004_pagamento_historico_vigente` marca como históricos os pagamentos que já possuem estorno, sem apagar pagamentos, estornos ou lançamentos existentes.
- A propriedade `ContaPagar.pagamento` mantém a interface apontando apenas para o pagamento vigente.

## Evidência TDD

Regressão escrita antes da correção e executada no candidato-base:

```text
Found 1 test(s).
ERROR
sqlite3.IntegrityError: UNIQUE constraint failed: financeiro_pagamentocontapagar.conta_pagar_id
financeiro.services.FinanceOperationError: Essa conta a pagar já foi paga.
Ran 1 test in 0.824s
FAILED (errors=1)
```

Depois da correção:

```text
Found 1 test(s).
Ran 1 test in 0.828s
OK
```

O teste comprova saldo `R$ 500 → R$ 380 → R$ 500 → R$ 380`, dois pagamentos históricos, um estorno, um único pagamento `vigente=True`, três lançamentos e obrigação paga. O replay da primeira chave retorna o pagamento histórico sem novo débito. O replay da chave do estorno antigo retorna o estorno original sem reabrir a obrigação.

## Concorrência e migração

Com duas conexões SQLite independentes, o teste concorrente passou:

```text
Found 1 test(s).
Ran 1 test in 0.520s
OK
```

A prova encontrou dois pagamentos históricos, um pagamento vigente, um estorno e somente dois débitos totais — o débito antigo e um único débito novo. A obrigação terminou em `PAGA` e o saldo em `R$ 380`.

A migração foi executada em `C:\desenvolvimento\Contruct\.tmp-financeiro-etapa3-test\migration.sqlite3`, iniciando no estado `0003`, com uma obrigação paga e outra já estornada:

```text
MIGRATION_OK
PAYMENTS_TOTAL 2
CURRENT_PAID 1
CURRENT_REFUNDED 0
HISTORICAL_REFUNDED 1
PAYABLES_PAID 2
```

## Testes e checks

| Verificação | Resultado |
|---|---|
| Focado: contas a pagar, serviços financeiros, views e Compras | PASS — 66/66, 77,859 s |
| Recorte serial funcional | PASS — 199/199, 78,020 s |
| Suíte completa serial | PASS — 472/472, 234,210 s |
| `manage.py check` | PASS — no issues |
| `makemigrations --check --dry-run` | PASS — no changes detected |
| Ruff | PASS — all checks passed |
| mypy | PASS — no issues in 3 source files |
| `git diff --check` | PASS — somente avisos LF/CRLF do checkout |

## Interface e Impeccable

- Teste automatizado de view: PASS — a gerente paga após a reabertura, o painel expõe novamente a ação `Pagar` e o detalhe mostra duas ocorrências de `Pagamento de conta a pagar`; saldo final `R$ 380`.
- Navegador desktop/mobile: HOLD — o navegador disponível retornou `ERR_CONNECTION_REFUSED` para o servidor descartável em `127.0.0.1:15024`.
- Impeccable `detect`: executado; retornou `[]`, com aviso de resolução incompleta do stylesheet Django.
- Impeccable `audit`: HOLD — retorno `Unknown command: "audit"`.
- Impeccable `polish`: HOLD — retorno `Unknown command: "polish"`.

O HOLD do Impeccable está separado do QA automatizado e do bloqueio de navegador. A atualização da ferramenta não foi executada.

## Veredito por critério

| Critério | Veredito |
|---|---|
| Pagamento após estorno preserva histórico e estado correto | PASS |
| Unicidade de pagamento vigente no banco | PASS |
| Replay do pagamento e do estorno antigos | PASS |
| Concorrência SQLite com um único novo débito | PASS |
| Migração de obrigações pagas e estornadas | PASS |
| Permissões, transação e checks do projeto | PASS |
| Confirmação visual no navegador | HOLD |
| Impeccable audit/polish | HOLD |

Estado final: candidato congelado no commit `87c4d574db2ea53b139704ae3a529128cb3d0a66`, sem push, merge ou implantação. A Etapa 2 e o fechamento mensal permanecem fora do escopo.
