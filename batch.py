"""
Command-line batch runner — fastest option for big sheets (runs on your PC,
no browser needed).

    python batch.py input.xlsx                  # uses config.json + .streamlit/secrets.toml
    python batch.py input.csv -c my_config.json -o result.xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import fields
from pathlib import Path

from drive_client import DriveClient
from pdf_engine import Handle, HandlesConfig, InsertSpec, JobConfig, StampConfig
from processor import OUT_COL, process_sheet, read_sheet, to_csv_bytes, to_excel_bytes

BASE = Path(__file__).parent
STAMP_FIELDS = {f.name for f in fields(StampConfig)} - {"image"}
HANDLES_FIELDS = {f.name for f in fields(HandlesConfig)} - {"handles"}


def load(path):
    if not path:
        return None
    p = BASE / path
    if not p.exists():
        print(f"(skipping {path}: file not found)")
        return None
    return p.read_bytes()


def stamp_from(d):
    if not d:
        return None
    return StampConfig(image=load(d.get("image")), **{k: v for k, v in d.items() if k in STAMP_FIELDS})


def handles_from(d):
    if not d:
        return None
    items = [Handle(name=h.get("name", ""), image=load(h.get("image")), link=h.get("link", ""),
                    enabled=h.get("enabled", True)) for h in d.get("handles", [])]
    return HandlesConfig(handles=items, **{k: v for k, v in d.items() if k in HANDLES_FIELDS})


def drive_from_secrets(path: Path) -> DriveClient:
    s = tomllib.loads(path.read_text())
    if "google_oauth" in s:
        return DriveClient.from_oauth(**s["google_oauth"])
    if "gcp_service_account" in s:
        return DriveClient.from_service_account(s["gcp_service_account"])
    sys.exit(f"No [google_oauth] or [gcp_service_account] section in {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet")
    ap.add_argument("-c", "--config", default="config.json")
    ap.add_argument("-s", "--secrets", default=".streamlit/secrets.toml")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()

    cfg = json.loads((BASE / args.config).read_text())
    job = JobConfig(
        header_pdf=load(cfg.get("header_pdf")),
        footer_pdf=load(cfg.get("footer_pdf")),
        inserts=[InsertSpec(pdf=load(i["pdf"]), after=str(i.get("after", "1")))
                 for i in cfg.get("inserts", [])],
        logo=stamp_from(cfg.get("logo")),
        handles=handles_from(cfg.get("handles")),
    )
    drive = drive_from_secrets(BASE / args.secrets)
    sheet = Path(args.sheet)
    df = read_sheet(sheet.read_bytes(), sheet.name)
    print(f"{len(df)} rows -> Drive folder {cfg['drive_folder_id']}")

    def progress(done, total, i, res):
        print(f"[{done}/{total}] row {i + 1}: {res or '(empty row)'}")

    result = process_sheet(df, drive, job, cfg["drive_folder_id"], cfg.get("workers", 4),
                           cfg.get("overwrite", False), cfg.get("make_public", True), progress)
    out = Path(args.output or sheet.with_name(f"{sheet.stem}_output.xlsx"))
    out.write_bytes(to_csv_bytes(result) if out.suffix.lower() == ".csv" else to_excel_bytes(result))
    ok = result[OUT_COL].str.startswith("http").sum()
    print(f"\nDone: {ok}/{len(result)} files created. Result saved to {out}")


if __name__ == "__main__":
    main()
