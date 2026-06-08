"""
DeepSeek API 客户端

封装 API 调用、超时处理、分段转换和角色 ID 一致性维护。
使用 httpx.AsyncClient 进行异步 HTTP 请求。
"""

import logging
from typing import Any

import httpx

from prompts import (
    build_convert_prompt,
    build_character_extraction_prompt,
    SYSTEM_PROMPT,
)
from parser import Chapter, detect_chapters

logger = logging.getLogger(__name__)

# ── 常量 ───────────────────────────────────────────────────────

MAX_CHARS_PER_REQUEST = 50000   # 单次请求最大字符数（超出则分段）
MAX_CHAPTERS = 200              # 分段模式下最大章节数，防止资源耗尽
MAX_CHARS_FOR_EXTRACTION = 200000  # 角色提取 Prompt 最大字符数
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TEMPERATURE = 0           # 温度 0 保证同一输入始终得到相同输出
REQUEST_TIMEOUT = 120.0         # API 请求超时（秒）


# ── 客户端 ─────────────────────────────────────────────────────


class DeepSeekClient:
    """DeepSeek API 异步客户端（OpenAI 兼容接口）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = DEFAULT_MODEL,
    ):
        """
        Args:
            api_key: DeepSeek API Key
            base_url: API 基础地址
            model: 模型名称
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 客户端（延迟初始化）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(REQUEST_TIMEOUT),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── 公开接口 ────────────────────────────────────────────

    async def convert(self, text: str, title: str = "") -> str:
        """
        将小说全文转换为 YAML 剧本。

        若文本较短（≤50000 字），一次调用完成；
        若文本较长，先提取全局角色表，再逐章转换后合并。

        Args:
            text: 完整小说文本
            title: 小说标题（可选）

        Returns:
            完整的 YAML 剧本字符串

        Raises:
            ValueError: 文本中章节不足或为空
            RuntimeError: API 调用失败
        """
        chapters = detect_chapters(text)

        if len(chapters) < 3:
            raise ValueError(
                f"检测到 {len(chapters)} 个章节，需要至少 3 个章节。"
                f"请确认文件格式。"
            )

        total_chars = sum(len(ch.content) for ch in chapters)

        if total_chars <= MAX_CHARS_PER_REQUEST:
            # 短文本：一次性转换
            return await self._convert_single(text, len(chapters), title)
        else:
            # 长文本：分段转换
            logger.info(
                "文本总长 %d 字符，启用分段转换模式（%d 章）",
                total_chars,
                len(chapters),
            )
            return await self._convert_chunked(chapters, title)

    # ── 内部方法 ────────────────────────────────────────────

    async def _convert_single(
        self, text: str, chapter_count: int, title: str
    ) -> str:
        """单次 LLM 调用完成转换。"""
        prompt = build_convert_prompt(text, chapter_count, title)
        return await self._call_llm(SYSTEM_PROMPT, prompt)

    async def _convert_chunked(
        self, chapters: list[Chapter], title: str
    ) -> str:
        """
        分段转换模式：
        1. 先用全文提取全局角色表
        2. 再逐章转换场景内容
        3. 合并为完整 YAML
        """
        # 安全上限：防止超大文本导致资源耗尽
        if len(chapters) > MAX_CHAPTERS:
            logger.warning(
                "章节数 %d 超过上限 %d，仅处理前 %d 章",
                len(chapters), MAX_CHAPTERS, MAX_CHAPTERS,
            )
            chapters = chapters[:MAX_CHAPTERS]

        full_text = "\n\n".join(ch.content for ch in chapters)
        if len(full_text) > MAX_CHARS_FOR_EXTRACTION:
            logger.warning(
                "角色提取文本 %d 字符超过上限 %d，截断处理",
                len(full_text), MAX_CHARS_FOR_EXTRACTION,
            )
            full_text = full_text[:MAX_CHARS_FOR_EXTRACTION]

        # 第一步：提取全局角色表
        char_prompt = build_character_extraction_prompt(full_text)
        char_yaml = await self._call_llm(SYSTEM_PROMPT, char_prompt)
        characters_block = _extract_yaml_section(char_yaml, "characters")

        # 第二步：逐章转换场景
        all_scenes: list[str] = []
        for ch in chapters:
            scene_prompt = _build_chapter_scene_prompt(
                ch, characters_block, title
            )
            scene_yaml = await self._call_llm(SYSTEM_PROMPT, scene_prompt)
            scenes_block = _extract_yaml_section(scene_yaml, "scenes")
            if scenes_block:
                all_scenes.append(scenes_block)

        # 第三步：组装完整 YAML
        return _assemble_yaml(chapters, characters_block, all_scenes)

    async def _call_llm(
        self, system_prompt: str, user_prompt: str
    ) -> str:
        """
        底层 API 调用。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词

        Returns:
            LLM 返回的原始文本

        Raises:
            RuntimeError: 网络错误、Key 无效、超时等
        """
        client = await self._get_client()
        url = f"{self.base_url}/chat/completions"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": 8192,
        }

        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException:
            raise RuntimeError(
                "API 请求超时（120 秒）。请稍后重试或尝试更小的文件。"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise RuntimeError(
                    "API Key 无效。请检查 backend/.env 中的 DEEPSEEK_API_KEY。"
                )
            if e.response.status_code == 429:
                raise RuntimeError(
                    "API 请求频率过高，请稍后重试。"
                )
            raise RuntimeError(
                f"API 请求失败（HTTP {e.response.status_code}），请检查 API Key 和网络连接。"
            )
        except httpx.RequestError as e:
            raise RuntimeError(
                f"无法连接 API 服务：{e}"
            )

        # 安全解析 JSON 响应
        try:
            data = response.json()
        except Exception:
            logger.error(
                "API 返回非 JSON 响应（HTTP %d），Content-Type: %s",
                response.status_code,
                response.headers.get("content-type", "unknown"),
            )
            raise RuntimeError(
                "API 返回了非预期的格式（非 JSON），请检查 DEEPSEEK_BASE_URL 配置。"
            )

        # 检查 API 是否返回了错误
        if "error" in data:
            err = data["error"]
            if isinstance(err, dict):
                raise RuntimeError(
                    f"API 返回错误：{err.get('message', str(err))}"
                    f"（类型：{err.get('type', 'unknown')}）"
                )
            raise RuntimeError(f"API 返回错误：{err}")

        # 提取 content
        try:
            content: str = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            logger.error(
                "API 响应结构与预期不符。keys=%s",
                list(data.keys()) if isinstance(data, dict) else type(data),
            )
            raise RuntimeError(
                f"API 响应结构异常（{e}）。"
                f"请检查 DEEPSEEK_MODEL 配置是否正确。"
                f"响应 keys：{list(data.keys()) if isinstance(data, dict) else 'N/A'}"
            )

        return content


# ── 辅助函数 ───────────────────────────────────────────────────


def _extract_yaml_section(yaml_str: str, section: str) -> str:
    """
    从 LLM 返回的 YAML 中提取指定段落（characters 或 scenes）。
    处理常见的 markdown 代码块包裹问题。
    """
    # 去除可能的 markdown 代码块包裹
    cleaned = yaml_str.strip()
    if cleaned.startswith("```"):
        # 找到第一个换行后的内容
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
        # 去除尾部 ```
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    # 查找目标段落
    marker = f"{section}:"
    idx = cleaned.find(marker)
    if idx == -1:
        return cleaned  # 返回原文，让 validator 处理

    return cleaned[idx:]


_CHAPTER_SCENE_TEMPLATE = """请将以下单一章节的小说文本转换为 YAML 场景列表。

小说标题："__TITLE__"
章节：第__CHAPTER_NUM__章 __CHAPTER_TITLE__

【已有角色表（请严格使用以下角色 ID，不要创建新 ID）】
__CHARACTERS_BLOCK__

【输出格式】
scenes:
  - scene_id: SCENE___CHAPTER_NUM_PAD___001
    chapter: __CHAPTER_NUM__
    chapter_title: "__CHAPTER_TITLE__"
    location: "地点"
    time_of_day: "时间"
    characters_present:
      - CHAR001
    summary: "场景概要"
    content:
      - type: action
        description: "描写"
      - type: dialogue
        character: CHAR001
        text: "对话"

规则：
- scene_id 使用 SCENE___CHAPTER_NUM_PAD___XXX 格式
- 只输出 scenes 列表，不要包含 script/meta/characters
- 直接输出纯 YAML，不要用代码块包裹
- type 只能是 action / dialogue / narration

章节文本：
---
__CHAPTER_CONTENT__
---

请输出场景列表："""


def _sanitize_user_text(text: str) -> str:
    """清理用户文本中可能与 prompt 分隔符冲突的内容。"""
    # 将独立的 --- 分隔符替换为 ...，防止 prompt 注入
    import re
    return re.sub(r'^---$', '...', text, flags=re.MULTILINE)


def _build_chapter_scene_prompt(
    chapter: Chapter, characters_block: str, title: str
) -> str:
    """为单章转换构建 Prompt，含已知角色表以确保 ID 一致。"""
    display_title = title if title else "从文本提取"
    return (
        _CHAPTER_SCENE_TEMPLATE
        .replace("__CHAPTER_CONTENT__", _sanitize_user_text(chapter.content))  # 用户文本先替换
        .replace("__CHARACTERS_BLOCK__", characters_block)
        .replace("__CHAPTER_TITLE__", chapter.title)
        .replace("__CHAPTER_NUM_PAD__", f"{chapter.number:03d}")
        .replace("__CHAPTER_NUM__", str(chapter.number))
        .replace("__TITLE__", display_title)  # 用户控制的 title 最后
    )


def _assemble_yaml(
    chapters: list[Chapter],
    characters_block: str,
    all_scenes: list[str],
) -> str:
    """组装分段转换结果为完整 YAML。"""
    from datetime import date

    scene_count = sum(
        s.count("scene_id:") for s in all_scenes
    )
    char_count = characters_block.count("id:")

    lines = ["script:"]
    lines.append("  meta:")
    lines.append(f"    title: \"\"")
    lines.append(f"    author: \"\"")
    lines.append(f"    source_chapters: {len(chapters)}")
    lines.append(f"    total_scenes: {scene_count}")
    lines.append(f"    character_count: {char_count}")
    lines.append(f"    converted_at: \"{date.today().isoformat()}\"")

    # 角色块（保持缩进）
    for line in characters_block.splitlines():
        lines.append(f"  {line}" if not line.startswith(" ") else f"  {line}")

    # 场景块
    lines.append("  scenes:")
    for block in all_scenes:
        # 去掉顶层 scenes: 标记，内容缩进一层
        block_stripped = block.strip()
        if block_stripped.startswith("scenes:"):
            block_stripped = block_stripped[len("scenes:"):].strip()
        for line in block_stripped.splitlines():
            if line.strip():
                lines.append(f"    {line.strip()}")

    return "\n".join(lines)
