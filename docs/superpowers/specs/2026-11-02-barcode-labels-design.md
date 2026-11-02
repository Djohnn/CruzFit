# Código de barras e etiquetas térmicas — Cruz Fit

## Objetivo

Adicionar código de barras EAN-13 aos produtos e permitir a impressão de etiquetas em impressora térmica, sem quebrar os fluxos atuais de cadastro, consulta, estoque, entrada ou baixa.

## Escopo aprovado

- Campo de código de barras no produto.
- Digitação manual de EAN-13.
- Geração automática de EAN-13 pelo cadastro.
- Unicidade do código por produto.
- Exibição no estoque, consulta e detalhe.
- Busca de produto por nome ou código na baixa.
- Cadastro de modelos de etiqueta com dimensões em milímetros.
- Impressão individual ou em lote pela página de impressão do navegador.
- Compatibilidade com leitores USB que funcionam como teclado.

## Decisões de arquitetura

### Produto

`Produto` receberá um campo `codigo_barras` com até 13 dígitos, único quando preenchido e opcional para preservar produtos já existentes. A validação aceitará apenas EAN-13 válido, removendo separadores comuns como espaços e hífens antes da validação.

Produtos já cadastrados continuarão funcionando sem código. O banco impedirá códigos duplicados.

### Geração EAN-13

A geração será feita no backend por uma função própria, sem depender de serviço externo. O código terá 12 dígitos-base e o dígito verificador EAN-13 será calculado pelo sistema. O botão do cadastro solicitará um código disponível ao backend e preencherá o campo.

Os códigos gerados pelo sistema usarão uma faixa interna reservada para operação própria da loja. Isso evita apresentar um código interno como se fosse uma numeração comercial GS1 registrada.

### Modelo de etiqueta

Será criado um modelo persistido para configuração da impressão, com:

- nome do modelo;
- largura da etiqueta em milímetros;
- altura da etiqueta em milímetros;
- margens horizontal e vertical;
- espaçamento entre etiquetas;
- indicador de modelo ativo.

Será criado um modelo inicial de referência de 50 × 30 mm. O usuário poderá cadastrar outros tamanhos sem alteração de código.

### Impressão térmica

A página de impressão receberá o produto, o modelo e a quantidade de etiquetas. Cada etiqueta terá:

- marca Cruz Fit;
- nome do produto;
- tamanho, cor e modelo quando informados;
- imagem gráfica EAN-13;
- número do código abaixo das barras.

O CSS de impressão usará `@page` com as dimensões do modelo selecionado e uma etiqueta por página, permitindo que o driver da impressora térmica faça o corte ou avanço correto. A impressão ocorrerá pelo diálogo normal do navegador; impressão silenciosa ficará fora deste escopo por depender de integração específica do fabricante.

### Busca na baixa

O catálogo usado pela busca da baixa incluirá nome e código. A busca aceitará qualquer um dos dois valores e continuará usando o ID oculto selecionado para o POST, preservando a validação atual no backend.

## Fluxos

### Cadastro

1. Usuário abre o cadastro de produto.
2. Digita um EAN-13 ou clica em “Gerar código”.
3. O sistema valida formato e unicidade.
4. O produto é salvo com o código, quando informado.

### Impressão

1. Usuário abre o detalhe do produto.
2. Clica em “Imprimir etiquetas”.
3. Escolhe o modelo de etiqueta e a quantidade.
4. O sistema abre a superfície de impressão térmica.
5. Usuário confirma a impressora térmica no navegador.

### Configuração

1. Usuário abre “Modelos de etiqueta”.
2. Cadastra nome e medidas em milímetros.
3. O sistema rejeita dimensões zeradas ou negativas.
4. O modelo fica disponível no fluxo de impressão.

## Compatibilidade e segurança

- Produtos sem código não serão removidos nem bloqueados.
- O código enviado pelo navegador será validado novamente no servidor.
- A busca da baixa não permitirá selecionar produto inexistente apenas alterando o HTML.
- As rotas de cadastro, configuração e impressão exigirão autenticação conforme o padrão atual.
- Nenhum pagamento, caixa, fornecedor ou integração externa será adicionado.

## Testes de aceitação

- EAN-13 válido é aceito.
- EAN-13 com dígito verificador inválido é rejeitado.
- Código duplicado é rejeitado.
- Geração automática retorna código válido e disponível.
- Produto legado sem código continua renderizando.
- Estoque, consulta e detalhe exibem o código quando presente.
- Busca da baixa encontra pelo nome e pelo código.
- Modelo de etiqueta aceita medidas válidas e rejeita medidas inválidas.
- Página de impressão usa as medidas do modelo selecionado.
- A suíte existente permanece verde.

## Fora do escopo

- Leitura pela câmera do celular ou webcam.
- Impressão silenciosa sem diálogo do sistema operacional.
- Integração com SDK proprietário de impressora.
- Registro de numeração GS1 oficial.
- Alterações em vendas, financeiro ou pagamentos.
