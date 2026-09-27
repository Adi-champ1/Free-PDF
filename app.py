"""
PDF Brander — free, self-hosted alternative to iLovePDF for bulk branding.

Run locally:   streamlit run app.py
Host free:     push to GitHub -> deploy on https://share.streamlit.io
"""
from __future__ import annotations

import hmac
from pathlib import Path

import streamlit as st

from drive_client import DriveClient, describe_error
from pdf_engine import (PAGE_MODES, POSITIONS, InsertSpec, JobConfig, StampConfig,
                        build_pdf, default_playstore_badge, render_thumbnails)
from processor import OUT_COL, process_sheet, read_sheet, safe_name, to_csv_bytes, to_excel_bytes

DEFAULT_FOLDER_ID = "1t_EqD-5qVsF-pv6GxHnF6lxs5N9yYPJH"
MOCKGROW_URL = "https://mockgrow.com"
PLAYSTORE_URL = "https://play.google.com/store/apps/details?id=com.zqegzu.timjvk&pcampaignid=web_share"
ASSETS = Path(__file__).parent / "assets"

st.set_page_config(page_title="ADITYA MG PDF", page_icon="📄", layout="wide")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:  # no secrets file (e.g. local run)
        return default


def asset(name: str):
    p = ASSETS / name
    return p.read_bytes() if p.exists() else None


def password_gate():
    pw = secret("APP_PASSWORD")
    if not pw or st.session_state.get("auth_ok"):
        return
    st.title("📄 PDF Brander")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered, str(pw)):
            st.session_state.auth_ok = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()


@st.cache_resource(show_spinner="Connecting to Google Drive…")
def get_drive():
    oauth, sa = secret("google_oauth"), secret("gcp_service_account")
    if oauth:
        return DriveClient.from_oauth(**dict(oauth))
    if sa:
        return DriveClient.from_service_account(dict(sa))
    return None


def stamp_controls(prefix: str, *, link: str, opacity: float, width: int, position: str,
                   pages: str, fallback_img, fallback_note: str) -> StampConfig:
    enabled = st.toggle("Enabled", value=True, key=f"{prefix}_on")
    up = st.file_uploader("Image (PNG with transparent background works best)",
                          type=["png", "jpg", "jpeg", "webp"], key=f"{prefix}_img")
    img = up.getvalue() if up else fallback_img
    if img:
        st.image(img, width=160, caption="Uploaded image" if up else fallback_note)
    elif enabled:
        st.info("Upload an image to use this stamp.")

    link = st.text_input("Hyperlink (clicking the image opens this)", link, key=f"{prefix}_link")
    c1, c2 = st.columns(2)
    opacity = c1.slider("Opacity", 0.05, 1.0, opacity, 0.05, key=f"{prefix}_op",
                        help="Lower = more transparent")
    width = c2.slider("Size (% of page width)", 3, 100, width, key=f"{prefix}_w")
    c1, c2 = st.columns(2)
    rotation = c1.slider("Rotation / orientation (°)", -180, 180, 0, 5, key=f"{prefix}_rot",
                         help="45 = diagonal, 90 = vertical. Counter-clockwise.")
    margin = c2.slider("Margin from edge (pt)", 0, 150, 24, key=f"{prefix}_m",
                       help="72 pt = 1 inch")
    c1, c2 = st.columns(2)
    position = c1.selectbox("Position", POSITIONS, index=POSITIONS.index(position), key=f"{prefix}_pos")
    page_mode = c2.selectbox("Apply on", list(PAGE_MODES), index=list(PAGE_MODES).index(pages),
                             format_func=PAGE_MODES.get, key=f"{prefix}_pages")
    cx = cy = 50.0
    if position == "custom":
        c1, c2 = st.columns(2)
        cx = c1.slider("Horizontal (% from left)", 0, 100, 50, key=f"{prefix}_cx")
        cy = c2.slider("Vertical (% from top)", 0, 100, 50, key=f"{prefix}_cy")
    custom_pages = ""
    if page_mode == "custom":
        custom_pages = st.text_input("Pages", "1,last", key=f"{prefix}_cp",
                                     help="Final-document page numbers, e.g. 1,3,5-7,last")
    c1, c2, c3 = st.columns(3)
    tile = c1.checkbox("Tile across page", key=f"{prefix}_tile")
    gap = c2.slider("Tile gap %", 0, 200, 40, key=f"{prefix}_gap") if tile else 40
    behind = c3.checkbox("Behind content", key=f"{prefix}_behind",
                         help="Draw under text/images instead of on top")
    return StampConfig(image=img, enabled=enabled, link=link, opacity=opacity, width_pct=width,
                       rotation=rotation, position=position, margin_pt=margin,
                       custom_x_pct=cx, custom_y_pct=cy, pages=page_mode,
                       custom_pages=custom_pages, tile=tile, tile_gap_pct=gap,
                       behind_content=behind)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
password_gate()
st.title("📄 PDF Brander")
st.caption("Add hyperlinked logos, a Play Store badge and header / footer / custom pages to PDFs — "
           "one file or a whole sheet of Google Drive links.")

tab_brand, tab_pages, tab_single, tab_batch = st.tabs(
    ["🎨 Logo & Play Store", "📑 Header / Footer / Inserts", "👁️ Preview & single file",
     "🚀 Batch: Sheet → Drive"])

