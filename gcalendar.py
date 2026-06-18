from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class CalendarService:
    def __init__(self, credentials: Credentials, account_name: str = ""):
        self.service = build("calendar", "v3", credentials=credentials)
        self.account_name = account_name

    # ------------------------------------------------------------------ calendars

    def list_calendars(self) -> List[Dict[str, Any]]:
        result = self.service.calendarList().list().execute()
        return [
            {
                "id": cal["id"],
                "summary": cal.get("summary", ""),
                "description": cal.get("description", ""),
                "primary": cal.get("primary", False),
                "accessRole": cal.get("accessRole", ""),
                "backgroundColor": cal.get("backgroundColor", ""),
            }
            for cal in result.get("items", [])
        ]

    # ------------------------------------------------------------------ events

    def list_events(
        self,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "maxResults": min(max_results, 50),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min or now,
        }
        if time_max:
            params["timeMax"] = time_max

        result = self.service.events().list(**params).execute()
        events = [self._parse_event(e) for e in result.get("items", [])]
        return {
            "calendar_id": calendar_id,
            "count": len(events),
            "events": events,
            "nextPageToken": result.get("nextPageToken"),
        }

    def search_events(
        self,
        query: str,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "q": query,
            "maxResults": min(max_results, 50),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min or now,
        }
        if time_max:
            params["timeMax"] = time_max

        result = self.service.events().list(**params).execute()
        events = [self._parse_event(e) for e in result.get("items", [])]
        return {
            "query": query,
            "count": len(events),
            "events": events,
        }

    def get_event(self, event_id: str, calendar_id: str = "primary") -> Dict[str, Any]:
        event = self.service.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()
        return self._parse_event(event)

    # ------------------------------------------------------------------ write

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        all_day: bool = False,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        timezone: Optional[str] = None,
        calendar_id: str = "primary",
        send_updates: str = "none",
        add_meet: bool = False,
        recurrence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"summary": summary}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        body["start"] = self._time_field(start, all_day, timezone)
        body["end"] = self._time_field(end, all_day, timezone)
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        if recurrence:
            # RRULE/RDATE/EXDATE lines, e.g. ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=8"].
            body["recurrence"] = recurrence

        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "body": body,
            "sendUpdates": send_updates,
        }
        if add_meet:
            import uuid

            body["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            params["conferenceDataVersion"] = 1

        event = self.service.events().insert(**params).execute()
        return self._parse_event(event)

    def update_event(
        self,
        event_id: str,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        all_day: bool = False,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        timezone: Optional[str] = None,
        calendar_id: str = "primary",
        send_updates: str = "none",
        recurrence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # Patch only the provided fields.
        body: Dict[str, Any] = {}
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description
        if location is not None:
            body["location"] = location
        if start is not None:
            body["start"] = self._time_field(start, all_day, timezone)
        if end is not None:
            body["end"] = self._time_field(end, all_day, timezone)
        if attendees is not None:
            body["attendees"] = [{"email": a} for a in attendees]
        if recurrence is not None:
            # Pass [] to clear recurrence (convert a series to a single event).
            body["recurrence"] = recurrence

        event = self.service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=body,
            sendUpdates=send_updates,
        ).execute()
        return self._parse_event(event)

    def delete_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
        send_updates: str = "none",
    ) -> Dict[str, Any]:
        self.service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates=send_updates,
        ).execute()
        return {"status": "deleted", "event_id": event_id, "calendar_id": calendar_id}

    def quick_add_event(
        self, text: str, calendar_id: str = "primary", send_updates: str = "none"
    ) -> Dict[str, Any]:
        event = self.service.events().quickAdd(
            calendarId=calendar_id, text=text, sendUpdates=send_updates
        ).execute()
        return self._parse_event(event)

    # ------------------------------------------------------------------ availability

    def free_busy(
        self,
        time_min: str,
        time_max: str,
        calendar_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Return busy intervals for one or more calendars over a window.
        time_min/time_max are RFC3339. Defaults to the primary calendar."""
        cals = calendar_ids or ["primary"]
        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": c} for c in cals],
        }
        result = self.service.freebusy().query(body=body).execute()
        calendars = result.get("calendars", {})
        return {
            "timeMin": time_min,
            "timeMax": time_max,
            "busy": {
                cid: info.get("busy", []) for cid, info in calendars.items()
            },
            "errors": {
                cid: info["errors"]
                for cid, info in calendars.items()
                if info.get("errors")
            },
        }

    # ------------------------------------------------------------------ internals

    def _time_field(
        self, value: str, all_day: bool, timezone: Optional[str]
    ) -> Dict[str, Any]:
        """Build a Calendar API start/end object. All-day events use 'date'
        (YYYY-MM-DD); timed events use 'dateTime' (RFC3339)."""
        if all_day:
            return {"date": value[:10]}
        field: Dict[str, Any] = {"dateTime": value}
        if timezone:
            field["timeZone"] = timezone
        return field

    def _parse_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        start = event.get("start", {})
        end = event.get("end", {})

        attendees = [
            {
                "email": a.get("email", ""),
                "name": a.get("displayName", ""),
                "response": a.get("responseStatus", ""),
                "self": a.get("self", False),
            }
            for a in event.get("attendees", [])
        ]

        return {
            "id": event.get("id", ""),
            "summary": event.get("summary", "(no title)"),
            "description": event.get("description", ""),
            "location": event.get("location", ""),
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "allDay": "date" in start and "dateTime" not in start,
            "status": event.get("status", ""),
            "organizer": event.get("organizer", {}).get("email", ""),
            "attendees": attendees,
            "attendeeCount": len(attendees),
            "meetLink": event.get("hangoutLink", ""),
            "htmlLink": event.get("htmlLink", ""),
            "recurrence": bool(event.get("recurrence") or event.get("recurringEventId")),
        }