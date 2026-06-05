"""
Prompt 模板

集中管理所有 LLM 提示词，便于调试和迭代。
注意：所有包含用户文本的模板使用 str.replace() 拼接，
避免 f-string 因原文中的 { } 字符而崩溃。
"""

# ── 系统提示词 ─────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位资深的剧本编辑专家，擅长将小说叙事文本改编为专业的影视剧本格式。

你的任务：
1. **角色提取**：从文本中识别所有出场角色，为每个角色分配唯一 ID（CHAR001, CHAR002...），提取姓名、别名、身份、性格特征
2. **场景拆分**：根据地点变化或时间变化拆分场景，每个场景分配唯一 ID（SCENE_001, SCENE_002...）
3. **内容分类**：将每个场景的内容精确分类为三种类型：
   - `action`：动作描写、场景环境、人物走位
   - `dialogue`：角色对话（需标注说话人 ID，可选标注情绪）
   - `narration`：旁白、内心独白、叙述性文字

输出要求：
- 只输出 YAML，不要包含任何解释、前言、后缀
- 不要用 ```yaml 代码块包裹，直接输出纯 YAML 文本
- 严格遵循下方 Schema 结构，字段名不可修改
- 角色 ID 在整个输出中必须全局唯一且一致
- 中文内容保持原文风格，不要翻译或改写
- emotion 字段为可选，不确定时省略"""


# ── Schema 示例（嵌入 Prompt 供 LLM 参照）──────────────────────

SCHEMA_EXAMPLE = """
script:
  meta:
    title: "原小说名"
    author: ""
    source_chapters: 3
    total_scenes: 0
    character_count: 0
    converted_at: "YYYY-MM-DD"

  characters:
    - id: CHAR001
      name: "角色姓名"
      aliases: ["别名1"]
      role: protagonist
      traits:
        - "性格标签"
        - "外貌特征"

  scenes:
    - scene_id: SCENE_001
      chapter: 1
      chapter_title: "第一章 标题"
      location: "地点描述"
      time_of_day: "清晨"
      characters_present:
        - CHAR001
        - CHAR002
      summary: "本场景一句话概要"
      content:
        - type: action
          description: "动作或场景描写"
        - type: dialogue
          character: CHAR001
          text: "对话内容"
          emotion: 愤怒
        - type: narration
          description: "旁白或叙述文字"
"""

# ── Prompt 模板（用 __PLACEHOLDER__ 避免 f-string 注入问题）─────

_CONVERT_TEMPLATE = """请将以下小说文本转换为结构化 YAML 剧本。

小说标题：__TITLE_HINT__
章节数量：__CHAPTER_COUNT__

输出 Schema 参照如下（必须严格遵守）：
__SCHEMA_EXAMPLE__

重要规则：
- 角色 role 字段必须从以下枚举中选择：protagonist（主角）、antagonist（反派）、supporting（配角）、minor（龙套）
- 场景 time_of_day 字段从以下选择：清晨、上午、下午、傍晚、深夜、未知
- 场景拆分标准：每当地点发生显著变化，或时间发生跳跃，即创建新场景
- 角色一旦分配 ID（如 CHAR001），后续场景若同一角色出场，必须使用相同 ID
- content 列表中的 type 必须为 action / dialogue / narration 之一
- 如果无法确定某段文字的说话人，不要强行分配 dialogue，归类为 narration

以下是要转换的小说文本：

---
__NOVEL_TEXT__
---

现在请输出 YAML："""

_RETRY_TEMPLATE = """上次你输出的 YAML 格式有误，校验失败。请修正后重新输出。

【格式错误详情】
__ERROR_DETAIL__

【修正要求】
1. 必须是合法的 YAML 格式，缩进使用 2 空格
2. 所有必填字段不可缺失
3. role 只能是 protagonist / antagonist / supporting / minor 之一
4. type 只能是 action / dialogue / narration 之一
5. 角色 ID 和场景 ID 保持全局唯一且前后一致
6. 直接输出纯 YAML，不要包裹在 ```yaml 代码块中

以下是原始任务：

__ORIGINAL_PROMPT__

请重新输出修正后的 YAML："""

_CHARACTER_EXTRACT_TEMPLATE = """请从以下小说文本中提取所有出场角色，只输出角色列表（YAML 格式）。

输出格式（严格遵循）：
characters:
  - id: CHAR001
    name: "姓名"
    aliases: ["别名"]
    role: protagonist
    traits: ["特征1", "特征2"]
  - id: CHAR002
    name: "姓名"
    aliases: []
    role: supporting
    traits: ["特征"]

规则：
- role 必须是 protagonist / antagonist / supporting / minor 之一
- aliases 列出该角色的所有别名、昵称、称呼
- traits 列出性格和外观特征（至少 1 个）
- 只输出 characters 列表，不要包含其他内容
- 不要用代码块包裹

小说文本：
---
__NOVEL_TEXT__
---

请输出角色列表："""


# ── 公共构建函数 ───────────────────────────────────────────────


def _sanitize_user_text(text: str) -> str:
    """清理用户文本中可能与 prompt 分隔符冲突的内容。"""
    import re
    # 将独立的 --- 替换为 ...，防止 prompt 注入跳过数据边界
    return re.sub(r'^---$', '...', text, flags=re.MULTILINE)


def build_convert_prompt(novel_text: str, chapter_count: int, title: str = "") -> str:
    """
    构建小说转剧本的主 Prompt。

    Args:
        novel_text: 完整小说文本（或分段文本）
        chapter_count: 章节数量
        title: 小说标题（可选）

    Returns:
        完整的用户提示词
    """
    title_hint = f'"{title}"' if title else "从文本中提取"

    return (
        _CONVERT_TEMPLATE
        .replace("__NOVEL_TEXT__", _sanitize_user_text(novel_text))  # 用户文本先替换+清理
        .replace("__SCHEMA_EXAMPLE__", SCHEMA_EXAMPLE)
        .replace("__CHAPTER_COUNT__", str(chapter_count))
        .replace("__TITLE_HINT__", title_hint)                       # title 最后
    )


def build_retry_prompt(original_prompt: str, error_detail: str) -> str:
    """
    构建重试 Prompt，在格式错误时使用。

    Args:
        original_prompt: 原始用户提示词
        error_detail: 上次输出校验失败的具体原因

    Returns:
        强调格式修正的提示词
    """
    return (
        _RETRY_TEMPLATE
        .replace("__ERROR_DETAIL__", error_detail)
        .replace("__ORIGINAL_PROMPT__", original_prompt)
    )


def build_character_extraction_prompt(novel_text: str) -> str:
    """
    从全文中先提取角色表（分段处理时第一轮使用）。

    Args:
        novel_text: 完整小说文本

    Returns:
        角色提取专用提示词
    """
    return _CHARACTER_EXTRACT_TEMPLATE.replace("__NOVEL_TEXT__", novel_text)
