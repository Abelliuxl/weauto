# web-search

- 用途: 联网搜索，优先使用 Tavily API；对外只汇报搜索结果或当前是否可用，不暴露底层实现细节
- 触发词: 搜一下、查一下、帮我找、联网搜

## 规则

- Tavily API Key: `YOUR_TAVILY_API_KEY`
- 代理地址（备用）: `192.168.5.100:7890`
- web_search 和 web_extract 工具已注册在 toolsets.py
- config.yaml 已配置 `web.backend: tavily`
- 优先用 Tavily；不可用时只做内部重试或内部排查

## 步骤

1. 调用 `web_search` 工具（来自 web_tools.py），传入查询词
2. 如果报错或无结果，可做一次内部重试或改用浏览器工具验证
3. 对用户只返回搜索结果，或者明确说明当前联网搜索不可用

## 注意事项

- config.yaml 路径: `~/.hermes/config.yaml`
- web_tools.py 路径: `/Users/claw/.hermes/hermes-agent/tools/web_tools.py`
- tavily 直接返回结构化结果，不需要手动构造请求
- 不要在微信回复里提及 Hermes、OpenClaw、日志路径、环境变量、代理地址、进程名等内部实现细节，除非用户明确要求排查基础设施
