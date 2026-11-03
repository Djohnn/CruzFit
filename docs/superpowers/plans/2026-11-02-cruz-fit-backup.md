# Cruz Fit — plano de implementação de backup

> **Para o agente executor:** usar `executing-plans`, trabalhar tarefa a tarefa e registrar os resultados reais. Este documento autoriza preparar a implementação em desenvolvimento; os ensaios usam somente banco, imagens e dispositivos simulados em diretórios temporários. Não restaurar, migrar ou modificar os dados reais durante a implementação. Não fazer push ou publicação por consequência deste plano.

**Objetivo:** criar backups recuperáveis do banco e das imagens do Cruz Fit em pendrive ou HD externo, automaticamente antes da abertura do sistema e também por comando manual.

**Arquitetura:** serviços Python separados da interface de linha de comando; snapshot SQLite, pacote ZIP validado e restauração offline com recuperação de interrupções. Um supervisor local mantém um bloqueio do sistema operacional durante a execução do servidor e serializa inicialização, backup e restauração.

**Stack:** Django existente, SQLite, Windows, biblioteca padrão Python (`sqlite3`, `zipfile`, `hashlib`, `json`, `pathlib`, `subprocess`, `msvcrt`) e testes Django/unittest existentes. Sem nova dependência de infraestrutura.

**Estado:** proposta pronta para revisão e execução após confirmação do escopo. Destino principal confirmado pelo usuário: pendrive ou HD externo. Letra e pasta serão escolhidas na configuração no computador de uso; não presumir `E:`.

## 1. Evidências e limites

- Repositório: `C:\desenvolvimento\Contruct\Project_construct`.
- Base observada: branch `codex/impeccable-remediation`, commit `9eaff79`; revalidar antes da execução.
- `construct/settings.py` configura SQLite em `db.sqlite3` e arquivos em `media/`. Este projeto usa templates Django, não o frontend React/PDV/PostgreSQL do TJSys.
- `INICIAR_CRUZ_FIT.bat` e `INSTALAR_CRUZ_FIT.bat` executam migrações. O backup deve acontecer antes delas quando já existir banco.
- `PARAR_CRUZ_FIT.bat` atualmente encerra processos pela porta; adaptar somente o necessário para reconhecer o servidor pertencente a esta instalação.
- `LEIA-ME-EXECUCAO.txt` orienta cópia manual com o sistema parado. `docs/LOCAL_DEVELOPMENT.md` documenta cópia apenas do banco: atualizar ambas as instruções.
- Existe `backups/cruzfit-20261103-024012/` com banco e media. Não foi validado como recuperável. Preservar integralmente esse material.
- Não há ferramenta de backup identificada no grafo de código ou nas áreas operacionais inspecionadas.
- Graphify foi gerado com `graphify extract . --code-only --no-cluster --max-workers 2`: 348 nós e 731 arestas. A extração é estrutural e não inclui a semântica dos documentos e arquivos BAT; estes foram lidos diretamente.

## 2. Escopo proposto e decisões

**Incluído:** configurar o dispositivo, backup manual, backup em toda inicialização antes de migrações, validação independente, restauração offline, logs operacionais e documentação. O backup será completo em cada execução; nenhuma limpeza automática na V1.

**Comportamento proposto:** se o dispositivo não estiver conectado, não corresponder ao configurado, estiver sem espaço ou o backup falhar, não migrar nem iniciar o sistema. Mostrar instrução objetiva para conectar/configurar o dispositivo e tentar novamente. Não criar silenciosamente uma pasta local que substitua o destino externo.

Alternativas consideradas:

1. **Backup completo antes da abertura, recomendado:** ponto de recuperação antes de migrações e banco/imagens sem alterações concorrentes; exige o dispositivo conectado e aumenta o tempo de abertura.
2. **Somente backup manual:** implementação menor, mas depende da lembrança do operador e não protege cada abertura.
3. **Backup agendado durante o uso:** cobre sessões longas, mas exige coordenação de escrita de imagens e banco; fica fora desta primeira entrega.

