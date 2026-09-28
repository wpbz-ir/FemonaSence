from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AdminEnhancementsStaticTests(unittest.TestCase):
    def test_extended_admin_is_valid_python(self):
        ast.parse((ROOT / "app/api/admin_extended.py").read_text(encoding="utf-8"))

    def test_main_registers_extended_router(self):
        # [P0-10] admin_extended is imported via `from app.api import admin_extended`
        # (it decorates the shared admin router; there is no literal
        # "import app.api.admin_extended" any more) and the panel HTML is served
        # from app/api/templates/admin.html at /admin (plus /api/admin/ui).
        text = (ROOT / "app/api/main.py").read_text(encoding="utf-8")
        self.assertIn("from app.api import admin_extended", text)
        self.assertIn('"templates" / "admin.html"', text)
        self.assertIn('@app.get("/admin"', text)

    def test_admin_ui_is_persian_and_cdn_font_ready(self):
        # [P0-10] Vazirmatn is now loaded from the Google Fonts CDN; the local
        # @font-face/woff2 statics were removed. Keep the protective intent:
        # the CDN reference must exist and no local font URL may 404.
        from html.parser import HTMLParser

        class _VisibleText(HTMLParser):
            def __init__(self):
                super().__init__()
                self.skip = 0
                self.parts = []

            def handle_starttag(self, tag, attrs):
                if tag.lower() in {"script", "style", "noscript"}:
                    self.skip += 1

            def handle_endtag(self, tag):
                if tag.lower() in {"script", "style", "noscript"} and self.skip:
                    self.skip -= 1

            def handle_data(self, data):
                if not self.skip:
                    self.parts.append(data)

        raw = (ROOT / "app/api/templates/admin.html").read_text(encoding="utf-8")
        parser = _VisibleText()
        parser.feed(raw)
        text = " ".join(parser.parts)
        # Mojibake must not be VISIBLE; the panel's own JS mojibake detector
        # intentionally lists the bad characters inside <script>, so only the
        # visible text is asserted here.
        self.assertNotIn("Ø", text)
        self.assertNotIn("Ù", text)
        self.assertIn("https://fonts.googleapis.com", raw)
        self.assertIn("family=Vazirmatn", raw)
        self.assertIn("font-family:Vazirmatn", raw)
        for stale in ("@font-face", "/admin/static/fonts/", "woff2"):
            self.assertNotIn(stale, raw)
        self.assertIn("نمای ۳۶۰", raw)
        self.assertIn("درگاه پرداخت", raw)
        self.assertIn("ساخت پردازش کیفیت", raw)
        self.assertIn("کدگذاری", raw)
        self.assertIn("منوی ربات", raw)
        self.assertNotIn("تنظیمات این بخش مستقل از تنظیمات Bot", raw)
        self.assertNotIn("شناسه‌های فنی callback", raw)

    def test_payment_and_pricing_services_parse(self):
        for name in [
            "pricing.py",
            "runtime_payment_config.py",
            "runtime_admin_config.py",
            "text_normalization.py",
        ]:
            ast.parse((ROOT / "app/services" / name).read_text(encoding="utf-8"))

    def test_pipeline_key_has_advanced_dimensions(self):
        text = (ROOT / "app/services/content_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("advanced_key", text)
        self.assertIn("default_advanced_key", text)
        self.assertIn("target_codec_video", text)
        self.assertIn("target_codec_audio", text)
        self.assertIn("target_container", text)


if __name__ == "__main__":
    unittest.main()
