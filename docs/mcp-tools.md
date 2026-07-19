# MCP 工具说明

本项目已经将旅行规划所需的外部数据查询封装为 MCP Server，Planning Agent 可以通过 MCP Client 自主决定是否调用工具。

## MCP Server

入口文件：

```text
app/mcp/travel_tools_server.py
```

启动方式：

```powershell
.\.venv\Scripts\python.exe -m app.mcp.travel_tools_server
```

默认使用 `stdio` transport，主要供后端 Planning Agent 作为 MCP Client 自动拉起和调用。

## 暴露工具

### get_weather_forecast

查询指定城市或地区未来 1 到 7 天高德天气，返回穿衣、带伞和行程天气判断所需的原始数据。

参数：

- `location`：城市或地区名称。
- `days`：查询天数。

### get_tour_route_context

根据城市、景点关键词和旅行天数查询高德 POI、地址、经纬度与景点间驾车路线信息。

参数：

- `city`：城市名称。
- `keywords`：景点关键词列表。
- `days`：旅行天数。
- `max_pois`：最大 POI 数量。

### get_selected_scenic_address_context

读取当前会话中上一轮保存的候选景点，按用户最新选择或自动选择要求返回已选景点地址、经纬度和路线信息。

参数：

- `selection_message`：用户第二轮选择内容，例如“选择 1、2、3”或“你帮我选并直接规划”。

补充上下文通过 MCP 子进程环境变量注入：

- `TRAVEL_MCP_THREAD_ID`
- `TRAVEL_MCP_AUTO_SELECT_ATTRACTIONS`
- `TRAVEL_MCP_AUTO_FILL_REMAINING_ATTRACTIONS`
- `TRAVEL_MCP_ADDITIONAL_ATTRACTIONS`

这样可以避免由前端或模型直接传入敏感会话标识。

## 调用链路

```text
用户输入
  -> FastAPI /api/chat 或 /api/chat/stream
  -> LangGraph Travel Graph
  -> Planning Agent
  -> stdio MCP Client
  -> app.mcp.travel_tools_server
  -> 高德天气 / 高德 POI / 高德路线 API
  -> Planning Agent 汇总生成结构化 JSON 行程
```

## 降级策略

如果 MCP 子进程启动失败或 MCP 工具加载失败，Planning Agent 会自动回退到本地 LangChain Tool，保证核心旅行规划流程仍可运行。
