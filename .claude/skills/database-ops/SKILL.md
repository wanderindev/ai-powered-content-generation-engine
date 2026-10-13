---
name: database-ops
description: Use this skill for database operations including querying production, creating backups, restoring data, and running migrations.
---

# Database Operations Procedure

## Production Database Details

| Setting | Value |
|---------|-------|
| Host | `<your-db-host>.db.ondigitalocean.com` |
| Port | `25060` |
| Database | `defaultdb` |
| User | `doadmin` |
| SSL | Required |

**Connection String:**
```
postgresql://doadmin:PASSWORD@<your-db-host>.db.ondigitalocean.com:25060/defaultdb?sslmode=require
```

## Querying Production Database

### Authenticate by sourcing `.env.prod-readonly`

Prod credentials live in `.env.prod-readonly` (chmod 600, gitignored). Source
it in your shell only when you actually need to touch prod — that way
`$DATABASE_URL` is unset in normal sessions and you can't accidentally hit
prod from a local script.

```bash
set -a; source .env.prod-readonly; set +a
```

After that, `psql` and `pg_dump` accept the URL directly — no need to pass
host / port / user / password as separate flags.

### Safe Read-Only Queries

```bash
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM articles WHERE status = 'APPROVED';"
```

### Common Queries

**Count articles by taxonomy:**
```sql
SELECT t.name, COUNT(a.id) as article_count
FROM articles a
JOIN categories c ON a.category_id = c.id
JOIN taxonomies t ON c.taxonomy_id = t.id
WHERE a.status = 'APPROVED'
GROUP BY t.name
ORDER BY article_count DESC;
```

**List series (articles with children):**
```sql
SELECT a.id, a.title, COUNT(child.id) as children_count
FROM articles a
LEFT JOIN articles child ON child.series_parent_id = a.id
WHERE a.series_parent_id IS NULL
GROUP BY a.id, a.title
HAVING COUNT(child.id) > 0
ORDER BY children_count DESC;
```

**Check article by slug pattern:**
```sql
SELECT id, title, status
FROM articles
WHERE LOWER(title) LIKE '%panama canal%'
LIMIT 10;
```

## Creating Backups

First, `set -a; source .env.prod-readonly; set +a` so `$DATABASE_URL` is set.

### Full Database Backup

```bash
mkdir -p ./backups
pg_dump "$DATABASE_URL" --no-owner --no-acl -F c \
  -f "./backups/pic_backup_$(date +%Y%m%d_%H%M%S).dump"
```

### Table-Specific Backup

```bash
pg_dump "$DATABASE_URL" -t articles --no-owner -F c \
  -f "./backups/articles_$(date +%Y%m%d_%H%M%S).dump"
```

## Restoring to Local Database

### Start Local PostgreSQL (Docker)

```bash
# Start the local development database
docker-compose up -d db

# Wait for it to be ready
sleep 5
```

### Restore from Backup

```bash
# Restore to local database
pg_restore \
  -h localhost \
  -p 5432 \
  -U postgres \
  -d panama_in_context \
  --no-owner \
  --clean \
  --if-exists \
  ./backups/pic_backup_TIMESTAMP.dump
```

## Alembic Migrations

### Configuration

Alembic is configured in `backend/alembic/`:
- `alembic.ini` - Main configuration
- `env.py` - Environment setup
- `versions/` - Migration files

### Common Commands

All Alembic commands run inside the Docker container:

```bash
# Check current revision
docker-compose exec backend alembic current

# Show migration history
docker-compose exec backend alembic history

# Create new migration (auto-generate from models)
docker-compose exec backend alembic revision --autogenerate -m "Add translation columns"

# Create empty migration (manual)
docker-compose exec backend alembic revision -m "Custom migration"

# Apply all pending migrations
docker-compose exec backend alembic upgrade head

# Apply specific number of migrations
docker-compose exec backend alembic upgrade +1

# Rollback one migration
docker-compose exec backend alembic downgrade -1

# Rollback to specific revision
docker-compose exec backend alembic downgrade abc123

# Preview migration SQL without executing
docker-compose exec backend alembic upgrade head --sql
```

### Migration File Template

```python
"""Add translation columns

Revision ID: abc123
Revises: def456
Create Date: 2024-01-01 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'abc123'
down_revision = 'def456'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('articles', sa.Column('title_es', sa.Text(), nullable=True))
    op.add_column('articles', sa.Column('title_en', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('articles', 'title_en')
    op.drop_column('articles', 'title_es')
```

## Safety Rules

1. **NEVER run DELETE or UPDATE on production without WHERE clause**
2. **ALWAYS create backup before running migrations on production**
3. **TEST migrations on local database first**
4. **Use transactions for multi-step operations**

## Workflow: Safe Production Migration

1. Create backup of production database
2. Test migration on local with production data copy
3. Review migration SQL (`docker-compose exec backend alembic upgrade head --sql`)
4. Apply to production during low-traffic period
5. Verify application still works
6. Keep backup for 7 days minimum
