"""Where alert emails come from: a folder of .eml files (demo/tests) or a Gmail inbox/label."""
from __future__ import annotations

import base64
import email
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from .parsers import MailMessage
from .settings import Settings


def _from_email_message(msg: email.message.EmailMessage, fallback_id: str) -> MailMessage:
    text_part = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    date_header = msg.get("Date")
    return MailMessage(
        message_id=(msg.get("Message-ID") or fallback_id).strip(),
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        received_at=parsedate_to_datetime(date_header) if date_header else None,
        text=text_part.get_content() if text_part else "",
        html=html_part.get_content() if html_part else None,
    )


class LocalMailbox:
    """Reads every .eml file in a directory. Used for the demo, tests and sanitized samples."""

    def __init__(self, directory: Path):
        self.directory = directory

    def fetch(self) -> Iterator[MailMessage]:
        for path in sorted(self.directory.glob("*.eml")):
            raw = email.message_from_bytes(path.read_bytes(), policy=policy.default)
            yield _from_email_message(raw, fallback_id=path.name)


class GmailMailbox:
    """Gmail API intake, filtered by a search query (labels, senders, recency).

    Setup: create an OAuth client (Desktop app) in Google Cloud, download credentials.json,
    run `python -m bidscore gmail-auth` once to create token.json. Scope is read-only.
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(self, settings: Settings):
        self.settings = settings

    def _service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(str(self.settings.gmail_token), self.SCOPES)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def authorize(self) -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(self.settings.gmail_credentials), self.SCOPES)
        creds = flow.run_local_server(port=0)
        self.settings.gmail_token.write_text(creds.to_json())

    def fetch(self) -> Iterator[MailMessage]:
        service = self._service()
        request = service.users().messages().list(userId="me", q=self.settings.gmail_query, maxResults=100)
        while request is not None:
            page = request.execute()
            for ref in page.get("messages", []):
                raw = service.users().messages().get(userId="me", id=ref["id"], format="raw").execute()
                data = base64.urlsafe_b64decode(raw["raw"].encode())
                yield _from_email_message(email.message_from_bytes(data, policy=policy.default), fallback_id=ref["id"])
            request = service.users().messages().list_next(request, page)


def get_mailbox(settings: Settings):
    if settings.mailbox == "gmail":
        return GmailMailbox(settings)
    return LocalMailbox(settings.mailbox_dir)
