from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('caixa', '0006_cortecaixa_cortecaixa_caixa_corte_unico_codigo'),
        ('financeiro', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DepositoCartao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data_deposito', models.DateField()),
                ('valor_bruto', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('taxas', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))])),
                ('valor_liquido', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('referencia_operadora', models.CharField(max_length=120)),
                ('motivo', models.CharField(max_length=500)),
                ('chave_idempotencia', models.CharField(max_length=64, unique=True)),
                ('assinatura', models.CharField(max_length=64)),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('conta', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='depositos_cartao', to='financeiro.contafinanceira')),
                ('lancamento', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='deposito_cartao', to='financeiro.lancamentofinanceiro')),
                ('responsavel', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='depositos_cartao_registrados', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-data_deposito', '-criada_em', '-pk')},
        ),
        migrations.CreateModel(
            name='RecebimentoPixFinanceiro',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('data_confirmacao', models.DateField()),
                ('motivo', models.CharField(max_length=500)),
                ('chave_idempotencia', models.CharField(max_length=64, unique=True)),
                ('assinatura', models.CharField(max_length=64)),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('conta', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='recebimentos_pix', to='financeiro.contafinanceira')),
                ('lancamento', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='recebimento_pix', to='financeiro.lancamentofinanceiro')),
                ('pagamento_origem', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='recebimento_pix_financeiro', to='caixa.pagamentovenda')),
                ('responsavel', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='recebimentos_pix_financeiros', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-data_confirmacao', '-criada_em', '-pk')},
        ),
        migrations.CreateModel(
            name='RecebivelCartao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor_bruto', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('data_aprovacao', models.DateField()),
                ('motivo', models.CharField(max_length=500)),
                ('chave_idempotencia', models.CharField(max_length=64, unique=True)),
                ('assinatura', models.CharField(max_length=64)),
                ('cancelado', models.BooleanField(default=False)),
                ('cancelado_em', models.DateField(blank=True, null=True)),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('pagamento_origem', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='recebivel_cartao', to='caixa.pagamentovenda')),
                ('responsavel', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='recebiveis_cartao_registrados', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ('-data_aprovacao', '-criada_em', '-pk')},
        ),
        migrations.CreateModel(
            name='LiquidacaoCartao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor_bruto', models.DecimalField(decimal_places=2, max_digits=14)),
                ('taxas', models.DecimalField(decimal_places=2, max_digits=14)),
                ('valor_liquido', models.DecimalField(decimal_places=2, max_digits=14)),
                ('deposito', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='parcelas', to='financeiro.depositocartao')),
                ('recebivel', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='liquidacao', to='financeiro.recebivelcartao')),
            ],
            options={'ordering': ('pk',)},
        ),
        migrations.CreateModel(
            name='EstornoRecebimentoCartao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('valor', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('data_movimento', models.DateField()),
                ('motivo', models.CharField(max_length=500)),
                ('chave_idempotencia', models.CharField(max_length=64, unique=True)),
                ('assinatura', models.CharField(max_length=64)),
                ('criada_em', models.DateTimeField(auto_now_add=True)),
                ('lancamento', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='estorno_recebimento_cartao', to='financeiro.lancamentofinanceiro')),
                ('pagamento_restituicao', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='estorno_recebimento_cartao', to='caixa.pagamentorestituicao')),
                ('responsavel', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='estornos_recebimento_cartao', to=settings.AUTH_USER_MODEL)),
                ('recebivel', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='estornos_recebimento', to='financeiro.recebivelcartao')),
            ],
            options={'ordering': ('-data_movimento', '-criada_em', '-pk')},
        ),
        migrations.AddConstraint(
            model_name='depositocartao',
            constraint=models.CheckConstraint(check=models.Q(('valor_bruto__gt', 0)), name='financeiro_deposito_cartao_bruto_positivo'),
        ),
        migrations.AddConstraint(
            model_name='depositocartao',
            constraint=models.CheckConstraint(check=models.Q(('taxas__gte', 0)), name='financeiro_deposito_cartao_taxas_nao_neg'),
        ),
        migrations.AddConstraint(
            model_name='depositocartao',
            constraint=models.CheckConstraint(check=models.Q(('valor_liquido__gt', 0)), name='financeiro_deposito_cartao_liquido_positivo'),
        ),
        migrations.AddConstraint(
            model_name='depositocartao',
            constraint=models.CheckConstraint(check=models.Q(('taxas__lte', models.F('valor_bruto'))), name='financeiro_deposito_cartao_taxas_ate_bruto'),
        ),
        migrations.AddConstraint(
            model_name='recebimentopixfinanceiro',
            constraint=models.CheckConstraint(check=models.Q(('valor__gt', 0)), name='financeiro_pix_valor_positivo'),
        ),
        migrations.AddConstraint(
            model_name='recebivelcartao',
            constraint=models.CheckConstraint(check=models.Q(('valor_bruto__gt', 0)), name='financeiro_recebivel_cartao_positivo'),
        ),
        migrations.AddConstraint(
            model_name='liquidacaocartao',
            constraint=models.CheckConstraint(check=models.Q(('valor_bruto__gt', 0)), name='financeiro_liquidacao_cartao_bruto_positivo'),
        ),
        migrations.AddConstraint(
            model_name='liquidacaocartao',
            constraint=models.CheckConstraint(check=models.Q(('taxas__gte', 0)), name='financeiro_liquidacao_cartao_taxas_nao_neg'),
        ),
        migrations.AddConstraint(
            model_name='liquidacaocartao',
            constraint=models.CheckConstraint(check=models.Q(('valor_liquido__gt', 0)), name='financeiro_liquidacao_cartao_liquido_positivo'),
        ),
        migrations.AddConstraint(
            model_name='estornorecebimentocartao',
            constraint=models.CheckConstraint(check=models.Q(('valor__gt', 0)), name='financeiro_estorno_receb_cartao_positivo'),
        ),
    ]
