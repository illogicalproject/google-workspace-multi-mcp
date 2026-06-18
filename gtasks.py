"""Google Tasks service wrapper (read-write) for the multi-account MCP server."""

from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class TasksService:
    def __init__(self, credentials: Credentials, account_name: str = ""):
        self.service = build("tasks", "v1", credentials=credentials)
        self.account_name = account_name

    # ------------------------------------------------------------------ task lists

    def list_tasklists(self) -> List[Dict[str, Any]]:
        result = self.service.tasklists().list(maxResults=100).execute()
        return [
            {"id": tl["id"], "title": tl.get("title", ""), "updated": tl.get("updated", "")}
            for tl in result.get("items", [])
        ]

    # ------------------------------------------------------------------ tasks

    def list_tasks(
        self,
        tasklist: str = "@default",
        show_completed: bool = False,
        max_results: int = 100,
    ) -> Dict[str, Any]:
        result = self.service.tasks().list(
            tasklist=tasklist,
            showCompleted=show_completed,
            showHidden=show_completed,
            maxResults=min(max_results, 100),
        ).execute()
        return {
            "tasklist": tasklist,
            "tasks": [self._parse_task(t) for t in result.get("items", [])],
        }

    def create_task(
        self,
        title: str,
        tasklist: str = "@default",
        notes: Optional[str] = None,
        due: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            # Tasks API only honors the date part of 'due' (RFC3339).
            body["due"] = due
        task = self.service.tasks().insert(tasklist=tasklist, body=body).execute()
        return self._parse_task(task)

    def update_task(
        self,
        task_id: str,
        tasklist: str = "@default",
        title: Optional[str] = None,
        notes: Optional[str] = None,
        due: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if notes is not None:
            body["notes"] = notes
        if due is not None:
            body["due"] = due
        if status is not None:
            body["status"] = status  # "needsAction" or "completed"
        task = self.service.tasks().patch(
            tasklist=tasklist, task=task_id, body=body
        ).execute()
        return self._parse_task(task)

    def complete_task(self, task_id: str, tasklist: str = "@default") -> Dict[str, Any]:
        return self.update_task(task_id, tasklist=tasklist, status="completed")

    def delete_task(self, task_id: str, tasklist: str = "@default") -> Dict[str, Any]:
        self.service.tasks().delete(tasklist=tasklist, task=task_id).execute()
        return {"status": "deleted", "task_id": task_id, "tasklist": tasklist}

    # ------------------------------------------------------------------ internals

    def _parse_task(self, t: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": t.get("id", ""),
            "title": t.get("title", ""),
            "notes": t.get("notes", ""),
            "status": t.get("status", ""),
            "due": t.get("due", ""),
            "completed": t.get("completed", ""),
            "updated": t.get("updated", ""),
        }