Não incluir interface web, agendador, nuvem, criptografia própria, backup incremental, retenção com exclusão ou migração para PostgreSQL. A cópia anterior à abertura não cobre mudanças posteriores dessa sessão; orientar backup manual após encerrar o expediente. Uma segunda mídia periódica protege contra falha ou perda do dispositivo principal.

## 3. Contrato operacional

### Configuração e identidade do destino

- Salvar `.backup/config.json` local, com versão de formato, caminho absoluto da pasta, identificador da instalação e identificação do volume Windows.
- O configurador recebe uma pasta explicitamente escolhida pelo operador, confirma o volume montado e grava sua identidade. Recusar volume de origem do projeto, destinos dentro do projeto/media e caminhos não locais nesta V1.
- Antes de cada operação, validar montagem e identidade; letra igual com outro dispositivo deve falhar. Uma troca de letra requer reconfiguração explícita.
- Não criar a raiz de uma unidade ausente. Criar apenas a subpasta autorizada dentro de um volume verificado.
- Configuração e troca de destino só podem ocorrer com o sistema parado e sob o mesmo bloqueio das demais operações.

### Pacote de backup

Nome: `cruzfit-<installation_id>-<UTC YYYYMMDDTHHMMSSZ>-<operation_id>.zip`.

```text
manifest.json
db.sqlite3
media/<caminhos relativos originais>
```

Manifesto versão 1: identificador da instalação, operation_id, reason, data UTC, versão Python/Django, versão do pacote, migrações aplicadas e inventário de cada arquivo com tamanho e SHA-256. O manifesto deve representar também `media/` vazio; media ausente é erro, não exclusão silenciosa de imagens.

- Snapshot pelo `sqlite3.Connection.backup()`, origem aberta em modo somente leitura e com prazo limitado. Não copiar somente `db.sqlite3` enquanto houver conexões ativas nem descartar WAL/journal da origem.
- Incluir todos os arquivos regulares de `media/`; rejeitar links simbólicos, junctions/reparse points e caminhos que escapem da raiz. Conferir inventário antes/depois e falhar se houver alteração inesperada.
- Excluir código, `.git`, ambientes Python, `.env`, configuração de backup, logs, outros backups e arquivos temporários.
- Escrever temporário com sufixo `.partial`, no mesmo destino do ZIP final. Validar CRC, inventário, hashes, SQLite `PRAGMA integrity_check` e `foreign_key_check` antes da publicação por renomeação. Apenas um resultado validado recebe extensão `.zip` final.
- Identificador de operação e motivo são obrigatórios no serviço. Mesma chave com os mesmos parâmetros retorna o pacote já validado; mesma chave com parâmetros diferentes falha. Não reutilizar um pacote ausente/corrompido nem deduplicar duas aberturas diferentes.
- Reservar espaço para snapshot/staging e pacote; verificar volume local e externo. A estimativa não substitui tratar falta de espaço durante a escrita.
- Não apagar backups existentes, inclusive em erro ou falta de espaço. Remover apenas temporários da operação atual, dentro dos caminhos resolvidos e verificados.
- SHA-256 detecta alteração acidental; não é assinatura de autenticidade. Pacotes contêm dados e credenciais de usuários armazenadas no banco; documentar a guarda física do dispositivo.

### Exclusão mútua e execução local

- `.backup/operation.lock`: bloqueio de arquivo Windows mantido pelo processo supervisor, liberado pelo SO ao encerrar. Não usar existência de um arquivo como prova de processo ativo.
- `runtime start` adquire o bloqueio, verifica recuperação pendente e servidor existente, faz backup, executa migrações e mantém o bloqueio durante toda a vida do servidor filho `--noreload`.
- Backup/restauração/configuração manual tentam o mesmo bloqueio e recusam operação se houver servidor ou outra manutenção ativa. Não encerrar o sistema automaticamente para fazer backup.
- Detectar processos Django iniciados fora do supervisor para esta pasta e listener na porta 15001 antes da operação; se a identidade não puder ser verificada, recusar com instrução para encerramento manual. Uma porta livre isoladamente não prova sistema parado.
- Atualizar a documentação: execução suportada usa os launchers. Não prometer proteção contra scripts externos arbitrários que escrevam diretamente no banco ou em media.
- O processo supervisor registra os PIDs e identidade dos processos que iniciou. O encerramento só pode atingir processo validado desta instalação; nunca matar qualquer dono da porta.
- Após queda do supervisor, impedir nova manutenção se o filho ainda existir; não considerar o lock liberado suficiente. Testar também a queda do servidor e a liberação normal.
- Interface CLI retorna 0 em sucesso e código diferente de zero em falha; mensagens em português mostram destino, resultado e ação corretiva, sem expor conteúdo do banco.

