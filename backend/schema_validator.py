"""
YAML Schema 校验器

校验 LLM 输出的 YAML 格式是否符合预定义 Schema。
检测常见格式问题并尝试自动修复；无法修复时返回详细错误信息供重试使用。
"""

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── 枚举约束 ───────────────────────────────────────────────────

VALID_ROLES = {"protagonist", "antagonist", "supporting", "minor"}
VALID_CONTENT_TYPES = {"action", "dialogue", "narration"}
VALID_TIMES_OF_DAY = {"清晨", "上午", "下午", "傍晚", "深夜", "未知"}

# ── 公共接口 ───────────────────────────────────────────────────


def validate_and_fix(raw_yaml: str) -> tuple[bool, str, dict | None]:
    """
    校验 LLM 输出的 YAML 是否符合 Schema，并尝试自动修复。

    Args:
        raw_yaml: LLM 返回的原始字符串

    Returns:
        (是否合法, 错误信息或"OK", 修复后的 YAML 字符串或 None)
        若合法：返回 (True, "OK", 修复后字符串)
        若非法：返回 (False, 详细错误描述, None)
    """
    # 1. 清理常见问题
    cleaned = _clean_yaml(raw_yaml)

    # 2. 尝试解析 YAML
    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        return False, f"YAML 语法错误：{e}", None

    if not isinstance(data, dict):
        return False, "YAML 顶层必须是一个字典（mapping）", None

    # 3. 逐层校验
    errors: list[str] = []

    script = data.get("script")
    if not isinstance(script, dict):
        return False, "缺少顶层 script 字段或其类型不是字典", None

    errors.extend(_validate_meta(script.get("meta")))
    errors.extend(_validate_characters(script.get("characters", [])))
    errors.extend(_validate_scenes(
        script.get("scenes", []),
        _build_char_id_set(script.get("characters", [])),
    ))

    if errors:
        error_msg = "；".join(errors)
        return False, error_msg, None

    # 4. 重新序列化（保证格式统一）
    fixed = yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
    )
    return True, "OK", fixed


def extract_meta(raw_yaml: str) -> dict[str, int]:
    """
    从 YAML 中提取元信息（章节数、场景数、角色数）。

    Args:
        raw_yaml: 合法的 YAML 字符串

    Returns:
        {"chapter_count": N, "scene_count": N, "character_count": N}
    """
    try:
        data = yaml.safe_load(raw_yaml)
        script = data.get("script", {})
        characters = script.get("characters", [])
        scenes = script.get("scenes", [])

        chapters = set()
        for s in scenes:
            if isinstance(s, dict) and "chapter" in s:
                chapters.add(s["chapter"])

        return {
            "chapter_count": len(chapters),
            "scene_count": len(scenes),
            "character_count": len(characters),
        }
    except Exception:
        return {
            "chapter_count": 0,
            "scene_count": 0,
            "character_count": 0,
        }


# ── 校验子函数 ─────────────────────────────────────────────────


def _validate_meta(meta: Any) -> list[str]:
    """校验 meta 区块。"""
    errors: list[str] = []
    if not isinstance(meta, dict):
        errors.append("script.meta 必须是字典")
        return errors

    if "title" not in meta:
        errors.append("script.meta 缺少 title 字段")
    if "source_chapters" not in meta:
        errors.append("script.meta 缺少 source_chapters 字段")
    elif not isinstance(meta["source_chapters"], int):
        errors.append("script.meta.source_chapters 必须是整数")

    return errors


