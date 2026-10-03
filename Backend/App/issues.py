import json
from datetime import datetime, timezone
import httpx
from App.config import settings
from App.database import connect

LABELS = ("good first issue", "help wanted", "bug")

def now():
    return datetime.now(timezone.utc).isoformat()

def convert(row):
    item = dict(row)
    item["labels"] = json.loads(item.pop("labels"))
    return item

async def discover(language="Python", per_page=15):
    query = f"is:issue is:open language:{language} " + " ".join(
        f'label:"{label}"' for label in LABELS
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Patchwork",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            "https://api.github.com/search/issues",
            params={"q": query, "sort": "updated", "order": "desc",
                    "per_page": per_page},
            headers=headers,
        )
        response.raise_for_status()
        results = response.json().get("items", [])

    saved = []
    with connect() as conn:
        for issue in results:
            if "pull_request" in issue:
                continue
            repo = issue.get("repository_url", "").replace(
                "https://api.github.com/repos/", ""
            )
            if not repo:
                continue
            timestamp = now()
            labels = json.dumps([x["name"] for x in issue.get("labels", [])])
            conn.execute("""
                INSERT INTO issues
                (repository, number, title, url, body, labels, comments,
                 state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title, body=excluded.body,
                    labels=excluded.labels, comments=excluded.comments,
                    updated_at=excluded.updated_at
            """, (
                repo, issue["number"], issue["title"], issue["html_url"],
                issue.get("body") or "", labels, issue.get("comments", 0),
                timestamp, timestamp,
            ))
            row = conn.execute(
                "SELECT * FROM issues WHERE url=?", (issue["html_url"],)
            ).fetchone()
            saved.append(convert(row))
    return saved

def list_issues(state=None):
    with connect() as conn:
        if state:
            rows = conn.execute(
                "SELECT * FROM issues WHERE state=? ORDER BY updated_at DESC",
                (state,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM issues ORDER BY updated_at DESC"
            ).fetchall()
    return [convert(row) for row in rows]

def transition(issue_id, target, reason=None):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM issues WHERE id=?", (issue_id,)
        ).fetchone()
        if not row:
            return None
        old = row["state"]
        timestamp = now()
        conn.execute(
            "UPDATE issues SET state=?, reason=?, updated_at=? WHERE id=?",
            (target, reason if target in ("rejected", "declined") else None,
             timestamp, issue_id),
        )
        conn.execute("""
            INSERT INTO events(issue_id, from_state, to_state, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (issue_id, old, target, reason, timestamp))
        return convert(conn.execute(
            "SELECT * FROM issues WHERE id=?", (issue_id,)
        ).fetchone())
