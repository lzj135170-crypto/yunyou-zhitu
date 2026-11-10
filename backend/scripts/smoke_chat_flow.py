"""会话链路冒烟测试（开发自测用）。

用法（在 backend 目录下执行，需要服务已经启动）：
    .venv/Scripts/python.exe scripts/smoke_chat_flow.py

前置条件：
1. 服务已启动：
       .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
2. users 表里有下面 USER_A / USER_B 两个账号，密码为各自的第二项。
   没有的话先执行 scripts/seed_users.py。

设计要点：
1. 只用标准库 urllib + json，不引第三方库。这样这个脚本永远不会因为
   依赖没装而跑不起来——它是用来验证"服务活着吗"的，自己不能先成为变量。
2. 断言失败不中断，把剩下的用例跑完再统一打印统计，最后以非零退出码结束，
   这样挂在 CI 或反复手工执行都能一眼看出有没有回归。
3. 会在库里创建会话，结束时自动删除，不留垃圾数据。

⚠️ 耗时提醒：接入智能体图之后，每次"发消息"都会真实调用大模型跑一整轮
（Planner + 各专家 + 汇总，一轮 20 秒起步），本脚本要发好几条，
**跑完一轮大概两三分钟**。这不是卡住了，等它跑完即可。
另外它会真实消耗 DeepSeek 的额度——别当成免费的单元测试反复刷。
"""

import json
import os
import urllib.error
import urllib.request

# 默认打 8000，也可以用环境变量指到别的端口，方便对着临时起的服务验证：
#     SMOKE_BASE_URL=http://127.0.0.1:8001 python scripts/smoke_chat_flow.py
BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000")

# 主流程用的账号
USER_A = ("user01", "123456")
# 专门用来测越权的另一个账号：拿它的 token 去访问 USER_A 的会话，应该全部被拒
USER_B = ("user02", "123456")

_passed = 0
_failed = 0


def check(condition: bool, label: str) -> bool:
    """记录一条断言结果。失败不抛异常，继续跑完剩下的用例。"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}")
    return condition


def call(
    method: str, path: str, token: str | None = None, body: dict | None = None
) -> tuple[int, dict]:
    """发一个请求，返回 (HTTP 状态码, 响应体 dict)。

    4xx/5xx 也当作正常返回值而不是异常：越权用例正是靠读 404 响应体里的
    code 字段来判断的，如果把 HTTPError 抛出去就没法断言了。
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def login(username: str, password: str) -> str:
    """登录换 token。失败直接终止——后面所有用例都依赖它，继续跑没有意义。"""
    status, body = call(
        "POST", "/api/auth/login", body={"username": username, "password": password}
    )
    if status != 200 or body.get("code") != 200:
        raise SystemExit(
            f"登录失败 {username}：HTTP {status} {body}\n"
            f"请确认该账号存在，或先执行 scripts/seed_users.py 建账号。"
        )
    return body["data"]["token"]


