#!/usr/bin/env python
import os

import click
import yaml

from cloudrobo_core.cli.config_utils import USER_CONFIG_PATH

_SENSITIVE_KEYS = {"ak", "sk"}


def _mask(value: str) -> str:
    if len(value) > 8:
        return value[:4] + "****" + value[-4:]
    return "****"


@click.group()
def config():
    """配置管理命令"""
    pass


@config.command("set")
@click.argument("pairs", nargs=-1, required=True)
def set_config(pairs):
    """设置配置项（支持一次设置多个）

    用法:
      cloudrobo config set ak xxx sk yyy
      cloudrobo config set ak xxx sk yyy region cn-southwest-2

    支持的 key:
      ak, sk, region

    ak/sk 会自动加密存储（机器绑定），region 明文存储。
    """
    if len(pairs) % 2 != 0:
        click.echo("参数必须是 key-value 对，例如: config set ak xxx sk yyy", err=True)
        return

    config_path = USER_CONFIG_PATH

    data = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    if "cloudrobo" not in data:
        data["cloudrobo"] = {}
    if "auth" not in data["cloudrobo"]:
        data["cloudrobo"]["auth"] = {}

    key_map = {
        "ak": ("cloudrobo", "auth", "ak"),
        "sk": ("cloudrobo", "auth", "sk"),
        "region": ("cloudrobo", "region"),
    }

    for i in range(0, len(pairs), 2):
        key, value = pairs[i], pairs[i + 1]

        if key not in key_map:
            click.echo(f"不支持的配置项: {key}", err=True)
            click.echo(f"支持的配置项: {', '.join(key_map.keys())}", err=True)
            return

        if key in _SENSITIVE_KEYS:
            from cloudrobo_core.sdk.crypto import encrypt
            enc_value = encrypt(value)
            data["cloudrobo"]["auth"][f"{key}_enc"] = enc_value
            data["cloudrobo"]["auth"].pop(key, None)
            click.echo(f"已设置 {key} (加密存储)")
        else:
            path = key_map[key]
            obj = data
            for p in path[:-1]:
                if p not in obj:
                    obj[p] = {}
                obj = obj[p]
            obj[path[-1]] = value
            click.echo(f"已设置 {key} = {value}")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass


@config.command("get")
@click.argument("key")
def get_config(key):
    """获取配置项

    支持的 key:
      ak, sk, region

    ak/sk 解密后脱敏显示（仅显示前4后4位）。
    """
    config_path = USER_CONFIG_PATH

    if not config_path.exists():
        click.echo(f"配置文件不存在: {config_path}", err=True)
        return

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cloudrobo = data.get("cloudrobo", {})
    auth = cloudrobo.get("auth", {})

    if key in _SENSITIVE_KEYS:
        enc_field = f"{key}_enc"
        enc_value = auth.get(enc_field)
        if enc_value:
            from cloudrobo_core.sdk.crypto import decrypt
            try:
                value = decrypt(enc_value)
                click.echo(_mask(value))
            except Exception:
                click.echo("(解密失败)", err=True)
        else:
            plain = auth.get(key, "")
            if plain:
                click.echo(f"{_mask(plain)} (明文存储)")
            else:
                click.echo(f"{key} 未设置")
        return

    if key == "region":
        value = cloudrobo.get("region", "")
        click.echo(value if value else f"{key} 未设置")
        return

    click.echo(f"不支持的配置项: {key}", err=True)
    click.echo(f"支持的配置项: ak, sk, region", err=True)


@config.command("list")
def list_config():
    """列出所有配置"""
    config_path = USER_CONFIG_PATH

    if not config_path.exists():
        click.echo(f"配置文件不存在: {config_path}", err=True)
        return

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cloudrobo = data.get("cloudrobo", {})
    auth = cloudrobo.get("auth", {})

    click.echo(f"配置文件: {config_path}")
    click.echo()
    click.echo("认证配置:")

    for key in ("ak", "sk"):
        enc_field = f"{key}_enc"
        if auth.get(enc_field):
            click.echo(f"  {key}: 已加密存储 ✓")
        elif auth.get(key):
            click.echo(f"  {key}: 明文存储 ⚠ (建议重新配置以启用加密存储)")
        else:
            click.echo(f"  {key}: (未设置)")

    click.echo()
    click.echo("其他配置:")
    region = cloudrobo.get("region", "")
    click.echo(f"  region: {region if region else '(未设置)'}")
