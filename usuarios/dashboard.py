"""Consultas do painel diário, sem gravação ou cache."""

from decimal import Decimal

from django.db.models import F, Q, Sum
from django.utils import timezone

from caixa.models import PagamentoVenda, SessaoCaixa
from estoque.models import Produto
from vendas.models import Venda, VendaDevolucao

ZERO = Decimal("0.00")


def painel_do_dia():
    hoje = timezone.localdate()
    vendas = Venda.objects.filter(
        criada_em__date=hoje, cancelamento__isnull=True,
    )
    quantidade = vendas.count()
    total = vendas.aggregate(valor=Sum("total"))["valor"] or ZERO
    devolvido = VendaDevolucao.objects.filter(
        venda__in=vendas,
    ).aggregate(valor=Sum("valor_total"))["valor"] or ZERO
    liquido = total - devolvido

    recebimentos = dict(
        PagamentoVenda.objects.filter(recebimento__criada_em__date=hoje)
        .values("forma").annotate(valor=Sum("valor_aplicado"))
        .values_list("forma", "valor")
    )
    maior = max(recebimentos.values(), default=ZERO)
    formas = [
        {
            "nome": nome,
            "valor": recebimentos.get(codigo, ZERO),
            "largura": int(recebimentos.get(codigo, ZERO) / maior * 100)
            if maior else 0,
        }
        for codigo, nome in PagamentoVenda.FORMA_PAGAMENTO_CHOICES
    ]
    alertas = Produto.objects.filter(ativo=True).filter(
        Q(quantidade__lte=0)
        | Q(estoque_minimo__isnull=False, quantidade__lte=F("estoque_minimo")),
    ).order_by("quantidade", "nome", "pk")
    return {
        "hoje": hoje,
        "total_vendas": liquido,
        "quantidade_vendas": quantidade,
        "ticket_medio": (liquido / quantidade).quantize(Decimal("0.01"))
        if quantidade else ZERO,
        "sessao": SessaoCaixa.objects.filter(aberta=True).first(),
        "formas": formas,
        "ultimas_vendas": vendas.order_by("-criada_em", "-pk")[:5],
        "alertas": alertas[:4],
        "quantidade_alertas": alertas.count(),
    }
