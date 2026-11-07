# Etapa 4 — Fechamento mensal: relatório de auditoria

Data: 2026-11-07

Branch: `codex/financeiro`

Worktree: `C:\desenvolvimento\Contruct\.worktrees\financeiro`

Base solicitada: `98255829d382dc24200027bf0bb8b8bad781f5c0`

SHA da implementação congelada: `071f63e` (`feat: implement fechamento mensal`)

Commits de documentação anteriores desta entrega:

- `543e2ca` — especificação aprovada do fechamento mensal.
- `fdf2f5c` — plano de implementação.

Não houve merge, push ou implantação. A `main`, as demais worktrees e os dados reais foram preservados.

## Escopo entregue

- Modelos versionados `FechamentoMensal`, `FechamentoMensalConta` e `ReaberturaFechamentoMensal`.
- Migração `financeiro.0005_fechamentomensal_fechamentomensalconta_and_more`.
- Prévia mensal por datas efetivas, com saldo inicial/final por conta, entradas operacionais líquidas, contas do mês, pagamentos efetivos, abertas, vencidas anteriores e cartão a receber.
- Reconstrução temporal de pagamento e estorno: uma obrigação paga em dezembro e estornada em janeiro aparece aberta em novembro, paga em dezembro e aberta novamente em janeiro.
- Assinatura determinística da prévia com mês, margem, identidade e dados dos lançamentos, obrigações, pagamentos, estornos, cartões, saldos e totais. Trocar um lançamento mantendo o total altera a assinatura.
- Confirmação transacional com recálculo dentro da transação, proteção contra prévia obsoleta, idempotência, auditoria e uma versão vigente por mês.
- Reabertura transacional e auditada, preservando os valores da versão anterior e permitindo nova versão posterior.
- Interface gerencial com cobertura, situação das obrigações e saldo disponível em áreas separadas, além do histórico e da ação de reabertura.
- Bloqueio server-side de escrita por vendedor.

## Graphify e preparação

Foi consultada a cópia descartável do grafo:

`C:\desenvolvimento\Contruct\.tmp-financeiro-etapa3-graphify-7f5a1b`

Consultas principais:

- `LancamentoFinanceiro calcular_saldo receita_operacional_consolidada entradas operacionais estornos`
- `RecebivelCartao DepositoCartao RecebimentoPix liquidar_cartao`
- `estornar_lancamento() auditoria idempotencia`
- `ContaPagar PagamentoContaPagar EstornoPagamentoContaPagar vencimento pagamento`

O grafo confirmou os vínculos de lançamentos, receita operacional, Pix, cartão, liquidação, estorno e auditoria. A consulta aos nomes de Contas a pagar não encontrou nós porque o índice disponível antecede a materialização da Etapa 3; os serviços atuais foram revisados diretamente após essa lacuna e os testes existentes confirmaram os contratos de pagamento/estorno.

Ambiente descartável:

- Diretório: `C:\desenvolvimento\Contruct\.tmp-financeiro-etapa4-test`
- Banco visual: `visual.sqlite3`
- Configuração: `qa_settings.py`
- Mídia e temporários: dentro do mesmo diretório descartável.
- Nenhum banco, mídia ou arquivo de dados reais foi usado.

## Teste RED antes da implementação

Com a regressão escrita antes dos serviços:

```text
Found 12 test(s).
...
Ran 12 tests in 10.207s

FAILED (failures=2, errors=10)
```

Os erros eram APIs ausentes de prévia/confirmação/reabertura; as duas falhas eram as rotas ainda não registradas. Não houve erro de coleta.

Após a criação dos modelos, o RED funcional permaneceu em 12 testes, com os mesmos 10 erros de serviços e 2 falhas de rotas. A implementação só foi iniciada depois desse registro.

## Evidência funcional

```text
Etapa 4 focada:
Ran 14 tests in 11.851s
OK

Concorrência de confirmações:
Ran 1 test in 0.567s
OK

Recorte focado Financeiro/Compras:
Ran 80 tests in 91.230s
OK

Recorte serial comparável:
Ran 213 tests in 92.135s
OK

Suíte ampla serial:
Ran 486 tests in 258.982s
OK
```

A regressão comprova:

