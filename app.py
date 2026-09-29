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
from pdf_engine import (PAGE_MODES, POSITIONS, Handle, HandlesConfig, InsertSpec, JobConfig,
                        StampConfig, build_pdf, render_thumbnails, sample_pdf)
from processor import OUT_COL, process_sheet, read_sheet, safe_name, to_csv_bytes, to_excel_bytes

DEFAULT_FOLDER_ID = "1t_EqD-5qVsF-pv6GxHnF6lxs5N9yYPJH"
MOCKGROW_URL = "https://mockgrow.com"
PLAYSTORE_URL = "https://play.google.com/store/apps/details?id=com.zqegzu.timjvk&pcampaignid=web_share"

# Default handles: (name, icon file in assets/handles/, default link)
DEFAULT_HANDLES = [
    ("Play Store", "playstore.png", PLAYSTORE_URL),
    ("Instagram", "instagram.png", "https://www.instagram.com/mockgrow/"),
    ("YouTube", "youtube.png", "https://youtube.com/@mockgrow"),
    ("Telegram", "telegram.png", "https://t.me/MockGrow_Official"),
    ("Website", "website.png", MOCKGROW_URL),
]
ASSETS = Path(__file__).parent / "assets"

st.set_page_config(page_title="MockGrow PDF", page_icon="📕", layout="wide")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:  # no secrets file (e.g. local run)
        return default


def asset(name: str):
    """Look in assets/<name>, then assets/<file>, then the repo's main folder."""
    root = Path(__file__).parent
    file = Path(name).name
    for p in (ASSETS / name, ASSETS / file, root / file):
        if p.exists():
            return p.read_bytes()
    return None


def password_gate():
    pw = secret("APP_PASSWORD")
    if not pw or st.session_state.get("auth_ok"):
        return
    st.title("📕 MockGrow PDF")
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


def logo_controls() -> StampConfig:
    p = "logo"
    enabled = st.toggle("Enabled", value=True, key=f"{p}_on")
    up = st.file_uploader("Replace logo (PNG with transparent background works best)",
                          type=["png", "jpg", "jpeg", "webp"], key=f"{p}_img")
    img = up.getvalue() if up else asset("logo.png")
    if img:
        st.image(img, width=200, caption="Uploaded logo" if up else "Default: MockGrow logo")
    elif enabled:
        st.info("Upload a logo image.")
    link = st.text_input("Hyperlink (clicking the logo opens this)", MOCKGROW_URL, key=f"{p}_link")
    c1, c2 = st.columns(2)
    opacity = c1.slider("Opacity", 0.05, 1.0, 0.3, 0.05, key=f"{p}_op", help="Lower = more transparent")
    width = c2.slider("Size (% of page width)", 3, 100, 40, key=f"{p}_w")
    c1, c2 = st.columns(2)
    rotation = c1.slider("Rotation / orientation (°)", -180, 180, 0, 5, key=f"{p}_rot",
                         help="45 = diagonal, 90 = vertical. Counter-clockwise.")
    margin = c2.slider("Margin from edge (pt)", 0, 150, 24, key=f"{p}_m", help="72 pt = 1 inch")
    c1, c2 = st.columns(2)
    position = c1.selectbox("Position", POSITIONS, index=POSITIONS.index("center"), key=f"{p}_pos")
    page_mode = c2.selectbox("Apply on", list(PAGE_MODES), format_func=PAGE_MODES.get, key=f"{p}_pages")
    cx = cy = 50.0
    if position == "custom":
        c1, c2 = st.columns(2)
        cx = c1.slider("Horizontal (% from left)", 0, 100, 50, key=f"{p}_cx")
        cy = c2.slider("Vertical (% from top)", 0, 100, 50, key=f"{p}_cy")
    custom_pages = ""
    if page_mode == "custom":
        custom_pages = st.text_input("Pages", "1,last", key=f"{p}_cp",
                                     help="Final-document page numbers, e.g. 1,3,5-7,last")
    c1, c2, c3 = st.columns(3)
    tile = c1.checkbox("Tile across page", key=f"{p}_tile")
    gap = c2.slider("Tile gap %", 0, 200, 40, key=f"{p}_gap") if tile else 40
    behind = c3.checkbox("Behind content", key=f"{p}_behind",
                         help="Draw under text/images instead of on top")
    return StampConfig(image=img, enabled=enabled, link=link, opacity=opacity, width_pct=width,
                       rotation=rotation, position=position, margin_pt=margin,
                       custom_x_pct=cx, custom_y_pct=cy, pages=page_mode,
                       custom_pages=custom_pages, tile=tile, tile_gap_pct=gap,
                       behind_content=behind)


