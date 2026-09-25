from __future__ import annotations

import ast
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    ROOT / "app/api/admin_extended.py",
    ROOT / "app/api/templates/admin.html",
    ROOT / "app/services/pricing.py",
    ROOT / "app/services/runtime_payment_config.py",
    ROOT / "app/services/runtime_admin_config.py",
    ROOT / "app/services/text_normalization.py",
    ROOT / "scripts/fetch_vazirmatn.ps1",
    ROOT / "scripts/check_vazirmatn_local.py",
]


def parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def assert_contains(path: Path, *needles: str) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [x for x in needles if x not in text]
    if missing:
        raise AssertionError(f"{path}: missing {missing}")


def main() -> None:
    for path in REQUIRED_FILES:
        if not path.is_file():
            raise AssertionError(f"required file missing: {path}")
        if path.suffix == ".py":
            parse(path)

    assert_contains(ROOT / "app/api/main.py", "import app.api.admin_extended", "/admin/static")
    assert_contains(
        ROOT / "app/api/admin_extended.py",
        '@router.get("/users"',
        '@router.get("/users/{user_id}/360"',
        '@router.patch("/users/{user_id}/status"',
        '@router.post("/users/{user_id}/subscription"',
        '@router.patch("/genres/{genre_id}"',
        '@router.patch("/people/{person_id}"',
        '@router.get("/countries"',
        '@router.get("/years"',
        '@router.get("/series"',
        '@router.get("/seasons"',
        '@router.get("/episodes"',
        '@router.get("/payment-gateway"',
        '@router.put("/payment-gateway"',
        '@router.post("/payment-gateway/test"',
        '@router.get("/settings/modules"',
        '@router.put("/settings/modules"',
        '@router.get("/audit"',
        '@router.post("/encoding/repair"',
        '@router.post("/titles/{title_id}/pipeline-advanced"',
        "or_(*conditions)",
        "title_people.c.role",
    )

    html = (ROOT / "app/api/templates/admin.html").read_text(encoding="utf-8")
    if "Ø" in html or "Ù" in html or "â€" in html:
        raise AssertionError("admin.html contains common mojibake markers")

    class VisibleTextParser(HTMLParser):
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

    parser = VisibleTextParser()
    parser.feed(html)
    visible = " ".join(parser.parts)
    visible_english = re.findall(
        r"\b(?:Dashboard|Login|Logout|Register|Settings|Save|Cancel|Delete|Edit|Search|Loading|Success|Error|Admin|User|Payment|Wallet|Subscription|Plan|Active|Pending|Failed|Delivered|Retry|Profile|Encoding)\b",
        visible,
        flags=re.I,
    )
    if visible_english:
        raise AssertionError(f"English UI labels remain: {visible_english[:10]}")
    for unwanted in (" Bot ", " callback "):
        if unwanted in f" {visible} ".lower():
            raise AssertionError(f"Untranslated UI wording remains: {unwanted.strip()}")
    assert_contains(
        ROOT / "app/api/templates/admin.html",
        "@font-face",
        "/admin/static/fonts/Vazirmatn-Regular.woff2",
        "/admin/static/fonts/Vazirmatn-Bold.woff2",
        "پروفایل ۳۶۰",
        "درگاه پرداخت",
        "ساخت ماتریس",
        "کُدگذاری",
    )

    # Parse the inline JavaScript with the installed Node runtime when available.
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.I | re.S)
    js = "\n\n".join(scripts)
    js_path = ROOT / "_admin_inline_check.js"
    try:
        js_path.write_text(js, encoding="utf-8")
        node = subprocess.run(["node", "--check", str(js_path)], text=True, capture_output=True)
        if node.returncode != 0:
            raise AssertionError("admin.html inline JavaScript failed node --check: " + node.stderr.strip())
    finally:
        js_path.unlink(missing_ok=True)

    # Every direct DOM lookup in the Admin script must have a corresponding HTML id.
    ids = set(re.findall(r"\bid=[\"']([^\"']+)[\"']", html))
    referenced = set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", js))
    missing_ids = sorted(referenced - ids)
    if missing_ids:
        raise AssertionError(f"admin.html JS references missing ids: {missing_ids}")

    assert_contains(
        ROOT / "app/services/content_pipeline.py",
        "advanced_key =",
        "default_advanced_key",
        "target_codec_video",
        "target_codec_audio",
        "target_container",
    )
    assert_contains(
        ROOT / "app/services/pricing.py",
        "plan_discount_percent",
        "plan_discounted_toman",
        "plan_stars",
        "discount_image_url",
        "discount_icon_url",
    )
    assert_contains(
        ROOT / "app/services/runtime_payment_config.py",
        "https://",
        "merchant_id_configured",
        "NamedTemporaryFile",
    )

    print("ADMIN_ENHANCEMENTS_STATIC_OK")


if __name__ == "__main__":
    main()
