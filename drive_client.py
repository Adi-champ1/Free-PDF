"""
Google Drive helper (free Google Drive API).

Auth options:
  * OAuth refresh token of YOUR Google account  -> works with any normal My Drive folder (recommended)
  * Service account                             -> only works if the folder is inside a Shared Drive
"""
from __future__ import annotations

import io
import re
import threading
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]

_ID_PATTERNS = [
    re.compile(r"/d/([a-zA-Z0-9_-]{20,})"),          # .../file/d/<id>/view
    re.compile(r"[?&]id=([a-zA-Z0-9_-]{20,})"),      # open?id=<id>, uc?id=<id>
    re.compile(r"^([a-zA-Z0-9_-]{25,60})$"),          # bare file ID
]


def extract_file_id(link: str) -> Optional[str]:
    link = (link or "").strip()
    for pat in _ID_PATTERNS:
        if m := pat.search(link):
            return m.group(1)
    return None


def split_links(cell: str) -> List[str]:
    """A cell may hold several links (comma / semicolon / newline separated) -> merged in order."""
    return [p for p in re.split(r"[\s,;]+", str(cell or "").strip()) if p]


def describe_error(e: Exception) -> str:
    if isinstance(e, HttpError):
        status = getattr(e.resp, "status", None)
        if status == 404:
            return "file/folder not found, or the connected Google account has no access to it"
        if status == 403:
            reason = str(e)
            if "storageQuotaExceeded" in reason or "storage quota" in reason.lower():
                return ("service accounts have no storage — use OAuth credentials, "
                        "or put the destination folder in a Shared Drive")
            return "permission denied by Google Drive (check sharing / quota)"
        return f"Google Drive error {status}: {e.reason if hasattr(e, 'reason') else e}"
    return str(e)[:300]


class DriveClient:
    def __init__(self, creds):
        self.creds = creds
        if not creds.valid:
            creds.refresh(Request())  # fail fast on bad credentials
        self._local = threading.local()

    @classmethod
    def from_oauth(cls, client_id: str, client_secret: str, refresh_token: str, **_) -> "DriveClient":
        creds = Credentials(
            None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        return cls(creds)

    @classmethod
    def from_service_account(cls, info: dict) -> "DriveClient":
        return cls(service_account.Credentials.from_service_account_info(info, scopes=SCOPES))

    @property
    def svc(self):
        # googleapiclient is not thread-safe -> one service object per thread
        if not hasattr(self._local, "svc"):
            self._local.svc = build("drive", "v3", credentials=self.creds, cache_discovery=False)
        return self._local.svc

    def download_pdf(self, file_id: str) -> bytes:
        meta = self.svc.files().get(fileId=file_id, fields="id,name,mimeType",
                                    supportsAllDrives=True).execute(num_retries=3)
        if meta["mimeType"].startswith("application/vnd.google-apps."):
            # Google Docs / Slides etc. -> export as PDF
            req = self.svc.files().export_media(fileId=file_id, mimeType="application/pdf")
        else:
            req = self.svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk(num_retries=3)
        data = buf.getvalue()
        if b"%PDF" not in data[:1024]:
            raise ValueError(f"'{meta.get('name', file_id)}' is not a PDF")
        return data

    def upload_pdf(self, name: str, data: bytes, folder_id: str,
                   overwrite: bool = False, make_public: bool = True) -> str:
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/pdf",
                                  resumable=len(data) > 5 * 1024 * 1024)
        existing = None
        if overwrite:
            safe = name.replace("\\", "\\\\").replace("'", "\\'")
            res = self.svc.files().list(
                q=f"name = '{safe}' and '{folder_id}' in parents and trashed = false",
                fields="files(id)", pageSize=1,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute(num_retries=3)
            existing = (res.get("files") or [None])[0]

        if existing:
            f = self.svc.files().update(fileId=existing["id"], media_body=media,
                                        fields="id,webViewLink",
                                        supportsAllDrives=True).execute(num_retries=3)
        else:
            f = self.svc.files().create(body={"name": name, "parents": [folder_id]},
                                        media_body=media, fields="id,webViewLink",
                                        supportsAllDrives=True).execute(num_retries=3)
        if make_public:
            self.svc.permissions().create(fileId=f["id"], body={"type": "anyone", "role": "reader"},
                                          supportsAllDrives=True).execute(num_retries=3)
        return f["webViewLink"]
