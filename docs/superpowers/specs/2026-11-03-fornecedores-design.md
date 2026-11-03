# Fornecedores — escopo confirmado

O usuário aprovou cadastro integrado às entradas, com fornecedor obrigatório nas novas compras, histórico preservado e inativação em vez de exclusão.

Manter Django, SQLite e templates do Cruz Fit. Criar `Fornecedor` no app estoque, regras em `estoque/fornecedores.py`, formulários/views/templates próprios e navegação consistente com a identidade existente. Nome obrigatório; fantasia, documento, contato, telefone, e-mail, endereço e observações opcionais. Documento informado é normalizado, validado e único, incluindo CNPJ alfanumérico.

Criação registra responsável, data e chave idempotente. Alteração e inativação têm registros próprios de auditoria com motivo, responsável, data e chave. A mesma chave com conteúdo diferente é rejeitada. Apenas gerente ou superusuário administra fornecedores; usuários autenticados podem selecionar fornecedores ativos nas entradas e consultar histórico.

Adicionar fornecedor nullable com PROTECT a EntradaEstoque e snapshots de nome/documento. Migração mantém entradas antigas sem fornecedor. Compras novas exigem fornecedor ativo; ajuste/devolução continuam aceitando ausência. Retentativa idêntica de entrada concluída deve funcionar mesmo após inativação do fornecedor; mudança de conteúdo com a mesma chave é rejeitada. Serviços fazem toda escrita dentro de transação.

Telas: lista pesquisável/paginada com filtro de situação, cadastro/edição agrupados, detalhe com contato e histórico paginado de entradas/produtos, confirmação de inativação com motivo. Formulários com CSRF, erros junto aos campos e preservação dos valores. Entrada inclui fornecedor na revisão e no histórico. Sem nova API ou dependência DRF, pois esta aplicação usa views Django.

Testes usam exclusivamente banco temporário e dados sintéticos. Conferir fluxo autenticado, permissões, validação/duplicidade, idempotência, auditoria, fornecedor obrigatório/inativo, snapshots e preservação de legado. Não migrar o banco real durante implementação. Verificar interface desktop/mobile, Ruff, mypy e suíte completa. Preservar alterações de backup existentes.
