import os
import subprocess
import sys
import tempfile

import click


@click.group()
def self():
    """CloudRobo 自身管理命令组"""
    pass


@self.command("uninstall")
@click.argument("packages", nargs=-1, required=False)
@click.option("--yes", "-y", is_flag=True, help="跳过确认提示")
@click.option("--all", "uninstall_all", is_flag=True, help="卸载所有 CloudRobo 包")
def uninstall(packages, yes, uninstall_all):
    """卸载 CloudRobo Client 安装包

    不指定包名时卸载所有包。支持子包简写（如 asset, dataset）或完整包名。

    示例:

      cloudrobo self uninstall                   # 卸载所有包

      cloudrobo self uninstall asset             # 卸载 asset 子包

      cloudrobo self uninstall hw-cloudrobo-asset  # 卸载 asset 子包（完整名）

      cloudrobo self uninstall asset dataset     # 卸载多个子包
    """
    # 获取已安装的包列表
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=columns"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        click.echo(f"获取安装包列表失败: {result.stderr.strip()}", err=True)
        sys.exit(1)

    # 扫描所有 cloudrobo 相关包（支持开发模式和发布模式）
    installed_packages = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and (
            parts[0].startswith("cloudrobo-")
            or parts[0].startswith("hw-cloudrobo-")
        ):
            installed_packages.append(parts[0])

    if not installed_packages:
        click.echo("未找到任何 CloudRobo 安装包。")
        return

    # 确定要卸载的包
    if uninstall_all or not packages:
        # 卸载所有包
        to_uninstall = installed_packages
    else:
        # 卸载指定包（支持简写和完整名）
        to_uninstall = []
        for pkg in packages:
            # 如果已经是完整包名且已安装
            if pkg in installed_packages:
                to_uninstall.append(pkg)
            else:
                # 尝试匹配简写（asset → hw-cloudrobo-asset 或 cloudrobo-asset）
                matched = [
                    p for p in installed_packages
                    if p.endswith(f"-{pkg}") or p == f"hw-cloudrobo-{pkg}" or p == f"cloudrobo-{pkg}"
                ]
                if matched:
                    to_uninstall.extend(matched)
                else:
                    click.echo(f"警告: 未找到包 '{pkg}'，跳过。")

    if not to_uninstall:
        click.echo("没有需要卸载的包。")
        return

    click.echo("即将卸载以下安装包：")
    for pkg in to_uninstall:
        click.echo(f"  - {pkg}")

    if not yes:
        if not click.confirm("确认卸载？"):
            click.echo("已取消。")
            return

    # 生成卸载脚本（避免自杀问题）
    script = "import subprocess, sys\n"
    script += f"pkgs = {to_uninstall!r}\n"
    script += "cmd = [sys.executable, '-m', 'pip', 'uninstall', '-y'] + pkgs\n"
    script += "r = subprocess.run(cmd)\n"
    script += "sys.exit(r.returncode)\n"

    script_path = os.path.join(tempfile.gettempdir(), "cloudrobo_uninstall.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

    click.echo("卸载脚本已生成，即将退出当前进程并执行卸载...")
    popen_kwargs = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, script_path], **popen_kwargs)
    sys.exit(0)
