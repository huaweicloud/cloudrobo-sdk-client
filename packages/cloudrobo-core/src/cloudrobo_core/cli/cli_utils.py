import json
import os
import click
from cloudrobo_core.sdk import Config, HttpClient


def get_client(ctx, client_class):
    config_path = os.environ.get("CLOUDROBO_SERVICE_CONFIG")
    config = Config(config_path or None)
    http = HttpClient(config)
    return client_class(http)


def out(result, prefix_info=None):
    """输出 JSON 结果。

    若提供 prefix_info（dict），将其字段合并到 result 的顶层（payload 同级），
    避免 create-task 等命令在 JSON 前打印人类可读前缀导致解析失败。
    保留 payload 原有字段不变；prefix_info 字段（如 task_id/status）作为顶层快捷字段。
    """
    if prefix_info and isinstance(result, dict):
        output = dict(result)
        output.update(prefix_info)
        click.echo(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
