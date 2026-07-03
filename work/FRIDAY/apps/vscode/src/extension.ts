// FRIDAY VS Code Extension — entry point
// Real TypeScript implementation that talks to the Friday FastAPI backend.

import * as vscode from 'vscode';
import * as http from 'http';
import { URL } from 'url';

const COL_BG = '#050810';
const COL_PRIMARY = '#7C3AED';
const COL_ACCENT = '#06B6D4';
const COL_SUCCESS = '#10B981';
const COL_WARN = '#F59E0B';
const COL_DANGER = '#EF4444';

let fridayPanel: vscode.WebviewPanel | undefined;
let statusBarItem: vscode.StatusBarItem | undefined;

export function activate(context: vscode.ExtensionContext) {
    console.log('Friday AI Assistant extension activating...');

    // Status bar item — fetches real mode + provider from /api/chat/stats
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = '◆ Friday [...]';
    statusBarItem.tooltip = 'Friday AI Assistant — click to open dashboard';
    statusBarItem.command = 'friday.openDashboard';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Refresh status bar on activation + every 60 seconds
    refreshStatusBar();
    const statusInterval = setInterval(refreshStatusBar, 60_000);
    context.subscriptions.push({ dispose: () => clearInterval(statusInterval) });

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('friday.explainSelection', explainSelection),
        vscode.commands.registerCommand('friday.fixError', fixError),
        vscode.commands.registerCommand('friday.generateCode', generateCode),
        vscode.commands.registerCommand('friday.researchTopic', researchTopic),
        vscode.commands.registerCommand('friday.morningBriefing', morningBriefing),
        vscode.commands.registerCommand('friday.openDashboard', openDashboard),
    );

    // Auto-detect errors in Problems panel
    if (vscode.workspace.getConfiguration('friday').get<boolean>('autoErrorDetect')) {
        const diag = vscode.languages.onDidChangeDiagnostics((e) => {
            const uris = e.uris;
            for (const uri of uris) {
                const diags = vscode.languages.getDiagnostics(uri);
                const errors = diags.filter(d => d.severity === vscode.DiagnosticSeverity.Error);
                if (errors.length > 0) {
                    const filename = vscode.workspace.asRelativePath(uri);
                    const firstErr = errors[0];
                    const action = vscode.window.showInformationMessage(
                        `Friday noticed an error in ${filename}:${firstErr.range.start.line + 1} — want me to look at it?`,
                        'Yes',
                        'No',
                    );
                    action.then(choice => {
                        if (choice === 'Yes') {
                            const msg = `${firstErr.message} (in ${filename}:${firstErr.range.start.line + 1})`;
                            sendToFriday(`Fix this error: ${msg}`, 'fix');
                        }
                    });
                }
            }
        });
        context.subscriptions.push(diag);
    }
}

export function deactivate() {
    console.log('Friday AI Assistant deactivating...');
}

// ---------------------------------------------------------------------------
// Status bar refresh — fetches real mode + provider from /api/chat/stats
// ---------------------------------------------------------------------------

async function refreshStatusBar(): Promise<void> {
    if (!statusBarItem) { return; }
    const cfg = getConfig();
    try {
        const http = require('http');
        const url = new URL('/api/chat/stats', cfg.apiUrl);
        if (cfg.apiToken) {
            url.searchParams.set('token', cfg.apiToken);
        }
        http.get(url.toString(), (res: any) => {
            let body = '';
            res.on('data', (chunk: Buffer) => { body += chunk.toString(); });
            res.on('end', () => {
                if (res.statusCode !== 200) {
                    statusBarItem!.text = '◆ Friday [offline]';
                    return;
                }
                try {
                    const stats = JSON.parse(body);
                    const provider = (stats.provider || 'unknown').toUpperCase();
                    // Friday doesn't expose mode via /chat/stats — derive from time of day
                    const hour = new Date().getHours();
                    const mode = (hour >= 6 && hour < 9) ? 'MORNING'
                               : (hour >= 9 && hour < 18) ? 'WORK'
                               : (hour >= 18 && hour < 22) ? 'EVENING'
                               : 'NIGHT';
                    statusBarItem!.text = `◆ Friday [${mode} · ${provider}]`;
                } catch {
                    statusBarItem!.text = '◆ Friday [error]';
                }
            });
        }).on('error', () => {
            statusBarItem!.text = '◆ Friday [offline]';
        });
    } catch {
        statusBarItem.text = '◆ Friday [error]';
    }
}

