"""
wc_drive.py — Find and download the most recent Desk365 export from a Google
Drive folder (or from a local folder, for testing).

Configuration lives in `.streamlit/secrets.toml` locally and in
*App settings → Secrets* on Streamlit Community Cloud:

    [drive]
    folder_url = "https://drive.google.com/drive/folders/<FOLDER_ID>"   # <- paste the folder link here

    # Optional: read from a local file/folder instead of Drive (handy on your PC).
    # local_path = "C:/Users/you/Desk365 exports"

    [gcp_service_account]
    type = "service_account"
    project_id = "..."
    private_key_id = "..."
    private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
    client_email = "wc-dashboard@<project>.iam.gserviceaccount.com"
    client_id = "..."
    auth_uri = "https://accounts.google.com/o/oauth2/auth"
    token_uri = "https://oauth2.googleapis.com/token"
    auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
    client_x509_cert_url = "..."

The Drive folder must be shared (Viewer) with `client_email`.  Nothing in
this module depends on Streamlit, so it can be unit-tested with a fake
service object.
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"
EXPORT_PREFIX = "Tkt_Lst"
DATA_EXTENSIONS = (".csv", ".xlsx", ".xls")

# Tkt_Lst_Created_Time_Oct_01_2026_to_Oct_31_2026(.csv)
_NAME_RX = re.compile(
    r"Tkt_Lst_Created_Time_(?P<m1>[A-Za-z]{3})_(?P<d1>\d{1,2})_(?P<y1>\d{4})_to_(?P<m2>[A-Za-z]{3})_(?P<d2>\d{1,2})_(?P<y2>\d{4})",
    re.I,
)


@dataclass
class ExportFile:
    id: str
    name: str
    mime_type: str = ""
    modified: str = ""  # RFC3339 from Drive, or ISO from the local filesystem
    size: int | None = None
    start: datetime | None = None  # parsed from the file name
    end: datetime | None = None

    @property
    def label(self) -> str:
        if self.start and self.end:
            return f"{self.start:%d %b %Y} → {self.end:%d %b %Y}"
        return self.name


# --------------------------------------------------------------------------- #
# Pure helpers (no network)
# --------------------------------------------------------------------------- #
def folder_id_from_url(url: str) -> str:
    """Accept a Drive folder link in any common shape, or a bare folder id."""
    url = (url or "").strip()
    for rx in (r"/folders/([A-Za-z0-9_-]{10,})", r"[?&]id=([A-Za-z0-9_-]{10,})"):
        m = re.search(rx, url)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", url):
        return url
    raise ValueError("That does not look like a Google Drive folder link (expected .../drive/folders/<id>).")


def parse_export_name(name: str) -> tuple[datetime | None, datetime | None]:
    m = _NAME_RX.search(name or "")
    if not m:
        return None, None
    try:
        start = datetime.strptime(f"{m['m1']} {m['d1']} {m['y1']}", "%b %d %Y")
        end = datetime.strptime(f"{m['m2']} {m['d2']} {m['y2']}", "%b %d %Y")
        return start, end
    except ValueError:
        return None, None


def is_export(name: str, mime_type: str = "") -> bool:
    if mime_type == GOOGLE_FOLDER:
        return False
    n = (name or "").strip()
    if not n.lower().startswith(EXPORT_PREFIX.lower()):
        return False
    return mime_type == GOOGLE_SHEET or n.lower().endswith(DATA_EXTENSIONS) or "." not in n


def pick_latest(files: list[ExportFile]) -> ExportFile | None:
    """Newest export: by the period in the name first, then by modified time."""
    candidates = [f for f in files if is_export(f.name, f.mime_type)]
    if not candidates:
        return None
    floor = datetime(1900, 1, 1)
    return max(candidates, key=lambda f: (f.end or floor, f.start or floor, f.modified or ""))


def _to_export(meta: dict) -> ExportFile:
    start, end = parse_export_name(meta.get("name", ""))
    size = meta.get("size")
    return ExportFile(
        id=meta.get("id", ""),
        name=meta.get("name", ""),
        mime_type=meta.get("mimeType", ""),
        modified=meta.get("modifiedTime", ""),
        size=int(size) if size not in (None, "") else None,
        start=start,
        end=end,
    )


# --------------------------------------------------------------------------- #
# Google Drive
# --------------------------------------------------------------------------- #
def build_service(service_account_info: dict):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = dict(service_account_info)
    # Secrets pasted into Streamlit sometimes carry literal "\n" in the key.
    if "private_key" in info and "\\n" in info["private_key"]:
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_folder(service, folder_id: str) -> list[ExportFile]:
    files: list[ExportFile] = []
    token = None
    while True:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                pageSize=200,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(_to_export(m) for m in resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return files


def download(service, file: ExportFile) -> tuple[str, bytes]:
    """Return (file name, bytes). Google Sheets are exported as CSV."""
    from googleapiclient.http import MediaIoBaseDownload

    if file.mime_type == GOOGLE_SHEET:
        request = service.files().export_media(fileId=file.id, mimeType="text/csv")
        name = file.name if file.name.lower().endswith(".csv") else f"{file.name}.csv"
    else:
        request = service.files().get_media(fileId=file.id, supportsAllDrives=True)
        name = file.name if "." in file.name else f"{file.name}.csv"
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return name, buf.getvalue()


def fetch_latest_from_drive(folder_url: str, service_account_info: dict) -> tuple[ExportFile, str, bytes]:
    folder_id = folder_id_from_url(folder_url)
    service = build_service(service_account_info)
    files = list_folder(service, folder_id)
    latest = pick_latest(files)
    if latest is None:
        raise FileNotFoundError(
            f"No Desk365 export found in the Drive folder ({len(files)} item(s) visible). "
            f"Expected a file named like 'Tkt_Lst_Created_Time_Oct_01_2026_to_Oct_31_2026.csv'. "
            f"If the folder is not empty, check that it is shared with {service_account_info.get('client_email', 'the service account')}."
        )
    name, data = download(service, latest)
    return latest, name, data


# --------------------------------------------------------------------------- #
# Local folder / file (testing or on-premise use)
# --------------------------------------------------------------------------- #
def fetch_latest_from_path(path: str) -> tuple[ExportFile, str, bytes]:
    p = Path(path).expanduser()
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = [f for f in p.iterdir() if f.is_file()]
    else:
        raise FileNotFoundError(f"Local path not found: {p}")
    metas = []
    for f in files:
        start, end = parse_export_name(f.name)
        metas.append(ExportFile(id=str(f), name=f.name, modified=datetime.fromtimestamp(f.stat().st_mtime).isoformat(), size=f.stat().st_size, start=start, end=end))
    latest = pick_latest(metas) if p.is_dir() else metas[0]
    if latest is None:
        raise FileNotFoundError(f"No Desk365 export (Tkt_Lst_Created_Time_*.csv) found in {p}")
    return latest, latest.name, Path(latest.id).read_bytes()


def env_local_path() -> str | None:
    return os.environ.get("WC_LOCAL_PATH") or None