### Restauração

- Exige sistema parado, bloqueio exclusivo e confirmação digitada `RESTAURAR`. Validação isolada não pede confirmação e não altera os dados.
- Validar o arquivo completo antes de tocar no destino: versão, inventário exato, hashes/CRC, integridade do banco, histórico de migrações compatível e origem da instalação. Importar outra instalação não faz parte desta V1.
- Rejeitar caminhos absolutos, `..`, barras invertidas ambíguas, unidades Windows, ADS, nomes reservados, membros duplicados inclusive por caixa, links e arquivos fora do inventário. Limitar tamanho descompactado e quantidade: padrões configuráveis de 20 GiB e 100 mil arquivos; exceder falha antes da extração e orienta revisão da configuração.
- Extrair por streaming para staging local em volume igual ao destino; validar novamente. Não usar `extractall()` sem validação dos membros.
- Criar e validar backup `pre_restore` no externo antes da substituição. Se isso falhar, manter destino intacto.
- Preparar banco e media novos, fechar conexões e registrar um journal durável em `.backup/recovery/`. Preservar originais em diretório irmão de recuperação; não mesclar media antigo com novo.
- Banco e media são duas substituições, não uma transação atômica. Registrar e sincronizar cada etapa. Em falha, reverter ambos. Em queda de energia, próxima inicialização detecta journal e termina rollback antes de aceitar abertura ou outra operação.
- Nunca apagar WAL/journal de uma conexão ativa; banco recuperado não pode receber sidecars antigos. Gerenciar banco e sidecars antigos como conjunto no rollback e somente com processos encerrados.
- Só registrar sucesso após validar o conjunto instalado. Guardar recovery e backup anterior até a confirmação durável; falha de rollback bloqueia início e informa os caminhos de recuperação.

## 4. Arquivos e responsabilidades

Criar em `construct/backup/`:

| Arquivo | Responsabilidade |
|---|---|
| `__init__.py` | Pacote, sem efeitos colaterais |
| `contracts.py` | Dataclasses tipadas, erros e formato do manifesto |
| `config.py` | Configuração, identidade de instalação e destino |
| `windows.py` | Identidade do volume e dos processos, lock do SO |
| `archive.py` | Inventário, hashes e validação/extração do ZIP |
| `services.py` | Criar, verificar e restaurar backups; idempotência e auditoria |
| `recovery.py` | Journal e rollback de substituições interrompidas |
| `runtime.py` | Supervisor start/prepare-database/stop e CLI operacional |
| `cli.py` | CLI de configurar/criar/verificar/restaurar, sem regras de negócio |
| `test_config.py`, `test_archive.py`, `test_services.py`, `test_recovery.py`, `test_runtime.py` | Testes focados e integrados |

Criar `CONFIGURAR_BACKUP_CRUZ_FIT.bat`, `BACKUP_CRUZ_FIT.bat`, `RESTAURAR_BACKUP_CRUZ_FIT.bat` e `docs/BACKUP_E_RESTAURACAO.md`.

Modificar os três launchers existentes, `.gitignore`, `LEIA-ME-EXECUCAO.txt`, `docs/LOCAL_DEVELOPMENT.md` e `estoque/test_migrations.py` somente para atualizar o contrato documental. Não criar modelos ou migrações: esta operação é de arquivos, com auditoria em `.backup/audit.jsonl` local e metadados de sucesso no manifesto externo. Registrar operação, motivo, início/fim, resultado e erro; não incluir dados dos registros.

## 5. Tarefas do agente, em ordem

### Tarefa 1 — preflight e configuração

