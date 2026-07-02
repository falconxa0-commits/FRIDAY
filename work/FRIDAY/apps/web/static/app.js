/* =============================================
   FRIDAY AI Assistant — JavaScript Application
   ============================================= */

// ---- Configuration ----
const API_BASE = window.location.origin;
const WS_URL = (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + '/api/stream';
const RECONNECT_DELAY = 2000;
const MAX_RECONNECT_ATTEMPTS = 10;

// ---- State ----
let ws = null;
let reconnectAttempts = 0;
let isStreaming = false;
let currentAssistantEl = null;
let currentAssistantText = '';
let currentToolCalls = [];
let messageCount = 0;
let toolCallCount = 0;
let currentAgentType = null;
let allMemories = [];

// ---- Initialize ----
document.addEventListener('DOMContentLoaded', () => {
  setGreeting();
  connectWebSocket();
  loadBrainStats();
  refreshActions();
  refreshIntegrations();

  // Auto-refresh actions every 15s
  setInterval(refreshActions, 15000);
});

// ---- Greeting based on time ----
function setGreeting() {
  const hour = new Date().getHours();
  const el = document.getElementById('greeting');
  if (el) {
    if (hour < 12) el.textContent = 'morning';
    else if (hour < 17) el.textContent = 'afternoon';
    else el.textContent = 'evening';
  }
}

// ================================================
// WebSocket Connection
// ================================================

function connectWebSocket() {
  updateConnectionStatus('connecting');
  
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    console.error('WebSocket creation failed:', e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log('WebSocket connected');
    reconnectAttempts = 0;
    updateConnectionStatus('connected');
    
    // Send auth token if available
    const token = localStorage.getItem('friday_token') || 'dev_token';
    ws.send(JSON.stringify({ token }));
  };

  ws.onmessage = (event) => {
    handleWSMessage(event.data);
  };

  ws.onclose = (event) => {
    console.log('WebSocket closed:', event.code, event.reason);
    updateConnectionStatus('disconnected');
    if (!event.wasClean) {
      scheduleReconnect();
    }
  };

  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
    updateConnectionStatus('error');
  };
}

function scheduleReconnect() {
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    console.log('Max reconnection attempts reached');
    updateConnectionStatus('failed');
    return;
  }
  
  reconnectAttempts++;
  const delay = RECONNECT_DELAY * Math.pow(1.5, reconnectAttempts - 1);
  console.log(`Reconnecting in ${Math.round(delay)}ms (attempt ${reconnectAttempts})`);
  
  setTimeout(() => {
    connectWebSocket();
  }, Math.min(delay, 30000));
}

function updateConnectionStatus(status) {
  const statusEl = document.getElementById('connectionStatus');
  const systemEl = document.getElementById('systemStatus');
  
  const configs = {
    connected: { dot: 'status-online', text: 'Connected', systemText: 'FRIDAY Online' },
    connecting: { dot: 'status-connecting', text: 'Connecting...', systemText: 'Connecting...' },
    disconnected: { dot: 'status-offline', text: 'Disconnected', systemText: 'Disconnected' },
    error: { dot: 'status-offline', text: 'Error', systemText: 'Connection Error' },
    failed: { dot: 'status-offline', text: 'Failed', systemText: 'Connection Failed' },
  };
  
  const cfg = configs[status] || configs.disconnected;
  
  if (statusEl) {
    statusEl.innerHTML = `<span class="status-dot ${cfg.dot}"></span> ${cfg.text}`;
  }
  if (systemEl) {
    systemEl.innerHTML = `<div class="status-dot ${cfg.dot}"></div><span>${cfg.systemText}</span>`;
  }
}

function handleWSMessage(data) {
  // Try to parse as JSON first
  try {
    const parsed = JSON.parse(data);
    
    // Handle different message types
    if (parsed.type === 'ping') {
      ws.send('__pong__');
      return;
    }
    
    if (parsed.pending_actions !== undefined) {
      updateActionBadge(parsed.pending_actions.length);
      return;
    }
    
    if (parsed.error) {
      showToast(parsed.error, 'error');
      return;
    }
  } catch (e) {
    // Not JSON — treat as streamed text chunk
  }
  
  // Stream text
  if (isStreaming) {
    appendStreamChunk(data);
  }
}

// ================================================
// Chat
// ================================================

