"""把历史 AgentRun 的行程产出回填到 trips / trip_days / trip_items。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/backfill_trips.py --dry-run
    .venv/Scripts/python.exe scripts/backfill_trips.py
    .venv/Scripts/python.exe scripts/backfill_trips.py --force

行为：
- 只处理 status=success 且 findings.itinerary.days 非空的运行；
- 默认跳过已经投影过的 source_run_id；
- --force 重建已存在的行程（保留 title/status，子表重建）；
- --dry-run 只统计，不写库；
- 每条运行单独事务，单条失败不影响其余运行。

不调用 LLM、不调用高德、不打印消息正文或任何密钥。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.agent_run import AgentRun  # noqa: E402
from app.models.trip import Trip  # noqa: E402
from app.models.trip_day import TripDay  # noqa: E402
from app.models.trip_item import TripItem  # noqa: E402
from app.services.trip_persist_service import has_itinerary, sync_run_trip  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回填历史行程到规范化表")
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计，不写数据库"
    )
    parser.add_argument(
        "--force", action="store_true", help="重建已经存在的行程"
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    stats = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "no_itinerary": 0,
    }

    try:
        runs = (
            db.query(AgentRun)
            .filter(AgentRun.status == "success")
            .order_by(AgentRun.id.asc())
            .all()
        )
        print(f"扫描 {len(runs)} 条成功运行...")

        for run in runs:
            if not has_itinerary(run.output):
                stats["no_itinerary"] += 1
                continue

            if args.dry_run:
                existing = (
                    db.query(Trip).filter(Trip.source_run_id == run.id).first()
                )
                if existing is not None and not args.force:
                    stats["skipped"] += 1
                    print(f"  skip  run={run.id} 已有 trip={existing.id}")
                elif existing is not None:
                    stats["updated"] += 1
                    print(f"  would-update run={run.id} trip={existing.id}")
                else:
                    stats["created"] += 1
                    days = len(
                        run.output["findings"]["itinerary"]["days"]
                    )
                    print(
                        f"  would-create run={run.id} "
                        f"conversation={run.conversation_id} days={days}"
                    )
                continue

            try:
                result = sync_run_trip(db, run.id, force=args.force)
            except Exception as exc:
                # 只打印异常类名，避免把连接串/密钥等细节带进终端。
                stats["failed"] += 1
                print(f"  ❌ run={run.id} 失败：{type(exc).__name__}")
                continue

            key = result if result in ("created", "updated", "skipped") else "skipped"
            stats[key] += 1
            print(f"  {result:<8} run={run.id}")

        if args.dry_run:
            print(
                "\n[dry-run] 可新增 {created}，可重建 {updated}，"
                "已存在跳过 {skipped}，无行程 {no_itinerary}，失败 {failed}".format(**stats)
            )
            return 0

        trips = db.query(func.count(Trip.id)).scalar() or 0
        days = db.query(func.count(TripDay.id)).scalar() or 0
        items = db.query(func.count(TripItem.id)).scalar() or 0
        print(
            "\n完成：新增 {created}，重建 {updated}，跳过 {skipped}，"
            "无行程 {no_itinerary}，失败 {failed}".format(**stats)
        )
        print(f"当前 trips={trips}，trip_days={days}，trip_items={items}")
        return 0 if stats["failed"] == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
