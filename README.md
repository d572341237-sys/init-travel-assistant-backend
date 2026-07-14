# 智能旅行助手后端 MVP

基于 FastAPI + LangGraph StateGraph 的多 agent 旅行规划 MVP。当前能力：

- 自然语言询问天气
- 自然语言旅游路线规划
- LangGraph StateGraph 编排 supervisor、天气 agent、景区地址信息 agent、总 planning agent 和结果合并节点
- 两阶段旅行规划：先返回候选景点，用户选择后再生成完整行程
- 后端解析地点和天数
- 高德 Web 服务 API 查询天气实况和预报
- 高德 Web 服务 API 查询 POI 和路线距离
- DeepSeek OpenAI-compatible 大模型生成旅行建议
- 普通 JSON 接口与 SSE 流式接口；SSE 中由总 planning agent 流式输出最终旅行规划
- 浏览器会话通过后端 HttpOnly Cookie 管理，前端不需要手动传 `thread_id`
- SQLite + SQLAlchemy 持久化会话状态和历史旅行规划
- 用户注册、登录、退出、当前用户查询
- 用户画像存储：旅行节奏、常用出发/结束时间、偏好城市、偏好景点类型、备注
- 总 planning agent 输出结构化 JSON，前端负责渲染为可读行程
- SessionStore 使用内存缓存 + 数据库持久化，支持 TTL 过期和最大会话数清理
- 统一错误响应：用户侧返回稳定错误码和友好提示，后端日志记录完整异常

## 启动

```powershell
cd C:\Users\达文的电脑\Documents\RAG系统\travel-assistant-backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

AMAP_API_KEY=your-amap-web-service-key
AMAP_PRIVATE_KEY=your-amap-private-key-if-signature-enabled
AMAP_JS_API_KEY=your-amap-web-js-api-key
AMAP_JS_SECURITY_CODE=your-amap-web-js-security-code

DATABASE_URL=sqlite:///./travel_assistant.db
```

密钥安全：

- `.env` 只保存本地真实密钥，不要提交到 Git。
- `.env.example` 只保存占位符，用于说明需要哪些环境变量。
- 项目已通过 `.gitignore` 忽略 `.env`、`.venv/`、`.idea/`、`__pycache__/` 等本地文件。
- 项目已通过 `.gitignore` 忽略本地 SQLite 数据库文件，如 `*.db`、`*.sqlite`。
- 部署到服务器时，建议使用部署平台的环境变量管理真实 Key，而不是把 Key 写入代码或 README。

高德 Key 必须选择 Web 服务 API 类型。天气查询依赖两个高德接口：

- 行政区域查询：把城市名解析成 adcode
- 天气查询：使用 adcode 查询实况和预报

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

## 调试

健康检查：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/health"
```

直接测试气象 API：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/weather?location=泉州&days=3"
```

