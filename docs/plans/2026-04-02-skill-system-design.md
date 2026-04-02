# Skill 系统设计

## 概述

让老师/教育者通过上传 SKILL.md 文件定义教学方法，系统在对话、出题、教案等环节自动识别并应用对应的教学策略。

## Skill 文件格式

每个 skill 是 `skills/<name>/SKILL.md`，YAML frontmatter + Markdown 指令：

```yaml
---
name: feynman
display_name: 费曼学习法
description: 通过"教别人"的方式深入理解概念。适用于学生希望用简单语言理解复杂知识、避免术语、通过类比学习的场景。
affects:
  - chat
  - lesson
  - quiz
---

## 教学原则
1. 让学生用自己的话解释概念
2. 发现解释不清的地方，回到原始材料
3. 简化语言，避免术语
4. 用类比连接已有知识

## 对话指令
在对话中，不要直接给答案。引导学生自己解释：
- "你能用自己的话解释一下吗？"
- "如果要教一个小学生，你会怎么说？"
- 当学生解释正确时，追问更深层的"为什么"

## 出题指令
题目设计应侧重理解而非记忆：
- 多用"用自己的话解释"类型的题
- 避免纯记忆性选择题
- 加入类比选择题（"以下哪个类比最能解释这个概念？"）

## 教案指令
教案每个知识点的结构：
1. 先给出概念的简单类比
2. 逐步深入解释
3. 加"自我检测"环节：让学生用一句话总结
4. 列出常见的解释误区
```

---

## 运行机制

### 第一步：启动时扫描（一次性）

服务器启动时，扫描 `skills/` 目录，解析每个 SKILL.md 的 frontmatter，构建摘要列表：

```python
# loader.py 扫描结果
[
  {
    "name": "feynman",
    "display_name": "费曼学习法",
    "description": "通过教别人的方式深入理解概念。适用于学生希望用简单语言理解复杂知识的场景。",
    "affects": ["chat", "lesson", "quiz"]
  },
  {
    "name": "socratic",
    "display_name": "苏格拉底式提问",
    "description": "通过反问引导学生自主思考和发现答案。适用于培养批判性思维和逻辑推理能力。",
    "affects": ["chat", "lesson"]
  }
]
```

### 第二步：intent 节点匹配（每条消息都判断，参照 OpenClaw）

**每条用户消息**经过 intent 节点时，prompt 里都包含所有 skill 的摘要。LLM 在判断意图的同时判断是否匹配 skill。用户可以随时切换教学方法。

**示例 1：用户明确指定 skill**

```
用户消息: "用费曼学习法帮我学勾股定理"

intent 节点的 prompt:
  ┌──────────────────────────────────────────────┐
  │ 分析用户意图，分类为以下之一：                    │
  │ - learn_topic: 学习某个主题                     │
  │ - start_diagnosis: 开始诊断                    │
  │ - chat: 普通对话                               │
  │                                                │
  │ 可用教学方法（如果用户提到或内容适合则匹配）：      │
  │ - feynman: 通过教别人的方式深入理解概念。         │
  │   适用于学生希望用简单语言理解复杂知识的场景。      │
  │ - socratic: 通过反问引导学生自主思考和发现答案。   │
  │   适用于培养批判性思维和逻辑推理能力。             │
  │                                                │
  │ 用户消息: "用费曼学习法帮我学勾股定理"            │
  └──────────────────────────────────────────────┘

LLM 返回:
  {intent: "learn_topic", topic: "勾股定理", skill: "feynman"}
```

**示例 2：用户没提，但 LLM 根据 description 语义匹配**

```
用户消息: "帮我用最简单的方式理解勾股定理，像教小孩一样"

LLM 判断: "用简单方式理解"、"像教小孩" → 匹配 feynman 的描述
          "用简单语言理解复杂知识、避免术语、通过类比学习"
返回:
  {intent: "learn_topic", topic: "勾股定理", skill: "feynman"}
```

**示例 3：不匹配任何 skill**

