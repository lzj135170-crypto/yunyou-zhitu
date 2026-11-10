"""直接跑旅行智能体图，打印每个节点的执行轨迹（开发自测用）。

用法（在 backend 目录下执行）：
    # 单轮
    .venv/Scripts/python.exe scripts/run_travel_graph.py "帮我规划成都三日游"

    # 多轮：同一个 thread_id 连问两句，验证多轮记忆
    .venv/Scripts/python.exe scripts/run_travel_graph.py --thread demo "帮我规划成都三日游" "那酒店呢"

    # 不带记忆（每轮独立），用于对比
    .venv/Scripts/python.exe scripts/run_travel_graph.py --no-memory "成都天气怎么样"

    # 强制走 weather MCP（否则沿用当前配置，默认关闭）
    .venv/Scripts/python.exe scripts/run_travel_graph.py --mcp "成都未来三天天气"

    # 同时启用天气 + POI MCP（景点/美食/住宿专家会拿高德真实地点）
    .venv/Scripts/python.exe scripts/run_travel_graph.py --mcp --amap-mcp "帮我规划成都三日游"

设计要点：
1. 这是【不经过 HTTP 和 FastAPI】的直接验证手段。图本身是独立可运行的，
   出了问题能立刻分清是"图本身的毛病"还是"接口层的问题"。
2. 用 graph.stream(stream_mode="updates") 拿执行轨迹，能看到每个节点吐了什么，
   比只看最终结果有用得多——调度顺序错了、某个专家的结果被覆盖了，一眼就能看出来。
3. 前置条件：数据库能连上（rag 节点要查 documents 表）。
   没配 DEEPSEEK_API_KEY 也能跑，会走降级分支并在输出里说明。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.graph import build_graph  # noqa: E402
from app.agents.llm import get_llm  # noqa: E402
from app.config import settings  # noqa: E402

# 每轮最多走多少步。六个专家 + reset + itinerary + response，
# 加上 planner 的反复进入，正常十几步就结束。
# 设 30 是留余量：真撞上这个上限说明图里有环在绕，是个必须查的信号，不该悄悄放过。
RECURSION_LIMIT = 30


def _print_update(update) -> None:
    """把节点返回的东西压成一行摘要。

    节点返回的可能是普通 dict（如 reset / response），
    也可能是 Command 对象经 LangGraph 拆出来的 update，两种都要能处理。
    """
    if update is None:
        print("       (无更新)")
        return
    if not isinstance(update, dict):
        print(f"       {update!r}")
        return
    for key, value in update.items():
        text = repr(value)
        if len(text) > 150:
            text = text[:150] + " …"
        print(f"       {key} = {text}")


def run_one(graph, thread_id: str, query: str) -> dict:
    """跑一轮并打印轨迹，返回这一轮结束时的状态。"""
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": RECURSION_LIMIT,
    }

    print(f"\n{'=' * 66}")
    print(f"[用户]   {query}")
    print(f"[thread] {thread_id}")
    print("-" * 66)

    step = 0
    values: dict = {}
    # 同时订两种流：updates 给执行轨迹，values 给每步之后的完整状态。
    # 结尾那条 values 就是这一轮结束时的状态。
    #
    # 特意不用 graph.get_state(config)——那个【要求图带 checkpointer】，
    # 加了 --no-memory 时会直接抛 "No checkpointer set"。
    # 从流里取状态则两种情况通吃，脚本不用分叉。
    for mode, payload in graph.stream(
        {"user_query": query}, config=config, stream_mode=["updates", "values"]
    ):
        if mode == "updates":
            for node, update in payload.items():
                step += 1
                print(f"  {step:2d}. {node}")
                _print_update(update)
        elif mode == "values":
            values = payload or {}

    print("-" * 66)
    print(f"[任务清单] {values.get('tasks')}")
    print(f"[已完成]   {values.get('done')}")
    print(f"[消息条数] {len(values.get('messages') or [])}  （多轮时会累加，证明记忆生效）")
    print("[最终回复]")
    print(values.get("final_answer") or "(空)")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="直接运行旅行智能体图并打印执行轨迹",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("queries", nargs="+", help="一个或多个用户问题；多个会依次在同一 thread 里执行")
    parser.add_argument("--thread", default="cli-demo", help="thread_id，同一个 id 的多轮共享记忆（默认 cli-demo）")
    parser.add_argument("--no-memory", action="store_true", help="不带检查点运行，每轮互不影响")
    mcp_group = parser.add_mutually_exclusive_group()
    mcp_group.add_argument("--mcp", dest="mcp", action="store_true", help="强制启用 weather MCP")
    mcp_group.add_argument("--no-mcp", dest="mcp", action="store_false", help="强制关闭 weather MCP")
    parser.set_defaults(mcp=None)

    amap_group = parser.add_mutually_exclusive_group()
    amap_group.add_argument("--amap-mcp", dest="amap_mcp", action="store_true", help="强制启用高德 POI MCP")
    amap_group.add_argument("--no-amap-mcp", dest="amap_mcp", action="store_false", help="强制关闭高德 POI MCP")
    parser.set_defaults(amap_mcp=None)
    args = parser.parse_args()

    if args.mcp is not None:
        # 命令行覆盖环境变量/.env；不传时沿用当前配置。
        settings.MCP_ENABLED = args.mcp
    if args.amap_mcp is not None:
        settings.MCP_AMAP_ENABLED = args.amap_mcp

    llm = get_llm("deepseek") or get_llm("mimo")
    print("=" * 66)
    print(f"LLM 状态：{'✅ 已启用 ' if llm else '❌ 未启用（未配 DEEPSEEK_API_KEY / MIMO_API_KEY）'}")
    print(f"多轮记忆：{'❌ 已关闭（--no-memory）' if args.no_memory else '✅ 已启用'}")
    print(f"天气 MCP：{'✅ 已启用' if settings.MCP_ENABLED else '❌ 已关闭（走 Open-Meteo 直连）'}")
    poi_enabled = settings.MCP_ENABLED and settings.MCP_AMAP_ENABLED
    print(f"POI MCP：{'✅ 已启用（高德真实地点）' if poi_enabled else '❌ 已关闭'}")
    if settings.MCP_AMAP_ENABLED and not settings.MCP_ENABLED:
        print("          ⚠️ MCP_AMAP_ENABLED=true 但 MCP_ENABLED=false，POI 不会生效")

    if args.no_memory:
        graph = build_graph(checkpointer=None)
    else:
        from app.agents.checkpointer import close, get_checkpointer

        graph = build_graph(checkpointer=get_checkpointer())

    try:
        # 图里有哪些节点，一眼确认拓扑对不对
        print(f"节点清单：{sorted(graph.get_graph().nodes)}")

        for query in args.queries:
            run_one(graph, args.thread, query)
    finally:
        if not args.no_memory:
            close()
        from app.services.mcp_weather import close as close_mcp_weather

        close_mcp_weather()

    print(f"\n{'=' * 66}")
    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
