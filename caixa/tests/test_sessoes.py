"""Contrato de abertura: Given/When/Then, com dados descartáveis."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from caixa.models import SessaoCaixa
from caixa.services import CaixaOperationError, abrir_sessao


class AberturaCaixaTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username='gerente-caixa', cargo='G',
        )

    def open(self, **overrides):
        values = dict(
            actor=self.manager, fundo_inicial='100.00',
            motivo='Abertura do expediente', idempotency_key='abertura-1',
        )
        values.update(overrides)
        return abrir_sessao(**values)

    def test_abertura_registra_valor_autor_motivo_e_auditoria(self):
        """Given gerente; When abrir; Then registrar fundo e autoria persistida."""
        result = self.open()
        session = SessaoCaixa.objects.get()
        self.assertTrue(result.created)
        self.assertEqual(result.sessao.pk, session.pk)
        self.assertEqual(session.fundo_inicial, Decimal('100.00'))
        self.assertEqual(session.responsavel, self.manager)
        self.assertEqual(session.motivo, 'Abertura do expediente')
        self.assertTrue(session.aberta)
        self.assertEqual(session.versao, 0)
        self.assertIsNotNone(session.aberta_em)
        self.assertEqual(len(session.assinatura), 64)

    def test_replay_normalizado_nao_abre_duas_sessoes(self):
        """Given abertura; When repetir conteúdo equivalente; Then recuperar original."""
        first = self.open()
        replay = self.open(fundo_inicial='100,00', motivo=' Abertura do expediente ')
        self.assertFalse(replay.created)
        self.assertEqual(first.sessao.pk, replay.sessao.pk)
        self.assertEqual(SessaoCaixa.objects.count(), 1)

    def test_replay_funciona_apos_fechamento(self):
        """Given sessão encerrada; When repetir abertura; Then devolver evento original."""
        first = self.open()
        SessaoCaixa.objects.filter(pk=first.sessao.pk).update(aberta=False)
        replay = self.open()
        self.assertFalse(replay.created)
        self.assertFalse(replay.sessao.aberta)

    def test_replay_com_outro_conteudo_ou_autor_falha(self):
        """Given chave usada; When mudar valor, motivo ou autor; Then rejeitar."""
        self.open()
        other = get_user_model().objects.create_user(username='outro', cargo='G')
        for changes in (
            {'fundo_inicial': '101'}, {'motivo': 'Outro motivo'}, {'actor': other},
        ):
            with self.subTest(changes=changes), self.assertRaises(CaixaOperationError):
                self.open(**changes)
        self.assertEqual(SessaoCaixa.objects.count(), 1)

    def test_somente_gerente_ativo_ou_superusuario_abre(self):
        """Given anônimo, vendedor, caixa ou inativo; When abrir; Then negar."""
        for actor in (
            None, AnonymousUser(),
            get_user_model().objects.create_user(username='vendedor', cargo='V'),
            get_user_model().objects.create_user(username='operador', cargo='C'),
            get_user_model().objects.create_user(
                username='inativo', cargo='G', is_active=False,
            ),
        ):
            with self.subTest(actor=actor), self.assertRaises(CaixaOperationError):
                self.open(actor=actor)
        self.assertFalse(SessaoCaixa.objects.exists())
        root = get_user_model().objects.create_user(
            username='root', cargo='V', is_superuser=True,
        )
        self.assertTrue(self.open(actor=root).created)

    def test_fundo_zero_aceito_e_valores_invalidos_nao_gravam(self):
        """Given valor inválido; When abrir; Then não gravar; zero é permitido."""
        for value in ('-0.01', '1.001', 'NaN', 'Infinity', '-Infinity',
                      '10000000000.00', 'abc', '', None):
            with self.subTest(value=value), self.assertRaises(CaixaOperationError):
                self.open(fundo_inicial=value)
        self.assertFalse(SessaoCaixa.objects.exists())
        self.assertEqual(self.open(fundo_inicial='0').sessao.fundo_inicial, Decimal('0'))

    def test_chave_e_motivo_obrigatorios(self):
        """Given chave ou motivo inválido; When abrir; Then falhar sem persistir."""
        for changes in (
            {'motivo': ''}, {'motivo': ' '}, {'motivo': 'a' * 501},
            {'idempotency_key': None}, {'idempotency_key': ' '},
            {'idempotency_key': 'a' * 65},
        ):
            with self.subTest(changes=changes), self.assertRaises(CaixaOperationError):
                self.open(**changes)
        self.assertFalse(SessaoCaixa.objects.exists())

    def test_segunda_abertura_rejeitada_mas_nova_sessao_apos_encerrar_permitida(self):
        """Given sessão aberta; When outra chave abrir; Then rejeitar até encerrar."""
        first = self.open()
        with self.assertRaisesMessage(CaixaOperationError, 'aberta'):
            self.open(idempotency_key='abertura-2')
        SessaoCaixa.objects.filter(pk=first.sessao.pk).update(aberta=False)
        self.assertTrue(self.open(idempotency_key='abertura-2').created)

    def test_banco_impede_duas_sessoes_abertas_e_fundo_negativo(self):
        """Given bypass do serviço; When violar invariantes; Then banco rejeitar."""
        first = self.open()
        for amount in ('0.00', '-0.01'):
            with self.subTest(amount=amount), self.assertRaises(IntegrityError):
                with transaction.atomic():
                    SessaoCaixa.objects.create(
                        responsavel=self.manager, motivo='Teste de constraint',
                        fundo_inicial=amount, chave_idempotencia='direto', assinatura='0' * 64,
                        aberta=amount == '0.00',
                    )
        self.assertEqual(SessaoCaixa.objects.get().pk, first.sessao.pk)

    def test_responsavel_preservado_por_protect(self):
        """Given sessão auditada; When excluir gerente; Then preservar histórico."""
        self.open()
        with self.assertRaises(ProtectedError):
            self.manager.delete()
