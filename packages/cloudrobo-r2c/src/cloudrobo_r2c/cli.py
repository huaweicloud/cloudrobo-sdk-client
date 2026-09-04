"""Click command group for the R2C data-plane SDK, registered via cloudrobo.groups."""

from __future__ import annotations

import click


@click.group()
def r2c() -> None:
    """R2C (Robot-to-Cloud) data-plane SDK commands."""
    pass


# 启动 R2C 客户端 cloudrobo r2c client 命令
@r2c.command("client", context_settings={"ignore_unknown_options": True})
@click.argument("argv", nargs=-1, type=click.UNPROCESSED)
def client(argv: tuple[str, ...]) -> None:
    """Run the R2C client (robot side, long-running).

    All extra arguments are forwarded to the underlying argparse entry point.
    Example: cloudrobo r2c client --client-config config/client_config.yaml --robot-config config/robot_dummy_config.yaml
    """
    from .cloudroboclient import main as client_main

    client_main(list(argv))
