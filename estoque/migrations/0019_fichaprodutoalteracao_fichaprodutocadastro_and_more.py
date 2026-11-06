import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('estoque', '0018_remove_itemvendadevolucao_item_venda_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FichaProdutoAlteracao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('motivo', models.CharField(max_length=500)),
                ('chave_idempotencia', models.CharField(max_length=64, unique=True)),
                ('assinatura', models.CharField(max_length=64)),
                ('dados_novos', models.JSONField()),
            ],
            options={
                'ordering': ('-criada_em', '-pk'),
            },
        ),
        migrations.CreateModel(
            name='FichaProdutoCadastro',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('motivo', models.CharField(max_length=500)),
                ('chave_idempotencia', models.CharField(max_length=64, unique=True)),
                ('assinatura', models.CharField(max_length=64)),
                ('dados_novos', models.JSONField()),
            ],
            options={
                'ordering': ('-criada_em', '-pk'),
            },
        ),
        migrations.AddField(
            model_name='produto',
            name='cor_normalizada',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='produto',
            name='tamanho_normalizado',
            field=models.CharField(blank=True, default='', max_length=5),
        ),
        migrations.AlterField(
            model_name='produto',
            name='nome',
            field=models.CharField(max_length=40),
        ),
        migrations.CreateModel(
            name='FichaProduto',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=40)),
                ('modelo', models.CharField(blank=True, default='', max_length=80)),
                ('tecido', models.CharField(blank=True, default='', max_length=80)),
                ('genero', models.CharField(blank=True, default='', max_length=10)),
                ('slug', models.SlugField(blank=True, null=True, unique=True)),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('atualizada_em', models.DateTimeField(auto_now=True)),
                ('categoria', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='estoque.categoria')),
            ],
            options={
                'ordering': ('nome', 'pk'),
            },
        ),
        migrations.AddField(
            model_name='produto',
            name='ficha',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='variacoes', to='estoque.fichaproduto'),
        ),
        migrations.AddConstraint(
            model_name='produto',
            constraint=models.UniqueConstraint(condition=models.Q(('ficha__isnull', True)), fields=('nome',), name='unique_nome_produto_simples'),
        ),
        migrations.AddConstraint(
            model_name='produto',
            constraint=models.UniqueConstraint(condition=models.Q(('ficha__isnull', False)), fields=('ficha', 'cor_normalizada', 'tamanho_normalizado'), name='unique_ficha_cor_tamanho_normalizados'),
        ),
        migrations.AddField(
            model_name='fichaprodutoalteracao',
            name='ficha',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='alteracoes', to='estoque.fichaproduto'),
        ),
        migrations.AddField(
            model_name='fichaprodutoalteracao',
            name='responsavel',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='fichaprodutocadastro',
            name='ficha',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cadastros', to='estoque.fichaproduto'),
        ),
        migrations.AddField(
            model_name='fichaprodutocadastro',
            name='responsavel',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
    ]