function sendMessage() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text || isStreaming) return;
  
  // Hide welcome screen
  const welcome = document.getElementById('welcomeScreen');
  if (welcome) welcome.style.display = 'none';
  
  // Add user message
  addMessage('user', text);
  input.value = '';
  autoResize(input);
  
  // Start streaming
  startStreaming(text);
}

function sendQuick(text) {
  const input = document.getElementById('chatInput');
  input.value = text;
  sendMessage();
}

function handleInputKey(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 150) + 'px';
}

function startStreaming(text) {
  isStreaming = true;
  currentAssistantText = '';
  currentToolCalls = [];
  
  // Show streaming indicator
  const indicator = document.getElementById('streamingIndicator');
  if (indicator) indicator.style.display = 'flex';
  
  // Disable send button
  document.getElementById('btnSend').disabled = true;
  
  // Create assistant message bubble
  currentAssistantEl = addMessage('assistant', '', true);
  
  // Send via WebSocket
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(text);
  } else {
    // Fallback to SSE
    streamViaSSE(text);
  }
}

function streamViaSSE(text) {
  const token = localStorage.getItem('friday_token') || 'dev_token';
  const eventSource = new EventSource(
    `${API_BASE}/api/chat/stream?message=${encodeURIComponent(text)}&token=${encodeURIComponent(token)}`
  );
  
  eventSource.onmessage = (event) => {
    if (event.data === '[DONE]') {
      eventSource.close();
      finishStreaming();
      return;
    }
    
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'tool_call') {
        addToolCallToUI(data.name, data.input, data.output);
      } else if (data.type === 'text') {
        appendStreamChunk(data.content);
      }
    } catch (e) {
      // Plain text chunk
      appendStreamChunk(event.data);
    }
  };
  
  eventSource.onerror = () => {
    eventSource.close();
    if (isStreaming) {
      finishStreaming();
    }
  };
}

function appendStreamChunk(chunk) {
  if (!currentAssistantEl) return;
  
  currentAssistantText += chunk;
  const bubble = currentAssistantEl.querySelector('.message-bubble');
  if (bubble) {
    bubble.innerHTML = renderMarkdown(currentAssistantText);
    bubble.classList.add('streaming-cursor');
    
    // Scroll to bottom
    const container = document.getElementById('chatMessages');
    if (container) container.scrollTop = container.scrollHeight;
  }
}

function finishStreaming() {
  isStreaming = false;
  
  // Remove streaming cursor
  if (currentAssistantEl) {
    const bubble = currentAssistantEl.querySelector('.message-bubble');
    if (bubble) bubble.classList.remove('streaming-cursor');
  }
  
  currentAssistantEl = null;
  
  // Hide streaming indicator
  const indicator = document.getElementById('streamingIndicator');
  if (indicator) indicator.style.display = 'none';
  
  // Enable send button
  document.getElementById('btnSend').disabled = false;
  
  // Update stats
  messageCount++;
  updateConvStats();
}

function addMessage(role, content, isStreaming = false) {
  const container = document.getElementById('chatMessages');
  if (!container) return null;
  
  const msgDiv = document.createElement('div');
  msgDiv.className = `message ${role}`;
  
  const avatarText = role === 'user' ? 'Y' : 'F';
  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  
  msgDiv.innerHTML = `
    <div class="message-avatar">${avatarText}</div>
    <div class="message-content">
      <div class="message-bubble">${isStreaming ? '' : renderMarkdown(content)}</div>
      <div class="message-meta">${time}</div>
    </div>
  `;
  
  container.appendChild(msgDiv);
  container.scrollTop = container.scrollHeight;
  
  return msgDiv;
}

function addToolCallToUI(name, input, output) {
  toolCallCount++;
  updateConvStats();
  
  if (!currentAssistantEl) return;
  
  const content = currentAssistantEl.querySelector('.message-content');
  if (!content) return;
  
  const template = document.getElementById('toolCallTemplate');
  const clone = template.content.cloneNode(true);
  
  clone.querySelector('.tool-name').textContent = name;
  clone.querySelector('.tool-input').textContent = 
    typeof input === 'string' ? input : JSON.stringify(input, null, 2);
  clone.querySelector('.tool-output').textContent = 
    typeof output === 'string' ? output : JSON.stringify(output, null, 2);
  
  // Insert before message-meta
  const meta = content.querySelector('.message-meta');
  content.insertBefore(clone, meta);
}

