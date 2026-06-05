"""
AI 小说转剧本工具 — FastAPI 后端入口

提供文件上传转换接口，调用 DeepSeek API 将小说文本转为 YAML 剧本。
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse

from models import ConvertResponse, HealthResponse
from parser import detect_chapters, validate_chapter_count
from llm_client import DeepSeekClient
from schema_validator import validate_and_fix, extract_meta
from prompts import build_convert_prompt, build_retry_prompt, SYSTEM_PROMPT

# ── 配置加载 ───────────────────────────────────────────────────

# 尝试从 backend/.env 加载，也尝试项目根目录 .env
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)
load_dotenv()  # 兜底：从当前工作目录加载

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if not API_KEY:
    logger.warning(
        "⚠️  未设置 DEEPSEEK_API_KEY！"
        "请复制 backend/.env.example 为 backend/.env 并填入 API Key。"
    )

# ── FastAPI 应用 ───────────────────────────────────────────────

app = FastAPI(
    title="AI 小说转剧本工具",
    description="将小说文本（≥3 章）自动转换为结构化 YAML 剧本",
    version="1.0.0",
)

# CORS：允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 延迟初始化 LLM 客户端 ──────────────────────────────────────

_llm_client: DeepSeekClient | None = None


def get_llm_client() -> DeepSeekClient:
    """获取 LLM 客户端实例（延迟初始化，确保 env 已加载）。"""
    global _llm_client
    if _llm_client is None:
        if not API_KEY:
            raise HTTPException(
                status_code=500,
                detail="服务器未配置 API Key。请在 backend/.env 中设置 DEEPSEEK_API_KEY。",
            )
        _llm_client = DeepSeekClient(
            api_key=API_KEY,
            base_url=BASE_URL,
            model=MODEL,
        )
    return _llm_client


# ── API 路由 ───────────────────────────────────────────────────


# ── 前端页面（根路径直接返回 index.html，避免 file:// 跨域问题）──

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """返回前端单页应用。"""
    index_path = _FRONTEND_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>前端文件未找到</h1>", status_code=404)
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """健康检查接口。"""
    return HealthResponse(status="ok", version="1.0.0")


@app.post("/api/convert", response_model=ConvertResponse)
async def convert_novel(file: UploadFile = File(...)):
    """
    上传小说文件，转换为 YAML 剧本。

    支持的格式：.txt / .md
    要求：至少包含 3 个可识别的章节

    流程：
    1. 读取文件内容
    2. 检测章节数量（≥3）
    3. 调用 DeepSeek API 转换
    4. 校验输出 YAML，失败则自动重试一次
    5. 返回 YAML 内容及元信息
    """
    # 1. 文件类型校验
    filename = file.filename or "unknown"
    if not filename.lower().endswith((".txt", ".md")):
        raise HTTPException(
            status_code=400,
            detail="仅支持 .txt 和 .md 文件格式。",
        )

    # 2. 读取文件内容
    try:
        content = await file.read()
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="无法解码文件内容，请使用 UTF-8 或 GBK 编码。",
            )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"读取文件失败：{e}",
        )

    if not text.strip():
        raise HTTPException(status_code=400, detail="文件内容为空。")

    # 3. 检测章节
    chapters = detect_chapters(text)
    is_valid, error_msg = validate_chapter_count(chapters)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{error_msg}\n"
                f"当前检测到 {len(chapters)} 个章节。"
                f"请确保文件包含至少 3 个完整章节，"
                f"并使用第X章 / Chapter X / Markdown 标题等格式标记。"
            ),
        )

    logger.info(
        "收到转换请求：%s，共 %d 章节，%d 字符",
        filename,
        len(chapters),
        len(text),
    )

    # 4. 调用 LLM 转换（含重试逻辑）
    title = Path(filename).stem
    client = get_llm_client()

    try:
        raw_yaml = await client.convert(text, title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("LLM 调用异常")
        raise HTTPException(status_code=500, detail=f"转换失败：{e}")

    # 5. 校验 YAML 输出
    is_valid, error_detail, fixed_yaml = validate_and_fix(raw_yaml)

    if not is_valid:
        logger.warning("首次输出校验失败：%s，正在重试...", error_detail)

        # 重试一次
        original_prompt = build_convert_prompt(text, len(chapters), title)
        retry_prompt = build_retry_prompt(original_prompt, error_detail)

        try:
            raw_yaml = await client._call_llm(SYSTEM_PROMPT, retry_prompt)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"重试时 API 调用失败：{e}",
            )

        is_valid, error_detail, fixed_yaml = validate_and_fix(raw_yaml)

        if not is_valid:
            raise HTTPException(
                status_code=422,
                detail=f"YAML 格式校验两次均失败：{error_detail}。"
                       f"请尝试调整小说文本格式后重新转换。",
            )

    # 6. 提取元信息并返回
    meta = extract_meta(fixed_yaml or raw_yaml)
    logger.info(
        "转换成功：%d 章 → %d 场景，%d 角色",
        meta.get("chapter_count", 0),
        meta.get("scene_count", 0),
        meta.get("character_count", 0),
    )

    return ConvertResponse(
        success=True,
        yaml_content=fixed_yaml or raw_yaml,
        meta=meta,
    )


# ── 启动入口 ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
