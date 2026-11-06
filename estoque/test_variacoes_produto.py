"""Regressões P0 para fichas e variações de produto."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from compras.services import confirmar_pedido, criar_rascunho, receber_pedido
from vendas.services import registrar_venda

from .barcodes import generate_ean13, validate_ean13
from .forms import VariacaoProdutoForm
from .models import Categoria, FichaProduto, Fornecedor, Produto
from .services import (
    ProductOperationError,
    adicionar_variacoes_ficha,
    cadastrar_ficha_variacoes,
)


class VariacoesProdutoTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='gerente-variacoes', password='senha', cargo='G',
        )
        self.category = Categoria.objects.create(titulo='Camisetas')
        self.codes = []

    def code(self):
        value = generate_ean13(self.codes)
        self.codes.append(value)
        return value

    def common(self):
        return {
            'nome': 'Camiseta Dry Fit',
            'categoria': self.category,
            'modelo': 'Dry Fit',
            'tecido': 'Poliamida',
            'genero': '',
        }

    def line(self, color, size, quantity, cost='20.00', price='49.90', code=None):
        return {
            'cor': color,
            'tamanho': size,
            'quantidade': quantity,
            'preco_compra': cost,
            'preco_venda': price,
            'estoque_minimo': '',
            'codigo_barras': code or self.code(),
        }

    def create_catalog(self):
        return cadastrar_ficha_variacoes(
            actor=self.manager,
            dados=self.common(),
            variacoes=[
                self.line('Preta', 'M', '8.00'),
                self.line('Preta', 'G', '5.00'),
                self.line('Branca', 'M', '3.00'),
            ],
            reason='Cadastrar coleção Dry Fit',
            idempotency_key='ficha-dry-fit-1',
        ).ficha

    def test_cadastro_cria_ficha_linhas_e_total_informativo(self):
        """Given três linhas; When cadastrar; Then totalizar 16 sem estoque na ficha."""
        ficha = self.create_catalog()

        self.assertEqual(ficha.nome, 'Camiseta Dry Fit')
        self.assertEqual(ficha.variacoes.count(), 3)
        self.assertEqual(
            sum(ficha.variacoes.values_list('quantidade', flat=True)),
            Decimal('16.00'),
        )
        self.assertEqual(ficha.variacoes.first().ficha_id, ficha.pk)
        self.assertEqual(
            len(set(ficha.variacoes.values_list('slug', flat=True))),
            3,
        )
        self.assertTrue(all(ficha.variacoes.values_list('slug', flat=True)))
        self.assertFalse(hasattr(ficha, 'quantidade'))

    def test_codigo_em_branco_gera_ean_unico_nos_dois_fluxos(self):
        manual = self.code()
        linhas = [
            {**self.line('Preta', 'M', '1'), 'codigo_barras': ''},
            {**self.line('Branca', 'M', '1'), 'codigo_barras': ''},
            self.line('Azul', 'G', '1', code=manual),
        ]
        dados = dict(actor=self.manager, dados=self.common(), variacoes=linhas,
                     reason='Cadastrar ficha', idempotency_key='ficha-ean')
        ficha = cadastrar_ficha_variacoes(**dados).ficha
        self.assertFalse(cadastrar_ficha_variacoes(**dados).created)
        form = VariacaoProdutoForm({**self.line('Verde', 'G', '1'), 'codigo_barras': ''})
        self.assertTrue(form.is_valid(), form.errors)
        adicionar_variacoes_ficha(
            actor=self.manager, ficha_id=ficha.pk, variacoes=[form.cleaned_data],
            reason='Adicionar verde', idempotency_key='adicionar-ean',
        )
        codigos = list(ficha.variacoes.values_list('codigo_barras', flat=True))
        self.assertEqual(len(set(codigos)), 4)
        self.assertEqual(ficha.variacoes.get(cor='Azul').codigo_barras, manual)
        self.assertTrue(all(validate_ean13(codigo) for codigo in codigos))

    def test_venda_baixa_somente_a_variacao_selecionada(self):
        """Given Preta M=8; When vender uma; Then somente ela fica em 7."""
        ficha = self.create_catalog()
        preta_m = ficha.variacoes.get(cor='Preta', tamanho='M')
        preta_g = ficha.variacoes.get(cor='Preta', tamanho='G')

        result = registrar_venda(
            actor=self.manager,
            product_ids=[str(preta_m.pk)],
            quantities=['1.00'],
            discount='0.00',
            payment_method='DINHEIRO',
            idempotency_key='venda-preta-m-1',
        )

        self.assertTrue(result.created)
        preta_m.refresh_from_db()
        preta_g.refresh_from_db()
        self.assertEqual(preta_m.quantidade, Decimal('7.00'))
        self.assertEqual(preta_g.quantidade, Decimal('5.00'))
        self.assertEqual(result.sale.itens.get().produto_id, preta_m.pk)

    def test_recebimento_de_compra_aumenta_somente_a_variacao(self):
        """Given Preta G=5; When receber 2; Then somente ela fica em 7."""
        ficha = self.create_catalog()
        preta_g = ficha.variacoes.get(cor='Preta', tamanho='G')
        supplier = Fornecedor.objects.create(
            nome='Fornecedor Dry Fit', responsavel=self.manager,
            chave_idempotencia='fornecedor-dry-fit', assinatura='assinatura',
        )
        draft = criar_rascunho(
            actor=self.manager,
            fornecedor_id=supplier.pk,
            lines=[{'produto_id': preta_g.pk, 'quantidade': '2.00', 'custo_unitario': '21.00'}],
            observation='', reason='Criar compra da Preta G',
            idempotency_key='pedido-preta-g',
        ).pedido
        pedido = confirmar_pedido(
            actor=self.manager, pedido_id=draft.pk,
            reason='Confirmar compra da Preta G', idempotency_key='pedido-preta-g-ok',
        ).pedido

        receber_pedido(
            actor=self.manager, pedido_id=pedido.pk,
            lines=[{'item_id': pedido.itens.get().pk, 'quantidade': '2.00'}],
            referencia_documental='NF-D-FIT', reason='Receber Preta G',
            idempotency_key='recebimento-preta-g',
        )

        preta_g.refresh_from_db()
        self.assertEqual(preta_g.quantidade, Decimal('7.00'))
        self.assertEqual(ficha.variacoes.get(cor='Preta', tamanho='M').quantidade, Decimal('8.00'))

    def test_inclusao_posterior_preserva_linhas_e_adiciona_azul_p(self):
        """Given ficha existente; When incluir Azul P; Then manter as linhas anteriores."""
        ficha = self.create_catalog()
        result = adicionar_variacoes_ficha(
            actor=self.manager, ficha_id=ficha.pk,
            variacoes=[self.line('Azul', 'P', '0.00')],
            reason='Adicionar Azul P', idempotency_key='ficha-dry-fit-azul-p',
        )

        self.assertTrue(result.created)
        self.assertEqual(ficha.variacoes.count(), 4)
        self.assertEqual(ficha.variacoes.get(cor='Azul', tamanho='P').quantidade, Decimal('0.00'))
        self.assertEqual(ficha.variacoes.get(cor='Preta', tamanho='M').quantidade, Decimal('8.00'))

    def test_rejeita_combinacao_normalizada_e_codigo_repetido(self):
        """Given combinação/código usados; When repetir normalizado; Then rejeitar tudo."""
        ficha = self.create_catalog()
        duplicate = self.line('  preta  ', 'm', '2.00')
        with self.assertRaises(ProductOperationError):
            adicionar_variacoes_ficha(
                actor=self.manager, ficha_id=ficha.pk,
                variacoes=[duplicate], reason='Duplicata de combinação',
                idempotency_key='duplicata-combinacao',
            )
        self.assertEqual(ficha.variacoes.count(), 3)

        same_code = self.line('Azul', 'P', '2.00', code=ficha.variacoes.first().codigo_barras)
        with self.assertRaises(ProductOperationError):
            adicionar_variacoes_ficha(
                actor=self.manager, ficha_id=ficha.pk,
                variacoes=[same_code], reason='Duplicata de código',
                idempotency_key='duplicata-codigo',
            )
        self.assertEqual(ficha.variacoes.count(), 3)

    def test_falha_em_linha_nao_grava_ficha_parcial(self):
        """Given uma linha inválida; When cadastrar; Then não persistir ficha nem produto."""
        with self.assertRaises(ProductOperationError):
            cadastrar_ficha_variacoes(
                actor=self.manager, dados=self.common(),
                variacoes=[self.line('Preta', 'M', '8.00'), self.line('', 'G', '5.00')],
                reason='Cadastro inválido', idempotency_key='ficha-invalida',
            )
        self.assertEqual(FichaProduto.objects.count(), 0)
        self.assertEqual(Produto.objects.count(), 0)

    def test_replay_identico_e_conflito_de_chave(self):
        """Given operação repetida; When replayar; Then uma ficha, conflito rejeitado."""
        first = self.create_catalog()
        replay = cadastrar_ficha_variacoes(
            actor=self.manager, dados=self.common(),
            variacoes=[
                self.line('Preta', 'M', '8.00', code=first.variacoes.get(cor='Preta', tamanho='M').codigo_barras),
                self.line('Preta', 'G', '5.00', code=first.variacoes.get(cor='Preta', tamanho='G').codigo_barras),
                self.line('Branca', 'M', '3.00', code=first.variacoes.get(cor='Branca', tamanho='M').codigo_barras),
            ],
            reason='Cadastrar coleção Dry Fit', idempotency_key='ficha-dry-fit-1',
        )
        self.assertFalse(replay.created)
        self.assertEqual(FichaProduto.objects.count(), 1)
        with self.assertRaises(ProductOperationError):
            cadastrar_ficha_variacoes(
                actor=self.manager, dados=self.common(),
                variacoes=[self.line('Azul', 'P', '1.00')],
                reason='Outro conteúdo', idempotency_key='ficha-dry-fit-1',
            )

    def test_produto_simples_herdado_preserva_nome_slug_codigo_e_saldo(self):
        """Given produto simples legado; When consultar schema novo; Then preservar identidade."""
        product = Produto.objects.create(
            nome='Produto legado', categoria=self.category, quantidade='4.00',
            preco_compra='10.00', preco_venda='20.00', codigo_barras=self.code(),
        )
        original = (product.pk, product.slug, product.codigo_barras, product.quantidade)
        product.refresh_from_db()
        self.assertEqual((product.pk, product.slug, product.codigo_barras, product.quantidade), original)
        self.assertIsNone(product.ficha_id)
