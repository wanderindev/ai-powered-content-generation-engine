# Archived pre-baseline migrations

These 21 migrations predate the Alembic baseline introduced in #3. They tracked
deltas on top of the implicit `Base.metadata.create_all()` schema that the
production database was originally bootstrapped from (Flask era) — there was
never a migration that *created* the base schema, so `alembic upgrade head`
against an empty database could not reproduce prod.

`versions/0000_baseline_schema.py` now captures the full production schema as
the initial revision (`down_revision = None`). These files are kept here, out
of Alembic's version path, for historical reference only. Production and
existing local databases were stamped at `0000_baseline` rather than re-run.

Do not move these back into `versions/` — they reference tables the baseline
already creates and would no longer chain correctly.
