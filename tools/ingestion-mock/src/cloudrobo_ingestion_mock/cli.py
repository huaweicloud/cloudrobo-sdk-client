"""Internal foreground test entry: python -m cloudrobo_ingestion_mock."""

import json
import logging
import math
from dataclasses import replace
from pathlib import Path

import click
from cloudrobo_ingestion.progress import Reporter

from .runtime import MockRunner
from .scenarios import SCENARIOS


def positive_finite(ctx, param, value):
    if not math.isfinite(value) or value <= 0:
        raise click.BadParameter("必须为有限正数", ctx=ctx, param=param)
    return value


def connection_config(path=None, endpoints=(), project_id=None, device_id=None, client_id=None):
    from cloudrobo_r2c.common.config import ClientConfig, ZenohConnectConfig

    if path:
        config = ClientConfig.from_yaml(str(path), None, None, None)
    else:
        config = ClientConfig(
            project_id="r2c",
            device_id="local",
            client_id="cloudrobo-ingestion-mock",
            mode="client",
            endpoint_role="connect",
            endpoints=("tcp/127.0.0.1:7447",),
        )
    identities = {}
    for field, override, default in (
        ("project_id", project_id, "r2c"),
        ("device_id", device_id, "local"),
        ("client_id", client_id, "cloudrobo-ingestion-mock"),
    ):
        configured = getattr(config, field)
        value = override if override is not None else default if configured is None else configured
        if not isinstance(value, str) or not value or any(c in value for c in "/*?#$"):
            raise ValueError(f"{field} 必须是单段身份字符串")
        identities[field] = value
    config = replace(config, **identities)
    addresses = tuple(endpoints) if endpoints else tuple(config.resolved_endpoints())
    if not addresses:
        addresses = ("tcp/127.0.0.1:7447",)
    connect = config.connect
    if config.connect is None:
        connect = ZenohConnectConfig(endpoints=addresses, timeout_ms=2000, exit_on_failure=True)
    elif endpoints:
        connect = replace(config.connect, endpoints=addresses)
    config = replace(config, endpoints=addresses, connect=connect)
    if config.endpoint_role != "connect":
        raise ValueError("开发测试入口只支持 endpoint_role=connect")
    config.validate()
    return config


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--scenario", type=click.Choice(SCENARIOS), default="overview", show_default=True)
@click.option(
    "--file-count",
    type=click.IntRange(1, 5000),
    default=100,
    show_default=True,
    help="仅 stress 场景的节点数。",
)
@click.option(
    "--time-scale",
    type=float,
    callback=positive_finite,
    default=1.0,
    show_default=True,
    help="正常上传阶段时间倍率；关键状态至少三周期，压力及协议异常不加速。",
)
@click.option(
    "--repeat",
    type=click.IntRange(min=0),
    default=1,
    show_default=True,
    help="执行轮数，0 表示持续循环。",
)
@click.option(
    "--zenoh-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="可选的 R2C 格式连接 YAML（不是 ingestion.yaml）。",
)
@click.option("--endpoint", "endpoints", multiple=True, help="整体替换连接地址，可重复。")
@click.option("--project-id", default=None)
@click.option("--device-id", default=None)
@click.option("--client-id", default=None)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="另存执行事件为 JSONL；默认仅终端输出。",
)
def main(
    scenario,
    file_count,
    time_scale,
    repeat,
    zenoh_config,
    endpoints,
    project_id,
    device_id,
    client_id,
    output,
):
    """内部 Mock 自测，前台运行，不访问 OBS；Ctrl+C 停止。

    正常场景、压力及协议异常均需通过 --scenario 显式选择；只确认本地发布调用，
    云端展示及异常处理结果需人工验证。
    """
    stream = None
    try:
        config = connection_config(zenoh_config, endpoints, project_id, device_id, client_id)
        topic = f"{config.project_id}/{config.device_id}/data_ingestion/upload_status"
        logging.basicConfig(level=logging.WARNING)
        # Reuse the Reporter connection/retry/error handling with an already validated ClientConfig.
        reporter = Reporter(
            None,
            topic,
            factory=lambda _: Reporter.connect_transport(config),
            endpoints=config.resolved_endpoints(),
        )
        if output:
            stream = output.open("w", encoding="utf-8")

        def emit(event):
            # Redirected Windows stdout may use a legacy code page. JSON
            # escapes preserve every Unicode value without changing the
            # terminal encoding; the optional file remains readable UTF-8.
            click.echo(json.dumps(event, ensure_ascii=True, allow_nan=False))
            if stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()

        emit(
            dict(
                event="connection",
                scenario=scenario,
                source=f"{config.project_id}/{config.device_id}",
                topic=topic,
                endpoints=list(config.resolved_endpoints()),
            )
        )
        MockRunner(
            reporter,
            f"{config.project_id}/{config.device_id}",
            scenario=scenario,
            file_count=file_count,
            time_scale=time_scale,
            repeat=repeat,
            emit=emit,
        ).run()
    except KeyboardInterrupt:
        raise click.exceptions.Exit(130) from None
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if stream:
            stream.close()
