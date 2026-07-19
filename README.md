# 智能旅行助手后端

这是一个面向简历展示的全栈 AI 行程规划项目后端。项目基于 FastAPI、LangGraph、DeepSeek 和高德开放平台构建，支持自然语言旅行需求理解、候选景点推荐、天气查询、景区地址查询、路线规划、住宿地段推荐、历史行程保存和用户偏好画像。

当前版本采用 MVP 思路：后端提供完整 API、Agent 编排、工具调用和本地调试页面；前端可以通过后端 `/debug/` 调试，也可以独立部署到 Codex Sites 后连接 Railway 后端。

## 核心能力

- 自然语言旅行规划：用户可以输入“帮我规划洛阳 5 天行程，轻松一点，每天 9 点到 18 点”。
- 两阶段行程规划：第一轮返回候选景点，第二轮根据用户选择或“你帮我选”生成完整行程。
- 上下文状态保存：保存目的地、旅行天数、节奏、候选景点和用户选择，避免第二轮对话丢失上下文。
- 多 Agent 编排：使用 LangGraph StateGraph 组织总控、天气、景区地址和总规划流程。
- MCP 工具调用：天气、POI、路线等外部数据查询封装为 MCP 工具，Planning Agent 可以通过工具调用补充数据。
- 高德天气查询：通过高德 Web 服务 API 查询城市天气实况和预报。
- 高德 POI 与路线数据：查询景点地址、经纬度、行政区和景点间驾车路线信息。
- DeepSeek 大模型生成：使用 DeepSeek OpenAI-compatible API 完成意图判断、工具决策和最终行程生成。
- SSE 流式输出：最终 Planning Agent 支持流式输出，前端可逐步展示生成过程。
- 结构化结果：最终 Planning Agent 输出结构化 JSON，前端负责渲染为用户可读的旅行计划。
- 用户系统：支持注册、登录、退出、当前用户查询。
- 用户画像：保存旅行节奏、常用出发/结束时间、偏好城市、偏好景点类型和备注。
- 历史行程：保存用户生成过的旅行计划，支持历史列表查看。
- 基础安全配置：HttpOnly Cookie、7 天过期时间、CORS 配置、基础安全响应头、XSS 风险控制和常见 SQL 注入输入拦截。
- 部署适配：提供 Railway 启动配置，支持部署时读取平台环境变量。

## 技术栈

- Web 框架：FastAPI
- Agent 编排：LangGraph
- LLM 接入：DeepSeek OpenAI-compatible API
- 工具协议：MCP、LangChain MCP Adapters
- 外部 API：高德 Web 服务 API、高德 Web 端 JS API
- 数据库：SQLite 本地开发，Railway 部署建议使用 PostgreSQL
- ORM：SQLAlchemy
- 流式传输：Server-Sent Events
- 本地调试页面：`static/index.html`
- 部署：Railway 后端，Codex Sites 前端

## 项目结构

```text
travel-assistant-backend/
├── app/
│   ├── agents/                 # LangGraph 与各 Agent 实现
│   ├── core/                   # 配置、会话、安全工具
│   ├── db/                     # SQLAlchemy 数据库模型和连接
│   ├── mcp/                    # MCP Server 工具入口
│   ├── repositories/           # 用户、画像、会话、历史行程持久化
│   ├── schemas/                # API 请求/响应模型
│   ├── tools/                  # 高德天气、POI、路线工具
│   └── main.py                 # FastAPI 入口
├── docs/
│   └── mcp-tools.md            # MCP 工具说明
├── static/
│   └── index.html              # 本地调试前端
├── .env.example                # 环境变量模板，不包含真实 Key
├── railway.json                # Railway 部署启动配置
├── requirements.txt            # Python 依赖
└── README.md
```

## 环境变量

复制模板文件：

```powershell
Copy-Item .env.example .env
```

`.env` 示例：