测试天气 agent：

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/chat" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"message":"查一下泉州未来3天天气，适合带伞吗？"}'
```

浏览器访问时，后端会通过 HttpOnly Cookie 自动维护会话。JSON 接口仍会返回当前会话 ID，便于本地调试：

```json
{
  "answer": "...",
  "thread_id": "后端生成的会话 ID"
}
```

综合旅行规划采用两阶段流程：第一次返回候选景点，用户选择景点后再生成完整行程。

```powershell
$body = @{
  message = "帮我规划泉州两天路线，想去开元寺、西街、清源山，轻松一点"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/chat" -Method Post -ContentType "application/json; charset=utf-8" -Body $body
```

第二轮选择景点后典型返回结构：

```json
{
  "answer": "{\"destination\":\"泉州\",\"days_count\":2,\"pace\":\"relaxed\",\"daily_time_window\":{\"start\":\"09:00\",\"end\":\"18:00\"},\"weather_summary\":{},\"itinerary_days\":[],\"hotel_area_recommendation\":{},\"general_tips\":[]}",
  "thread_id": "后端生成或用户传入的会话 ID"
}
```

`answer` 是 JSON 字符串，核心字段：

- `destination`：目的地
- `days_count`：旅行天数
- `pace`：行程节奏
- `daily_time_window`：每日开始和结束时间
- `weather_summary`：天气、穿衣、带伞和安全建议
- `itinerary_days`：分天行程，每个景点包含时间、地点、地址、活动、交通提示
- `hotel_area_recommendation`：住宿地段建议
- `general_tips`：其他建议

测试路线 agent 单独入口：

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/api/route" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"message":"帮我规划泉州一日游路线，想去开元寺、西街、清源山"}'
```

直接测试高德路线上下文：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/route/context?city=泉州&days=1"
```

查询城市候选景点：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/attractions?city=泉州&max_pois=8"
```

两阶段旅行规划：

```powershell
$body = @{
  message = "帮我规划泉州3天路线，轻松一点，每天9点开始18点结束"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/chat" -Method Post -ContentType "application/json; charset=utf-8" -Body $body

$body = @{
  message = "选择 1、3、5，改成2天，紧凑一点，每天10点开始20点结束"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/chat" -Method Post -ContentType "application/json; charset=utf-8" -Body $body
```

查询当前会话历史旅行规划：

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/plans?limit=10"
```

用户注册：

```powershell
$body = @{
  username = "demo_user"
  password = "password123"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/auth/register" -Method Post -ContentType "application/json; charset=utf-8" -Body $body -SessionVariable s
```

用户登录：

```powershell
$body = @{
  username = "demo_user"
  password = "password123"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/auth/login" -Method Post -ContentType "application/json; charset=utf-8" -Body $body -WebSession $s
```

查询当前登录用户：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/auth/me" -WebSession $s
```

更新用户画像：

```powershell
$body = @{
  preferred_pace = "relaxed"
  preferred_start_time = "09:00"
  preferred_end_time = "18:00"
  favorite_cities = @("泉州", "汕头")
  favorite_attraction_types = @("历史街区", "海滨")
  notes = "偏好轻松路线，不喜欢太赶"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8001/api/profile" -Method Put -ContentType "application/json; charset=utf-8" -Body $body -WebSession $s
```

SSE 接口：

```powershell
curl.exe -N -X POST "http://127.0.0.1:8001/api/chat/stream" `
  -H "Content-Type: application/json" `
  -d "{\"message\":\"查一下上海未来3天天气，适合带伞吗？\"}"
```

SSE 会返回 LangGraph 节点事件。第二轮选择景点后，总 planning agent 会 token 流式输出最终行程：

```text
event: agent_selected
data: {"agent":"planning_agent"}

event: agents_started
data: {"agents":["weather_agent","scenic_address_agent","planning_agent"],"mode":"langgraph_parallel"}

event: node_start
data: {"node":"planning_agent"}

event: token
data: {"node":"planning_agent","content":"第"}

event: token
data: {"node":"planning_agent","content":"一天"}

event: node_end
data: {"node":"planning_agent"}

event: done
data: {"answer":"..."}
```

错误响应示例：

```json
{
  "detail": {
    "code": "AGENT_EXECUTION_FAILED",
    "message": "旅行助手暂时无法完成请求，请稍后再试。"
  }
}
```

## 架构

当前 `/api/chat` 使用 LangGraph `StateGraph` 编排多 agent：

```text
START
  -> supervisor
  -> route_agent                         # 第一轮候选景点
  -> weather_agent + scenic_address_agent # 第二轮数据查询
  -> planning_agent                       # 汇总并流式生成
  -> merge
  -> END
```

节点职责：

- `supervisor`：判断用户最新真实需求，处理新城市、新计划、景点选择和需求不明确时的追问。
- `weather_agent`：识别地区和天数，调用高德天气 API，生成天气和穿衣建议。
- `route_agent`：第一轮查询城市候选景点并保存选择上下文。
- `scenic_address_agent`：用户选择景点后查询景区地址、区域和距离信息。
- `planning_agent`：整合天气、景区地址、天数、时间和节奏，流式生成完整旅行规划。
- `merge`：返回最终结果。

## 会话状态

浏览器会话通过 HttpOnly Cookie 保存后端生成的 UUID。多轮对话在同一个浏览器会话内会自动携带 Cookie，前端不需要展示或手动传入 `thread_id`。

当前会话状态使用 SessionStore + SQLite：

- 按 `thread_id` 隔离候选景点和最近一次路线规划上下文。
- 临时状态写入 `session_states` 表，服务重启后可恢复。
- 最终旅行规划写入 `travel_plans` 表，可通过 `/api/plans` 查询。
- 登录用户写入 `users` 表，用户画像写入 `user_profiles` 表。
- 用户登录后，旅行规划会记录 `user_id`，`/api/plans` 优先返回该用户历史行程。
- 默认 2 小时 TTL。
- 默认最多保存 1000 个会话，超过后清理最旧会话。
- 当前方案适合本地 MVP 和单进程服务；生产部署可把 `DATABASE_URL` 切换为 PostgreSQL。

## 目录

```text
app/
  agents/travel_graph.py    LangGraph StateGraph 多 agent 编排
  agents/travel_agent.py    travel graph 对外薄封装
  agents/weather_agent.py   天气 agent 和受控天气流程
  agents/route_agent.py     路线 agent
  agents/scenic_address_agent.py 景区地址信息 agent
  agents/planning_agent.py  总 planning agent
  core/config.py            环境配置
  core/session_store.py     内存缓存 + 数据库会话状态存储
  db/database.py            SQLAlchemy 数据库连接和建表
  db/models.py              数据库模型
  repositories/             数据访问层
  core/security.py          密码哈希和校验
  schemas/chat.py           API 请求响应模型
  tools/weather.py          高德天气工具
  tools/route.py            高德 POI/路线工具
  main.py                   FastAPI 入口
```
