# Relatório de auditoria — Financeiro Etapa 2

Data: 2026-11-07
Branch: `codex/financeiro`
Worktree: `C:\desenvolvimento\Contruct\.worktrees\financeiro`
Base confirmada: `517b4e9b3526a87558a428d734e1b1bf973a5792`
Commit de implementação: `0ae700d` (`feat: implementa recebimentos financeiros`)
Integração, push e implantação: não executados.

## Etapa 0 e orientação

- A worktree iniciou limpa no SHA de base informado e permaneceu isolada da `main` e das demais worktrees.
- O Graphify foi consultado antes das alterações sobre Caixa, Vendas, pagamentos, estornos, Financeiro, permissões, auditoria e idempotência. O grafo usado foi o snapshot descartável em `C:\Users\johnn\.codex\visualizations\2026\11\06\01a1b22c-4afc-7f23-98e1-e390cdb31c9c\financeiro-graph\graphify-out\graph.json`. A consulta confirmou `caixa.services.registrar_recebimento`, `vendas.services.registrar_venda`, `vendas.services.cancelar_venda`, `vendas.services.devolver_venda` e o caminho de estorno financeiro existente.
- Banco, mídia e logs de QA foram mantidos no diretório descartável `C:\Users\johnn\.codex\visualizations\2026\11\06\01a1b22c-4afc-7f23-98e1-e390cdb31c9c\financeiro-test`.
- A data de corte é inclusiva. O saldo inicial representa a abertura da data e não gera receita; movimentos anteriores são rejeitados antes da escrita. O teste usa `CUTOVER = 2026-11-06`: `2026-11-05` é rejeitado e `2026-11-06` é aceito. A data real de ativação continua para a implantação.

## Entrega

- `RecebimentoPixFinanceiro` liga o crédito único ao `PagamentoVenda` original.
- `RecebivelCartao`, `DepositoCartao` e `LiquidacaoCartao` registram aprovação, lote, conta, data, bruto, taxas, líquido e referência da operadora. A liquidação grava uma entrada líquida única e cada parcela fica vinculada ao depósito.
- `EstornoRecebimentoCartao` preserva o evento do Caixa: antes da liquidação cancela o a receber; depois cria débito vinculado ao pagamento de restituição, sem apagar histórico.
- Caixa cria o a receber automaticamente para pagamentos de cartão, mantém dinheiro em espécie no próprio Caixa e aciona o Financeiro nos eventos existentes de reversão/restituição.
- Escritas manuais usam serviço transacional, `Decimal`, gerente/superusuário no servidor, auditoria e idempotência. Reenvios equivalentes retornam o registro original; chave com assinatura diferente conflita. As tentativas adicionais tratam somente contenção SQLite e reiniciam a transação inteira.
- Interface: `/financeiro/recebimentos/pix/` e `/financeiro/recebimentos/cartao/`, links no painel, histórico/saldo/receita operacional por conta e `403` para vendedor.

## Evidência de testes

### Regressão RED

Antes dos modelos da Etapa 2, o teste novo foi executado primeiro:

```text
Found 1 test(s).
test_services ... ERROR
ImportError: cannot import name 'DepositoCartao' from 'financeiro.models'
Ran 1 test in 0.000s
FAILED (errors=1)
```

### Focados

Comando final:

```text
$env:PYTHONPATH='C:\Users\johnn\.codex\visualizations\2026\11\06\01a1b22c-4afc-7f23-98e1-e390cdb31c9c\financeiro-test'
venv\Scripts\python.exe manage.py test financeiro.tests --settings=validation_financeiro_serial_excluding_settings --verbosity 1 --noinput
```

Resultado: `Ran 27 tests ... OK` — 23 serviços, incluindo a concorrência de cartão, e 4 testes de interface.

Casos comprovados: Pix de R$ 50 uma única vez; conflito de chave; cartão de R$ 100 aprovado em novembro e liquidado em `2026-12-01` com taxa de R$ 3; saldo e receita de dezembro em R$ 97; novembro em R$ 0; pagamento misto sem retirar dinheiro do Caixa; lote com rollback após a segunda parcela; cancelamento antes da liquidação; estorno depois da liquidação; véspera/dia do corte; vendedor bloqueado.