- [ ] Revalidar status, HEAD e instruções locais. Preservar alterações existentes. Consultar `graphify check-update .`; atualizar se necessário, e executar `graphify query "settings startup SQLite media backup" --budget 2500`.
- [ ] Carregar `django-qa-testes`, `test-driven-development` e `verification-before-completion`. Inspecionar testes Django existentes antes de acrescentar novos. Não instalar pytest, E2E ou outro runner para esta entrega.
- [ ] Criar testes de configuração com `SimpleTestCase`, `TemporaryDirectory` e adaptador de volume simulado: destino válido, caminho com espaços/acentos, destino local recusado, unidade ausente, identidade trocada, JSON inválido e configuração incompleta.
- [ ] Executar `venv\Scripts\python.exe manage.py test construct.backup.test_config -v 2`; capturar falha pela funcionalidade ausente.
- [ ] Implementar contracts/config/windows mínimos para passar os casos. Não testar conexão de pendrive real nesta fase.
- [ ] Reexecutar o teste e registrar output bruto.

### Tarefa 2 — pacote íntegro e repetição segura

- [ ] Criar SQLite de fixture com `sqlite3.connect()` em pasta temporária, tabela sintética e uma imagem sintética. Não usar `settings.DATABASES` real nem ler media real em testes.
- [ ] Derivar os cenários CT-BKP-01 a 06 abaixo em docstrings Given/When/Then; executar testes vermelhos antes do serviço.
- [ ] Implementar inventário, snapshot, manifesto, ZIP temporário, validação, publicação e auditoria. O serviço recebe caminhos explicitamente para permitir ensaio isolado.
- [ ] Implementar orçamento de tempo do snapshot: 120 segundos por padrão, cancelamento pelo callback de progresso e erro controlado; não permitir loop infinito em banco ocupado.
- [ ] Executar `venv\Scripts\python.exe manage.py test construct.backup.test_archive construct.backup.test_services -v 2`; exigir que pacote inválido jamais seja apresentado como sucesso.

### Tarefa 3 — supervisor e launchers

- [ ] Escrever e executar testes vermelhos CT-BKP-07 a 10, com subprocessos auxiliares e banco temporário; não iniciar nem parar o servidor real.
- [ ] Implementar `runtime start`: lock → verificar recuperação/processos → backup validado → `manage.py migrate --noinput` → servidor → aguardar fim/liberar lock. Passar comandos como listas, sem `shell=True` e sem interpolação de entrada em PowerShell.
- [ ] Implementar `runtime prepare-database`: mesmo gate para o instalador existente. Se banco ainda não existir, permitir somente primeira criação sem alegar backup; se existir e estiver inválido, parar. O fluxo completo de primeira instalação deve terminar com destino configurado e backup inicial validado antes de liberar uso.
- [ ] Implementar `runtime stop` com validação de identidade/PIDs. A ação explícita de parar pode encerrar o servidor da instalação; backup nunca chama stop por conta própria.
- [ ] Fazer BATs usarem `.runtime\Scripts\python.exe`, diretório `%~dp0` e aspas em todos os caminhos. O desenvolvimento usa `venv\Scripts\python.exe`. Os scripts de backup não instalam dependências ou migram banco.
- [ ] Preservar porta 15001 e abertura do navegador somente após readiness do servidor filho; se o backup falhar, o launcher também retorna falha e não abre o navegador.
- [ ] Executar `venv\Scripts\python.exe manage.py test construct.backup.test_runtime -v 2`; registrar ordem real de chamadas e falhas injetadas.

### Tarefa 4 — verificação e restauração recuperável

- [ ] Escrever testes vermelhos CT-BKP-11 a 15 antes da restauração.
- [ ] Implementar validação independente e modo de restauração com destino explicitamente injetado nos testes; confirmação fica na CLI e todas as regras ficam no serviço.
- [ ] Implementar staging, pre_restore, journal persistido, substituição de ambos os componentes e rollback. Injetar falha entre cada etapa e simular reinicialização a partir do journal.
- [ ] Restaurar o pacote de uma fixture Django em outro diretório temporário. Em subprocesso com módulo de settings temporário, abrir o banco restaurado e comprovar login, estoque e imagem, sem executar migração no banco real.
- [ ] Executar `venv\Scripts\python.exe manage.py test construct.backup.test_recovery construct.backup.test_services -v 2` e preservar o output.

