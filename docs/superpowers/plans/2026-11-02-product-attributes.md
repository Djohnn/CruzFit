# Atributos de produto — Plano de implementação

**Objetivo:** adicionar tamanho, modelo, cor, tecido e gênero ao cadastro de produto.

**Arquitetura:** ampliar o modelo `Produto` com campos opcionais para preservar registros existentes; usar choices controlados para tamanho e gênero; manter a gravação no fluxo atual de `add_produto`.

**Tech Stack:** Django 5, SQLite local, templates Django, CSS Cruz Fit e `manage.py test`.

## Tarefas

- [x] Criar testes RED para presença dos campos, choices e persistência no POST.
- [x] Adicionar campos no modelo e gerar a migração `estoque`.
- [x] Passar os choices e dados do formulário para o template.
- [x] Validar e persistir os novos dados no POST existente.
- [x] Executar testes focados, suíte completa e `manage.py check`.
- [x] Validar cadastro no navegador sem criar dados permanentes de QA.
