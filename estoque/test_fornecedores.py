"""Cenários Given/When/Then de fornecedores e integração com estoque."""

from importlib import import_module
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from . import models
from .services import StockOperationError, registrar_entrada


class FornecedorTests(TestCase):
    def setUp(self):
        self.assertTrue(
            hasattr(models, "Fornecedor"),
            "O cadastro de fornecedores ainda não existe.",
        )
        self.api = import_module("estoque.services")
        self.actor = get_user_model().objects.create_user(
            username="gerente-fornecedores",
            cargo="G",
            first_name="Gerente",
        )
        self.client.force_login(self.actor)
        self.dados = {
            "nome": "Confecções Horizonte",
            "nome_fantasia": "Horizonte",
            "documento": "11.222.333/0001-81",
            "contato": "Ana",
            "telefone": "(11) 99999-0000",
            "email": "contato@example.com",
            "endereco": "Rua das Flores, 100",
            "observacoes": "Entrega semanal",
        }
        categoria = models.Categoria.objects.create(titulo="Camisetas")
        self.produto = models.Produto.objects.create(
            nome="Camiseta fornecedor",
            categoria=categoria,
            quantidade=10,
            preco_compra=20,
            preco_venda=40,
        )

    def cadastrar(self, **overrides):
        return self.api.cadastrar_fornecedor(
            actor=self.actor,
            dados={**self.dados, **overrides},
            idempotency_key=uuid4().hex,
        )

    def entrada(self, fornecedor=None, **overrides):
        return registrar_entrada(
            **{
                "actor": self.actor,
                "product_ids": [str(self.produto.pk)],
                "quantities": ["2"],
                "reason": "COMPRA",
                "observation": "",
                "idempotency_key": uuid4().hex,
                "supplier_id": fornecedor.pk if fornecedor else None,
                **overrides,
            }
        )

    def test_cadastro_normaliza_documento_e_audita(self):
        """Given gerente; When cadastrar; Then normalizar e registrar autoria."""
        fornecedor = self.cadastrar(nome="  Confecções Horizonte  ")
        self.assertEqual(fornecedor.nome, "Confecções Horizonte")
        self.assertEqual(fornecedor.documento, "11222333000181")
        self.assertEqual(fornecedor.responsavel, self.actor)
        self.assertTrue(fornecedor.ativo)
        self.assertIsNotNone(fornecedor.criado_em)

    def test_documento_opcional_e_cnpj_alfanumerico(self):
        """Given documento vazio/alfanumérico; When cadastrar; Then aceitar ambos."""
        self.cadastrar(documento="")
        self.cadastrar(nome="Sem documento 2", documento="")
        fornecedor = self.cadastrar(nome="Alfa", documento="12.ABC.345/01DE-35")
        self.assertEqual(fornecedor.documento, "12ABC34501DE35")

    def test_validacao_e_duplicidade(self):
        """Given dados inválidos; When cadastrar; Then rejeitar sem persistir."""
        for dados in (
            {"nome": "  "},
            {"documento": "11111111111"},
            {"documento": "11222333000180"},
            {"email": "inválido"},
        ):
            with self.subTest(dados=dados), self.assertRaises(ValidationError):
                self.cadastrar(**dados)
        self.assertEqual(models.Fornecedor.objects.count(), 0)
        self.cadastrar()
        with self.assertRaises(ValidationError):
            self.cadastrar(nome="Outro nome", documento="11222333000181")

    def test_cadastro_repetido_e_conflito(self):
        """Given chave usada; When repetir/conflitar; Then retornar/rejeitar."""
        fornecedor = self.api.cadastrar_fornecedor(
            actor=self.actor,
            dados=self.dados,
            idempotency_key="cadastro-1",
        )
        repetido = self.api.cadastrar_fornecedor(
            actor=self.actor,
            dados=self.dados,
            idempotency_key="cadastro-1",
        )
        self.assertEqual(fornecedor.pk, repetido.pk)
        with self.assertRaises(ValidationError):
            self.api.cadastrar_fornecedor(
                actor=self.actor,
                dados={**self.dados, "nome": "Outro"},
                idempotency_key="cadastro-1",
            )
        self.assertEqual(models.Fornecedor.objects.count(), 1)

    def test_edicao_e_inativacao_auditadas_idempotentes(self):
        """Given fornecedor; When editar/inativar duas vezes; Then uma auditoria."""
        fornecedor = self.cadastrar()
        dados = {**self.dados, "nome": "Novo nome"}
        for _ in range(2):
            self.api.editar_fornecedor(
                actor=self.actor,
                fornecedor_id=fornecedor.pk,
                dados=dados,
                reason="Correção de cadastro",
                idempotency_key="editar-1",
            )
            self.api.inativar_fornecedor(
                actor=self.actor,
                fornecedor_id=fornecedor.pk,
                reason="Não fornece mais",
                idempotency_key="inativar-1",
            )
        fornecedor.refresh_from_db()
        self.assertFalse(fornecedor.ativo)
        alteracao = models.FornecedorAlteracao.objects.get()
        self.assertEqual(alteracao.dados_anteriores["nome"], self.dados["nome"])
        self.assertEqual(alteracao.dados_novos["nome"], "Novo nome")
        self.assertEqual(alteracao.responsavel, self.actor)
        self.assertEqual(models.FornecedorInativacao.objects.count(), 1)

    def test_servico_exige_gerente_chave_e_motivo(self):
        """Given usuário sem cargo/chave/motivo; When escrever; Then rejeitar."""
        vendedor = get_user_model().objects.create_user(username="vendedor", cargo="V")
        for actor, key in ((vendedor, "chave"), (None, "chave"), (self.actor, "")):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.api.cadastrar_fornecedor(
                    actor=actor,
                    dados=self.dados,
                    idempotency_key=key,
                )
        fornecedor = self.cadastrar()
        with self.assertRaises(ValidationError):
            self.api.inativar_fornecedor(
                actor=self.actor,
                fornecedor_id=fornecedor.pk,
                reason=" ",
                idempotency_key="sem-motivo",
            )

    def test_compra_exige_fornecedor_ativo_sem_alterar_saldo(self):
        """Given compra sem fornecedor/fornecedor inativo; When entrada; Then falhar."""
        with self.assertRaises(StockOperationError):
            self.entrada()
        for value in ("0", "-1", "invalido", "999999999999999999999999"):
            with self.subTest(value=value), self.assertRaises(StockOperationError):
                self.entrada(supplier_id=value)
        fornecedor = self.cadastrar()
        self.api.inativar_fornecedor(
            actor=self.actor,
            fornecedor_id=fornecedor.pk,
            reason="Encerrado",
            idempotency_key="inativar",
        )
        with self.assertRaises(StockOperationError):
            self.entrada(fornecedor)
        self.produto.refresh_from_db()
        self.assertEqual(self.produto.quantidade, 10)
        self.assertFalse(models.EntradaEstoque.objects.exists())

    def test_snapshot_historico_e_protecao_do_fornecedor(self):
        """Given compra concluída; When editar cadastro; Then preservar origem."""
        fornecedor = self.cadastrar()
        entrada = self.entrada(fornecedor).movement
        self.api.editar_fornecedor(
            actor=self.actor,
            fornecedor_id=fornecedor.pk,
            dados={**self.dados, "nome": "Nome corrigido", "documento": ""},
            reason="Correção",
            idempotency_key="editar",
        )
        entrada.refresh_from_db()
        self.assertEqual(entrada.fornecedor_nome, self.dados["nome"])
        self.assertEqual(entrada.fornecedor_documento, "11222333000181")
        with self.assertRaises(ProtectedError):
            fornecedor.delete()

    def test_replay_de_compra_apos_inativacao_e_conflito(self):
        """Given compra e posterior inativação; When replay; Then não duplicar."""
        fornecedor = self.cadastrar()
        original = self.entrada(fornecedor, idempotency_key="compra")
        self.api.inativar_fornecedor(
            actor=self.actor,
            fornecedor_id=fornecedor.pk,
            reason="Fim",
            idempotency_key="fim",
        )
        replay = self.entrada(fornecedor, idempotency_key="compra")
        self.assertFalse(replay.created)
        self.assertEqual(replay.movement.pk, original.movement.pk)
        with self.assertRaises(StockOperationError):
            self.entrada(fornecedor, quantities=["3"], idempotency_key="compra")
        self.produto.refresh_from_db()
        self.assertEqual(self.produto.quantidade, 12)

    def test_ajuste_sem_fornecedor_e_legado_permanecem_validos(self):
        """Given entrada antiga/ajuste; When consultar/criar; Then aceitar ausência."""
        legado = models.EntradaEstoque.objects.create(motivo="COMPRA")
        self.assertIsNone(legado.fornecedor_id)
        self.assertEqual(legado.fornecedor_nome, "")
        ajuste = self.entrada(reason="AJUSTE").movement
        self.assertIsNone(ajuste.fornecedor_id)

    def test_rotas_exigem_login_e_gerente_para_escrita(self):
        """Given anônimo/vendedor; When acessar cadastro; Then redirecionar/negar."""
        self.client.logout()
        self.assertEqual(self.client.get(reverse("fornecedores")).status_code, 302)
        vendedor = get_user_model().objects.create_user(username="leitura", cargo="V")
        self.client.force_login(vendedor)
        self.assertEqual(self.client.get(reverse("fornecedor_novo")).status_code, 403)
        self.assertEqual(self.client.get(reverse("fornecedores")).status_code, 200)

    def test_telas_cadastro_edicao_pesquisa_e_historico(self):
        """Given gerente; When cadastrar e comprar; Then consultar origem e itens."""
        response = self.client.post(
            reverse("fornecedor_novo"),
            {
                **self.dados,
                "chave_idempotencia": "cadastro-http",
            },
        )
        fornecedor = models.Fornecedor.objects.get()
        self.assertRedirects(
            response, reverse("fornecedor_detalhe", args=[fornecedor.pk])
        )
        self.entrada(fornecedor)
        detalhe = self.client.get(reverse("fornecedor_detalhe", args=[fornecedor.pk]))
        self.assertContains(detalhe, self.produto.nome)
        self.assertContains(detalhe, "Confecções Horizonte")
        pesquisa = self.client.get(reverse("fornecedores"), {"q": "11.222.333/0001-81"})
        self.assertContains(pesquisa, "Confecções Horizonte")
        editar = self.client.post(
            reverse("fornecedor_editar", args=[fornecedor.pk]),
            {
                **self.dados,
                "nome": "Horizonte atualizado",
                "motivo": "Correção",
                "chave_idempotencia": "editar-http",
            },
        )
        self.assertEqual(editar.status_code, 302)
        fornecedor.refresh_from_db()
        self.assertEqual(fornecedor.nome, "Horizonte atualizado")

    def test_erros_preservam_campos_e_csrf_e_inativacao_post(self):
        """Given formulário inválido ou sem CSRF; When enviar; Then proteger dados."""
        response = self.client.post(
            reverse("fornecedor_novo"),
            {
                **self.dados,
                "nome": "",
                "chave_idempotencia": "invalido",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Entrega semanal")
        self.assertEqual(models.Fornecedor.objects.count(), 0)
        protegido = Client(enforce_csrf_checks=True)
        protegido.force_login(self.actor)
        self.assertEqual(
            protegido.post(reverse("fornecedor_novo"), {}).status_code, 403
        )
        fornecedor = self.cadastrar()
        url = reverse("fornecedor_inativar", args=[fornecedor.pk])
        self.client.get(url)
        fornecedor.refresh_from_db()
        self.assertTrue(fornecedor.ativo)
        response = self.client.post(
            url,
            {
                "motivo": "Fim de parceria",
                "chave_idempotencia": "inativar-http",
            },
        )
        self.assertEqual(response.status_code, 302)
        fornecedor.refresh_from_db()
        self.assertFalse(fornecedor.ativo)

    def test_entrada_http_mostra_fornecedor_e_preserva_erro(self):
        """Given compra incompleta; When enviar; Then manter dados e explicar erro."""
        fornecedor = self.cadastrar()
        url = reverse("registrar_entrada")
        self.assertContains(self.client.get(url), fornecedor.nome)
        response = self.client.post(
            url,
            {
                "produto": str(self.produto.pk),
                "quantidade": "3",
                "motivo": "COMPRA",
                "observacao": "Lote especial",
                "chave_idempotencia": "compra-http",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lote especial")
        self.assertContains(response, "fornecedor")
        response = self.client.post(
            url,
            {
                "produto": str(self.produto.pk),
                "quantidade": "3",
                "motivo": "COMPRA",
                "fornecedor": fornecedor.pk,
                "chave_idempotencia": "compra-http",
            },
        )
        self.assertRedirects(response, url)
        self.assertContains(self.client.get(url), fornecedor.nome)

    def test_documento_cpf_valido_e_invalido(self):
        """Given CPF; When cadastrar; Then conferir dígitos e formato."""
        self.assertEqual(
            self.cadastrar(documento="529.982.247-25").documento, "52998224725"
        )
        for documento in ("529.982.247-26", "<52998224725>", "１２３４５６７８９００"):
            with self.subTest(documento=documento), self.assertRaises(ValidationError):
                self.cadastrar(documento=documento)

    def test_edicao_invalida_reverte_e_replay_conflitante_falha(self):
        """Given edição inválida/replay diferente; When salvar; Then não sobrescrever."""
        fornecedor = self.cadastrar()
        self.cadastrar(nome="Segundo", documento="52998224725")
        with self.assertRaises(ValidationError):
            self.api.editar_fornecedor(
                actor=self.actor,
                fornecedor_id=fornecedor.pk,
                dados={
                    **self.dados,
                    "nome": "Não persistir",
                    "documento": "52998224725",
                },
                reason="Teste",
                idempotency_key="edicao-invalida",
            )
        fornecedor.refresh_from_db()
        self.assertEqual(fornecedor.nome, self.dados["nome"])
        self.assertFalse(models.FornecedorAlteracao.objects.exists())
        kwargs = {
            "actor": self.actor,
            "fornecedor_id": fornecedor.pk,
            "dados": self.dados,
            "reason": "Correção",
            "idempotency_key": "edicao",
        }
        self.api.editar_fornecedor(**kwargs)
        with self.assertRaises(ValidationError):
            self.api.editar_fornecedor(**{**kwargs, "reason": "Outro motivo"})

    def test_inativacao_oculta_na_selecao_mas_preserva_pesquisa_e_historico(self):
        """Given fornecedor com compra; When inativar; Then histórico permanece."""
        fornecedor = self.cadastrar()
        self.entrada(fornecedor)
        self.api.inativar_fornecedor(
            actor=self.actor,
            fornecedor_id=fornecedor.pk,
            reason="Encerrado",
            idempotency_key="ocultar",
        )
        response = self.client.get(reverse("registrar_entrada"))
        self.assertFalse(response.context["fornecedores"].exists())
        self.assertContains(response, self.dados["nome"])  # snapshot no histórico
        self.assertNotContains(
            self.client.get(reverse("fornecedores")), self.dados["nome"]
        )
        self.assertContains(
            self.client.get(reverse("fornecedores"), {"situacao": "inativos"}),
            self.dados["nome"],
        )
        self.assertContains(
            self.client.get(reverse("fornecedor_detalhe", args=[fornecedor.pk])),
            self.produto.nome,
        )

    def test_usuario_sem_permissao_nao_edita_nem_inativa_por_post(self):
        """Given vendedor; When forjar POST de escrita; Then negar sem auditoria."""
        fornecedor = self.cadastrar()
        vendedor = get_user_model().objects.create_user(
            username="sem-permissao", cargo="V"
        )
        self.client.force_login(vendedor)
        for route in ("fornecedor_editar", "fornecedor_inativar"):
            response = self.client.post(
                reverse(route, args=[fornecedor.pk]),
                {
                    **self.dados,
                    "nome": "Fraude",
                    "motivo": "Teste",
                    "chave_idempotencia": "forjado",
                },
            )
            self.assertEqual(response.status_code, 403)
        fornecedor.refresh_from_db()
        self.assertTrue(fornecedor.ativo)
        self.assertEqual(fornecedor.nome, self.dados["nome"])
        self.assertFalse(models.FornecedorAlteracao.objects.exists())

    def test_replay_de_entrada_nao_aceita_outro_usuario_ou_fornecedor(self):
        """Given chave concluída; When trocar autor/origem; Then rejeitar."""
        fornecedor = self.cadastrar()
        segundo = self.cadastrar(nome="Segundo", documento="")
        self.entrada(fornecedor, idempotency_key="mesma-entrada")
        with self.assertRaises(StockOperationError):
            self.entrada(segundo, idempotency_key="mesma-entrada")
        outro = get_user_model().objects.create_user(username="outro", cargo="G")
        with self.assertRaises(StockOperationError):
            self.entrada(fornecedor, actor=outro, idempotency_key="mesma-entrada")

    def test_paginacao_preserva_filtros(self):
        """Given mais de 20 fornecedores; When paginar pesquisa; Then preservar filtro."""
        for index in range(21):
            self.cadastrar(nome=f"Fornecedor página {index:02}", documento="")
        response = self.client.get(
            reverse("fornecedores"),
            {
                "q": "Fornecedor página",
                "situacao": "todos",
                "page": 2,
            },
        )
        self.assertEqual(len(response.context["pagina"]), 1)
        self.assertContains(response, "situacao=todos")

    def test_historico_apos_remover_usuario_responsavel(self):
        """Given histórico com responsável removido; When abrir; Then manter consulta."""
        fornecedor = self.cadastrar()
        self.entrada(fornecedor)
        outro = get_user_model().objects.create_user(
            username="gerente-consulta", cargo="G"
        )
        self.client.force_login(outro)
        self.actor.delete()
        self.assertContains(
            self.client.get(reverse("fornecedor_detalhe", args=[fornecedor.pk])),
            "Usuário removido",
        )
