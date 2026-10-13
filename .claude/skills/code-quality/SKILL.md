---
name: code-quality
description: Use this skill for linting, formatting, and code quality checks. Defines ruff configuration and common fixes.
---

# Code Quality Procedure

## Tools

| Tool | Purpose | Command |
|------|---------|---------|
| ruff | Linting + Formatting | `ruff check .` / `ruff format .` |

## Ruff Configuration

Located in `backend/pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
src = ["app", "tests"]

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "F",   # pyflakes
    "I",   # isort
    "UP",  # pyupgrade
]
ignore = [
    "E501",  # line too long (handled by formatter)
]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

## Running Checks

All linting runs inside the Docker container:

```bash
# Check for issues
docker-compose exec backend ruff check /app

# Check specific file
docker-compose exec backend ruff check /app/app/api/articles.py

# Auto-fix issues
docker-compose exec backend ruff check /app --fix

# Format code
docker-compose exec backend ruff format /app

# Format specific file
docker-compose exec backend ruff format /app/app/api/articles.py

# Check what would be formatted (dry run)
docker-compose exec backend ruff format /app --check
```

Note: Paths inside the container start with `/app/` which maps to `backend/` on the host.

## Common Issues and Fixes

### F401: Unused Import
```python
# Bad
from typing import Optional, List, Dict  # Dict unused

# Good
from typing import Optional, List
```

### F841: Unused Variable
```python
# Bad
def func():
    unused_var = 42  # Never used
    return "something"

# Good - prefix with underscore if intentionally unused
def func():
    _unused_var = 42  # Indicates intentional
    return "something"
```

### E711: Comparison to None
```python
# Bad
if value == None:

# Good
if value is None:
```

### I001: Import Sorting
```python
# Bad
from app.models import Article
import os
from typing import Optional

# Good (sorted: stdlib, third-party, local)
import os
from typing import Optional

from app.models import Article
```

### UP035: Use Modern Type Hints
```python
# Bad (Python < 3.9 style)
from typing import List, Dict, Optional

def func(items: List[str]) -> Dict[str, int]:
    pass

# Good (Python 3.9+ style)
def func(items: list[str]) -> dict[str, int]:
    pass

# Optional is still fine, or use X | None
def func(value: str | None = None):
    pass
```

## Pre-Commit Workflow

Before committing code:

```bash
# 1. Format code
docker-compose exec backend ruff format /app

# 2. Fix auto-fixable issues
docker-compose exec backend ruff check /app --fix

# 3. Check for remaining issues
docker-compose exec backend ruff check /app

# 4. Run tests
docker-compose exec backend pytest
```

## Integration with Quality Gate Hook

The quality gate hook runs these checks automatically:
1. `ruff check .` - Must pass (exit 0)
2. `pytest` - Selected tests must pass

If either fails, you cannot complete the task until fixed.

## Ignoring Rules (Use Sparingly)

### Inline Ignore
```python
import unused_module  # noqa: F401
```

### File-Level Ignore
```python
# ruff: noqa: F401
```

### In pyproject.toml
```toml
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["F401"]  # Allow unused imports in tests
```

## Type Checking (Optional)

Pyright is configured but not enforced. To run:

```bash
cd backend
pyright app/
```

This is informational only - not part of the quality gate.
