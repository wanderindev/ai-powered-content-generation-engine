"""Mint, list and revoke bearer tokens for the PIC MCP server (#638).

The plaintext token is printed **once**, by ``create``; only its SHA-256 hex
digest is stored (``mcp_tokens.token_hash``). Lose it and mint a new one.
Revoking stamps ``revoked_at``; rows are never deleted.

Scopes (``McpScope``): ``read``, ``pipeline``, ``publish_request``. No scope
implies another, so a token that needs both reads and pipeline writes lists
both.

Check which database ``DATABASE_URL`` points at before running ``create`` or
``revoke`` (the dev backend container may be pointed at production).

Usage:
    python -m scripts.mcp_tokens create --name sentry-triage --scopes read
    python -m scripts.mcp_tokens create --name operator-laptop --scopes read,pipeline
    python -m scripts.mcp_tokens list
    python -m scripts.mcp_tokens revoke --name sentry-triage
"""

import argparse
import sys
from datetime import UTC, datetime

from app.core.database import SessionLocal
from app.mcp.auth import generate_token, hash_token
from app.models.enums import McpScope
from app.models.mcp_token import McpToken
from sqlalchemy.orm import Session


def parse_scopes(raw: str) -> list[str]:
    """Split ``read,pipeline`` into a de-duplicated, validated scope list."""
    scopes: list[str] = []
    for part in raw.split(","):
        scope = part.strip()
        if not scope:
            continue
        if scope not in McpScope.values():
            raise ValueError(f"unknown scope {scope!r}; valid: {', '.join(McpScope.values())}")
        if scope not in scopes:
            scopes.append(scope)
    if not scopes:
        raise ValueError("at least one scope is required")
    return scopes


def create_token(db: Session, *, name: str, scopes: list[str]) -> tuple[McpToken, str]:
    """Insert a token row and return it with the plaintext (shown once)."""
    if db.query(McpToken).filter(McpToken.name == name).first() is not None:
        raise ValueError(f"a token named {name!r} already exists")
    token = generate_token()
    row = McpToken(name=name, token_hash=hash_token(token), scopes=scopes)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, token


def list_tokens(db: Session) -> list[McpToken]:
    return db.query(McpToken).order_by(McpToken.id).all()


def revoke_token(db: Session, *, name: str) -> McpToken:
    """Stamp ``revoked_at`` (idempotent: an already-revoked row keeps its time)."""
    row = db.query(McpToken).filter(McpToken.name == name).first()
    if row is None:
        raise ValueError(f"no token named {name!r}")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()
    return row


def _fmt(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.mcp_tokens", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="mint a token and print it once")
    create.add_argument("--name", required=True)
    create.add_argument("--scopes", required=True, help="comma-separated, e.g. read,pipeline")
    sub.add_parser("list", help="list tokens (never shows the token itself)")
    revoke = sub.add_parser("revoke", help="revoke a token by name")
    revoke.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.command == "create":
            try:
                scopes = parse_scopes(args.scopes)
                row, token = create_token(db, name=args.name.strip(), scopes=scopes)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(
                f"Created MCP token {row.name!r} (id {row.id}) with scopes {','.join(row.scopes)}."
            )
            print("Token (shown once, store it now):")
            print(token)
        elif args.command == "list":
            for row in list_tokens(db):
                state = "revoked" if row.revoked_at else "active"
                print(
                    f"{row.id}\t{row.name}\t{','.join(row.scopes)}\t{state}\t"
                    f"created={_fmt(row.created_at)}\tlast_used={_fmt(row.last_used_at)}\t"
                    f"revoked={_fmt(row.revoked_at)}"
                )
        else:
            try:
                row = revoke_token(db, name=args.name.strip())
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"Revoked MCP token {row.name!r} at {_fmt(row.revoked_at)}.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