function clearContext() {
  fetch(`${API_BASE}/api/chat/clear`, { method: 'POST', headers: getHeaders() })
    .then(r => r.json())
    .then(data => {
      const container = document.getElementById('chatMessages');
      container.innerHTML = '';
      messageCount = 0;
      toolCallCount = 0;
      updateConvStats();
      showToast('Conversation cleared', 'success');
    })
    .catch(err => showToast('Failed to clear: ' + err.message, 'error'));
}

function updateConvStats() {
  const msgEl = document.getElementById('convMsgCount');
  const toolEl = document.getElementById('convToolCount');
  if (msgEl) msgEl.textContent = messageCount;
  if (toolEl) toolEl.textContent = toolCallCount;
}

// ================================================
// Markdown Rendering
// ================================================

function renderMarkdown(text) {
  if (!text) return '';
  
  let html = escapeHtml(text);
  
  // Code blocks (``` ... ```)
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
    return `<pre><code class="lang-${lang}">${code.trim()}</code></pre>`;
  });
  
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  
  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  
  // Italic
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  
  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  
  // Unordered lists
  html = html.replace(/^[*-] (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`);
  
  // Blockquotes
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  
  // Links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  
  // Paragraphs — convert double newlines
  html = html.replace(/\n\n/g, '</p><p>');
  
  // Single newlines
  html = html.replace(/\n/g, '<br>');
  
  // Wrap in paragraph
  html = `<p>${html}</p>`;
  
  // Clean up empty paragraphs
  html = html.replace(/<p>\s*<\/p>/g, '');
  
  return html;
}

function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return text.replace(/[&<>"']/g, c => map[c]);
}

// ================================================
// Views / Navigation
// ================================================

function switchView(viewName, navEl) {
  // Deactivate all nav items
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  if (navEl) navEl.classList.add('active');
  
  // Hide all views
  document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
  
  // Show target view
  const view = document.getElementById('view' + viewName.charAt(0).toUpperCase() + viewName.slice(1));
  if (view) view.classList.add('active');
  
  // Load view data
  switch (viewName) {
    case 'memory': refreshMemories(); break;
    case 'agents': loadAgentTypes(); break;
    case 'integrations': refreshIntegrations(); break;
    case 'actions': refreshActions(); break;
  }
  
  // Close mobile sidebar
  if (window.innerWidth <= 768) {
    closeSidebar('left');
  }
  
  return false;
}

// ================================================
// Sidebar Toggle
// ================================================

function toggleSidebar(side) {
  const sidebar = document.getElementById(side === 'left' ? 'sidebarLeft' : 'sidebarRight');
  if (!sidebar) return;
  
  if (window.innerWidth <= 1024 && side === 'right') {
    sidebar.classList.toggle('open');
  } else if (window.innerWidth <= 768 && side === 'left') {
    sidebar.classList.toggle('open');
  } else {
    sidebar.classList.toggle('collapsed');
  }
}

function closeSidebar(side) {
  const sidebar = document.getElementById(side === 'left' ? 'sidebarLeft' : 'sidebarRight');
  if (!sidebar) return;
  sidebar.classList.remove('open');
}

// ================================================
// Memory Panel
// ================================================

function refreshMemories() {
  const list = document.getElementById('memoryList');
  if (!list) return;
  
  list.innerHTML = '<div class="skeleton-card"></div><div class="skeleton-card"></div>';
  
  fetch(`${API_BASE}/api/memory/all`, { headers: getHeaders() })
    .then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(data => {
      allMemories = Array.isArray(data) ? data : [];
      renderMemories(allMemories);
    })
    .catch(err => {
      list.innerHTML = `<div class="empty-state"><p>Error loading memories: ${err.message}</p></div>`;
    });
}

function renderMemories(memories) {
  const list = document.getElementById('memoryList');
  if (!list) return;
  
  if (memories.length === 0) {
    list.innerHTML = '<div class="empty-state"><p>No memories found</p></div>';
    return;
  }
  
  list.innerHTML = memories.map((m, i) => {
    const content = m.content || m.text || JSON.stringify(m);
    const category = m.metadata?.category || m.category || 'general';
    const timestamp = m.timestamp || m.metadata?.timestamp || '';
    
    return `
      <div class="memory-card">
        <div class="memory-card-content">
          <p>${escapeHtml(content)}</p>
          <div class="memory-card-meta">
            <span class="memory-category">${escapeHtml(category)}</span>
            ${timestamp ? `<span>${new Date(timestamp).toLocaleDateString()}</span>` : ''}
          </div>
        </div>
        <div class="memory-card-actions">
          <button class="btn-icon" onclick="deleteMemory(${m.id || i})" title="Delete">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      </div>
    `;
  }).join('');
}

function searchMemories(query) {
  if (!query.trim()) {
    renderMemories(allMemories);
    return;
  }
  
  const filtered = allMemories.filter(m => {
    const content = (m.content || m.text || '').toLowerCase();
    return content.includes(query.toLowerCase());
  });
  renderMemories(filtered);
}

function addMemory() {
  const textInput = document.getElementById('memoryAddText');
  const categorySelect = document.getElementById('memoryAddCategory');
  
  const text = textInput.value.trim();
  const category = categorySelect.value;
  
  if (!text) {
    showToast('Please enter a memory', 'warning');
    return;
  }
  
  fetch(`${API_BASE}/api/memory/`, {
    method: 'POST',
    headers: { ...getHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, metadata: { category } })
  })
    .then(r => r.json())
    .then(data => {
      textInput.value = '';
      showToast('Memory added', 'success');
      refreshMemories();
    })
    .catch(err => showToast('Failed to add memory: ' + err.message, 'error'));
}

function deleteMemory(id) {
  fetch(`${API_BASE}/api/memory/${id}`, {
    method: 'DELETE',
    headers: getHeaders()
  })
    .then(r => r.json())
    .then(data => {
      showToast('Memory deleted', 'success');
      refreshMemories();
    })
    .catch(err => showToast('Failed to delete: ' + err.message, 'error'));
}

// ================================================
// Agents
// ================================================

function loadAgentTypes() {
  fetch(`${API_BASE}/api/agents/types`, { headers: getHeaders() })
    .then(r => r.json())
    .then(data => {
      // Agent types are already shown statically
      console.log('Available agent types:', data);
    })
    .catch(err => console.error('Failed to load agent types:', err));
}

function runAgent(type) {
  currentAgentType = type;
  
  const promptArea = document.getElementById('agentPromptArea');
  const titleEl = document.getElementById('agentPromptTitle');
  const resultEl = document.getElementById('agentResult');
  
  if (titleEl) titleEl.textContent = `${type.charAt(0).toUpperCase() + type.slice(1)} Agent Prompt`;
  if (resultEl) resultEl.style.display = 'none';
  if (promptArea) promptArea.style.display = 'block';
  
  document.getElementById('agentPromptInput').focus();
}

function executeAgent() {
  const prompt = document.getElementById('agentPromptInput').value.trim();
  if (!prompt) {
    showToast('Please enter a prompt', 'warning');
    return;
  }
  
  const resultEl = document.getElementById('agentResult');
  resultEl.style.display = 'block';
  resultEl.textContent = 'Executing...';
  
  fetch(`${API_BASE}/api/agents/task`, {
    method: 'POST',
    headers: { ...getHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_type: currentAgentType, prompt })
  })
    .then(r => r.json())
    .then(data => {
      resultEl.textContent = JSON.stringify(data, null, 2);
      showToast('Agent completed', 'success');
    })
    .catch(err => {
      resultEl.textContent = 'Error: ' + err.message;
      showToast('Agent failed: ' + err.message, 'error');
    });
}

function cancelAgent() {
  currentAgentType = null;
  const promptArea = document.getElementById('agentPromptArea');
  if (promptArea) promptArea.style.display = 'none';
}

// ================================================
// Integrations
// ================================================

function refreshIntegrations() {
  const grid = document.getElementById('integrationGrid');
  if (!grid) return;
  
  grid.innerHTML = '<div class="skeleton-card"></div><div class="skeleton-card"></div>';
  
  fetch(`${API_BASE}/api/integrations/`, { headers: getHeaders() })
    .then(r => r.json())
    .then(data => {
      const services = data.integrations || [];
      
      // Also fetch available services
      return fetch(`${API_BASE}/api/integrations/available`, { headers: getHeaders() })
        .then(r => r.json())
        .then(availData => {
          const available = availData.available || [];
          renderIntegrations(services, available);
        })
        .catch(() => renderIntegrations(services, []));
    })
    .catch(err => {
      grid.innerHTML = `<div class="empty-state"><p>Error loading integrations: ${err.message}</p></div>`;
    });
}

function renderIntegrations(services, available) {
  const grid = document.getElementById('integrationGrid');
  if (!grid) return;
  
  // Group by category via registry
  const categories = {
    'Core': ['Weather', 'Calendar', 'Gmail', 'Spotify', 'HomeAssistant'],
    'Finance': ['CryptoTracker', 'Finance'],
    'Information': ['GlobalPulse'],
  };
  
  let html = '';
  for (const [cat, svcs] of Object.entries(categories)) {
    const categoryServices = services.filter(s => svcs.includes(s));
    if (categoryServices.length === 0 && svcs.length > 0) {
      // Show registered services even if not in the list
      html += `
        <div class="integration-category">
          <div class="integration-category-header">${cat}</div>
          <div class="integration-category-body">
            ${svcs.map(s => {
              const isAvailable = available.includes(s);
              const isRegistered = categoryServices.includes(s);
              return `
                <div class="integration-item">
                  <div class="integration-status ${isAvailable ? 'available' : isRegistered ? 'registered' : 'unavailable'}"></div>
                  <span>${s}</span>
                </div>
              `;
            }).join('')}
          </div>
        </div>
      `;
    }
  }
  
  if (!html) {
    html = '<div class="empty-state"><p>No integrations found</p></div>';
  }
  
  grid.innerHTML = html;
}

// ================================================
// Actions
// ================================================

function refreshActions() {
  fetch(`${API_BASE}/api/actions/`, { headers: getHeaders() })
    .then(r => r.json())
    .then(data => {
      const pending = data.pending || [];
      updateActionBadge(pending.length);
      renderActions(pending);
    })
    .catch(err => console.error('Failed to load actions:', err));
}

function renderActions(actions) {
  const list = document.getElementById('actionsList');
  if (!list) return;
  
  if (actions.length === 0) {
    list.innerHTML = `
      <div class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#30363D" stroke-width="1.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
        <p>No pending actions</p>
      </div>
    `;
    return;
  }
  
  list.innerHTML = actions.map(action => {
    const risk = action.risk_level || 'low';
    const timestamp = action.timestamp ? new Date(action.timestamp).toLocaleString() : '';
    
    return `
      <div class="action-card">
        <div class="action-card-header">
          <span class="action-card-title">
            <code>${action.component || ''}.${action.action || ''}</code>
          </span>
          <span class="risk-badge ${risk}">${risk} risk</span>
        </div>
        <div class="action-card-body">
          ${action.params ? `<code>${JSON.stringify(action.params)}</code>` : ''}
        </div>
        <div class="action-card-footer">
          <button class="btn-success" onclick="approveAction('${action.id}')">Approve</button>
          <button class="btn-danger" onclick="rejectAction('${action.id}')">Reject</button>
          <span class="timestamp">${timestamp}</span>
        </div>
      </div>
    `;
  }).join('');
}

function approveAction(id) {
  fetch(`${API_BASE}/api/actions/${id}/approve`, {
    method: 'POST',
    headers: getHeaders()
  })
    .then(r => r.json())
    .then(data => {
      showToast('Action approved', 'success');
      refreshActions();
    })
    .catch(err => showToast('Failed to approve: ' + err.message, 'error'));
}

function rejectAction(id) {
  fetch(`${API_BASE}/api/actions/${id}/reject`, {
    method: 'POST',
    headers: getHeaders()
  })
    .then(r => r.json())
    .then(data => {
      showToast('Action rejected', 'info');
      refreshActions();
    })
    .catch(err => showToast('Failed to reject: ' + err.message, 'error'));
}

function updateActionBadge(count) {
  const badge = document.getElementById('actionBadge');
  if (badge) {
    if (count > 0) {
      badge.style.display = 'inline';
      badge.textContent = count;
    } else {
      badge.style.display = 'none';
    }
  }
}

// ================================================
// Brain Stats
// ================================================

function loadBrainStats() {
  fetch(`${API_BASE}/api/chat/stats`, { headers: getHeaders() })
    .then(r => r.json())
    .then(data => {
      const provider = document.getElementById('statProvider');
      const history = document.getElementById('statHistory');
      const skills = document.getElementById('statSkills');
      const memory = document.getElementById('statMemory');
      
      if (provider) provider.textContent = data.provider || '—';
      if (history) history.textContent = data.history_length || 0;
      if (skills) skills.textContent = (data.skills_loaded || []).length;
      if (memory) memory.textContent = data.memory_enabled ? 'On' : 'Off';
      
      // Update context panel
      updateSkills(data.skills_loaded || []);
      updateTools(data.tools_available || []);
    })
    .catch(err => console.error('Failed to load brain stats:', err));
}

function updateSkills(skills) {
  const list = document.getElementById('skillList');
  if (!list) return;
  
  if (skills.length === 0) {
    list.innerHTML = '<span class="skill-tag">None loaded</span>';
    return;
  }
  
  list.innerHTML = skills.map(s => `<span class="skill-tag">${s}</span>`).join('');
}

function updateTools(tools) {
  const list = document.getElementById('toolList');
  if (!list) return;
  
  if (tools.length === 0) {
    list.innerHTML = '<span class="tool-tag">None</span>';
    return;
  }
  
  list.innerHTML = tools.map(t => `<span class="tool-tag">${t}</span>`).join('');
}

// ================================================
// Utility
// ================================================

function getHeaders() {
  const token = localStorage.getItem('friday_token') || 'dev_token';
  return { 'Authorization': `Bearer ${token}` };
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  
  const icons = { success: '✓', error: '✕', warning: '⚠', info: 'ℹ' };
  
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || 'ℹ'}</span>
    <span>${escapeHtml(message)}</span>
  `;
  
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(20px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ================================================
// Memory export / import (Section 5b)
// ================================================

async function exportMemories() {
  try {
    const resp = await fetch('/api/memory/export', {
      headers: { 'Authorization': 'Bearer ' + (window.FRIDAY_TOKEN || '') }
    });
    if (!resp.ok) throw new Error('Export failed: ' + resp.status);
    const data = await resp.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'friday_memories_' + new Date().toISOString().slice(0,10) + '.json';
    a.click();
    URL.revokeObjectURL(url);
    showToast('Exported ' + data.memory_count + ' memories');
  } catch (e) {
    showToast('Export failed: ' + e.message);
  }
}

async function importMemories(event) {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const memories = parsed.memories || parsed;
    const resp = await fetch('/api/memory/import', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + (window.FRIDAY_TOKEN || ''),
      },
      body: JSON.stringify({ memories: memories }),
    });
    if (!resp.ok) throw new Error('Import failed: ' + resp.status);
    const result = await resp.json();
    showToast(result.message || 'Import complete');
    refreshMemories();
  } catch (e) {
    showToast('Import failed: ' + e.message);
  }
  event.target.value = '';  // reset so same file can be re-imported
}