def _validate_characters(characters: Any) -> list[str]:
    """校验 characters 列表。"""
    errors: list[str] = []
    if not isinstance(characters, list):
        errors.append("script.characters 必须是列表")
        return errors

    seen_ids: set[str] = set()
    for i, char in enumerate(characters):
        if not isinstance(char, dict):
            errors.append(f"characters[{i}] 必须是字典")
            continue

        char_id = char.get("id", "")
        if not char_id:
            errors.append(f"characters[{i}] 缺少 id 字段")
        elif not isinstance(char_id, str) or not char_id.startswith("CHAR"):
            errors.append(f"characters[{i}].id 格式有误，应为 CHAR001 等")
        elif char_id in seen_ids:
            errors.append(f"角色 ID 重复：{char_id}")
        else:
            seen_ids.add(char_id)

        if "name" not in char:
            errors.append(f"characters[{i}]（{char_id}）缺少 name 字段")

        role = char.get("role", "")
        if not role:
            errors.append(f"characters[{i}]（{char_id}）缺少 role 字段")
        elif role not in VALID_ROLES:
            errors.append(
                f"characters[{i}]（{char_id}）role 值非法：'{role}'，"
                f"允许值：{VALID_ROLES}"
            )

        aliases = char.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            errors.append(f"characters[{i}]（{char_id}）aliases 必须是列表")

        traits = char.get("traits")
        if traits is not None and not isinstance(traits, list):
            errors.append(f"characters[{i}]（{char_id}）traits 必须是列表")

    return errors


def _validate_scenes(scenes: Any, valid_char_ids: set[str]) -> list[str]:
    """校验 scenes 列表。"""
    errors: list[str] = []
    if not isinstance(scenes, list):
        errors.append("script.scenes 必须是列表")
        return errors

    seen_scene_ids: set[str] = set()
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f"scenes[{i}] 必须是字典")
            continue

        sid = scene.get("scene_id", f"scenes[{i}]")

        # scene_id
        if not isinstance(scene.get("scene_id"), str):
            errors.append(f"scenes[{i}] 缺少 scene_id")
        elif scene["scene_id"] in seen_scene_ids:
            errors.append(f"场景 ID 重复：{scene['scene_id']}")
        else:
            seen_scene_ids.add(scene["scene_id"])

        # chapter
        if "chapter" not in scene:
            errors.append(f"{sid} 缺少 chapter 字段")

        # time_of_day
        tod = scene.get("time_of_day", "")
        if tod and tod not in VALID_TIMES_OF_DAY:
            errors.append(
                f"{sid} time_of_day 值非法：'{tod}'，允许值：{VALID_TIMES_OF_DAY}"
            )

        # characters_present 引用一致性
        present = scene.get("characters_present", [])
        if isinstance(present, list):
            for cid in present:
                if isinstance(cid, str) and valid_char_ids and cid not in valid_char_ids:
                    errors.append(
                        f"{sid} 引用了不存在的角色 ID：{cid}"
                    )

        # content
        content = scene.get("content", [])
        if not isinstance(content, list):
            errors.append(f"{sid} content 必须是列表")
            continue

        if len(content) == 0:
            errors.append(f"{sid} content 不能为空")
            continue

        errors.extend(_validate_content(content, sid))

    return errors


def _validate_content(content: list, scene_label: str) -> list[str]:
    """校验 content 列表中的每个条目。"""
    errors: list[str] = []
    for j, item in enumerate(content):
        if not isinstance(item, dict):
            errors.append(f"{scene_label} content[{j}] 必须是字典")
            continue

        label = f"{scene_label} content[{j}]"
        ctype = item.get("type", "")

        if ctype not in VALID_CONTENT_TYPES:
            errors.append(
                f"{label} type 值非法：'{ctype}'，允许值：{VALID_CONTENT_TYPES}"
            )
            continue

        if ctype == "dialogue":
            if "character" not in item:
                errors.append(f"{label} 是 dialogue 类型但缺少 character 字段")
            if "text" not in item:
                errors.append(f"{label} 是 dialogue 类型但缺少 text 字段")
        else:
            # action 或 narration
            if "description" not in item:
                errors.append(f"{label} 是 {ctype} 类型但缺少 description 字段")

    return errors


# ── 辅助函数 ───────────────────────────────────────────────────


def _clean_yaml(raw: str) -> str:
    """
    清理 LLM 输出中的常见格式问题：
    - 去除 markdown 代码块包裹
    - 去除开头/结尾的空白
    """
    text = raw.strip()

    # 去除 ```yaml ... ``` 包裹
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return text


def _build_char_id_set(characters: list) -> set[str]:
    """从 characters 列表中提取所有角色 ID 集合。"""
    ids: set[str] = set()
    for char in characters:
        if isinstance(char, dict) and "id" in char:
            ids.add(char["id"])
    return ids
