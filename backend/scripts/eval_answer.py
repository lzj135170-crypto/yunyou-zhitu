"""端到端答案质量评估：跑完整的图，让 LLM 当裁判打分。

**和 `eval_quality.py` 的分工**：

    eval_quality.py   只测检索层（快、确定、可回归）
    eval_answer.py    测用户真正拿到的东西（慢、有 LLM 介入、用来看趋势）

为什么两个都要：**检索指标好 ≠ 用户拿到好答案**。中间还隔着两段：

    1. 检索对了，但 response/itinerary 节点没用上——把资料晾在 findings 里，
       靠模型自己的常识回答
    2. 用了，但说得像它自己知道的——最终回复里没有任何出处，
       用户没法核对，也就没法信任

这两件事在检索指标上完全看不出来。26 秒一轮的图跑出来的答案到底怎么样，
只有真的去看才有人知道。

用法（在 backend 目录下执行）：

    # 先跑 10 条看看裁判靠不靠谱（**第一次务必这么做**）
    .venv/Scripts/python.exe scripts/eval_answer.py --limit 10 --show 10

    # 全量
    .venv/Scripts/python.exe scripts/eval_answer.py

    # 只看某些类型
    .venv/Scripts/python.exe scripts/eval_answer.py --kind semantic --limit 5

⚠️ 很慢。每条要跑一整轮图（约 26 秒）再加两次模型调用（裁判 + 可能的改写），
   10 条就要 5 分钟以上。所以默认 limit=10，全量要显式加 --limit 0。

⚠️ **裁判本身需要被验证**。`--show N` 会把前 N 条的「问题 + 检索到的资料 +
最终答案 + 裁判打分 + 裁判理由」完整打出来，**人工看一遍**。
一个不可靠的裁判比没有裁判更糟——它给出一个看起来客观的数字，
所有人都会引用它，而它是错的。
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

QA_PATH = Path(__file__).resolve().parent.parent / "eval" / "qa_set.jsonl"

# 裁判提示词放在这里而不是 app/agents/prompts.py。
# prompts.py 收的是"8 个智能体的系统提示词"，那些提示词决定了产品的行为；
# 这个只在评估脚本里用，改了它不会影响线上任何一条回复。
# 放一起容易让人以为它也是产品的一部分。
JUDGE_PROMPT = """你是一个严格的答案质量评审。给你一道问题和一份「助理最终给出的回答」，
另外给你这次回答【手上有的原始材料】（可能为空）。

你的任务是判断这个回答有多可信，从三个维度各打 1—5 分：

**grounded（依据性）** —— 回答里的具体事实（数字、地名、时间、价格）
有多少能在资料里找到？
  5 = 关键事实都能在资料里找到
  3 = 一部分能找到，一部分找不到
  1 = 资料里几乎没有，基本是凭空说的

**cited（出处）** —— 回答有没有让读者知道信息来自哪里？
  5 = 明确说明出自哪份资料（如"根据《成都美食指南》"）
  3 = 含糊提了"资料显示"之类，但没说是哪份
  1 = 完全没提出处，读起来像是回答者自己就知道的

**complete（完整性）** —— 资料里和这个问题相关、且重要的信息，回答说了多少？
  5 = 相关的都说到了
  3 = 说了主要的部分，漏了一些
  1 = 资料里明明有，但回答基本没用上

⚠️ 几条必须遵守的判分纪律：

1. **材料为空时，grounded 和 complete 都打 1**，不管回答本身写得多好。
   回答得好不代表有依据——那正是最危险的情况。
2. **绝对不要自己判断事实对不对**。你只判断"回答里说的东西能不能在
   给你的材料里找到"，不要用你自己的知识去补。你补的那个判断会让
   打分失去意义。
3. **注意力放在具体的事实上**（金额、时长、地名、开放时间）。
   概括性的话（"成都很值得去"）不算可核对的事实，别拿它当依据。
4. fabricated 字段只列【作为确定事实写出、且材料里没有的】具体事实，
   不要列主观评价。没有就留空。
   ⚠️ 回答里明确带"估算 / 约 / 大概 / 区间 / 资料未给出 / 以官方为准"
   等不确定措辞的数字，**不列进 fabricated**——它没有被当成确定事实。
   但 **grounded 仍按材料判断**：这类没有材料依据的估算如果很多，
   grounded 不能给 5。