```dotenv
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash

AMAP_API_KEY=your-amap-web-service-key
AMAP_PRIVATE_KEY=your-amap-private-key-if-signature-enabled
AMAP_JS_API_KEY=your-amap-web-js-api-key
AMAP_JS_SECURITY_CODE=your-amap-web-js-security-code

APP_ENV=development
LOG_LEVEL=INFO
DATABASE_URL=sqlite:///./travel_assistant.db

COOKIE_SECURE=false
COOKIE_SAMESITE=lax
CORS_ORIGINS=*
```

说明：

- `DEEPSEEK_API_KEY`：DeepSeek API Key。
- `DEEPSEEK_MODEL`：DeepSeek 模型名称。
- `AMAP_API_KEY`：高德 Web 服务 API Key，用于天气、行政区、POI 和路线查询。
- `AMAP_JS_API_KEY`：高德 Web 端 JS API Key，用于前端地图展示。
- `AMAP_JS_SECURITY_CODE`：高德 Web 端 JS API 安全密钥。
- `DATABASE_URL`：本地默认 SQLite；部署到 Railway 建议使用 PostgreSQL。
- `COOKIE_SECURE`：生产环境 HTTPS 下应设置为 `true`。
- `COOKIE_SAMESITE`：前后端跨域部署时应设置为 `none`。
- `CORS_ORIGINS`：生产环境建议设置为前端正式域名，不建议长期使用 `*`。

## 本地启动

```powershell
cd C:\Users\达文的电脑\Documents\RAG系统\travel-assistant-backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

浏览器调试页面：

```text
http://127.0.0.1:8001/debug/
```

健康检查：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/health"
```

## 常用测试命令

天气查询：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/weather?location=泉州&days=3" | ConvertTo-Json -Depth 10
```

候选景点查询：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/attractions?city=汕头&max_pois=8" | ConvertTo-Json -Depth 10
```