```
用户消息: "我想学习勾股定理"

LLM 判断: 没有明确教学方法偏好
返回:
  {intent: "learn_topic", topic: "勾股定理", skill: null}
  → 系统行为和现在完全一样，不注入任何 skill
```

**示例 4：中途切换 skill**

```
消息 1: "用费曼学习法帮我学勾股定理"
  → intent: {intent: "learn_topic", topic: "勾股定理", skill: "feynman"}
  → 后续节点用费曼学习法

消息 5: "换成苏格拉底式提问吧"
  → intent: {intent: "chat", skill: "socratic"}
  → state 中 active_skill 更新为 "socratic"
  → 后续节点改用苏格拉底式提问

消息 8: "不用教学方法了，正常聊"
  → intent: {intent: "chat", skill: null}
  → state 中 active_skill 清空
  → 回到默认模式
```

### 第三步：加载并存储

intent 返回 `skill: "feynman"` 后：

```python
# intent_node 中
if result.skill:
    content = skill_registry.load_full_content(result.skill)
    return {
        "active_skill": result.skill,
        "active_skill_content": content,  # 完整 SKILL.md body
    }
elif result.skill is None and state.get("active_skill"):
    # 用户明确要求取消 skill
    return {
        "active_skill": None,
        "active_skill_content": None,
    }
# 否则保持 state 中现有的 skill 不变
```

`active_skill_content` 存到 session state，后续节点从 state 读取。每条消息都会重新判断，用户可随时切换或取消。

### 第四步：各节点注入（affects 控制）

以下用完整示例展示每个环节的 prompt 变化。假设 `active_skill = "feynman"`。

---

#### 示例 A：knowledge_graph 节点（affects 不含 graph → 不注入）

费曼学习法的 `affects` 是 `[chat, lesson, quiz]`，不包含 `graph`，所以知识图谱生成**不受影响**：

```
发送给 LLM 的 prompt（和现在一样，无变化）:

  你是教育知识图谱构建者。给定一个学习主题，生成相关概念的知识图谱。
  主题：勾股定理
  最大节点数：8
  ...
```

---

#### 示例 B：chat 节点（affects 含 chat → 注入）

```
发送给 LLM 的 prompt:

  ┌─── 原始 system prompt ───┐
  │ You are a friendly AI     │
  │ learning assistant...     │
  └──────────────────────────┘

  ┌─── 追加的 skill 内容 ───┐
  │                          │
  │ ## 教学方法要求（费曼学习法）│
  │                          │
  │ ## 教学原则               │
  │ 1. 让学生用自己的话解释概念│
  │ 2. 发现解释不清的地方...  │
  │                          │
  │ ## 对话指令               │
  │ 不直接给答案。引导学生：   │
  │ - "你能用自己的话解释吗？" │
  │ - "如果教小学生怎么说？"   │
  └──────────────────────────┘

效果：AI 不会直接讲解勾股定理，而是反问学生
  AI: "你已经知道直角三角形有什么特点吗？试试用自己的话说说看。"
```

**对比：没有 skill 时**

```
  AI: "勾股定理是指直角三角形中，两条直角边的平方和等于斜边的平方，即 a² + b² = c²。"
```

---

#### 示例 C：quiz 节点（affects 含 quiz → 注入）

```
发送给 LLM 的 prompt:

  ┌─── 原始出题 prompt ───────────────┐
  │ 你是自适应学习的出题生成器。            │
  │ 为以下知识点生成选择题：               │
  │ - 直角三角形: 基础概念                │
  │ - 勾股定理: 核心公式                  │
  │ 每个知识点 2 道题，共 4 个选项...      │
  └────────────────────────────────────┘

  ┌─── 追加的 skill 内容 ───────────────┐
  │                                      │
  │ ## 教学方法要求（费曼学习法）            │
  │                                      │
  │ ## 出题指令                           │
  │ 题目设计应侧重理解而非记忆：            │
  │ - 多用"用自己的话解释"类型的题          │
  │ - 避免纯记忆性选择题                   │
  │ - 加入类比选择题                       │
  └────────────────────────────────────┘

效果：LLM 生成的题目风格会变

  无 skill 时的题：
    "勾股定理的公式是什么？ A) a+b=c B) a²+b²=c² C) ..."  （记忆型）

  有 feynman skill 时的题：
    "以下哪个类比最能解释勾股定理？
     A) 两个小正方形拼成一个大正方形
     B) 三角形的面积公式
     C) ..."  （理解型）
```

