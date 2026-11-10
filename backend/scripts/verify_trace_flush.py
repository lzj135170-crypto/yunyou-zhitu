"""验证 span 真的能写进 agent_run_spans，且落库失败不影响业务。

这个脚本会调用真实的 message_service.send_message，但把 LLM 换成假实现，
并把所有写操作放在一个外层事务 + SAVEPOINT 里；结束统一 rollback。
跑完数据库里不会留下任何消息、运行记录或 span。

排查 Redis 污染：message_service 的缓存删除/写入也被替换成空操作，
所以测试内容不会写进 Redis 的历史缓存。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/verify_trace_flush.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import app.database as database
from app.common.tracing import trace_span
from app.models.agent_run import AgentRun
from app.models.agent_run_span import AgentRunSpan
from app.models.conversation import Conversation
from app.models.user import User
from app.services import message_service
from app.services.agent import AgentReply

_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  OK  {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")
        if detail:
            print(f"       {detail}")


def main() -> int:
    conn = database.engine.connect()
    outer = conn.begin()
    TestSession = sessionmaker(
        bind=conn, join_transaction_mode="create_savepoint", autoflush=False
    )
    db = TestSession()

    original_session = database.SessionLocal
    original_generate = message_service.generate_reply
    original_delete = message_service.cache.delete
    original_set = message_service.cache.set_json
    trace_id = ""
    marker = f"trace flush verification {uuid.uuid4().hex}"
    temp_title = ""

    def fake_generate(_db, _conversation, _user_message):
        # 模拟图里的一个节点；真正的图还会产生 graph/node/llm 多级 span。
        with trace_span("node.fake", "node"):
            # prompt 是故意塞进去的禁用字段，用来验证 API 的属性白名单。
            with trace_span(
                "llm.fake",
                "llm",
                node_name="fake",
                attributes={
                    "provider": "fake",
                    "model": "fake-model",
                    "prompt": "should-not-leak",
                },
            ) as llm_span:
                llm_span.tokens = 3
        return AgentReply(
            content="trace flush verification reply",
            total_tokens=5,
            output={"findings": {}, "tasks": [], "done": []},
        )

    try:
        user = db.query(User).first()
        if user is None:
            print("跳过：users 表为空，先运行 scripts/seed_users.py")
            return 0

        conversation = (
            db.query(Conversation)
            .filter(
                Conversation.user_id == user.user_id,
                Conversation.deleted_at.is_(None),
            )
            .first()
        )
        if conversation is None:
            temp_title = f"_trace_flush_verify_{uuid.uuid4().hex}"
            conversation = Conversation(user_id=user.user_id, title=temp_title)
            db.add(conversation)
            db.flush()

        message_service.generate_reply = fake_generate
        message_service.cache.delete = lambda *_args, **_kwargs: None
        message_service.cache.set_json = lambda *_args, **_kwargs: None
        database.SessionLocal = lambda: TestSession()

        user_message, _assistant_message = message_service.send_message(
            db, conversation.id, user.user_id, marker
        )
        run = (
            db.query(AgentRun)
            .filter(AgentRun.request_message_id == user_message.id)
            .order_by(AgentRun.id.desc())
            .first()
        )
        if run is None:
            check("生成了 agent_run", False)
            return 1

        trace_id = run.trace_id
        spans = (
            db.query(AgentRunSpan)
            .filter(AgentRunSpan.run_id == run.id)
            .order_by(AgentRunSpan.start_time.asc(), AgentRunSpan.id.asc())
            .all()
        )

        check("agent_run 成功", run.status == "success")
        check("token 回填", run.total_tokens == 5, str(run.total_tokens))
        check("span 已落库", bool(spans), str(len(spans)))
        check("所有 span 的 trace_id 一致", all(s.trace_id == trace_id for s in spans))
        check("所有 span 的 run_id 一致", all(s.run_id == run.id for s in spans))
        check("有 run 根 span", any(s.span_type == "run" for s in spans))
        check("有 node span", any(s.span_type == "node" for s in spans))
        check("有 db span", any(s.span_type == "db" for s in spans))
        check("有 cache span", any(s.span_type == "cache" for s in spans))
        check(
            "根 span 名称正确",
            any(s.span_type == "run" and s.name == "run.send_message" for s in spans),
        )
        check(
            "span 有耗时和状态",
            all(s.status in ("success", "failed") and s.duration_ms >= 0 for s in spans),
        )

        from app.api.trace import get_run_spans

        api_result = get_run_spans(conversation.id, trace_id, user, db)
        api_data = api_result.data
        from fastapi.encoders import jsonable_encoder

        encoded = jsonable_encoder(api_result)
        check(
            "API 响应可 JSON 序列化",
            encoded["data"]["run"]["run_id"] == run.id,
            str(encoded.get("data", {}).get("run")),
        )
        check("API 能按 trace 查回 run", api_data.run.run_id == run.id)
        check("API span 数与 DB 一致", len(api_data.spans) == len(spans))
        check(
            "API 返回根 span",
            any(
                span.name == "run.send_message" and span.span_type == "run"
                for span in api_data.spans
            ),
        )
        llm_api = next(
            (span for span in api_data.spans if span.span_type == "llm"), None
        )
        check("API 返回 LLM span", llm_api is not None)
        if llm_api is not None:
            check(
                "API attributes 保留白名单字段",
                (llm_api.attributes or {}).get("provider") == "fake",
                str(llm_api.attributes),
            )
            check(
                "API attributes 丢弃 prompt",
                "prompt" not in (llm_api.attributes or {}),
                str(llm_api.attributes),
            )
    finally:
        message_service.generate_reply = original_generate
        message_service.cache.delete = original_delete
        message_service.cache.set_json = original_set
        database.SessionLocal = original_session
        db.close()
        outer.rollback()
        conn.close()

    check("外层事务已回滚", True)

    verify_conn = database.engine.connect()
    try:
        if trace_id:
            runs_left = verify_conn.execute(
                text("SELECT count(*) FROM agent_runs WHERE trace_id = :trace"),
                {"trace": trace_id},
            ).scalar()
            spans_left = verify_conn.execute(
                text("SELECT count(*) FROM agent_run_spans WHERE trace_id = :trace"),
                {"trace": trace_id},
            ).scalar()
            check("回滚后没有残留 agent_run", runs_left == 0, str(runs_left))
            check("回滚后没有残留 span", spans_left == 0, str(spans_left))
            messages_left = verify_conn.execute(
                text("SELECT count(*) FROM messages WHERE content = :content"),
                {"content": marker},
            ).scalar()
            check("回滚后没有残留消息", messages_left == 0, str(messages_left))
        else:
            check("回滚后没有残留（未产生 trace）", True)

        if temp_title:
            conversations_left = verify_conn.execute(
                text("SELECT count(*) FROM conversations WHERE title = :title"),
                {"title": temp_title},
            ).scalar()
            check(
                "回滚后没有残留临时会话",
                conversations_left == 0,
                str(conversations_left),
            )
    finally:
        verify_conn.close()

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
