from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.template.defaultfilters import slugify

from .barcodes import validate_ean13
from .documentos import validar_documento


class Categoria(models.Model):
    titulo = models.CharField(max_length=40)

    def __str__(self):
        return self.titulo
    
class Produto(models.Model):
    TAMANHO_CHOICES = (
        ('P', 'P'),
        ('M', 'M'),
        ('G', 'G'),
        ('GG', 'GG'),
        ('G1', 'G1'),
        ('G2', 'G2'),
        ('UNICO', 'Único'),
    )
    GENERO_CHOICES = (
        ('MASCULINO', 'Masculino'),
        ('FEMININO', 'Feminino'),
    )

    nome = models.CharField(max_length=40, unique=True)
    categoria = models.ForeignKey(Categoria, on_delete=models.SET_NULL, null=True)
    tamanho = models.CharField(max_length=5, choices=TAMANHO_CHOICES, blank=True, default='')
    modelo = models.CharField(max_length=80, blank=True, default='')
    cor = models.CharField(max_length=40, blank=True, default='')
    tecido = models.CharField(max_length=80, blank=True, default='')
    genero = models.CharField(max_length=10, choices=GENERO_CHOICES, blank=True, default='')
    quantidade = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    estoque_minimo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
        default=None,
        validators=[MinValueValidator(Decimal('0'))],
    )
    preco_compra = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    preco_venda = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    slug = models.SlugField(unique=True, blank=True, null=True)
    codigo_barras = models.CharField(
        max_length=13,
        unique=True,
        blank=True,
        null=True,
    )
    ativo = models.BooleanField(default=True)


    def __str__(self) -> str:
        return self.nome

    def _normalizar_codigo_barras(self):
        if not self.codigo_barras:
            self.codigo_barras = None
            return

        try:
            self.codigo_barras = validate_ean13(self.codigo_barras)
        except ValueError as exc:
            raise ValidationError({'codigo_barras': str(exc)}) from exc

    def _normalizar_precos(self):
        for field_name in ('preco_compra', 'preco_venda'):
            value = getattr(self, field_name, None)
            if isinstance(value, float):
                setattr(self, field_name, Decimal(str(value)))

    def full_clean(
        self,
        exclude=None,
        validate_unique=True,
        validate_constraints=True,
    ):
        codigo_barras_excluido = bool(
            exclude and 'codigo_barras' in exclude
        )
        # Model.full_clean chama clean_fields antes de clean; este estado
        # temporário mantém exclude={'codigo_barras'} válido também em clean.
        self._codigo_barras_excluido_durante_clean = codigo_barras_excluido
        try:
            self._normalizar_precos()
            if not codigo_barras_excluido:
                self._normalizar_codigo_barras()
            return super().full_clean(
                exclude=exclude,
                validate_unique=validate_unique,
                validate_constraints=validate_constraints,
            )
        finally:
            del self._codigo_barras_excluido_durante_clean
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nome)
        self.full_clean()

        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if not getattr(self, '_codigo_barras_excluido_durante_clean', False):
            self._normalizar_codigo_barras()

    
    def gerar_desconto(self, desconto):
        desconto = Decimal(str(desconto))
        return self.preco_venda - ((self.preco_venda * desconto) / 100)
    
    def lucro(self):
        if not self.preco_compra:
            return Decimal('0')
        lucro = self.preco_venda - self.preco_compra
        return (lucro * 100) / self.preco_compra


class ProdutoAlteracao(models.Model):
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='alteracoes',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    dados_anteriores = models.JSONField()
    dados_novos = models.JSONField()

    class Meta:
        ordering = ('-criada_em', '-pk')


class ProdutoCadastro(models.Model):
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='cadastros',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    dados_novos = models.JSONField()

    class Meta:
        ordering = ('-criada_em', '-pk')


class ProdutoInativacao(models.Model):
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        related_name='inativacoes',
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')


