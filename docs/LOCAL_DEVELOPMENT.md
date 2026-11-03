# Execução local do Cruz Fit

O Cruz Fit foi configurado para uso local por um gerente em um único computador.
Não exponha o servidor diretamente à internet e não trate esta configuração como
uma configuração de produção.

Antes da primeira execução e depois de qualquer alteração de modelo, configure
um destino externo e use os launchers do Cruz Fit. Se precisar operar
manualmente, faça backup do banco e das imagens com o servidor parado antes de
aplicar migrações:

```text
Copy-Item -LiteralPath db.sqlite3 -Destination db.sqlite3.backup -Force
if (Test-Path media) { Copy-Item media db.sqlite3.backup.media -Recurse }
venv\Scripts\python.exe manage.py migrate
```

Só depois inicie o servidor local:

```text
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Os testes usam banco, imagens e destinos temporários do Django e não substituem
o backup do `db.sqlite3` real. A execução suportada usa
`INICIAR_CRUZ_FIT.bat`, que faz backup validado antes de migrar quando já existe
banco. Nunca execute `migrate` no banco real sem confirmar que o backup externo
foi criado e pode ser restaurado. Consulte
`docs/BACKUP_E_RESTAURACAO.md` para restauração offline.

Os hosts locais permitidos são `localhost`, `127.0.0.1` e `[::1]`. HTTPS, HSTS,
filas, infraestrutura distribuída e banco PostgreSQL não fazem parte deste
escopo.
