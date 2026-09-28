from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_bot_menu_config_preserves_callback_contracts():
    text = read("app/bot/keyboards/main_menu.py")
    tree = ast.parse(text)
    callbacks = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("menu:")}
    required = {"menu:movies","menu:series","menu:animation","menu:popular","menu:new","menu:imdb","menu:years","menu:genres","menu:collections","menu:actors","menu:search","menu:favorites","menu:history","menu:account","menu:subscription","menu:wallet","menu:admin","menu:home"}
    assert required.issubset(callbacks)


def test_admin_html_has_bot_menu_section_and_endpoint():
    # [P0-10] The old bot-menu SETTINGS panel (botMenuSettings/saveBotMenuSettings)
    # was removed from admin.html; the current "منوی ربات" section is a read-only
    # view fed by GET /bot-menu (app/api/admin.py, backed by the bot keyboard).
    html = read("app/api/templates/admin.html")
    assert "'bot-menu':'منوی ربات'" in html  # page label registered in PAGES
    assert "api('/bot-menu')" in html  # section fetches the endpoint
    admin = read("app/api/admin.py")
    assert '@router.get("/bot-menu", dependencies=[Depends(admin_gate)])' in admin
    assert "MAIN_MENU_ITEMS" in admin  # served from the bot keyboard source


def test_granular_settings_exist():
    text = read("app/services/runtime_admin_config.py")
    for key in ("taxonomy","releases","users","subscriptions","payments","matrix","jobs","audit"):
        assert f'"{key}"' in text