---

#### 示例 D：lesson 节点（affects 含 lesson → 注入）

```
发送给 LLM 的 prompt:

  ┌─── 原始教案 prompt ──────────────┐
  │ 你是个性化教案生成器。               │
  │ 主题：勾股定理                     │
  │ 薄弱知识点：                       │
  │ - 平方与平方根：运算基础             │
  │ - 坐标距离：应用场景                │
  │ 生成包含解释、公式、例题、易错点...   │
  └──────────────────────────────────┘

  ┌─── 追加的 skill 内容 ──────────────┐
  │                                     │
  │ ## 教学方法要求（费曼学习法）           │
  │                                     │
  │ ## 教案指令                          │
  │ 每个知识点的结构：                    │
  │ 1. 先给出概念的简单类比               │
  │ 2. 逐步深入解释                      │
  │ 3. 加"自我检测"：让学生用一句话总结   │
  │ 4. 列出常见的解释误区                 │
  └──────────────────────────────────┘

效果：教案结构会变

  无 skill 时：
    ## 平方与平方根
    定义：...
    公式：...
    例题：...

  有 feynman skill 时：
    ## 平方与平方根
    ### 类比理解
    想象一个正方形，边长 3，面积就是 3² = 9...
    ### 深入解释
    ...
    ### 自我检测
    试试用一句话解释：什么是平方根？
    ### 常见解释误区
    很多人会说"开方就是除以2"，这是错的...
```

---

## Skill 匹配策略（参考 OpenClaw）

基于调研，我们采用与 OpenClaw 相同的策略：

| 场景 | 做法 | 示例 |
|------|------|------|
| Skill <10 个 | 所有 description 直接放 prompt，LLM 语义判断 | 当前方案 |
| Skill 10-50 个 | 关键词预过滤 + LLM | 未来扩展 |
| Skill 50+ 个 | 向量相似度检索 top-K + LLM | 未来扩展 |

**description 写得好是关键。** 好的描述能让 LLM 在用户没有明确提到 skill 名称时也能语义匹配。

## 改动范围

| 类型 | 文件 | 改动 |
|------|------|------|
| 新增 | `src/skills/__init__.py` | 包初始化 |
| 新增 | `src/skills/loader.py` | 扫描 skills/，解析 YAML frontmatter |
| 新增 | `skills/feynman/SKILL.md` | 示例：费曼学习法 |
| 新增 | `skills/socratic/SKILL.md` | 示例：苏格拉底式提问 |
| 修改 | `src/agent/state.py` | 加 `active_skill`, `active_skill_content` 字段 |
| 修改 | `src/prompts/intent.j2` | 加 skill 摘要列表 |
| 修改 | `src/agent/nodes/intent.py` | 返回匹配的 skill，加载完整内容存到 state |
| 修改 | `src/agent/nodes/chat.py` | 检查 state，注入 skill 到 system prompt |
| 修改 | `src/agent/nodes/quiz.py` | 检查 state，注入 skill 到出题 prompt |
| 修改 | `src/agent/nodes/lesson.py` | 检查 state，注入 skill 到教案 prompt |
| 修改 | `src/agent/nodes/knowledge_graph.py` | 检查 state，注入 skill 到图谱 prompt |
| 新增 | `GET /api/skills` | 返回可用 skill 列表（给前端展示） |

## 不做的事（MVP）

- 不做网页上传（文件方式即可）
- 不做多 skill 叠加（一次只用一个）
- 不做 skill 市场/注册中心
- 不做向量检索（<10 个直接放 prompt）
- 不做 skill 版本管理
