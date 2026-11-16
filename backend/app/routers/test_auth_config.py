from app.core.config import settings
from app.routers.auth import google_config


def test_google_config_exposes_only_the_public_client_id(monkeypatch):
    client_id = "client.apps.googleusercontent.com"
    monkeypatch.setattr(settings, "google_client_id", client_id)

    response = google_config()

    assert response.enabled is True
    assert response.client_id == client_id


def test_google_config_reports_disabled_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", None)

    response = google_config()

    assert response.enabled is False
    assert response.client_id is None
