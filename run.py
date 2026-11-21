"""BloomAnalytics 统一命令行入口。

用法
----
    python run.py gen       # 1. 生成模拟数据（CSV，含脏数据）
    python run.py load      # 2. 装载业务库 + ODS 贴源层
    python run.py dw        # 3. 构建维度层与数仓（DWD/DWS/ADS）
    python run.py check     # 4. 数据验证与对账
    python run.py all       # 串起来跑一遍

Windows 用户也可以直接双击各目录下的 .bat 文件（内部调用的就是这个脚本）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CODE_ROOT = ROOT / "00_核心代码_20261120"
sys.path.insert(0, str(CODE_ROOT))

from bloom.logger import get_logger  # noqa: E402

log = get_logger("cli")

BANNER = r"""
   ____  _                         _             _           _
  | __ )| | ___   ___  _ __ ___   / \   _ __   | |_ _   _| |_ ___
  |  _ \| |/ _ \ / _ \| '_ ` _ \ / _ \ | '_ \  | __| | | | __/ __|
  | |_) | | (_) | (_) | | | | | / ___ \| | | | | |_| |_| | |_\__ \
  |____/|_|\___/ \___/|_| |_| |_/_/   \_\_| |_|  \__|\__, |\__|___/
  花店全渠道经营数据分析平台            |___/   v0.1
"""


def cmd_gen(args: argparse.Namespace) -> None:
    from bloom.datagen import generate_all

    generate_all(persist=True, write_sample=not args.no_sample)


def cmd_load(args: argparse.Namespace) -> None:
    from bloom.pipeline import load

    if not args.only_ods:
        load.load_oltp()
    if not args.only_oltp:
        load.load_ods()


def cmd_dw(args: argparse.Namespace) -> None:
    from bloom.pipeline import build_dim, dw

    build_dim.load_dimensions()
    dw.build_dw()


def cmd_check(args: argparse.Namespace) -> None:
    from bloom.pipeline import dw

    ok = dw.run_checks()
    sys.exit(0 if ok else 1)


def cmd_etl(args: argparse.Namespace) -> None:
    """第 2 轮：ODS → DWD 清洗、对账、特征工程。"""
    from bloom.etl import run_all

    run_all()


def cmd_gov(args: argparse.Namespace) -> None:
    """第 2 轮：数据治理（质量/字典/指标/标签）。"""
    from bloom.governance import run_all

    run_all()


def cmd_round2(args: argparse.Namespace) -> None:
    """第 2 轮全流程：ETL → 治理。"""
    t0 = time.time()
    cmd_etl(args)
    cmd_gov(args)
    log.info("第 2 轮完成，总耗时 %.1f 分钟", (time.time() - t0) / 60)


def cmd_analysis(args: argparse.Namespace) -> None:
    """第 3 轮：分析建模（RFM / BCG / 促销 / 渠道 / 库存 / 关联 / 预测）。"""
    from bloom.analysis import run_all

    run_all()


def cmd_dashboard(args: argparse.Namespace) -> None:
    """第 4 轮：启动 Web 看板（Flask + ECharts）。"""
    from bloom.api.app import create_app

    port = int(getattr(args, "port", 0) or 5050)
    app = create_app()
    log.info("=" * 60)
    log.info("看板已启动： http://127.0.0.1:%d", port)
    log.info("按 Ctrl+C 停止服务")
    log.info("=" * 60)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


def cmd_report(args: argparse.Namespace) -> None:
    """第 5 轮：生成经营分析报告（MD / HTML / Excel）与 BI 数据源。"""
    from bloom.reporting import run as build_report
    from bloom.reporting.export import export_all

    build_report()
    export_all()


def cmd_all(args: argparse.Namespace) -> None:
    t0 = time.time()
    cmd_gen(args)
    cmd_load(args)
    cmd_dw(args)
    from bloom.pipeline import dw

    dw.run_checks()
    log.info("全流程完成，总耗时 %.1f 分钟", (time.time() - t0) / 60)


def main() -> None:
    print(BANNER)
    parser = argparse.ArgumentParser(description="花店全渠道经营数据分析平台 · 命令行入口")
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("gen", help="生成模拟业务数据")
    p_gen.add_argument("--no-sample", action="store_true", help="不生成 GitHub 演示样本")
    p_gen.set_defaults(func=cmd_gen)

    p_load = sub.add_parser("load", help="装载业务库与 ODS")
    p_load.add_argument("--only-oltp", action="store_true")
    p_load.add_argument("--only-ods", action="store_true")
    p_load.set_defaults(func=cmd_load)

    p_dw = sub.add_parser("dw", help="构建维度层与数仓")
    p_dw.set_defaults(func=cmd_dw)

    p_check = sub.add_parser("check", help="数据验证与对账")
    p_check.set_defaults(func=cmd_check)

    p_etl = sub.add_parser("etl", help="第2轮：清洗 ODS→DWD + 对账 + 特征工程")
    p_etl.set_defaults(func=cmd_etl)

    p_gov = sub.add_parser("gov", help="第2轮：数据治理（质量/字典/指标/标签）")
    p_gov.set_defaults(func=cmd_gov)

    p_r2 = sub.add_parser("round2", help="第2轮全流程（ETL + 治理）")
    p_r2.set_defaults(func=cmd_round2)

    p_an = sub.add_parser("analysis", help="第3轮：分析建模（8 个模块）")
    p_an.set_defaults(func=cmd_analysis)

    p_rep = sub.add_parser("report", help="第5轮：生成经营分析报告 + BI 数据源")
    p_rep.set_defaults(func=cmd_report)

    p_dash = sub.add_parser("dashboard", help="第4轮：启动 Web 看板")
    p_dash.add_argument("--port", type=int, default=5050, help="监听端口（默认 5050）")
    p_dash.set_defaults(func=cmd_dashboard)

    p_all = sub.add_parser("all", help="全流程（生成→装载→建仓→校验）")
    p_all.add_argument("--no-sample", action="store_true")
    p_all.add_argument("--only-oltp", action="store_true")
    p_all.add_argument("--only-ods", action="store_true")
    p_all.set_defaults(func=cmd_all)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        log.error("执行失败：%s", exc)
        raise


if __name__ == "__main__":
    main()
