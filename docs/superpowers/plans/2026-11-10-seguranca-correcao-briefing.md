# Cruz Fit — briefing de correção de segurança

Responsável pela implementação: agente implementador. Responsável pelo aceite: auditor independente.
Status inicial: tarefas pendentes. Correções autorizadas, publicação e atualização do cliente aguardam autorização específica.

## Preparação

- [ ] Partir da main na revisão e97c63de9b7f0be31091aa93bc7056f962d12258, conferindo eventual avanço remoto.
- [ ] Trabalhar em uma worktree dedicada, branch codex/seguranca, preservando o checkout principal e a pasta local tools/security.
- [ ] Consultar Graphify antes das alterações. O grafo atual ainda aponta alguns arquivos para construct; validar os caminhos reais em cruzfit e atualizar o grafo escopado quando necessário.
- [ ] Ler o relatório externo de evidências em C:/Users/johnn/.codex/visualizations/2026/11/02/01a19da3-3220-7363-8a3f-7445c620acc2/cruzfit-revisao-redirect-xss.md.
- [ ] Reproduzir os achados em banco e diretórios descartáveis. Registrar os testes que falham antes da correção.

## Etapa 1 — erros e configuração de execução

- [ ] Validar o parâmetro categoria na consulta de produtos. Valores não numéricos, inexistentes ou excessivos precisam gerar resposta controlada.
- [ ] Usar depuração desativada na execução distribuída e opção explícita para desenvolvimento.
- [ ] Garantir que a configuração segura mantém login, imagens, CSS, JavaScript, impressão, readiness HTTP e os launchers funcionando. Validar o fornecimento dos arquivos estáticos com DEBUG=False.
- [ ] Comprovar que o erro antes reproduzido em /estoque/consulta/?categoria=qa-invalid não apresenta traceback ou configuração.

Aceite PASS exige resposta controlada e funcionamento do fluxo oficial. FAIL se detalhes internos continuarem expostos ou se os recursos visuais deixarem de carregar.

## Etapa 2 — chave individual por instalação

- [ ] Retirar o segredo fixo como alternativa na distribuição e gerar uma chave aleatória própria por instalação.
- [ ] Persistir a chave em local privado, fora do Git. A escrita inicial deve ser atômica e segura sob duas inicializações concorrentes.
- [ ] Preservar a chave existente entre reinícios e atualizações. Respeitar a variável DJANGO_SECRET_KEY fornecida explicitamente.
- [ ] Definir o procedimento de atualização, reinstalação e recuperação. Considerar assinaturas de revisão de vendas, sessões e demais usos de assinatura antes de decidir sobre a substituição do segredo anterior.
- [ ] Não imprimir segredo em logs, relatórios, comandos ou evidências.
- [ ] Testar duas instalações com chaves diferentes, persistência após reinício, falha na gravação e concorrência.

Aceite PASS exige segredo individual estável e recuperação documentada. FAIL se houver retorno silencioso a uma chave pública fixa ou mudança do segredo a cada abertura.

## Etapa 3 — limitação de tentativas de login

- [ ] Implementar limitação persistente com expiração, adequada à instalação local, contemplando identificador e origem.
- [ ] Tratar identificadores alternativos e contas inexistentes com mensagens que evitem expor a existência de usuários.
- [ ] Evitar bloqueio global que impeça todos os funcionários de trabalhar. Definir e documentar limites, duração e recuperação.
- [ ] Validar contagem sob concorrência, comportamento após reinício, expiração, sucesso de autenticação e isolamento entre usuários.
- [ ] Preservar a proteção CSRF e os contratos de permissão existentes.

Aceite PASS exige bloqueio observável após o limite definido e desbloqueio comprovado após a expiração. Não aceitar apenas espera artificial ou contador em memória sem justificar a persistência.

## Etapa 4 — dependências e validação integrada

- [ ] Migrar Django 5.0.6 para uma versão com suporte de segurança vigente. Consultar fontes oficiais na execução e fixar uma versão corrigida.
- [ ] Verificar compatibilidade com Python do cliente, Python de desenvolvimento, django-role-permissions e dependências de qualidade.
- [ ] Executar testes de migração e preservação de dados legados, autenticação, permissões, vendas divididas, crédito/débito, fechamento, estoque, Compras e Financeiro.
- [ ] Comprovar dois ciclos do supervisor em instalação descartável, com servidor HTTP real, backup validado, migração, parada e liberação da porta. Incluir o caso de filho do lançador do Python no Windows.
- [ ] Validar restauração de banco e mídia, com os efeitos sobre a configuração privada de instalação documentados.
- [ ] Executar suíte completa serial, Django check, migration drift, Ruff, mypy e git diff --check no ambiente compatível documentado.
- [ ] Revisar os caminhos de redirecionamento, escape de HTML e assinatura afetados. O relatório anterior não confirmou open redirect ou XSS nos cenários testados.

## Entrega e revisão

- [ ] Entregar relatório com base e SHA final, arquivos alterados, decisões, comandos e saídas dos testes, cenários RED/GREEN e matriz PASS/FAIL/HOLD.
- [ ] Explicar efeitos visíveis da atualização, incluindo eventual necessidade de novo login, configuração de chave e recuperação.
- [ ] Congelar o candidato para auditoria independente.

HOLD deve identificar a limitação concreta e os critérios sem evidência. Nenhuma ferramenta de auditoria bloqueada pode ser apresentada como execução aprovada. Usar exclusivamente banco, usuários, mídia e backups de teste durante a implementação.
