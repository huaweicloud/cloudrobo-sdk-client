from __future__ import annotations

import logging
import os
import sys
import traceback
from importlib.metadata import version as get_pkg_version

import click
import requests

from cloudrobo_core.plugins import PluginGroup
from cloudrobo_core.cli.config_utils import ensure_user_config
from cloudrobo_core.cli.self_cmd import self
from cloudrobo_core.cli.config_cmd import config
from cloudrobo_core.cli.skill_cmd import skill
from cloudrobo_core.sdk.exceptions import (
    AuthenticationError,
    CloudRoboError,
    RateLimitError,
    ResourceConflictError,
    ResourceNotFoundError,
    BadParameterError,
    ServiceError,
)


logger = logging.getLogger("cloudrobo")


def _parse_bool(val):
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        val_lower = val.lower()
        if val_lower in ("1", "true", "yes"):
            return True
        if val_lower in ("0", "false", "no"):
            return False
    return None


def _is_verbose() -> bool:
    """Check if verbose mode is enabled via CLI flag, env var, or config."""
    if "--verbose" in sys.argv or "-v" in sys.argv:
        return True
    env_val = os.environ.get("CLOUDROBO_VERBOSE")
    if env_val is not None:
        parsed = _parse_bool(env_val)
        if parsed is not None:
            return parsed
    try:
        from cloudrobo_core.cli.config_utils import USER_CONFIG_PATH
        import yaml
        if USER_CONFIG_PATH.exists():
            with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
                user_data = yaml.safe_load(f) or {}
            parsed = _parse_bool(user_data.get("debug", {}).get("verbose"))
            if parsed is not None:
                return parsed
    except Exception as e:
        logger.debug("Failed to read verbose config from %s: %s", USER_CONFIG_PATH, e)
    return False


def _check_credentials() -> tuple[bool, str]:
    """Check if AK/SK are configured. Returns (is_valid, error_message)."""
    from cloudrobo_core.sdk import Config
    cfg = Config()
    ak = cfg.ak
    sk = cfg.sk
    ak_failed = cfg.ak_decrypt_failed
    sk_failed = cfg.sk_decrypt_failed

    # 检查解密失败的情况
    if ak_failed and sk_failed:
        return False, "AK 和 SK 解密失败。请运行 'cloudrobo config set ak <your-ak> sk <your-sk>' 重新配置（会自动加密存储）"
    if ak_failed:
        return False, "AK 解密失败。请运行 'cloudrobo config set ak <your-ak>' 重新配置（会自动加密存储）"
    if sk_failed:
        return False, "SK 解密失败。请运行 'cloudrobo config set sk <your-sk>' 重新配置（会自动加密存储）"

    # 检查未配置的情况
    if not ak and not sk:
        return False, "AK 和 SK 均未配置。请运行 'cloudrobo config set ak <your-ak> sk <your-sk>' 配置（会自动加密存储）"
    if not ak:
        return False, "AK 未配置。请运行 'cloudrobo config set ak <your-ak>' 配置（会自动加密存储）"
    if not sk:
        return False, "SK 未配置。请运行 'cloudrobo config set sk <your-sk>' 配置（会自动加密存储）"
    return True, ""


def _handle_error(e: Exception) -> int:
    """Handle exception and return exit code. Prints error message to stderr."""
    verbose = _is_verbose()

    if isinstance(e, (BadParameterError)):
        click.echo(f"参数错误: {'; '.join(e.args[0])}", err=True)
        if verbose:
            traceback.print_exc()
        return 1

    if isinstance(e, AuthenticationError):
        # Check if credentials are missing or decrypt failed
        is_valid, msg = _check_credentials()
        if not is_valid:
            click.echo(f"错误: {msg}", err=True)
        else:
            click.echo("错误: 认证失败，请检查 AK/SK 是否正确", err=True)
            click.echo("提示: 运行 'cloudrobo config set ak <your-ak> sk <your-sk>' 重新配置", err=True)
            if verbose:
                click.echo(f"详情: {e}", err=True)
        if verbose:
            traceback.print_exc()
        return 1

    if isinstance(e, ResourceNotFoundError):
        click.echo(f"错误: 资源不存在 - {e}", err=True)
        if verbose:
            traceback.print_exc()
        return 1

    if isinstance(e, ResourceConflictError):
        click.echo(f"错误: 资源冲突 - {e}", err=True)
        if verbose:
            traceback.print_exc()
        return 1

    if isinstance(e, RateLimitError):
        click.echo(f"错误: 请求过于频繁，请稍后重试", err=True)
        if verbose:
            click.echo(f"详情: {e}", err=True)
            traceback.print_exc()
        return 1

    if isinstance(e, ServiceError):
        click.echo(f"错误: 服务异常 - {e}", err=True)
        if verbose:
            traceback.print_exc()
        return 1

    if isinstance(e, CloudRoboError):
        click.echo(f"错误: {e}", err=True)
        if verbose:
            traceback.print_exc()
        return 1

    if isinstance(e, requests.ConnectionError):
        click.echo("错误: 网络连接失败，请检查网络或代理设置", err=True)
        click.echo("提示: 可设置环境变量 CLOUDROBO_HTTP_PROXY / CLOUDROBO_HTTPS_PROXY 配置代理", err=True)
        click.echo("提示: 如遇 SSL 证书问题，可设置 CLOUDROBO_VERIFY_SSL=false（不推荐）或 CLOUDROBO_CA_BUNDLE=/path/to/ca.pem", err=True)
        click.echo("提示: 设置 CLOUDROBO_LOG_TRAFFIC=true 可记录 HTTP 请求详情", err=True)
        if verbose:
            click.echo(f"详情: {e}", err=True)
            traceback.print_exc()
        return 1

    if isinstance(e, requests.Timeout):
        click.echo("错误: 请求超时，请检查网络或稍后重试", err=True)
        if verbose:
            click.echo(f"详情: {e}", err=True)
            traceback.print_exc()
        return 1

    # Unexpected error
    click.echo(f"错误: 发生未知错误 - {e}", err=True)
    if verbose:
        traceback.print_exc()
    else:
        click.echo("提示: 使用 --verbose 或 -v 查看详细错误信息", err=True)
        click.echo("提示: 设置 CLOUDROBO_LOG_TRAFFIC=true 可记录 HTTP 请求详情", err=True)
    return 1


def _get_version():
    """从已安装包元数据动态获取版本号"""
    try:
        return get_pkg_version("hw-cloudrobo-client")
    except Exception:
        try:
            return get_pkg_version("hw-cloudrobo-core")
        except Exception:
            return "unknown"


@click.group(cls=PluginGroup)
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
@click.version_option(version=_get_version(), prog_name="cloudrobo")
@click.pass_context
def cloudrobo(ctx, verbose):
    """CloudRobo CLI - CloudRobo命令行工具"""
    is_verbose = verbose or _is_verbose()
    log_level = logging.DEBUG if is_verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    ctx.ensure_object(dict)


cloudrobo.add_command(self)
cloudrobo.add_command(config)
cloudrobo.add_command(skill)


def main():
    ensure_user_config()
    try:
        cloudrobo(obj={})
    except (CloudRoboError, requests.ConnectionError, requests.Timeout) as e:
        sys.exit(_handle_error(e))
    except KeyboardInterrupt:
        click.echo("\n操作已取消", err=True)
        sys.exit(130)
    except Exception as e:
        sys.exit(_handle_error(e))


if __name__ == "__main__":
    main()
