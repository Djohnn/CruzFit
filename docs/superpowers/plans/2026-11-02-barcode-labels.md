# Código de barras e etiquetas térmicas Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with test-first development. Keep each checkpoint reviewable and do not commit or push without explicit authorization.

**Goal:** Adicionar EAN-13 aos produtos, permitir busca por código na baixa e imprimir etiquetas térmicas usando modelos de tamanho cadastráveis.

**Architecture:** A validação e a geração EAN-13 ficarão em `estoque/barcodes.py`, sem dependência externa. `Produto` armazenará um código opcional e único; `ModeloEtiqueta` armazenará as dimensões em milímetros. As views Django continuarão controlando os fluxos existentes, e a impressão será uma página HTML/CSS própria para o driver da impressora térmica.

**Tech Stack:** Django, SQLite/PostgreSQL conforme o ambiente atual, templates Django, JavaScript vanilla, CSS de impressão, unittest/Django TestCase.

---

## Mapa de arquivos

- Create: `estoque/barcodes.py` — normalização, validação, dígito verificador, geração e SVG EAN-13.
- Create: `estoque/test_barcodes.py` — testes unitários da regra EAN-13.
- Create: `estoque/templates/modelos_etiqueta.html` — cadastro dos modelos de etiqueta.
- Create: `estoque/templates/imprimir_etiquetas.html` — seleção de modelo, quantidade e superfície de impressão.
- Modify: `estoque/models.py` — campo `Produto.codigo_barras` e modelo `ModeloEtiqueta`.
- Create: `estoque/migrations/0010_produto_codigo_barras_modeloetiqueta.py` — schema e modelo padrão 50 × 30 mm.
- Modify: `estoque/forms.py` — `ProdutoForm` e `ModeloEtiquetaForm` com validações.
- Modify: `estoque/views.py` — cadastro, endpoint de geração, modelos e impressão.
- Modify: `estoque/urls.py` — rotas de geração, modelos e impressão.
- Modify: `estoque/templates/add_produto.html` — campo e botão para código.
- Modify: `estoque/templates/produto.html` — exibição e link de impressão.
- Modify: `estoque/templates/estoque.html` — código visível no card.
- Modify: `estoque/templates/consulta_produto.html` — código visível no card.
- Modify: `estoque/templates/registrar_baixa.html` — busca por nome ou código.
- Modify: `templates/base.html` — link para modelos de etiqueta.
- Modify: `templates/static/estoque/css/css.css` — controles do código e layout térmico.
- Modify: `estoque/test_barcode_feature.py` — integração de cadastro, busca, modelos e impressão.

---

### Task 1: Criar as regras EAN-13

**Files:**
- Create: `estoque/barcodes.py`
- Create: `estoque/test_barcodes.py`

- [ ] **Step 1: Escrever os testes vermelhos**

Adicionar testes que cubram uma base EAN-13 conhecida, normalização, erro de dígito verificador, tamanho inválido e geração única:

```python
from django.test import SimpleTestCase

from .barcodes import (
    calculate_ean13_check_digit,
    generate_ean13,
    normalize_ean13,
    validate_ean13,
)


class Ean13Tests(SimpleTestCase):
    def test_calculates_known_check_digit(self):
        self.assertEqual(calculate_ean13_check_digit('400638133393'), '1')

    def test_normalizes_spaces_and_hyphens(self):
        self.assertEqual(normalize_ean13('4006-3813 3393 1'), '4006381333931')

    def test_rejects_invalid_check_digit(self):
        with self.assertRaises(ValueError):
            validate_ean13('4006381333932')

    def test_generates_available_internal_code(self):
        generated = generate_ean13({'2000000000000'})
        self.assertEqual(len(generated), 13)
        self.assertTrue(validate_ean13(generated))
        self.assertNotEqual(generated, '2000000000000')
```

- [ ] **Step 2: Rodar os testes e confirmar RED**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcodes --verbosity 1
```

Expected: `ImportError` ou falha equivalente porque `estoque/barcodes.py` ainda não existe.

- [ ] **Step 3: Implementar o mínimo necessário**

Implementar:

```python
import secrets


def normalize_ean13(value):
    raw = str(value or '').replace('-', '').replace(' ', '')
    normalized = ''.join(character for character in raw if character.isdigit())
    if len(normalized) != len(raw):
        raise ValueError('O código de barras deve conter somente dígitos.')
    if len(normalized) != 13:
        raise ValueError('O código de barras deve conter 13 dígitos.')
    return normalized


