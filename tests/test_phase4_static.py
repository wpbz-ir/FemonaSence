from pathlib import Path

def test_phase4_files_exist():
    expected = [
        "app/services/access.py",
        "app/services/release_catalog.py",
        "app/bot/handlers/releases.py",
    ]
    assert not [p for p in expected if not Path(p).exists()]
