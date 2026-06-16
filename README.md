# google-workspace-multi-mcp

A local [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that connects **multiple Google accounts at once** — Gmail, Calendar, Drive, Docs, and Sheets — to Claude Desktop. Runs entirely on your machine. No cloud hosting, nothing proxied through anyone else's servers.

The official Claude Google connector authenticates **one** account at a time. This server lets you work with several Google accounts (e.g. personal + work) in the same conversation, and adds full read/write Drive, Docs, and Sheets on top of Gmail and Calendar.

## Features

- **Multiple accounts** — connect as many Google accounts as you like; every tool takes an `account` parameter
- **Gmail** — unified search across all accounts, read messages/threads, send, threaded replies, drafts, labels, trash
- **Calendar** (read/write) — list calendars, browse and search events, and create, update, delete, or quick-add events (with attendees and optional Google Meet links)
- **Drive** (read/write) — list/search files, read content, create files & folders, rename, move, copy, trash, share
- **Docs** (read/write) — create, read, append, find-and-replace, plus **Markdown rendering** — turn Markdown into real Docs formatting (headings, bold/italic, `code`, links, bullet & numbered lists, blockquotes, tables)
- **Sheets** (read/write) — create, add tabs, read/write/append ranges, clear

## Requirements

- macOS or Linux
- Python 3.11+
- A free Google Cloud project (you create your own — see setup)
- Claude Desktop

## Before you start: how auth works here

This is a **bring-your-own-credentials** tool. You create your own Google Cloud project and OAuth client, and the server uses *your* credentials to talk directly to Google's APIs from your machine. Your data never touches the author's infrastructure — there is none.

A direct consequence: during authentication you **will** see a **"Google hasn't verified this app"** warning. This is expected and safe in this context:

- It's *your* app, *your* Google Cloud project, accessing *your* data.
- The server runs locally — your mail and files never pass through any third party.
- Making the warning disappear would require Google's **paid annual security assessment** (CASA, ~$500–$4,500/yr) for the restricted Gmail/Drive scopes. That makes no sense for a self-hosted personal tool, so this project does not pursue it.

To proceed past it: **Advanced → Go to [your app] (unsafe)**. If you'd prefer a milder consent screen, you can run with a reduced scope set — see [Scopes](#scopes--permissions).

## Installation

### 1. Clone

```bash
git clone https://github.com/illogicalproject/google-workspace-multi-mcp.git
cd google-workspace-multi-mcp
```

### 2. Install dependencies

```bash
bash setup.sh
```

Creates a virtualenv (`.venv`) and installs the Python dependencies.

### 3. Create a Google Cloud project & OAuth client

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. **Enable the APIs** you plan to use (APIs & Services → Library): **Gmail**, **Google Calendar**, **Google Drive**, **Google Docs**, **Google Sheets**. Enable only the ones you want.
3. **Configure the OAuth consent screen** (APIs & Services → OAuth consent screen):
   - User type: **External**.
   - Add every email address you'll connect as a **Test user**.
   - **Publish the app to Production.** You can remain unverified, but if you leave it in *Testing*, Google **expires your refresh tokens every 7 days** and you'll have to re-authenticate weekly. Publishing to Production removes that 7-day limit.
4. **Create credentials** → OAuth 2.0 Client ID → application type **Desktop app**. Download the JSON and save it as `credentials/client_secret.json`.

### 4. Configure your accounts

```bash
cp config.json.example config.json
```

Edit `config.json` — the keys (`personal`, `work`, …) are the names you'll use when asking Claude to act on a specific account:

```json
{
  "accounts": {
    "personal": { "email": "you@gmail.com",     "description": "Personal account" },
    "work":     { "email": "you@company.com",   "description": "Work account" }
  },
  "services": ["gmail", "calendar", "drive", "docs", "sheets"],
  "credentials_dir": "./credentials"
}
```

The optional **`services`** list controls which Google services are enabled — both the OAuth scopes requested *and* the tools exposed to Claude. Omit it to enable everything. Trim it to request fewer permissions and get a milder consent screen (see [Scopes](#scopes--permissions)). Valid entries: `gmail`, `calendar`, `drive`, `drive.file`, `docs`, `sheets`. Use `drive.file` instead of `drive` for per-file (non-restricted) Drive access.

### 5. Authenticate

```bash
source .venv/bin/activate
python setup_auth.py
```

A browser opens for each account in turn — sign in with the matching Google account and approve. You'll pass the unverified-app warning described above. Tokens are stored locally in `credentials/tokens/` and refresh automatically; you only do this once per account (and again if you change scopes).

### 6. Register with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-workspace": {
      "command": "/absolute/path/to/google-workspace-multi-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/google-workspace-multi-mcp/server.py"]
    }
  }
}
```

Use the real absolute path where you cloned the repo. The key (`google-workspace`) becomes the prefix on the tool names Claude sees. Restart Claude Desktop and the tools appear.

## Scopes & permissions

Scopes follow the `services` list in `config.json` (see step 4) — enable only the services you need for a lower-tier, less alarming consent screen. The per-service scope definitions live in `auth.py` (`SERVICE_SCOPES`) if you want to customize further.

| Service | Scope | Google tier |
|---|---|---|
| Gmail | `gmail.readonly`, `gmail.send`, `gmail.compose`, `gmail.modify` | **Restricted** |
| Calendar | `calendar.events` (read + create/edit/delete events) | Sensitive |
| Drive | `drive` (full read/write) | **Restricted** |
| Docs | `documents` | Sensitive |
| Sheets | `spreadsheets` | Sensitive |

The **restricted** scopes (Gmail, full Drive) are what trigger the most prominent warning and would require the paid CASA assessment to verify. If you want a quieter, lower-risk setup: drop the Gmail scopes and swap `drive` for `drive.file` (per-file access, non-sensitive). A Calendar/Docs/Sheets/`drive.file` configuration uses only sensitive/non-sensitive scopes.

## Available tools

Every tool takes an `account` parameter matching a key in `config.json`.

**Accounts:** `list_accounts`

**Gmail:** `gmail_get_profile`, `gmail_search`, `gmail_read_message`, `gmail_read_thread`, `gmail_send`, `gmail_reply`, `gmail_create_draft`, `gmail_list_drafts`, `gmail_list_labels`, `gmail_modify_labels`, `gmail_trash`

**Calendar:** `calendar_list_calendars`, `calendar_list_events`, `calendar_search`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, `calendar_quick_add_event`

**Drive:** `drive_list_files`, `drive_get_metadata`, `drive_read_file`, `drive_create_folder`, `drive_create_text_file`, `drive_update_text_file`, `drive_rename_file`, `drive_move_file`, `drive_copy_file`, `drive_trash_file`, `drive_share_file`

**Docs:** `docs_create`, `docs_read`, `docs_append_text`, `docs_replace_text`, `docs_create_markdown`, `docs_append_markdown`, `docs_replace_with_markdown`

**Sheets:** `sheets_create`, `sheets_get_info`, `sheets_add_sheet`, `sheets_read_range`, `sheets_write_range`, `sheets_append_rows`, `sheets_clear_range`

## Usage examples

- *"Do I have any unread emails in my work account?"*
- *"Search all my accounts for invoices from the last month."*
- *"What meetings do I have this week on my work calendar?"*
- *"Add a 30-minute 'Design review' to my work calendar Friday at 2pm and invite alex@company.com."*
- *"Find the 'Q3 planning' doc in my personal Drive and read it."*
- *"Create a spreadsheet in my work account and add a header row plus three rows of data."*
- *"Turn these meeting notes into a formatted Google Doc with headings and a table."*

## Adding a new account

1. Add it to `config.json`.
2. Add the email as a Test user on the OAuth consent screen (if still in Testing).
3. Run `python setup_auth.py` — it only prompts for accounts that aren't authenticated yet.
4. Restart Claude Desktop.

## Security & privacy

- OAuth tokens live in `credentials/tokens/`; your client secret in `credentials/client_secret.json`. Both, plus `config.json`, are excluded from version control via `.gitignore`. **Never commit `credentials/`.**
- All traffic is directly between your machine and Google's APIs. There is no hosted backend and no telemetry.
- Revoke access anytime at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Troubleshooting

- **Re-authenticating an account:** `python setup_auth.py`. If a token is in a bad state, delete the relevant file in `credentials/tokens/` and re-run.
- **Weekly re-auth nags:** your OAuth consent screen is probably still in *Testing*. Publish it to *Production* (you can stay unverified) to stop the 7-day refresh-token expiry.
- **`403 … API has not been used in project … or is disabled`:** enable that API in the Cloud Console and wait a minute.
- **Changed scopes but nothing changed:** delete the token files in `credentials/tokens/` and re-run `setup_auth.py` — an existing valid token won't pick up new scopes on its own.

## License

MIT
