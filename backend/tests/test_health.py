"""Tests for health and info endpoints."""


def test_health_check(test_client):
    """Health endpoint should return healthy status."""
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_root_returns_api_info(test_client):
    """Root endpoint should return API information."""
    response = test_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data
    assert "docs" in data