class BaixaEstoque(models.Model):
    MOTIVO_CHOICES = (
        ('VENDA', 'Venda'),
        ('AJUSTE', 'Ajuste'),
        ('PERDA', 'Perda'),
    )

    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(
        max_length=12,
        choices=MOTIVO_CHOICES,
        default='VENDA',
    )
    observacao = models.TextField(blank=True, default='')
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='baixas_estoque',
    )
    chave_idempotencia = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        unique=True,
    )

    class Meta:
        ordering = ('-criada_em',)

    def __str__(self) -> str:
        return f'Baixa em {self.criada_em:%d/%m/%Y %H:%M}'


class ItemBaixaEstoque(models.Model):
    baixa = models.ForeignKey(
        BaixaEstoque,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('baixa', 'produto'),
                name='unique_produto_por_baixa',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.produto.nome} - {self.quantidade:g}'
    

class EntradaEstoque(models.Model):
    MOTIVO_CHOICES = (
        ('COMPRA', 'Compra'),
        ('DEVOLUCAO', 'Devolução'),
        ('AJUSTE', 'Ajuste'),
    )

    fornecedor = models.ForeignKey(
        'Fornecedor', blank=True, null=True, on_delete=models.PROTECT,
        related_name='entradas',
    )
    fornecedor_nome = models.CharField(max_length=160, blank=True, default='')
    fornecedor_documento = models.CharField(max_length=14, blank=True, default='')
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=12, choices=MOTIVO_CHOICES)
    observacao = models.TextField(blank=True, default='')
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='entradas_estoque',
    )
    chave_idempotencia = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        unique=True,
    )

    class Meta:
        ordering = ('-criada_em',)

    def __str__(self) -> str:
        return f'Entrada em {self.criada_em:%d/%m/%Y %H:%M}'


class Fornecedor(models.Model):
    nome = models.CharField('Nome / razão social', max_length=160)
    nome_fantasia = models.CharField(max_length=160, blank=True, default='')
    documento = models.CharField('CPF / CNPJ', max_length=14, blank=True,
                                 null=True, unique=True)
    contato = models.CharField('Pessoa de contato', max_length=120, blank=True)
    telefone = models.CharField(max_length=40, blank=True)
    email = models.EmailField('E-mail', blank=True)
    endereco = models.CharField('Endereço', max_length=500, blank=True)
    observacoes = models.TextField('Observações', max_length=2000, blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name='fornecedores_cadastrados',
    )
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('nome', 'pk')
        verbose_name = 'Fornecedor'
        verbose_name_plural = 'Fornecedores'

    def clean(self):
        super().clean()
        self.nome = self.nome.strip()
        if not self.nome:
            raise ValidationError({'nome': 'Informe o nome ou razão social.'})
        try:
            self.documento = validar_documento(self.documento or '')
        except ValidationError as error:
            raise ValidationError({'documento': error.messages}) from error

    def __str__(self) -> str:
        return self.nome


class FornecedorAlteracao(models.Model):
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT,
                                   related_name='alteracoes')
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    on_delete=models.SET_NULL)
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)
    dados_anteriores = models.JSONField()
    dados_novos = models.JSONField()

    class Meta:
        ordering = ('-criada_em', '-pk')


class FornecedorInativacao(models.Model):
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT,
                                   related_name='inativacoes')
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    on_delete=models.SET_NULL)
    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=500)
    chave_idempotencia = models.CharField(max_length=64, unique=True)
    assinatura = models.CharField(max_length=64)

    class Meta:
        ordering = ('-criada_em', '-pk')


class ItemEntradaEstoque(models.Model):
    entrada = models.ForeignKey(
        EntradaEstoque,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('entrada', 'produto'),
                name='unique_produto_por_entrada',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.produto.nome} - {self.quantidade:g}'


class Imagem(models.Model):
    imagem = models.ImageField(upload_to='imagem_produto/')
    produto = models.ForeignKey(Produto, on_delete=models.CASCADE)


class ModeloEtiqueta(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    largura_mm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    altura_mm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    margem_horizontal_mm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    margem_vertical_mm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    espacamento_mm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ('nome',)

    def __str__(self):
        return self.nome
