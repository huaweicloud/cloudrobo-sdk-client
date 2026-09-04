import json

import click

from cloudrobo_core.cli.cli_utils import get_client, out
from .client import EvalClient


@click.group()
def eval():
    """模型评测命令组"""
    pass


@eval.command("create-job")
@click.option("--name", required=True)
@click.option("--virtual-world-id", required=True)
@click.option("--infer-server-id", required=True)
@click.option("--model-source", required=True, help="CLOUDROBO_SQUARE/WORKSPACE")
@click.option("--skill-description", default=None)
@click.option("--testing-round", type=int, default=None)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def create_job(ctx, name, virtual_world_id, infer_server_id, model_source, skill_description, testing_round, dry_run):
    """创建技能仿真评测任务"""
    if dry_run:
        click.echo(f"[DRY-RUN] create_eval_job(name={name})")
        return
    client = get_client(ctx, EvalClient)
    req = {"name": name, "virtual_world_id": virtual_world_id, "infer_server_id": infer_server_id, "model_source": model_source}
    if skill_description:
        req["skill_description"] = skill_description
    if testing_round:
        req["testing_round"] = testing_round
    result = client.create_eval_job(req)
    out(result)


@eval.command("list-jobs")
@click.option("--status", default=None)
@click.pass_context
def list_jobs(ctx, status):
    """查询评测任务列表"""
    client = get_client(ctx, EvalClient)
    params = {}
    if status:
        params["status"] = status
    result = client.list_eval_jobs(**params)
    out(result)


@eval.command("show-job")
@click.option("--job-id", required=True)
@click.pass_context
def show_job(ctx, job_id):
    """查询评测任务详情"""
    client = get_client(ctx, EvalClient)
    result = client.show_eval_job(job_id)
    out(result)


@eval.command("stop-job")
@click.option("--job-id", required=True)
@click.pass_context
def stop_job(ctx, job_id):
    """停止评测任务"""
    client = get_client(ctx, EvalClient)
    result = client.update_eval_job(job_id, {"action": "STOP"})
    out(result)


@eval.command("restart-job")
@click.option("--job-id", required=True)
@click.pass_context
def restart_job(ctx, job_id):
    """重启评测任务"""
    client = get_client(ctx, EvalClient)
    result = client.update_eval_job(job_id, {"action": "RESTART"})
    out(result)


@eval.command("delete-job")
@click.option("--job-id", required=True)
@click.pass_context
def delete_job(ctx, job_id):
    """删除评测任务"""
    client = get_client(ctx, EvalClient)
    result = client.delete_eval_job(job_id)
    out(result)


@eval.command("batch-delete-jobs")
@click.option("--job-ids", required=True, help="任务ID列表(逗号分隔)")
@click.pass_context
def batch_delete_jobs(ctx, job_ids):
    """批量删除评测任务"""
    client = get_client(ctx, EvalClient)
    ids = [x.strip() for x in job_ids.split(",")]
    result = client.batch_delete_eval_jobs(ids)
    out(result)


@eval.command("list-executions")
@click.option("--job-id", required=True)
@click.option("--status", default=None)
@click.pass_context
def list_executions(ctx, job_id, status):
    """查询执行记录列表"""
    client = get_client(ctx, EvalClient)
    params = {}
    if status:
        params["status"] = status
    result = client.list_executions(job_id, **params)
    out(result)


@eval.command("show-execution")
@click.option("--job-id", required=True)
@click.option("--execution-id", required=True)
@click.pass_context
def show_execution(ctx, job_id, execution_id):
    """查询执行记录详情"""
    client = get_client(ctx, EvalClient)
    result = client.show_execution(job_id, execution_id)
    out(result)


@eval.command("get-vnc-address")
@click.option("--job-id", required=True)
@click.option("--execution-id", required=True)
@click.pass_context
def get_vnc_address(ctx, job_id, execution_id):
    """获取仿真环境VNC登录链接"""
    client = get_client(ctx, EvalClient)
    result = client.get_vnc_address(job_id, execution_id)
    out(result)


@eval.command("show-stats")
@click.option("--workspace-id", default=None)
@click.pass_context
def show_stats(ctx, workspace_id):
    """作业状态统计"""
    client = get_client(ctx, EvalClient)
    result = client.show_eval_stats(workspace_id=workspace_id)
    out(result)


@eval.command("run-with-generalization")
@click.option("--config", "job_config", required=True, help="评测配置(JSON)")
@click.option("--generalization-types", required=True, help="泛化测试类型(逗号分隔)")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def run_with_generalization(ctx, job_config, generalization_types, dry_run):
    """带泛化性测试的评测"""
    if dry_run:
        click.echo(f"[DRY-RUN] run_with_generalization")
        return
    client = get_client(ctx, EvalClient)
    try:
        req = json.loads(job_config)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON: {e}")
    req["generalization_test_types"] = [t.strip() for t in generalization_types.split(",")]
    result = client.create_eval_job(req)
    out(result)
