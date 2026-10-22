"""CLI plugin; runtime imports are deferred until a command is invoked."""

import json

import click
import yaml

from .config import RUN_MODES
from .diagnostics import LABELS, STAGES, DiagnosticError, make_error


def show_error(info):
    click.echo(
        f"{LABELS[info['component']]} [{info['code']}]\n阶段：{STAGES.get(info['stage'], info['stage'])}\n原因：{info['message']}"
    )
    for key, value in info["context"].items():
        if value is not None:
            click.echo(f"{key}：{value}")
    for key in ("http_status", "error_code", "request_id", "errno", "winerror"):
        value = info["details"].get(key)
        if value not in (None, "", "None"):
            click.echo(f"{key}：{value}")
    click.echo(f"建议：{info['hint']}\n错误 ID：{info['error_id']}")


def command_error(exc, stage, context=None):
    info = (
        exc.info
        if isinstance(exc, DiagnosticError)
        else make_error("local", stage, exc, context=context)
    )
    target = "；".join(
        f"{key}={value}" for key, value in info["context"].items() if value is not None
    )
    return click.ClickException(
        f"{LABELS[info['component']]} [{info['code']}] 阶段：{STAGES.get(info['stage'], info['stage'])}；{info['message']}；{target}；建议：{info['hint']}；错误 ID：{info['error_id']}"
    )


@click.group()
def ingestion():
    """机器人本地数据回流到 OBS。"""


@ingestion.command("start")
@click.option(
    "--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option("--run-mode", type=click.Choice(RUN_MODES), default=None)
@click.option("--source-dir", type=click.Path(file_okay=False), default=None)
@click.option("--work-dir", type=click.Path(file_okay=False), default=None)
@click.option("--obs-path", default=None)
@click.option("--task-type", default=None)
@click.option("--collect-method", default=None)
@click.option("--max-files-per-batch", type=click.IntRange(min=1), default=None)
@click.option("--validate-lerobot/--no-validate-lerobot", "validation_enabled", default=None)
@click.option("--zenoh-mode", type=click.Choice(["client", "peer"]), default=None)
@click.option("--zenoh-endpoint", multiple=True)
@click.option("--zenoh-project-id", default=None)
@click.option("--zenoh-device-id", default=None)
@click.option("--zenoh-client-id", default=None)
def start(config_path, **overrides):
    """启动后台目录监控。"""
    from . import lifecycle

    try:
        result = lifecycle.start(config_path, overrides=overrides)
    except Exception as exc:
        stage = (
            "config"
            if isinstance(exc, (ValueError, yaml.YAMLError))
            else "initializing"
            if isinstance(exc, (OSError, RuntimeError))
            else "runtime"
        )
        raise command_error(exc, stage, dict(config=str(config_path))) from exc
    click.echo(
        f"回流进程已启动，模式={result['run_mode']}，PID={result['pid']}，工作目录={result['work_dir']}"
    )


@ingestion.command("status")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON 状态快照")
def status(as_json):
    """离线查询进程及最近的回流状态。"""
    from . import lifecycle

    result = lifecycle.status()
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False))
        return
    click.echo(
        f"进程: {'状态无法确定' if result.get('state') == 'unknown' else '运行中' if result['running'] else '已停止'}"
    )
    click.echo(f"运行模式: {result.get('run_mode', 'upload_and_report')}")
    click.echo(f"批次统计: {result.get('counts', {})}")
    click.echo(f"当前批次: {result.get('active_batch') or '-'}")
    click.echo(f"上传进度: {result.get('progress', {})}")
    scan = result.get("scan", {})
    if scan:
        click.echo(
            f"扫描状态: {scan.get('state', 'idle')}，已检查目录: {scan.get('checked_dirs', 0)}，扫描异常: {scan.get('error_count', 0)}"
        )
        click.echo(f"等待建立清单批次: {scan.get('pending_manifest_batches', 0)}")
    reporter = result.get("reporter", {})
    click.echo(
        f"上报状态: {'未启用' if not reporter.get('enabled', True) else '连接正常（仅本地调用）' if reporter.get('connected') else '未连接'}"
    )
    shown = set()
    for item in result.get("batch_errors", []):
        if item.get("error_info"):
            show_error(item["error_info"])
            shown.add(item["error_info"]["error_id"])
    for info in (
        result.get("scan_errors", []) + [result.get("error_info")] + reporter.get("errors", [])
    ):
        if info and info["error_id"] not in shown:
            show_error(info)
            shown.add(info["error_id"])
    if result.get("error") and not shown:
        click.echo(f"最近错误: {result['error']}")


@ingestion.command("stop")
def stop():
    """尽快结束当前上传并退出后台进程。"""
    from . import lifecycle

    try:
        lifecycle.stop()
    except Exception as exc:
        raise command_error(exc, "control") from exc
    click.echo("回流进程已停止")