// ================================================
// Creative Suite (Section 9 — Z.ai image + video)
// ================================================

async function generateImage() {
  const prompt = document.getElementById('imagePrompt').value.trim();
  const resultDiv = document.getElementById('imageResult');
  if (!prompt) { showToast('Enter a prompt first'); return; }

  resultDiv.innerHTML = '<p>Generating image via CogView-3… (5-30 seconds)</p>';

  try {
    // Use the integrations route to call ImageGen
    const resp = await fetch('/api/integrations/execute', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + (window.FRIDAY_TOKEN || ''),
      },
      body: JSON.stringify({
        service: 'ImageGen',
        action: 'generate_image',
        params: { prompt: prompt, size: '1024x1024' },
      }),
    });
    const data = await resp.json();
    if (data.status === 'success' && data.receipt && data.receipt.data && data.receipt.data.image_url) {
      resultDiv.innerHTML =
        '<p class="creative-success">Image generated:</p>' +
        '<img src="' + data.receipt.data.image_url + '" alt="Generated" style="max-width:100%;border-radius:8px;margin-top:8px">';
    } else if (data.status === 'not_implemented') {
      resultDiv.innerHTML = '<p class="creative-warn">' + (data.message || 'ImageGen not configured') + '</p>';
    } else {
      resultDiv.innerHTML = '<p class="creative-error">Status: ' + data.status + ' — ' + (data.message || '') + '</p>';
    }
  } catch (e) {
    resultDiv.innerHTML = '<p class="creative-error">Request failed: ' + e.message + '</p>';
  }
}

