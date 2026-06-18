import base64
import mimetypes
import os
import re
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class GmailService:
    def __init__(self, credentials: Credentials, account_name: str = ""):
        self.service = build("gmail", "v1", credentials=credentials)
        self.account_name = account_name

    # ------------------------------------------------------------------ profile

    def get_profile(self) -> Dict[str, Any]:
        return self.service.users().getProfile(userId="me").execute()

    # ------------------------------------------------------------------ search / read

    def search_messages(
        self,
        query: str,
        max_results: int = 20,
        page_token: Optional[str] = None,
        include_body: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "userId": "me",
            "q": query,
            "maxResults": min(max_results, 100),
        }
        if page_token:
            params["pageToken"] = page_token

        result = self.service.users().messages().list(**params).execute()
        raw_messages = result.get("messages", [])

        messages = []
        for raw in raw_messages:
            fmt = "full" if include_body else "metadata"
            msg = self._get_raw_message(raw["id"], format=fmt)
            messages.append(self._parse_message(msg))

        return {
            "messages": messages,
            "nextPageToken": result.get("nextPageToken"),
            "resultSizeEstimate": result.get("resultSizeEstimate", 0),
        }

    def get_message(self, message_id: str) -> Dict[str, Any]:
        msg = self._get_raw_message(message_id, format="full")
        return self._parse_message(msg)

    def get_thread(self, thread_id: str) -> Dict[str, Any]:
        thread = self.service.users().threads().get(userId="me", id=thread_id).execute()
        messages = [self._parse_message(m) for m in thread.get("messages", [])]
        return {
            "id": thread["id"],
            "messageCount": len(messages),
            "messages": messages,
        }

    # ------------------------------------------------------------------ send / draft

    @staticmethod
    def _safe_header(value: str) -> str:
        """Reject CR/LF in header values to prevent header injection.

        Recipient and subject fields are often populated from untrusted content
        (forwarded mail, scraped text). A newline would let a caller smuggle in
        extra headers (Bcc, spoofed From) or a second body."""
        if value and ("\n" in value or "\r" in value):
            raise ValueError(
                "Invalid header value: line breaks are not allowed in "
                "email recipients or subject."
            )
        return value

    def _build_message(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        html: bool = False,
        attachments: Optional[List[str]] = None,
    ):
        """Construct a MIME message. body is sent as HTML when html=True, else
        plain text. Uses multipart only when attachments are present."""
        subtype = "html" if html else "plain"
        attachments = attachments or []
        if attachments:
            msg: Any = MIMEMultipart()
            msg.attach(MIMEText(body, subtype))
            for path in attachments:
                self._attach_file(msg, path)
        else:
            msg = MIMEText(body, subtype)
        msg["to"] = self._safe_header(to)
        msg["subject"] = self._safe_header(subject)
        if cc:
            msg["cc"] = self._safe_header(cc)
        if bcc:
            msg["bcc"] = self._safe_header(bcc)
        return msg

    @staticmethod
    def _attach_file(msg: MIMEMultipart, path: str) -> None:
        """Read a local file and attach it. The path is on the machine running
        this server (not wherever the model is)."""
        full = os.path.expanduser(path)
        if not os.path.isfile(full):
            raise ValueError(f"Attachment not found: {path}")
        ctype, encoding = mimetypes.guess_type(full)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(full, "rb") as f:
            data = f.read()
        part = MIMEBase(maintype, subtype)
        part.set_payload(data)
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", "attachment", filename=os.path.basename(full)
        )
        msg.attach(part)

    def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        html: bool = False,
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        msg = self._build_message(to, subject, body, cc, bcc, html, attachments)
        return self.service.users().messages().send(
            userId="me", body={"raw": self._encode(msg)}
        ).execute()

    def reply_to_thread(
        self,
        thread_id: str,
        message_id: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        html: bool = False,
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        msg = self._build_message(to, subject, body, cc, bcc, html, attachments)
        # Threading needs the original's RFC822 Message-ID header
        # (e.g. <CA+abc@mail.gmail.com>), NOT Gmail's internal message id.
        # Without it the reply won't thread in non-Gmail clients.
        rfc_message_id = self._rfc_message_id(message_id)
        if rfc_message_id:
            msg["In-Reply-To"] = rfc_message_id
            msg["References"] = rfc_message_id

        return self.service.users().messages().send(
            userId="me",
            body={"raw": self._encode(msg), "threadId": thread_id},
        ).execute()

    def _rfc_message_id(self, gmail_message_id: str) -> str:
        """Look up the RFC822 'Message-ID' header for a Gmail message id.
        Returns '' if it can't be found (reply still sends via threadId)."""
        try:
            meta = self.service.users().messages().get(
                userId="me",
                id=gmail_message_id,
                format="metadata",
                metadataHeaders=["Message-ID"],
            ).execute()
        except Exception:
            return ""
        for h in meta.get("payload", {}).get("headers", []):
            if h.get("name", "").lower() == "message-id":
                return h.get("value", "")
        return ""

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        html: bool = False,
        attachments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        msg = self._build_message(to, subject, body, cc, bcc, html, attachments)
        return self.service.users().drafts().create(
            userId="me", body={"message": {"raw": self._encode(msg)}}
        ).execute()

    def list_drafts(self, max_results: int = 20) -> List[Dict[str, Any]]:
        result = self.service.users().drafts().list(
            userId="me", maxResults=min(max_results, 50)
        ).execute()

        drafts = []
        for draft in result.get("drafts", []):
            details = self.service.users().drafts().get(
                userId="me", id=draft["id"], format="full"
            ).execute()
            msg = self._parse_message(details.get("message", {}))
            msg["draft_id"] = draft["id"]
            drafts.append(msg)

        return drafts

    # ------------------------------------------------------------------ labels

    def list_labels(self) -> List[Dict[str, Any]]:
        result = self.service.users().labels().list(userId="me").execute()
        return [
            {"id": lbl["id"], "name": lbl["name"], "type": lbl.get("type", "")}
            for lbl in result.get("labels", [])
        ]

    def modify_labels(
        self,
        message_id: str,
        add_labels: Optional[List[str]] = None,
        remove_labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        return self.service.users().messages().modify(
            userId="me", id=message_id, body=body
        ).execute()

    def trash_message(self, message_id: str) -> Dict[str, Any]:
        return self.service.users().messages().trash(
            userId="me", id=message_id
        ).execute()

    # ------------------------------------------------------------------ attachments

    def get_attachment(
        self, message_id: str, attachment_id: str, save_path: str
    ) -> Dict[str, Any]:
        """Download a message attachment (by the attachmentId surfaced when
        reading a message) and write it to a local path on this machine."""
        att = self.service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        data = base64.urlsafe_b64decode(att["data"].encode())
        full = os.path.expanduser(save_path)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return {"saved_to": full, "bytes": len(data)}

    # ------------------------------------------------------------------ internals

    def _get_raw_message(self, message_id: str, format: str = "full") -> Dict[str, Any]:
        return self.service.users().messages().get(
            userId="me", id=message_id, format=format
        ).execute()

    def _parse_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        payload = msg.get("payload", {})
        headers: Dict[str, str] = {}
        for h in payload.get("headers", []):
            headers[h["name"].lower()] = h["value"]

        body = self._extract_body(payload)
        attachments = self._extract_attachments(payload)

        return {
            "id": msg.get("id", ""),
            "threadId": msg.get("threadId", ""),
            "labels": msg.get("labelIds", []),
            "snippet": msg.get("snippet", ""),
            "date": headers.get("date", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "subject": headers.get("subject", "(no subject)"),
            "body": body,
            "attachments": attachments,
            "hasAttachments": bool(attachments),
        }

    def _extract_attachments(
        self, payload: Dict[str, Any], acc: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Walk the MIME tree and collect attachment parts (filename + a
        downloadable attachmentId). Use get_attachment to fetch the bytes."""
        if acc is None:
            acc = []
        filename = payload.get("filename") or ""
        body = payload.get("body", {})
        if filename and body.get("attachmentId"):
            acc.append({
                "filename": filename,
                "mimeType": payload.get("mimeType", ""),
                "size": body.get("size", 0),
                "attachmentId": body["attachmentId"],
            })
        for part in payload.get("parts", []) or []:
            self._extract_attachments(part, acc)
        return acc

    def _extract_body(self, payload: Dict[str, Any]) -> str:
        if not payload:
            return ""

        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")

        if body_data:
            decoded = base64.urlsafe_b64decode(body_data.encode()).decode(
                "utf-8", errors="replace"
            )
            if "html" in mime_type:
                decoded = re.sub(r"<[^>]+>", " ", decoded)
                decoded = (
                    decoded.replace("&nbsp;", " ")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&amp;", "&")
                    .replace("&quot;", '"')
                )
                decoded = re.sub(r"\s+", " ", decoded)
            return decoded.strip()

        parts = payload.get("parts", [])

        for part in parts:
            if part.get("mimeType") == "text/plain":
                result = self._extract_body(part)
                if result:
                    return result

        for part in parts:
            result = self._extract_body(part)
            if result:
                return result

        return ""

    @staticmethod
    def _encode(msg: MIMEText | MIMEMultipart) -> str:
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")