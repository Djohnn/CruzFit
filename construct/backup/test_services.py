"""Testes de integração do serviço de backup; casos específicos ficam em test_archive."""

from construct.backup.services import create_backup, verify_backup


def test_service_symbols_are_exposed():
    """Contrato inicial: serviços públicos existem para a CLI adaptar entrada/saída."""
    assert callable(create_backup)
    assert callable(verify_backup)
