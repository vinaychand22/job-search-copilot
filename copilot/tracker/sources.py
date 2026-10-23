"""Where emails come from. Every source yields the same small Email record,
so the classifier never knows (or cares) whether it's reading Gmail or a test file.

    GmailSource  - live, read-only Gmail API (OAuth, scope gmail.readonly)
    JsonlSource  - one email per JSON line (evals and demos)
"""
from __future__ import annotations

import base64
import datetime as dt
import email.utils
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from copilot.config import ROOT

MAX_BODY_CHARS = 3_000


@dataclass(frozen=True)
class Email:
    message_id: str
    sender: str
    subject: str
    received_at: str  # ISO, local time, no timezone (consistent string ordering in SQLite)
    body: str


# ---------- text cleanup: shorter input = fewer tokens and less noise ----------

_TAGS = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_QUOTED = re.compile(r"\n(On .{5,120} wrote:|-{2,} ?Original Message ?-{2,}|From: .+\nSent: ).*", re.S)


def clean_body(text: str, is_html: bool = False) -> str:
    if is_html:
        text = html.unescape(_TAGS.sub(" ", text))
    text = _QUOTED.sub("", text)                 # drop quoted earlier messages in the thread
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:MAX_BODY_CHARS]


def to_local_iso(rfc2822_or_ms: str | int) -> str:
    if isinstance(rfc2822_or_ms, int) or str(rfc2822_or_ms).isdigit():
        d = dt.datetime.fromtimestamp(int(rfc2822_or_ms) / 1000)
    else:
        d = email.utils.parsedate_to_datetime(str(rfc2822_or_ms)).astimezone().replace(tzinfo=None)
    return d.isoformat(timespec="seconds")


class JsonlSource:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self) -> Iterator[Email]:
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    yield Email(d["message_id"], d["sender"], d["subject"], d["received_at"],
                                clean_body(d["body"]))


class GmailSource:
    """Read-only Gmail access.

    One-time setup (see README): create a Google Cloud project, enable the Gmail API,
    create an OAuth client of type "Desktop app", download it to config/credentials.json.
    The first run opens a browser for consent and stores config/token.json (gitignored).
    """
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]  # cannot send, delete or modify mail

    def __init__(self, query: str, max_results: int = 200,
                 credentials: Path = ROOT / "config" / "credentials.json",
                 token: Path = ROOT / "config" / "token.json") -> None:
        self.query, self.max_results = query, max_results
        self.credentials, self.token = credentials, token

    def _service(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if self.token.exists():
            creds = Credentials.from_authorized_user_file(str(self.token), self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials.exists():
                    raise FileNotFoundError(f"Missing {self.credentials}. See README > Gmail setup.")
                creds = InstalledAppFlow.from_client_secrets_file(str(self.credentials), self.SCOPES) \
                    .run_local_server(port=0)
            self.token.write_text(creds.to_json())
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def fetch(self, skip_ids: set[str] | None = None) -> Iterator[Email]:
        """Lists matching message ids, then downloads only the ones not seen before."""
        skip_ids = skip_ids or set()
        svc = self._service().users().messages()
        ids, page = [], None
        while len(ids) < self.max_results:
            resp = svc.list(userId="me", q=self.query, pageToken=page,
                            maxResults=min(100, self.max_results - len(ids))).execute()
            ids += [m["id"] for m in resp.get("messages", [])]
            page = resp.get("nextPageToken")
            if not page:
                break
        for mid in ids:
            if mid in skip_ids:
                continue  # incremental sync: already classified on a previous run
            msg = svc.get(userId="me", id=mid, format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            body, is_html = _extract_body(msg["payload"])
            yield Email(mid, headers.get("from", ""), headers.get("subject", ""),
                        to_local_iso(int(msg["internalDate"])), clean_body(body, is_html))


def _extract_body(payload: dict) -> tuple[str, bool]:
    """Depth-first search for text/plain, falling back to text/html."""
    plain, htm = None, None
    stack = [payload]
    while stack:
        part = stack.pop()
        stack.extend(part.get("parts", []))
        data = part.get("body", {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        if part.get("mimeType") == "text/plain" and plain is None:
            plain = text
        elif part.get("mimeType") == "text/html" and htm is None:
            htm = text
    if plain:
        return plain, False
    return htm or "", True
