# Session 导出功能设计

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 支持将完整学习会话导出为 Markdown 或 JSON 文件

**Architecture:** 纯前端实现，从 state API 获取数据，在浏览器端组装并下载。不需要新后端接口。

**Tech Stack:** 现有 JS + state API

---

## Task 1: api.js 加导出函数

**Files:**
- Modify: `static/api.js`

**Step 1: 实现 exportSession 函数**

```javascript
async function exportSession(sessionId, format) {
  const state = await getSessionState(sessionId);
  let content, filename, mime;

  if (format === 'json') {
    content = JSON.stringify(state, null, 2);
    filename = `${state.topic || 'session'}-${sessionId.substring(0, 8)}.json`;
    mime = 'application/json';
  } else {
    content = buildMarkdownReport(state);
    filename = `${state.topic || 'session'}-${sessionId.substring(0, 8)}.md`;
    mime = 'text/markdown';
  }

  // Trigger browser download
  const blob = new Blob([content], { type: mime + ';charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function buildMarkdownReport(state) {
  let md = '';
  const topic = state.topic || '未命名主题';

  // Title
  md += `# 学习报告：${topic}\n\n`;

  // Knowledge graph
  if (state.graph) {
    md += `## 知识图谱\n\n`;
    md += `| 知识点 | 层级 | 描述 |\n|--------|------|------|\n`;
    for (const n of state.graph.nodes || []) {
      const level = {prerequisite:'前置', core:'核心', extension:'拓展'}[n.level] || n.level;
      md += `| ${n.name} | ${level} | ${n.description} |\n`;
    }
    md += '\n';
  }

  // Quiz
  if (state has questions) {
    // render each question with student answer, correct answer, ✓/✗
  }

  // Diagnosis result
  if (state.diagnosis_result) {
    // render score, breakdown
  }

  // Lesson
  if (state.lesson_content) {
    md += `## 个性化教案\n\n${state.lesson_content}\n`;
  }

  return md;
}
```

**Step 2: Commit**

## Task 2: sidebar.js 加 Export 菜单项

**Files:**
- Modify: `static/sidebar.js`

**Step 1: 在 Rename 和 Delete 之间加 Export 按钮**

点击后弹出选择弹窗（Markdown / JSON），选择后调用 exportSession()。

**Step 2: Commit**
