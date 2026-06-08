# AI 小说转剧本工具

将 ≥3 章小说文本自动转换为结构化 YAML 剧本，降低改编门槛，获得可编辑的初稿。

## Demo 视频

> [📺 B站演示视频](https://www.bilibili.com/video/BV13U7k6HE9Y/)

## 快速开始

### 环境要求

- Python 3.10+
- DeepSeek API Key（[获取地址](https://platform.deepseek.com)）

### 安装与运行

```bash
# 1. 克隆仓库
git clone <repo-url> && cd Ai小说转剧本工具

# 2. 安装依赖
cd backend && pip install -r requirements.txt

# 3. 配置 API Key
cp .env.example .env
# 编辑 .env，将 DEEPSEEK_API_KEY 替换为你的真实 Key

# 4. 启动后端
python3 main.py
# 服务运行在 http://localhost:8000

# 5. 浏览器访问
open http://localhost:8000
```

## 使用流程

1. 打开 `http://localhost:8000`
2. 上传 `.txt` 或 `.md` 格式的小说文件（需 ≥3 个完整章节）
3. 点击"开始转换"，AI 自动分析文本
4. 在 textarea 中查看/编辑生成的 YAML 剧本
5. 复制到剪贴板或下载为 `.yaml` 文件

## 支持的章节格式

| 格式 | 示例 |
|------|------|
| 中文章节 | `第一章 笼中雀`、`第3章 下山`、`第一回 开场` |
| 中文数字 | `第十二章`、`第一百零三回` |
| 英文章节 | `Chapter 1`、`CHAPTER 2` |
| Markdown | `# 第一章`、`## Chapter 3` |
| 空行兜底 | 连续空行分割（≥3 段时作为章节边界） |

## API 文档

### `GET /api/health`

健康检查。

**响应**：
```json
{"status": "ok", "version": "1.0.0"}
```

### `POST /api/convert`

上传小说文件，返回 YAML 剧本。

**请求**：`multipart/form-data`，字段 `file`（.txt / .md）

**成功响应** (200)：
```json
{
  "success": true,
  "yaml_content": "script:\n  meta:\n    ...",
  "meta": {
    "chapter_count": 3,
    "scene_count": 8,
    "character_count": 5
  }
}
```

**错误响应**：
| HTTP | 含义 |
|------|------|
| 400 | 文件类型不符 / 章节不足 3 个 / 文件为空 |
| 422 | YAML 格式校验两次均失败 |
| 500 | 服务器未配置 API Key |
| 502 | 上游 DeepSeek API 错误 |

## 目录结构

```
├── backend/
│   ├── main.py              # FastAPI 入口：路由、CORS、文件上传 API
│   ├── parser.py            # 章节识别器：中英文 + Markdown + 空行兜底
│   ├── llm_client.py        # DeepSeek API 客户端：调用、重试、分段合并
│   ├── schema_validator.py  # YAML 校验器：格式检查 + 自动重试
│   ├── prompts.py           # Prompt 模板：系统提示词 + 场景/角色拆分指令
│   ├── models.py            # Pydantic 数据模型
│   ├── requirements.txt     # Python 依赖
│   └── .env.example         # 环境变量模板（真实 .env 被 gitignore）
├── frontend/
│   └── index.html           # 单文件 SPA（内嵌 CSS + JS）
├── schema_design.md         # YAML Schema 设计文档
└── README.md
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python + FastAPI |
| AI 模型 | DeepSeek V4 Pro（OpenAI 兼容 API） |
| 前端 | 纯 HTML/CSS/JS（零依赖） |
| 数据格式 | YAML（剧本输出） |
| 设计风格 | Catppuccin Mocha 暗色主题 |

### 第三方依赖

| 包名 | 版本要求 | 用途 |
|------|----------|------|
| fastapi | ≥0.104.0 | Web 框架，路由处理与请求校验 |
| uvicorn | ≥0.24.0 | ASGI 服务器，运行 FastAPI 应用 |
| httpx | ≥0.25.0 | 异步 HTTP 客户端，调用 DeepSeek API |
| pydantic | ≥2.0（fastapi 内置） | 数据模型定义与请求/响应校验 |
| pyyaml | ≥6.0.1 | YAML 格式解析与序列化 |
| python-multipart | ≥0.0.6 | 文件上传解析（multipart/form-data） |
| python-dotenv | ≥1.0.0 | 从 .env 文件加载环境变量（API Key） |

以上均为 Python 生态通用第三方库，项目原创功能部分包括：章节识别算法、LLM Prompt 模板设计、YAML Schema 校验逻辑、分段转换与角色 ID 一致性维护机制。

## YAML Schema 说明

详见 [schema_design.md](./schema_design.md)，包含完整 Schema 定义、设计原则和扩展性说明。

## 核心特性

- **多章节识别**：支持中文/英文/Markdown/空行四种章节格式
- **智能场景拆分**：按地点+时间变化自动分场
- **角色全局索引**：ID 唯一引用，长文本分段处理时保持一致
- **容错机制**：YAML 格式错误自动重试一次
- **安全设计**：API Key 通过 .env 注入，不暴露到前端
- **同源部署**：前后端统一端口，无跨域问题
