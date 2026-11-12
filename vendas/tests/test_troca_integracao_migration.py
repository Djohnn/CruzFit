"""Given main schema and financial records; When migrating Trocas; Then preserve source and values."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from estoque.models import Categoria, Produto
from vendas.services import registrar_venda


class TrocaMainMigrationTests(TransactionTestCase):
    def test_main_financial_records_survive_exchange_integration(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        main = [("estoque", "0021_produtoconversaoficha"), ("vendas", "0003_liquido_e_restituicoes"),
            ("caixa", "0007_modalidade_cartao"), ("financeiro", "0005_fechamentomensal_fechamentomensalconta_and_more")]
        try:
            executor.migrate(main)
            apps = executor.loader.project_state(main).apps
            manager = get_user_model().objects.create_user(username="mig-main-trocas", cargo="G")
            product = Produto.objects.create(nome="Legado main", categoria=Categoria.objects.create(titulo="Legado main"),
                quantidade="5", preco_compra="10", preco_venda="100")
            sale = registrar_venda(actor=manager, product_ids=[str(product.pk)], quantities=["1"],
                discount="0", payment_method="PIX", idempotency_key="venda-main").sale
            session = apps.get_model("caixa", "SessaoCaixa").objects.create(responsavel_id=manager.pk,
                fundo_inicial="0", motivo="Legado", chave_idempotencia="sessao-main", assinatura="s")
            receipt = apps.get_model("caixa", "RecebimentoVenda").objects.create(venda_id=sale.pk,
                sessao_id=session.pk, valor_total="100", responsavel_id=manager.pk,
                motivo="Legado", chave_idempotencia="rec-main", assinatura="s")
            payment = apps.get_model("caixa", "PagamentoVenda").objects.create(recebimento_id=receipt.pk,
                forma="PIX", valor_aplicado="100", valor_entregue="100", troco="0")
            account = apps.get_model("financeiro", "ContaFinanceira").objects.create(nome="Banco legado", saldo_inicial="0",
                data_corte_financeiro=date(2020,1,1), responsavel_criacao_id=manager.pk, motivo_criacao="Legado",
                chave_idempotencia="conta-main", assinatura="s")
            ledger = apps.get_model("financeiro", "LancamentoFinanceiro").objects.create(conta_id=account.pk,
                direcao="CREDITO", valor="100", data_movimento=date(2026,11,1), descricao="Pix legado",
                motivo="Legado", responsavel_id=manager.pk)
            pix = apps.get_model("financeiro", "RecebimentoPixFinanceiro").objects.create(pagamento_origem_id=payment.pk,
                conta_id=account.pk, lancamento_id=ledger.pk, valor="100", data_confirmacao=date(2026,11,1),
                responsavel_id=manager.pk, motivo="Legado", chave_idempotencia="pix-main", assinatura="s")
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            current = executor.loader.project_state(latest).apps
            saved = current.get_model("financeiro", "RecebimentoPixFinanceiro").objects.get(pk=pix.pk)
            self.assertEqual(saved.pagamento_origem_id, payment.pk)
            self.assertIsNone(saved.pagamento_troca_id)
            self.assertEqual(saved.valor, Decimal("100"))
            product.refresh_from_db()
            self.assertEqual(product.quantidade, Decimal("4"))
            self.assertEqual(current.get_model("financeiro", "LancamentoFinanceiro").objects.get(pk=ledger.pk).valor, Decimal("100"))
        finally:
            MigrationExecutor(connection).migrate(latest)
