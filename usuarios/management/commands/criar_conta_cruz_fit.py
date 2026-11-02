from django.core.management.base import BaseCommand, CommandError

from usuarios.services import UserOperationError, criar_conta_principal


class Command(BaseCommand):
    help = 'Cria a primeira conta gerencial do Cruz Fit.'

    def add_arguments(self, parser):
        parser.add_argument('--nome', dest='nome')
        parser.add_argument('--senha', dest='senha')

    def handle(self, *args, **options):
        nome = (options.get('nome') or input('Nome da conta: ')).strip()
        senha = options.get('senha')
        if senha is None:
            from getpass import getpass

            senha = getpass('Senha: ')
            confirmacao = getpass('Confirme a senha: ')
            if senha != confirmacao:
                raise CommandError('As senhas não conferem.')

        try:
            user = criar_conta_principal(nome=nome, senha=senha or '')
        except UserOperationError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(
                f'Conta criada com sucesso para {user.first_name}. '
                'Use o identificador informado no login.'
            )
        )