### Tarefa 5 — operação e documentação

- [ ] Implementar `python -m construct.backup.cli` com `configure --destino`, `create --reason --operation-id`, `verify --arquivo` e `restore --arquivo --reason --operation-id`; o restore interativo exige a palavra exata de confirmação.
- [ ] Criar BATs para escolher/configurar a pasta externa, backup manual e seleção do pacote a restaurar. Mensagens: conectar dispositivo, encerrar Cruz Fit, liberar espaço ou reconfigurar destino, conforme o erro real.
- [ ] Documentar configurar → encerrar sistema → backup manual → verificar → restaurar. Explicar ausência de retenção automática, backup após expediente, segunda mídia e limite de recuperação da sessão.
- [ ] Atualizar instalador/runbook com a sequência backup validado → `manage.py migrate` → `manage.py runserver`, preservando teste de ordem em `estoque/test_migrations.py` e acrescentando a obrigação de backup anterior.
- [ ] Ignorar `.backup/`, pacotes/temporários gerados e `graphify-out/` em `.gitignore`. Não apagar nem versionar backups antigos ou arquivos de dados.
- [ ] Fazer ensaio Windows com mídia e instalação descartáveis: caminho com espaços, dispositivo desconectado antes e durante escrita, dois cliques simultâneos, parada do servidor e restauração cancelada.

### Tarefa 6 — revisão do coordenador e aceite

- [ ] Reexecutar grafo para a área alterada; revisar se os comandos apenas adaptam entrada/saída e serviços concentram as regras.
- [ ] Rodar os checks abaixo na raiz do repositório e capturar duração, código de saída e output bruto.
- [ ] Revisar diff: nenhum dado real, pacote, configuração privada, mudança de modelo ou alteração de requisito para mascarar falha.
- [ ] Entregar relatório com arquivos/linhas, decisões, limitações e evidência CT-BKP-01 a 15. Coordenador só aceita após revisar pacote restaurado e evidência de falhas/rollback.

## 6. Matriz de aceitação Given/When/Then

| Caso | Given / When / Then |
|---|---|
| CT-BKP-01 | Banco e media temporários válidos / criar pacote / banco íntegro, bytes das imagens e inventário conferem. |
| CT-BKP-02 | Banco SQLite com WAL sintético / snapshot / transações confirmadas presentes sem depender da cópia dos sidecars. |
| CT-BKP-03 | Unidade ausente, identidade trocada ou sem espaço / criar / erro explícito, sem ZIP final e sem mudar origem ou backups anteriores. |
| CT-BKP-04 | Falha ao copiar, desconexão ou timeout / criar / erro e temporário sem status de sucesso; retry com nova tentativa é possível. |
| CT-BKP-05 | Mesma chave e parâmetros / repetir / mesmo pacote válido; chave com parâmetros distintos falha e não substitui pacote. |
| CT-BKP-06 | Media vazio, ausente, link ou alterado durante leitura / criar / vazio é representado; demais casos falham sem publicação. |
| CT-BKP-07 | Servidor ou manutenção ativa / backup ou restore / recusa; processo existente permanece intacto. |
| CT-BKP-08 | Nova abertura / iniciar / backup ocorre antes de migrate e runserver; erro impede ambos. |
| CT-BKP-09 | Duas aberturas ou queda do supervisor com filho vivo / iniciar novamente / só um servidor; manutenção recusada até encerrar filho identificado. |
| CT-BKP-10 | Porta ocupada por processo alheio / parar / nenhum processo alheio é encerrado; erro orienta operador. |
| CT-BKP-11 | Pacote truncado, checksum diferente, versão desconhecida ou banco incompatível / verificar/restaurar / erro antes de modificar destino. |
| CT-BKP-12 | ZIP com traversal, links, ADS, duplicatas ou limites excedidos / validar / recusa sem escrever fora do staging. |
| CT-BKP-13 | Pacote válido e destino sintético diferente / restaurar confirmado / login funciona, estoque e imagens correspondem ao backup; media antigo não reaparece. |
| CT-BKP-14 | Usuário cancela, pre_restore falha ou erro entre substituições / restaurar / destino anterior preservado ou rollback completo. |
| CT-BKP-15 | Processo interrompido após cada etapa persistida / reiniciar / recuperação restaura conjunto anterior antes de abrir; recuperação impossível bloqueia início e preserva evidência. |

