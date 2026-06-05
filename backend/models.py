"""
Pydantic 数据模型定义

定义 FastAPI 请求/响应的数据结构，自动生成 OpenAPI 文档。
"""

from pydantic import BaseModel, Field


class ConvertResponse(BaseModel):
    """POST /api/convert 的响应模型"""

    success: bool = Field(..., description="转换是否成功")
    yaml_content: str = Field(default="", description="生成的 YAML 剧本内容")
    error: str | None = Field(default=None, description="错误信息（仅失败时返回）")
    meta: dict | None = Field(
        default=None,
        description="转换元信息，如章节数、场景数、角色数",
    )


class HealthResponse(BaseModel):
    """GET /api/health 的响应模型"""

    status: str = Field(default="ok", description="服务健康状态")
    version: str = Field(default="1.0.0", description="API 版本号")
