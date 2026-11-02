# Execução local do Cruz Fit

O Cruz Fit foi configurado para uso local por um gerente em um único computador.
Não exponha o servidor diretamente à internet e não trate esta configuração como
uma configuração de produção.

Antes da primeira execução e depois de qualquer alteração de modelo, faça um
backup do banco local e aplique as migrações no banco de desenvolvimento:

```text
Copy-Item -LiteralPath db.sqlite3 -Destination db.sqlite3.backup -Force
venv\Scripts\python.exe manage.py migrate
```

Só depois inicie o servidor local:

```text
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Os testes usam um banco temporário do Django e não substituem o backup do
`db.sqlite3` real. Nunca execute `migrate` no banco real sem confirmar que o
backup foi criado e pode ser restaurado.

Os hosts locais permitidos são `localhost`, `127.0.0.1` e `[::1]`. HTTPS, HSTS,
filas, infraestrutura distribuída e banco PostgreSQL não fazem parte deste
escopo.
