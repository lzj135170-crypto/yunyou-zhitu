"""独立进程运行的 MCP server 包。

目前只有 weather_server；它使用 stdio 传输，由 app/services/mcp_weather.py
作为客户端拉起。不要把 server 的 import 放进 app.main：它不是 FastAPI 的一部分，
只会在子进程里执行。
"""