def calculate_ean13_check_digit(base12):
    if len(base12) != 12 or not base12.isdigit():
        raise ValueError('A base EAN-13 deve conter 12 dígitos.')
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(base12))
    return str((10 - total % 10) % 10)


def validate_ean13(value):
    normalized = normalize_ean13(value)
    if calculate_ean13_check_digit(normalized[:12]) != normalized[-1]:
        raise ValueError('O dígito verificador do código de barras é inválido.')
    return normalized


def generate_ean13(used_codes):
    used = {str(code) for code in used_codes if code}
    while True:
        base12 = '200' + ''.join(str(secrets.randbelow(10)) for _ in range(9))
        code = base12 + calculate_ean13_check_digit(base12)
        if code not in used:
            return code
```

`validate_ean13` deve retornar o valor normalizado quando válido e lançar `ValueError` quando inválido. O prefixo `200` identifica geração interna e não deve ser apresentado como registro GS1 oficial.

- [ ] **Step 4: Rodar novamente e confirmar GREEN**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcodes --verbosity 1
```

Expected: todos os testes da classe passam.

---

### Task 2: Adicionar código ao produto e à migração

**Files:**
- Modify: `estoque/models.py`
- Modify: `estoque/forms.py`
- Create: `estoque/migrations/0010_produto_codigo_barras_modeloetiqueta.py`
- Create: `estoque/test_barcode_feature.py`

- [ ] **Step 1: Escrever teste vermelho do modelo**

Adicionar um `TestCase` que cria dois produtos: o primeiro com EAN-13 válido e o segundo com o mesmo código. O primeiro deve ser salvo; o segundo deve falhar na unicidade do banco/formulário.

```python
def test_product_stores_unique_barcode(self):
    first = Produto.objects.create(
        nome='Produto com código',
        codigo_barras='4006381333931',
        quantidade=1,
        preco_compra=1,
        preco_venda=2,
    )
    self.assertEqual(first.codigo_barras, '4006381333931')
    duplicate = ProdutoForm(data={
        'nome': 'Outro produto',
        'codigo_barras': first.codigo_barras,
        'categoria': '',
        'tamanho': '',
        'modelo': '',
        'cor': '',
        'tecido': '',
        'genero': '',
        'quantidade': '1',
        'preco_compra': '1',
        'preco_venda': '2',
    })
    self.assertFalse(duplicate.is_valid())
    self.assertIn('codigo_barras', duplicate.errors)
```

Usar uma fixture de categoria e os campos numéricos obrigatórios já existentes no setup do arquivo.

- [ ] **Step 2: Rodar o teste e confirmar RED**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.BarcodeProductTests --verbosity 1
```

Expected: falha porque `Produto` ainda não possui `codigo_barras`.

- [ ] **Step 3: Implementar o campo e o formulário**

Adicionar ao modelo:

```python
codigo_barras = models.CharField(
    max_length=13,
    unique=True,
    blank=True,
    null=True,
)
```

No `ProdutoForm`, declarar o campo com limpeza baseada em `validate_ean13`, preservando vazio como `None`. Gerar a migração com:

```powershell
venv\Scripts\python.exe manage.py makemigrations estoque
```

Na migração, criar também `ModeloEtiqueta` com `DecimalField` para medidas, `BooleanField(default=True)` e ordenação por nome. Inserir o registro inicial `Etiqueta térmica 50x30 mm`, com largura `50`, altura `30`, margens `0` e espaçamento `0`.

- [ ] **Step 4: Aplicar migração e verificar GREEN**

Run:

```powershell
venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.BarcodeProductTests --verbosity 1
```

Expected: migração aplicada e teste de unicidade passando.

---

### Task 3: Integrar cadastro e geração automática

**Files:**
- Modify: `estoque/views.py`
- Modify: `estoque/urls.py`
- Modify: `estoque/templates/add_produto.html`
- Modify: `templates/static/estoque/css/css.css`
- Modify: `estoque/test_barcode_feature.py`

- [ ] **Step 1: Escrever testes vermelhos do fluxo web**

Adicionar testes autenticados para:

```python
def test_generate_barcode_endpoint_returns_valid_available_code(self):
    response = self.client.get(reverse('gerar_codigo_barras'))
    self.assertEqual(response.status_code, 200)
    self.assertTrue(validate_ean13(response.json()['codigo_barras']))


