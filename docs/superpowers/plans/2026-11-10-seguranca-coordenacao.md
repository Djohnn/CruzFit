# Cruz Fit — correções de segurança

Candidato na branch codex/seguranca, base e97c63de9b7f0be31091aa93bc7056f962d12258.
Worktree C:/desenvolvimento/Contruct/.worktrees/seguranca-20261110.
Execução retomada em 11/11/2026. Alterações ainda sem commit.

## Escopo implementado

- [x] Categoria inválida recebe HTTP 400 e orientação controlada. Categorias válidas e busca por nome preservadas.
- [x] DEBUG desativado por padrão. CRUZFIT_DEVELOPMENT=1 habilita desenvolvimento explicitamente.
- [x] Chave aleatória individual persistida fora do repositório. DJANGO_SECRET_KEY explícita tem precedência.
- [x] Publicação atômica da chave com renomeação exclusiva no Windows e link no POSIX. Inicializações concorrentes conservam a mesma chave.
- [x] Login local e Admin limitados por identidade e origem. Cinco falhas iniciam bloqueio de dez minutos. Expiração e sucesso permitem recuperação.
- [x] Identificadores alternativos da mesma conta compartilham o contador, incluindo nomes Unicode. A mensagem genérica preserva o contrato do identificador de login.
- [x] Django atualizado para 5.2.19, validado em Python 3.14.0 e 3.12.14.
- [x] Dois ciclos reais do supervisor em instalação descartável, atualização de banco legado, backup verificado e restauração.
- [x] Suíte final congelada passou 524/524 em 470.080s, código de saída 0.
- [ ] Auditoria independente do pacote final.

## Evidências e regressões

A consulta inválida foi reproduzida antes da correção com dois testes e múltiplos subcenários, totalizando três falhas e três erros. Após corrigir, dois testes passaram. O recorte de consulta/relatórios passou 11/11.

Os nove testes combinados de configuração/chave/categoria passaram em Python 3.12. A criação da chave nesse runtime inicialmente falhava em os.link no Windows. O ajuste para renomeação exclusiva foi validado por teste e por inicialização HTTP real.

O módulo usuarios.tests passou 18/18 em 38.745s com SQLite em arquivo. Inclui concorrência por conexões independentes, expiração com timestamp concorrente, aliases Unicode, Admin, CSRF, sucesso e isolamento. O teste legado que exige a palavra identificador na mensagem também passou isoladamente após o ajuste da mensagem.

Uma suíte provisória encontrou erro de lock no banco em memória e incompatibilidade da mensagem. A rodada seguinte executou 523 testes em 431.608s com uma falha de mensagem. Esses resultados motivaram a rodada final com código congelado e SQLite em arquivo. A rodada definitiva passou 524/524 em 470.080s, OK, FINAL_SUITE_EXIT=0. A evidência é cruzfit-seguranca-suite-congelada.txt na pasta externa de evidências. O roteiro é cruzfit-seguranca-final-runner.py, que cria settings, SQLite, mídia e temporários descartáveis e remove o ambiente ao terminar.

## Ensaio operacional

Python 3.12.14 e Django 5.2.19 executaram duas inicializações e duas paradas oficiais em uma cópia descartável. Login, CSS e mídia retornaram HTTP 200. A chave persistiu entre ciclos e as portas foram liberadas após as paradas.

O banco inicial estava em usuarios.0003 com uma conta sintética legada. As migrações 0004_loginattempt e 0005_loginattempt_updated_at_index foram aplicadas. Três ZIPs foram validados. A restauração comprovou PRAGMA integrity_check=ok, preservação da conta, presença das duas migrações, mídia original e mesma chave.

A evidência está em C:/Users/johnn/.codex/visualizations/2026/11/02/01a19da3-3220-7363-8a3f-7445c620acc2/cruzfit-seguranca-supervisor-final.txt. O roteiro reproduzível é cruzfit-seguranca-supervisor-qa.py na mesma pasta. O destino de backup desse ensaio era temporário, fora da instalação descartável.

## Verificações finais

Ruff passou. Mypy passou em 79 arquivos. Django check não encontrou problemas. makemigrations --check --dry-run não encontrou alterações. Depois da suíte, foram removidas exclusivamente linhas vazias finais em usuarios/services.py, usuarios/tests.py e usuarios/views.py. O diff check final passou. Ruff e mypy foram repetidos após a limpeza e passaram. Os arquivos finais são identificados no manifesto SHA-256 ao lado deste relatório.

## Decisões e limites

O usuário autorizou GPT-6 Luna para substituir o modelo GPT 5.6 Luna indisponível. Configuração, dependências e login foram divididos entre três Lunas. O coordenador corrigiu a consulta de categoria e a criação atômica da chave no Windows. As revisões cruzadas dos fontes não identificaram bloqueio concreto. O grafo foi consultado anteriormente pelos responsáveis. Duas novas consultas Graphify ficaram sem resultado. O coordenador identificou as linhas de comando e encerrou somente os quatro processos dessas consultas ao finalizar a revisão.

O limite de tentativas é por conta e origem. Não há contador global por endereço para tentativas distribuídas entre identificadores diferentes. A origem é REMOTE_ADDR e ignora cabeçalhos encaminhados sem contrato de proxy confiável.

Na primeira atualização, a troca da chave pública antiga encerra sessões existentes e exige nova revisão de vendas pendentes. O segredo privado pertence ao perfil Windows e ao caminho da instalação. Atualizações no mesmo perfil e caminho mantêm a chave. A política de recuperação está em docs/security/configuracao-local-chave.md. O backup operacional não inclui esse segredo.

As rotas de arquivos locais mantêm o uso em localhost com DEBUG=False. O ensaio comprova HTTP e ciclo operacional. Uma nova inspeção visual completa da interface não foi executada nesta retomada.

O relatório anterior não confirmou open redirect ou XSS nos cenários examinados. Esta entrega cobre os quatro achados descritos no briefing. A auditoria independente e a decisão de publicação permanecem pendentes.

## Estado final

PASS técnico para os quatro achados e validação integrada. Candidato congelado com alterações sem commit. A auditoria independente do pacote permanece pendente.

Django e migrações foram conferidos por manage.py check e manage.py makemigrations --check --dry-run. A suíte executou manage.py test --noinput --parallel 1 --verbosity 1 em Python 3.14 com Django 5.2.19 e banco SQLite em arquivo.

Fontes oficiais consultadas pela Luna de dependências em 10/11/2026 — https://www.djangoproject.com/download/, https://docs.djangoproject.com/en/5.2/releases/5.2/ e https://www.djangoproject.com/weblog/2026/nov/03/security-releases/.