// ---------------------------------------------------------------------------
// Command handlers
// ---------------------------------------------------------------------------

async function explainSelection() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No active editor');
        return;
    }
    const selection = editor.selection;
    const text = editor.document.getText(selection);
    if (!text) {
        vscode.window.showWarningMessage('Select some code first');
        return;
    }
    const languageId = editor.document.languageId;
    await sendToFriday(`Explain this ${languageId} code:\n\n\`\`\`${languageId}\n${text}\n\`\`\``, 'explain');
}

async function fixError() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const selection = editor.selection;
    const text = editor.document.getText(selection);
    const input = await vscode.window.showInputBox({
        prompt: 'Describe the error',
        value: text ? `Fix this: ${text}` : '',
    });
    if (input) {
        await sendToFriday(input, 'fix');
    }
}

async function generateCode() {
    const prompt = await vscode.window.showInputBox({
        prompt: 'What code should Friday generate?',
    });
    if (prompt) {
        await sendToFriday(`Generate code: ${prompt}`, 'generate');
    }
}

async function researchTopic() {
    const topic = await vscode.window.showInputBox({
        prompt: 'Research topic',
    });
    if (topic) {
        await sendToFriday(`Research: ${topic}`, 'research');
    }
}

async function morningBriefing() {
    await sendToFriday('morning briefing', 'briefing');
}

async function openDashboard() {
    if (fridayPanel) {
        fridayPanel.reveal(vscode.ViewColumn.Active);
        return;
    }
    fridayPanel = vscode.window.createWebviewPanel(
        'fridayDashboard',
        'Friday Dashboard',
        vscode.ViewColumn.Active,
        {
            enableScripts: true,
            retainContextWhenHidden: true,
        },
    );
    fridayPanel.webview.html = getDashboardHtml();
    fridayPanel.onDidDispose(() => { fridayPanel = undefined; });
}

// ---------------------------------------------------------------------------
// Friday API client
// ---------------------------------------------------------------------------

function getConfig() {
    const cfg = vscode.workspace.getConfiguration('friday');
    return {
        apiUrl: cfg.get<string>('apiUrl') || 'http://localhost:8000',
        apiToken: cfg.get<string>('apiToken') || '',
    };
}