def main() -> int:
    print(f"目标服务：{BASE_URL}")
    token_a = login(*USER_A)
    token_b = login(*USER_B)
    print(f"登录成功：{USER_A[0]} / {USER_B[0]}\n")

    conversation_id: int | None = None
    try:
        # ---------------- 1. 鉴权 ----------------
        print("1. 鉴权")
        status, _ = call("GET", "/api/conversations")
        check(status == 401, f"不带 token 访问会话列表被拒（HTTP {status}）")

        # ---------------- 2. 主链路 ----------------
        print("\n2. 主链路：建会话 → 发消息 → 查历史")
        status, body = call("POST", "/api/conversations", token_a, {})
        check(body.get("code") == 200, "新建会话成功")
        conversation = body["data"]
        conversation_id = conversation["id"]
        check(conversation["title"] is None, "新建时标题为空（等首条消息生成）")

        content = "帮我规划一个周末去杭州的行程\n预算两千"
        status, body = call(
            "POST", f"/api/conversations/{conversation_id}/messages", token_a, {"content": content}
        )
        check(body.get("code") == 200, "发送消息成功")
        sent = body["data"]
        check(sent["user_message"]["role"] == "user", "返回了用户消息")
        check(sent["assistant_message"]["role"] == "assistant", "返回了 AI 回复")

        # 只断言"是像样的回复"，不断言具体文案。
        # 断言具体文案的话，每调一次提示词就得改一次测试，
        # 最后没人会去改，测试变成默认失败——那还不如不写。
        #
        # 这三条在两种运行模式下都成立：
        # - 配了 key：模型生成的真实回复
        # - 没配 key：图会走降级分支产出【降级回复】，同样非空、是中文
        assistant_content = sent["assistant_message"]["content"]
        check(bool(assistant_content.strip()), "AI 回复非空")
        check("桩回复" not in assistant_content, "回复不再来自桩函数（已接入智能体图）")
        check(
            any("一" <= ch <= "鿿" for ch in assistant_content),
            "回复是中文",
        )

        status, body = call("GET", f"/api/conversations/{conversation_id}", token_a)
        title = body["data"]["title"]
        check(title is not None, f"标题已自动生成：{title!r}")
        check("\n" not in (title or ""), "标题里的换行已被压成空格")

        # 再补两条，凑够 3 轮共 6 条消息，后面分页要用
        call("POST", f"/api/conversations/{conversation_id}/messages", token_a, {"content": "第二天呢？"})
        call("POST", f"/api/conversations/{conversation_id}/messages", token_a, {"content": "预算能再省一点吗"})

        status, body = call("GET", f"/api/conversations/{conversation_id}/messages", token_a)
        items = body["data"]["items"]
        check(len(items) == 6, f"历史共 6 条消息（实际 {len(items)}）")
        ids = [m["id"] for m in items]
        check(ids == sorted(ids), "按 id 升序返回（旧 → 新）")
        check(body["data"]["has_more"] is False, "默认 limit 下 has_more=False")

        # ---------------- 3. 列表排序 ----------------
        print("\n3. 会话列表")
        status, body = call("GET", "/api/conversations", token_a)
        listed = [c["id"] for c in body["data"]]
        check(listed and listed[0] == conversation_id, f"最近聊过的会话排最前（前 3：{listed[:3]}）")

        # ---------------- 4. 越权 ----------------
        print("\n4. 越权防护（用 USER_B 的 token 访问 USER_A 的会话）")
        for method, path, payload, label in [
            ("GET", f"/api/conversations/{conversation_id}", None, "读会话详情"),
            ("PATCH", f"/api/conversations/{conversation_id}", {"title": "改别人的"}, "改标题"),
            ("GET", f"/api/conversations/{conversation_id}/messages", None, "读消息"),
            ("POST", f"/api/conversations/{conversation_id}/messages", {"content": "越权"}, "发消息"),
            ("DELETE", f"/api/conversations/{conversation_id}", None, "删会话"),
        ]:
            status, body = call(method, path, token_b, payload)
            check(
                body.get("code") == 404 and body.get("message") == "会话不存在",
                f"{label} 被拒（code={body.get('code')}）",
            )

        # 越权写尝试不能真的写进去
        status, body = call("GET", f"/api/conversations/{conversation_id}/messages", token_a)
        check(len(body["data"]["items"]) == 6, "越权写尝试后消息数不变（没有产生任何写入）")

        # ---------------- 5. 分页 ----------------
        print("\n5. 游标分页")
        status, body = call("GET", f"/api/conversations/{conversation_id}/messages?limit=2", token_a)
        page1 = body["data"]["items"]
        check(len(page1) == 2 and body["data"]["has_more"] is True, "limit=2 时 has_more=True")

        status, body = call(
            "GET",
            f"/api/conversations/{conversation_id}/messages?limit=2&before_id={page1[0]['id']}",
            token_a,
        )
        page2 = body["data"]["items"]
        check(
            len(page2) == 2 and page2[-1]["id"] < page1[0]["id"],
            "用 before_id 翻到了更早的一页",
        )
        check(
            not ({m["id"] for m in page1} & {m["id"] for m in page2}),
            "两页之间没有重复消息",
        )

        # ---------------- 6. 参数校验 ----------------
        print("\n6. 参数校验")
        status, _ = call(
            "POST", f"/api/conversations/{conversation_id}/messages", token_a, {"content": "   \n  "}
        )
        check(status == 422, f"纯空白内容被拒（HTTP {status}）")
        status, _ = call("POST", "/api/conversations", token_a, {"title": "x" * 300})
        check(status == 422, f"超长标题被拒（HTTP {status}）")

        # ---------------- 7. 改名 + 删除 ----------------
        print("\n7. 重命名与删除")
        status, body = call(
            "PATCH", f"/api/conversations/{conversation_id}", token_a, {"title": "杭州周末游"}
        )
        check(body["data"]["title"] == "杭州周末游", "重命名成功")

        status, body = call("DELETE", f"/api/conversations/{conversation_id}", token_a)
        check(body.get("code") == 200, "删除成功")
        conversation_id_deleted = conversation_id
        conversation_id = None  # 已删除，finally 里不用再清

        status, body = call("GET", f"/api/conversations/{conversation_id_deleted}", token_a)
        check(body.get("code") == 404, f"删除后再访问返回 {body.get('code')}（应为 404）")
        status, body = call("GET", f"/api/conversations/{conversation_id_deleted}/messages", token_a)
        check(body.get("code") == 404, "删除后其消息也访问不到")

    finally:
        # 中途失败也要把测试会话清掉，不留垃圾数据
        if conversation_id is not None:
            call("DELETE", f"/api/conversations/{conversation_id}", token_a)
            print(f"\n已清理测试会话 id={conversation_id}")

    total = _passed + _failed
    print(f"\n{'=' * 46}")
    print(f"通过 {_passed} / {total}" + (f"，失败 {_failed}" if _failed else "，全部通过"))
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
