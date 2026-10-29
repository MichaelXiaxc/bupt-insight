"""BUPT Insight command line. No credential is ever accepted as a command argument."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import shutil
import sys

from core import secrets as secret_store
from core.config import ROOT, data_dir, ensure_dirs, load_config
from core.errors import InsightError, safe_error
from core.pipeline import database, database_path, sync


def output(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def parser():
    ap = argparse.ArgumentParser(description="BUPT Insight：只保留 BYR 论坛信息源的本地阅读站")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="创建 config.yaml 与必要目录（不保存密码）")

    web = sub.add_parser("web", help="启动只监听本机的阅读网页")
    web.add_argument("--host", default=None)
    web.add_argument("--port", type=int, default=None)
    web.add_argument("--open", action="store_true", help="启动后打开浏览器")

    cred = sub.add_parser("credentials", help="管理加密保存的 BYR 账号")
    cred.add_argument("action", choices=["save", "status", "clear"])

    login = sub.add_parser("login", help="保存 BYR 登录会话（加密）")
    login.add_argument("source", choices=["byr"])
    login.add_argument("--import-state", metavar="FILE", help="导入 Playwright storage_state JSON 并加密")
    login.add_argument("--timeout", type=int, default=300)

    scan = sub.add_parser("scan", help="采集 BYR 并写入 SQLite")
    scan.add_argument("sources", nargs="*", metavar="SOURCE")
    scan.add_argument("--boards", help="版面代号，逗号分隔")
    scan.add_argument("--push", action="store_true")
    scan.add_argument("--baseline", action="store_true", help="建立基线，不发送历史内容")

    sub.add_parser("push", help="发送待处理队列，重试明确失败的渠道")
    test = sub.add_parser("test-push", help="发送虚构测试通知")
    test.add_argument("channel", choices=["webhook", "ntfy", "pushplus", "serverchan"])

    sub.add_parser("status", help="采集状态与推送队列")
    history = sub.add_parser("history", help="查看本地历史")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--id")

    delivery = sub.add_parser("delivery", help="查看/确认/回执/人工重试")
    delivery.add_argument("action", choices=["list", "confirm", "check", "retry"])
    delivery.add_argument("id", nargs="?", type=int)

    sub.add_parser("health", help="检查最近成功采集、数据新鲜度与备份")

    backup = sub.add_parser("backup", help="在线备份、校验和带确认的恢复")
    backup.add_argument("action", choices=["create", "list", "verify", "restore", "recover-copy"])
    backup.add_argument("name", nargs="?")
    backup.add_argument("--confirm", help="恢复预览提供的 SHA256 指纹")

    profile = sub.add_parser("profile", help="初始化或校验本地画像")
    profile.add_argument("action", choices=["init", "validate", "path"])

    ai = sub.add_parser("ai", help="离线演示、分析、查看或保存模型密钥")
    ai.add_argument("action", choices=["demo", "analyze", "show", "evaluate", "key"])
    ai.add_argument("--id")
    ai.add_argument("--limit", type=int, default=3)
    ai.add_argument("--mode", choices=["off", "mock", "api"])
    ai.add_argument("--notify", action="store_true")

    feedback = sub.add_parser("feedback", help="记录本人对当前内容版本的反馈，不重发通知")
    feedback.add_argument("action", choices=["add", "list"])
    feedback.add_argument("--id")
    feedback.add_argument("--label", choices=["important", "relevant", "irrelevant", "unsure"])
    feedback.add_argument("--quality", type=int, choices=range(1, 6))

    recommend = sub.add_parser("recommend", help="本地预览相关性与证据，不弹窗")
    recommend.add_argument("--id")
    recommend.add_argument("--limit", type=int, default=20)

    evaluation = sub.add_parser("evaluate", help="冻结本地历史、标注并对比规则")
    evaluation.add_argument("action", choices=["freeze", "run", "label", "rate", "list"])
    evaluation.add_argument("batch", nargs="?")
    evaluation.add_argument("--sample")
    evaluation.add_argument("--label", choices=["important", "relevant", "irrelevant", "unsure"])
    evaluation.add_argument("--method", choices=["all", "legacy", "interest", "api"], default="all")
    evaluation.add_argument("--limit", type=int, default=40)
    evaluation.add_argument("--score", type=int, choices=range(0, 6))

    logout = sub.add_parser("logout", help="清除本地会话，可选清除加密凭据")
    logout.add_argument("--clear-credentials", action="store_true")
    sub.add_parser("version", help="打印版本")
    return ap


def credentials_command(args):
    cfg = load_config()
    ensure_dirs(cfg)
    if args.action == "save":
        if not sys.stdin.isatty():
            raise InsightError("interaction_required", "请在本机交互式终端保存凭据")
        username = getpass.getpass("BYR 账号（输入不可见）：").strip()
        password = getpass.getpass("BYR 密码（输入不可见）：")
        if not username or not password:
            raise InsightError("credentials_missing", "账号和密码不能为空")
        secret_store.save_byr_credentials(cfg, username, password)
        output({"status": "saved", **secret_store.credentials_status(cfg)})
    elif args.action == "status":
        output(secret_store.credentials_status(cfg))
    else:
        secret_store.clear_byr_credentials(cfg)
        output({"status": "cleared", "file": str(secret_store.byr_credentials_store(cfg).path)})


def login_byr(args):
    from core import browser

    cfg = load_config()
    ensure_dirs(cfg)
    if args.import_state:
        browser.import_state(cfg, args.import_state)
        return 0
    if not browser.login(cfg, wait_seconds=args.timeout):
        raise InsightError("interaction_required", "BYR 登录未完成，未保存会话")
    return 0


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr
    )
    args = parser().parse_args(argv)
    try:
        if args.cmd == "init":
            destination = ROOT / "config.yaml"
            if destination.exists():
                print("config.yaml 已存在，保留现有配置")
            else:
                shutil.copyfile(ROOT / "config.example.yaml", destination)
                print(f"已创建 {destination}；请编辑后运行 credentials save")
            cfg = load_config()
            ensure_dirs(cfg)
            output({"status": "initialized", "config": str(destination), "data_dir": str(data_dir(cfg))})
            return 0
        if args.cmd == "version":
            from core import __version__

            output({"version": __version__})
            return 0
        if args.cmd == "credentials":
            credentials_command(args)
            return 0
        cfg = load_config()

        if args.cmd == "web":
            from core.web import serve

            serve(args.host, args.port, args.open)
            return 0
        if args.cmd == "login":
            return login_byr(args)
        if args.cmd == "logout":
            secret_store.clear_byr_state(cfg)
            if args.clear_credentials:
                secret_store.clear_byr_credentials(cfg)
            output({"status": "logged_out", "credentials_cleared": bool(args.clear_credentials)})
            return 0
        if args.cmd == "backup":
            from core import backup
            from core.locking import single_run

            path = database_path(cfg)
            with single_run(data_dir(cfg) / "run.lock"):
                if args.action == "create":
                    output(backup.create(path))
                elif args.action == "list":
                    output(backup.listing(path))
                else:
                    if not args.name:
                        raise InsightError("configuration_error", "请指定备份文件名")
                    if args.action == "verify":
                        output(backup.verify(path, args.name))
                    elif args.action == "recover-copy":
                        output(backup.recover_copy(path, args.name))
                    else:
                        output(backup.restore(path, args.name, args.confirm))
            return 0
        if args.cmd == "profile":
            from core import profile

            if args.action == "init":
                output({"path": profile.initialize(cfg), "status": "created_blank"})
            elif args.action == "path":
                output({"path": str(profile.path(cfg))})
            else:
                value = profile.load(cfg)
                output({"status": "valid", "fingerprint": value.fingerprint(), "empty": value == profile.Profile()})
            return 0

        readonly = args.cmd in ("status", "history", "health", "recommend") or (
            args.cmd == "delivery" and args.action == "list"
        ) or (args.cmd == "ai" and args.action == "show") or (args.cmd == "feedback" and args.action == "list")
        from contextlib import nullcontext

        from core.locking import single_run

        guard = nullcontext() if readonly else single_run(data_dir(cfg) / "run.lock")
        with guard:
            db = database(cfg)
            try:
                if args.cmd == "scan":
                    if any(source != "byr" for source in args.sources):
                        raise InsightError("configuration_error", "信息源只有 byr")
                    report = sync(cfg, args.sources or None, args.push, args.baseline, args.boards)
                    output(report)
                    failed = any(s["status"] not in ("ok", "no_new") for s in report["sources"].values())
                    deliveries = any(d["status"] in ("failed", "unknown") for d in report["deliveries"])
                    return int(failed or deliveries or report.get("backup", {}).get("status") == "failed")

                if args.cmd == "push":
                    from core.analysis import run_pending
                    from core.notifications import dispatch

                    db.recover()
                    run_pending(cfg, db)
                    results = dispatch(cfg, db)
                    output(results)
                    return int(not results or any(d["status"] in ("failed", "unknown") for d in results))

                if args.cmd == "test-push":
                    from uuid import uuid4

                    from core.database import identity
                    from core.notifications import dispatch
                    from core.schema import Item

                    key = uuid4().hex
                    item = Item(
                        source="test",
                        channel="BUPT Insight 本地测试",
                        title="BUPT Insight 通知测试",
                        url=f"https://bbs.byr.cn/#insight-test-{key}",
                        body="这是虚构测试消息，用于检查推送配置；不包含任何论坛内容。",
                    )
                    db.upsert(item, channels=[args.channel])
                    cfg["push"]["channels"] = {
                        name: {**value, "enabled": name == args.channel}
                        for name, value in (cfg["push"]["channels"] or {}).items()
                    }
                    cfg["push"]["channels"].setdefault(args.channel, {"enabled": True})
                    results = dispatch(cfg, db, notice_id=identity(item))
                    output(results)
                    return int(not results or any(d["status"] in ("failed", "unknown") for d in results))

                if args.cmd == "feedback":
                    from core.feedback import record
                    from core.profile import load

                    if args.action == "list":
                        output(
                            [
                                dict(r)
                                for r in db.db.execute(
                                    "SELECT id,notice_id,label,explanation_quality,created_at FROM feedback ORDER BY id DESC LIMIT 50"
                                )
                            ]
                        )
                    else:
                        row = db.db.execute("SELECT * FROM notices WHERE id=?", (args.id,)).fetchone()
                        if not row or not args.label:
                            raise InsightError("feedback_invalid", "请指定现有内容 ID 和反馈标签")
                        ident = record(db, row, load(cfg), args.label, explanation_quality=args.quality)
                        output({"status": "recorded", "feedback_id": ident, "delivery_modified": False})

                elif args.cmd == "recommend":
                    from core.analysis import rules
                    from core.feedback import latest
                    from core.personalization import decide
                    from core.profile import load
                    from core.smart import feedback_route

                    profile = load(cfg)
                    rows = (
                        db.db.execute("SELECT * FROM notices WHERE id=?", (args.id,)).fetchall()
                        if args.id
                        else db.db.execute(
                            "SELECT * FROM notices WHERE source='byr' ORDER BY updated_at DESC LIMIT ?",
                            (max(1, min(200, args.limit)),),
                        ).fetchall()
                    )
                    output(
                        [
                            {
                                "id": r["id"],
                                "title": r["title"],
                                "relevance": (analysis := rules(json.loads(r["payload"]), profile)).relevance,
                                "route": feedback_route(json.loads(r["payload"]), analysis, "ok", profile, latest(db, r, profile)),
                                "matches": decide(json.loads(r["payload"]), profile).to_dict(),
                                "qualification": "unknown",
                            }
                            for r in rows
                        ]
                    )

                elif args.cmd == "evaluate":
                    from core import evaluation
                    from core.profile import load

                    if args.action == "freeze":
                        output(evaluation.freeze(cfg, db, load(cfg), args.limit))
                    elif args.action == "list":
                        output([f.name for f in sorted(evaluation.root(cfg).glob("*")) if f.is_dir()])
                    else:
                        if not args.batch:
                            raise InsightError("evaluation_invalid", "请指定评估批次名")
                        if args.action == "run":
                            output(evaluation.evaluate(cfg, args.batch, args.method))
                        elif args.action == "rate":
                            output(evaluation.rate(cfg, args.batch, args.sample, args.method, args.score))
                        else:
                            output(evaluation.label(cfg, args.batch, args.sample, args.label))

                elif args.cmd == "status":
                    from core.health import inspect
                    from core.progress import read as read_progress

                    state = db.status()
                    state["database_path"] = db.db.execute("PRAGMA database_list").fetchone()[2]
                    state["progress"] = read_progress(cfg)
                    state["health"] = inspect(cfg, db)
                    state["analysis"] = [
                        dict(r)
                        for r in db.db.execute(
                            "SELECT engine,status,count(*) AS count FROM analysis_cache GROUP BY engine,status"
                        )
                    ]
                    output(state)

                elif args.cmd == "health":
                    from core.health import inspect

                    value = inspect(cfg, db)
                    output(value)
                    return int(value["status"] != "healthy")

                elif args.cmd == "ai":
                    from core.analysis import analyze_row, ensure_llm_env, settings
                    from core.profile import load

                    options = settings(cfg)
                    if args.mode:
                        options["mode"] = args.mode
                    if args.action == "key":
                        if not sys.stdin.isatty():
                            raise InsightError("interaction_required", "请在本地交互式终端保存 AI 密钥")
                        value = getpass.getpass("AI API Key（不回显，空输入删除）：")
                        if value:
                            secret_store.save_llm_key(cfg, value)
                        else:
                            secret_store.clear_llm_key(cfg)
                        output({"status": "saved" if value else "cleared"})
                    elif args.action == "demo":
                        from core.analysis import rules
                        from core.profile import Profile

                        item = {
                            "title": "虚构研究助理招募",
                            "body": "虚构实验室招募科研助理。要求掌握 Python。报名截止 2026 年 11 月 20 日。",
                            "source": "test",
                        }
                        result = rules(item, Profile(skills=["Python"], goals=["科研"]))
                        output({"simulation": True, "engine": "mock-rules", "result": result.model_dump()})
                        if args.notify:
                            from uuid import uuid4

                            from core.database import identity
                            from core.notifications import dispatch
                            from core.notifications import enabled as enabled_channels
                            from core.schema import Item

                            channels = enabled_channels(cfg)
                            if not channels:
                                output({"status": "skipped", "message": "没有启用的推送渠道；测试消息未入队"})
                                return 0
                            demo = Item(
                                source="test",
                                channel="BUPT Insight 虚构样例",
                                title="BUPT Insight 推送测试",
                                url="https://bbs.byr.cn/#demo-" + uuid4().hex,
                                body=item["body"],
                            )
                            db.upsert(demo, channels=channels)
                            output(dispatch(cfg, db, notice_id=identity(demo)))
                    elif args.action == "show":
                        row = db.db.execute(
                            "SELECT engine,status,result FROM analysis_cache WHERE notice_id=? ORDER BY created_at DESC LIMIT 1",
                            (args.id,),
                        ).fetchone()
                        output(
                            {"engine": row["engine"], "status": row["status"], "result": json.loads(row["result"])}
                            if row
                            else {"status": "not_found"}
                        )
                    elif args.action == "evaluate":
                        from tools.evaluate_analysis import evaluate

                        output(evaluate())
                    else:
                        import time

                        ensure_llm_env(cfg)
                        profile_value = load(cfg)
                        rows = (
                            db.db.execute("SELECT * FROM notices WHERE id=?", (args.id,)).fetchall()
                            if args.id
                            else db.db.execute(
                                "SELECT * FROM notices WHERE source!='test' ORDER BY updated_at DESC LIMIT ?",
                                (max(1, min(100, args.limit)),),
                            ).fetchall()
                        )
                        start = time.monotonic()
                        budget = max(0.1, min(60, float(options.get("budget_seconds", 12))))
                        results = []
                        for row in rows:
                            remaining = budget - (time.monotonic() - start)
                            if remaining <= 0.1:
                                break
                            results.append(analyze_row(db, row, profile_value, options, remaining))
                        output({"processed": len(results), "results": results, "push_created": False})

                elif args.cmd == "history":
                    if args.id:
                        row = db.db.execute("SELECT payload FROM notices WHERE id=?", (args.id,)).fetchone()
                        output(json.loads(row[0]) if row else {"error": "未找到内容"})
                    else:
                        output(db.history(max(1, min(args.limit, 200))))

                elif args.cmd == "delivery":
                    if args.action == "list":
                        output(
                            [
                                dict(r)
                                for r in db.db.execute(
                                    "SELECT id,channel,status,attempts,detail FROM deliveries ORDER BY id DESC LIMIT 50"
                                )
                            ]
                        )
                    else:
                        row = db.db.execute("SELECT * FROM deliveries WHERE id=?", (args.id,)).fetchone()
                        if row is None:
                            raise InsightError("configuration_error", "请指定有效 delivery ID")
                        if args.action == "confirm":
                            db.delivery(args.id, "received", "用户人工确认已收到")
                        elif args.action == "retry":
                            member = db.db.execute("SELECT batch_id FROM digest_members WHERE delivery_id=?", (args.id,)).fetchone()
                            if member:
                                with db.db:
                                    db.db.execute("UPDATE digest_batches SET status='pending' WHERE id=?", (member[0],))
                                    db.db.execute(
                                        "UPDATE deliveries SET status='digest_waiting' WHERE id IN "
                                        "(SELECT delivery_id FROM digest_members WHERE batch_id=?)",
                                        (member[0],),
                                    )
                                output({"status": "digest_retry_requested", "message": "将重发整组汇总，可能包含已查看的成员"})
                            else:
                                db.delivery(args.id, "pending", "用户已核对后显式要求重试")
                        elif args.action == "check":
                            if row["channel"] != "pushplus" or not row["receipt"]:
                                raise InsightError("configuration_error", "该条没有可查询的 PushPlus 流水号")
                            from core.notifications import check_receipt

                            result = check_receipt(row["receipt"])
                            db.delivery(args.id, result.status, result.detail, result.receipt)
                            output(result.__dict__)
            finally:
                db.close()
        return 0
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code == 0:
            return 0
        status, message = safe_error(exc)
        output({"status": status, "message": message})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