def test_product_create_persists_manual_barcode(self):
    response = self.client.post(reverse('add_produto'), {
        'nome': 'Regata com código',
        'codigo_barras': '4006381333931',
        'categoria': '',
        'tamanho': 'M',
        'modelo': 'Slim',
        'cor': 'Preta',
        'tecido': 'Dry fit',
        'genero': 'MASCULINO',
        'quantidade': '2',
        'preco_compra': '10',
        'preco_venda': '25',
    })
    self.assertRedirects(response, reverse('add_produto'))
    self.assertTrue(Produto.objects.filter(codigo_barras='4006381333931').exists())
```

- [ ] **Step 2: Rodar e confirmar RED**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.BarcodeProductTests --verbosity 1
```

Expected: falha porque a URL e a persistência do campo ainda não existem.

- [ ] **Step 3: Implementar view, rota e formulário visual**

Na view de cadastro:

- incluir `codigo_barras` em `form_data` e no objeto `Produto`;
- normalizar/validar com `validate_ean13` quando informado;
- rejeitar duplicidade e manter os dados na sessão ao redirecionar;
- adicionar endpoint autenticado `gerar_codigo_barras` que consulta os códigos existentes e retorna JSON.

Adicionar em `estoque/urls.py`:

```python
path('codigo-barras/gerar/', views.gerar_codigo_barras, name='gerar_codigo_barras'),
```

No template, adicionar input `codigo_barras` com `inputmode="numeric"`, botão `id="generate-barcode"` e texto de ajuda sobre EAN-13. O JavaScript do botão fará `fetch` GET na rota e preencherá o input, sem substituir a validação do servidor.

- [ ] **Step 4: Rodar os testes focados**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.BarcodeProductTests estoque.test_impeccable_remediation --verbosity 1
```

Expected: todos passam.

- [ ] **Step 5: Rodar o audit/polish da superfície alterada**

Usar a skill Impeccable na tela de cadastro e rodar o detector de layout. Corrigir somente problemas introduzidos pelo campo/botão e manter a paleta Cruz Fit já aprovada.

---

### Task 4: Exibir o código e buscar por código na baixa

**Files:**
- Modify: `estoque/templates/estoque.html`
- Modify: `estoque/templates/consulta_produto.html`
- Modify: `estoque/templates/produto.html`
- Modify: `estoque/templates/registrar_baixa.html`
- Modify: `estoque/test_barcode_feature.py`

- [ ] **Step 1: Escrever testes vermelhos de renderização e busca**

Adicionar testes que confirmem o código no estoque, consulta, detalhe e catálogo JavaScript da baixa:

```python
def test_barcode_is_visible_on_product_surfaces(self):
    product = self.create_product(codigo_barras='4006381333931')
    for url in (
        reverse('estoque'),
        reverse('consulta_produto') + '?nome=Produto',
        reverse('produto', kwargs={'slug': product.slug}),
        reverse('registrar_baixa'),
    ):
        response = self.client.get(url)
        self.assertContains(response, '4006381333931')
```

- [ ] **Step 2: Rodar e confirmar RED**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.BarcodeSurfaceTests --verbosity 1
```

Expected: falha porque os templates e o catálogo ainda não renderizam o código.

- [ ] **Step 3: Implementar a exibição e o filtro JavaScript**

Adicionar texto de código nos cards/detalhe somente quando preenchido. No catálogo da baixa, incluir `barcode` em cada item e alterar o filtro:

```javascript
const normalizedTerm = term.replace(/[^0-9a-záàâãéêíóôõúç]/gi, '');
const matches = productCatalog.filter(function (product) {
    return product.name.toLocaleLowerCase('pt-BR').includes(term)
        || (product.barcode || '').includes(normalizedTerm);
});
```

Os resultados devem mostrar nome e código; o POST continuará enviando o ID oculto, sem confiar no código digitado.