def handle_editor(key: str, name: str, default_img, default_link: str, expanded: bool,
                  editable_name: bool = False):
    """One handle row -> (order, Handle)."""
    with st.expander(name, expanded=expanded):
        c1, c2 = st.columns([1, 4])
        with c2:
            if editable_name:
                name = st.text_input("Name", name, key=f"{key}_name")
            enabled = st.checkbox("Show this icon", value=True, key=f"{key}_on")
            link = st.text_input("Link", default_link, key=f"{key}_link",
                                 placeholder=f"https://… your {name} link")
            a, b = st.columns([3, 1])
            up = a.file_uploader("Replace icon" if default_img else "Icon image",
                                 type=["png", "jpg", "jpeg", "webp"], key=f"{key}_img")
            order = b.number_input("Order", 1, 20, int(key.split("_")[-1]) + 1, key=f"{key}_ord")
        img = up.getvalue() if up else default_img
        with c1:
            if img:
                st.image(img, width=56)
        if enabled and not link.strip():
            st.caption("Add a link to include this icon.")
        if enabled and not img:
            st.caption("Upload an icon to include this handle.")
    return order, Handle(name=name, image=img, link=link, enabled=enabled)


def handles_controls() -> HandlesConfig:
    p = "hdl"
    enabled = st.toggle("Enabled", value=True, key=f"{p}_on")
    st.caption("Icons are placed together as one group; each icon links to its own URL. "
               "Icons without a link are skipped.")
    rows = []
    for k, (name, icon, link) in enumerate(DEFAULT_HANDLES):
        rows.append(handle_editor(f"{p}_{k}", name, asset(f"handles/{icon}"), link, expanded=(k == 0)))
    n_extra = st.number_input("Add more handles (Facebook, WhatsApp, LinkedIn…)", 0, 10, 0, key=f"{p}_n")
    base = len(DEFAULT_HANDLES)
    for k in range(int(n_extra)):
        rows.append(handle_editor(f"{p}_{base + k}", f"Custom handle {k + 1}", None, "",
                                  expanded=True, editable_name=True))
    handles = [h for _, h in sorted(rows, key=lambda r: r[0])]

    st.markdown("**Group layout**")
    c1, c2 = st.columns(2)
    layout = c1.radio("Arrange icons", ["horizontal", "vertical"], horizontal=True, key=f"{p}_lay",
                      format_func={"horizontal": "In a row", "vertical": "In a column"}.get)
    position = c2.selectbox("Position", POSITIONS, index=POSITIONS.index("bottom-right"), key=f"{p}_pos")
    c1, c2 = st.columns(2)
    size = c1.slider("Icon size (% of page width)", 2.0, 20.0, 5.0, 0.5, key=f"{p}_size")
    spacing = c2.slider("Space between icons (% of page width)", 0.0, 10.0, 1.5, 0.5, key=f"{p}_gap")
    c1, c2 = st.columns(2)
    opacity = c1.slider("Opacity", 0.05, 1.0, 1.0, 0.05, key=f"{p}_op")
    margin = c2.slider("Margin from edge (pt)", 0, 150, 24, key=f"{p}_m")
    cx, cy = 50.0, 95.0
    if position == "custom":
        c1, c2 = st.columns(2)
        cx = c1.slider("Horizontal (% from left)", 0, 100, 50, key=f"{p}_cx")
        cy = c2.slider("Vertical (% from top)", 0, 100, 95, key=f"{p}_cy")
    c1, c2 = st.columns(2)
    page_mode = c1.selectbox("Apply on", list(PAGE_MODES), format_func=PAGE_MODES.get, key=f"{p}_pages")
    behind = c2.checkbox("Behind content", key=f"{p}_behind")
    custom_pages = ""
    if page_mode == "custom":
        custom_pages = st.text_input("Pages", "1,last", key=f"{p}_cp")
    return HandlesConfig(handles=handles, enabled=enabled, layout=layout, icon_size_pct=size,
                         spacing_pct=spacing, opacity=opacity, position=position, margin_pt=margin,
                         custom_x_pct=cx, custom_y_pct=cy, pages=page_mode,
                         custom_pages=custom_pages, behind_content=behind)


@st.cache_data(show_spinner=False)
def _sample() -> bytes:
    return sample_pdf()


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
password_gate()
st.title("📕 MockGrow PDF")
st.caption("Add a hyperlinked logo, social / app handles and header / footer / custom pages to PDFs — "
           "one file or a whole sheet of Google Drive links.")

tab_brand, tab_pages, tab_single, tab_batch = st.tabs(
    ["🎨 Logo & Handles", "📑 Header / Footer / Inserts", "👁️ Preview & single file",
     "🚀 Batch: Sheet → Drive"])

with tab_brand:
    col1, col2 = st.columns(2, gap="large")
    with col1:
        st.subheader("Logo watermark")
        logo_cfg = logo_controls()
    with col2:
        st.subheader("Additional handles")
        handles_cfg = handles_controls()

    st.subheader("Live preview")
    try:
        preview = build_pdf([_sample()], JobConfig(logo=logo_cfg, handles=handles_cfg))
        st.image(render_thumbnails(preview, max_pages=1, dpi=80)[0][1], width=420,
                 caption="Sample A4 page — updates as you change settings")
    except Exception as e:  # noqa: BLE001
        st.error(f"Preview failed: {e}")

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
                logo=logo_cfg, handles=handles_cfg)

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
        st.caption("The logo and every handle icon are clickable links in the downloaded PDF.")

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
