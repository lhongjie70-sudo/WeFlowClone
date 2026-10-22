"""``wfc`` 命令行：把 WeFlow 的数据和 WeClone 的管线串成一条命令流。

设计原则
--------
1. **每一步都能单独跑**：``pull`` 只管取数、``prepare`` 只管校验、
   ``train`` / ``serve`` 只是转发给 WeClone。出问题能一眼定位。
2. **失败要能照做**：所有错误都翻译成中文 + 下一步命令。
3. **不碰上游**：WeClone 的代码一行不改，靠 cwd + settings 绝对路径对接。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .config import (
    ConfigError,
    ExportSettings,
    MediaSettings,
    ServerSettings,
    Settings,
    WatchSettings,
    WeCloneSettings,
    WeFlowSettings,
    load_settings,
)
from .console import dim, failure, info, say, step, success, table, truncate, warning
from .errors import WeFlowCloneError, WeFlowError
from .pipeline import bootstrap, dataset, doctor
from .pipeline.dataset import scan, validate
from .pipeline.weclone import WeCloneRunner
from .sync import Watcher, pull
from .weflow.client import ClientOptions, WeFlowClient
from .weflow.importer import Importer

DEFAULT_CONFIG_HINT = "运行 `wfc init` 生成 weflow.jsonc"


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def build_client(settings: Settings) -> WeFlowClient:
    token = settings.weflow.access_token or os.environ.get("WEFLOW_TOKEN", "")
    return WeFlowClient(
        ClientOptions(
            base_url=settings.weflow.base_url,
            access_token=token,
            timeout=settings.weflow.timeout_seconds,
            retries=settings.weflow.retries,
            page_size=settings.weflow.page_size,
        )
    )


def _load(args: argparse.Namespace) -> Settings:
    path = Path(args.config).expanduser() if getattr(args, "config", None) else None
    return load_settings(path)


def _print_banner() -> None:
    say()
    say("  " + info("WeFlowClone") + dim(f"  v{__version__}") + "  ·  WeFlow 数据 → WeClone 分身")
    say()


def _print_next_steps(steps: list[str]) -> None:
    if not steps:
        return
    say()
    say(dim("下一步："))
    for index, text in enumerate(steps, start=1):
        say(f"  {index}. {text}")


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
def cmd_init(args: argparse.Namespace) -> int:
    _print_banner()
    try:
        result = bootstrap.init_project(
            force=args.force, storage_mode=args.storage
        )
    except ConfigError as exc:
        failure(str(exc))
        return 1

    success(f"项目根：{result['root']}")
    success(f"整合配置：{result['weflow_config']}")
    success(f"WeClone 配置：{result['weclone_settings']}")
    if result["created_dirs"]:
        say(dim(f"  创建了 {len(result['created_dirs'])} 个目录"))
    _print_next_steps(
        [
            "打开 WeFlow → 设置 → API 服务，启动服务并复制 Access Token",
            "把 Token 填进 weflow.jsonc 的 weflow.access_token（或设置环境变量 WEFLOW_TOKEN）",
            "wfc doctor            # 自检：环境 / WeFlow / WeClone / 数据",
            "wfc pull              # 从 WeFlow 拉取聊天记录并生成 WeClone 数据集",
            "wfc prepare           # 校验数据集，必要时调用 weclone-cli make-dataset",
        ]
    )
    return 0


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(args: argparse.Namespace) -> int:
    _print_banner()
    try:
        settings = _load(args)
    except ConfigError as exc:
        failure(str(exc))
        say(f"       {DEFAULT_CONFIG_HINT}")
        return 1

    checks = doctor.run_checks(settings)
    table(
        ["", "检查项", "结果"],
        [[check.symbol, check.name, truncate(check.detail, 100)] for check in checks],
    )

    hints = [check for check in checks if check.hint and check.status != doctor.OK]
    if hints:
        say()
        say(dim("建议："))
        for check in hints:
            say(f"  {check.symbol} {check.name}：{check.hint}")

    failures = [check for check in checks if check.status == doctor.FAIL]
    say()
    if failures:
        failure(f"{len(failures)} 项检查未通过")
        return 1
    warnings = len(checks) - len(failures)
    success("全部关键检查通过")
    return 0


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
def cmd_sessions(args: argparse.Namespace) -> int:
    settings = _load(args)
    client = build_client(settings)
    sessions = client.sessions(limit=10000)
    if not sessions:
        warning("WeFlow 里没有会话。确认微信已登录、数据库已连接。")
        return 0

    if args.json:
        say(json.dumps([s.to_dict() for s in sessions], ensure_ascii=False, indent=2))
        return 0

    rows = []
    for session in sessions:
        kind = "群聊" if session.is_group else "私聊"
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(session.last_timestamp)) if session.last_timestamp else "-"
        rows.append([session.display_name, session.username, kind, stamp])
    say()
    table(["会话", "会话 ID", "类型", "最后消息"], rows)
    say()
    say(dim("用法：wfc pull --session 张三        只拉某个会话"))
    say(dim("      wfc pull --groups             连同群聊一起拉"))
    return 0


# --------------------------------------------------------------------------- #
# pull
# --------------------------------------------------------------------------- #
def cmd_pull(args: argparse.Namespace) -> int:
    settings = _load(args)
    client = build_client(settings)

    step(f"连接 WeFlow：{settings.weflow.base_url}")
    client.health()
    success("WeFlow 在线")

    progress_state = {"last": -1}

    def on_progress(done: int, total: int) -> None:
        percent = int(done / max(1, total) * 100)
        if percent >= progress_state["last"] + 10:
            progress_state["last"] = percent
            say(dim(f"    写入中… {done}/{total}（{percent}%）"))

    step(f"输出目录：{settings.csv_dir}")
    summary = pull(
        settings,
        client,
        selectors=args.session or [],
        since=args.since,
        until=args.until,
        limit=args.limit,
        include_groups=True if args.groups else (False if getattr(args, "no_groups", False) else None),
        media=False if getattr(args, "no_media", False) else None,
        dry_run=args.dry_run,
        progress=on_progress,
    )

    say()
    if summary.results:
        table(
            ["会话", "写入/总数", "媒体", "CSV"],
            [
                [
                    result.session.display_name,
                    f"{result.kept}/{result.written}",
                    str(result.media_files),
                    str(len(result.csv_files)),
                ]
                for result in summary.results
            ],
        )
    for name, reason in summary.skipped:
        warning(f"跳过 {name}：{reason}")
    for name, reason in summary.failures:
        failure(f"{name}：{reason}")

    say()
    if summary.results:
        success(f"完成：{summary.describe()}")
    else:
        warning("没有写入任何数据")

    if summary.results and settings.weclone.auto_make_dataset:
        runner = WeCloneRunner(settings)
        step("auto_make_dataset 已开启，调用 weclone-cli make-dataset")
        code = dataset.make_dataset(settings, runner)
        if code == 0:
            success("make-dataset 完成")
        else:
            warning(f"make-dataset 退出码 {code}，可稍后手动执行 `wfc prepare --make-dataset`")

    if summary.results:
        _print_next_steps(["wfc prepare   校验数据集并生成报告", "wfc serve     启动分身服务"])
    return 0 if not summary.failures else 1


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def cmd_import(args: argparse.Namespace) -> int:
    settings = _load(args)
    importer = Importer(settings)
    total = 0
    failed = 0
    for raw in args.paths:
        path = Path(raw).expanduser()
        step(f"导入 {path}")
        try:
            results = importer.import_path(path)
        except WeFlowCloneError as exc:
            failure(str(exc))
            failed += 1
            continue
        for result in results:
            success(result.summary())
            for message in result.warnings:
                warning(message)
            total += result.messages
    say()
    if total:
        success(f"共导入 {total} 条消息 → {settings.csv_dir}")
        _print_next_steps(["wfc prepare   校验数据集"])
    return 0 if failed == 0 and total else 1


# --------------------------------------------------------------------------- #
# prepare / status
# --------------------------------------------------------------------------- #
def cmd_prepare(args: argparse.Namespace) -> int:
    settings = _load(args)
    stats = scan(settings)
    say()
    say(f"数据集：{stats.describe()}")
    if stats.by_type:
        detail = "、".join(f"{name or '(空)'} {count}" for name, count in sorted(stats.by_type.items()))
        say(dim(f"  消息类型：{detail}"))

    issues = validate(settings)
    if issues:
        say()
        table(
            ["级别", "对象", "问题"],
            [["✗" if issue.level == "fail" else "!", issue.target, issue.message] for issue in issues],
        )
        for issue in issues:
            if issue.hint:
                say(dim(f"    {issue.target}：{issue.hint}"))

    hard = [issue for issue in issues if issue.level == "fail"]
    if hard:
        failure(f"有 {len(hard)} 个问题需要先修复")
        return 1

    if args.check_only or not args.make_dataset:
        success("校验通过")
        if not args.make_dataset:
            say(dim("  加上 --make-dataset 可调用 weclone-cli make-dataset 生成训练数据"))
        return 0

    runner = WeCloneRunner(settings)
    step("调用 weclone-cli make-dataset（这一步需要已安装 WeClone 依赖）")
    code = dataset.make_dataset(settings, runner)
    if code != 0:
        failure(f"make-dataset 失败（exit {code}）")
        return code
    success("make-dataset 完成")
    _print_next_steps(["wfc train     开始 LoRA 微调", "wfc serve     启动分身服务"])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = _load(args)
    stats = scan(settings)
    if args.json:
        say(
            json.dumps(
                {
                    "sessions": [
                        {
                            "folder": item.folder,
                            "messages": item.messages,
                            "files": item.files,
                            "from_me": item.from_me,
                            "from_other": item.from_other,
                            "by_type": item.by_type,
                        }
                        for item in stats.sessions
                    ],
                    "totals": {
                        "messages": stats.messages,
                        "files": stats.files,
                        "media_files": stats.media_files,
                        "media_bytes": stats.media_bytes,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    say()
    if not stats.sessions:
        warning("还没有数据集")
        say(dim("  wfc pull    从 WeFlow 拉取"))
        return 0

    table(
        ["会话", "消息", "我/对方", "图片", "CSV"],
        [
            [
                item.folder,
                str(item.messages),
                f"{item.from_me}/{item.from_other}",
                str(item.images),
                str(item.files),
            ]
            for item in stats.sessions
        ],
    )
    say()
    say(f"合计：{stats.describe()}")
    return 0


# --------------------------------------------------------------------------- #
# serve / train / profile / test
# --------------------------------------------------------------------------- #
def cmd_serve(args: argparse.Namespace) -> int:
    settings = _load(args)
    port = args.port or settings.server.port
    host = args.host or settings.server.host
    inference = not args.no_inference

    runner = WeCloneRunner(settings)
    if not runner.is_present:
        failure(f"找不到 WeClone 源码：{settings.weclone_dir}")
        return 1

    url = f"http://{host}:{port}"
    step(f"启动 WeClone 服务：{url}（推理 {'开' if inference else '关'}）")
    if args.open_browser or settings.server.open_browser:
        threading.Timer(6.0, lambda: webbrowser.open(url)).start()
        say(dim("  服务就绪后会自动打开浏览器"))
    proc = runner.serve(host, port, inference=inference)
    return proc.returncode


def _passthrough(args: argparse.Namespace) -> list[str]:
    extra = list(getattr(args, "extra", []) or [])
    return [item for item in extra if item != "--"]


def cmd_train(args: argparse.Namespace) -> int:
    settings = _load(args)
    runner = WeCloneRunner(settings)
    deps = runner.check_dependencies()
    if not deps.train_ready:
        warning(deps.describe())
        say(dim("  scripts/setup.ps1 会创建 .venv 并安装 WeClone 依赖"))
        if not args.force:
            return 1
    step("调用 weclone-cli train-sft（LoRA 微调）")
    proc = runner.train_sft(_passthrough(args))
    return proc.returncode


def cmd_profile(args: argparse.Namespace) -> int:
    settings = _load(args)
    runner = WeCloneRunner(settings)
    extra = _passthrough(args)
    if args.profile_only:
        extra.append("--profile-only")
    elif args.with_events:
        extra.append("--with-events")
    step("调用 weclone-cli build-profile（抽取画像 / 记忆）")
    proc = runner.build_profile(extra)
    return proc.returncode


def cmd_weclone(args: argparse.Namespace) -> int:
    """原生命令透传：wfc weclone make-dataset --xxx"""
    settings = _load(args)
    runner = WeCloneRunner(settings)
    if not args.args:
        failure("用法：wfc weclone <weclone-cli 子命令> [参数...]")
        return 2
    proc = runner.run(args.args)
    return proc.returncode


# --------------------------------------------------------------------------- #
# bot（内置机器人：只要 API Key）
# --------------------------------------------------------------------------- #
def cmd_bot(args: argparse.Namespace) -> int:
    from .bot import save_bot_config
    from .bot.server import DEFAULT_HOST, DEFAULT_PORT, serve as serve_bot

    try:
        settings = _load(args)
    except ConfigError:
        # 打包版 / 首次运行：自动建目录与最小配置，让用户「双击就能用」
        bootstrap.ensure_runtime()
        settings = _load(args)

    updates = {
        "api_base": args.api_base,
        "api_key": args.api_key,
        "model": args.model,
    }
    updates = {key: value for key, value in updates.items() if value}
    if updates:
        save_bot_config(settings.data_dir, updates)
        success(f"已写入机器人配置：{', '.join(updates)}")

    say()
    say(dim(f"数据目录：{settings.data_dir}"))
    return serve_bot(
        settings,
        host=args.host or DEFAULT_HOST,
        port=args.port or DEFAULT_PORT,
        open_browser=not args.no_browser,
        reindex=args.reindex,
        log=say,
    )


def cmd_bot_config(args: argparse.Namespace) -> int:
    """非交互地查看 / 写入机器人配置（部署脚本用）。"""
    from .bot import load_bot_config, save_bot_config

    settings = _load(args)
    if any([args.api_base, args.api_key, args.model, args.bot_name]):
        updates = {
            "api_base": args.api_base,
            "api_key": args.api_key,
            "model": args.model,
            "bot_name": args.bot_name,
        }
        save_bot_config(settings.data_dir, {k: v for k, v in updates.items() if v})
        success("已更新")
    config = load_bot_config(settings.data_dir)
    say(json.dumps(config.sanitized(), ensure_ascii=False, indent=2))
    say(dim(f"配置文件：{settings.data_dir / 'bot' / 'config.json'}"))
    return 0


# --------------------------------------------------------------------------- #
# watch
# --------------------------------------------------------------------------- #
def cmd_watch(args: argparse.Namespace) -> int:
    settings = _load(args)
    client = build_client(settings)
    client.health()
    watcher = Watcher(settings, client, log=say)
    try:
        watcher.run()
    except KeyboardInterrupt:
        say()
        success("已停止监听")
    return 0


# --------------------------------------------------------------------------- #
# demo
# --------------------------------------------------------------------------- #
def cmd_demo(args: argparse.Namespace) -> int:
    """不用微信、不用装 WeFlow，演示「WeFlow → WeClone 数据集」全链路。"""
    from .tools.mock_weflow import start_server

    _print_banner()
    workdir = Path(tempfile.mkdtemp(prefix="wfc-demo-"))
    media_root = workdir / "weflow-media"
    token = "demo-token"

    step("启动模拟 WeFlow API（内置演示聊天数据）")
    server, _thread = start_server(media_root, token=token)
    say(dim(f"  {server.base_url}"))

    demo_root = workdir / "project"
    settings = Settings(
        weflow=WeFlowSettings(
            base_url=server.base_url, access_token=token, timeout_seconds=10, retries=0, page_size=7
        ),
        export=ExportSettings(
            data_dir=str(demo_root / "data"),
            include_types=["text", "image", "file", "voice", "sticker"],
            media=MediaSettings(download=True, images=True),
        ),
        weclone=WeCloneSettings(),
        server=ServerSettings(),
        watch=WatchSettings(),
        root=demo_root,
        source=Path("<demo>"),
    )

    say()
    step("拉取会话（分页 page_size=7，模拟真实翻页）")
    client = WeFlowClient(
        ClientOptions(
            base_url=server.base_url,
            access_token=token,
            page_size=settings.weflow.page_size,
            retries=0,
            timeout=10,
        )
    )
    summary = pull(settings, client, include_groups=True, media=True)
    for result in summary.results:
        success(result.summary())

    say()
    step("产出文件")
    for path in sorted(settings.csv_dir.rglob("*")):
        if path.is_file():
            say(f"  {path.relative_to(settings.data_dir)}  {dim(f'{path.stat().st_size} B')}")

    say()
    step("CSV 预览（WeClone 直接读这个）")
    sample = next(iter(sorted(settings.csv_dir.rglob("*.csv"))), None)
    if sample is not None:
        lines = sample.read_text(encoding="utf-8-sig").splitlines()
        for line in lines[:6]:
            say(dim("  ") + line)

    say()
    step("数据集自检")
    issues = validate(settings)
    fails = [issue for issue in issues if issue.level == "fail"]
    if fails:
        for issue in fails:
            failure(f"{issue.target}: {issue.message}")
    else:
        success("校验通过（表头 / 类型 / 时间 / 图片齐全）")
    for issue in issues:
        if issue.level != "fail":
            warning(f"{issue.target}: {issue.message}")

    server.shutdown()
    server.server_close()

    if args.keep:
        success(f"演示产物保留在：{settings.data_dir}")
    else:
        say()
        say(dim(f"演示目录：{workdir}（加 --keep 可保留）"))
        shutil.rmtree(workdir, ignore_errors=True)

    say()
    success("演示结束：这条链路跑通后，把 weflow.base_url 换成你本机 WeFlow 的地址即可")
    return 0 if not fails else 1


# --------------------------------------------------------------------------- #
# mock / version
# --------------------------------------------------------------------------- #
def cmd_mock(args: argparse.Namespace) -> int:
    from .tools.mock_weflow import main as mock_main

    argv = ["--port", str(args.port), "--token", args.token]
    if args.media_dir:
        argv += ["--media-dir", args.media_dir]
    return mock_main(argv)


def cmd_version(args: argparse.Namespace) -> int:
    say(f"WeFlowClone {__version__}")
    return 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    # --config 既能写在子命令前，也能写在子命令后：
    #    wfc --config a.jsonc pull   /   wfc pull --config a.jsonc
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="weflow.jsonc 路径（默认项目根）", default=None)

    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--config",
        help="weflow.jsonc 路径（默认项目根）",
        default=argparse.SUPPRESS,  # 避免覆盖主命令上已解析的值
    )

    parser = argparse.ArgumentParser(
        prog="wfc",
        parents=[common],
        description="WeFlowClone：以 WeFlow 为数据底座、WeClone 为训练引擎的数字分身工作站",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常用流程：\n"
            "  wfc init                 生成配置与目录\n"
            "  wfc doctor               自检环境 / WeFlow / WeClone\n"
            "  wfc pull                 从 WeFlow 拉取聊天记录\n"
            "  wfc prepare              校验数据集（可加 --make-dataset）\n"
            "  wfc train                微调模型\n"
            "  wfc serve                启动分身服务\n"
            "  wfc demo                 无微信演示全链路\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"WeFlowClone {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def add(name: str, **kwargs: object) -> argparse.ArgumentParser:
        return subparsers.add_parser(name, parents=[sub_common], **kwargs)  # type: ignore[arg-type]

    init = add("init", help="生成 weflow.jsonc、WeClone settings.jsonc 与目录骨架")
    init.add_argument("--force", action="store_true", help="覆盖已存在的配置")
    init.add_argument(
        "--storage",
        choices=["plaintext", "encrypted"],
        default="plaintext",
        help="WeClone 数据存储模式（默认 plaintext，便于与 WeFlow 明文导出衔接）",
    )
    init.set_defaults(func=cmd_init)

    doctor_cmd = add("doctor", help="自检：环境 / WeFlow 连接 / WeClone 管线 / 数据")
    doctor_cmd.set_defaults(func=cmd_doctor)

    sessions = add("sessions", help="列出 WeFlow 中的会话")
    sessions.add_argument("--json", action="store_true", help="输出 JSON")
    sessions.set_defaults(func=cmd_sessions)

    pull_cmd = add("pull", help="从 WeFlow 拉取聊天记录并生成 WeClone 数据集")
    pull_cmd.add_argument("--session", action="append", metavar="名称", help="只拉某个会话（可多次）")
    pull_cmd.add_argument("--since", help="起始时间：YYYYMMDD / YYYY-MM-DD")
    pull_cmd.add_argument("--until", help="结束时间：YYYYMMDD / YYYY-MM-DD")
    pull_cmd.add_argument("--limit", type=int, default=0, help="每个会话最多拉多少条（0=全部）")
    pull_cmd.add_argument("--groups", action="store_true", help="连群聊一起拉")
    pull_cmd.add_argument("--no-groups", action="store_true", help="即使配置允许也跳过群聊")
    pull_cmd.add_argument("--no-media", action="store_true", help="不导出媒体文件")
    pull_cmd.add_argument("--dry-run", action="store_true", help="只看会拉哪些会话，不写文件")
    pull_cmd.set_defaults(func=cmd_pull)

    import_cmd = add("import", help="导入 WeFlow 手工导出（WeClone CSV / ChatLab JSON）")
    import_cmd.add_argument("paths", nargs="+", help="导出目录或文件")
    import_cmd.set_defaults(func=cmd_import)

    prepare = add("prepare", help="校验数据集；可选调用 weclone-cli make-dataset")
    prepare.add_argument("--make-dataset", action="store_true", help="校验通过后调用 weclone-cli make-dataset")
    prepare.add_argument("--check-only", action="store_true", help="只校验，不调用 WeClone")
    prepare.set_defaults(func=cmd_prepare)

    status = add("status", help="查看数据集统计")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    serve = add("serve", help="启动 WeClone 对话服务（OpenAI 兼容 /v1 + Web UI）")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--no-inference", action="store_true", help="不加载模型，只起服务")
    serve.add_argument("--no-browser", action="store_true")
    serve.set_defaults(func=cmd_serve)

    train = add("train", help="调用 weclone-cli train-sft 微调模型")
    train.add_argument("--force", action="store_true", help="依赖检查失败也继续")
    train.add_argument("extra", nargs="*", help="透传给 weclone-cli 的参数")
    train.set_defaults(func=cmd_train)

    profile = add("profile", help="调用 weclone-cli build-profile 生成画像")
    profile.add_argument("--profile-only", action="store_true")
    profile.add_argument("--with-events", action="store_true")
    profile.add_argument("extra", nargs="*", help="透传给 weclone-cli 的参数")
    profile.set_defaults(func=cmd_profile)

    watch = add("watch", help="监听 WeFlow 推送并自动增量同步")
    watch.set_defaults(func=cmd_watch)

    demo = add("demo", help="用内置模拟 WeFlow 演示完整数据链路")
    demo.add_argument("--keep", action="store_true", help="保留演示产物")
    demo.set_defaults(func=cmd_demo)

    bot = add("bot", help="启动内置机器人（网页聊天，只要 API Key）")
    bot.add_argument("--host", default=None, help="监听地址（默认 127.0.0.1，局域网访问用 0.0.0.0）")
    bot.add_argument("--port", type=int, default=None, help="端口（默认 8765）")
    bot.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    bot.add_argument("--reindex", action="store_true", help="启动前重新学习聊天记录")
    bot.add_argument("--api-base", default=None, help="OpenAI 兼容地址，例如 https://api.deepseek.com/v1")
    bot.add_argument("--api-key", default=None, help="API Key（也可以用环境变量 WFC_API_KEY）")
    bot.add_argument("--model", default=None, help="模型名，例如 deepseek-chat")
    bot.set_defaults(func=cmd_bot)

    bot_config = add("bot-config", help="查看 / 写入机器人配置（不启动服务）")
    bot_config.add_argument("--api-base", default=None)
    bot_config.add_argument("--api-key", default=None)
    bot_config.add_argument("--model", default=None)
    bot_config.add_argument("--bot-name", default=None)
    bot_config.set_defaults(func=cmd_bot_config)

    mock = add("mock", help="启动模拟 WeFlow API（联调 / 演示用）")
    mock.add_argument("--port", type=int, default=5031)
    mock.add_argument("--token", default="demo-token")
    mock.add_argument("--media-dir", default="")
    mock.set_defaults(func=cmd_mock)

    weclone = add("weclone", help="透传任意 weclone-cli 子命令")
    weclone.add_argument("args", nargs=argparse.REMAINDER)
    weclone.set_defaults(func=cmd_weclone)

    version = add("version", help="显示版本")
    version.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # 双击打包后的 exe / app 时，直接进内置机器人（最常见的用法）
        if getattr(sys, "frozen", False):
            args = parser.parse_args(["bot"])
        else:
            _print_banner()
            parser.print_help()
            return 0

    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        failure(str(exc))
        if "weflow.jsonc" in str(exc):
            say(f"       {DEFAULT_CONFIG_HINT}")
        return 1
    except WeFlowError as exc:
        failure(str(exc))
        return 1
    except WeFlowCloneError as exc:
        failure(str(exc))
        return 1
    except KeyboardInterrupt:
        say()
        warning("已中断")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
