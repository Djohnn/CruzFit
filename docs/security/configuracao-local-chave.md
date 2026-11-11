# Configuração local e chave de assinatura

## Execução

A distribuição inicia com `DEBUG=False`. O launcher continua limitado a
`127.0.0.1`; não defina redirecionamento obrigatório para HTTPS nessa instalação
local. Para depuração manual, inicie o Django com `CRUZFIT_DEVELOPMENT=1` no
ambiente desse processo e remova a variável ao voltar à execução distribuída.
Não altere o launcher para habilitar depuração.

Com `DEBUG=False`, o projeto ainda publica apenas `/static/` e `/media/` pelas
rotas do Django. Os caminhos passam pelos finders de estáticos e pelo diretório
de mídia configurado; não há listagem de diretórios. O launcher local não usa
`collectstatic` e não exige HTTPS para readiness, login ou impressão.

## Chave por instalação

Se `DJANGO_SECRET_KEY` estiver definida, Django usa esse valor e não lê nem cria
um arquivo local. Um valor vazio é erro de configuração. Sem a variável, a chave
é criada com fonte criptográfica em `%LOCALAPPDATA%\CruzFit\instances\<id>.key`
no Windows. O identificador é derivado do caminho da instalação. A pasta é do
usuário atual. A criação grava e sincroniza um arquivo temporário, depois o
publica com renomeação no Windows ou link físico no Linux/macOS. A operação
preserva a chave criada por outra inicialização concorrente. Falhas de leitura
ou escrita interrompem a inicialização; não há chave pública de reserva. No
Linux/macOS, a localização equivalente usa `XDG_DATA_HOME` ou
`~/.local/share/CruzFit/instances/`.

Não copie o valor da chave para logs, chamados ou relatórios. Atualizações feitas
no mesmo perfil do Windows e no mesmo caminho conservam a chave automaticamente.
Uma instalação nova recebe outra chave.

## Atualização, backup e recuperação

O segredo anterior embutido no código era público; não é preservado como chave
legada. Na primeira inicialização após esta atualização, sessões existentes são
invalidadas e os funcionários precisam entrar novamente. Uma revisão de venda
que ainda estivesse aberta no navegador deve ser refeita. A confirmação de venda
valida a assinatura com a chave corrente (`caixa.checkout-review`), então uma
revisão pendente assinada pelo segredo anterior será recusada e precisará ser
gerada de novo.

O backup operacional do banco e da mídia não inclui a chave privada. Para
restaurar uma instalação existente, mantenha uma cópia protegida do arquivo de
chave e restaure-a no perfil/caminho que corresponde à instalação; ou forneça o
mesmo segredo por `DJANGO_SECRET_KEY` no ambiente do launcher. Proteja a cópia
com controles de acesso equivalentes aos dados financeiros e nunca a inclua em
backup compartilhado sem criptografia. Para mudar o segredo deliberadamente,
pare o servidor, guarde o novo segredo fora do banco e configure-o via
`DJANGO_SECRET_KEY`; isso encerra sessões e invalida revisões de venda pendentes.
Não apague o arquivo para “recuperar” a inicialização: uma chave existente
inválida deve ser recuperada de uma cópia protegida ou substituída de forma
deliberada pelo administrador.
