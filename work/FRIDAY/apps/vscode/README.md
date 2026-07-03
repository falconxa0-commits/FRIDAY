# FRIDAY VS Code Extension

A real VS Code extension that connects to your local Friday AI Assistant backend.

## Features

- **Friday: Explain Selection** — send selected code to Friday, get a streamed explanation in the Friday output panel
- **Friday: Fix This Error** — describe or paste an error, get a fix suggestion
- **Friday: Generate Code** — open an input box, get generated code
- **Friday: Research Topic** — open an input box, get research findings with sources
- **Friday: Morning Briefing** — trigger the morning briefing skill
- **Friday: Open Dashboard** — opens a sci-fi-styled webview dashboard inside VS Code

## Auto Error Detection

When enabled (default: on), Friday watches VS Code's Problems panel. When a new error appears, you'll see:

> Friday noticed an error in main.py:42 — want me to look at it?

Click **Yes** to send the error context to Friday's brain. Friday cannot modify files without ledger approval.

## Status Bar

The status bar shows the current Friday mode and provider: `◆ Friday [WORK · GLM]`

Click it to open the dashboard.

## Build From Source

```bash
cd apps/vscode
npm install
npm run compile          # produces out/extension.js
npm run package          # produces friday.vsix (requires @vscode/vsce)
```

## Install

```bash
# Build first (see above), then:
code --install-extension friday.vsix
```

Or in VS Code: Extensions panel → ⋯ menu → "Install from VSIX" → select `friday.vsix`.

## Configuration

Open VS Code Settings and search for "Friday":

| Setting | Default | Description |
|---------|---------|-------------|
| `friday.apiUrl` | `http://localhost:8000` | Friday API URL |
| `friday.apiToken` | (empty) | Friday API token (must match `FRIDAY_API_TOKEN` on the server) |
| `friday.autoErrorDetect` | `true` | Auto-detect errors in the Problems panel |

## Backend Setup

The extension requires a running Friday backend:

```bash
# In the FRIDAY project root:
pip install -r requirements.txt
export GLM_API_KEY="your-free-key-from-open.bigmodel.cn"
export FRIDAY_API_TOKEN="any-secret-string"
uvicorn api.main:app --port 8000
```

Set the same `FRIDAY_API_TOKEN` in VS Code's `friday.apiToken` setting.

## Architecture

- Pure TypeScript, no framework
- Talks to Friday via `/api/chat/stream` (SSE) for word-by-word streaming
- Uses VS Code's `OutputChannel` for response display
- Dashboard is a Webview panel with the same sci-fi palette as the CLI and web dashboard