- [ ] **Step 4: Rodar testes e fazer QA funcional**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature estoque.test_impeccable_remediation --verbosity 1
```

No navegador, autenticar um usuário de QA, abrir `/estoque/baixa/`, digitar um código válido e confirmar que o produto aparece, sem submeter uma baixa real.

---

### Task 5: Criar modelos de etiqueta configuráveis

**Files:**
- Modify: `estoque/forms.py`
- Modify: `estoque/views.py`
- Modify: `estoque/urls.py`
- Create: `estoque/templates/modelos_etiqueta.html`
- Modify: `templates/base.html`
- Modify: `templates/static/estoque/css/css.css`
- Modify: `estoque/test_barcode_feature.py`

- [ ] **Step 1: Escrever testes vermelhos de configuração**

Cobrir GET autenticado, criação válida e rejeição de largura/altura zero ou negativa:

```python
def test_label_template_can_be_created_with_positive_dimensions(self):
    response = self.client.post(reverse('modelos_etiqueta'), {
        'nome': 'Etiqueta 40x30',
        'largura_mm': '40',
        'altura_mm': '30',
        'margem_horizontal_mm': '0',
        'margem_vertical_mm': '0',
        'espacamento_mm': '0',
        'ativo': 'on',
    })
    self.assertRedirects(response, reverse('modelos_etiqueta'))
    self.assertTrue(ModeloEtiqueta.objects.filter(nome='Etiqueta 40x30').exists())
```

- [ ] **Step 2: Rodar e confirmar RED**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.LabelTemplateTests --verbosity 1
```

Expected: falha porque a view, formulário e template ainda não existem.

- [ ] **Step 3: Implementar formulário e view**

Criar `ModeloEtiquetaForm` com `min_value=0.01` para largura/altura e `min_value=0` para margens/espaçamento. A view `modelos_etiqueta` deve:

- exigir login;
- renderizar os modelos ordenados;
- salvar o formulário válido;
- mostrar erros sem apagar os dados;
- redirecionar para a mesma tela após salvar.

Adicionar rota:

```python
path('etiquetas/modelos/', views.modelos_etiqueta, name='modelos_etiqueta'),
```

Criar template com formulário acessível, campos numéricos em milímetros, listagem dos modelos cadastrados e link para voltar ao estoque. Adicionar link “Etiquetas” à navegação existente sem alterar rotas atuais.

- [ ] **Step 4: Validar layout e testes**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.LabelTemplateTests --verbosity 1
```

Depois rodar Impeccable `audit` e `polish` na nova página, usando os tokens Cruz Fit existentes.

---

### Task 6: Gerar SVG EAN-13 e imprimir etiquetas térmicas

**Files:**
- Modify: `estoque/barcodes.py`
- Modify: `estoque/views.py`
- Modify: `estoque/urls.py`
- Create: `estoque/templates/imprimir_etiquetas.html`
- Modify: `estoque/templates/produto.html`
- Modify: `templates/static/estoque/css/css.css`
- Modify: `estoque/test_barcode_feature.py`

- [ ] **Step 1: Escrever testes vermelhos da impressão**

Cobrir SVG, rota de configuração e quantidade renderizada:

```python
def test_ean13_svg_contains_bar_guard_and_data(self):
    svg = render_ean13_svg('4006381333931')
    self.assertIn('<svg', svg)
    self.assertIn('4006381333931', svg)
    self.assertIn('aria-label="Código de barras 4006381333931"', svg)


def test_print_page_uses_selected_template_dimensions(self):
    product = self.create_product(codigo_barras='4006381333931')
    template = ModeloEtiqueta.objects.create(
        nome='Etiqueta 40x30', largura_mm=40, altura_mm=30,
        margem_horizontal_mm=0, margem_vertical_mm=0, espacamento_mm=0,
    )
    response = self.client.get(
        reverse('imprimir_etiquetas', kwargs={'slug': product.slug}),
        {'modelo': template.pk, 'quantidade': 2},
    )
    self.assertContains(response, '@page')
    self.assertContains(response, 'size: 40mm 30mm')
    self.assertEqual(response.context['etiquetas'], [1, 2])
```

- [ ] **Step 2: Rodar e confirmar RED**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.LabelPrintingTests --verbosity 1
```

Expected: falha porque o renderer SVG, a view e a página de impressão ainda não existem.

- [ ] **Step 3: Implementar o renderer SVG**

Adicionar em `estoque/barcodes.py` as tabelas padrão EAN-13 e a função `render_ean13_svg(value, height=48)`. Ela deve:

- validar o código com `validate_ean13`;
- desenhar as barras laterais/centrais e os dígitos com SVG;
- escapar somente conteúdo fixo/numérico;
- retornar `mark_safe` apenas para o SVG gerado internamente.

O SVG deve ser responsivo em largura, ter `role="img"`, `aria-label` com o número e deixar área branca suficiente para leitura.

- [ ] **Step 4: Implementar view, rota e template térmico**

