import copy

from django.contrib.auth import get_user_model
from django.template import Context
from django.test import TestCase

# Create your tests here.


class DjangoTemplateContextCompatibilityTests(TestCase):
    def test_context_can_be_copied(self):
        context = Context({"produto": "teste"})

        duplicate = copy.copy(context)

        self.assertEqual(duplicate["produto"], "teste")
        self.assertIsNot(duplicate.dicts, context.dicts)

    def test_product_admin_changelist_renders(self):
        user_model = get_user_model()
        user_model.objects.create_superuser(
            username="admin-compat",
            email="admin-compat@example.com",
            password="test-password",
        )
        self.client.login(username="admin-compat", password="test-password")

        response = self.client.get("/admin/estoque/produto/")

        self.assertEqual(response.status_code, 200)
