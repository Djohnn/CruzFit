from decimal import Decimal

import django.core.validators
from django.db import migrations, models


MODELO_ETIQUETA_PADRAO = 'Etiqueta térmica 50x30 mm'


def criar_modelo_etiqueta_padrao(apps, schema_editor):
    modelo_etiqueta = apps.get_model('estoque', 'ModeloEtiqueta')
    modelo_etiqueta.objects.using(schema_editor.connection.alias).get_or_create(
        nome=MODELO_ETIQUETA_PADRAO,
        defaults={
            'largura_mm': Decimal('50'),
            'altura_mm': Decimal('30'),
            'margem_horizontal_mm': Decimal('0'),
            'margem_vertical_mm': Decimal('0'),
            'espacamento_mm': Decimal('0'),
            'ativo': True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0009_entradaestoque_itementradaestoque_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='produto',
            name='codigo_barras',
            field=models.CharField(
                blank=True,
                max_length=13,
                null=True,
                unique=True,
            ),
        ),
        migrations.CreateModel(
            name='ModeloEtiqueta',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('nome', models.CharField(max_length=100, unique=True)),
                (
                    'largura_mm',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(
                                Decimal('0.01')
                            )
                        ],
                    ),
                ),
                (
                    'altura_mm',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(
                                Decimal('0.01')
                            )
                        ],
                    ),
                ),
                (
                    'margem_horizontal_mm',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal('0'))
                        ],
                    ),
                ),
                (
                    'margem_vertical_mm',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal('0'))
                        ],
                    ),
                ),
                (
                    'espacamento_mm',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=6,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal('0'))
                        ],
                    ),
                ),
                ('ativo', models.BooleanField(default=True)),
            ],
            options={'ordering': ('nome',)},
        ),
        migrations.RunPython(
            criar_modelo_etiqueta_padrao,
            # O nome não identifica com segurança um registro criado pela
            # migration; o reverse não deve apagar dados pré-existentes ou editados.
            reverse_code=migrations.RunPython.noop,
        ),
    ]