路线上下文查询：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/route/context?city=汕头&days=3" | ConvertTo-Json -Depth 10
```

第一轮旅行规划：

```powershell
$body = @{
  message = "帮我规划汕头3天旅行，行程不要太紧凑，每天9点开始，18点结束，并结合天气给穿衣建议。"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/chat" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

第二轮选择景点：

```powershell
$body = @{
  message = "选择 1、2、3、4，你帮我合理安排。"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/chat" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

SSE 流式接口：

```powershell
curl.exe -N -X POST "http://127.0.0.1:8001/api/chat/stream" ^
  -H "Content-Type: application/json" ^
  -d "{\"message\":\"帮我规划泉州2天旅行，轻松一点，每天9点开始18点结束。\"}"
```

用户注册：

```powershell
$body = @{
  username = "demo_user"
  password = "password123"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/auth/register" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body `
  -SessionVariable s
```

用户登录：

```powershell
$body = @{
  username = "demo_user"
  password = "password123"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/auth/login" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $body `
  -WebSession $s
```

更新用户画像：

```powershell
$body = @{
  preferred_pace = "relaxed"
  preferred_start_time = "09:00"
  preferred_end_time = "18:00"
  favorite_cities = @("泉州", "汕头")
  favorite_attraction_types = @("历史街区", "海滨")
  notes = "偏好轻松路线，不喜欢太赶。"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/profile" `
  -Method Put `
  -ContentType "application/json; charset=utf-8" `
  -Body $body `
  -WebSession $s
```

查询历史行程：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/plans?limit=10" | ConvertTo-Json -Depth 10
```

## API 列表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/config/map` | 获取前端地图配置 |
| POST | `/api/auth/register` | 用户注册 |
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/logout` | 用户退出 |
| GET | `/api/auth/me` | 查询当前用户 |
| GET | `/api/profile` | 查询用户画像 |
| PUT | `/api/profile` | 更新用户画像 |
| GET | `/api/weather` | 直接查询天气 |
| GET | `/api/route/context` | 查询路线规划上下文 |
| GET | `/api/attractions` | 查询候选景点 |
| POST | `/api/chat` | 非流式旅行规划入口 |
| POST | `/api/chat/stream` | SSE 流式旅行规划入口 |
| POST | `/api/route` | 路线 Agent 单独入口 |
| GET | `/api/plans` | 查询历史行程 |

## Agent 运行逻辑

```text
用户输入
  -> FastAPI /api/chat 或 /api/chat/stream
  -> LangGraph Travel Graph
  -> 总控节点识别用户最新需求和会话状态
  -> 第一阶段：返回候选景点并保存目的地、天数、节奏等上下文
  -> 第二阶段：用户选择景点或要求系统自动选择
  -> 天气 Agent 查询天气
  -> 景区地址信息 Agent 查询 POI、地址、经纬度和路线信息
  -> Planning Agent 通过 MCP/Tool 补充必要数据
  -> Planning Agent 生成结构化 JSON 行程
  -> 前端渲染为天气建议、每日行程、住宿地段和地图路线
```

## MCP 工具

MCP Server 入口：

```powershell
.\.venv\Scripts\python.exe -m app.mcp.travel_tools_server
```

当前暴露的 MCP 工具：

- `get_weather_forecast`：查询高德天气。
- `get_tour_route_context`：查询城市 POI、地址、经纬度和景点间路线信息。
- `get_selected_scenic_address_context`：读取当前会话上一轮候选景点，并结合用户选择返回已选景点上下文。

Planning Agent 优先通过 MCP Client 加载工具；如果 MCP 子进程启动失败，会回退到本地 LangChain Tool，保证核心规划流程仍可运行。

更详细说明见：[docs/mcp-tools.md](docs/mcp-tools.md)。

## Railway 部署

项目已包含 `railway.json`：

```json
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT"
  }
}
```

Railway 环境变量建议：

```env
DEEPSEEK_API_KEY=your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
AMAP_API_KEY=your-amap-web-service-key
AMAP_PRIVATE_KEY=your-amap-private-key-if-needed
AMAP_JS_API_KEY=your-amap-js-api-key
AMAP_JS_SECURITY_CODE=your-amap-js-security-code
APP_ENV=production
LOG_LEVEL=INFO
DATABASE_URL=railway-postgres-url
COOKIE_SECURE=true
COOKIE_SAMESITE=none
CORS_ORIGINS=https://your-frontend-domain
```

本地 CLI 部署命令：

```cmd
cd C:\Users\达文的电脑\Documents\RAG系统\travel-assistant-backend
"C:\Users\达文的电脑\AppData\Roaming\npm\railway.cmd" login
"C:\Users\达文的电脑\AppData\Roaming\npm\railway.cmd" init
"C:\Users\达文的电脑\AppData\Roaming\npm\railway.cmd" up
"C:\Users\达文的电脑\AppData\Roaming\npm\railway.cmd" domain
```

## GitHub 上传前检查

真实 Key 只允许放在 `.env` 或部署平台环境变量中，不要提交到 GitHub。

当前 `.gitignore` 已忽略：

- `.env`
- `.venv/`
- `.idea/`
- `__pycache__/`
- `*.db`
- `*.sqlite`
- `*.log`
- `dist/`
- `build/`

上传前可以执行：

```powershell
git status --short
git check-ignore -v .env travel_assistant.db .venv .idea
```

## 简历项目描述

智能旅行助手是一个基于 FastAPI、LangGraph、MCP、DeepSeek 和高德开放平台构建的全栈 AI 行程规划系统。系统通过多 Agent 协作完成城市识别、候选景点推荐、天气查询、景区地址查询、路线信息获取和结构化行程生成；结合 SSE 流式输出、HttpOnly Cookie 会话管理、SQLAlchemy 持久化和用户画像，实现了较完整的 AI 旅行规划闭环。
