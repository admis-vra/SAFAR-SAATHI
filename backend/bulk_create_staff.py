"""Create N placeholder driver or conductor accounts for demo/testing (not a real roster).

Login ID  = driver1, driver2, ... driverN (or conductor1, conductor2, ... for conductors).
Password  = a randomly generated (but deterministic per ID, so reruns are stable) staff name,
            lowercased with spaces removed, e.g. "Ramesh Negi" -> "rameshnegi".

Each account is a real Supabase Auth user with app_metadata {"role": ..., "bus_id": ...,
"name": ...} set directly at creation time, so it's immediately usable -- no manual dashboard +
SQL step needed (unlike the single-account flow in README.md). bus_id cycles through your
existing buses table so every driver/conductor is assigned to a real bus.

Usage (from backend/, with the venv active):
  python bulk_create_staff.py driver 90
  python bulk_create_staff.py conductor 90

Needs in backend/.env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (see bulk_import_passengers.py),
and DATABASE_URL (used once, read-only, to list real bus ids).

Safe to re-run: an ID that's already registered is left alone (skipped), and because the name
per ID is deterministic, the printed credentials always match what's actually in Supabase.
"""
import asyncio
import csv
import os
import random
import sys
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

EMAIL_DOMAIN = "staff.safarsaathi.local"
ROLES = ("driver", "conductor")

FIRST_NAMES = [
    "Ramesh", "Suresh", "Mahesh", "Rajesh", "Vinod", "Pradeep", "Dinesh", "Mukesh",
    "Sanjay", "Vikram", "Anil", "Sunil", "Deepak", "Naresh", "Ravi", "Ashok",
    "Vijay", "Manoj", "Rakesh", "Satish",
]
LAST_NAMES = [
    "Negi", "Bisht", "Rawat", "Rana", "Rathore", "Panwar", "Bhatt", "Chauhan",
    "Thapa", "Gusain", "Kandari", "Sati", "Naithani", "Dobhal", "Pundir", "Rautela",
]


def random_name(role: str, i: int) -> str:
    # Seeded on (role, i) so driverN and conductorN don't get the same name by accident, and the
    # same ID always gets the same name across reruns. driver's seed is plain `i` for backwards
    # compatibility with names already generated/shared before conductors existed.
    rng = random.Random(i if role == "driver" else i + 100_000)
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def password_for(name: str) -> str:
    base = "".join(name.lower().split())
    return base if len(base) >= 6 else base + "1" * (6 - len(base))


async def main(role: str, count: int):
    load_dotenv(Path(__file__).parent / ".env")
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        bus_ids = [r["id"] for r in await conn.fetch("select id from buses order by id")]
    finally:
        await conn.close()
    if not bus_ids:
        raise SystemExit("No buses found -- add buses before creating staff.")

    out_path = Path(__file__).parent / f"{role}_credentials.csv"
    created = skipped = failed = 0
    with open(out_path, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.writer(out_f)
        writer.writerow(["login_id", "password", "name", "bus_id"])
        async with httpx.AsyncClient(base_url=f"{supabase_url}/auth/v1", headers=headers, timeout=30) as client:
            for i in range(1, count + 1):
                login_id = f"{role}{i}"
                name = random_name(role, i)
                password = password_for(name)
                bus_id = bus_ids[(i - 1) % len(bus_ids)]
                email = f"{login_id}@{EMAIL_DOMAIN}"

                resp = await client.post("/admin/users", json={
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "app_metadata": {"role": role, "bus_id": bus_id, "name": name},
                })
                if resp.status_code in (200, 201):
                    created += 1
                elif resp.status_code in (400, 422) and "already" in resp.text.lower():
                    skipped += 1
                else:
                    print(f"FAILED {email}: {resp.status_code} {resp.text}")
                    failed += 1
                    continue

                writer.writerow([login_id, password, name, bus_id])

    print(f"\n{created} created, {skipped} already existed, {failed} failed, {count} total")
    print(f"Credentials written to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ROLES or not sys.argv[2].isdigit():
        raise SystemExit(f"usage: python bulk_create_staff.py <{'|'.join(ROLES)}> <count>")
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