async function sendToFriday(message: string, kind: string) {
    const cfg = getConfig();
    const channel = vscode.window.createOutputChannel('Friday');
    channel.show(true);
    channel.appendLine(`◆ Friday [${kind}] — sending: ${message.substring(0, 100)}…`);

    // Use the SSE streaming endpoint for word-by-word display
    const url = new URL('/api/chat/stream', cfg.apiUrl);
    url.searchParams.set('message', message);
    url.searchParams.set('user_name', 'vscode');
    if (cfg.apiToken) {
        url.searchParams.set('token', cfg.apiToken);
    }

    return new Promise<void>((resolve) => {
        const req = http.get(url.toString(), (res) => {
            if (res.statusCode !== 200) {
                channel.appendLine(`✗ HTTP ${res.statusCode} — is Friday running at ${cfg.apiUrl}?`);
                channel.appendLine('  Start it with: uvicorn api.main:app --port 8000');
                resolve();
                return;
            }
            let buffer = '';
            res.on('data', (chunk: Buffer) => {
                buffer += chunk.toString();
                const lines = buffer.split('\n\n');
                buffer = lines.pop() || '';
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const payload = line.substring(6).trim();
                    if (payload === '[DONE]') {
                        channel.appendLine('\n✓ Done.');
                        resolve();
                        return;
                    }
                    try {
                        const evt = JSON.parse(payload);
                        if (evt.type === 'text') {
                            channel.append(evt.content);
                        } else if (evt.type === 'tool_call') {
                            channel.appendLine(`\n[tool] ${evt.raw}`);
                        } else if (evt.type === 'error') {
                            channel.appendLine(`\n✗ ${evt.message}`);
                        }
                    } catch (e) {
                        // ignore
                    }
                }
            });
            res.on('end', () => {
                channel.appendLine('\n✓ Stream ended.');
                resolve();
            });
        });
        req.on('error', (err) => {
            channel.appendLine(`✗ Request failed: ${err.message}`);
            channel.appendLine(`  Is Friday running at ${cfg.apiUrl}?`);
            channel.appendLine('  Start it with: uvicorn api.main:app --port 8000');
            resolve();
        });
    });
}

// ---------------------------------------------------------------------------
// Dashboard HTML — same sci-fi palette as CLI / web UI
// ---------------------------------------------------------------------------

function getDashboardHtml(): string {
    const cfg = getConfig();
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  body { background: ${COL_BG}; color: #E5E7EB; font-family: 'Inter', system-ui, sans-serif; padding: 16px; }
  h1 { color: ${COL_PRIMARY}; margin: 0 0 16px 0; font-size: 1.4em; }
  .card { background: rgba(124, 58, 237, 0.08); border: 1px solid ${COL_PRIMARY}; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
  .card h2 { color: ${COL_ACCENT}; margin: 0 0 8px 0; font-size: 1.05em; }
  .stat { display: inline-block; padding: 4px 12px; margin-right: 8px; background: rgba(6, 182, 212, 0.12); border-radius: 4px; color: ${COL_ACCENT}; }
  .api-link { color: ${COL_ACCENT}; text-decoration: none; font-size: 0.85em; }
  button { background: ${COL_PRIMARY}; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
  button:hover { background: #6D28D9; }
  input { background: rgba(255,255,255,0.05); border: 1px solid ${COL_PRIMARY}; color: white; padding: 8px; border-radius: 4px; width: 70%; }
  pre { background: rgba(0,0,0,0.3); padding: 8px; border-radius: 4px; white-space: pre-wrap; word-wrap: break-word; }
</style>
</head>
<body>
  <h1>◆ Friday Dashboard</h1>
  <div class="card">
    <h2>Quick Chat</h2>
    <input id="msg" placeholder="Ask Friday anything…" />
    <button onclick="sendMsg()">Send</button>
    <pre id="resp" style="margin-top:8px;">(responses appear here)</pre>
  </div>
  <div class="card">
    <h2>Backend</h2>
    <p>API URL: <code>${cfg.apiUrl}</code></p>
    <p>Token: <code>${cfg.apiToken ? '✓ set' : '∅ not set (dev mode)'}</code></p>
    <a class="api-link" href="https://open.bigmodel.cn" target="_blank">Get a free GLM key ↗</a>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    async function sendMsg() {
      const msg = document.getElementById('msg').value;
      const resp = document.getElementById('resp');
      resp.textContent = '...';
      try {
        const r = await fetch('${cfg.apiUrl}/api/chat', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ${cfg.apiToken ? `'Authorization': 'Bearer ${cfg.apiToken}',` : ''}
          },
          body: JSON.stringify({ message: msg }),
        });
        const data = await r.json();
        resp.textContent = data.response || data.detail || '(no response)';
      } catch (e) {
        resp.textContent = 'Error: ' + e.message + '\\nIs Friday running at ${cfg.apiUrl}?';
      }
    }
  </script>
</body>
</html>`;
}
