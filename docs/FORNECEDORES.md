# Fornecedores no Cruz Fit

## Uso

O menu **Fornecedores** permite pesquisar por nome, fantasia, CPF/CNPJ, pessoa de contato ou e-mail, com filtro de ativos/inativos e paginação. Gerentes e superusuários podem cadastrar, editar e inativar; demais usuários autenticados podem consultar.

O nome/razão social é obrigatório. Fantasia, documento, contato, telefone, e-mail, endereço e observações são opcionais. CPF/CNPJ informado é normalizado, tem seus dígitos verificadores conferidos e não pode repetir outro cadastro. CNPJ aceita o formato alfanumérico conforme o [manual da Receita Federal](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj). A validação é local; não comprova situação cadastral na Receita.

Em **Entradas**, o motivo Compra exige fornecedor ativo. Ajuste e Devolução continuam permitindo ausência. O detalhe do fornecedor tem um atalho que já preenche fornecedor e motivo Compra; revise os produtos, quantidades e fornecedor antes de confirmar.

Cada entrada guarda o vínculo e uma cópia do nome/documento do fornecedor no recebimento. Alterações posteriores no cadastro não reescrevem essa origem. Entradas anteriores à migração permanecem sem fornecedor, exibindo “Não informado”.

Editar exige motivo. Inativar tem uma confirmação própria com motivo, impede novos recebimentos desse fornecedor e preserva suas compras. Não existe ação de exclusão ou reativação nesta versão.

## Ativação no computador de uso

A migração `estoque.0013_fornecedores` foi criada e testada em banco isolado. Ela **não foi aplicada ao banco real** nesta entrega; `showmigrations estoque` confirmou seu estado pendente.

Encerre o sistema, conecte o destino externo configurado e abra pelo `INICIAR_CRUZ_FIT.bat` existente. O supervisor de backup faz a cópia validada antes de executar as migrações e abrir o servidor. Se o backup falhar, resolva o problema do dispositivo antes de prosseguir. Não execute migrações em paralelo ao servidor nem contorne esse passo.

## Arquitetura e auditoria

- `Fornecedor`: dados cadastrais, responsável/data da criação e chave/assinatura idempotentes.
- `FornecedorAlteracao`: responsável, motivo, data, chave/assinatura, valores anteriores e novos.
- `FornecedorInativacao`: responsável, motivo, data e chave/assinatura.
- `EntradaEstoque.fornecedor`: nullable e PROTECT, com snapshots `fornecedor_nome` e `fornecedor_documento`.
- Interface pública de escrita em `estoque.services`; implementação do domínio em `estoque/fornecedores.py`. Views fazem validação de formulário, autorização e apresentação; serviços validam novamente e escrevem em transações.
- Chave repetida com dados diferentes é recusada. Repetir uma compra concluída não duplica saldo, mesmo se o fornecedor foi inativado depois.

## Evidências de verificação — 03/11/2026

Ambiente: venv do projeto, Django/SQLite. Baseline anterior: 170 testes passaram. Foram acrescentados 21 testes com cenários Given/When/Then para cadastro, documentos, duplicidade, auditoria, autenticação/autorização, CSRF, entradas, replay, snapshots, erro de formulário e paginação.

Saída real da suíte completa:

```text
Found 191 test(s).
System check identified no issues (0 silenced).
Ran 191 tests in 61.200s
OK
```

Após a organização final dos serviços, a suíte específica foi executada novamente:

```text
Found 21 test(s).
System check identified no issues (0 silenced).
Ran 21 tests in 0.499s
OK
```

O teste inicial de cadastro falhou antes da implementação com `AssertionError: O cadastro de fornecedores ainda não existe`. A revisão também encontrou e corrigiu um erro de template ao consultar histórico cujo responsável foi removido; o cenário passou depois da correção.

Migração ensaiada sobre dados sintéticos na versão 0012, avançando à versão 0013:

```text
MIGRACAO LEGADO: entrada, itens e saldo preservados; fornecedor NULL.
QA ISOLADO: banco sintético pronto; nenhum dado real utilizado.
```

No navegador Chromium, ambiente exclusivo `127.0.0.1:15007`, foi exercitado login, cadastro com CNPJ alfanumérico, detalhe, entrada por compra, revisão, confirmação, histórico e inativação. Saldo do produto sintético: 10 → 13 após entrada de 3; fornecedor inativo saiu do seletor, mas sua compra continuou no histórico. A entrada legada continuou com “Não informado”.

Tamanhos inspecionados: 1365×900 e 390×844, com capturas em `qa-browser-fornecedores-20261103/`. Em detalhe e entrada mobile, `document.documentElement.scrollWidth > innerWidth` retornou `false`. Trata-se de viewport emulado, não ensaio em aparelho físico.

Impeccable: contexto carregado; audit/polish realizados sobre os templates e capturas, preservando a identidade preta/dourada existente. Detector estático retornou `[]` nas áreas alteradas. Ele avisou que não resolve os caminhos `{% static %}` dos templates Django; o CSS foi incluído explicitamente no scan, e o layout foi conferido no navegador. Isso não representa certificação WCAG ou medição de performance. Revisão local realizada pelo executor, sem parecer independente de outro agente.

Também foram verificados Ruff, mypy, `manage.py check`, `makemigrations --check --dry-run` e `git diff --check`. Nenhuma dependência nova, commit, push ou publicação foi feita. Alterações preexistentes do backup foram preservadas.

## Verificação reproduzível

Na raiz do repositório:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_fornecedores -v 2
venv\Scripts\python.exe manage.py test -v 1
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m mypy .
venv\Scripts\python.exe manage.py check
venv\Scripts\python.exe manage.py makemigrations --check --dry-run
git diff --check
```

Os testes usam banco temporário. O banco/media reais não receberam migrações ou dados de teste. O servidor isolado é encerrado ao fim da revisão.
