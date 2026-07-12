from __future__ import annotations

import io
import json

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _get_drive_service(service_account_json: str):
    info = json.loads(service_account_json)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_pdfs_in_folder(service_account_json: str, folder_id: str) -> list[dict]:
    """Return [{"id": ..., "name": ...}, ...] for PDFs directly inside the folder."""
    service = _get_drive_service(service_account_json)
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    result = (
        service.files()
        .list(q=query, fields="files(id, name)", pageSize=100, orderBy="name")
        .execute()
    )
    return result.get("files", [])


def download_pdf(service_account_json: str, file_id: str) -> bytes:
    """Download a single file's bytes from Google Drive."""
    service = _get_drive_service(service_account_json)
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()
