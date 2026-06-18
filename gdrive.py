"""Google Drive service wrapper (read-write) for the multi-account MCP server."""

import io
import mimetypes
import os
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import (
    MediaFileUpload,
    MediaIoBaseDownload,
    MediaIoBaseUpload,
)

# Google-native MIME types and how to export them to text.
_EXPORT_MAP = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

_FILE_FIELDS = "id, name, mimeType, modifiedTime, size, owners(emailAddress), parents, webViewLink, webContentLink, trashed"


class DriveService:
    def __init__(self, credentials: Credentials, account_name: str = ""):
        self.service = build("drive", "v3", credentials=credentials)
        self.account_name = account_name

    # ------------------------------------------------------------------ search

    def list_files(
        self,
        query: Optional[str] = None,
        max_results: int = 20,
        order_by: str = "modifiedTime desc",
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "pageSize": min(max_results, 100),
            "fields": f"files({_FILE_FIELDS}), nextPageToken",
            "orderBy": order_by,
            "spaces": "drive",
        }
        if query:
            params["q"] = query
        result = self.service.files().list(**params).execute()
        return {
            "count": len(result.get("files", [])),
            "files": [self._parse_file(f) for f in result.get("files", [])],
            "nextPageToken": result.get("nextPageToken"),
        }

    def get_metadata(self, file_id: str) -> Dict[str, Any]:
        f = self.service.files().get(fileId=file_id, fields=_FILE_FIELDS).execute()
        return self._parse_file(f)

    # ------------------------------------------------------------------ read

    def read_file(self, file_id: str, max_chars: int = 100_000) -> Dict[str, Any]:
        """Return text content of a file. Exports Google-native docs to text/CSV;
        downloads other text-like files directly."""
        meta = self.service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
        mime = meta.get("mimeType", "")

        if mime in _EXPORT_MAP:
            request = self.service.files().export_media(
                fileId=file_id, mimeType=_EXPORT_MAP[mime]
            )
        else:
            request = self.service.files().get_media(fileId=file_id)

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        raw = buf.getvalue()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "id": file_id,
                "name": meta.get("name", ""),
                "mimeType": mime,
                "error": "Binary file — not decodable as text.",
                "bytes": len(raw),
            }

        truncated = len(text) > max_chars
        return {
            "id": file_id,
            "name": meta.get("name", ""),
            "mimeType": mime,
            "truncated": truncated,
            "content": text[:max_chars],
        }

    def download_file(
        self,
        file_id: str,
        save_path: str,
        export_mime_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download any Drive file's bytes to a local path (on the machine
        running this server). Binary files download directly. Google-native
        files (Docs/Sheets/Slides) must be exported — pass export_mime_type
        (e.g. 'application/pdf')."""
        meta = self.service.files().get(
            fileId=file_id, fields="id, name, mimeType"
        ).execute()
        mime = meta.get("mimeType", "")

        if mime.startswith("application/vnd.google-apps"):
            if not export_mime_type:
                raise ValueError(
                    f"'{meta.get('name', file_id)}' is a Google-native file "
                    f"({mime}). Pass export_mime_type (e.g. 'application/pdf') to "
                    "export it, or use drive_read_file to get its text."
                )
            request = self.service.files().export_media(
                fileId=file_id, mimeType=export_mime_type
            )
        else:
            request = self.service.files().get_media(fileId=file_id)

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = buf.getvalue()

        full = os.path.expanduser(save_path)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)
        return {
            "saved_to": full,
            "bytes": len(data),
            "name": meta.get("name", ""),
            "mimeType": export_mime_type or mime,
        }

    # ------------------------------------------------------------------ write

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]
        f = self.service.files().create(body=body, fields=_FILE_FIELDS).execute()
        return self._parse_file(f)

    def create_text_file(
        self,
        name: str,
        content: str,
        mime_type: str = "text/plain",
        parent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name}
        if parent_id:
            body["parents"] = [parent_id]
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False
        )
        f = self.service.files().create(
            body=body, media_body=media, fields=_FILE_FIELDS
        ).execute()
        return self._parse_file(f)

    def update_text_file(self, file_id: str, content: str, mime_type: str = "text/plain") -> Dict[str, Any]:
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False
        )
        f = self.service.files().update(
            fileId=file_id, media_body=media, fields=_FILE_FIELDS
        ).execute()
        return self._parse_file(f)

    def upload_file(
        self,
        path: str,
        name: Optional[str] = None,
        parent_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload a local file (any type, including binary) to Drive. The path
        is on the machine running this server."""
        full = os.path.expanduser(path)
        if not os.path.isfile(full):
            raise ValueError(f"File not found: {path}")
        if mime_type is None:
            guessed, _ = mimetypes.guess_type(full)
            mime_type = guessed or "application/octet-stream"
        body: Dict[str, Any] = {"name": name or os.path.basename(full)}
        if parent_id:
            body["parents"] = [parent_id]
        media = MediaFileUpload(full, mimetype=mime_type, resumable=True)
        f = self.service.files().create(
            body=body, media_body=media, fields=_FILE_FIELDS
        ).execute()
        return self._parse_file(f)

    def rename_file(self, file_id: str, new_name: str) -> Dict[str, Any]:
        f = self.service.files().update(
            fileId=file_id, body={"name": new_name}, fields=_FILE_FIELDS
        ).execute()
        return self._parse_file(f)

    def move_file(self, file_id: str, new_parent_id: str) -> Dict[str, Any]:
        current = self.service.files().get(fileId=file_id, fields="parents").execute()
        prev_parents = ",".join(current.get("parents", []))
        f = self.service.files().update(
            fileId=file_id,
            addParents=new_parent_id,
            removeParents=prev_parents,
            fields=_FILE_FIELDS,
        ).execute()
        return self._parse_file(f)

    def copy_file(self, file_id: str, new_name: Optional[str] = None) -> Dict[str, Any]:
        body = {"name": new_name} if new_name else {}
        f = self.service.files().copy(fileId=file_id, body=body, fields=_FILE_FIELDS).execute()
        return self._parse_file(f)

    def trash_file(self, file_id: str) -> Dict[str, Any]:
        f = self.service.files().update(
            fileId=file_id, body={"trashed": True}, fields=_FILE_FIELDS
        ).execute()
        return self._parse_file(f)

    def share_file(
        self,
        file_id: str,
        email: str,
        role: str = "reader",
        notify: bool = False,
    ) -> Dict[str, Any]:
        # Guard against unintended escalation. 'owner' would transfer ownership
        # of the user's file; reject it. Only allow the safe sharing roles.
        allowed_roles = {"reader", "commenter", "writer"}
        if role not in allowed_roles:
            raise ValueError(
                f"Invalid role '{role}'. Allowed: {', '.join(sorted(allowed_roles))} "
                "(ownership transfer is not permitted through this tool)."
            )
        permission = {"type": "user", "role": role, "emailAddress": email}
        result = self.service.permissions().create(
            fileId=file_id,
            body=permission,
            sendNotificationEmail=notify,
            fields="id, role, type, emailAddress",
        ).execute()
        return {"file_id": file_id, "permission": result}

    # ------------------------------------------------------------------ internals

    def _parse_file(self, f: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "mimeType": f.get("mimeType", ""),
            "modifiedTime": f.get("modifiedTime", ""),
            "size": f.get("size", ""),
            "owners": [o.get("emailAddress", "") for o in f.get("owners", [])],
            "parents": f.get("parents", []),
            "webViewLink": f.get("webViewLink", ""),
            # Direct-download URL. Present for binary/uploaded files; Google-native
            # Docs/Sheets/Slides don't have one (use webViewLink for those).
            "webContentLink": f.get("webContentLink", ""),
            "trashed": f.get("trashed", False),
        }
