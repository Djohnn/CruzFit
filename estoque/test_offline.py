import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


class OfflineAssetTests(SimpleTestCase):
    """Cenários Given/When/Then para operação local sem rede externa."""

    required_assets = (
        'vendor/bootstrap/css/bootstrap.min.css',
        'vendor/jquery/jquery.slim.min.js',
        'vendor/bootstrap/js/bootstrap.bundle.min.js',
    )

    def test_base_uses_only_local_vendor_assets(self):
        """Given rede bloqueada; When renderizar base; Then não depender de CDN."""
        base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(
            encoding='utf-8',
        )

        self.assertNotIn('https://', base)
        for asset in self.required_assets:
            self.assertIn(f"static '{asset}'", base)

    def test_required_vendor_assets_are_findable_and_non_empty(self):
        """Given instalação local; Then todos os assets necessários são servidos."""
        for asset in self.required_assets:
            found = finders.find(asset)
            self.assertIsNotNone(found, asset)
            assert found is not None
            self.assertGreater(Path(found).stat().st_size, 1000, asset)

    def test_local_vendor_assets_have_no_external_imports(self):
        """Given assets locais; Then CSS/JS não puxam recursos externos."""
        for asset in self.required_assets:
            found = finders.find(asset)
            self.assertIsNotNone(found, asset)
            assert found is not None
            content = Path(found).read_text(encoding='utf-8', errors='ignore')
            executable_content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
            self.assertNotRegex(
                executable_content,
                r'@import\s+[^;]*https?://|url\(\s*[\"\']?https?://',
                asset,
            )
            self.assertNotRegex(
                executable_content,
                r'(?:import|require|fetch)\(\s*[\"\']https?://',
                asset,
            )
