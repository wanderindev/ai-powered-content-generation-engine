---
name: pytest-testing
description: Use this skill when writing, running, or debugging tests. Defines test patterns, fixtures, and pytest conventions for this project.
---

# Testing Standard Operating Procedure

## Philosophy

We follow practical testing with focus on API endpoint behavior. Tests should:
- Verify endpoint responses and status codes
- Test edge cases and error handling
- Use real PostgreSQL (via Docker) for realistic testing
- Mock external services only (Make.com webhook)

## Tech Stack

| Tool | Purpose |
|------|---------|
| pytest | Test runner |
| pytest-asyncio | Async test support |
| httpx | Test client for FastAPI |
| testcontainers | PostgreSQL container for tests |

## Test File Location

```
backend/tests/
├── __init__.py
├── conftest.py          # Shared fixtures
├── test_articles.py     # Article endpoint tests
├── test_categories.py   # Category endpoint tests
├── test_search.py       # Search endpoint tests
└── test_contact.py      # Contact form tests
```

## Naming Conventions

```python
# File: test_{module}.py
# Function: test_{action}_{scenario}

def test_list_articles_returns_paginated_results():
    ...

def test_get_article_by_slug_returns_404_when_not_found():
    ...
```

## Using the Test Client

```python
from fastapi.testclient import TestClient
from app.main import app

def test_example(test_client: TestClient):
    response = test_client.get("/api/v1/articles")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
```

## Using the Test Database

The `conftest.py` provides a `test_db` fixture with a real PostgreSQL container:

```python
def test_with_database(test_client: TestClient, test_db: Session):
    # test_db is a real PostgreSQL session
    # test_client is configured to use test_db

    # Create test data
    article = Article(title="Test", content="Content", status="APPROVED")
    test_db.add(article)
    test_db.commit()

    # Test the endpoint
    response = test_client.get(f"/api/v1/articles/{article.id}")
    assert response.status_code == 200
```

## Mocking External Services

For the contact form (Make.com webhook):

```python
from unittest.mock import patch

def test_contact_form_submission(test_client: TestClient):
    with patch("app.api.contact.httpx.post") as mock_post:
        mock_post.return_value.status_code = 200

        response = test_client.post("/api/v1/contact", json={
            "name": "Test User",
            "email": "test@example.com",
            "message": "Hello"
        })

        assert response.status_code == 200
        mock_post.assert_called_once()
```

## Running Tests

All tests run inside the Docker container:

```bash
# All tests
docker-compose exec backend pytest

# Specific file
docker-compose exec backend pytest tests/test_articles.py

# Specific test
docker-compose exec backend pytest -k "test_list_articles"

# With coverage
docker-compose exec backend pytest --cov=app --cov-report=term-missing

# Verbose output
docker-compose exec backend pytest -v

# Stop on first failure
docker-compose exec backend pytest -x

# Run with fresh container (if dependencies changed)
docker-compose build backend && docker-compose up -d backend
docker-compose exec backend pytest
```

## Workflow When Writing Tests

1. Identify the endpoint/function to test
2. Create or update the appropriate test file
3. Write the test function following naming conventions
4. Use fixtures from conftest.py (test_client, test_db)
5. Run the specific test to verify it passes
6. Run related tests to ensure no regressions

## Common Assertions

```python
# Status codes
assert response.status_code == 200
assert response.status_code == 404
assert response.status_code == 422  # Validation error

# JSON response
data = response.json()
assert "id" in data
assert data["title"] == "Expected Title"
assert len(data) == 10

# List responses
assert isinstance(data, list)
assert all("id" in item for item in data)

# Error responses
assert response.status_code == 404
assert "detail" in response.json()
```