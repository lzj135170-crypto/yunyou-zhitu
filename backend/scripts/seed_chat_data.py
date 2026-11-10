"""向 conversations / messages / agent_runs / user_preferences
四张表灌入模拟数据（开发、联调、前端对接自测用）。

⚠️ 【不造 documents】。那张表现在只有一个写入方：
scripts/ingest_knowledge.py，它按 knowledge/ 下的 Markdown 语料推导出每一行。
这里曾经造过 4 条假文档，已经删了——理由见文件里 SEED_DOCUMENTS 原址的注释。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/seed_chat_data.py

前置条件：users 表里必须已经有账号。如果还没灌，先执行：
    .venv/Scripts/python.exe scripts/seed_users.py

设计要点：
1. 用户是"引用"而不是"新建"：脚本按 username 去 users 表里查真实存在的用户，
   查不到就跳过并打印提示，绝不凭空造用户（数据库外键也会拦住这种数据）。
   所以本脚本不关心 users 表里到底有哪些账号，缺了只会跳过，不会报错中断。
2. 幂等：会话按 (所属用户, 标题) 判断，偏好按用户判断，
   已存在的直接跳过。和 seed_users.py 一样可以反复执行来补齐缺失的数据。
3. 子数据跟随父数据：messages 和 agent_runs 都挂在会话下，
   只有当会话是本次新建的，才会给它插消息和运行记录。
   否则反复执行会让会话里的消息越插越多。
4. 所有写入放在一个事务里，任何一条失败整体回滚，不会留下插了一半的脏数据。
"""

# 把 backend 根目录加入模块搜索路径，这样才能 import app 包。
# 否则直接执行 scripts/seed_chat_data.py 时，Python 只会把 scripts/ 当作搜索起点，
# 报 ModuleNotFoundError: No module named 'app'。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models.agent_run import AgentRun  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_preference import UserPreference  # noqa: E402