- R$ 900 de entradas e R$ 1.000 de obrigações: `MAL`, cobertura `-100`.
- R$ 1.050: `NO_LIMITE`, cobertura `50`.
- R$ 1.100: `BEM`, cobertura `100`.
- A mesma obrigação de R$ 1.000 permanece aberta nos três cenários.
- Saldo inicial de R$ 500 fica separado da receita operacional.
- Cartão aprovado em novembro permanece a receber; depósito líquido de R$ 97 em dezembro entra somente em dezembro.
- Pagamento em dezembro e estorno em janeiro são reconstruídos por data efetiva.
- Reenvio equivalente retorna a versão original com `created=False`.
- Chave com dados diferentes é conflito sem gravação.
- Duas confirmações simultâneas criam uma única versão vigente e um único snapshot de conta.
- Vendedor não confirma nem reabre pelo serviço ou POST direto.

Smoke de migração no banco descartável:

```text
MIGRATION_HISTORY_OK 1050.00 50.00 1030.00
```

O resultado representa obrigações abertas em novembro, pagas em dezembro e reabertas por estorno em janeiro, coexistindo com registros já pagos e já estornados.

## Checks

| Critério | Veredito | Evidência |
|---|---|---|
| Prévia histórica por datas efetivas | PASS | Testes de pagamento/estorno e cartão em meses diferentes; assinatura usa dados datados |
| Cobertura e classificação | PASS | 900/Mal, 1050/No limite, 1100/Bem; obrigação aberta permanece visível |
| Saldo disponível separado | PASS | DTO e interface separam saldo por conta, cobertura e situação das obrigações |
| Confirmação transacional e prévia obsoleta | PASS | Recalculo dentro da transação; prévia alterada é rejeitada sem snapshot |
| Histórico, reabertura e nova versão | PASS | Versão anterior preservada como REABERTO; nova confirmação incrementa `versao` |
| Idempotência e concorrência | PASS | Replay, conflito, segunda reabertura e duas conexões SQLite cobertos |
| Permissões e auditoria | PASS | Gerente/superusuário no servidor; vendedor retorna 403; evento de auditoria gravado |
| Migração em cópia descartável | PASS | `manage.py migrate --settings=qa_settings` aplicou 0005; smoke histórico OK |
| Django check | PASS | `System check identified no issues (0 silenced).` |
| Drift de migrações | PASS | `No changes detected` |
| Ruff | PASS | `ruff.exe check financeiro` — `All checks passed!` |
| mypy | PASS | `mypy.exe financeiro` — `Success: no issues found in 8 source files` |
| Higiene do diff | PASS | `git diff --cached --check` sem ocorrências; avisos restantes são LF/CRLF do checkout |
| QA visual desktop/mobile | HOLD | O navegador não alcançou `http://127.0.0.1:15004` nem `http://localhost:15004`; ambos retornaram `net::ERR_CONNECTION_TIMED_OUT`. Os testes de views passaram, mas não substituem inspeção visual |
| Impeccable detector | HOLD | Detector executado uma vez; não conseguiu resolver o stylesheet Django e informou regras de cor incompletas |
| Impeccable audit | HOLD | Comando instalado respondeu `Unknown command: "audit"` |
| Impeccable polish | HOLD | Comando instalado respondeu `Unknown command: "polish"` |
| Atualização do Impeccable | HOLD | A ferramenta informou v4.8 disponível; atualização não foi executada neste recorte |

## Arquivos alterados

- `financeiro/models.py`: snapshots mensais, saldos por conta e reaberturas auditadas.
- `financeiro/migrations/0005_fechamentomensal_fechamentomensalconta_and_more.py`: schema e unicidades.
- `financeiro/services.py`: prévia histórica, assinatura, confirmação, concorrência e reabertura.
- `financeiro/views.py`: prévia, confirmação e reabertura gerencial.
- `financeiro/urls.py`: rotas `fechamento_mensal` e `fechamento_reabrir`.
- `financeiro/templates/financeiro/fechamento_form.html`: interface do demonstrativo e histórico.
- `financeiro/templates/financeiro/painel.html`: atalho para fechamento mensal.
- `financeiro/tests/test_fechamento_mensal.py`: regressão, aceite, concorrência, idempotência e permissões.
- `docs/superpowers/plans/2026-11-06-financeiro-fechamento-mensal-prd.md`: checkbox da Etapa 4 atualizado com a evidência.

## Estado final

Candidato funcional congelado para auditoria independente na branch `codex/financeiro`, sem promoção. A Etapa 5 de QA/auditoria ampla, a integração na `main`, publicação e implantação permanecem fora desta entrega.