async function generateVideo() {
  const prompt = document.getElementById('videoPrompt').value.trim();
  const resultDiv = document.getElementById('videoResult');
  if (!prompt) { showToast('Enter a prompt first'); return; }

  resultDiv.innerHTML = '<p>Generating video via CogVideoX… <strong>(1-3 minutes)</strong></p>';

  try {
    const resp = await fetch('/api/integrations/execute', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + (window.FRIDAY_TOKEN || ''),
      },
      body: JSON.stringify({
        service: 'VideoGen',
        action: 'generate_video',
        params: { prompt: prompt, quality: 'quality' },
      }),
    });
    const data = await resp.json();
    if (data.status === 'success' && data.receipt && data.receipt.data) {
      const url = data.receipt.data.video_url || data.receipt.data.task_id;
      resultDiv.innerHTML =
        '<p class="creative-success">Video task complete:</p>' +
        '<pre>' + JSON.stringify(data.receipt.data, null, 2) + '</pre>';
    } else if (data.status === 'not_implemented') {
      resultDiv.innerHTML = '<p class="creative-warn">' + (data.message || 'VideoGen not configured') + '</p>';
    } else {
      resultDiv.innerHTML = '<p class="creative-error">Status: ' + data.status + ' — ' + (data.message || '') + '</p>';
    }
  } catch (e) {
    resultDiv.innerHTML = '<p class="creative-error">Request failed: ' + e.message + '</p>';
  }
}

