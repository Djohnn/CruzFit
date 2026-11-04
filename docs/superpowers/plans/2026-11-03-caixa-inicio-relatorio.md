# Caixa — relatório de início

Escopo autorizado: somente Caixa, em worktree exclusivo, com 11 etapas (0–10) e suporte a duas ou três formas de pagamento na mesma venda. Consumidor final, sem cadastro de cliente.

## Referência

- Repositório original: `C:\desenvolvimento\Contruct\Project_construct`.
- Worktree de execução: `C:\desenvolvimento\Contruct\.worktrees\caixa`.
- Branch: `codex/caixa`.
- HEAD inicial: `b37c37b887ec399a2c590e8ff532f10070298954`.
- Graphify: check-update e consulta do fluxo registrar_venda/baixa executados na referência original antes das alterações.
- Python: ambiente existente do repositório original; código e testes executados com diretório de trabalho no novo worktree.

## Entrega inicial

- PRD atualizado para 11 etapas com pagamento dividido, comprovante por parcela, restituição por forma original e apuração por forma no fechamento.
- App `caixa` instalado, modelo `SessaoCaixa` e migração `0001_initial` criados.
- Serviço `abrir_sessao` valida gerente ativo/superusuário, fundo monetário, motivo e chave obrigatórios.
- Banco restringe a uma sessão aberta e impede fundo negativo. Responsável usa PROTECT.
- Abertura guarda autor, data, motivo, chave e assinatura. Replay normalizado retorna a mesma sessão, inclusive após fechamento; conteúdo divergente é recusado.
- Dez cenários Given/When/Then exercitam o contrato com dados sintéticos.

Arquivos principais: `caixa/apps.py`, `caixa/models.py`, `caixa/services.py`, `caixa/migrations/0001_initial.py`, `caixa/tests/test_sessoes.py`, `construct/settings.py` e PRD.

## Evidências brutas

Referência anterior à implementação:

```text
Found 262 test(s).
Ran 262 tests in 79.013s
OK
exit_code=0
```

Primeiro ciclo vermelho (antes de criar o modelo/serviço):

```text
ModuleNotFoundError: No module named 'caixa.models'
Ran 1 test in 0.000s
FAILED (errors=1)
exit_code=1
```

Ciclo verde focado, com migração aplicada somente no banco temporário do runner:

```text
Applying caixa.0001_initial... OK
Ran 10 tests in 0.079s
OK
exit_code=0
```

Checks:

```text
All checks passed!
Success: no issues found in 56 source files
System check identified no issues (0 silenced).
No changes detected
git diff --check: exit_code=0
```

Suíte completa após a implementação:

```text
Found 272 test(s).
Ran 272 tests in 79.486s
OK
Destroying test database for alias 'default'...
exit_code=0
```

Etapas 0 e 1: PASS técnico. Etapas 2–10: pendentes. Logs completos locais em `qa-browser-caixa/baseline.txt` e `qa-browser-caixa/sessoes-suite.txt` (artefatos ignorados pelo Git).

## Limites e sequência

Este é o início da implementação, sem aceite independente de auditor. Pagamentos divididos, venda integrada, interface, movimentos, restituições, fechamento e ativação continuam pendentes conforme o PRD. Concorrência com conexões SQLite separadas ainda será exercitada; este lote valida a exclusividade pela constraint do banco.

Não houve commit, push, cópia de dados reais para o worktree, migração no banco real ou acionamento do launcher de produção. A configuração de backup real não foi copiada. O arquivo SQLite vazio que o comando de migrações cria no worktree não contém dados de produção.

Próxima etapa: Etapa 2, validação e persistência dos pagamentos divididos, seguida da integração atômica com venda e estoque na Etapa 3.