Adicionar em `estoque/urls.py`:

```python
path('produto/<slug:slug>/etiquetas/', views.imprimir_etiquetas, name='imprimir_etiquetas'),
```

A view deve:

- exigir login;
- retornar 404 para produto inexistente;
- listar modelos ativos;
- aceitar `modelo` e `quantidade` por GET;
- limitar quantidade entre 1 e 500;
- usar o primeiro modelo ativo quando nenhum for escolhido;
- renderizar formulário de configuração quando não houver parâmetros válidos;
- renderizar a lista repetida de etiquetas quando houver parâmetros válidos.

O template deve ter controles fora da área imprimível e uma área `.thermal-label-sheet` com uma `.thermal-label` por etiqueta. A marcação de impressão deve aplicar dimensões numéricas do modelo:

```html
<style>
@page { size: {{ modelo.largura_mm }}mm {{ modelo.altura_mm }}mm; margin: 0; }
.thermal-label { width: {{ modelo.largura_mm }}mm; height: {{ modelo.altura_mm }}mm; }
</style>
```

No detalhe do produto, mostrar o botão “Imprimir etiquetas” somente quando houver código cadastrado.

- [ ] **Step 5: Rodar os testes de impressão**

Run:

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcode_feature.LabelPrintingTests --verbosity 1
```

Expected: todos passam.

- [ ] **Step 6: Fazer QA real no navegador**

Com o servidor na porta `15001`:

1. Abrir detalhe de um produto com código.
2. Clicar em “Imprimir etiquetas”.
3. Selecionar modelo 50 × 30 mm.
4. Informar duas etiquetas.
5. Confirmar que a prévia mostra duas etiquetas e um SVG EAN-13.
6. Capturar screenshot desktop e verificar a visualização de impressão.

---

### Task 7: Verificação final e regressão

**Files:**
- Testes e arquivos alterados nas tarefas anteriores.

- [ ] **Step 1: Rodar a suíte focada**

```powershell
venv\Scripts\python.exe manage.py test estoque.test_barcodes estoque.test_barcode_feature estoque.test_impeccable_remediation --verbosity 1
```

Expected: todos os testes passam.

- [ ] **Step 2: Rodar a suíte completa e checks Django**

```powershell
venv\Scripts\python.exe manage.py test --verbosity 1
venv\Scripts\python.exe manage.py check
venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Expected: suíte verde, `System check identified no issues` e nenhuma migração pendente.

- [ ] **Step 3: Rodar gates de qualidade visual e diff**

```powershell
C:\ERP\.agents\skills\impeccable\scripts\impeccable.cmd detect --json --scope layout estoque\templates\add_produto.html estoque\templates\modelos_etiqueta.html estoque\templates\imprimir_etiquetas.html estoque\templates\produto.html templates\static\estoque\css\css.css
git diff --check
git status --short
```

Interpretar o aviso conhecido do detector sobre `{% static %}` como limitação de resolução de template Django; qualquer warning real de layout deve ser corrigido antes da entrega.

- [ ] **Step 4: QA funcional final no navegador**

Validar sem criar dados permanentes:

- cadastro manual de código;
- geração automática;
- detalhe exibindo código;
- busca por código na baixa sem registrar baixa;
- criação de modelo 40 × 30 mm;
- impressão com modelo selecionado;
- responsividade do cadastro, modelos e impressão.

- [ ] **Step 5: Encerrar sem commit automático**

Reportar arquivos, saída bruta dos testes, evidência visual, migrações aplicadas e qualquer limitação de driver da impressora. Não executar `git commit`, `git push` ou merge sem autorização separada.

---

## Self-review do plano

- O campo manual e a geração automática estão cobertos nas Tasks 1–3.
- Unicidade, validação EAN-13 e compatibilidade com produtos antigos estão cobertas nas Tasks 1–2.
- Exibição no estoque, consulta e detalhe, além da busca da baixa, estão cobertas na Task 4.
- Cadastro de modelos e dimensões em milímetros estão cobertos na Task 5.
- Impressão térmica, SVG, quantidade, `@page` e escolha do modelo estão cobertos na Task 6.
- Suíte completa, check Django, migração, detector e QA no navegador estão cobertos na Task 7.
- Não há dependência externa, câmera, financeiro ou integração proprietária no plano.
- As etapas exigem RED antes da implementação correspondente e não deixam lacunas ou decisões abertas.
