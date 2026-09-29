"""Reads the sheet (Col A = final name, Col B = Drive link(s)), runs every row in
parallel and returns the same sheet with Col C = link of the final PDF."""
from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import pandas as pd

from drive_client import describe_error, extract_file_id, split_links
from pdf_engine import JobConfig, build_pdf

OUT_COL = "Final PDF Link"


def read_sheet(data: bytes, filename: str) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        raw = pd.read_excel(io.BytesIO(data), header=None, dtype=str)
    else:
        raw = pd.read_csv(io.BytesIO(data), header=None, dtype=str, encoding="utf-8-sig",
                          skip_blank_lines=True)
        if raw.shape[1] < 2:  # maybe ';' or tab separated
            raw = pd.read_csv(io.BytesIO(data), header=None, dtype=str, encoding="utf-8-sig",
                              sep=None, engine="python")
    raw = raw.fillna("")
    if raw.shape[1] < 2 or raw.empty:
        raise ValueError("The sheet needs 2 columns: A = final file name, B = Drive link(s) of the PDF.")
    raw = raw.iloc[:, :2].apply(lambda col: col.str.strip())

    first_b = raw.iat[0, 1]
    has_header = not any(extract_file_id(x) for x in split_links(first_b))
    if has_header:
        headers = [raw.iat[0, 0] or "File Name", raw.iat[0, 1] or "PDF Links"]
        if headers[0] == headers[1]:
            headers[1] += " (B)"
        df = raw.iloc[1:].reset_index(drop=True)
    else:
        headers, df = ["File Name", "PDF Links"], raw
    df.columns = headers
    return df


def safe_name(name: str, idx: int) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip().strip(".")
    if not name:
        name = f"output_{idx + 1}"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def process_sheet(df: pd.DataFrame, drive, job: JobConfig, folder_id: str, workers: int = 4,
                  overwrite: bool = False, make_public: bool = True,
                  on_progress: Optional[Callable[[int, int, int, str], None]] = None) -> pd.DataFrame:
    results = [""] * len(df)
    job.prepared()  # prepare logo/badge once for the whole batch

    def work(i: int) -> str:
        name, cell = str(df.iat[i, 0]).strip(), str(df.iat[i, 1]).strip()
        if not name and not cell:
            return ""
        links = split_links(cell)
        if not links:
            return "ERROR: no Drive link in column B"
        ids = []
        for link in links:
            fid = extract_file_id(link)
            if not fid:
                return f"ERROR: not a Google Drive link: {cell[:80]}"
            ids.append(fid)
        try:
            sources = [drive.download_pdf(fid) for fid in ids]
            final_pdf = build_pdf(sources, job)
            return drive.upload_pdf(safe_name(name, i), final_pdf, folder_id, overwrite, make_public)
        except Exception as e:  # noqa: BLE001 — one bad row must not stop the batch
            return f"ERROR: {describe_error(e)}"

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futures = {ex.submit(work, i): i for i in range(len(df))}
        for done, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            results[i] = fut.result()
            if on_progress:
                on_progress(done, len(df), i, results[i])

    out = df.copy()
    out[OUT_COL] = results
    return out


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Output")
        ws = writer.sheets["Output"]
        for row in range(2, len(df) + 2):
            cell = ws.cell(row=row, column=3)
            if isinstance(cell.value, str) and cell.value.startswith("http"):
                cell.hyperlink = cell.value
                cell.style = "Hyperlink"
        for col, width in zip("ABC", (40, 70, 70)):
            ws.column_dimensions[col].width = width
    return buf.getvalue()


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")
