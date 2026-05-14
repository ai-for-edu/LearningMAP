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

// Get session_id from URL parameters
function getSessionId() {
  return new URLSearchParams(window.location.search).get('session_id');
}

// Save session_id to URL without page reload
function setSessionId(id) {
  const url = new URL(window.location);
  url.searchParams.set('session_id', id);
  window.history.replaceState({}, '', url);
}

// Send chat message (non-streaming fallback)
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

// SSE streaming helper: POST request, parse SSE events, call handlers
async function streamSSE(url, body, handlers) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!resp.ok) throw new Error(`API error: ${resp.status}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));
          if (handlers[data.type]) handlers[data.type](data);
        } catch (e) { /* skip parse errors */ }
      }
    }
  }
}

// Background generation API
async function startGeneration(sessionId, taskType) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/generate/${taskType}`, { method: 'POST' });
  if (!resp.ok) throw new Error(`Start generation failed: ${resp.status}`);
  return resp.json();
}

async function cancelGeneration(sessionId, taskType) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/generate/${taskType}/cancel`, { method: 'POST' });
  if (!resp.ok) throw new Error(`Cancel failed: ${resp.status}`);
  return resp.json();
}

async function getGenerationStatus(sessionId, taskType) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/generate/${taskType}/status`);
  if (!resp.ok) throw new Error(`Status check failed: ${resp.status}`);
  return resp.json();
}

function pollStatus(sessionId, taskType, onUpdate, onDone, interval = 2000) {
  let stopped = false;
  const poll = async () => {
    if (stopped) return;
    try {
      const status = await getGenerationStatus(sessionId, taskType);
      if (stopped) return;
      if (status.status === 'done') {
        onDone(status);
        return;
      }
      if (status.status === 'error') {
        onDone(status);
        return;
      }
      if (status.status === 'running') {
        onUpdate(status);
      }
      if (status.status === null) {
        // Task not running (cancelled or never started)
        onDone({ status: null });
        return;
      }
      setTimeout(poll, interval);
    } catch (err) {
      if (!stopped) setTimeout(poll, interval * 2);
    }
  };
  poll();
  return { stop: () => { stopped = true; } };
}

// Get session state
async function getSessionState(sessionId) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/state`);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

// Review flow (PR-3)
async function startReview(sessionId) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/start-review`, {
    method: 'POST',
  });
  if (resp.status === 404) {
    const body = await resp.json().catch(() => ({}));
    return { ok: false, reason: body.detail || 'not-available' };
  }
  if (!resp.ok) throw new Error(`start-review failed: ${resp.status}`);
  const data = await resp.json();
  return { ok: true, questions: data.questions };
}

async function submitReview(sessionId, answers) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/submit-review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  });
  if (!resp.ok) throw new Error(`submit-review failed: ${resp.status}`);
  const data = await resp.json();
  return data.review_result;
}

// Export session
async function exportSession(sessionId, format) {
  const state = await getSessionState(sessionId);
  let content, filename, mime;
  const name = (state.topic || 'session').replace(/[/\\?%*:|"<>]/g, '-');

  if (format === 'json') {
    content = JSON.stringify(state, null, 2);
    filename = `${name}-${sessionId.substring(0, 8)}.json`;
    mime = 'application/json';
  } else {
    content = buildMarkdownReport(state);
    filename = `${name}-${sessionId.substring(0, 8)}.md`;
    mime = 'text/markdown';
  }

  const blob = new Blob([content], { type: mime + ';charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// Skill API helpers
async function fetchSkills() {
  const resp = await fetch(`${API_BASE}/skills`);
  if (!resp.ok) return [];
  return resp.json();
}

async function setSkill(sessionId, skillName) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/set-skill`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skill: skillName }),
  });
  if (!resp.ok) throw new Error(`Set skill failed: ${resp.status}`);
  return resp.json();
}

async function setSessionMode(sessionId, mode) {
  const resp = await fetch(`${API_BASE}/chat/${sessionId}/set-mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!resp.ok) throw new Error(`Set mode failed: ${resp.status}`);
  return resp.json();
}

function buildMarkdownReport(state) {
  const topic = state.topic || 'Untitled Topic';
  let md = `# Learning Report: ${topic}\n\n`;

  // Conversation Log
  const messages = state.messages || [];
  if (messages.length > 0) {
    md += `## Conversation Log\n\n`;
    for (const m of messages) {
      const role = m.role === 'ai' ? 'AI' : 'Student';
      // Truncate very long AI messages (lesson plans etc.)
      const content = m.content.length > 200 ? m.content.substring(0, 200) + '...' : m.content;
      md += `- **${role}:** ${content}\n`;
    }
    md += '\n';
  }

  // Knowledge Graph
  if (state.graph && state.graph.nodes) {
    md += `## Knowledge Graph\n\n`;
    md += `| Knowledge Point | Tier | Description |\n|-----------------|------|-------------|\n`;
    const levelMap = { prerequisite: 'prerequisite', core: 'core', extension: 'extension' };
    for (const n of state.graph.nodes) {
      md += `| ${n.name} | ${levelMap[n.level] || n.level} | ${n.description} |\n`;
    }
    md += '\n';
  }

  // Diagnosis Quiz
  const questions = state.questions || [];
  const answers = state.answers || [];
  if (questions.length > 0) {
    md += `## Diagnosis Quiz\n\n`;
    const letters = ['A', 'B', 'C', 'D'];
    for (let i = 0; i < questions.length; i++) {
      const q = questions[i];
      const studentIdx = i < answers.length ? answers[i] : -1;
      const correctIdx = q.answer;
      const isCorrect = studentIdx === correctIdx;
      const mark = studentIdx === -1 ? 'Not answered' : (isCorrect ? '✓' : '✗');

      md += `### Question ${i + 1} (${q.knowledge_point}) ${mark}\n\n`;
      md += `**Question:** ${q.question}\n\n`;
      for (let j = 0; j < q.options.length; j++) {
        let prefix = `${letters[j]}) ${q.options[j]}`;
        if (j === studentIdx && j === correctIdx) prefix += ' ← Student answer ✓';
        else if (j === studentIdx) prefix += ' ← Student answer ✗';
        else if (j === correctIdx) prefix += ' ← Correct answer';
        md += `- ${prefix}\n`;
      }
      md += '\n';
    }
  }

  // Diagnosis Result
  const r = state.diagnosis_result;
  if (r) {
    md += `## Diagnosis Result\n\n`;
    md += `**Score: ${r.score}%** (${r.correct}/${r.total})\n\n`;
    md += `| Knowledge Point | Status | Details |\n|-----------------|--------|--------|\n`;
    const statusMap = { mastered: 'Mastered ✓', weak: 'Needs review ✗', skipped: 'Skipped —' };
    for (const b of r.breakdown) {
      md += `| ${b.name} | ${statusMap[b.status] || b.status} | ${b.detail} |\n`;
    }
    md += '\n';
  }

  // Personalized Lesson
  if (state.lesson_content) {
    md += `## Personalized Lesson\n\n${state.lesson_content}\n`;
  }

  return md;
}
