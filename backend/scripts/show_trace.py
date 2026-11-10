"""按 trace_id 或 run_id 打印一次运行的 span 树。

用法（在 backend 目录下执行）：

    .venv/Scripts/python.exe scripts/show_trace.py --trace-id <32位id>
    .venv/Scripts/python.exe scripts/show_trace.py --run-id 123
    .venv/Scripts/python.exe scripts/show_trace.py --trace-id <id> --json

这是本地运维脚本，直接查数据库，不做用户归属校验；
线上要给用户看请走 GET /api/conversations/{id}/runs/{trace_id}/spans。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models.agent_run import AgentRun
from app.models.agent_run_span import AgentRunSpan


def _iso(value):
    return value.isoformat() if value is not None else None


def _run_dict(run: AgentRun) -> dict:
    return {
        "run_id": run.id,
        "trace_id": run.trace_id,
        "status": run.status,
        "agent_name": run.agent_name,
        "total_tokens": run.total_tokens,
        "latency_ms": run.latency,
        "create_time": _iso(run.create_time),
        "finished_at": _iso(run.finished_at),
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def _span_dict(span: AgentRunSpan) -> dict:
    return {
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "span_type": span.span_type,
        "node_name": span.node_name,
        "status": span.status,
        "start_time": _iso(span.start_time),
        "end_time": _iso(span.end_time),
        "duration_ms": span.duration_ms,
        "tokens": span.tokens,
        "error_code": span.error_code,
        "error_message": span.error_message,
        "attributes": span.attributes,
    }


def _print_tree(run: AgentRun, spans: list[AgentRunSpan]) -> None:
    print(
        f"run #{run.id} trace={run.trace_id} status={run.status} "
        f"agent={run.agent_name} latency={run.latency}ms "
        f"tokens={run.total_tokens}"
    )
    if run.error_code:
        print(f"  error={run.error_code} {run.error_message or ''}".rstrip())
    if not spans:
        print("  (no spans; 这次运行可能早于追踪上线)")
        return

    span_ids = {span.span_id for span in spans}
    children: dict[str | None, list[AgentRunSpan]] = {}
    for span in spans:
        parent = span.parent_span_id if span.parent_span_id in span_ids else None
        children.setdefault(parent, []).append(span)
    for items in children.values():
        items.sort(key=lambda item: (item.start_time, item.id))

    def walk(parent: str | None, prefix: str) -> None:
        items = children.get(parent, [])
        for index, span in enumerate(items):
            last = index == len(items) - 1
            branch = "`- " if last else "|- "
            suffix = ""
            if span.error_code:
                suffix += f" error={span.error_code}"
            print(
                f"{prefix}{branch}[{span.span_type}] {span.name} "
                f"{span.status} {span.duration_ms}ms tokens={span.tokens}{suffix}"
            )
            walk(span.span_id, prefix + ("   " if last else "|  "))

    walk(None, "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打印一次智能体运行的 span 树")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--trace-id", dest="trace_id", help="agent_runs.trace_id")
    target.add_argument("--run-id", dest="run_id", type=int, help="agent_runs.id")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是树")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.trace_id:
            run = (
                db.query(AgentRun)
                .filter(AgentRun.trace_id == args.trace_id)
                .first()
            )
        else:
            run = db.get(AgentRun, args.run_id)
        if run is None:
            print("没有找到对应的 agent_run", file=sys.stderr)
            return 1

        spans = (
            db.query(AgentRunSpan)
            .filter(AgentRunSpan.run_id == run.id)
            .order_by(AgentRunSpan.start_time.asc(), AgentRunSpan.id.asc())
            .all()
        )
    finally:
        db.close()

    if args.json:
        print(
            json.dumps(
                {"run": _run_dict(run), "spans": [_span_dict(s) for s in spans]},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    else:
        _print_tree(run, spans)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