// ================================================
// Trust & Stats (Section 9 + 10c)
// ================================================

async function loadTrustStats() {
  // 1. Benchmark pass rate from benchmarks/results.md (via static file or API)
  try {
    const r = await fetch('/api/stats', { headers: { 'Authorization': 'Bearer ' + (window.FRIDAY_TOKEN || '') } });
    const stats = await r.json();
    document.getElementById('costTracker').textContent =
      '$' + (stats.total_cost_usd || 0).toFixed(6) + ' (' + stats.total_requests + ' reqs)';
    document.getElementById('costSource').textContent = stats.provider_cost_note || '';
    if (stats.zai_rate_limits) {
      document.getElementById('rateLimit').textContent =
        stats.rate_limit_warning
          ? stats.rate_limit_warning.rpm_pct.toFixed(0) + '% RPM'
          : 'OK';
      document.getElementById('rateLimitSource').textContent = stats.zai_rate_limits.description || '';
    } else {
      document.getElementById('rateLimit').textContent = 'N/A';
      document.getElementById('rateLimitSource').textContent = 'Not on GLM tier';
    }
  } catch (e) {
    document.getElementById('costTracker').textContent = 'Error';
  }

  // 2. Benchmark pass rate — try to read benchmarks/results.md
  try {
    const r = await fetch('/static/benchmarks/results.md');
    if (r.ok) {
      const text = await r.text();
      // Look for "X/Y tests passing" or "pass rate: Z%"
      const m = text.match(/(\d+)\s*\/\s*(\d+)\s*(?:tests?\s*)?pass/i)
              || text.match(/pass\s*rate[:\s]+(\d+(?:\.\d+)?)\s*%/i);
      if (m) {
        document.getElementById('benchmarkRate').textContent = m[0];
      } else {
        document.getElementById('benchmarkRate').textContent = 'See results.md';
      }
    } else {
      document.getElementById('benchmarkRate').textContent = 'N/A';
    }
  } catch (e) {
    document.getElementById('benchmarkRate').textContent = 'Error';
  }
}

