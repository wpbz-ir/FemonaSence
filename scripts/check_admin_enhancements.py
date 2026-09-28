from __future__ import annotations

import ast
import re
import subprocess
import tempfile
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
    ROOT / "scripts/check_vazirmatn_local.py",
]
# [P0-10] scripts/fetch_vazirmatn.ps1 was deleted: the panel loads Vazirmatn
# from the Google Fonts CDN, so there is nothing to fetch locally any more.


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

    # [P0-10] current reality: admin_extended registers its routes on the shared
    # admin router via `from app.api import admin_extended`, and the panel HTML
    # lives in app/api/templates/admin.html served at /admin (main.py) and at
    # /api/admin/ui (admin.py) — there is no /admin/static mount any more.
    assert_contains(
        ROOT / "app/api/main.py",
        "from app.api import admin_extended",
        '"templates" / "admin.html"',
        '@app.get("/admin"',
    )
    assert_contains(
        ROOT / "app/api/admin.py",
        '@router.get("/ui"',
        '"templates" / "admin.html"',
    )
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
    # [P0-10] mojibake must not be VISIBLE; the panel's own JS mojibake detector
    # intentionally lists the bad characters inside <script>, so the raw file
    # can legitimately contain them — only rendered text is asserted.
    if "Ø" in visible or "Ù" in visible or "â€" in visible:
        raise AssertionError("admin.html contains common mojibake markers")
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
    # [P0-10] fonts: local @font-face/woff2 statics were replaced by the
    # Google Fonts CDN — assert the CDN wiring instead of local file paths.
    assert_contains(
        ROOT / "app/api/templates/admin.html",
        "https://fonts.googleapis.com",
        "family=Vazirmatn",
        "font-family:Vazirmatn",
        "نمای ۳۶۰",
        "درگاه پرداخت",
        "ساخت پردازش کیفیت",
        "کدگذاری",
        "منوی ربات",
    )
    for stale in ("@font-face", "/admin/static/fonts/", "woff2"):
        if stale in html:
            raise AssertionError(f"stale local-font reference remains in admin.html: {stale}")

    # Parse the inline JavaScript with the installed Node runtime when available.
    # The extracted JS goes into a throwaway temp dir (NEVER into the repo root).
    scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.I | re.S)
    js = "\n\n".join(scripts)
    with tempfile.TemporaryDirectory() as tmp_dir:
        js_path = Path(tmp_dir) / "_admin_inline_check.js"
        js_path.write_text(js, encoding="utf-8")
        try:
            node = subprocess.run(["node", "--check", str(js_path)], text=True, capture_output=True)
        except FileNotFoundError:
            node = None  # [P0-10] Node not installed here; skip the JS syntax gate
        if node is not None and node.returncode != 0:
            raise AssertionError("admin.html inline JavaScript failed node --check: " + node.stderr.strip())

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
    # [P0-10] the real pricing API is discounted_amount/plan_toman_price/
    # plan_stars_price (+ normalize_percent) — the old plan_discount_*
    # helpers never existed in the shipped module.
    assert_contains(
        ROOT / "app/services/pricing.py",
        "def normalize_percent",
        "def discounted_amount",
        "def plan_toman_price",
        "def plan_stars_price",
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