5. **材料可能分成两段，两段都算数。** 一段是知识库资料，另一段可能标着
   「实时天气数据」——那是天气接口当次取的实时值，**不是编造**。
   回答里引用天气数字时，只要能在那一段里找到，就算有依据。
6. **材料为空时，回答里出现的任何"出处"都是编造的。** 它没有材料可引，
   却说自己"根据《XX》"——那个来源是虚构的，**cited 也打 1**，
   并把这个出处写进 fabricated。
   ⚠️ 这比"不提出处"更危险：用户看到一个具体书名会以为能去核对，
   而那份资料根本不存在，或者存在但没被用过。

⚠️ **你只看到"原始材料"，看不到各专家节点自己的产出。** 这是有意的：
那些产出本身可能就是编的，拿它们当依据等于让被审查的人自己出证据。
所以判断依据时**只认这两段原始材料**，其余一概不算。
"""


class Judgment(BaseModel):
    """裁判的打分。"""

    grounded: int = Field(ge=1, le=5, description="依据性 1-5")
    cited: int = Field(ge=1, le=5, description="出处 1-5")
    complete: int = Field(ge=1, le=5, description="完整性 1-5")
    fabricated: list[str] = Field(
        default_factory=list,
        description="回答里作为确定事实写出、但材料里没有的具体事实；"
        "明确标注为估算/约/资料未给出的不算",
    )
    reason: str = Field(description="判断理由，一两句话")


@dataclass
class AnswerCase:
    index: int
    query: str
    kind: str
    answer: str
    context: str
    note: str  # rag 节点的 note，说明这次检索实际走了哪条路
    weather: str = ""
    """天气节点这次拿到的实时数据。

    ⚠️ 必须交给裁判，否则天气类回答会被系统性地判成"编造"。
    实测见过：回答里"16 日降水概率 92%、气温 17.2—23.5℃"被列进 fabricated，
    因为裁判只看得到知识库那一段，而那段里当然没有实时预报——
    它把"我看不到"当成了"你编的"。

    **裁判看不到的材料等于不存在。** 这不是裁判的错，是喂给它的东西不全。
    """
    judgment: Judgment | None = None


# ==========================================================================
# 裁判自检
#
# 为什么必须有：**一个不可靠的裁判比没有裁判更糟**——它给出一个看起来客观的
# 数字，所有人都会引用它，而它是错的。
#
# 上一轮这个裁判只被【人工抽查过 10 条】。抽查确实抓到了一次真错（它把实时天气
# 判成编造，因为材料里没给天气数据），但那是运气好——恰好有人去看了那 10 条明细。
# 没有机制的话，下一次出错不会有人看。
#
# 这套检查的思路和 `eval_quality.py --self-check` 一样：**造已知答案，断言方向**。
# 区别是那边的答案由确定性函数打分（可以断言精确值），这边由 LLM 打分
# （只能断言方向，而且允许抖动）。
# ==========================================================================

_CHECK_MATERIAL = """【1】《成都主要景点详解 > 大熊猫繁育研究基地》
- **门票**：约 55 元，60 岁以上老人和 1.3 米以下儿童免票。需提前 3—7 天在官方公众号实名预约
- **建议时长**：半天（3—4 小时）
- **关键点**：早上 7:30 开园就进。熊猫在 8—9 点最活跃"""

# 逐字来自材料，且明确点了出处 → 依据/完整/出处都该高
_ANSWER_FAITHFUL = """根据《成都主要景点详解》，大熊猫繁育研究基地门票约 55 元，
60 岁以上老人和 1.3 米以下儿童免票，需提前 3—7 天在官方公众号实名预约。
建议留半天（3—4 小时），并且早上 7:30 开园就进去——熊猫在 8—9 点最活跃。"""

# 通篇和材料无关（讲住宿），而且给的数字材料里一个都没有 → 依据该低、编造该多
_ANSWER_UNRELATED = """成都的住宿选择挺多。春熙路 / 太古里一带经济型酒店大约 250—400 元/晚，
中高档 500—900 元/晚。天府广场 / 盐市口一带连锁酒店密集，大约 300—550 元/晚。
熊猫基地周边也有度假型酒店，大约 500—1000 元/晚。"""

# 一半来自材料、一半是自己加的，且没点出处 → 依据中等，编造里该有"学生票"
_ANSWER_PARTIAL = """门票约 55 元，老人和儿童免票，建议早上 7:30 开园就进去。
另外学生票大约 30 元，淡季还会有折扣，去之前可以查一下。"""


def _mk_case(
    label: str, answer: str, context: str, weather: str = ""
) -> AnswerCase:
    return AnswerCase(
        index=0, query=label, kind="self-check", answer=answer,
        context=context, note="", weather=weather,
    )


def _self_check() -> int:
    """喂已知质量的回答，断言裁判给分的方向。

    ⚠️ 断言的是【方向】不是精确值：裁判是 LLM，同一个输入两次可能差一分。
    所以这里只问"该高的比该低的高"，不问"等于几"。
    如果连方向都不对，那这个裁判给出的任何数字都不能用。
    """
    print("裁判自检：喂已知质量的回答，断言打分方向（不跑图）")
    print("⚠️ 断言的是方向不是精确值——LLM 裁判本身有抖动，差一分是正常的")
    print()

    checks: list[tuple[str, bool, str]] = []

    def run(label: str, answer: str, context: str) -> Judgment | None:
        ac = _mk_case(label, answer, context)
        print(f"  [{label}] 判分中…")
        j = _judge(ac)
        if j is None:
            print(f"    ❌ 裁判调用失败")
            return None
        print(
            f"    依据 {j.grounded} / 出处 {j.cited} / 完整 {j.complete}"
            + (f"  编造 {len(j.fabricated)} 处" if j.fabricated else "  无编造")
        )
        return j

    faithful = run("逐字一致 + 有出处", _ANSWER_FAITHFUL, _CHECK_MATERIAL)
    unrelated = run("完全无关", _ANSWER_UNRELATED, _CHECK_MATERIAL)
    partial = run("一半材料一半自编", _ANSWER_PARTIAL, _CHECK_MATERIAL)
    no_material = run("材料为空", _ANSWER_FAITHFUL, "")

    print()
    print("断言：")

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))
        print(f"  {'✅' if ok else '❌'} {name}")
        if not ok:
            print(f"     {detail}")

    if faithful and unrelated:
        check(
            "逐字一致的依据性 > 完全无关的",
            faithful.grounded > unrelated.grounded,
            f"逐字 {faithful.grounded} vs 无关 {unrelated.grounded}——裁判分不出"
            f"'抄材料'和'编'的话，依据性这一维就没有意义",
        )
        check(
            "完全无关的会被列出编造内容",
            len(unrelated.fabricated) > 0,
            "材料里一个数字都没有的回答，fabricated 却是空的",
        )
        check(
            "逐字一致的不算编造",
            len(faithful.fabricated) == 0,
            f"误判了 {faithful.fabricated[:2]}——把材料原文当编造，会系统性拉低所有分数",
        )
        check(
            "有明确出处的，出处分 > 没提出处的",
            faithful.cited > unrelated.cited,
            f"有出处 {faithful.cited} vs 无出处 {unrelated.cited}——"
            f"裁判看不出'《成都主要景点详解》'和什么都没提的区别",
        )

    if partial and unrelated:
        check(
            "一半材料一半自编的依据性高于完全无关的",
            partial.grounded > unrelated.grounded,
            f"半对 {partial.grounded} vs 无关 {unrelated.grounded}——"
            f"裁判只做二值判断的话，'部分依据'这个档位形同虚设",
        )

    if no_material:
        check(
            "材料为空时依据性打 1（判分纪律 #1）",
            no_material.grounded == 1,
            f"实际 {no_material.grounded}——材料为空时给高分是最危险的情况："
            f"回答写得好不代表有依据",
        )
        check(
            "材料为空时完整性打 1（判分纪律 #1）",
            no_material.complete == 1,
            f"实际 {no_material.complete}",
        )
        # 这一条是自检第一次跑就发现的洞：材料为空时裁判断言
        # "依据 1 / 出处 3"——因为纪律只规定了 grounded 和 complete 打 1，
        # 没人管 cited。而回答里写着"根据《成都主要景点详解》"，
        # 那是**凭空捏的出处**，比不标出处更危险：用户以为能去核对。
        check(
            "材料为空时出处也打 1（判分纪律 #6）",
            no_material.cited == 1,
            f"实际 {no_material.cited}——材料为空时还标出处，那个来源是虚构的",
        )

    passed = sum(1 for _, ok, _ in checks if ok)
    print()
    print("=" * 60)
    print(f"通过 {passed} / {len(checks)}")
    if passed < len(checks):
        print()
        print("⚠️ 有断言没过。**先在裁判上找原因，不要去改被测的回答。**")
        print("   判分纪律或提示词写得不清楚，会让裁判在所有用例上系统性偏。")
    return 1 if passed < len(checks) else 0


def main() -> int:
    args = _parse_args()

    if args.self_check:
        return _self_check()

    cases = _load_cases(args)

    if not cases:
        print("❌ 没有符合条件的用例")
        return 1

    print(f"将评估 {len(cases)} 条（每条跑一轮完整的图，约 26 秒）")
    print("提示：这是慢操作，可以随时 Ctrl+C 中断，已评估的部分会打印出来")
    print()

    results: list[AnswerCase] = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['query']}")
        try:
            answer, context, note, weather = _run_graph(case)
        except Exception as e:
            print(f"    ⚠️ 跑图失败，跳过：{e}")
            continue

        ac = AnswerCase(
            index=i,
            query=case["query"],
            kind=case.get("kind", "?"),
            answer=answer,
            context=context,
            note=note,
            weather=weather,
        )
        ac.judgment = _judge(ac)
        if ac.judgment:
            j = ac.judgment
            print(
                f"    依据 {j.grounded}/5  出处 {j.cited}/5  完整 {j.complete}/5"
                + (f"  编造 {len(j.fabricated)} 处" if j.fabricated else "")
            )
        results.append(ac)

    _report(results)

    if args.show:
        _show(results[: args.show])

    if args.out:
        _save(results, args.out)

    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="端到端答案质量评估")
    p.add_argument("--limit", type=int, default=10, help="跑几条；0 = 全量")
    p.add_argument("--kind", help="只跑这个类型的用例")
    p.add_argument("--show", type=int, default=0, help="完整打印前 N 条供人工核对裁判")
    p.add_argument("--out", help="把结果存成 JSON")
    p.add_argument(
        "--self-check",
        action="store_true",
        help="验证【裁判本身】靠不靠谱：喂已知质量的回答，断言打分方向（不跑图，约 5 次裁判调用）",
    )
    return p.parse_args()


def _load_cases(args: argparse.Namespace) -> list[dict]:
    cases = [
        json.loads(line)
        for line in QA_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # 只评估"有期望答案"的用例。不可答的那几条考的是检索层的分数分布，
    # 不是答案质量——对"上海有什么好玩的"打分，只会得到一堆
    # "依据性 1 分"的无意义数字（因为知识库里本来就没有上海）。
    cases = [c for c in cases if c.get("expect")]
    if args.kind:
        cases = [c for c in cases if c.get("kind") == args.kind]
    return cases if args.limit <= 0 else cases[: args.limit]


def _run_graph(case: dict) -> tuple[str, str, str, str]:
    """跑一轮完整的图，返回 (最终回复, 知识库资料, rag 的 note, 天气实时数据)。

    ⚠️ 每条用一个独立的 thread_id。
    复用同一个 thread 的话，第二条会带着第一条的历史，
    于是"多轮"这件事被无意中打开了，评估的就不是单轮回答了。
    多轮那几条本来就是用 case 里的 history 字段显式构造的。
    """
    from app.agents.graph import build_graph

    graph = build_graph(checkpointer=None)  # 不带检查点 = 每轮独立，正好
    config = {"configurable": {"thread_id": f"eval-{case.get('_i', 0)}"}, "recursion_limit": 30}
    result = graph.invoke({"user_query": case["query"]}, config=config)

    findings = result.get("findings") or {}
    rag = findings.get("rag") or {}
    return (
        result.get("final_answer") or "",
        rag.get("context") or "",
        rag.get("note") or "",
        _weather_material(findings.get("weather")),
    )


def _weather_material(finding: Any) -> str:
    """把天气节点的产出渲染成给裁判看的一段材料。

    ⚠️ 只取【接口返回的原始数据】，不取它写给用户的建议文字。
    理由和判分纪律第 5 条一样：专家节点自己的措辞可能是编的，
    而它引用的接口数据不是。混在一起喂给裁判，等于让它拿被审查者的
    说法当证据。

    一个 dict/list/str 混合的结构，直接 json.dumps 最省事，
    也最不容易把字段漏掉。
    """
    if not finding or finding.get("error"):
        return ""
    return json.dumps(finding, ensure_ascii=False, indent=2, default=str)


def _judge(ac: AnswerCase) -> Judgment | None:
    """让模型给答案打分。失败返回 None（这条不参与统计）。"""
    from app.agents.llm import invoke_structured

    materials = ac.context or "（这次回答没有检索到任何知识库资料）"
    if ac.weather:
        materials += (
            f"\n\n【实时天气数据（天气接口当次取的，不是知识库）】\n{ac.weather}"
        )

    instruction = (
        f"【问题】\n{ac.query}\n\n"
        f"【助理手上有的原始材料】\n{materials}\n\n"
        f"【助理最终给出的回答】\n{ac.answer or '（没有产出回答）'}"
    )
    try:
        parsed, _ = invoke_structured(JUDGE_PROMPT, [], instruction, Judgment)
    except Exception as e:
        print(f"    ⚠️ 裁判调用失败：{e}")
        return None
    return parsed


def _report(results: list[AnswerCase]) -> None:
    scored = [r for r in results if r.judgment]
    if not scored:
        print("\n没有拿到任何评分")
        return

    n = len(scored)
    print()
    print(f"=== 汇总（{n} 条）===")
    for name, key in (("依据性", "grounded"), ("出处", "cited"), ("完整性", "complete")):
        vals = [getattr(r.judgment, key) for r in scored]
        print(f"  {name:<6} 平均 {sum(vals) / n:.2f}/5   分布 {_hist(vals)}")

    fabricators = [r for r in scored if r.judgment.fabricated]
    print()
    print(f"  有编造内容的回答：{len(fabricators)} / {n}")
    for r in fabricators[:5]:
        print(f"    - {r.query}")
        for f in r.judgment.fabricated[:2]:
            print(f"        {f[:60]}")

    # 按检索路径分组：降级了的那些，依据性应该明显更低。
    # 如果降级和不降级一样高，说明"依据性"这个维度没在衡量它该衡量的东西。
    print()
    print("  按检索路径分组（检验裁判有没有在衡量'有没有依据'）：")
    by_backend: dict[str, list[int]] = {}
    for r in scored:
        key = "降级" if "降级" in (r.note or "") or "fallback" in (r.note or "") else "正常"
        by_backend.setdefault(key, []).append(r.judgment.grounded)
    for key, vals in by_backend.items():
        print(f"    {key:<6} {len(vals):>2} 条   依据性平均 {sum(vals) / len(vals):.2f}/5")


def _hist(vals: list[int]) -> str:
    counts = {i: vals.count(i) for i in range(1, 6)}
    return "  ".join(f"{k}分×{v}" for k, v in counts.items() if v)


def _show(results: list[AnswerCase]) -> None:
    """完整打印，供人工核对裁判的判断。**这一步不能省。**"""
    print()
    print("=" * 70)
    print("以下供人工核对裁判——重点看它给的分和理由对不对得上")
    print("=" * 70)
    for r in results:
        if not r.judgment:
            continue
        j = r.judgment
        print()
        print(f"──── [{r.index}] {r.query}  ({r.kind})")
        print(f"  检索路径: {r.note}")
        print(f"  资料长度: {len(r.context)} 字", end="")
        # 天气那一段单独标出来：核对裁判时最容易被误判的就是它——
        # 回答里的天气数字在【资料】里找不到，却在【实时天气数据】里能找到。
        print(f" + 天气数据 {len(r.weather)} 字" if r.weather else "（无天气数据）")
        print(f"  【资料】{r.context[:300] or '（无）'}{'…' if len(r.context) > 300 else ''}")
        print(f"  【回答】{r.answer[:400]}{'…' if len(r.answer) > 400 else ''}")
        print(f"  【裁判】依据 {j.grounded} / 出处 {j.cited} / 完整 {j.complete}")
        print(f"          理由：{j.reason}")
        if j.fabricated:
            print(f"          认定编造：{j.fabricated}")


def _save(results: list[AnswerCase], path: str) -> None:
    data = [
        {
            "query": r.query,
            "kind": r.kind,
            "note": r.note,
            "answer": r.answer,
            "context_len": len(r.context),
            "judgment": r.judgment.model_dump() if r.judgment else None,
        }
        for r in results
    ]
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已存到 {path}")


if __name__ == "__main__":
    sys.exit(main())
