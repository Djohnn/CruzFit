from django.db import migrations, models


def marcar_pagamentos_estornados_historicos(apps, schema_editor):
    PagamentoContaPagar = apps.get_model('financeiro', 'PagamentoContaPagar')
    EstornoPagamentoContaPagar = apps.get_model(
        'financeiro',
        'EstornoPagamentoContaPagar',
    )
    estornados = EstornoPagamentoContaPagar.objects.values_list(
        'pagamento_id',
        flat=True,
    )
    PagamentoContaPagar.objects.filter(pk__in=estornados).update(vigente=False)


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0003_contapagar_pagamentocontapagar_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pagamentocontapagar',
            name='vigente',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='pagamentocontapagar',
            name='conta_pagar',
            field=models.ForeignKey(
                on_delete=models.PROTECT,
                related_name='pagamentos',
                to='financeiro.contapagar',
            ),
        ),
        migrations.RunPython(
            marcar_pagamentos_estornados_historicos,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='pagamentocontapagar',
            constraint=models.UniqueConstraint(
                condition=models.Q(vigente=True),
                fields=('conta_pagar',),
                name='financeiro_pagamento_conta_pagar_vigente_unico',
            ),
        ),
    ]
