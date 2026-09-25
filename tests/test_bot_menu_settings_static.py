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


def test_admin_html_has_bot_menu_panel_and_endpoint():
    html = read("app/api/templates/admin.html")
    assert 'data-panel="bot-menu"' in html
    assert 'id="botMenuSettings"' in html
    assert 'saveBotMenuSettings' in html
    assert "'/settings/bot-menu'" in html


def test_granular_settings_exist():
    text = read("app/services/runtime_admin_config.py")
    for key in ("taxonomy","releases","users","subscriptions","payments","matrix","jobs","audit"):
        assert f'"{key}"' in text
