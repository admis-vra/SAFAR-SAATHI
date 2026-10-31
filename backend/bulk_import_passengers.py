"""Bulk-create passenger accounts from a (name, student_id) CSV.

Login ID  = the student's ID, exactly as given (e.g. 25101280584).
Password  = their full name, lowercased with spaces removed (e.g. "Siddhant Singh Rawat" -> "siddhantsinghrawat").
            Supabase won't allow a password under 6 characters (this can't be configured lower),
            so a short single-word name is padded with trailing 1s (e.g. "Isha" -> "isha11").
            The exact password used is always in the generated passenger_credentials.csv.

Supabase Auth requires a real email per account, so each user gets a synthetic one derived
from their student ID (e.g. 25101280584@passenger.safarsaathi.local) -- see
PASSENGER_EMAIL_DOMAIN in frontend/web/src/pages/Login.jsx, which builds the same address
from the student ID the student types in, so no backend lookup is needed at login time.

Usage (from backend/, with the venv active):
  python bulk_import_passengers.py import_data/passengers.csv

Only talks to Supabase's Admin REST API, not your database, so it needs just two things in
backend/.env:
  SUPABASE_URL=...                Already required by the rest of the backend.
  SUPABASE_SERVICE_ROLE_KEY=...   Project Settings > API > service_role secret.
                                  NEVER put this in the frontend or commit it.

Safe to re-run: an email that's already registered is left alone (skipped).
"""
import asyncio
import csv
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

EMAIL_DOMAIN = "passenger.safarsaathi.local"  # must match PASSENGER_EMAIL_DOMAIN in Login.jsx


def title_case(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split())


def password_for(name: str) -> str:
    base = "".join(name.lower().split())
    # Supabase enforces a hard floor of 6 characters (can't be configured lower), so a short
    # single-word name gets padded with trailing 1s. The exact password is always in the
    # generated passenger_credentials.csv, so students never need to guess the padding.
    return base if len(base) >= 6 else base + "1" * (6 - len(base))


async def main(csv_path: str):
    load_dotenv(Path(__file__).parent / ".env")
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("name", "").strip() and r.get("student_id", "").strip()]

    seen_ids = set()
    for r in rows:
        sid = r["student_id"].strip()
        if sid in seen_ids:
            raise SystemExit(f"Duplicate student_id in CSV: {sid} -- student IDs must be unique, fix the CSV first.")
        seen_ids.add(sid)

    out_path = Path(csv_path).with_name("passenger_credentials.csv")
    created = skipped = failed = 0
    with open(out_path, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.writer(out_f)
        writer.writerow(["login_id", "password"])
        async with httpx.AsyncClient(base_url=f"{supabase_url}/auth/v1", headers=headers, timeout=30) as client:
            for i, r in enumerate(rows, 1):
                name = title_case(r["name"])
                student_id = r["student_id"].strip()
                password = password_for(name)
                email = f"{student_id}@{EMAIL_DOMAIN}"

                resp = await client.post("/admin/users", json={
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"name": name, "student_id": student_id},
                })
                if resp.status_code in (200, 201):
                    created += 1
                elif resp.status_code in (400, 422) and "already" in resp.text.lower():
                    skipped += 1
                else:
                    print(f"FAILED {email}: {resp.status_code} {resp.text}")
                    failed += 1
                    continue

                writer.writerow([student_id, password])

                if i % 100 == 0:
                    print(f"...{i}/{len(rows)}")

    print(f"\n{created} created, {skipped} already existed, {failed} failed, {len(rows)} total rows")
    print(f"Credentials written to {out_path} -- this is as sensitive as the source spreadsheet, handle it the same way.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python bulk_import_passengers.py <csv path>")
    asyncio.run(main(sys.argv[1]))
