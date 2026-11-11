from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import admin as admin_auth_django
from django.core.exceptions import ValidationError

from .forms import UserChangeForm, UserCreationForm
from .models import Users
from .services import limpar_falhas_login, login_bloqueado, registrar_falha_login


class LoginRateLimitAdminAuthenticationForm(AdminAuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get('username')
        if username and login_bloqueado(self.request, username):
            raise ValidationError(
                self.error_messages['invalid_login'],
                code='invalid_login',
                params={'username': username},
            )
        try:
            data = super().clean()
        except ValidationError:
            if username:
                registrar_falha_login(self.request, username)
            raise
        if username:
            limpar_falhas_login(self.request, username)
        return data


@admin.register(Users)
class UsersAdmin(admin_auth_django.UserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    model = Users
    fieldsets = admin_auth_django.UserAdmin.fieldsets + (
        ('Cargo', {'fields': ('cargo',)}),
    )


admin.site.login_form = LoginRateLimitAdminAuthenticationForm
