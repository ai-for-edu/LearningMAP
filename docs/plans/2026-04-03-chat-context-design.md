# 对话上下文注入设计

## 问题

chat_node 只传 messages 给 LLM，不知道当前 session 已经做过什么（图谱、诊断、教案）。用户问"我哪里比较弱"时 AI 无法回答。

## 方案

每次 chat_node 调 LLM 前，从 state 提取关键数据，组装成简短摘要（~200 tokens），注入到 system prompt。

## 改动

只改 `src/agent/nodes/chat.py`，新增 `_build_context_summary(state)` 函数。
