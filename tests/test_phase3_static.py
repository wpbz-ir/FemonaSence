from pathlib import Path


def test_phase3_files_exist():
    expected = [
        "app/services/delivery.py",
        "app/services/content_admin.py",
        "app/api/admin.py",
        "app/api/admin_auth.py",
        "app/api/templates/admin.html",
        "app/db/models/media_delivery.py",
        "alembic/versions/0002_deliveries.py",
    ]
    missing = [p for p in expected if not Path(p).exists()]
    assert not missing, missing
