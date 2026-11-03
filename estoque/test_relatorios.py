"""Cenários Given/When/Then para histórico, relatórios e exportação."""

import csv
from datetime import datetime
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Categoria, Fornecedor, Produto
from .services import registrar_baixa, registrar_entrada


class RelatoriosTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="gerente-relatorios",
            cargo="G",
            first_name="Gerente Relatórios",
        )
        self.client.force_login(self.actor)
        categoria = Categoria.objects.create(titulo="Relatórios")
        self.product = Produto.objects.create(
            nome="Camiseta relatório",
            categoria=categoria,
            quantidade="4.00",
            estoque_minimo="5.00",
            preco_compra="20.00",
            preco_venda="49.90",
        )
        self.product_ok = Produto.objects.create(
            nome="Short relatório",
            categoria=categoria,
            quantidade="10.00",
            estoque_minimo="5.00",
            preco_compra="18.00",
            preco_venda="39.90",
        )
        self.supplier = Fornecedor.objects.create(
            nome="Fornecedor Relatório",
            documento=None,
            responsavel=self.actor,
            chave_idempotencia="fornecedor-relatorio",
            assinatura="assinatura-relatorio",
        )

    def _date(self, year=2026, month=1, day=10):
        return timezone.make_aware(datetime(year, month, day, 12, 0))

    def _entrada(
        self,
        *,
        key=None,
        actor=None,
        product=None,
        reason="AJUSTE",
        supplier=None,
        observation="Ajuste de relatório",
        created_at=None,
    ):
        movement = registrar_entrada(
            actor=actor or self.actor,
            product_ids=[str((product or self.product).pk)],
            quantities=["1.00"],
            reason=reason,
            observation=observation,
            idempotency_key=key or uuid4().hex,
            supplier_id=supplier.pk if supplier else None,
        ).movement
        if created_at:
            type(movement).objects.filter(pk=movement.pk).update(criada_em=created_at)
        return movement

    def _baixa(self, *, key=None, created_at=None, observation="Baixa de relatório"):
        movement = registrar_baixa(
            actor=self.actor,
            product_ids=[str(self.product_ok.pk)],
            quantities=["1.00"],
            reason="VENDA",
            observation=observation,
            idempotency_key=key or uuid4().hex,
        ).movement
        if created_at:
            type(movement).objects.filter(pk=movement.pk).update(criada_em=created_at)
        return movement

    def test_anonymous_history_redirects_to_login(self):
        """Given anônimo; When consultar histórico; Then pedir login."""
        self.client.logout()

        response = self.client.get(reverse("movimentacoes"))

        self.assertRedirects(response, "/auth/login/?next=/estoque/movimentacoes/")

    def test_anonymous_reports_and_csv_redirect_to_login(self):
        """Given anônimo; When consultar relatório/CSV; Then pedir login."""
        self.client.logout()

        self.assertEqual(self.client.get(reverse("relatorios")).status_code, 302)
        self.assertEqual(
            self.client.get(reverse("exportar_relatorio_csv")).status_code,
            302,
        )

    def test_history_filters_and_pagination_preserve_query(self):
        """Given 21 entradas filtradas; When paginar; Then manter os filtros."""
        for index in range(21):
            self._entrada(
                key=f"historico-{index}",
                created_at=self._date(day=1 + index),
            )

        response = self.client.get(
            reverse("movimentacoes"),
            {
                "produto": self.product.pk,
                "responsavel": self.actor.pk,
                "motivo": "AJUSTE",
                "data_inicio": "2026-01-01",
                "data_fim": "2026-01-31",
                "page": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 21)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(response, "produto=")
        self.assertContains(response, "data_inicio=2026-01-01")

    def test_history_preserves_supplier_snapshot_after_responsible_removed(self):
        """Given entrada antiga; When autor removido; Then manter a consulta legível."""
        other = get_user_model().objects.create_user(
            username="responsavel-removido",
            cargo="G",
        )
        movement = self._entrada(
            key="historico-legado",
            actor=other,
            reason="COMPRA",
            supplier=self.supplier,
            created_at=self._date(2025, 12, 20),
        )
        other.delete()

        response = self.client.get(reverse("movimentacoes"))

        self.assertContains(response, "Usuário removido")
        self.assertContains(response, self.supplier.nome)
        self.assertContains(response, f"Entrada #{movement.pk}")

        filtered = self.client.get(
            reverse("movimentacoes"),
            {"fornecedor": self.supplier.pk, "tipo": "entrada"},
        )
        self.assertEqual(filtered.context["page_obj"].paginator.count, 1)

    def test_reports_show_current_balance_and_reorder_filter(self):
        """Given produtos com mínimo; When abrir relatórios; Then calcular saldo/reposição."""
        self.product_ok.ativo = False
        self.product_ok.save(update_fields=("ativo",))

        response = self.client.get(reverse("relatorios"), {"relatorio": "reposicao"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.product.nome)
        self.assertNotContains(response, self.product_ok.nome)
        self.assertContains(response, "Relatório de reposição")

        balance = self.client.get(reverse("relatorios"), {"relatorio": "saldo"})
        self.assertContains(balance, self.product.nome)
        self.assertContains(balance, "4,00")

    def test_movement_report_aggregates_period_by_product_and_direction(self):
        """Given entrada e baixa no período; When filtrar; Then somar cada direção uma vez."""
        self._entrada(key="relatorio-entrada", created_at=self._date(day=5))
        self._baixa(key="relatorio-baixa", created_at=self._date(day=6))

        response = self.client.get(
            reverse("relatorios"),
            {
                "relatorio": "movimentacoes",
                "data_inicio": "2026-01-01",
                "data_fim": "2026-01-31",
            },
        )

        self.assertEqual(response.status_code, 200)
        rows = response.context["movement_report"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["quantidade"], "1,00")
        self.assertContains(response, "Entradas")
        self.assertContains(response, "Saídas")

    def test_product_filter_projects_only_matching_items_across_history_report_and_csv(self):
        """Given movimentos com dois produtos; When filtrar; Then projetar apenas o produto alvo."""
        entrada = registrar_entrada(
            actor=self.actor,
            product_ids=[str(self.product.pk), str(self.product_ok.pk)],
            quantities=["1.00", "1.00"],
            reason="AJUSTE",
            observation="Entrada com dois produtos",
            idempotency_key="filtro-produto-entrada",
        ).movement
        baixa = registrar_baixa(
            actor=self.actor,
            product_ids=[str(self.product.pk), str(self.product_ok.pk)],
            quantities=["1.00", "1.00"],
            reason="VENDA",
            observation="Baixa com dois produtos",
            idempotency_key="filtro-produto-baixa",
        ).movement

        filtered_history = self.client.get(
            reverse("movimentacoes"),
            {"produto": self.product.pk},
        )
        history_rows = list(filtered_history.context["movements"])
        self.assertEqual(
            {row["id"] for row in history_rows},
            {entrada.pk, baixa.pk},
        )
        self.assertEqual(
            {
                item["produto_id"]
                for row in history_rows
                for item in row["itens"]
            },
            {self.product.pk},
        )

        filtered_report = self.client.get(
            reverse("relatorios"),
            {"relatorio": "movimentacoes", "produto": self.product.pk},
        )
        report_rows = filtered_report.context["movement_report"]
        self.assertEqual([row["produto_id"] for row in report_rows], [self.product.pk])
        self.assertEqual(report_rows[0]["entradas"], "1,00")
        self.assertEqual(report_rows[0]["saidas"], "1,00")

        filtered_csv = self.client.get(
            reverse("exportar_relatorio_csv"),
            {"relatorio": "movimentacoes", "produto": self.product.pk},
        )
        filtered_csv_text = filtered_csv.content.decode("utf-8-sig")
        self.assertIn(self.product.nome, filtered_csv_text)
        self.assertNotIn(self.product_ok.nome, filtered_csv_text)

        all_history = self.client.get(reverse("movimentacoes"))
        all_history_rows = list(all_history.context["movements"])
        self.assertTrue(any(len(row["itens"]) == 2 for row in all_history_rows))

        all_report = self.client.get(
            reverse("relatorios"),
            {"relatorio": "movimentacoes"},
        )
        self.assertEqual(
            {row["produto_id"] for row in all_report.context["movement_report"]},
            {self.product.pk, self.product_ok.pk},
        )

        all_csv = self.client.get(
            reverse("exportar_relatorio_csv"),
            {"relatorio": "movimentacoes"},
        )
        all_csv_text = all_csv.content.decode("utf-8-sig")
        self.assertIn(self.product.nome, all_csv_text)
        self.assertIn(self.product_ok.nome, all_csv_text)

    def test_history_csv_reuses_filters_and_sanitizes_formula_text(self):
        """Given observação com fórmula; When exportar; Then filtrar e neutralizar fórmula."""
        self._entrada(
            key="csv-formula",
            observation="=SOMA(A1:A2)",
            created_at=self._date(day=7),
        )
        self._baixa(key="csv-fora-do-filtro", created_at=self._date(day=8))

        response = self.client.get(
            reverse("exportar_relatorio_csv"),
            {
                "relatorio": "movimentacoes",
                "produto": self.product.pk,
                "motivo": "AJUSTE",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows[0][0], "Referência")
        self.assertEqual(len(rows), 2)
        self.assertIn("'=SOMA(A1:A2)", rows[1])
        self.assertNotIn("Baixa", rows[1])

    def test_balance_csv_has_spreadsheet_headers(self):
        """Given saldo atual; When exportar CSV; Then entregar cabeçalhos estáveis."""
        response = self.client.get(
            reverse("exportar_relatorio_csv"),
            {"relatorio": "saldo"},
        )

        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(
            rows[0],
            ["Produto", "Código de barras", "Saldo", "Estoque mínimo", "Situação"],
        )
        self.assertEqual(len(rows), 3)