O teste concorrente usa duas conexões SQLite independentes e chaves diferentes para a mesma origem: uma chamada cria, a outra recebe “já foi liquidada”; há um depósito, uma liquidação, um lançamento e saldo de R$ 100.

### Recorte serial comparável

Foi executado em banco separado, com `--parallel 1`, excluindo somente os dois módulos legados de concorrência que travam o SQLite (`caixa.tests.test_concorrencia` e `compras.tests.test_concorrencia`). O comando usou os módulos reais `caixa.tests.*`, `compras.tests.*`, `estoque.tests`, `financeiro.tests` e `usuarios.tests`; o comando anterior que citava `vendas.tests` foi corrigido porque esse módulo não existe.

Resultado final pós-ajuste: `Ran 151 tests in 100.263s — OK`.

### Checks

- `manage.py check`: PASS — nenhum problema.
- `manage.py makemigrations --check --dry-run`: PASS — `No changes detected`.
- `ruff check --no-cache` nos arquivos alterados: PASS.
- `mypy --cache-dir <temporário> caixa financeiro`: PASS — `Success: no issues found in 16 source files`.
- `git diff --check`: PASS; apenas avisos de normalização LF/CRLF do Git.
- Migração em banco descartável: PASS — `financeiro.0002... OK`.

### Suíte ampla

`manage.py test --parallel 1` descobriu 454 testes no banco descartável. A execução ficou HOLD: 13 erros já concentrados nos módulos de imagens/registro de estoque e regressões de startup, e depois parou no módulo legado `caixa.tests.test_concorrencia` sem produzir resumo final. Nenhum erro do recorte Financeiro apareceu; os módulos financeiros passaram no recorte serial comparável. As falhas amplas anteriores permanecem registradas e não foram ampliadas nesta etapa.

## Interface e Impeccable

- QA visual desktop em navegador local: PASS no painel, formulário de confirmação Pix e formulário de liquidação de cartão; árvore de acessibilidade e capturas confirmaram os campos, links e ações.
- QA visual mobile: HOLD. Havia navegador, mas a superfície disponível não expôs controle de viewport/emulação móvel nesta sessão; não foi declarado PASS por inspeção desktop redimensionada.
- `impeccable context`: executado; informou ausência de `PRODUCT.md` e detector manual.
- `impeccable detect --json ...`: executado; retornou `[]`, com aviso de que não conseguiu resolver o stylesheet referenciado por template Django.
- `impeccable audit`: HOLD/indisponível — `Unknown command: "audit"`.
- `impeccable polish`: HOLD/indisponível — `Unknown command: "polish"`.
- Atualização da ferramenta: fora do recorte; não executada. A instalação informou que há uma versão mais nova disponível.

## Veredito por critério

| Critério | Veredito | Evidência |
|---|---|---|
| Pix confirmado credita uma vez e protege pagamento de origem | PASS | 27/27; replay, conflito e saldo R$ 50 |
| Cartão fica a receber e liquida bruto/taxa/líquido por parcela | PASS | 27/27; R$ 100 − R$ 3 = R$ 97 em dezembro |
| Pagamento misto preserva dinheiro no Caixa | PASS | teste integrado no recorte financeiro |
| Estorno antes/depois da liquidação | PASS | cancelamento do recebível e débito vinculado, histórico preservado |
| Corte inclusivo e saldo inicial fora da receita | PASS | véspera rejeitada, dia aceito; janela mensal R$ 0/R$ 97 |
| Idempotência, assinatura e proteção concorrente | PASS | duas conexões SQLite; uma gravação, uma rejeição de origem |
| Lote atômico com rollback | PASS | falha simulada na segunda parcela deixa zero depósito/lançamento |
| Permissões de escrita no servidor | PASS | gerente opera; vendedor recebe `403` nos novos POSTs |
| Auditoria e migração | PASS | eventos em serviço; migração aplicada e sem drift |
| Interface desktop | PASS | navegador local, painel e dois formulários |
| Interface mobile visual | HOLD | viewport mobile indisponível |
| Recorte serial comparável | PASS | 151/151 |
| Suíte ampla | HOLD | 454 descobertos; 13 erros fora do Financeiro e bloqueio legado |
| Impeccable audit/polish | HOLD | comandos indisponíveis na instalação |

Estado final: candidato congelado para auditoria independente, sem merge, push, alteração na `main` ou implantação no cliente.
