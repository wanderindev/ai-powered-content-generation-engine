---
name: fastapi-endpoint
description: Use this skill when creating new API endpoints. Defines the patterns for routers, schemas, and error handling in this project.
---

# FastAPI Endpoint Creation Procedure

## File Organization

```
backend/app/
├── api/
│   ├── __init__.py
│   ├── articles.py      # /api/v1/articles/*
│   ├── categories.py    # /api/v1/categories/*
│   ├── search.py        # /api/v1/search/*
│   └── contact.py       # /api/v1/contact/*
├── schemas/
│   ├── __init__.py
│   ├── article.py       # Article request/response models
│   ├── category.py      # Category models
│   └── contact.py       # Contact form models
├── models/
│   ├── __init__.py
│   ├── article.py       # SQLAlchemy Article model
│   ├── category.py      # Category + Taxonomy models
│   └── media.py         # Media model
└── services/            # Business logic (if complex)
```

## Step-by-Step: Creating a New Endpoint

### Step 1: Define Pydantic Schemas

Create or update schema file in `app/schemas/`:

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr

# Request model (for POST/PUT)
class ItemCreate(BaseModel):
    name: str
    description: Optional[str] = None

# Response model
class ItemResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True  # Allows ORM model conversion
```

### Step 2: Create the Router

Create or update router file in `app/api/`:

```python
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.item import Item
from app.schemas.item import ItemCreate, ItemResponse

router = APIRouter(prefix="/items", tags=["items"])


@router.get("", response_model=list[ItemResponse])
def list_items(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all items with pagination."""
    offset = (page - 1) * limit
    items = db.query(Item).offset(offset).limit(limit).all()
    return items


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
):
    """Get a single item by ID."""
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("", response_model=ItemResponse, status_code=201)
def create_item(
    item_data: ItemCreate,
    db: Session = Depends(get_db),
):
    """Create a new item."""
    item = Item(**item_data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
```

### Step 3: Register the Router

Add to `app/main.py`:

```python
from app.api import items  # Import new router

# In the router registration section:
app.include_router(items.router, prefix="/api/v1")
```

### Step 4: Write Tests

Create `tests/test_items.py`:

```python
def test_list_items(test_client):
    response = test_client.get("/api/v1/items")
    assert response.status_code == 200


def test_get_item_not_found(test_client):
    response = test_client.get("/api/v1/items/99999")
    assert response.status_code == 404
```

## Patterns in This Project

### Database Session Injection
Always use dependency injection:
```python
db: Session = Depends(get_db)
```

### Filtering Approved Content
For public endpoints, always filter by status:
```python
query = db.query(Article).filter(Article.status == "APPROVED")
```

### Excluding Bio Articles
Exclude taxonomy ID 5 (Notable Figures):
```python
EXCLUDED_TAXONOMY_ID = 5
query = query.filter(Taxonomy.id != EXCLUDED_TAXONOMY_ID)
```

### Eager Loading Relationships
Use `joinedload` to avoid N+1 queries:
```python
from sqlalchemy.orm import joinedload

article = (
    db.query(Article)
    .options(
        joinedload(Article.category).joinedload(Category.taxonomy),
        joinedload(Article.feature_image),
    )
    .filter(Article.id == article_id)
    .first()
)
```

### Pagination Pattern
```python
@router.get("")
def list_items(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * limit
    items = db.query(Item).offset(offset).limit(limit).all()
    return items
```

### Error Responses
```python
from fastapi import HTTPException

# Not found
raise HTTPException(status_code=404, detail="Article not found")

# Bad request
raise HTTPException(status_code=400, detail="Invalid parameter")

# Server error (let FastAPI handle, or explicit)
raise HTTPException(status_code=500, detail="Internal server error")
```

## Checklist for New Endpoints

- [ ] Schema defined in `app/schemas/`
- [ ] Router created/updated in `app/api/`
- [ ] Router registered in `app/main.py`
- [ ] Tests written in `tests/`
- [ ] Docstrings on all endpoint functions
- [ ] `response_model` specified on endpoints
- [ ] Appropriate status codes used
- [ ] Database queries use ORM patterns
