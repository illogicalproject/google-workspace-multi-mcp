"""Google Contacts (People API) wrapper — read-only — for the MCP server."""

from typing import Any, Dict, List

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

_PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations"
_READ_MASK = "names,emailAddresses,phoneNumbers,organizations"


class ContactsService:
    def __init__(self, credentials: Credentials, account_name: str = ""):
        self.service = build("people", "v1", credentials=credentials)
        self.account_name = account_name

    def search(self, query: str, max_results: int = 15) -> Dict[str, Any]:
        """Search the user's contacts by name, email, phone, etc.

        The People API keeps a per-session search cache that must be warmed with
        an initial request, so we send a throwaway warmup call first (Google's
        documented pattern) before the real query."""
        try:
            self.service.people().searchContacts(
                query="", readMask=_READ_MASK, pageSize=1
            ).execute()
        except Exception:
            pass  # warmup is best-effort
        result = self.service.people().searchContacts(
            query=query,
            readMask=_READ_MASK,
            pageSize=min(max_results, 30),
        ).execute()
        people = [self._parse_person(r.get("person", {})) for r in result.get("results", [])]
        return {"query": query, "count": len(people), "contacts": people}

    def list_contacts(self, max_results: int = 50) -> Dict[str, Any]:
        result = self.service.people().connections().list(
            resourceName="people/me",
            pageSize=min(max_results, 200),
            personFields=_PERSON_FIELDS,
            sortOrder="LAST_MODIFIED_DESCENDING",
        ).execute()
        people = [self._parse_person(p) for p in result.get("connections", [])]
        return {
            "count": len(people),
            "contacts": people,
            "nextPageToken": result.get("nextPageToken", ""),
        }

    # ------------------------------------------------------------------ internals

    def _parse_person(self, p: Dict[str, Any]) -> Dict[str, Any]:
        names = p.get("names", [])
        orgs = p.get("organizations", [])
        return {
            "name": names[0].get("displayName", "") if names else "",
            "emails": [e.get("value", "") for e in p.get("emailAddresses", [])],
            "phones": [ph.get("value", "") for ph in p.get("phoneNumbers", [])],
            "organization": orgs[0].get("name", "") if orgs else "",
        }
