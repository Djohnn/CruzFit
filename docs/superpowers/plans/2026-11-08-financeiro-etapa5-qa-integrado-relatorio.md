# Etapa 5 — QA integrado e auditoria final do Financeiro

Data: 2026-11-08

Branch: `codex/financeiro`

Worktree auditada: `C:\desenvolvimento\Contruct\.worktrees\financeiro`

Commit efetivamente testado: `1bef5cb63dee9445da6a443bac9c6cc05bbbbd72` (`docs: corrige contagem dos testes`)

Escopo autorizado: QA integrado e correção documental. Não houve merge, push, publicação na `main` ou implantação.

## Ambiente

As execuções foram feitas em uma cópia descartável do commit:

`C:\desenvolvimento\Contruct\.qa-financeiro-20261108\source-copy`

Banco, mídia, estáticos e temporários ficaram fora do checkout:

- SQLite: `C:\desenvolvimento\Contruct\.qa-financeiro-20261108\db\qa.sqlite3` e bancos temporários do runner.
- Mídia: `C:\desenvolvimento\Contruct\.qa-financeiro-20261108\media`.
- `TEMP`, `TMP` e `TMPDIR`: `C:\desenvolvimento\Contruct\.qa-financeiro-20261108\temp`/`tmp`, criados antes da configuração.
- Venv canônica: `C:\desenvolvimento\Contruct\Project_construct\venv` (Django 5.0.6).

A migração completa aplicou até `financeiro.0005_fechamentomensal_fechamentomensalconta_and_more`.

## Comandos e resultados

### Fluxos integrados e regressões

```text
python manage.py test financeiro.tests compras.tests caixa.tests.test_fluxo_integrado --verbosity 1 --parallel 1 --noinput
Found 95 test(s).
Ran 95 tests in 148.395s
OK
```

O recorte cobriu contas com saldo inicial, venda mista, Pix, cartão a receber e liquidação líquida posterior, obrigação única após recebimento integral em duas entregas, pagamento/estorno/novo pagamento, replay, rollback, concorrência SQLite, histórico e proteção contra vendedor.

```text
python manage.py test --verbosity 1 --parallel 1 --noinput
Found 487 test(s).
Ran 487 tests in 346.410s
OK
System check identified no issues (0 silenced).
```

Evidência bruta: `C:\desenvolvimento\Contruct\.qa-financeiro-20261108\evidence-focused.txt` e `evidence-serial.txt`.

### Fechamento mensal e caminho corrigido

O plano foi corrigido para usar a classe no módulo correto:

```text
financeiro.tests.test_fechamento_mensal.FechamentoMensalViewTests
```

Execução da referência corrigida:

```text
python manage.py test financeiro.tests.test_fechamento_mensal.FechamentoMensalViewTests --verbosity 1 --noinput
Found 3 test(s).
Ran 3 tests in 2.616s
OK
```

Os `403 Forbidden` observados para POST direto de vendedor são esperados e fazem parte do aceite. A suíte de 95 e a suíte serial de 487 também exercitaram prévia, cobertura, obrigações abertas, confirmação, replay, prévia obsoleta, concorrência, rollback, reabertura, versionamento, preservação histórica e correção retroativa somente após reabertura.

### Checks técnicos

```text
python manage.py check
System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run
No changes detected

ruff check financeiro caixa compras vendas estoque usuarios cruzfit
All checks passed!

mypy financeiro
Success: no issues found in 8 source files

git diff --check
exit 0
```

O `mypy` amplo executado incidentalmente com `C:\ERP\.venv` produziu 590 erros de tipagem legados em 19 arquivos; esse resultado não foi usado como veredito porque a venv não é a do projeto e o check canônico do plano é `mypy financeiro` na venv `Project_construct`. O diagnóstico foi preservado, não ocultado.

## Matriz de parecer

| Critério | Veredito | Evidência |
|---|---|---|
| Fluxo integrado de contas, venda mista, Pix/cartão e datas líquidas | PASS | Recorte integrado 95/95 e testes de serviços de cartão/Pix/venda mista |
| Recebimento em duas entregas e obrigação única | PASS | `financeiro.tests.test_contas_pagar` dentro do recorte; replay não duplica origem |
| Pagar → estornar → pagar novamente, saldos e histórico | PASS | Testes de pagamento, estorno, rollback e histórico; suíte 487/487 |
| Prévia, cobertura, abertas, confirmação e prévia obsoleta | PASS | `test_fechamento_mensal` e `FechamentoMensalViewTests`; suíte serial |
| Reabertura, nova versão e posição histórica imutável | PASS | Testes de reabertura/versionamento e reconstrução por datas efetivas |
| Concorrência SQLite, rollback e POST direto por vendedor | PASS | Testes transacionais/concorrentes; POST de vendedor retorna 403 |
| Suíte completa serial | PASS | 487/487 |
| Django check, migrações, Ruff, mypy Financeiro e diff | PASS | Saídas brutas acima |
| Navegador desktop | HOLD | Controle visual do Chrome foi bloqueado pelo ambiente: URL atual não pôde ser determinada com confiança; nenhuma evidência visual foi inventada |
| Navegador mobile `390×844` | HOLD | Mesmo bloqueio do controle visual; viewport não foi validado |
| Fluxo de empréstimos | HOLD | O fluxo ainda não existe; mantido separado e aceito como HOLD para este QA |
| Impeccable `audit`/`polish` | HOLD | Mantido separado conforme autorização; não é bloqueio dos gates automatizados desta etapa |

## Defeitos encontrados

Nenhum defeito funcional foi reproduzido nos fluxos cobertos. A falha inicial de migração ocorreu somente na primeira cópia criada com `tar`, que corrompeu o nome Unicode da migração `0003_rename_preço_compra_produto_preco_compra`; a cópia foi refeita com preservação de nomes e a migração passou. Não é defeito do commit auditado.

## Estado final

Parecer técnico: **HOLD para integração**, apesar dos gates automatizados PASS, por causa dos HOLDs independentes de navegador desktop/mobile, empréstimos e Impeccable `audit`/`polish`. A integração na `main`, publicação e implantação permanecem decisões separadas.

## Auditoria complementar e aceite do coordenador — 2026-11-08

O parecer acima registra a primeira execução. A auditoria complementar fornecida
pelo auditor, referente ao mesmo SHA `1bef5cb63dee9445da6a443bac9c6cc05bbbbd72`,
superou os HOLDs de navegador desktop e mobile. Foram comprovados venda mista,
liquidação líquida, compra em duas entregas, pagamento/estorno/novo pagamento,
fechamento v1 reaberto e v2 vigente, permissões e ausência de overflow em 390×844.
O auditor repetiu 95 testes integrados e 3 testes da referência documental,
todos aprovados. A suíte 487/487 permanece a execução registrada acima.

Evidências da auditoria complementar estão em
`C:\desenvolvimento\Contruct\.qa-financeiro-etapa5-complementar-20261108`,
incluindo `evidence-desktop-closing-v2.png` e `evidence-mobile-closing-v2.png`.
Este adendo consolida o parecer recebido, sem declarar nova execução de navegador.

O coordenador aceitou os HOLDs de empréstimos e do Impeccable para preparar
a integração isolada. O fluxo de empréstimos continua sem implementação e
sem validação própria. Prontidão para preparar integração isolada — **PASS**.
Publicação e implantação dependem de autorização específica.
