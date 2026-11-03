# Backup e restauração do Cruz Fit

O Cruz Fit usa SQLite local e a pasta `media/`. O backup principal deve ficar
em uma pasta escolhida pelo operador dentro de um pendrive ou HD externo
montado. A aplicação não presume letra de unidade, nuvem ou armazenamento
remoto.

## Configuração inicial

1. Instale o Cruz Fit com `INSTALAR_CRUZ_FIT.bat`.
2. Execute `CONFIGURAR_BACKUP_CRUZ_FIT.bat`.
3. Informe uma pasta existente no dispositivo externo. O sistema grava a
   identidade do volume em `.backup/config.json` e rejeita troca de dispositivo.
   Ao reconfigurar a pasta ou trocar a letra da unidade, o `installation_id`
   existente é preservado; assim, pacotes antigos continuam restauráveis.
   Um novo identificador só é criado por ação explícita:
   `configure --new-installation-id`.

Não configure uma pasta dentro do projeto, de `media/` ou uma unidade ausente.
Uma troca de letra ou dispositivo exige reconfiguração explícita.

## Abertura e backup automático

Use `INICIAR_CRUZ_FIT.bat`. O supervisor:

1. adquire o lock do sistema operacional;
2. verifica recuperação pendente, processo existente e destino externo;
3. cria e valida o backup completo quando `db.sqlite3` já existe;
4. executa `migrate --noinput`;
5. inicia o servidor com `--noreload` e só libera o navegador após readiness.

Se o dispositivo estiver ausente, trocado, dentro do projeto, em `media/`,
for um link/junction/reparse point, estiver sem espaço ou se qualquer validação
falhar, migração e servidor não são executados.

Na primeira instalação, o banco é criado pela migração e um backup inicial
validado é publicado antes de liberar o uso.

O supervisor grava diagnóstico persistente em
`.backup/logs/supervisor.log`. Esse arquivo registra falhas de preflight,
backup, migração e readiness sem registrar senhas ou dados de registros.

## Backup manual

Pare o sistema com `PARAR_CRUZ_FIT.bat` e execute `BACKUP_CRUZ_FIT.bat`.
Backup manual e restauração são recusados enquanto o servidor Cruz Fit estiver
ativo; o sistema não encerra processos externos automaticamente.
Informe o motivo. A operação exige `operation_id`, valida inventário, hashes,
CRC, integridade SQLite e migrações antes de renomear o `.partial` para `.zip`.
Backups existentes nunca são apagados automaticamente.

O pacote contém apenas `manifest.json`, `db.sqlite3` e `media/`. O manifesto
registra instalação, operação, motivo, versões, migrações e SHA-256 de cada
arquivo. SHA-256 detecta alteração acidental, mas não substitui guarda física
segura: o ZIP contém o banco e credenciais armazenadas pelo Django.

## Verificação e restauração

Para validar sem alterar os dados:

```text
\.runtime\Scripts\python.exe -m construct.backup.cli verify --arquivo "D:\\Cruz Fit Backup\\pacote.zip"
```

Para restaurar, mantenha o sistema parado, execute
`RESTAURAR_BACKUP_CRUZ_FIT.bat` e digite exatamente `RESTAURAR`. O serviço:

- valida o ZIP inteiro antes de tocar no destino;
- cria um pacote `pre_restore` no dispositivo externo;
- extrai por streaming em staging local;
- substitui banco e media com journal durável;
- reverte ambos em caso de falha e tenta o rollback pendente no próximo início.

Cancelar, falhar o `pre_restore`, detectar traversal/link/duplicata, exceder
limites ou interromper a substituição não deve apagar o conjunto anterior.

O manifesto também é comparado com as migrações disponíveis no código atual.
Pacotes com migração futura ou desconhecida são rejeitados antes do
`pre_restore`; pacotes antigos compatíveis podem ser atualizados pelo
`migrate` do supervisor.

## Rotina recomendada e limites

Faça um backup manual após encerrar o expediente. A cópia automática da
abertura protege o estado anterior às migrações e à sessão, mas não substitui
uma cópia após alterações feitas durante a sessão. Use uma segunda mídia
periodicamente para proteger contra perda física do dispositivo principal.

Não há retenção automática: o Cruz Fit nunca exclui backups existentes.
Também não há interface web, nuvem, agendador ou backup incremental nesta
versão.

### Criptografia

Esta versão não implementa criptografia própria nem possui gerenciamento de
chaves. O risco residual é aceito para o escopo local: o ZIP contém o banco e
credenciais Django, portanto o pendrive/HD externo deve permanecer sob guarda
física do operador. Não existe senha fixa, chave no código ou senha em BAT.