async function runTrustReport() {
  const resultDiv = document.getElementById('trustReportResult');
  resultDiv.innerHTML = '<p>Running hellfire_audit.py live…</p>';
  try {
    const r = await fetch('/api/trust/report', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + (window.FRIDAY_TOKEN || '') },
    });
    const data = await r.json();
    let html = '<div class="trust-report-summary">';
    html += '<strong>Passed:</strong> ' + data.passed + '/' + data.total_checks;
    html += ' | <strong>Failed:</strong> ' + data.failed;
    html += '</div>';
    if (data.failures && data.failures.length > 0) {
      html += '<ul class="trust-failures">';
      for (const f of data.failures) {
        html += '<li><strong>' + f.label + '</strong>: ' + f.detail + '</li>';
      }
      html += '</ul>';
    }
    html += '<ul class="trust-checks">';
    for (const c of data.checks) {
      const status = c.passed ? '✓' : '✗';
      const cls = c.passed ? 'trust-check-pass' : 'trust-check-fail';
      html += '<li class="' + cls + '">' + status + ' ' + c.name + '</li>';
    }
    html += '</ul>';
    resultDiv.innerHTML = html;
  } catch (e) {
    resultDiv.innerHTML = '<p class="creative-error">Trust report failed: ' + e.message + '</p>';
  }
}
