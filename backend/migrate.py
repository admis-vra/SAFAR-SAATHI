"""Apply supabase/migrations/*.sql in order, recording each one so it runs only once.

Usage (from backend/):
  .venv/Scripts/python migrate.py                     # uses DATABASE_URL from backend/.env
  .venv/Scripts/python migrate.py --local-auth-stub   # plain local Postgres only (no Supabase auth schema)
"""
import argparse
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

HERE = Path(__file__).parent
MIGRATIONS = HERE.parent / "supabase" / "migrations"

LOCAL_AUTH_STUB = """
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key);
"""


async def apply(dsn: str, local_auth_stub: bool = False, only: str | None = None) -> list[str]:
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        has_auth = await conn.fetchval("select to_regclass('auth.users') is not null")
        if not has_auth:
            if not local_auth_stub:
                raise SystemExit("auth.users not found. On Supabase this exists; for plain local Postgres "
                                 "re-run with --local-auth-stub.")
            await conn.execute(LOCAL_AUTH_STUB)
            # Supabase already searches "extensions" (where PostGIS lives); plain Postgres needs it set.
            dbname = await conn.fetchval("select current_database()")
            await conn.execute(f'alter database "{dbname}" set search_path = "$user", public, extensions')
            await conn.execute('set search_path = "$user", public, extensions')
        await conn.execute("create table if not exists schema_migrations "
                           "(version text primary key, applied_at timestamptz not null default now())")
        done = {r["version"] for r in await conn.fetch("select version from schema_migrations")}
        applied = []
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done or (only and path.stem != only):
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("insert into schema_migrations (version) values ($1)", path.stem)
            applied.append(path.stem)
        return applied
    finally:
        await conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-auth-stub", action="store_true")
    ap.add_argument("--only", metavar="NAME", help="apply just this migration (file name without .sql)")
    args = ap.parse_args()
    load_dotenv(HERE / ".env")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Set DATABASE_URL in backend/.env")
    applied = asyncio.run(apply(dsn, args.local_auth_stub, args.only))
    print("applied:", ", ".join(applied) if applied else "nothing (already up to date)")


if __name__ == "__main__":
    main()