# ============================================================================
# 模拟会话。每条会话带自己的消息和智能体运行记录。
# 字段含义：
#   username      归属账号，必须在 users 表里真实存在，否则整条跳过
#   title         会话标题
#   messages      该会话下的消息，role 取值 user/assistant/system/tool
#   agent_runs    该会话下的智能体调用记录
# 数据里刻意覆盖了几种边界情况，方便测试：
#   - 有一条 system 角色的消息（agent_name 为空，用户看不到）
#   - 有一条 failed 状态的运行记录（output 里放错误信息）
#   - 有一条耗时特别长(tokens/latency 大)的记录，用于测试展示效果
# ============================================================================
SEED_CONVERSATIONS = [
    {
        "username": "user01",
        "title": "成都三日游行程规划",
        "messages": [
            {
                "role": "user",
                "content": "帮我规划一个成都三日游的行程，我喜欢美食和慢节奏，预算 3000 左右。",
            },
            {
                "role": "assistant",
                "content": (
                    "第一天：宽窄巷子 → 人民公园鹤鸣茶社 → 太古里，晚上吃玉林串串；"
                    "第二天：大熊猫繁育研究基地（建议早上 7 点前到）→ 建设路小吃街；"
                    "第三天：都江堰 → 青城山。每天步行量控制在 1 万步以内，符合你说的慢节奏。"
                ),
                "agent_name": "route_agent",
            },
            {
                "role": "user",
                "content": "第三天如果下雨的话有什么替代方案？",
            },
            {
                "role": "assistant",
                "content": (
                    "查了一下，都江堰未来三天有中雨。替代方案建议改成室内路线："
                    "四川博物院 → 金沙遗址博物馆 → 锦里夜游，全程不受天气影响。"
                ),
                "agent_name": "weather_agent",
            },
        ],
        "agent_runs": [
            {
                "agent_name": "route_agent",
                "input": {"city": "成都", "days": 3, "budget": 3000, "tags": ["美食", "慢节奏"]},
                "output": {"plan": ["宽窄巷子", "熊猫基地", "都江堰"], "total_walk_km": 18.5},
                "status": "success",
                "tokens": 820,
                "latency": 1340,
            },
            {
                "agent_name": "weather_agent",
                "input": {"city": "都江堰", "date": "2026-10-14"},
                "output": {"weather": "中雨", "temperature": "16-21℃", "advice": "建议改室内行程"},
                "status": "success",
                "tokens": 260,
                "latency": 480,
            },
        ],
    },
    {
        "username": "user02",
        "title": "北京到三亚的机票和酒店",
        "messages": [
            {
                "role": "user",
                "content": "下个月想从北京去三亚玩 5 天，帮我看看机票和酒店。",
            },
            {
                "role": "assistant",
                "content": (
                    "11 月中旬北京直飞三亚的经济舱往返大约 1800-2400 元，"
                    "建议提前 3 周订票，越临近出发价格越高。"
                ),
                "agent_name": "flight_agent",
            },
            {
                "role": "user",
                "content": "酒店呢？想住海景房。",
            },
            {
                "role": "assistant",
                "content": (
                    "亚龙湾和海棠湾的海景房选择最多，11 月属于旺季前段，"
                    "均价在 600-900 元/晚。如果预算有限可以考虑大东海，价格低三成左右。"
                ),
                "agent_name": "hotel_agent",
            },
        ],
        "agent_runs": [
            {
                "agent_name": "flight_agent",
                "input": {"from": "北京", "to": "三亚", "date": "2026-11-15", "cabin": "economy"},
                "output": {"price_range": [1800, 2400], "airlines": ["国航", "南航", "海航"]},
                "status": "success",
                "tokens": 430,
                "latency": 910,
            },
            {
                "agent_name": "hotel_agent",
                "input": {"city": "三亚", "type": "海景房", "checkin": "2026-11-15"},
                # 这条故意做成失败状态：外部酒店接口超时，output 里记下错误原因，
                # 方便前端测试"加载失败 + 重试"的交互分支。
                "output": {"error": "upstream timeout", "retryable": True},
                "status": "failed",
                "tokens": 0,
                "latency": 5000,
            },
        ],
    },
    {
        "username": "admin",
        "title": "系统使用咨询",
        "messages": [
            # system 角色是给 AI 设定人设的系统提示词，用户界面上不展示。
            # agent_name 留空：它不归属任何智能体。
            {
                "role": "system",
                "content": "你是一个旅行规划助手，回答要简洁，涉及价格时注明数据来源和时效性。",
            },
            {
                "role": "user",
                "content": "这个系统支持多轮对话吗？",
            },
            {
                "role": "assistant",
                "content": "支持。每一轮问答都会以消息的形式保存在当前会话下，我会带着历史上下文回答。",
            },
        ],
        "agent_runs": [],
    },
    {
        "username": "user03",
        "title": "周末周边自驾推荐",
        "messages": [
            {
                "role": "user",
                "content": "周末想从重庆自驾出去两天，有什么推荐？",
            },
            {
                "role": "assistant",
                "content": "推荐武隆仙女山，单程约 2.5 小时，海拔高所以比市区凉快；或者金佛山，适合看日出。",
                "agent_name": "route_agent",
            },
        ],
        "agent_runs": [
            {
                "agent_name": "route_agent",
                "input": {"from": "重庆", "days": 2, "transport": "自驾"},
                "output": {"options": ["武隆仙女山", "金佛山"], "drive_hours": [2.5, 2.0]},
                "status": "success",
                "tokens": 380,
                "latency": 760,
            },
        ],
    },
]

# ============================================================================
# 这里【曾经】有一段 SEED_DOCUMENTS，造了 4 条假的 documents 行：
# 成都旅游攻略.pdf / 三亚酒店性价比清单.md / 川西自驾路线合集.docx /
# 全国高铁时刻表(2026版).json。已经删掉了。
#
# 为什么不能留：那 4 行的 source 指向 /data/docs/ 下并不存在的文件，
# chunk_count（128/37/214/902）是编的数字，meta 里的 author、page_count、
# records 也全是编的。它们看起来和真实的文档行完全一样，没有任何标记，
# 于是会以两种方式骗到人：
#
#   1. 向量库挂掉走降级路时，它们会混在真实文档里一起返回给用户，
#      而用户没有任何办法分辨哪条是真的
#   2. 正常走混合检索时它们又永远搜不到（向量库那边没有对应的块），
#      表现成"接了向量检索之后反而搜不到了"这种很迷惑的现象
#
# documents 表现在【只有一个写入方】：scripts/ingest_knowledge.py，
# 它按 knowledge/ 下的 Markdown 语料推导出每一行。
# 想加文档就往 knowledge/ 里加 .md 文件，不要再往这里塞字面量。
# ============================================================================

# ============================================================================
# 模拟用户偏好。user_preferences.user_id 上有唯一约束，
# 所以一个用户最多一条记录，重复执行会被幂等检查挡掉。
# ============================================================================
SEED_PREFERENCES = [
    {
        "username": "user01",
        "preferences": {
            "city": "成都",
            "budget": 3000,
            "tags": ["美食", "慢节奏"],
            "avoid": ["爬山", "早起"],
        },
    },
    {
        "username": "user02",
        "preferences": {
            "city": "北京",
            "budget": 8000,
            "tags": ["海景", "亲子"],
            "hotel_level": "5星",
        },
    },
    {
        "username": "user03",
        "preferences": {
            "city": "重庆",
            "budget": 1500,
            "tags": ["自驾", "露营"],
        },
    },
]


