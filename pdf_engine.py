"""
Core PDF engine — merge PDFs, insert header / footer / custom pages, stamp a
hyperlinked logo watermark and a group of hyperlinked "handles" (Play Store,
Instagram, YouTube, Telegram, Website, ...) onto pages.

Built only on free, open-source libraries: PyMuPDF + Pillow.
"""
from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pymupdf as fitz  # PyMuPDF
from PIL import Image

# PyMuPDF is NOT thread-safe. All PDF work goes through this lock, while
# downloads/uploads (the slow part) still run in parallel threads.
PDF_LOCK = threading.Lock()

MAX_STAMP_PX = 1600  # downscale huge logos -> faster + smaller output files

POSITIONS = [
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
    "custom",
]

PAGE_MODES = {
    "all": "All pages",
    "original": "Only original PDF pages",
    "inserted": "Only inserted pages (header / footer / custom)",
    "first": "First page only",
    "last": "Last page only",
    "custom": "Custom pages (e.g. 1,3,5-7,last)",
}


# --------------------------------------------------------------------------- #
# Config objects
# --------------------------------------------------------------------------- #
@dataclass
class StampConfig:
    """Settings for the logo watermark."""
    image: Optional[bytes] = None
    enabled: bool = True
    link: str = ""
    opacity: float = 0.3          # 0.0 (invisible) … 1.0 (solid)
    width_pct: float = 20.0       # stamp width as % of page width
    rotation: float = 0.0         # degrees, counter-clockwise
    position: str = "center"      # one of POSITIONS
    margin_pt: float = 24.0       # distance from page edge (points, 72 pt = 1 inch)
    custom_x_pct: float = 50.0    # used when position == "custom" (centre of stamp)
    custom_y_pct: float = 50.0
    pages: str = "all"            # one of PAGE_MODES
    custom_pages: str = ""
    tile: bool = False            # repeat across the whole page
    tile_gap_pct: float = 40.0
    behind_content: bool = False  # draw under the page content instead of on top


@dataclass
class PreparedStamp:
    png: bytes
    aspect: float  # height / width


@dataclass
class InsertSpec:
    """A PDF inserted between original pages. `after` examples:
    '2' | '2,5' | 'every 3' | '0' (before first original page) | 'last'."""
    pdf: bytes
    after: str = "1"


@dataclass
class Handle:
    """One icon in the Additional Handles group."""
    name: str
    image: Optional[bytes]
    link: str
    enabled: bool = True


@dataclass
class HandlesConfig:
    """Additional Handles: several icons laid out together, each with its own link."""
    handles: List[Handle] = field(default_factory=list)
    enabled: bool = True
    layout: str = "horizontal"    # "horizontal" (row) or "vertical" (column)
    icon_size_pct: float = 5.0    # icon height as % of page width
    spacing_pct: float = 1.5      # gap between icons as % of page width
    opacity: float = 1.0
    position: str = "bottom-right"
    margin_pt: float = 24.0
    custom_x_pct: float = 50.0
    custom_y_pct: float = 95.0
    pages: str = "all"
    custom_pages: str = ""
    behind_content: bool = False


@dataclass
class JobConfig:
    header_pdf: Optional[bytes] = None
    footer_pdf: Optional[bytes] = None
    inserts: List[InsertSpec] = field(default_factory=list)
    logo: Optional[StampConfig] = None
    handles: Optional[HandlesConfig] = None
    _prepared: Optional[tuple] = field(default=None, repr=False)

    def prepared(self):
        """Pre-process all images once per batch -> (logo_prep | None, [(Handle, prep), ...])."""
        if self._prepared is None:
            logo = None
            if self.logo and self.logo.enabled and self.logo.image:
                logo = prepare_image(self.logo.image, self.logo.rotation, self.logo.opacity)
            handles = []
            if self.handles and self.handles.enabled:
                for h in self.handles.handles:
                    if h.enabled and h.image and normalize_url(h.link):
                        handles.append((h, prepare_image(h.image, 0, self.handles.opacity)))
            self._prepared = (logo, handles)
        return self._prepared


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url):
        url = "https://" + url
    return url


def prepare_image(image: bytes, rotation: float = 0.0, opacity: float = 1.0) -> PreparedStamp:
    """Rotate, apply transparency and trim an image with Pillow."""
    img = Image.open(io.BytesIO(image))
    img.load()
    img = img.convert("RGBA")
    if max(img.size) > MAX_STAMP_PX:
        img.thumbnail((MAX_STAMP_PX, MAX_STAMP_PX), Image.Resampling.LANCZOS)
    if rotation % 360:
        img = img.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=True)
    bbox = img.getchannel("A").getbbox()  # trim fully transparent borders
    if bbox:
        img = img.crop(bbox)
    opacity = min(max(float(opacity), 0.01), 1.0)
    if opacity < 1.0:
        alpha = img.getchannel("A").point(lambda v: round(v * opacity))
        img.putalpha(alpha)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return PreparedStamp(png=buf.getvalue(), aspect=img.height / img.width)