with tab_brand:
    col1, col2 = st.columns(2, gap="large")
    with col1:
        st.subheader("Logo watermark")
        logo_cfg = stamp_controls("logo", link=MOCKGROW_URL, opacity=0.3, width=30, position="center",
                                  pages="all", fallback_img=asset("logo.png"),
                                  fallback_note="assets/logo.png")
    with col2:
        st.subheader("Play Store badge")
        play_cfg = stamp_controls("play", link=PLAYSTORE_URL, opacity=1.0, width=18,
                                  position="bottom-right", pages="all",
                                  fallback_img=asset("playstore.png") or default_playstore_badge(),
                                  fallback_note="Default badge — upload your own to replace")

with tab_pages:
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.subheader("Header (added as page 1)")
        h = st.file_uploader("Header PDF", type="pdf", key="header")
        header_pdf = h.getvalue() if h else asset("header.pdf")
        if not h and header_pdf:
            st.caption("Using assets/header.pdf")
    with c2:
        st.subheader("Footer (added as last page)")
        f = st.file_uploader("Footer PDF", type="pdf", key="footer")
        footer_pdf = f.getvalue() if f else asset("footer.pdf")
        if not f and footer_pdf:
            st.caption("Using assets/footer.pdf")

    st.subheader("Custom pages in between")
    st.caption("Positions count pages of the ORIGINAL PDF. Examples: `2` → after page 2 · "
               "`2,5` → after pages 2 and 5 · `every 3` → after every 3 pages · `0` → before page 1")
    n_ins = st.number_input("Number of custom inserts", 0, 10, 0, key="n_ins")
    inserts = []
    for k in range(int(n_ins)):
        a, b = st.columns([3, 1])
        up = a.file_uploader(f"Insert #{k + 1} PDF", type="pdf", key=f"ins_{k}")
        after = b.text_input("Insert after page(s)", "1", key=f"ins_after_{k}")
        if up:
            inserts.append(InsertSpec(pdf=up.getvalue(), after=after))

job = JobConfig(header_pdf=header_pdf, footer_pdf=footer_pdf, inserts=inserts,
                logo=logo_cfg, playstore=play_cfg)

with tab_single:
    st.write("Test your settings on a local file, or brand a single PDF without Google Drive.")
    files = st.file_uploader("PDF(s) — several files are merged in upload order", type="pdf",
                             accept_multiple_files=True, key="single_pdfs")
    out_name = st.text_input("Output file name", "branded.pdf")
    if st.button("Build PDF", type="primary", disabled=not files):
        try:
            with st.spinner("Building…"):
                st.session_state.single_out = (safe_name(out_name, 0),
                                               build_pdf([x.getvalue() for x in files], job))
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not build the PDF: {e}")
    if "single_out" in st.session_state:
        name, data = st.session_state.single_out
        thumbs = render_thumbnails(data)
        for col, (pno, png) in zip(st.columns(len(thumbs)), thumbs):
            col.image(png, caption=f"Page {pno}")
        st.download_button("Download PDF", data, file_name=name, mime="application/pdf")
        st.caption("Links on the logo and badge are clickable in the downloaded PDF.")

with tab_batch:
    drive, drive_err = None, None
    try:
        drive = get_drive()
    except Exception as e:  # noqa: BLE001
        drive_err = describe_error(e)
    if drive_err:
        st.error(f"Google Drive login failed: {drive_err}. Check the credentials in Secrets (see README).")
    elif drive is None:
        st.warning("Google Drive is not connected yet. Add credentials to Secrets — see README, step 2.")

    sheet = st.file_uploader("Sheet (.xlsx or .csv) — Col A: final file name, Col B: Drive link(s) of the PDF",
                             type=["xlsx", "xls", "csv"], key="sheet")
    folder_id = st.text_input("Destination Drive folder ID", secret("DRIVE_FOLDER_ID", DEFAULT_FOLDER_ID))
    c1, c2, c3 = st.columns(3)
    workers = c1.slider("Files processed in parallel", 1, 8, 4)
    overwrite = c2.checkbox("Replace files with the same name in the folder")
    public = c3.checkbox("Anyone with the link can view the output", value=True)

    if sheet:
        try:
            df = read_sheet(sheet.getvalue(), sheet.name)
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read the sheet: {e}")
            df = None
        if df is not None:
            st.dataframe(df, hide_index=True)
            st.caption(f"{len(df)} rows. A cell in column B may contain several links "
                       "(comma or new-line separated) — they are merged in order.")
            if st.button("Process all rows", type="primary", disabled=drive is None):
                bar = st.progress(0.0, text="Starting…")

                def progress(done, total, i, res):
                    icon = "✅" if res.startswith("http") else ("⚠️" if res else "—")
                    bar.progress(done / total, text=f"{done}/{total} done · row {i + 1} {icon}")

                result = process_sheet(df, drive, job, folder_id.strip(), workers,
                                       overwrite, public, progress)
                st.session_state.batch_result = (result, sheet.name)

    if "batch_result" in st.session_state:
        result, src_name = st.session_state.batch_result
        ok = int(result[OUT_COL].str.startswith("http").sum())
        errors = int(result[OUT_COL].str.startswith("ERROR").sum())
        (st.success if errors == 0 else st.warning)(f"{ok} files created · {errors} errors")
        st.dataframe(result, hide_index=True,
                     column_config={OUT_COL: st.column_config.LinkColumn(OUT_COL)})
        stem = Path(src_name).stem
        d1, d2 = st.columns(2)
        d1.download_button("Download result (.xlsx)", to_excel_bytes(result), f"{stem}_output.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        d2.download_button("Download result (.csv)", to_csv_bytes(result), f"{stem}_output.csv",
                           mime="text/csv")
