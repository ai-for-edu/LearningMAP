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

// 导出会话
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

function buildMarkdownReport(state) {
  const topic = state.topic || '未命名主题';
  let md = `# 学习报告：${topic}\n\n`;

  // 对话记录
  const messages = state.messages || [];
  if (messages.length > 0) {
    md += `## 对话记录\n\n`;
    for (const m of messages) {
      const role = m.role === 'ai' ? 'AI' : '学生';
      // 截断过长的 AI 消息（教案等）
      const content = m.content.length > 200 ? m.content.substring(0, 200) + '...' : m.content;
      md += `- **${role}：** ${content}\n`;
    }
    md += '\n';
  }

  // 知识图谱
  if (state.graph && state.graph.nodes) {
    md += `## 知识图谱\n\n`;
    md += `| 知识点 | 层级 | 描述 |\n|--------|------|------|\n`;
    const levelMap = { prerequisite: '前置', core: '核心', extension: '拓展' };
    for (const n of state.graph.nodes) {
      md += `| ${n.name} | ${levelMap[n.level] || n.level} | ${n.description} |\n`;
    }
    md += '\n';
  }

  // 诊断测验
  const questions = state.questions || [];
  const answers = state.answers || [];
  if (questions.length > 0) {
    md += `## 诊断测验\n\n`;
    const letters = ['A', 'B', 'C', 'D'];
    for (let i = 0; i < questions.length; i++) {
      const q = questions[i];
      const studentIdx = i < answers.length ? answers[i] : -1;
      const correctIdx = q.answer;
      const isCorrect = studentIdx === correctIdx;
      const mark = studentIdx === -1 ? '未作答' : (isCorrect ? '✓' : '✗');

      md += `### 第 ${i + 1} 题（${q.knowledge_point}）${mark}\n\n`;
      md += `**题目：** ${q.question}\n\n`;
      for (let j = 0; j < q.options.length; j++) {
        let prefix = `${letters[j]}) ${q.options[j]}`;
        if (j === studentIdx && j === correctIdx) prefix += ' ← 学生答案 ✓';
        else if (j === studentIdx) prefix += ' ← 学生答案 ✗';
        else if (j === correctIdx) prefix += ' ← 正确答案';
        md += `- ${prefix}\n`;
      }
      md += '\n';
    }
  }

  // 诊断结果
  const r = state.diagnosis_result;
  if (r) {
    md += `## 诊断结果\n\n`;
    md += `**正确率：${r.score}%**（${r.correct}/${r.total}）\n\n`;
    md += `| 知识点 | 状态 | 详情 |\n|--------|------|------|\n`;
    const statusMap = { mastered: '已掌握 ✓', weak: '需复习 ✗', skipped: '已跳过 —' };
    for (const b of r.breakdown) {
      md += `| ${b.name} | ${statusMap[b.status] || b.status} | ${b.detail} |\n`;
    }
    md += '\n';
  }

  // 个性化教案
  if (state.lesson_content) {
    md += `## 个性化教案\n\n${state.lesson_content}\n`;
  }

  return md;
}