def sample_pdf() -> bytes:
    """A4 page with dummy text, used for the live preview."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((56, 90), "Sample page", fontsize=26)
    page.insert_textbox(fitz.Rect(56, 120, 539, 780),
                        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 45,
                        fontsize=11, color=(0.35, 0.35, 0.35))
    data = doc.tobytes()
    doc.close()
    return data


# --------------------------------------------------------------------------- #
# Page-spec parsing
# --------------------------------------------------------------------------- #
def parse_page_list(spec: str, n: int) -> Set[int]:
    """'1,3,5-7,last' -> {0,2,4,5,6,n-1} (0-based)."""
    out: Set[int] = set()
    for part in re.split(r"[,\s]+", (spec or "").strip().lower()):
        if not part:
            continue
        if part in ("last", "-1"):
            out.add(n - 1)
        elif part == "first":
            out.add(0)
        elif m := re.fullmatch(r"(\d+)-(\d+|last)", part):
            a = int(m[1])
            b = n if m[2] == "last" else int(m[2])
            out.update(range(a - 1, min(b, n)))
        elif part.isdigit():
            out.add(int(part) - 1)
    return {i for i in out if 0 <= i < n}


def parse_after_spec(spec: str, n: int) -> List[int]:
    """Where to put a custom insert, counted in ORIGINAL pages (0..n)."""
    s = (spec or "").strip().lower()
    if m := re.fullmatch(r"every\s*(\d+)", s):
        step = int(m[1])
        return list(range(step, n, step)) if step > 0 else []
    res = []
    for part in re.split(r"[,\s]+", s):
        if part in ("last", "end"):
            res.append(n)
        elif part.isdigit():
            res.append(min(int(part), n))
    return res


def select_pages(mode: str, custom: str, kinds: List[str]) -> Set[int]:
    n = len(kinds)
    if n == 0:
        return set()
    if mode == "first":
        return {0}
    if mode == "last":
        return {n - 1}
    if mode == "original":
        return {i for i, k in enumerate(kinds) if k == "original"}
    if mode == "inserted":
        return {i for i, k in enumerate(kinds) if k != "original"}
    if mode == "custom":
        return parse_page_list(custom, n)
    return set(range(n))


# --------------------------------------------------------------------------- #
# Stamping
# --------------------------------------------------------------------------- #
def _place(page_rect: fitz.Rect, w: float, h: float, position: str, margin: float,
           cx_pct: float, cy_pct: float) -> fitz.Rect:
    """Top-left corner for a w x h box at the given position."""
    pw, ph = page_rect.width, page_rect.height
    if position == "custom":
        x0 = pw * cx_pct / 100.0 - w / 2
        y0 = ph * cy_pct / 100.0 - h / 2
    else:
        v, hz = ("middle", "center") if position == "center" else position.split("-")
        x0 = {"left": margin, "center": (pw - w) / 2, "right": pw - w - margin}[hz]
        y0 = {"top": margin, "middle": (ph - h) / 2, "bottom": ph - h - margin}[v]
    return fitz.Rect(page_rect.x0 + x0, page_rect.y0 + y0, page_rect.x0 + x0 + w, page_rect.y0 + y0 + h)


def stamp_rects(page_rect: fitz.Rect, cfg: StampConfig, aspect: float) -> List[fitz.Rect]:
    pw, ph = page_rect.width, page_rect.height
    w = pw * cfg.width_pct / 100.0
    h = w * aspect
    if h > ph:  # never taller than the page
        h = ph
        w = h / aspect
    m = cfg.margin_pt

    if cfg.tile:
        gap = cfg.tile_gap_pct / 100.0
        sx, sy = w * (1 + gap), h * (1 + gap)
        rects, y = [], page_rect.y0 + m
        while y < page_rect.y1 and len(rects) < 500:
            x = page_rect.x0 + m
            while x < page_rect.x1 and len(rects) < 500:
                rects.append(fitz.Rect(x, y, x + w, y + h))
                x += sx
            y += sy
        return rects
    return [_place(page_rect, w, h, cfg.position, m, cfg.custom_x_pct, cfg.custom_y_pct)]


def handle_rects(page_rect: fitz.Rect, cfg: HandlesConfig, aspects: List[float]) -> List[fitz.Rect]:
    """Lay the handle icons out as one group (row or column) and position the group."""
    pw = page_rect.width
    size = pw * cfg.icon_size_pct / 100.0
    gap = pw * cfg.spacing_pct / 100.0
    if cfg.layout == "vertical":
        widths = [min(size / a, size * 1.6) for a in aspects]  # same width-ish column
        heights = [w * a for w, a in zip(widths, aspects)]
        gw, gh = max(widths), sum(heights) + gap * (len(aspects) - 1)
    else:
        heights = [size] * len(aspects)                          # same height row
        widths = [size / a for a in aspects]
        gw, gh = sum(widths) + gap * (len(aspects) - 1), size
    group = _place(page_rect, gw, gh, cfg.position, cfg.margin_pt, cfg.custom_x_pct, cfg.custom_y_pct)
    rects, x, y = [], group.x0, group.y0
    for w, h in zip(widths, heights):
        if cfg.layout == "vertical":
            ox = group.x0 + (gw - w) / 2
            rects.append(fitz.Rect(ox, y, ox + w, y + h))
            y += h + gap
        else:
            rects.append(fitz.Rect(x, y, x + w, y + h))
            x += w + gap
    return rects


class _ImageInserter:
    """Embeds each image once per document and re-uses it on every page (small files)."""

    def __init__(self):
        self.xrefs: Dict[int, int] = {}

    def put(self, page: fitz.Page, rect: fitz.Rect, prep: PreparedStamp, overlay: bool, uri: str) -> None:
        if not rect.intersects(page.rect):
            return
        key = id(prep)
        if key in self.xrefs:
            page.insert_image(rect, xref=self.xrefs[key], overlay=overlay, keep_proportion=True)
        else:
            self.xrefs[key] = page.insert_image(rect, stream=prep.png, overlay=overlay, keep_proportion=True)
        if uri:
            page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(rect).intersect(page.rect), "uri": uri})


def _unrotate(page: fitz.Page) -> None:
    if page.rotation:
        page.remove_rotation()  # makes positions behave on rotated pages


def apply_logo(doc, pages: Set[int], cfg: StampConfig, prep: PreparedStamp, ins: _ImageInserter) -> None:
    uri = normalize_url(cfg.link)
    for i in sorted(pages):
        page = doc[i]
        _unrotate(page)
        for r in stamp_rects(page.rect, cfg, prep.aspect):
            ins.put(page, r, prep, not cfg.behind_content, uri)


def apply_handles(doc, pages: Set[int], cfg: HandlesConfig, items, ins: _ImageInserter) -> None:
    aspects = [p.aspect for _, p in items]
    for i in sorted(pages):
        page = doc[i]
        _unrotate(page)
        for (h, prep), r in zip(items, handle_rects(page.rect, cfg, aspects)):
            ins.put(page, r, prep, not cfg.behind_content, normalize_url(h.link))


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def _open(data: bytes, label: str) -> fitz.Document:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{label} is not a valid PDF ({e})") from e
    if doc.needs_pass:
        doc.close()
        raise ValueError(f"{label} is password protected")
    return doc


def build_pdf(sources: Sequence[bytes], job: JobConfig) -> bytes:
    """sources = one or more source PDFs (merged in order). Returns final PDF bytes."""
    with PDF_LOCK:
        return _build(sources, job)


def _build(sources: Sequence[bytes], job: JobConfig) -> bytes:
    src = fitz.open()
    for k, data in enumerate(sources, 1):
        d = _open(data, f"Source PDF #{k}")
        src.insert_pdf(d)
        d.close()
    n = src.page_count
    if n == 0:
        raise ValueError("Source PDF has no pages")

    after: Dict[int, List[Tuple[bytes, str]]] = {}
    for idx, ins in enumerate(job.inserts, 1):
        for k in parse_after_spec(ins.after, n):
            after.setdefault(k, []).append((ins.pdf, f"Custom insert #{idx}"))

    out = fitz.open()
    kinds: List[str] = []  # 'original' / 'inserted' for every output page

    def add_extra(data: bytes, label: str) -> None:
        d = _open(data, label)
        out.insert_pdf(d)
        kinds.extend(["inserted"] * d.page_count)
        d.close()

    if job.header_pdf:
        add_extra(job.header_pdf, "Header PDF")
    for data, label in after.get(0, []):
        add_extra(data, label)

    start = 0
    for k in sorted(x for x in after if x > 0):
        out.insert_pdf(src, from_page=start, to_page=k - 1)
        kinds.extend(["original"] * (k - start))
        start = k
        for data, label in after[k]:
            add_extra(data, label)
    if start < n:
        out.insert_pdf(src, from_page=start, to_page=n - 1)
        kinds.extend(["original"] * (n - start))

    if job.footer_pdf:
        add_extra(job.footer_pdf, "Footer PDF")
    src.close()

    logo_prep, handle_items = job.prepared()
    ins = _ImageInserter()
    if logo_prep:
        apply_logo(out, select_pages(job.logo.pages, job.logo.custom_pages, kinds), job.logo, logo_prep, ins)
    if handle_items:
        hc = job.handles
        apply_handles(out, select_pages(hc.pages, hc.custom_pages, kinds), hc, handle_items, ins)

    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data


def render_thumbnails(pdf: bytes, max_pages: int = 4, dpi: int = 60) -> List[Tuple[int, bytes]]:
    """PNG previews of the first pages + the last page."""
    with PDF_LOCK:
        doc = fitz.open(stream=pdf, filetype="pdf")
        n = doc.page_count
        idx = list(range(min(n, max_pages - 1)))
        if n - 1 not in idx:
            idx.append(n - 1)
        thumbs = [(i + 1, doc[i].get_pixmap(dpi=dpi).tobytes("png")) for i in idx]
        doc.close()
        return thumbs
