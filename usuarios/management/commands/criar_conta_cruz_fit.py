from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from usuarios.models import Users


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

        senha = senha or ''
        if not nome:
            raise CommandError('Informe um nome para a conta.')
        if len(senha) < 6:
            raise CommandError('A senha deve ter pelo menos 6 caracteres.')
        if Users.objects.filter(first_name__iexact=nome).exists():
            raise CommandError('Já existe uma conta com este nome.')

        base_username = slugify(nome).replace('-', '_') or 'cruzfit'
        username = base_username
        suffix = 2
        while Users.objects.filter(username=username).exists():
            username = f'{base_username}_{suffix}'
            suffix += 1

        user = Users.objects.create_user(
            username=username,
            first_name=nome,
            password=senha,
            cargo='G',
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Conta criada com sucesso para {user.first_name}. '
                'Use o nome informado no login.'
            )
        )
