"""
Core PDF engine — merge PDFs, insert header / footer / custom pages and stamp
hyperlinked images (logo watermark, Play Store badge) onto pages.

Built only on free, open-source libraries: PyMuPDF + Pillow.
"""
from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

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
    """Settings for one stamped image (logo watermark or Play Store badge)."""
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
class JobConfig:
    header_pdf: Optional[bytes] = None
    footer_pdf: Optional[bytes] = None
    inserts: List[InsertSpec] = field(default_factory=list)
    logo: Optional[StampConfig] = None
    playstore: Optional[StampConfig] = None
    _prepared: Optional[List[Tuple[StampConfig, PreparedStamp]]] = field(default=None, repr=False)

    def prepared(self) -> List[Tuple[StampConfig, PreparedStamp]]:
        """Pre-process stamp images once per batch (rotation, opacity, resize)."""
        if self._prepared is None:
            self._prepared = [
                (c, prepare_stamp(c))
                for c in (self.logo, self.playstore)
                if c is not None and c.enabled and c.image
            ]
        return self._prepared


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url):
        url = "https://" + url
    return url


def prepare_stamp(cfg: StampConfig) -> PreparedStamp:
    """Rotate, apply transparency and trim the stamp image with Pillow."""
    img = Image.open(io.BytesIO(cfg.image))
    img.load()
    img = img.convert("RGBA")
    if max(img.size) > MAX_STAMP_PX:
        img.thumbnail((MAX_STAMP_PX, MAX_STAMP_PX), Image.Resampling.LANCZOS)
    if cfg.rotation % 360:
        img = img.rotate(cfg.rotation, resample=Image.Resampling.BICUBIC, expand=True)
    bbox = img.getchannel("A").getbbox()  # trim fully transparent borders
    if bbox:
        img = img.crop(bbox)
    opacity = min(max(float(cfg.opacity), 0.01), 1.0)
    if opacity < 1.0:
        alpha = img.getchannel("A").point(lambda v: round(v * opacity))
        img.putalpha(alpha)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return PreparedStamp(png=buf.getvalue(), aspect=img.height / img.width)


def _font(size: int, bold: bool = False):
    names = ["DejaVuSans-Bold.ttf", "Arial Bold.ttf"] if bold else ["DejaVuSans.ttf", "Arial.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def default_playstore_badge() -> bytes:
    """A simple original 'get the app' button, used when no badge image is uploaded.
    (Upload the official Google Play badge PNG in the app if you prefer it.)"""
    w, h = 720, 200
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=40, fill=(1, 135, 95, 255))
    d.ellipse([34, 40, 154, 160], fill=(255, 255, 255, 255))
    d.polygon([(78, 70), (78, 130), (126, 100)], fill=(1, 135, 95, 255))
    d.text((184, 38), "Download our app", font=_font(40), fill=(255, 255, 255, 255))
    d.text((184, 88), "on Google Play", font=_font(64, bold=True), fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


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

    if cfg.position == "custom":
        x0 = pw * cfg.custom_x_pct / 100.0 - w / 2
        y0 = ph * cfg.custom_y_pct / 100.0 - h / 2
    else:
        v, hz = ("middle", "center") if cfg.position == "center" else cfg.position.split("-")
        x0 = {"left": m, "center": (pw - w) / 2, "right": pw - w - m}[hz]
        y0 = {"top": m, "middle": (ph - h) / 2, "bottom": ph - h - m}[v]
    x0 += page_rect.x0
    y0 += page_rect.y0
    return [fitz.Rect(x0, y0, x0 + w, y0 + h)]


def apply_stamp(doc: fitz.Document, pages: Set[int], cfg: StampConfig, prep: PreparedStamp) -> None:
    uri = normalize_url(cfg.link)
    xref = 0  # re-use the same embedded image on every page -> small files
    for i in sorted(pages):
        page = doc[i]
        if page.rotation:
            page.remove_rotation()  # makes positions behave on rotated pages
        for r in stamp_rects(page.rect, cfg, prep.aspect):
            if not r.intersects(page.rect):
                continue
            if xref:
                page.insert_image(r, xref=xref, overlay=not cfg.behind_content, keep_proportion=True)
            else:
                xref = page.insert_image(r, stream=prep.png, overlay=not cfg.behind_content,
                                         keep_proportion=True)
            if uri:
                page.insert_link({"kind": fitz.LINK_URI,
                                  "from": fitz.Rect(r).intersect(page.rect),
                                  "uri": uri})


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

    for cfg, prep in job.prepared():
        apply_stamp(out, select_pages(cfg.pages, cfg.custom_pages, kinds), cfg, prep)

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
