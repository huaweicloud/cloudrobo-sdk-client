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


class MarkedOption(click.Option):
    """An Option whose --help prefixes a required/optional marker.

    click appends `[required]` after the help description by default; this class
    moves the marker to the front (`[required] 描述` / `[not required] 描述`) and
    keeps click's built-in required validation untouched.
    """

    def get_help_extra(self, ctx):
        extra = super().get_help_extra(ctx)
        extra.pop("required", None)
        return extra

    def get_help_record(self, ctx):
        record = super().get_help_record(ctx)
        if record is None:
            return None
        opts, help_text = record
        marker = "[required]" if self.required else "[not required]"
        if help_text.startswith("\x08\n"):
            help_text = "\x08\n" + marker + " " + help_text[2:]
        elif help_text:
            help_text = marker + " " + help_text
        else:
            help_text = marker
        return opts, help_text
