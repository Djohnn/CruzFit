# Relatório — limitação de tentativas de login

Data: 2026-11-11
Worktree: `C:\desenvolvimento\Contruct\.worktrees\seguranca-20261110`
Branch: `codex/seguranca`
Base: `e97c63de9b7f0be31091aa93bc7056f962d12258`
Commit: nenhum; sem push ou promoção.

## Entrega

- O login local e o `/admin/login/` usam buckets persistidos por identidade e endereço de origem. As chaves são HMACs com a chave da instalação; identificadores e IPs em claro não são gravados.
- O limite é de cinco falhas por identidade/origem em uma janela de dez minutos. A quinta falha inicia bloqueio de dez minutos. O sucesso apaga o bucket.
- A janela anterior ao bloqueio também expira após dez minutos. Um bloqueio expirado permite nova tentativa e reinicia a contagem, mesmo que uma gravação concorrente tenha atualizado `updated_at` recentemente.
- Identificadores de nome e username que apontam à mesma conta usam a mesma chave. Contas inexistentes e nomes ambíguos recebem a mensagem genérica de falha.
- A origem usa somente `REMOTE_ADDR`; `X-Forwarded-For` é ignorado. O bucket inclui identidade e origem para que uma falha não bloqueie a loja inteira.
- O formulário de autenticação do Admin usa o mesmo serviço e preserva a validação de usuário ativo/staff do Django. A autenticação programática continua com o backend original.
- Buckets expirados são removidos a cada falha gravada; `updated_at` tem índice para a poda. Uma instalação sem novas falhas mantém linhas antigas até a próxima falha, quando são removidas.
- Contadores são incrementados atomicamente. Em SQLite, repetem-se apenas transações que falham com causa `SQLITE_BUSY`/`SQLITE_LOCKED`; erros homônimos de outros bancos são propagados.

## Arquivos deste escopo

- `usuarios/admin.py`
- `usuarios/models.py`
- `usuarios/services.py`
- `usuarios/views.py`
- `usuarios/tests.py`
- `usuarios/migrations/0004_loginattempt.py`
- `usuarios/migrations/0005_loginattempt_updated_at_index.py`

O relatório é este arquivo. Alterações paralelas visíveis em `cruzfit/`, `estoque/`, `requirements.txt` e `pyproject.toml` pertencem aos demais escopos da sprint.

## Verificação

RED inicial antes do modelo: `ImportError: cannot import name 'LoginAttempt' from 'usuarios.models'`. Após implementar, os testes focados passaram em banco SQLite file-backed temporário, sem acessar `db.sqlite3` real.

Saída final do runner (Django 5.2.19, Python 3.14):

```text
Found 18 test(s).
Applying usuarios.0004_loginattempt... OK
Applying usuarios.0005_loginattempt_updated_at_index... OK
test_admin_login_uses_same_limit_for_staff_without_bypassing_csrf ... ok
test_alternate_identifiers_share_one_account_bucket ... ok
test_ambiguous_name_uses_generic_login_error ... ok
test_csrf_rejection_does_not_change_admin_login_attempt_state ... ok
test_csrf_rejection_does_not_change_login_attempt_state ... ok
test_failures_below_limit_expire_after_the_configured_window ... ok
test_inactive_user_does_not_authenticate ... ok
test_lockout_expires_at_the_configured_instant ... ok
test_lockout_state_survives_new_client_and_database_reload ... ok
test_non_sqlite_lock_lookalike_is_rethrown_without_retry ... ok
test_stale_identifier_buckets_are_deleted_during_failure_cleanup ... ok
test_success_clears_prior_failures_for_any_supported_identifier ... ok
test_unknown_user_gets_same_message_and_separate_bucket ... ok
test_user_and_origin_are_isolated_and_forwarded_header_is_ignored ... ok
test_wrong_attempt_after_lock_expiry_starts_a_fresh_window ... ok
test_concurrent_failures_create_one_bucket_and_reach_lockout ... ok
test_concurrent_failures_on_existing_bucket_are_not_lost ... ok
Ran 18 tests in 38.745s
OK
Destroying test database for alias 'default'... OK
System check identified no issues (0 silenced).
```

A suíte inclui login correto/erro, usuário inativo e inexistente, mensagem ambígua genérica, aliases Unicode, CSRF nas telas local e admin, bloqueio persistente, expiração determinística (inclusive após corrida), isolamento por IP/usuário, limpeza de buckets antigos e incrementos concorrentes em buckets novos/existentes.

Gates separados:

```text
manage.py check: System check identified no issues (0 silenced).
manage.py makemigrations --check --dry-run: No changes detected.
ruff check usuarios --no-cache: All checks passed!
mypy usuarios --cache-dir <temporário>: Success: no issues found in 14 source files.
```

O mypy sem `--cache-dir` tentou escrever `.mypy_cache` dentro da worktree somente-leitura e falhou no cache; a execução redirecionada a diretório temporário passou.

## Limites e decisão

- Como a instalação local pode ver todos os operadores no mesmo endereço, atingir cinco falhas para uma conta nesse endereço bloqueia aquela conta por dez minutos. Outros usuários continuam podendo entrar. Para endereços iguais, a chave do username/nome fica compartilhada entre identificadores alternativos.
- O IP encaminhado por proxy não é considerado. Uma futura configuração explícita de proxy confiável exigiria regra separada.
- A limpeza de linhas ociosas acontece no próximo POST de falha, não por tarefa agendada.
- Sem testes de carga global ou auditoria externa nesta tarefa; os testes concorrentes exercitam chamadas simultâneas ao serviço SQLite file-backed.
