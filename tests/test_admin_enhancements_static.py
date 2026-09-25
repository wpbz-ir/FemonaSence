from pathlib import Path
import ast
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AdminEnhancementsStaticTests(unittest.TestCase):
    def test_extended_admin_is_valid_python(self):
        ast.parse((ROOT / "app/api/admin_extended.py").read_text(encoding="utf-8"))

    def test_main_registers_extended_router(self):
        text = (ROOT / "app/api/main.py").read_text(encoding="utf-8")
        self.assertIn("import app.api.admin_extended", text)
        self.assertIn('/admin/static', text)

    def test_admin_ui_is_persian_and_local_font_ready(self):
        text = (ROOT / "app/api/templates/admin.html").read_text(encoding="utf-8")
        self.assertNotIn("Ø", text)
        self.assertNotIn("Ù", text)
        self.assertIn("Vazirmatn-Regular.woff2", text)
        self.assertIn("Vazirmatn-Bold.woff2", text)
        self.assertIn("font-family:Vazirmatn,sans-serif", text)
        self.assertIn("پروفایل ۳۶۰", text)
        self.assertIn("درگاه پرداخت", text)
        self.assertIn("ساخت ماتریس", text)
        self.assertIn("کُدگذاری", text)
        self.assertIn("تنظیمات منوی ربات", text)
        self.assertNotIn("تنظیمات این بخش مستقل از تنظیمات Bot", text)
        self.assertNotIn("شناسه‌های فنی callback", text)

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
