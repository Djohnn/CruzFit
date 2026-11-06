# PRD — Gestão de variações — item 1

**Objetivo:** permitir que gerente/superusuário encontre uma ficha pelo Estoque e converta produto simples preservando o próprio `Produto` como primeira variação.

**Escopo deste candidato:** somente o item 1. A confirmação pós-salvamento, tabela de variações recentes, links individuais de etiqueta, alterações de EAN na grade/etiqueta e capturas do item 2 foram separados. A geração automática de EAN e a impressão da base continuam fora deste recorte.

## Critérios

- [x] Na lista de Estoque, exibir **Gerenciar variações** para produto já vinculado e **Transformar em produto com variações** para produto simples, apenas para gerente/superusuário.
- [x] Exigir confirmação prévia, pedir somente cor/tamanho ausentes e criar uma ficha própria.
- [x] Vincular o `Produto` original como primeira variação, preservando ID, slug, EAN, saldo, preços, fotos, status e histórico.
- [x] Validar no servidor gerente/superusuário, motivo obrigatório, auditoria, idempotência, conflito de chave e rollback transacional.
- [x] Rejeitar nova conversão do produto já vinculado; manter a inclusão de segunda variação pelo fluxo existente.
- [x] Confirmar que EAN automático e impressão existentes na base continuam sem alterações do item 2.

## Verificação e entrega

- [x] Usar banco e mídia descartáveis.
- [x] Rodar testes focados e registrar a nova contagem.
- [x] Rodar suíte serial, Django check, verificação de migrações, Ruff, mypy e `git diff --check`.
- [x] Registrar SHA de base, SHA final, diff contra a base, arquivos separados e veredito PASS/FAIL/HOLD.

Publicação, merge na `main` e atualização do cliente ficam fora desta tarefa.
