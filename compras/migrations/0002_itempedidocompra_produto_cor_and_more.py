from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compras', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='itempedidocompra',
            name='produto_cor',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='itempedidocompra',
            name='produto_tamanho',
            field=models.CharField(blank=True, default='', max_length=5),
        ),
    ]
