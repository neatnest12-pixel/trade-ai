import shutil
import pytest
from app.v3_5 import storage_health


@pytest.fixture(autouse=True)
def simulated_healthy_storage(monkeypatch):
    """Unit tests use synthetic stores; host disk usage is not a test input."""
    original = storage_health.shutil.disk_usage
    monkeypatch.setattr(storage_health.shutil, 'disk_usage',
                        lambda path: type(original('.'))(100 * 1024**3, 60 * 1024**3, 40 * 1024**3))
