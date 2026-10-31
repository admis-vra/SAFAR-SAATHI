"""One-off cleanup of the raw SafarSaathi bus schedule CSV export."""
import csv
import re

SRC = r"C:\Users\PRINCE\Downloads\SafarSaathi_Bus_Schedule.csv"
DST = r"C:\Users\PRINCE\Desktop\SAFAR SAATHI\backend\import_data\bus_schedule.csv"


def normalize_time(token: str) -> str:
    token = token.strip()
    m = re.match(r"(\d{1,2})[.:](\d{2})\s*([AP]M)?", token, re.IGNORECASE)
    if not m:
        return token
    hh, mm, ap = m.groups()
    ap = (ap or "").upper()
    return f"{hh}:{mm} {ap}".strip()


def split_shift(raw: str):
    raw = re.sub(r"\s+", " ", raw.strip())
    m = re.match(r"([\d.:]+\s*[AP]M)\s+(No Service|[\d.:]+\s*[AP]M)", raw, re.IGNORECASE)
    if not m:
        return normalize_time(raw), ""
    start, end = m.groups()
    if end.strip().lower() == "no service":
        return normalize_time(start), "No Service"
    return normalize_time(start), normalize_time(end)


def clean_route(raw: str) -> str:
    raw = raw.replace("\n", " ").replace("\r", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    stops = [s.strip() for s in raw.split("-") if s.strip()]
    return " - ".join(stops)


def main():
    with open(SRC, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    header = rows[0]
    out_header = [
        # Both runs are pickups towards college (e.g. 7:00 AM and 11:00 AM); "No Service" = first run only.
        "S.No.", "Reg. No.", "Bus No.", "1st Run Start", "2nd Run Start",
        "Driver Name", "Contact No.", "Route", "Seats",
    ]
    out_rows = []
    for i, row in enumerate(rows[1:], start=1):
        if not any(c.strip() for c in row):
            continue
        sno, reg_no, bus_no, shift, driver, contact, route, seats = row
        morning, evening = split_shift(shift)
        out_rows.append([
            str(i),
            reg_no.strip(),
            bus_no.strip(),
            morning,
            evening,
            re.sub(r"\s+", " ", driver.strip()),
            contact.strip(),
            clean_route(route),
            seats.strip(),
        ])

    with open(DST, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(out_header)
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows to {DST}")


if __name__ == "__main__":
    main()