def seed_chat_data() -> dict[str, int]:
    """把上面三组模拟数据写入数据库，返回各类数据的实际新增条数。

    返回值形如 {"conversation": 3, "message": 9, ...}，
    跳过的不计入，方便从输出直接看出这次到底插进去了多少。
    """
    db = SessionLocal()
    created = {"conversation": 0, "message": 0, "agent_run": 0, "preference": 0}
    skipped_users: set[str] = set()

    try:
        # ---------- 1. 会话 + 消息 + 智能体运行记录 ----------
        for spec in SEED_CONVERSATIONS:
            # 按 username 查用户。查不到就跳过：外键要求 user_id 必须真实存在，
            # 与其让数据库抛错中断整批导入，不如跳过并记下来提示调用方。
            user = db.query(User).filter(User.username == spec["username"]).first()
            if user is None:
                skipped_users.add(spec["username"])
                continue

            # 幂等判断：同一用户下已有同名会话就跳过
            exists = (
                db.query(Conversation)
                .filter(Conversation.user_id == user.user_id)
                .filter(Conversation.title == spec["title"])
                .filter(Conversation.deleted_at.is_(None))
                .first()
            )
            if exists:
                continue

            conversation = Conversation(user_id=user.user_id, title=spec["title"])
            db.add(conversation)
            # flush 而不是 commit：先把 INSERT 发给数据库以便拿到自增的 conversation.id，
            # 但事务不提交。这样能继续用这个 id 挂子记录，同时保留"整体回滚"的能力。
            db.flush()
            created["conversation"] += 1

            # 消息按列表顺序插入。id 自增，后插的 id 更大，
            # 而 Conversation.messages 关系是按 id 排序的，所以聊天记录能保持正确先后。
            message_rows = []
            for msg in spec["messages"]:
                row = Message(
                    conversation_id=conversation.id,
                    role=msg["role"],
                    content=msg["content"],
                    agent_name=msg.get("agent_name"),
                )
                db.add(row)
                message_rows.append(row)
                created["message"] += 1

            # flush 让消息拿到 create_time；再把会话的反规范化计数补齐。
            db.flush()
            visible = [m for m in message_rows if m.role != "system"]
            conversation.message_count = len(visible)
            conversation.last_message_at = (
                max((m.create_time for m in visible), default=None)
            )

            for run in spec["agent_runs"]:
                db.add(
                    AgentRun(
                        conversation_id=conversation.id,
                        # 运行记录必须有明确的归属用户。
                        user_id=user.user_id,
                        agent_name=run["agent_name"],
                        input=run["input"],
                        output=run["output"],
                        status=run["status"],
                        total_tokens=run["tokens"],
                        latency=run["latency"],
                    )
                )
                created["agent_run"] += 1

        # ---------- 2. 用户偏好 ----------
        for pref in SEED_PREFERENCES:
            user = db.query(User).filter(User.username == pref["username"]).first()
            if user is None:
                skipped_users.add(pref["username"])
                continue
            # user_id 上有唯一约束，这里先查一次，避免重复执行时撞约束报错
            if db.query(UserPreference).filter(UserPreference.user_id == user.user_id).first():
                continue
            db.add(UserPreference(user_id=user.user_id, preferences=pref["preferences"]))
            created["preference"] += 1

        # 全部成功才提交。前面若干次 flush 产生的 INSERT 到这里一并生效。
        db.commit()
    except Exception:
        # 任何异常都回滚，前面 flush 过的数据也会一起撤销，不会留下半截数据
        db.rollback()
        raise
    finally:
        # 一定要带括号调用，否则连接不会归还连接池
        db.close()

    if skipped_users:
        # 单独打印而不是抛异常：缺几个账号不该导致整批模拟数据都灌不进去
        print(f"提示：以下账号在 users 表里不存在，相关数据已跳过：{', '.join(sorted(skipped_users))}")
        print("      先跑 scripts/seed_users.py 建好账号，再重新执行本脚本即可补齐。")

    return created


if __name__ == "__main__":
    created = seed_chat_data()

    print("本次新增：")
    print(f"  会话         {created['conversation']} 条")
    print(f"  消息         {created['message']} 条")
    print(f"  智能体运行   {created['agent_run']} 条")
    print(f"  用户偏好     {created['preference']} 条")
