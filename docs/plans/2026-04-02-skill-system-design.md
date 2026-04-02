# Skill 系统设计

## 概述

让老师/教育者通过上传 SKILL.md 文件定义教学方法，系统在对话、出题、教案等环节自动识别并应用对应的教学策略。

## Skill 文件格式

每个 skill 是 `skills/<name>/SKILL.md`，YAML frontmatter + Markdown 指令：

```yaml
---
name: feynman
display_name: 费曼学习法
description: 通过"教别人"的方式深入理解概念。当学生希望用简单语言理解复杂知识时使用。
affects:
  - chat
  - lesson
  - quiz
---

## 教学原则
1. 让学生用自己的话解释概念
...

## 对话指令
不直接给答案，引导学生自己解释...

## 教案指令
每个知识点后加"用自己的话总结"环节...
```

## 运行机制

1. **启动时**：扫描 `skills/` 目录，解析所有 SKILL.md 的 frontmatter，构建摘要列表
2. **intent 节点**：prompt 中包含 skill 摘要，LLM 判断是否匹配某个 skill
3. **匹配后**：读取完整 SKILL.md 内容，存到 session state 的 `active_skill_content`
4. **各节点注入**：检查 `active_skill_content`，拼接到 prompt 末尾

注入方式：末尾拼接

```
原始 prompt + "\n\n## 教学方法要求\n" + skill 内容
```

## 改动范围

- 新增 `src/skills/loader.py` — 扫描、解析 SKILL.md
- 新增 `src/skills/__init__.py`
- 新增 `skills/feynman/SKILL.md` — 示例
- 新增 `skills/socratic/SKILL.md` — 示例
- 修改 `src/agent/state.py` — 加 `active_skill`, `active_skill_content` 字段
- 修改 `src/agent/nodes/intent.py` — intent prompt 加 skill 摘要
- 修改 `src/prompts/intent.j2` — 加 skill 列表
- 修改 `src/agent/nodes/chat.py` — 注入 skill
- 修改 `src/agent/nodes/lesson.py` — 注入 skill
- 修改 `src/agent/nodes/quiz.py` — 注入 skill
- 修改 `src/agent/nodes/knowledge_graph.py` — 注入 skill
- 新增 `GET /api/skills` — 返回可用 skill 列表
- MVP 不做网页上传，不做多 skill 叠加，不做 skill 市场
