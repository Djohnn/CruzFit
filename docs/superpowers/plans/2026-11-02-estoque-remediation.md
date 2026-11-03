# Correção dos defeitos QA do estoque — Plano de implementação

> **Para agentes:** executar os passos em ordem, mantendo cada correção mínima e verificável.

**Objetivo:** corrigir os oito defeitos reproduzidos na QA sem introduzir novas telas ou fluxos de negócio.

**Arquitetura:** preservar as views e modelos atuais; validar entradas no limite das views, corrigir o campo incorreto do modelo, apontar a raiz para o login existente e trocar a exclusão mutável por GET para POST com CSRF. Nenhuma refatoração ampla será feita.

**Stack:** Django 5.0.6, SQLite local, Django TestCase/Client.

### Tarefas

- [x] Adicionar testes de regressão para login autenticado, lucro, filtros inválidos, cadastro inválido/duplicado, cadastro de vendedor incompleto, rota raiz e exclusão somente via POST.
- [x] Corrigir a view de login para não referenciar a URL inexistente `plataforma`.
- [x] Corrigir `Produto.lucro()` para usar `preco_compra`.
- [x] Validar limites numéricos do filtro antes da consulta.
- [x] Validar dados do produto antes de salvar e retornar erro HTTP controlado para campos ausentes, duplicados ou inválidos.
- [x] Validar campos obrigatórios do cadastro de vendedor.
- [x] Redirecionar a raiz para a rota de login existente.
- [x] Exigir POST na exclusão e preservar o estilo existente do botão com formulário CSRF.
- [x] Rodar testes focados, suíte Django, `check`, migrações e smoke HTTP na porta 15001.