Exemplo mínimo do estilo de teste de snapshot, inteiramente sintético:

```python
def test_snapshot_preserves_committed_rows(self):
    """Given banco sintético; When criar snapshot; Then preservar linhas."""
    import sqlite3
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from construct.backup.archive import snapshot_database

    with TemporaryDirectory() as directory:
        source = Path(directory) / "source.sqlite3"
        target = Path(directory) / "snapshot.sqlite3"
        with sqlite3.connect(source) as database:
            database.execute("CREATE TABLE exemplo (valor TEXT)")
            database.execute("INSERT INTO exemplo VALUES (?)", ("produto-teste",))
        snapshot_database(source=source, target=target, timeout_seconds=120)
        with sqlite3.connect(target) as restored:
            self.assertEqual(
                restored.execute("SELECT valor FROM exemplo").fetchall(),
                [("produto-teste",)],
            )
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
```

Contrato público adicional: `create_backup`, `verify_backup` e `restore_backup` em `services.py` recebem caminhos/configuração explicitamente, retornam dataclasses tipadas de `contracts.py` e lançam `BackupError` controlado. Os testes de sistema de arquivos usam `SimpleTestCase`; testes de dados Django usam o banco temporário do runner ou subprocesso isolado. Nunca acrescentar uma opção CLI para ignorar o lock ou permitir backup parcial.

## 7. Comandos de verificação para o executor

Executar em `C:\desenvolvimento\Contruct\Project_construct`:

```powershell
venv\Scripts\python.exe manage.py test construct.backup -v 2
venv\Scripts\python.exe manage.py test -v 2
venv\Scripts\python.exe manage.py check
venv\Scripts\python.exe manage.py makemigrations --check --dry-run
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m mypy .
git diff --check
git status --short
```

Esperado: zero falhas/erros nos testes, zero erros em check/Ruff/mypy, nenhuma migração nova e nenhum erro no diff. Registrar problemas preexistentes separadamente; não afirmar qualidade limpa sem os resultados nem ampliar silenciosamente o escopo. Não rodar `migrate` no banco real como parte destes checks.

## 8. Briefing pronto para o executor

> Implemente o plano `docs/superpowers/plans/2026-11-02-cruz-fit-backup.md` após confirmação do escopo. Oriente-se pelo graphify antes de tocar no código. Preserve Django/SQLite/templates e o funcionamento local. Entregue backup completo em dispositivo externo escolhido pelo operador, automático antes da abertura/migração, e restauração offline com rollback. Execute as seis tarefas em ordem, escreva primeiro os cenários/testes e apresente o output real de cada runner. Use exclusivamente dados e pastas descartáveis durante os ensaios. Não toque no banco/media reais nem nos backups já existentes. Não faça push/publicação. Se surgir decisão fora do contrato, reporte antes de mudar o escopo. Entregue resumo, arquivos/linhas, saída bruta, evidências de recuperação e pendências.

Se houver delegação no fluxo de sprint, a regra fornecida pelo usuário exige **GPT 5.6 Luna explicitamente**. Não substituir por outra versão/modelo nem apresentar trabalho de outro modelo como Luna válida. Este planejamento não iniciou subagentes.

## 9. Fontes técnicas

- [SQLite — Online Backup API](https://www.sqlite.org/backup.html): snapshot consistente do banco; consistência de media depende do protocolo offline deste plano.
- [Python — sqlite3.Connection.backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup): API de snapshot na biblioteca padrão; usar somente recursos compatíveis com o Python instalado.
- [Python — zipfile](https://docs.python.org/3/library/zipfile.html): criação/verificação de ZIP e cuidados com extração de pacotes.

O plano foi revisado quanto ao destino confirmado, ordem backup/migração, preservação dos dados, exclusão mútua, restauração de banco e media, falhas parciais e critérios de aceite. Nenhuma funcionalidade descrita acima foi implementada ou testada nesta etapa de planejamento.
