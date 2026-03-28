// static/api.js — Frontend API helper module
const API_BASE = '/api';

// XSS protection: escape HTML entities
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Safe markdown rendering: parse + sanitize
function safeMarkdown(text) {
  if (typeof marked === 'undefined') return escapeHtml(text);
  const html = marked.parse(text);
  if (typeof DOMPurify !== 'undefined') return DOMPurify.sanitize(html);
  return html;
}

// 从 URL 参数获取 session_id
function getSessionId() {
  return new URLSearchParams(window.location.search).get('session_id');
}

// 保存 session_id 到 URL（不刷新页面）
function setSessionId(id) {
  const url = new URL(window.location);
  url.searchParams.set('session_id', id);
  window.history.replaceState({}, '', url);
}

// 发送聊天消息
async function sendMessage(message, sessionId) {
  const body = { message };
  if (sessionId) body.session_id = sessionId;

  const resp = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

// 获取会话状态
async function getSessionState(sessionId) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/state`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}
