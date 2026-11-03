from django.db import models
from django.template.defaultfilters import slugify

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
    quantidade = models.FloatField()
    preco_compra = models.FloatField()
    preco_venda = models.FloatField()
    slug = models.SlugField(unique=True, blank=True, null=True)


    def __str__(self) -> str:
        return self.nome
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nome)

        return super().save(*args, **kwargs)

    
    def gerar_desconto(self, desconto):
        return self.preco_venda - ((self.preco_venda * desconto) / 100)
    
    def lucro(self):
        lucro = self.preco_venda - self.preco_compra
        return (lucro * 100) / self.preco_compra


class BaixaEstoque(models.Model):
    criada_em = models.DateTimeField(auto_now_add=True)

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
    quantidade = models.FloatField()

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

    criada_em = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=12, choices=MOTIVO_CHOICES)
    observacao = models.TextField(blank=True, default='')

    class Meta:
        ordering = ('-criada_em',)

    def __str__(self) -> str:
        return f'Entrada em {self.criada_em:%d/%m/%Y %H:%M}'


class ItemEntradaEstoque(models.Model):
    entrada = models.ForeignKey(
        EntradaEstoque,
        on_delete=models.CASCADE,
        related_name='itens',
    )
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade = models.FloatField()

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
