"""
章节解析器

支持中英文 + Markdown 章节识别，含空行兜底策略。
返回结构化的章节列表供 LLM 调用和前端展示使用。
"""

import re
from dataclasses import dataclass, field


# ── 中文数字转换 ─────────────────────────────────────────────

_CN_NUM_MAP: dict[str, int] = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "百": 100, "千": 1000,
}


def _cn_to_int(cn: str) -> int | None:
    """将中文数字字符串转为整数，如 '一百二十三' → 123。失败返回 None。"""
    if not cn:
        return None
    # 纯阿拉伯数字
    if cn.isdigit():
        return int(cn)

    total = 0
    section = 0  # 当前节（千/百/十之前的数字）
    for ch in cn:
        if ch not in _CN_NUM_MAP:
            return None
        val = _CN_NUM_MAP[ch]
        if val >= 10:
            # 遇到单位：十/百/千
            section = (section if section > 0 else 1) * val
            if val >= 100:
                total += section
                section = 0
        else:
            section += val
    total += section
    return total if total > 0 else None


# ── 章节匹配模式 ──────────────────────────────────────────────

# 中文：第X章 / 第X回（X 为中文数字或阿拉伯数字）
# 注意：用 [ \t] 代替 \s，避免 MULTILINE 模式下 \s 吞噬 \n 导致行号偏移
# 要求"章/回"后必须是空格、行尾、标点，排除"第三章内容"等误匹配
_CN_CHAPTER_RE = re.compile(
    r"^[ \t]*第[ \t]*([一二三四五六七八九十百千万零\d]+)[ \t]*(章|回)(?=[ \t]|$|[：:—\-，。,\.])",
    re.MULTILINE,
)

# 英文：Chapter X / CHAPTER X（要求后接空格、行尾或标点）
_EN_CHAPTER_RE = re.compile(
    r"^[ \t]*(Chapter|CHAPTER)[ \t]+(\d+|[IVXLCDM]+)(?=[ \t]|$|[：:—\-，。,\.])",
    re.MULTILINE,
)

# Markdown 标题： # 第X章 / ## Chapter X / ### 第X回 等
_MD_CN_CHAPTER_RE = re.compile(
    r"^[ \t]*#{1,3}[ \t]+第[ \t]*([一二三四五六七八九十百千万零\d]+)[ \t]*(章|回)(?=[ \t]|$|[：:—\-，。,\.])",
    re.MULTILINE,
)
_MD_EN_CHAPTER_RE = re.compile(
    r"^[ \t]*#{1,3}[ \t]+(Chapter|CHAPTER)[ \t]+(\d+|[IVXLCDM]+)(?=[ \t]|$|[：:—\-，。,\.])",
    re.MULTILINE,
)

# 所有模式联合（按优先级：先精确后宽松）
_ALL_PATTERNS = [
    (_MD_CN_CHAPTER_RE, "md_cn"),
    (_MD_EN_CHAPTER_RE, "md_en"),
    (_CN_CHAPTER_RE, "cn"),
    (_EN_CHAPTER_RE, "en"),
]


@dataclass
class Chapter:
    """章节数据结构"""

    number: int          # 章节序号（从 1 开始）
    title: str           # 章节标题（如 "第一章 笼中雀"）
    content: str         # 章节正文（不含标题行）
    start_line: int = 0  # 在原文中的起始行号（1-based）


# ── 主解析函数 ────────────────────────────────────────────────


def detect_chapters(text: str) -> list[Chapter]:
    """
    从小说文本中识别章节。

    识别优先级：
    1. 中文格式：第X章 / 第X回（X 支持中文数字和阿拉伯数字）
    2. 英文格式：Chapter X / CHAPTER X
    3. Markdown 标题：# 第X章 / ## Chapter X
    4. 兜底：连续两个以上空行视为章节边界（需至少产生 3 段）

    Args:
        text: 原始小说全文

    Returns:
        Chapter 列表，按出现顺序排列，number 从 1 开始递增
    """
    chapters = _detect_by_regex(text)
    if len(chapters) > 0:
        return chapters

    # 正则完全匹配不到章节，尝试空行兜底
    fallback = _detect_by_blank_lines(text)
    return fallback


def validate_chapter_count(chapters: list[Chapter]) -> tuple[bool, str]:
    """
    校验章节数量是否满足最低要求（≥3 章）。

    Returns:
        (是否通过, 错误信息)
    """
    if len(chapters) < 3:
        return False, (
            f"检测到 {len(chapters)} 个章节，需要至少 3 个章节。"
            f"请检查文件格式：支持第X章、Chapter X、Markdown 标题等格式。"
        )
    return True, ""


# ── 内部实现 ──────────────────────────────────────────────────


def _detect_by_regex(text: str) -> list[Chapter]:
    """使用正则匹配识别章节。"""
    lines = text.splitlines(keepends=True)
    matches: list[tuple[int, int, str]] = []  # (行号, 章节序号, 标题行文本)

    for pattern, kind in _ALL_PATTERNS:
        for m in pattern.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            title_line = lines[line_no - 1].rstrip()

            if kind in ("cn", "md_cn"):
                cn_num = _cn_to_int(m.group(1))
                if cn_num is None:
                    continue
                chap_num = cn_num
            elif kind in ("en", "md_en"):
                try:
                    chap_num = int(m.group(2))
                except ValueError:
                    # 罗马数字暂不处理
                    continue
            else:
                continue

            matches.append((line_no, chap_num, title_line))

    if not matches:
        return []

    # 按行号排序，统一编号
    matches.sort(key=lambda x: x[0])

    chapters: list[Chapter] = []
    for i, (line_no, _, title_line) in enumerate(matches):
        # 章节内容：从当前标题行之后到下一个标题行之前
        start = sum(len(l) for l in lines[:line_no])  # 标题行之后的字符位置
        if i + 1 < len(matches):
            next_line = matches[i + 1][0]
            end = sum(len(l) for l in lines[: next_line - 1])
        else:
            end = len(text)

        content = text[start:end].strip()
        chapters.append(Chapter(
            number=i + 1,
            title=title_line,
            content=content,
            start_line=line_no,
        ))

    return chapters


def _detect_by_blank_lines(text: str) -> list[Chapter]:
    """
    兜底策略：将连续两个以上空行视为章节边界。
    至少产生 3 个段落才会返回结果。
    """
    # 按连续两个以上换行符分割
    segments = re.split(r"\n{2,}", text.strip())
    segments = [s.strip() for s in segments if s.strip()]

    if len(segments) < 3:
        return []

    chapters: list[Chapter] = []
    for i, seg in enumerate(segments):
        # 取第一行作为标题
        first_line, _, body = seg.partition("\n")
        chapters.append(Chapter(
            number=i + 1,
            title=first_line.strip()[:50],  # 截断过长标题
            content=body.strip() if body.strip() else seg.strip(),
            start_line=0,
        ))

    return chapters


def get_chapter_summary(chapters: list[Chapter]) -> str:
    """生成章节摘要文本，供前端预览和日志使用。"""
    lines = [f"共检测到 {len(chapters)} 个章节："]
    for ch in chapters:
        preview = ch.content[:40].replace("\n", " ")
        lines.append(f"  第{ch.number}章 {ch.title} | 正文预览: {preview}...")
    return "\n".join(lines)
