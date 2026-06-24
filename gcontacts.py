"""Google Contacts (People API) wrapper — read + write — for the MCP server.

Write methods (create/update/delete/photo) require the full
``https://www.googleapis.com/auth/contacts`` scope; the read-only scope will
return a 403 on those calls."""

import base64
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Everything we read back (and, for the writable subset, the field mask Google
# accepts on searchContacts/connections.list).
_PERSON_FIELDS = (
    "names,nicknames,emailAddresses,phoneNumbers,organizations,"
    "addresses,birthdays,urls,biographies,occupations,photos"
)
_READ_MASK = _PERSON_FIELDS


def _merge(singular: Optional[str], plural: Optional[List[str]]) -> List[str]:
    """Combine a convenience singular value with an optional list, primary
    first, de-duplicated, dropping blanks."""
    out: List[str] = []
    if singular:
        out.append(singular)
    for x in plural or []:
        if x and x not in out:
            out.append(x)
    return out


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

    # ------------------------------------------------------------------ write

    def create_contact(
        self,
        name: Optional[str] = None,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
        middle_name: Optional[str] = None,
        nickname: Optional[str] = None,
        email: Optional[str] = None,
        emails: Optional[List[str]] = None,
        phone: Optional[str] = None,
        phones: Optional[List[str]] = None,
        organization: Optional[str] = None,
        title: Optional[str] = None,
        department: Optional[str] = None,
        address: Optional[str] = None,
        birthday: Optional[str] = None,
        url: Optional[str] = None,
        urls: Optional[List[str]] = None,
        notes: Optional[str] = None,
        occupation: Optional[str] = None,
        photo_url: Optional[str] = None,
        photo_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new contact. Provide at least a name or email. A LinkedIn (or
        other) headshot can be set by passing photo_url; photo_path sets it from a
        local file on the machine running the server."""
        body, _ = self._build_person_body(
            name=name, given_name=given_name, family_name=family_name,
            middle_name=middle_name, nickname=nickname,
            emails=_merge(email, emails), phones=_merge(phone, phones),
            organization=organization, title=title, department=department,
            address=address, birthday=birthday, urls=_merge(url, urls),
            notes=notes, occupation=occupation,
        )
        if not body:
            raise ValueError("Provide at least a name or email to create a contact.")
        person = self.service.people().createContact(body=body).execute()
        resource_name = person.get("resourceName", "")
        photo_set = False
        if (photo_url or photo_path) and resource_name:
            updated = self.set_photo(resource_name, photo_url=photo_url, photo_path=photo_path)
            if updated:
                person = updated.get("person", person)
                photo_set = True
        return {
            "created": True,
            "photo_set": photo_set,
            "resourceName": resource_name,
            "contact": self._parse_person(person),
        }

    def update_contact(
        self,
        resource_name: str,
        name: Optional[str] = None,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
        middle_name: Optional[str] = None,
        nickname: Optional[str] = None,
        email: Optional[str] = None,
        emails: Optional[List[str]] = None,
        phone: Optional[str] = None,
        phones: Optional[List[str]] = None,
        organization: Optional[str] = None,
        title: Optional[str] = None,
        department: Optional[str] = None,
        address: Optional[str] = None,
        birthday: Optional[str] = None,
        url: Optional[str] = None,
        urls: Optional[List[str]] = None,
        notes: Optional[str] = None,
        occupation: Optional[str] = None,
        photo_url: Optional[str] = None,
        photo_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing contact. Only the fields you pass are changed.
        Reads the contact first to obtain its current etag (required by the
        People API to guard against concurrent edits). A photo is set via a
        separate call and does not require the etag."""
        merged_emails = _merge(email, emails)
        merged_phones = _merge(phone, phones)
        merged_urls = _merge(url, urls)

        body, update_fields = self._build_person_body(
            name=name, given_name=given_name, family_name=family_name,
            middle_name=middle_name, nickname=nickname,
            emails=merged_emails or None, phones=merged_phones or None,
            organization=organization, title=title, department=department,
            address=address, birthday=birthday, urls=merged_urls or None,
            notes=notes, occupation=occupation,
        )

        person: Dict[str, Any] = {"resourceName": resource_name}
        if update_fields:
            existing = self.service.people().get(
                resourceName=resource_name, personFields=_PERSON_FIELDS
            ).execute()
            body["etag"] = existing.get("etag")
            person = self.service.people().updateContact(
                resourceName=resource_name,
                updatePersonFields=",".join(update_fields),
                body=body,
            ).execute()

        photo_set = False
        if photo_url or photo_path:
            updated = self.set_photo(resource_name, photo_url=photo_url, photo_path=photo_path)
            if updated:
                person = updated.get("person", person)
                photo_set = True

        if not update_fields and not photo_set:
            raise ValueError("No fields provided to update.")

        return {
            "updated": True,
            "photo_set": photo_set,
            "resourceName": person.get("resourceName", resource_name),
            "contact": self._parse_person(person),
        }

    def set_photo(
        self,
        resource_name: str,
        photo_url: Optional[str] = None,
        photo_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Set a contact's photo from a URL or a local file path (on the machine
        running the server). Returns the updateContactPhoto response, or None if
        no source was given."""
        data: Optional[bytes] = None
        if photo_url:
            req = Request(photo_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted caller input)
                data = resp.read()
        elif photo_path:
            with open(photo_path, "rb") as f:
                data = f.read()
        if data is None:
            return None
        encoded = base64.b64encode(data).decode("ascii")
        return self.service.people().updateContactPhoto(
            resourceName=resource_name,
            body={"photoBytes": encoded, "personFields": _PERSON_FIELDS},
        ).execute()

    def delete_contact(self, resource_name: str) -> Dict[str, Any]:
        """Permanently delete a contact by its resourceName (e.g. people/c123)."""
        self.service.people().deleteContact(resourceName=resource_name).execute()
        return {"deleted": True, "resourceName": resource_name}

    # ------------------------------------------------------------------ internals

    def _build_person_body(
        self,
        name: Optional[str] = None,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
        middle_name: Optional[str] = None,
        nickname: Optional[str] = None,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        organization: Optional[str] = None,
        title: Optional[str] = None,
        department: Optional[str] = None,
        address: Optional[str] = None,
        birthday: Optional[str] = None,
        urls: Optional[List[str]] = None,
        notes: Optional[str] = None,
        occupation: Optional[str] = None,
    ) -> "tuple[Dict[str, Any], List[str]]":
        """Build a People API person body. Returns (body, field_names) where
        field_names is the list of top-level keys set — used directly as the
        updatePersonFields mask, since each body key is a valid person field."""
        body: Dict[str, Any] = {}

        if name or given_name or family_name or middle_name:
            n: Dict[str, str] = {}
            if given_name:
                n["givenName"] = given_name
            if middle_name:
                n["middleName"] = middle_name
            if family_name:
                n["familyName"] = family_name
            if name and not (given_name or family_name):
                n["unstructuredName"] = name
            body["names"] = [n]
        if nickname:
            body["nicknames"] = [{"value": nickname}]
        if emails:
            body["emailAddresses"] = [{"value": e} for e in emails]
        if phones:
            body["phoneNumbers"] = [{"value": p} for p in phones]
        if organization or title or department:
            org: Dict[str, str] = {}
            if organization:
                org["name"] = organization
            if title:
                org["title"] = title
            if department:
                org["department"] = department
            body["organizations"] = [org]
        if address:
            body["addresses"] = [{"formattedValue": address}]
        if birthday:
            body["birthdays"] = [{"text": birthday}]
        if urls:
            body["urls"] = [{"value": u} for u in urls]
        if notes:
            body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
        if occupation:
            body["occupations"] = [{"value": occupation}]

        return body, list(body.keys())

    def _parse_person(self, p: Dict[str, Any]) -> Dict[str, Any]:
        names = p.get("names", [])
        orgs = p.get("organizations", [])
        org0 = orgs[0] if orgs else {}
        nicks = p.get("nicknames", [])
        bdays = p.get("birthdays", [])
        bios = p.get("biographies", [])
        photos = p.get("photos", [])
        # prefer a non-default (user-set) photo, else the first available
        photo = ""
        for ph in photos:
            if not ph.get("default"):
                photo = ph.get("url", "")
                break
        if not photo and photos:
            photo = photos[0].get("url", "")
        return {
            "resourceName": p.get("resourceName", ""),
            "name": names[0].get("displayName", "") if names else "",
            "nickname": nicks[0].get("value", "") if nicks else "",
            "emails": [e.get("value", "") for e in p.get("emailAddresses", [])],
            "phones": [ph.get("value", "") for ph in p.get("phoneNumbers", [])],
            "organization": org0.get("name", ""),
            "title": org0.get("title", ""),
            "department": org0.get("department", ""),
            "addresses": [a.get("formattedValue", "") for a in p.get("addresses", [])],
            "birthday": bdays[0].get("text", "") if bdays else "",
            "urls": [u.get("value", "") for u in p.get("urls", [])],
            "notes": bios[0].get("value", "") if bios else "",
            "photo": photo,
        }
