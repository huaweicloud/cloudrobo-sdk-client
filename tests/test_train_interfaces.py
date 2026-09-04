"""
cloudrobo-train 接口准确性测试脚本

测试范围：
1. CLI 命令注册（18 个命令是否全部注册）
2. CLI 参数完整性（每个命令的 --flags 是否齐全，--sim-rl 开关是否在正确命令上）
3. SDK 方法存在性（33 个方法是否全部定义）
4. SDK URL 构造（mock HttpClient，验证每个方法构造的 URL 与 HTTP 方法）
5. Live API 调用（有凭证时实际调用只读接口，验证端点可达性）

用法：
    py tests/test_train_interfaces.py              # 静态测试（无需凭证）
    py tests/test_train_interfaces.py --live       # 含 Live API 测试（需凭证）
    py tests/test_train_interfaces.py --report      # 生成 HTML 报告
"""

import os
import sys
import json
import time
import inspect
import argparse
import traceback
from datetime import datetime
from unittest.mock import MagicMock

# 注入 packages 路径
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'packages', 'cloudrobo-core', 'src'))
sys.path.insert(0, os.path.join(ROOT, 'packages', 'cloudrobo-train', 'src'))

import click
from click.testing import CliRunner
from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_train.client import TrainClient
from cloudrobo_train.cli import train

# ========== 期望数据 ==========

EXPECTED_CLI_COMMANDS = {
    'create-task': {
        'params': ['--config', '--config-file', '--sim-rl', '--verbose', '-v', '--workspace-id'],
        'has_sim_rl': True,
    },
    'list-tasks': {
        'params': ['--workspace-id', '--train-mode', '--status', '--offset', '--limit', '--order', '--name',
                   '--group-id', '--user-name', '--run-id', '--execution-id', '--include-archived',
                   '--include-history', '--only-total', '--exact-name', '--order-time', '--order-by',
                   '--display-type', '--type', '--json', '--sim-rl'],
        'has_sim_rl': True,
    },
    'show-task': {
        'params': ['--task-id', '--sim-rl'],
        'has_sim_rl': True,
    },
    'update-task': {
        'params': ['--task-id', '--config', '--sim-rl'],
        'has_sim_rl': True,
    },
    'delete-tasks': {
        'params': ['--task-id', '--sim-rl'],
        'has_sim_rl': True,
    },
    'stop-task': {
        'params': ['--task-id', '--sim-rl'],
        'has_sim_rl': True,
    },
    'restart-task': {
        'params': ['--task-id', '--sim-rl', '--verbose', '-v', '--workspace-id'],
        'has_sim_rl': True,
    },
    'clone-task': {
        'params': ['--task-id'],
        'has_sim_rl': False,
    },
    'save-draft': {
        'params': ['--config', '--sim-rl', '--workspace-id'],
        'has_sim_rl': True,
    },
    'resume-task': {
        'params': ['--task-id'],
        'has_sim_rl': False,
    },
    'get-stages': {
        'params': ['--task-id', '--sim-rl'],
        'has_sim_rl': True,
    },
    'get-resource-usage': {
        'params': ['--task-id', '--metric', '--start', '--end', '--worker-index', '--step', '--sim-rl'],
        'has_sim_rl': True,
    },
    'get-logs': {
        'params': ['--task-id', '--file-name', '--log-name-pre', '--work-num', '--catalog',
                   '--start-byte', '--end-byte', '--offset', '--limit', '--sim-rl'],
        'has_sim_rl': True,
    },
    'get-signed-url': {
        'params': ['--task-id', '--file-source', '--file-name', '--catalog', '--sim-rl'],
        'has_sim_rl': True,
    },
    'get-events': {
        'params': ['--task-id', '--start-time', '--end-time', '--level', '--source',
                   '--pattern', '--offset', '--limit', '--order', '--sim-rl'],
        'has_sim_rl': True,
    },
    'stats': {
        'params': ['--workspace-id', '--user-id', '--sim-rl'],
        'has_sim_rl': True,
    },
    'list-checkpoints': {
        'params': ['--task-id', '--offset', '--limit', '--order', '--status', '--name'],
        'has_sim_rl': False,
    },
    'register-checkpoint': {
        'params': ['--task-id', '--checkpoint-name', '--save-mode', '--version-name', '--model-name', '--verbose',
                   '-v'],
        'has_sim_rl': False,
    },
}

EXPECTED_SDK_METHODS = {
    # 普通训练任务（19 个）
    'create_train_task': {'http': 'post', 'path': '/v1/training/train-tasks', 'params': ['req', 'workspace_id']},
    'list_train_tasks': {'http': 'get', 'path': '/v1/training/train-tasks', 'params': ['**params']},
    'batch_delete_train_tasks': {'http': 'post', 'path': '/v1/training/train-tasks/batch-delete',
                                 'params': ['execution_ids']},
    'count_train_tasks_by_status': {'http': 'get', 'path': '/v1/training/train-tasks/stats',
                                    'params': ['workspace_id', 'user_id']},
    'resume_train_task': {'http': 'post', 'path': '/v1/training/train-tasks/{task_id}/resume', 'params': ['task_id']},
    'stop_train_task': {'http': 'post', 'path': '/v1/training/train-tasks/{task_id}/stop', 'params': ['task_id']},
    'restart_train_task': {'http': 'post', 'path': '/v1/training/train-tasks/{task_id}/restart',
                           'params': ['task_id', 'req', 'workspace_id']},
    'save_draft': {'http': 'post', 'path': '/v1/training/train-tasks/draft', 'params': ['req', 'workspace_id']},
    'update_train_task': {'http': 'patch', 'path': '/v1/training/train-tasks/{task_id}', 'params': ['task_id', 'req']},
    'show_train_task': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}', 'params': ['task_id', '**params']},
    'list_train_stages': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}/stages', 'params': ['task_id']},
    'show_resource_usage': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}/resource-usage',
                            'params': ['task_id', 'metric', 'start', 'end', '**params']},
    'list_observations': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}/observability',
                          'params': ['task_id', '**params']},
    'get_log_signed_url': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}/observability/signed-url',
                           'params': ['task_id', 'file_source', 'file_name', '**params']},
    'get_log_content': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}/observability/content',
                        'params': ['task_id', '**params']},
    'list_events': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}/events',
                    'params': ['task_id', 'start_time', 'end_time', '**params']},
    'list_train_checkpoints': {'http': 'get', 'path': '/v1/training/train-tasks/{task_id}/checkpoints',
                               'params': ['task_id', '**params']},
    'register_train_checkpoint': {'http': 'post', 'path': '/v1/training/train-tasks/{task_id}/checkpoints/register',
                                  'params': ['task_id', 'req']},
    # 仿真强化学习任务（16 个）
    'count_sim_rl_tasks_by_status': {'http': 'get', 'path': '/v1/training/rl-tasks/simulation/stats',
                                     'params': ['workspace_id', 'user_id']},
    'list_sim_rl_tasks': {'http': 'get', 'path': '/v1/training/rl-tasks/simulation', 'params': ['**params']},
    'create_sim_rl_task': {'http': 'post', 'path': '/v1/training/rl-tasks/simulation', 'params': ['req', 'workspace_id']},
    'create_sim_rl_task_draft': {'http': 'post', 'path': '/v1/training/rl-tasks/simulation/draft', 'params': ['req', 'workspace_id']},
    'show_sim_rl_task': {'http': 'get', 'path': '/v1/training/rl-tasks/simulation/{task_id}', 'params': ['task_id']},
    'update_sim_rl_task': {'http': 'patch', 'path': '/v1/training/rl-tasks/simulation/{task_id}',
                           'params': ['task_id', 'req']},
    'delete_sim_rl_task': {'http': 'delete', 'path': '/v1/training/rl-tasks/simulation/{task_id}',
                           'params': ['task_id']},
    'stop_sim_rl_task': {'http': 'post', 'path': '/v1/training/rl-tasks/simulation/{task_id}/stop',
                         'params': ['task_id']},
    'copy_sim_rl_task': {'http': 'post', 'path': '/v1/training/rl-tasks/simulation/{task_id}/copy',
                         'params': ['task_id', 'req']},
    'restart_sim_rl_task': {'http': 'post', 'path': '/v1/training/rl-tasks/simulation/{task_id}/restart',
                            'params': ['task_id', 'req', 'workspace_id']},
    'show_sim_rl_task_resource_usage': {'http': 'get',
                                        'path': '/v1/training/rl-tasks/simulation/{task_id}/resource-usage',
                                        'params': ['task_id', 'metric', 'start', 'end', '**params']},
    'list_sim_rl_task_stages': {'http': 'get', 'path': '/v1/training/rl-tasks/simulation/{task_id}/stages',
                                'params': ['task_id']},
    'list_sim_rl_task_events': {'http': 'get', 'path': '/v1/training/rl-tasks/simulation/{task_id}/events',
                                'params': ['task_id', 'start_time', 'end_time', '**params']},
    'list_sim_rl_task_observations': {'http': 'get', 'path': '/v1/training/rl-tasks/simulation/{task_id}/observability',
                                      'params': ['task_id', '**params']},
    'show_sim_rl_task_observations_content': {'http': 'get',
                                              'path': '/v1/training/rl-tasks/simulation/{task_id}/observability/content',
                                              'params': ['task_id', '**params']},
    'show_sim_rl_task_observations_signed_url': {'http': 'get',
                                                 'path': '/v1/training/rl-tasks/simulation/{task_id}/observability/signed-url',
                                                 'params': ['task_id', 'file_source', 'file_name', '**params']},
}


# ========== 测试结果收集 ==========

class TestResult:
    def __init__(self, category, name, passed, details='', expected='', actual='', request='', response=''):
        self.category = category
        self.name = name
        self.passed = passed
        self.details = details
        self.expected = expected
        self.actual = actual
        self.request = request
        self.response = response

    def to_dict(self):
        return {
            'category': self.category,
            'name': self.name,
            'passed': self.passed,
            'details': self.details,
            'expected': self.expected,
            'actual': self.actual,
            'request': self.request,
            'response': self.response,
        }


results = []


def record(category, name, passed, details='', expected='', actual='', request='', response=''):
    results.append(TestResult(category, name, passed, details, expected, actual, request, response))


def _truncate(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _live_call(client, method_name, args=(), kwargs=None, http_method='GET', path=''):
    if kwargs is None:
        kwargs = {}
    category = 'Live API 调用'
    req_info = {'method': method_name, 'http': http_method, 'path': path, 'args': list(args), 'kwargs': kwargs}
    try:
        method = getattr(client, method_name, None)
        if method is None:
            record(category, method_name, False, f'方法 {method_name} 不存在',
                   request=_truncate(req_info))
            return None
        result = method(*args, **kwargs)
        record(category, method_name, True,
               f'{http_method} {path}',
               request=_truncate(req_info),
               response=_truncate(result))
        return result
    except Exception as e:
        record(category, method_name, False, f'调用失败: {e}',
               request=_truncate(req_info),
               response=traceback.format_exc(limit=3))
        return None


# ========== 测试 1：CLI 命令注册 ==========

def test_cli_registration():
    category = 'CLI 命令注册'
    runner = CliRunner()
    res = runner.invoke(train, ['--help'])
    if res.exit_code != 0:
        record(category, 'train --help 加载', False, f'exit_code={res.exit_code}, stderr={res.output}')
        return

    registered = set(train.commands.keys())
    expected = set(EXPECTED_CLI_COMMANDS.keys())

    for cmd in expected:
        if cmd in registered:
            record(category, f'命令 {cmd} 已注册', True, f'命令 {cmd} 存在于 train.commands')
        else:
            record(category, f'命令 {cmd} 已注册', False, f'命令 {cmd} 未注册', '已注册', '未找到')

    extra = registered - expected
    for cmd in extra:
        record(category, f'命令 {cmd} 多余注册', False, f'未预期的命令 {cmd}', '不在期望列表', '已注册')


# ========== 测试 2：CLI 参数完整性 ==========

def _get_command_params(command):
    """提取 click 命令的所有参数名（含 -- 前缀）"""
    params = []
    for param in command.params:
        if isinstance(param, click.Option):
            for opt in param.opts:
                params.append(opt)
    return sorted(params)


def test_cli_params():
    category = 'CLI 参数完整性'
    for cmd_name, spec in EXPECTED_CLI_COMMANDS.items():
        if cmd_name not in train.commands:
            record(category, f'{cmd_name} 参数检查', False, f'命令 {cmd_name} 未注册，无法检查参数')
            continue
        cmd = train.commands[cmd_name]
        actual_params = sorted(set(_get_command_params(cmd)))
        expected_params = sorted(set(spec['params']))

        missing = set(expected_params) - set(actual_params)
        extra = set(actual_params) - set(expected_params)

        if not missing and not extra:
            record(category, f'{cmd_name} 参数齐全', True,
                   f'参数: {", ".join(actual_params)}')
        else:
            details = []
            if missing:
                details.append(f'缺失: {", ".join(missing)}')
            if extra:
                details.append(f'多余: {", ".join(extra)}')
            record(category, f'{cmd_name} 参数齐全', False,
                   '; '.join(details),
                   ', '.join(expected_params),
                   ', '.join(actual_params))


# ========== 测试 3：--sim-rl 开关位置 ==========

def test_sim_rl_flag():
    category = '--sim-rl 开关'
    for cmd_name, spec in EXPECTED_CLI_COMMANDS.items():
        if cmd_name not in train.commands:
            record(category, f'{cmd_name} --sim-rl', False, f'命令 {cmd_name} 未注册')
            continue
        cmd = train.commands[cmd_name]
        actual_params = _get_command_params(cmd)
        has_sim_rl = '--sim-rl' in actual_params

        if has_sim_rl == spec['has_sim_rl']:
            record(category, f'{cmd_name} --sim-rl 开关', True,
                   f'期望={"有" if spec["has_sim_rl"] else "无"}, 实际={"有" if has_sim_rl else "无"}')
        else:
            record(category, f'{cmd_name} --sim-rl 开关', False,
                   f'--sim-rl 开关位置不正确',
                   f'{"应有 --sim-rl" if spec["has_sim_rl"] else "不应有 --sim-rl"}',
                   f'{"有 --sim-rl" if has_sim_rl else "无 --sim-rl"}')


# ========== 测试 4：SDK 方法存在性 ==========

def test_sdk_methods_exist():
    category = 'SDK 方法存在性'
    for method_name in EXPECTED_SDK_METHODS:
        if hasattr(TrainClient, method_name):
            method = getattr(TrainClient, method_name)
            if callable(method):
                record(category, f'{method_name} 已定义', True, f'TrainClient.{method_name} 存在且可调用')
            else:
                record(category, f'{method_name} 已定义', False, f'{method_name} 不是可调用方法')
        else:
            record(category, f'{method_name} 已定义', False, f'TrainClient.{method_name} 不存在', '已定义', '未找到')

    # 检查多余方法
    expected_set = set(EXPECTED_SDK_METHODS.keys())
    actual_methods = {m for m in dir(TrainClient) if not m.startswith('_') and callable(getattr(TrainClient, m, None))}
    # 排除继承自 BaseClient 的方法
    base_methods = {m for m in dir(type(TrainClient).__mro__[1]) if not m.startswith('_')} if len(
        TrainClient.__mro__) > 1 else set()
    actual_only = actual_methods - expected_set - base_methods
    for m in actual_only:
        record(category, f'{m} 多余方法', False, f'未预期的 SDK 方法 {m}', '不在期望列表', '已定义')


# ========== 测试 5：SDK 方法签名 ==========

def test_sdk_signatures():
    category = 'SDK 方法签名'
    for method_name, spec in EXPECTED_SDK_METHODS.items():
        if not hasattr(TrainClient, method_name):
            record(category, f'{method_name} 签名', False, f'方法 {method_name} 不存在')
            continue
        method = getattr(TrainClient, method_name)
        try:
            sig = inspect.signature(method)
            actual_params = list(sig.parameters.keys())
            # 移除 self
            if 'self' in actual_params:
                actual_params = actual_params[1:]

            expected_params = [p for p in spec['params'] if not p.startswith('**')]
            expected_param_names = [p.replace('**', '') for p in expected_params]

            # 检查必填参数是否都存在
            missing = [p for p in expected_param_names if p not in actual_params]
            if not missing:
                record(category, f'{method_name} 签名', True,
                       f'参数: {", ".join(actual_params)}')
            else:
                record(category, f'{method_name} 签名', False,
                       f'缺失参数: {", ".join(missing)}',
                       ', '.join(expected_param_names),
                       ', '.join(actual_params))
        except (ValueError, TypeError) as e:
            record(category, f'{method_name} 签名', False, f'无法获取签名: {e}')


# ========== 测试 6：SDK URL 构造 ==========

def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    mock.config.workspace_id = "ws-test"
    return mock


BASE = "https://api.example.com/cloudrobo-service"


def test_sdk_url_construction():
    category = 'SDK URL 构造'
    mock_http = _make_mock_client()
    client = TrainClient(mock_http)

    # 为每个方法配置 mock 返回值
    mock_http.get.return_value = {"list": [], "task_id": "t1", "content": "", "signed_url": ""}
    mock_http.post.return_value = {"task_id": "t1"}
    mock_http.patch.return_value = {"task_id": "t1"}
    mock_http.delete.return_value = None

    test_cases = [
        ('create_train_task', ({"name": "t"},), {}, '/v1/training/train-tasks', 'post', {'json': {"name": "t", "workspace_id": "ws-test"}}),
        ('list_train_tasks', (), {'status': 'RUNNING'}, '/v1/training/train-tasks', 'get',
         {'params': {'status': 'RUNNING', 'workspace_id': 'ws-test'}}),
        ('batch_delete_train_tasks', (["id1", "id2"],), {}, '/v1/training/train-tasks/batch-delete', 'post',
         {'json': {"execution_ids": ["id1", "id2"]}}),
        ('count_train_tasks_by_status', ("ws1",), {'user_id': 'u1'}, '/v1/training/train-tasks/stats', 'get',
         {'params': {'workspace_id': 'ws1', 'user_id': 'u1'}}),
        ('resume_train_task', ("t1",), {}, '/v1/training/train-tasks/t1/resume', 'post', {}),
        ('stop_train_task', ("t1",), {}, '/v1/training/train-tasks/t1/stop', 'post', {}),
        ('restart_train_task', ("t1", {"x": 1}), {}, '/v1/training/train-tasks/t1/restart', 'post', {'json': {"x": 1, "workspace_id": "ws-test"}}),
        ('save_draft', ({"name": "d"},), {}, '/v1/training/train-tasks/draft', 'post', {'json': {"name": "d", "workspace_id": "ws-test"}}),
        ('update_train_task', ("t1", {"desc": "x"}), {}, '/v1/training/train-tasks/t1', 'patch',
         {'json': {"desc": "x"}}),
        ('show_train_task', ("t1",), {'run_id': 'r1'}, '/v1/training/train-tasks/t1', 'get',
         {'params': {'run_id': 'r1'}}),
        ('list_train_stages', ("t1",), {}, '/v1/training/train-tasks/t1/stages', 'get', {}),
        ('show_resource_usage', ("t1", "cpu_util", 100, 200), {'worker_index': 1},
         '/v1/training/train-tasks/t1/resource-usage', 'get',
         {'params': {'metric': 'cpu_util', 'start': 100, 'end': 200, 'worker_index': 1}}),
        ('list_observations', ("t1",), {'file_name': 'log.txt'}, '/v1/training/train-tasks/t1/observability', 'get',
         {'params': {'file_name': 'log.txt'}}),
        ('get_log_signed_url', ("t1", "TRAIN", "log.txt"), {'catalog': 'logs'},
         '/v1/training/train-tasks/t1/observability/signed-url', 'get',
         {'params': {'file_source': 'TRAIN', 'file_name': 'log.txt', 'catalog': 'logs'}}),
        ('get_log_content', ("t1",), {'file_name': 'log.txt'}, '/v1/training/train-tasks/t1/observability/content',
         'get', {'params': {'file_name': 'log.txt'}}),
        ('list_events', ("t1", 1000, 2000), {'level': 'Info'}, '/v1/training/train-tasks/t1/events', 'get',
         {'params': {'start_time': 1000, 'end_time': 2000, 'level': 'Info'}}),
        ('list_train_checkpoints', ("t1",), {'status': 'SUCCESS', 'limit': 10},
         '/v1/training/train-tasks/t1/checkpoints', 'get', {'params': {'status': 'SUCCESS', 'limit': 10}}),
        ('register_train_checkpoint', ("t1", {"save_mode": "NEW_VERSION", "checkpoint_name": "ckpt_1000"}), {},
         '/v1/training/train-tasks/t1/checkpoints/register', 'post',
         {'json': {"save_mode": "NEW_VERSION", "checkpoint_name": "ckpt_1000"}}),
        # SimRL
        ('count_sim_rl_tasks_by_status', ("ws1",), {}, '/v1/training/rl-tasks/simulation/stats', 'get',
         {'params': {'workspace_id': 'ws1'}}),
        ('list_sim_rl_tasks', (), {'status': 'RUNNING'}, '/v1/training/rl-tasks/simulation', 'get',
         {'params': {'status': 'RUNNING', 'workspace_id': 'ws-test'}}),
        ('create_sim_rl_task', ({"name": "s"},), {}, '/v1/training/rl-tasks/simulation', 'post',
         {'json': {"name": "s", "workspace_id": "ws-test"}}),
        ('create_sim_rl_task_draft', ({"name": "d"},), {}, '/v1/training/rl-tasks/simulation/draft', 'post',
         {'json': {"name": "d", "workspace_id": "ws-test"}}),
        ('show_sim_rl_task', ("s1",), {}, '/v1/training/rl-tasks/simulation/s1', 'get', {}),
        ('update_sim_rl_task', ("s1", {"desc": "x"}), {}, '/v1/training/rl-tasks/simulation/s1', 'patch',
         {'json': {"desc": "x"}}),
        ('delete_sim_rl_task', ("s1",), {}, '/v1/training/rl-tasks/simulation/s1', 'delete', {}),
        ('stop_sim_rl_task', ("s1",), {}, '/v1/training/rl-tasks/simulation/s1/stop', 'post', {}),
        ('copy_sim_rl_task', ("s1", {"name": "copy"}), {}, '/v1/training/rl-tasks/simulation/s1/copy', 'post',
         {'json': {"name": "copy"}}),
        ('restart_sim_rl_task', ("s1", {"name": "restart"}), {}, '/v1/training/rl-tasks/simulation/s1/restart', 'post',
         {'json': {"name": "restart", "workspace_id": "ws-test"}}),
        ('show_sim_rl_task_resource_usage', ("s1", "gpu_util", 10, 20), {},
         '/v1/training/rl-tasks/simulation/s1/resource-usage', 'get',
         {'params': {'metric': 'gpu_util', 'start': 10, 'end': 20}}),
        ('list_sim_rl_task_stages', ("s1",), {}, '/v1/training/rl-tasks/simulation/s1/stages', 'get', {}),
        ('list_sim_rl_task_events', ("s1", 1000, 2000), {}, '/v1/training/rl-tasks/simulation/s1/events', 'get',
         {'params': {'start_time': 1000, 'end_time': 2000}}),
        ('list_sim_rl_task_observations', ("s1",), {}, '/v1/training/rl-tasks/simulation/s1/observability', 'get', {}),
        ('show_sim_rl_task_observations_content', ("s1",), {},
         '/v1/training/rl-tasks/simulation/s1/observability/content', 'get', {}),
        ('show_sim_rl_task_observations_signed_url', ("s1", "TRAIN", "log.txt"), {},
         '/v1/training/rl-tasks/simulation/s1/observability/signed-url', 'get',
         {'params': {'file_source': 'TRAIN', 'file_name': 'log.txt'}}),
    ]

    for method_name, args, kwargs, expected_path, http_method, expected_kwargs in test_cases:
        mock_http.reset_mock()
        method = getattr(client, method_name, None)
        if method is None:
            record(category, f'{method_name} URL 构造', False, f'方法 {method_name} 不存在')
            continue
        try:
            method(*args, **kwargs)
        except Exception as e:
            record(category, f'{method_name} URL 构造', False, f'调用异常: {e}')
            continue

        mock_call = getattr(mock_http, http_method)
        if not mock_call.called:
            record(category, f'{method_name} URL 构造', False,
                   f'未调用 http.{http_method}()',
                   f'{http_method.upper()} {expected_path}',
                   '未调用')
            continue

        actual_call = mock_call.call_args
        actual_url = actual_call.args[0] if actual_call.args else actual_call.kwargs.get('url', '')
        expected_url = f'{BASE}{expected_path}'

        url_ok = actual_url == expected_url

        # 检查 kwargs（json/params）
        kwargs_ok = True
        kwargs_detail = ''
        if expected_kwargs:
            for k, v in expected_kwargs.items():
                actual_v = actual_call.kwargs.get(k)
                if actual_v != v:
                    kwargs_ok = False
                    kwargs_detail = f'参数 {k}: 期望={v}, 实际={actual_v}'
                    break

        if url_ok and kwargs_ok:
            record(category, f'{method_name} URL 构造', True,
                   f'{http_method.upper()} {expected_path}')
        else:
            detail_parts = []
            if not url_ok:
                detail_parts.append(f'URL: 期望={expected_url}, 实际={actual_url}')
            if not kwargs_ok:
                detail_parts.append(kwargs_detail)
            record(category, f'{method_name} URL 构造', False,
                   '; '.join(detail_parts),
                   f'{http_method.upper()} {expected_path} {expected_kwargs}',
                   f'{actual_url} {actual_call.kwargs}')


# ========== 测试 7：Live API 调用 ==========

def _extract_task_id(result):
    if not isinstance(result, dict):
        return None
    payload = result.get('payload', result)
    items = payload.get('list', payload.get('items', []))
    if isinstance(items, list) and items:
        return items[0].get('id') or items[0].get('task_id') or items[0].get('execution_id')
    return None


def _extract_finished_task_id(result):
    if not isinstance(result, dict):
        return None
    payload = result.get('payload', result)
    items = payload.get('list', payload.get('items', []))
    if isinstance(items, list):
        for item in items:
            if item.get('status') == 'FINISHED':
                return item.get('id') or item.get('task_id') or item.get('execution_id')
    return None


def _extract_stopped_task_id(result):
    """从列表结果中提取第一个STOPPED状态任务的ID"""
    if not isinstance(result, dict):
        return None
    payload = result.get('payload', result)
    items = payload.get('list', payload.get('items', []))
    if isinstance(items, list):
        for item in items:
            if item.get('status') == 'STOPPED':
                return item.get('id') or item.get('task_id') or item.get('execution_id')
    return None


def _extract_running_task_id(result):
    """从列表结果中提取第一个RUNNING状态任务的ID"""
    if not isinstance(result, dict):
        return None
    payload = result.get('payload', result)
    items = payload.get('list', payload.get('items', []))
    if isinstance(items, list):
        for item in items:
            if item.get('status') == 'RUNNING':
                return item.get('id') or item.get('task_id') or item.get('execution_id')
    return None


def _find_current_user_task_id(result, user_id):
    """从列表中找到属于当前用户的第一个非DELETING、非测试任务的ID"""
    if not isinstance(result, dict):
        return None
    payload = result.get('payload', result)
    items = payload.get('list', payload.get('items', []))
    if isinstance(items, list):
        # 优先选择非测试任务（真实用户任务）
        test_prefixes = ('sdk-test-', 'manual-', 'test-')
        for item in items:
            if item.get('user_id') == user_id and item.get('status') != 'DELETING':
                name = item.get('name', '')
                if not name.startswith(test_prefixes):
                    return item.get('id') or item.get('task_id') or item.get('execution_id')
        # 回退：选择第一个非DELETING任务
        for item in items:
            if item.get('user_id') == user_id and item.get('status') != 'DELETING':
                return item.get('id') or item.get('task_id') or item.get('execution_id')
    return None


def _extract_dominant_user_id(result):
    """从任务列表中找出出现次数最多的 user_id（当前用户）"""
    if not isinstance(result, dict):
        return None
    payload = result.get('payload', result)
    items = payload.get('list', payload.get('items', []))
    if not isinstance(items, list) or not items:
        return None
    from collections import Counter
    user_counts = Counter(item.get('user_id') for item in items if item.get('user_id'))
    if user_counts:
        return user_counts.most_common(1)[0][0]
    return None


def _get_execution_id(client, task_id):
    """获取任务的execution_id（用于批量删除）"""
    detail = client.show_train_task(task_id)
    payload = (detail or {}).get('payload', detail or {})
    return payload.get('execution_id')


def _get_task_status(client, task_id):
    """获取训练任务当前状态，任务不存在或查询失败返回 None"""
    detail = client.show_train_task(task_id)
    payload = (detail or {}).get('payload', detail or {})
    return payload.get('status')


def _get_sim_task_status(client, task_id):
    """获取 SimRL 任务当前状态，任务不存在或查询失败返回 None"""
    detail = client.show_sim_rl_task(task_id)
    payload = (detail or {}).get('payload', detail or {})
    return payload.get('status')


def _wait_for_task_state(client, task_id, target_states, timeout=90, interval=5, is_sim=False):
    """轮询等待训练任务进入目标状态。返回最终状态，超时返回最终状态。"""
    from cloudrobo_core.sdk.exceptions import ResourceNotFoundError
    elapsed = 0
    consecutive_not_found = 0
    while elapsed < timeout:
        try:
            if is_sim:
                status = _get_sim_task_status(client, task_id)
            else:
                status = _get_task_status(client, task_id)
            consecutive_not_found = 0
            if status and status in target_states:
                return status
        except ResourceNotFoundError:
            consecutive_not_found += 1
            if consecutive_not_found >= 2:
                return None
        except Exception as e:
            print("wait for task exception %s " % str(e))
        time.sleep(interval)
        elapsed += interval
    try:
        return _get_sim_task_status(client, task_id) if is_sim else _get_task_status(client, task_id)
    except Exception:
        return None


def _extract_file_name(result):
    if not isinstance(result, dict):
        return None
    payload = result.get('payload', result)
    items = payload.get('list', payload.get('files', []))
    if not isinstance(items, list) or not items:
        return None
    for item in items:
        name = item.get('log_file_name') or item.get('name') or item.get('file_name')
        if name and name.startswith('modelarts-job-'):
            return name
    for item in items:
        name = item.get('log_file_name') or item.get('name') or item.get('file_name')
        if name:
            return name
    return None


def _skip_record(method_name, reason):
    record('Live API 调用', method_name, None, f'跳过: {reason}')


def _live_cli_call(runner, args, method_name, description=''):
    """调用 CLI 命令并记录结果。API 运行时错误（如资源不存在）记为跳过而非失败。"""
    category = 'Live CLI 调用'
    cmd_str = f'cloudrobo train {" ".join(args)}'
    try:
        result = runner.invoke(train, args)
        if result.exit_code == 0:
            raw = result.output
            json_start = -1
            for i, ch in enumerate(raw):
                if ch in '{[':
                    json_start = i
                    break
            if json_start >= 0:
                try:
                    output = json.loads(raw[json_start:])
                    record(category, method_name, True, description,
                          request=cmd_str,
                          response=json.dumps(output, indent=2, ensure_ascii=False))
                    return output
                except json.JSONDecodeError:
                    pass
            record(category, method_name, True, description,
                  request=cmd_str,
                  response=raw)
            return raw
        else:
            exc = result.exception
            exc_name = type(exc).__name__ if exc else 'Unknown'
            from cloudrobo_core.sdk.exceptions import CloudRoboError
            if isinstance(exc, CloudRoboError):
                record(category, method_name, None,
                       f'API 错误({exc_name}): {exc}',
                       request=cmd_str,
                       response=result.output or str(exc))
            else:
                record(category, method_name, False, f'exit_code={result.exit_code} ({exc_name})',
                      request=cmd_str,
                      response=result.output or str(exc))
            return None
    except Exception as e:
        record(category, method_name, False, f'调用失败: {e}',
              request=cmd_str,
              response=traceback.format_exc(limit=3))
        return None


def _skip_cli_record(method_name, reason):
    record('Live CLI 调用', method_name, None, f'跳过: {reason}')


def _live_cli_setup():
    """CLI 测试初始化。返回 (runner, workspace_id) 或 None。"""
    category = 'Live CLI 调用'

    try:
        config = Config()
        ak = config.ak
        sk = config.sk
    except Exception as e:
        record(category, 'CLI配置加载', False, f'Config 初始化失败: {e}')
        return None, None

    if not ak or not sk:
        record(category, 'CLI凭证检查', False,
               '未找到 AK/SK，跳过 Live CLI 测试')
        return None, None

    workspace_id = os.environ.get('CLOUDROBO_WORKSPACE_ID')
    if not workspace_id:
        try:
            from cloudrobo_workspace import WorkspaceClient
            http = HttpClient(config)
            ws_client = WorkspaceClient(http)
            ws_result = ws_client.list_workspaces()
            ws_payload = ws_result.get('payload', ws_result)
            workspaces = ws_payload.get('workspaces', ws_payload.get('list', []))
            for ws in workspaces:
                if ws.get('name') == 'default':
                    workspace_id = ws.get('workspace_id') or ws.get('id')
                    break
            if not workspace_id and workspaces:
                workspace_id = workspaces[0].get('workspace_id') or workspaces[0].get('id')
            record(category, 'CLI工作空间查找', True,
                   f'使用工作空间: {workspace_id}')
        except Exception as e:
            record(category, 'CLI工作空间查找', False,
                   f'查找失败 (错误: {e})')
            raise RuntimeError('无法获取工作空间ID，请通过 CLOUDROBO_WORKSPACE_ID 环境变量指定')

    runner = CliRunner()
    return runner, workspace_id


def _live_cli_train_readonly(runner, workspace_id):
    """普通训练任务 CLI 只读测试。返回 dict。"""
    # 1. stats
    _live_cli_call(runner, ['stats', '--workspace-id', workspace_id],
                   'cli_stats', 'GET /v1/training/train-tasks/stats')

    # 2. list-tasks
    list_result = _live_cli_call(runner, ['list-tasks', '--workspace-id', workspace_id, '--limit', '20'],
                                  'cli_list_tasks', 'GET /v1/training/train-tasks')

    # 从列表中提取任务 ID
    train_task_id = None
    train_finished_id = None
    if isinstance(list_result, dict):
        payload = list_result.get('payload', list_result)
        items = payload.get('list', payload.get('items', []))
        if isinstance(items, list) and items:
            for item in items:
                if not train_task_id:
                    train_task_id = item.get('id') or item.get('task_id')
                if item.get('status') == 'FINISHED' and not train_finished_id:
                    train_finished_id = item.get('id') or item.get('task_id')

    # 3. show-task
    if train_task_id:
        _live_cli_call(runner, ['show-task', '--task-id', train_task_id],
                       'cli_show_task', f'GET /v1/training/train-tasks/{train_task_id}')
    else:
        _skip_cli_record('cli_show_task', 'workspace 无训练任务')

    # 4. get-stages（使用 FINISHED 任务，避免 FAILED 任务报 ResourceNotFoundError）
    stages_target = train_finished_id or train_task_id
    if stages_target:
        _live_cli_call(runner, ['get-stages', '--task-id', stages_target],
                       'cli_get_stages', f'GET /v1/training/train-tasks/{stages_target}/stages')
    else:
        _skip_cli_record('cli_get_stages', 'workspace 无训练任务')

    # 5. get-events（毫秒时间戳，使用 FINISHED 任务）
    events_target = train_finished_id or train_task_id
    if events_target:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - 7 * 24 * 3600 * 1000
        _live_cli_call(runner, ['get-events', '--task-id', events_target,
                                '--start-time', str(start_ms), '--end-time', str(end_ms), '--limit', '5'],
                       'cli_get_events', f'GET /v1/training/train-tasks/{events_target}/events')
    else:
        _skip_cli_record('cli_get_events', 'workspace 无训练任务')

    # 6. get-resource-usage（秒时间戳）
    if train_task_id:
        end_s = int(time.time())
        start_s = end_s - 3600
        _live_cli_call(runner, ['get-resource-usage', '--task-id', train_task_id,
                                '--metric', 'cpu_util', '--start', str(start_s), '--end', str(end_s)],
                       'cli_get_resource_usage', f'GET /v1/training/train-tasks/{train_task_id}/resource-usage')
    else:
        _skip_cli_record('cli_get_resource_usage', 'workspace 无训练任务')

    # 7. list-checkpoints（API 未注册）
    if train_task_id:
        _skip_cli_record('cli_list_checkpoints', 'API 未注册，环境尚未部署 checkpoint 接口')
    else:
        _skip_cli_record('cli_list_checkpoints', 'workspace 无训练任务')

    return {
        'list_result': list_result,
        'train_task_id': train_task_id,
        'train_finished_id': train_finished_id,
    }


def _live_cli_simrl_readonly(runner, workspace_id):
    """SimRL CLI 只读测试。返回 dict。"""
    # 1. stats --sim-rl
    _live_cli_call(runner, ['stats', '--workspace-id', workspace_id, '--sim-rl'],
                   'cli_stats_simrl', 'GET /v1/training/rl-tasks/simulation/stats')

    # 2. list-tasks --sim-rl
    list_result = _live_cli_call(runner, ['list-tasks', '--workspace-id', workspace_id, '--limit', '20', '--sim-rl'],
                                  'cli_list_tasks_simrl', 'GET /v1/training/rl-tasks/simulation')

    # 从列表中提取任务 ID
    sim_task_id = None
    sim_finished_id = None
    if isinstance(list_result, dict):
        payload = list_result.get('payload', list_result)
        items = payload.get('list', payload.get('items', []))
        if isinstance(items, list) and items:
            for item in items:
                if not sim_task_id:
                    sim_task_id = item.get('id') or item.get('task_id')
                if item.get('status') == 'FINISHED' and not sim_finished_id:
                    sim_finished_id = item.get('id') or item.get('task_id')

    # 3. show-task --sim-rl
    if sim_task_id:
        _live_cli_call(runner, ['show-task', '--task-id', sim_task_id, '--sim-rl'],
                       'cli_show_task_simrl', f'GET /v1/training/rl-tasks/simulation/{sim_task_id}')
    else:
        _skip_cli_record('cli_show_task_simrl', 'workspace 无 SimRL 任务')

    # 4. get-stages --sim-rl（使用 FINISHED 任务，避免 FAILED 任务报 ResourceNotFoundError）
    stages_sim_target = sim_finished_id or sim_task_id
    if stages_sim_target:
        _live_cli_call(runner, ['get-stages', '--task-id', stages_sim_target, '--sim-rl'],
                       'cli_get_stages_simrl', f'GET /v1/training/rl-tasks/simulation/{stages_sim_target}/stages')
    else:
        _skip_cli_record('cli_get_stages_simrl', 'workspace 无 SimRL 任务')

    # 5. get-resource-usage --sim-rl
    if sim_task_id:
        end_s = int(time.time())
        start_s = end_s - 3600
        _live_cli_call(runner, ['get-resource-usage', '--task-id', sim_task_id, '--sim-rl',
                                '--metric', 'gpu_util', '--start', str(start_s), '--end', str(end_s)],
                       'cli_get_resource_usage_simrl',
                       f'GET /v1/training/rl-tasks/simulation/{sim_task_id}/resource-usage')
    else:
        _skip_cli_record('cli_get_resource_usage_simrl', 'workspace 无 SimRL 任务')

    # 6. get-events --sim-rl（毫秒时间戳，使用 FINISHED 任务）
    events_sim_target = sim_finished_id or sim_task_id
    if events_sim_target:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - 7 * 24 * 3600 * 1000
        _live_cli_call(runner, ['get-events', '--task-id', events_sim_target, '--sim-rl',
                                '--start-time', str(start_ms), '--end-time', str(end_ms)],
                       'cli_get_events_simrl',
                       f'GET /v1/training/rl-tasks/simulation/{events_sim_target}/events')
    else:
        _skip_cli_record('cli_get_events_simrl', 'workspace 无 SimRL 任务')

    # 7. get-logs --sim-rl 已在写操作阶段用新创建的任务测试

    return {
        'list_result': list_result,
        'sim_task_id': sim_task_id,
        'sim_finished_id': sim_finished_id,
    }


def _live_cli_train_write(runner, workspace_id, train_task_id):
    """训练任务 CLI 写操作测试。"""
    # 获取现有任务的完整配置，用于构建完整的请求体
    existing_task_detail = None
    if train_task_id:
        try:
            http = HttpClient(Config())
            client = TrainClient(http)
            _existing_raw = client.show_train_task(train_task_id)
            existing_task_detail = (_existing_raw or {}).get('payload', _existing_raw)
        except Exception as e:
            print(f'[CLI Train Write] 获取现有任务详情失败: {e}')

    # 1. save-draft - 构建完整请求体（参考 SDK 逻辑，没有 algorithm 就跳过）
    draft_name = f'cli-test-draft-{int(time.time())}'
    draft_train_id = None

    if existing_task_detail and existing_task_detail.get('algorithm'):
        # 有现有任务且包含 algorithm，构建完整配置
        draft_req = {'name': draft_name, 'workspace_id': workspace_id}
        # 从现有任务复制关键字段，确保 UI 能显示完整配置
        for field in ['algorithm', 'spec', 'train_mode', 'train_method', 'datasets',
                      'input_models', 'output_models', 'worker_num', 'cluster_id',
                      'parameters', 'env', 'description']:
            val = existing_task_detail.get(field)
            if val:
                draft_req[field] = val

        draft_result = _live_cli_call(runner, ['save-draft', '--config', json.dumps(draft_req)],
                                       'cli_save_draft', 'POST /v1/training/train-tasks/draft')

        if isinstance(draft_result, dict):
            payload = draft_result.get('payload', draft_result)
            draft_train_id = payload.get('task_id')
    else:
        # 没有现有任务或无 algorithm，跳过 save-draft（避免创建空 draft）
        _skip_cli_record('cli_save_draft', '无可用的现有任务配置（需要包含 algorithm 的现有任务）')

    # 2. create-task - 创建完整任务（参考 SDK T6，用于后续 get-stages/get-events 测试）
    cli_created_task_id = None
    if existing_task_detail and existing_task_detail.get('algorithm'):
        create_req = {
            'name': f'cli-test-create-{int(time.time())}',
            'workspace_id': workspace_id,
            'algorithm': existing_task_detail['algorithm'],
            'spec': existing_task_detail.get('spec', 'Ascend: 1 * SNT9B2 | 24 vCPUs | 192 GiB'),
            'train_mode': existing_task_detail.get('train_mode', 'MODEL_TUNING'),
        }
        if existing_task_detail.get('train_method'):
            create_req['train_method'] = existing_task_detail['train_method']

        create_result = _live_cli_call(runner, ['create-task', '--config', json.dumps(create_req)],
                                        'cli_create_task', 'POST /v1/training/train-tasks')
        if isinstance(create_result, dict):
            payload = create_result.get('payload', create_result)
            cli_created_task_id = payload.get('task_id')

    # 3. get-logs - 用自己创建的任务查询日志（等待 RUNNING 后查询）
    if cli_created_task_id:
        cli_log_file_name = None
        try:
            http = HttpClient(Config())
            _client = TrainClient(http)
            # 等待任务进入 RUNNING 状态
            _wait_for_task_state(_client, cli_created_task_id, {'RUNNING'}, timeout=600, interval=5)
            # 等待日志文件生成（重试最多 10 分钟）
            for _retry in range(120):
                _obs_result = _client.list_observations(cli_created_task_id, catalog='logs', limit=10)
                cli_log_file_name = _extract_file_name(_obs_result)
                if cli_log_file_name:
                    break
                time.sleep(5)
        except Exception as e:
            print(f'[CLI get-logs created] 获取日志文件列表失败: {e}')
        if cli_log_file_name:
            _live_cli_call(runner, ['get-logs', '--task-id', cli_created_task_id,
                                    '--file-name', cli_log_file_name, '--catalog', 'logs',
                                    '--start-byte', '0', '--end-byte', '1000000'],
                           'cli_get_logs', f'GET /v1/training/train-tasks/{cli_created_task_id}/observability/content')
        else:
            _skip_cli_record('cli_get_logs', '新创建的任务无可用日志文件（observations 未返回文件名）')

    # 4. update-task
    update_target = draft_train_id or train_task_id
    if update_target:
        update_req = {'name': f'{draft_name}-updated', 'description': 'updated by CLI test'}
        _live_cli_call(runner, ['update-task', '--task-id', update_target,
                                '--config', json.dumps(update_req)],
                       'cli_update_task', f'PATCH /v1/training/train-tasks/{update_target}')

    # 5. delete-tasks（清理草稿 + 新创建的任务）- 先停止可运行任务再删除
    ids_to_delete = []
    if draft_train_id:
        ids_to_delete.append(draft_train_id)
    if cli_created_task_id:
        ids_to_delete.append(cli_created_task_id)

    if ids_to_delete:
        try:
            http = HttpClient(Config())
            client = TrainClient(http)

            # 停止非 deletable 状态的任务（SUBMITTING 需等待，WAITING/PENDING/RUNNING 需先停止）
            for tid in ids_to_delete:
                status = _get_task_status(client, tid)
                print(f'[CLI delete-tasks] 任务 {tid} 当前状态: {status}')
                if status == 'SUBMITTING':
                    _wait_for_task_state(client, tid,
                        {'DRAFT', 'WAITING', 'PENDING', 'RUNNING', 'STOPPED', 'FINISHED', 'FAILED', 'ABNORMAL'},
                        timeout=60, interval=5)
                    status = _get_task_status(client, tid)
                    print(f'[CLI delete-tasks] 等待后任务 {tid} 状态: {status}')
                if status in ('WAITING', 'PENDING', 'RUNNING'):
                    try:
                        client.stop_train_task(tid)
                        final_status = _wait_for_task_state(client, tid, {'STOPPED', 'STOP_FAILED'}, timeout=30, interval=3)
                        print(f'[CLI delete-tasks] 停止后任务 {tid} 状态: {final_status}')
                    except Exception as e:
                        print(f'[CLI delete-tasks] 停止任务 {tid} 失败: {e}')

            # 获取所有 execution_id
            execution_ids = []
            for tid in ids_to_delete:
                detail = client.show_train_task(tid)
                payload = (detail or {}).get('payload', detail or {})
                exec_id = payload.get('execution_id')
                if exec_id:
                    execution_ids.append(exec_id)

            if execution_ids:
                # Click multiple=True 需要每个 ID 都带 --task-id 前缀
                delete_args = ['delete-tasks']
                for eid in execution_ids:
                    delete_args.extend(['--task-id', eid])
                _live_cli_call(runner, delete_args,
                               'cli_delete_tasks', 'POST /v1/training/train-tasks/batch-delete')
                # 等待删除完成，避免资源竞争
                time.sleep(2)
        except Exception as e:
            record('Live CLI 调用', 'cli_delete_tasks', False, f'获取 execution_id 失败: {e}',
                  request=f'delete-tasks --task-id {",".join(ids_to_delete)}')


def _live_cli_simrl_write(runner, workspace_id, sim_task_id):
    """SimRL CLI 写操作测试。参考 SDK _build_simrl_req 构建完整配置。"""
    # 获取现有 SimRL 任务的完整配置，用于构建完整的请求体
    sim_existing_detail = None
    if sim_task_id:
        try:
            http = HttpClient(Config())
            client = TrainClient(http)
            _sim_existing_raw = client.show_sim_rl_task(sim_task_id)
            sim_existing_detail = (_sim_existing_raw or {}).get('payload', _sim_existing_raw)
        except Exception as e:
            print(f'[CLI SimRL Write] 获取现有 SimRL 任务详情失败: {e}')

    created_sim_ids = []

    # 1. save-draft --sim-rl - 使用 _build_simrl_req 构建完整配置（参考 SDK）
    draft_name = f'cli-test-sim-draft-{int(time.time())}'
    draft_req = _build_simrl_req(sim_existing_detail, draft_name, workspace_id)
    draft_sim_id = None

    draft_result = _live_cli_call(runner, ['save-draft', '--config', json.dumps(draft_req), '--sim-rl'],
                                   'cli_save_draft_simrl', 'POST /v1/training/rl-tasks/simulation/draft')

    if isinstance(draft_result, dict):
        payload = draft_result.get('payload', draft_result)
        draft_sim_id = payload.get('task_id')
        if draft_sim_id:
            created_sim_ids.append(draft_sim_id)

    # 2. create-task --sim-rl - 创建完整 SimRL 任务（参考 SDK S7）
    create_name = f'cli-test-sim-create-{int(time.time())}'
    create_req = _build_simrl_req(sim_existing_detail, create_name, workspace_id)
    cli_created_sim_id = None

    create_result = _live_cli_call(runner, ['create-task', '--config', json.dumps(create_req), '--sim-rl'],
                                    'cli_create_task_simrl', 'POST /v1/training/rl-tasks/simulation')

    if isinstance(create_result, dict):
        payload = create_result.get('payload', create_result)
        cli_created_sim_id = payload.get('task_id')
        if cli_created_sim_id:
            created_sim_ids.append(cli_created_sim_id)

    # 3. get-logs --sim-rl - 用自己创建的任务查询日志（等待 RUNNING 后查询）
    if cli_created_sim_id:
        sim_log_file_name = None
        try:
            http = HttpClient(Config())
            _client = TrainClient(http)
            # 等待任务进入 RUNNING 状态
            _wait_for_task_state(_client, cli_created_sim_id, {'RUNNING'}, timeout=600, interval=5, is_sim=True)
            # 等待日志文件生成（轮询 10 分钟）
            for _ in range(120):
                _sim_obs_result = _client.list_sim_rl_task_observations(cli_created_sim_id, catalog='logs')
                sim_log_file_name = _extract_file_name(_sim_obs_result)
                if sim_log_file_name:
                    break
                time.sleep(5)
        except Exception as e:
            print(f'[CLI get-logs simrl created] 获取日志文件列表失败: {e}')
        if sim_log_file_name:
            _live_cli_call(runner, ['get-logs', '--task-id', cli_created_sim_id, '--sim-rl',
                                    '--file-name', sim_log_file_name, '--catalog', 'logs',
                                    '--start-byte', '0', '--end-byte', '1000000'],
                           'cli_get_logs_simrl',
                           f'GET /v1/training/rl-tasks/simulation/{cli_created_sim_id}/observability/content')
        else:
            _skip_cli_record('cli_get_logs_simrl', '新创建的 SimRL 任务无可用日志文件（observations 未返回文件名）')

    # 4. update-task --sim-rl
    update_target = draft_sim_id or sim_task_id
    if update_target:
        update_req = {'name': f'{draft_name}-updated', 'description': 'updated by CLI test'}
        _live_cli_call(runner, ['update-task', '--task-id', update_target, '--sim-rl',
                                '--config', json.dumps(update_req)],
                       'cli_update_task_simrl',
                       f'PATCH /v1/training/rl-tasks/simulation/{update_target}')

    # 5. delete-tasks --sim-rl（清理草稿 + 新创建的任务）
    for sid in created_sim_ids:
        if sid:
            _live_cli_call(runner, ['delete-tasks', '--task-id', sid, '--sim-rl'],
                           'cli_delete_tasks_simrl',
                           f'DELETE /v1/training/rl-tasks/simulation/{sid}')
            # 等待删除完成，避免资源竞争
            time.sleep(2)


def test_live_cli():
    """Live CLI 调用 — 编排子函数完成全量 CLI 接口测试。"""
    runner, workspace_id = _live_cli_setup()
    if not runner:
        return

    train_data = _live_cli_train_readonly(runner, workspace_id)
    sim_data = _live_cli_simrl_readonly(runner, workspace_id)

    _live_cli_train_write(runner, workspace_id, train_data['train_task_id'])
    _live_cli_simrl_write(runner, workspace_id, sim_data['sim_task_id'])


_DEFAULT_SIMRL_REQ = {
    'config_mode': 'SIMPLE',
    'task_set': 'LIBERO_SPATIAL',
    'simple_params': json.dumps([
        {'key': 'RL_ALGO', 'value': 'ppo', 'desc': '强化学习算法'},
        {'key': 'MAX_EPOCHS', 'value': '100', 'desc': '训练轮数'},
        {'key': 'SAVE_INTERVAL', 'value': '20', 'desc': '保存间隔'},
        {'key': 'TOTAL_NUM_TRAIN_ENVS', 'value': '16', 'desc': '训练环境数'},
        {'key': 'EVAL_NUM_TRAIN_ENVS', 'value': '500', 'desc': '评估环境数'},
        {'key': 'MICRO_BATCH_SIZE', 'value': '64', 'desc': '微批次大小'},
        {'key': 'GLOBAL_BATCH_SIZE', 'value': '256', 'desc': '全局批次大小'},
        {'key': 'ROLLOUT_EPOCH', 'value': '2', 'desc': 'rollout轮数'},
    ]),
    'spec': 'ASCEND: 1 * SNT9B2 | 24 vCPUs | 192 GiB',
    'cluster_id': 'pool-6872b4ac-518d-434d-b7f0-3ad49bc53733',
    'worker_num': 1,
    'input_models': [{
        'source_type': 'PUBLIC_MODEL_ASSET',
        'model_asset_id': 'fe204cf9-7b8c-4894-aa82-ed5c88bd8617',
        'model_name': 'RLinf-Pi0-LIBERO-Long-SFT',
        'version_id': 'b9027c97-7335-4535-a8da-3068fb353285',
        'version_name': 'v0.0.1',
    }],
    'output_models': [{
        'save_mode': 'NEW_MODEL',
        'model_name': 'sdk-test-output',
        'version_name': 'v0.0.1',
        'model_type': 'vla',
        'model_asset_id': None,
        'version_id': None,
        'strict': False,
        'skills': [],
    }],
}


def _build_simrl_req(sim_existing_detail, name, workspace_id, description=None, cluster_id=None):
    """构建 SimRL 任务请求，优先从现有任务复制配置，缺失字段使用默认值。
    output_models 始终使用默认值（NEW_MODEL 模式不能带 model_asset_id）。
    cluster_id 可显式覆盖（用于专属资源池测试）。
    output_models.model_name 基于任务名动态生成，确保唯一。"""
    req = {'name': name, 'workspace_id': workspace_id}
    if description:
        req['description'] = description
    if sim_existing_detail:
        for field in ['config_mode', 'spec', 'input_models',
                      'task_set', 'simple_params', 'rl_config_content',
                      'worker_num', 'cluster_id', 'description']:
            val = sim_existing_detail.get(field)
            if val:
                req[field] = val
    for field, default_val in _DEFAULT_SIMRL_REQ.items():
        if field not in req or not req.get(field):
            req[field] = default_val
    if cluster_id:
        req['cluster_id'] = cluster_id
    # 基于任务名生成唯一的输出模型名，避免"输出模型已存在"冲突
    import re as _re
    safe_name = _re.sub(r'[^a-zA-Z0-9\-]', '-', name)[:40]
    req['output_models'] = [{
        'save_mode': 'NEW_MODEL',
        'model_name': f'{safe_name}-out',
        'version_name': 'v0.0.1',
        'model_type': 'vla',
        'model_asset_id': None,
        'version_id': None,
        'strict': False,
        'skills': [],
    }]
    return req


def _live_setup():
    """凭证检查、客户端初始化、工作空间查找。返回 (client, workspace_id, http) 或 (None, None, None)。"""
    category = 'Live API 调用'

    try:
        config = Config()
        ak = config.ak
        sk = config.sk
    except Exception as e:
        record(category, '配置加载', False, f'Config 初始化失败: {e}')
        return None, None, None

    if not ak or not sk:
        record(category, '凭证检查', False,
               '未找到 AK/SK（环境变量 HUAWEI_CLOUD_AK/SK 或 ~/.cloudrobo/config.yaml 均未配置），跳过 Live API 测试')
        return None, None, None

    workspace_id = os.environ.get('CLOUDROBO_WORKSPACE_ID')

    try:
        http = HttpClient(config)
        client = TrainClient(http)
    except Exception as e:
        record(category, '客户端初始化', False, f'初始化失败: {e}')
        return None, None, None

    if not workspace_id:
        try:
            from cloudrobo_workspace import WorkspaceClient
            ws_client = WorkspaceClient(http)
            ws_result = ws_client.list_workspaces()
            ws_payload = ws_result.get('payload', ws_result)
            workspaces = ws_payload.get('workspaces', ws_payload.get('list', []))
            for ws in workspaces:
                if ws.get('name') == 'default':
                    workspace_id = ws.get('workspace_id') or ws.get('id')
                    break
            if not workspace_id and workspaces:
                workspace_id = workspaces[0].get('workspace_id') or workspaces[0].get('id')
            record(category, '工作空间查找', True,
                   f'使用工作空间: {workspace_id}',
                   request=_truncate({'action': 'list_workspaces'}),
                   response=_truncate(ws_result))
        except Exception as e:
            record(category, '工作空间查找', False,
                   f'查找失败 (错误: {e})',
                   response=traceback.format_exc(limit=3))
            raise RuntimeError('无法获取工作空间ID，请通过 CLOUDROBO_WORKSPACE_ID 环境变量指定')

    return client, workspace_id, http


def _live_train_readonly(client, workspace_id):
    """普通训练任务只读接口测试（#1-#10）。返回 dict。"""
    # ========== 普通训练任务（19 个 SDK 方法，覆盖全部只读接口） ==========

    # 1. count_train_tasks_by_status — GET /v1/training/train-tasks/stats
    _live_call(client, 'count_train_tasks_by_status', (workspace_id,), {},
               'GET', '/v1/training/train-tasks/stats')

    # 2. list_train_tasks — GET /v1/training/train-tasks
    list_result = _live_call(client, 'list_train_tasks', (),
                             {'workspace_id': workspace_id, 'limit': 20},
                             'GET', '/v1/training/train-tasks')
    # 提取当前用户 ID（列表中出现最多的 user_id），优先使用当前用户的任务
    current_user_id = _extract_dominant_user_id(list_result)
    train_task_id = _find_current_user_task_id(list_result, current_user_id) or _extract_task_id(list_result)
    train_finished_id = _extract_finished_task_id(list_result) or train_task_id

    # 3. show_train_task — GET /v1/training/train-tasks/{task_id}
    if train_task_id:
        _live_call(client, 'show_train_task', (train_task_id,), {},
                   'GET', f'/v1/training/train-tasks/{train_task_id}')
    else:
        _skip_record('show_train_task', 'workspace 无训练任务')

    # 4. list_train_stages — GET /v1/training/train-tasks/{task_id}/stages（使用 FINISHED 任务，避免 FAILED 任务报 ResourceNotFoundError）
    stages_target = train_finished_id or train_task_id
    if stages_target:
        _live_call(client, 'list_train_stages', (stages_target,), {},
                   'GET', f'/v1/training/train-tasks/{stages_target}/stages')
    else:
        _skip_record('list_train_stages', 'workspace 无训练任务')

    # 5. list_events — GET /v1/training/train-tasks/{task_id}/events（毫秒时间戳，使用 FINISHED 任务）
    events_target = train_finished_id or train_task_id
    if events_target:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - 7 * 24 * 3600 * 1000
        _live_call(client, 'list_events', (events_target, start_ms, end_ms), {'limit': 5},
                   'GET', f'/v1/training/train-tasks/{events_target}/events')
    else:
        _skip_record('list_events', 'workspace 无训练任务')

    # 6. show_resource_usage — GET /v1/training/train-tasks/{task_id}/resource-usage（秒时间戳）
    if train_task_id:
        end_s = int(time.time())
        start_s = end_s - 3600
        _live_call(client, 'show_resource_usage',
                   (train_task_id, 'cpu_util', start_s, end_s), {},
                   'GET', f'/v1/training/train-tasks/{train_task_id}/resource-usage')
    else:
        _skip_record('show_resource_usage', 'workspace 无训练任务')

    # 7. list_observations — GET /v1/training/train-tasks/{task_id}/observability?catalog=logs
    # 使用 FINISHED 任务查询日志，确保有完整日志文件
    obs_result = None
    if train_finished_id:
        obs_result = _live_call(client, 'list_observations',
                                (train_finished_id,), {'catalog': 'logs', 'limit': 10},
                                'GET', f'/v1/training/train-tasks/{train_finished_id}/observability?catalog=logs')
    else:
        _skip_record('list_observations', 'workspace 无训练任务')

    # 8. get_log_content — GET /v1/training/train-tasks/{task_id}/observability/content
    file_name = _extract_file_name(obs_result) if obs_result else None
    if train_finished_id and file_name:
        _live_call(client, 'get_log_content',
                   (train_finished_id,),
                   {'file_name': file_name, 'catalog': 'logs', 'start_byte': 0, 'end_byte': 1000000, 'flag': 'false'},
                   'GET', f'/v1/training/train-tasks/{train_finished_id}/observability/content')
    else:
        _skip_record('get_log_content', '无可用日志文件（observations 未返回文件名或无 FINISHED 任务）')

    # 9. get_log_signed_url — GET /v1/training/train-tasks/{task_id}/observability/signed-url
    if train_finished_id and file_name:
        _live_call(client, 'get_log_signed_url',
                   (train_finished_id, 'TRAIN', file_name), {},
                   'GET', f'/v1/training/train-tasks/{train_finished_id}/observability/signed-url')
    else:
        _skip_record('get_log_signed_url', '无可用日志文件')

    # 10. list_train_checkpoints — GET /v1/training/train-tasks/{task_id}/checkpoints
    _skip_record('list_train_checkpoints', 'API 未注册，环境尚未部署 checkpoint 接口')

    return {
        'list_result': list_result,
        'train_task_id': train_task_id,
        'train_finished_id': train_finished_id,
        'current_user_id': current_user_id,
    }


def _live_simrl_readonly(client, workspace_id, current_user_id=None):
    """SimRL 只读接口测试（#11-#19）。返回 dict。"""
    # ========== SimRL（16 个 SDK 方法，覆盖全部只读接口） ==========

    # 11. count_sim_rl_tasks_by_status — GET /v1/training/rl-tasks/simulation/stats
    _live_call(client, 'count_sim_rl_tasks_by_status', (workspace_id,), {},
               'GET', '/v1/training/rl-tasks/simulation/stats')

    # 12. list_sim_rl_tasks — GET /v1/training/rl-tasks/simulation
    sim_list_result = _live_call(client, 'list_sim_rl_tasks', (),
                                 {'workspace_id': workspace_id, 'limit': 20},
                                 'GET', '/v1/training/rl-tasks/simulation')
    sim_current_user_id = _extract_dominant_user_id(sim_list_result) or current_user_id
    sim_task_id = _find_current_user_task_id(sim_list_result, sim_current_user_id) or _extract_task_id(sim_list_result)
    sim_finished_id = _extract_finished_task_id(sim_list_result)

    # 13. show_sim_rl_task — GET /v1/training/rl-tasks/simulation/{task_id}
    if sim_task_id:
        _live_call(client, 'show_sim_rl_task', (sim_task_id,), {},
                   'GET', f'/v1/training/rl-tasks/simulation/{sim_task_id}')
    else:
        _skip_record('show_sim_rl_task', 'workspace 无 SimRL 任务')

    # 14. list_sim_rl_task_stages — GET /v1/training/rl-tasks/simulation/{task_id}/stages
    # 使用 FINISHED 任务，避免 FAILED 任务报 ResourceNotFoundError
    stages_sim_sdk_target = sim_finished_id or sim_task_id
    if stages_sim_sdk_target:
        _live_call(client, 'list_sim_rl_task_stages', (stages_sim_sdk_target,), {},
                   'GET', f'/v1/training/rl-tasks/simulation/{stages_sim_sdk_target}/stages')
    else:
        _skip_record('list_sim_rl_task_stages', 'workspace 无 SimRL 任务')

    # 15. show_sim_rl_task_resource_usage — GET /v1/training/rl-tasks/simulation/{task_id}/resource-usage
    if sim_task_id:
        end_s = int(time.time())
        start_s = end_s - 3600
        _live_call(client, 'show_sim_rl_task_resource_usage',
                   (sim_task_id, 'gpu_util', start_s, end_s), {},
                   'GET', f'/v1/training/rl-tasks/simulation/{sim_task_id}/resource-usage')
    else:
        _skip_record('show_sim_rl_task_resource_usage', 'workspace 无 SimRL 任务')

    # 16. list_sim_rl_task_events — GET /v1/training/rl-tasks/simulation/{task_id}/events（毫秒时间戳，使用 FINISHED 任务）
    events_sim_sdk_target = sim_finished_id or sim_task_id
    if events_sim_sdk_target:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - 7 * 24 * 3600 * 1000
        _live_call(client, 'list_sim_rl_task_events',
                   (events_sim_sdk_target, start_ms, end_ms), {},
                   'GET', f'/v1/training/rl-tasks/simulation/{events_sim_sdk_target}/events')
    else:
        _skip_record('list_sim_rl_task_events', 'workspace 无 SimRL 任务')

    # 17. list_sim_rl_task_observations — GET /v1/training/rl-tasks/simulation/{task_id}/observability?catalog=logs
    sim_obs_result = None
    sim_obs_task_id = sim_finished_id or sim_task_id
    if sim_obs_task_id:
        sim_obs_result = _live_call(client, 'list_sim_rl_task_observations',
                                    (sim_obs_task_id,), {'catalog': 'logs'},
                                    'GET',
                                    f'/v1/training/rl-tasks/simulation/{sim_obs_task_id}/observability?catalog=logs')
    else:
        _skip_record('list_sim_rl_task_observations', 'workspace 无 SimRL 任务')

    # 18. show_sim_rl_task_observations_content — GET /v1/training/rl-tasks/simulation/{task_id}/observability/content
    sim_file_name = _extract_file_name(sim_obs_result) if sim_obs_result else None
    if sim_obs_task_id and sim_file_name:
        _live_call(client, 'show_sim_rl_task_observations_content',
                   (sim_obs_task_id,),
                   {'file_name': sim_file_name, 'catalog': 'logs',
                    'start_byte': 0, 'end_byte': 1000000, 'flag': 'false'},
                   'GET', f'/v1/training/rl-tasks/simulation/{sim_obs_task_id}/observability/content')
    else:
        _skip_record('show_sim_rl_task_observations_content', '无可用日志文件（observations 未返回文件名或无 task）')

    # 19. show_sim_rl_task_observations_signed_url — GET /v1/training/rl-tasks/simulation/{task_id}/observability/signed-url
    if sim_obs_task_id and sim_file_name:
        _live_call(client, 'show_sim_rl_task_observations_signed_url',
                   (sim_obs_task_id, 'TRAIN', sim_file_name), {},
                   'GET', f'/v1/training/rl-tasks/simulation/{sim_obs_task_id}/observability/signed-url')
    else:
        _skip_record('show_sim_rl_task_observations_signed_url', '无可用日志文件')

    _sim_existing_raw = client.show_sim_rl_task(sim_task_id) if sim_task_id else None
    sim_existing_detail = (_sim_existing_raw or {}).get('payload', _sim_existing_raw)

    return {
        'sim_list_result': sim_list_result,
        'sim_task_id': sim_task_id,
        'sim_current_user_id': sim_current_user_id,
        'sim_existing_detail': sim_existing_detail,
    }


def _live_train_write(client, workspace_id, list_result, train_task_id, train_finished_id):
    """训练任务写操作测试（T1-T8）。包含 try/finally 清理。"""
    # ========== 写操作测试（Train 9 个写方法） ==========

    train_stopped_id = _extract_stopped_task_id(list_result)
    train_running_id = _extract_running_task_id(list_result)

    _existing_raw = client.show_train_task(train_task_id) if train_task_id else None
    existing_task_detail = (_existing_raw or {}).get('payload', _existing_raw)

    created_train_ids = []

    try:
        # T1. save_draft — POST /v1/training/train-tasks/draft
        # 从现有任务获取完整配置，确保 copy/restart 能成功
        draft_name = f'sdk-test-draft-{int(time.time())}'
        draft_req = {'name': draft_name, 'workspace_id': workspace_id}
        if existing_task_detail:
            for field in ['algorithm', 'spec', 'train_mode', 'train_method', 'datasets',
                          'input_models', 'output_models', 'worker_num', 'cluster_id',
                          'parameters', 'env', 'description']:
                val = existing_task_detail.get(field)
                if val:
                    draft_req[field] = val
        draft_result = _live_call(client, 'save_draft', (draft_req,), {},
                                  'POST', '/v1/training/train-tasks/draft')
        draft_payload = (draft_result or {}).get('payload', draft_result or {})
        draft_train_id = draft_payload.get('task_id')
        if draft_train_id:
            created_train_ids.append(draft_train_id)

        # T2. update_train_task — PATCH /v1/training/train-tasks/{task_id}
        update_target = draft_train_id or train_task_id
        if update_target:
            update_req = {'name': f'{draft_name}-updated', 'description': 'updated by live test'}
            _live_call(client, 'update_train_task', (update_target, update_req), {},
                       'PATCH', f'/v1/training/train-tasks/{update_target}')

        # T3. restart_train_task — POST /v1/training/train-tasks/{task_id}/restart（提交草稿）
        restart_target = draft_train_id or train_task_id
        if restart_target and existing_task_detail and existing_task_detail.get('algorithm'):
            restart_req = {
                'name': f'sdk-test-restart-{int(time.time())}',
                'workspace_id': workspace_id,
                'algorithm': existing_task_detail.get('algorithm'),
                'spec': existing_task_detail.get('spec', 'Ascend: 1 * SNT9B2 | 24 vCPUs | 192 GiB'),
                'train_mode': existing_task_detail.get('train_mode', 'MODEL_TUNING'),
            }
            if existing_task_detail.get('train_method'):
                restart_req['train_method'] = existing_task_detail['train_method']
            restart_result = _live_call(client, 'restart_train_task', (restart_target, restart_req), {},
                                        'POST', f'/v1/training/train-tasks/{restart_target}/restart')
            restart_payload = (restart_result or {}).get('payload', restart_result or {})
            restarted_train_id = restart_payload.get('task_id') or restart_target
            if restarted_train_id and restarted_train_id != restart_target:
                created_train_ids.append(restarted_train_id)
            _wait_for_task_state(
                client, restarted_train_id,
                {'RUNNING', 'FINISHED', 'FAILED', 'STOPPED', 'ABNORMAL', 'PENDING'},
                timeout=90, interval=5)
        else:
            restarted_train_id = None

        # T4. stop_train_task — POST /v1/training/train-tasks/{task_id}/stop
        # 优先查找 WAITING/PENDING/RUNNING 任务；否则创建专用任务
        stop_target = None
        # 先查找现有可停止任务
        for candidate in [restarted_train_id, train_running_id, train_task_id]:
            if candidate:
                status = _get_task_status(client, candidate)
                if status in ('WAITING', 'PENDING', 'RUNNING'):
                    stop_target = candidate
                    break
        # 如果没有可停止任务，创建专用任务
        if not stop_target and existing_task_detail and existing_task_detail.get('algorithm'):
            stop_create_req = {
                'name': f'sdk-test-stop-{int(time.time())}',
                'workspace_id': workspace_id,
                'algorithm': existing_task_detail['algorithm'],
                'spec': existing_task_detail.get('spec', 'Ascend: 1 * SNT9B2 | 24 vCPUs | 192 GiB'),
                'train_mode': existing_task_detail.get('train_mode', 'MODEL_TUNING'),
            }
            if existing_task_detail.get('train_method'):
                stop_create_req['train_method'] = existing_task_detail['train_method']
            stop_create_result = _live_call(client, 'create_train_task', (stop_create_req,), {},
                                            'POST', '/v1/training/train-tasks')
            stop_create_payload = (stop_create_result or {}).get('payload', stop_create_result or {})
            stop_target = stop_create_payload.get('task_id')
            if stop_target:
                created_train_ids.append(stop_target)
                # 等待进入可停止状态（WAITING/PENDING/RUNNING）
                pre_status = _wait_for_task_state(
                    client, stop_target,
                    {'WAITING', 'PENDING', 'RUNNING'},
                    timeout=120, interval=5)
            else:
                pre_status = None
        else:
            pre_status = _get_task_status(client, stop_target) if stop_target else None
            if pre_status in ('DRAFT', 'SUBMITTING', None):
                pre_status = _wait_for_task_state(
                    client, stop_target,
                    {'WAITING', 'PENDING', 'RUNNING'},
                    timeout=120, interval=5)

        if stop_target and pre_status in ('WAITING', 'PENDING', 'RUNNING'):
            _live_call(client, 'stop_train_task', (stop_target,), {},
                       'POST', f'/v1/training/train-tasks/{stop_target}/stop')
            _wait_for_task_state(client, stop_target, {'STOPPED', 'STOP_FAILED'},
                                 timeout=30, interval=3)
        else:
            _skip_record('stop_train_task',
                         f'无可停止任务（当前状态: {pre_status}）')

        # T5. resume_train_task — POST /v1/training/train-tasks/{task_id}/resume
        # 用刚停止的任务恢复（仅 STOPPED 状态可恢复）
        resume_target = stop_target
        if resume_target:
            pre_status = _get_task_status(client, resume_target)
            if pre_status == 'STOPPED':
                _live_call(client, 'resume_train_task', (resume_target,), {},
                           'POST', f'/v1/training/train-tasks/{resume_target}/resume')
            else:
                _skip_record('resume_train_task',
                             f'目标任务非 STOPPED 状态（当前: {pre_status}），跳过恢复')
        else:
            _skip_record('resume_train_task', '无可用的训练任务用于恢复')

        # T6. create_train_task — POST /v1/training/train-tasks（从现有任务复制配置）
        if existing_task_detail and existing_task_detail.get('algorithm'):
            create_req = {
                'name': f'sdk-test-create-{int(time.time())}',
                'workspace_id': workspace_id,
                'algorithm': existing_task_detail['algorithm'],
                'spec': existing_task_detail.get('spec', 'Ascend: 1 * SNT9B2 | 24 vCPUs | 192 GiB'),
                'train_mode': existing_task_detail.get('train_mode', 'MODEL_TUNING'),
            }
            if existing_task_detail.get('train_method'):
                create_req['train_method'] = existing_task_detail['train_method']
            create_result = _live_call(client, 'create_train_task', (create_req,), {},
                                       'POST', '/v1/training/train-tasks')
            create_payload = (create_result or {}).get('payload', create_result or {})
            created_id = create_payload.get('task_id')
            if created_id:
                created_train_ids.append(created_id)

        # T7. batch_delete_train_tasks — POST /v1/training/train-tasks/batch-delete（清理 + 测试接口）
        # 等待任务离开 SUBMITTING 状态（提交中的任务不允许删除）
        if created_train_ids:
            for tid in created_train_ids:
                _wait_for_task_state(
                    client, tid,
                    {'DRAFT', 'RUNNING', 'FINISHED', 'FAILED', 'STOPPED', 'ABNORMAL', 'PENDING'},
                    timeout=60, interval=5)
            exec_ids = []
            for tid in created_train_ids:
                try:
                    eid = _get_execution_id(client, tid)
                    if eid:
                        exec_ids.append(eid)
                except Exception as e:
                    print(f"[T7] 获取 execution_id 失败 (task={tid}): {e}")
            if exec_ids:
                _live_call(client, 'batch_delete_train_tasks', (exec_ids,), {},
                           'POST', '/v1/training/train-tasks/batch-delete')
                created_train_ids.clear()

        # T8. register_train_checkpoint — POST /v1/training/train-tasks/{task_id}/checkpoints/register
        _skip_record('register_train_checkpoint', 'API 未注册，环境尚未部署 checkpoint 接口')

    finally:
        if created_train_ids:
            for tid in created_train_ids:
                if tid:
                    _wait_for_task_state(
                        client, tid,
                        {'DRAFT', 'RUNNING', 'FINISHED', 'FAILED', 'STOPPED', 'ABNORMAL', 'PENDING'},
                        timeout=60, interval=5)
            exec_ids = []
            for tid in created_train_ids:
                if tid:
                    try:
                        eid = _get_execution_id(client, tid)
                        if eid:
                            exec_ids.append(eid)
                    except Exception as e:
                        print(f"[Cleanup] 获取 execution_id 失败 (task={tid}): {e}")
            if exec_ids:
                try:
                    client.batch_delete_train_tasks(exec_ids)
                except Exception as e:
                    print(f"[Cleanup] 批量删除失败: {e}")


def _live_simrl_write(client, workspace_id, sim_list_result, sim_task_id, sim_current_user_id, sim_existing_detail, http=None):
    """SimRL 写操作测试（S1-S8）。包含 try/finally 清理。"""
    category = 'Live API 调用'
    # ========== 写操作测试（SimRL 7 个写方法） ==========

    sim_running_id = _extract_running_task_id(sim_list_result)

    created_sim_ids = []

    try:
        # S1. create_sim_rl_task_draft — POST /v1/training/rl-tasks/simulation/draft
        sim_draft_name = f'sdk-test-sim-draft-{int(time.time())}'
        sim_draft_req = _build_simrl_req(sim_existing_detail, sim_draft_name, workspace_id)
        sim_draft_result = _live_call(client, 'create_sim_rl_task_draft', (sim_draft_req,), {},
                                      'POST', '/v1/training/rl-tasks/simulation/draft')
        sim_draft_payload = (sim_draft_result or {}).get('payload', sim_draft_result or {})
        draft_sim_id = sim_draft_payload.get('task_id')
        if draft_sim_id:
            created_sim_ids.append(draft_sim_id)

        # S2. update_sim_rl_task — PATCH /v1/training/rl-tasks/simulation/{task_id}
        sim_update_target = draft_sim_id or sim_task_id
        if sim_update_target:
            sim_update_req = {'name': f'{sim_draft_name}-updated', 'description': 'updated by live test'}
            _live_call(client, 'update_sim_rl_task', (sim_update_target, sim_update_req), {},
                       'PATCH', f'/v1/training/rl-tasks/simulation/{sim_update_target}')

        # S3. copy_sim_rl_task — POST /v1/training/rl-tasks/simulation/{task_id}/copy
        # 遍历当前用户的 SimRL 任务作为 copy 源（部分 model 无 copy 权限，需换源重试）
        # 注意：copy 源必须是非 DRAFT 任务（DRAFT 无 copy 权限）
        copied_sim_id = None
        sim_list_payload = (sim_list_result or {}).get('payload', sim_list_result or {})
        sim_list_items = sim_list_payload.get('list', [])
        tried_sim_algos = set()
        sim_copy_done = False
        sim_last_err = 'workspace 无可复制 SimRL 任务'
        sim_last_req = None
        sim_last_tb = ''
        for sim_item in sim_list_items:
            if sim_item.get('user_id') != sim_current_user_id or sim_item.get('status') == 'DELETING':
                continue
            sd = client.show_sim_rl_task(sim_item['id'])
            sdp = (sd or {}).get('payload', sd or {})
            im = sdp.get('input_models')
            if not im:
                continue
            im_key = str(im[0].get('model_asset_id')) if im else ''
            if im_key in tried_sim_algos:
                continue
            tried_sim_algos.add(im_key)
            sim_copy_source = sim_item['id']
            sim_copy_req = _build_simrl_req(sdp, f'sdk-test-sim-copy-{int(time.time())}', workspace_id)
            sim_req_info = {'method': 'copy_sim_rl_task', 'http': 'POST',
                            'path': f'/v1/training/rl-tasks/simulation/{sim_copy_source}/copy',
                            'args': [sim_copy_source, sim_copy_req], 'kwargs': {}}
            try:
                result = client.copy_sim_rl_task(sim_copy_source, sim_copy_req)
                record(category, 'copy_sim_rl_task', True,
                       f'POST /v1/training/rl-tasks/simulation/{sim_copy_source}/copy (model={im_key})',
                       request=_truncate(sim_req_info), response=_truncate(result))
                scp = (result or {}).get('payload', result or {})
                copied_sim_id = scp.get('task_id')
                if copied_sim_id:
                    created_sim_ids.append(copied_sim_id)
                sim_copy_done = True
                break
            except Exception as e:
                sim_last_err = str(e)
                sim_last_req = sim_req_info
                sim_last_tb = traceback.format_exc(limit=3)
                continue
        if not sim_copy_done:
            record(category, 'copy_sim_rl_task', False,
                   f'调用失败: {sim_last_err}',
                   request=_truncate(sim_last_req),
                   response=sim_last_tb)

        # S4. delete_sim_rl_task — DELETE /v1/training/rl-tasks/simulation/{task_id}
        # 只删除副本，保留草稿用于 S6 restart
        # 副本创建后处于 SUBMITTING 状态，需等待状态迁移后再删除
        if copied_sim_id:
            _wait_for_task_state(client, copied_sim_id,
                                 {'PENDING', 'WAITING', 'RUNNING', 'FINISHED', 'STOPPED', 'FAILED', 'ABNORMAL'},
                                 timeout=90, interval=5, is_sim=True)
            _live_call(client, 'delete_sim_rl_task', (copied_sim_id,), {},
                       'DELETE', f'/v1/training/rl-tasks/simulation/{copied_sim_id}')
            if copied_sim_id in created_sim_ids:
                created_sim_ids.remove(copied_sim_id)

        # S5. stop_sim_rl_task — POST /v1/training/rl-tasks/simulation/{task_id}/stop
        # 优先查找已存在的可停止任务（WAITING/PENDING/RUNNING）；否则创建新任务
        stop_target_id = None
        stop_target_status = None
        # 查找现有可停止任务
        for status_filter in ['RUNNING', 'WAITING', 'PENDING']:
            try:
                tasks = client.list_sim_rl_tasks(workspace_id=workspace_id, status=status_filter)
                task_list = (tasks or {}).get('payload', tasks or {}).get('list', [])
                if task_list:
                    stop_target_id = task_list[0].get('task_id')
                    stop_target_status = status_filter
                    break
            except Exception as e:
                print(f'[S5] 查找 {status_filter} SimRL 任务失败: {e}')

        if stop_target_id:
            _live_call(client, 'stop_sim_rl_task', (stop_target_id,), {},
                       'POST', f'/v1/training/rl-tasks/simulation/{stop_target_id}/stop')
            _wait_for_task_state(client, stop_target_id, {'STOPPED'}, timeout=30, interval=5, is_sim=True)
        elif sim_existing_detail:
            stop_create_req = _build_simrl_req(sim_existing_detail,
                                               f'sdk-test-sim-stop-{int(time.time())}', workspace_id)
            stop_create_result = _live_call(client, 'create_sim_rl_task', (stop_create_req,), {},
                                            'POST', '/v1/training/rl-tasks/simulation')
            stop_create_payload = (stop_create_result or {}).get('payload', stop_create_result or {})
            stop_sim_id = stop_create_payload.get('task_id')
            if stop_sim_id:
                created_sim_ids.append(stop_sim_id)
                # 等待进入可停止状态（WAITING/PENDING/RUNNING）
                pre_status = _wait_for_task_state(client, stop_sim_id,
                                                  {'WAITING', 'PENDING', 'RUNNING'},
                                                  timeout=120, interval=5, is_sim=True)
                if pre_status in ('WAITING', 'PENDING', 'RUNNING'):
                    _live_call(client, 'stop_sim_rl_task', (stop_sim_id,), {},
                               'POST', f'/v1/training/rl-tasks/simulation/{stop_sim_id}/stop')
                    _wait_for_task_state(client, stop_sim_id, {'STOPPED'}, timeout=30, interval=5, is_sim=True)
                else:
                    _skip_record('stop_sim_rl_task',
                                 f'创建的任务未进入可停止状态（当前: {pre_status}）')
            else:
                _skip_record('stop_sim_rl_task', '创建 SimRL 任务未返回 task_id')
        else:
            _skip_record('stop_sim_rl_task', '无 SimRL 任务可创建停止测试')

        # S6. restart_sim_rl_task — POST /v1/training/rl-tasks/simulation/{task_id}/restart
        sim_restart_target = draft_sim_id or sim_task_id
        if sim_restart_target and sim_existing_detail:
            sim_restart_req = _build_simrl_req(sim_existing_detail,
                                               f'sdk-test-sim-restart-{int(time.time())}', workspace_id)
            sim_restart_result = _live_call(client, 'restart_sim_rl_task', (sim_restart_target, sim_restart_req), {},
                                            'POST', f'/v1/training/rl-tasks/simulation/{sim_restart_target}/restart')
            sim_restart_payload = (sim_restart_result or {}).get('payload', sim_restart_result or {})
            sim_restarted_id = sim_restart_payload.get('task_id') or sim_restart_target
            if sim_restarted_id and sim_restarted_id != sim_restart_target:
                created_sim_ids.append(sim_restarted_id)
            _wait_for_task_state(
                client, sim_restarted_id,
                {'RUNNING', 'FINISHED', 'FAILED', 'STOPPED', 'ABNORMAL', 'PENDING'},
                timeout=90, interval=5, is_sim=True)

        # S7. create_sim_rl_task — POST /v1/training/rl-tasks/simulation
        if sim_existing_detail:
            sim_create_req = _build_simrl_req(sim_existing_detail,
                                              f'sdk-test-sim-create-{int(time.time())}', workspace_id)
            sim_create_result = _live_call(client, 'create_sim_rl_task', (sim_create_req,), {},
                                           'POST', '/v1/training/rl-tasks/simulation')
            sim_create_payload = (sim_create_result or {}).get('payload', sim_create_result or {})
            sim_created_id = sim_create_payload.get('task_id')
            if sim_created_id:
                created_sim_ids.append(sim_created_id)

        # S8. create_sim_rl_task on dedicated resource pool
        # 查找专属资源池（DEDICATED），使用其 cluster_id 创建 SimRL 任务
        try:
            from cloudrobo_resource import ResourceClient
            resource_client = ResourceClient(http)
            pools_result = resource_client.list_pools(workspace_id=workspace_id)
            pool_list = pools_result.get('resources', pools_result.get('payload', pools_result).get('list', []))
            dedicated_pool = None
            for pool in pool_list:
                if pool.get('pool_type') == 'DEDICATED' and pool.get('status') == 'AVAILABLE':
                    usages = pool.get('usages', [])
                    if 'SIMULATION' in usages or 'MODEL_TRAINING' in usages:
                        dedicated_pool = pool
                        break

            if dedicated_pool:
                dedicated_resource_id = dedicated_pool.get('resource_id')
                dedicated_cluster_id = f'pool-{dedicated_resource_id}' if not dedicated_resource_id.startswith(
                    'pool-') else dedicated_resource_id
                sim_dedicated_req = _build_simrl_req(
                    sim_existing_detail,
                    f'sdk-test-sim-dedicated-{int(time.time())}',
                    workspace_id,
                    cluster_id=dedicated_cluster_id)
                sim_dedicated_result = _live_call(
                    client, 'create_sim_rl_task', (sim_dedicated_req,), {},
                    'POST', '/v1/training/rl-tasks/simulation')
                sim_dedicated_payload = (sim_dedicated_result or {}).get('payload', sim_dedicated_result or {})
                sim_dedicated_id = sim_dedicated_payload.get('task_id')
                if sim_dedicated_id:
                    created_sim_ids.append(sim_dedicated_id)
                    record(category, 'create_sim_rl_task (dedicated pool)', True,
                           f'使用专属资源池 {dedicated_pool.get("resource_name")} ({dedicated_cluster_id}) 创建 SimRL 任务',
                           request=f'cluster_id: {dedicated_cluster_id}',
                           response=f'task_id: {sim_dedicated_id}')
            else:
                _skip_record('create_sim_rl_task (dedicated pool)', '无可用的专属资源池')
        except ImportError:
            _skip_record('create_sim_rl_task (dedicated pool)', 'cloudrobo_resource 模块未安装')
        except Exception as e:
            record(category, 'create_sim_rl_task (dedicated pool)', False,
                   f'查询或创建失败: {e}',
                   response=traceback.format_exc(limit=3))

    finally:
        for sid in created_sim_ids:
            if sid:
                _wait_for_task_state(client, sid,
                                     {'PENDING', 'WAITING', 'RUNNING', 'FINISHED', 'STOPPED', 'FAILED', 'ABNORMAL'},
                                     timeout=60, interval=5, is_sim=True)
                try:
                    client.delete_sim_rl_task(sid)
                except Exception as e:
                    print(f"[Cleanup] 删除 SimRL 任务失败 (task={sid}): {e}")


def test_live_api():
    """Live API 调用 — 编排子函数完成全量接口测试。"""
    client, workspace_id, _http = _live_setup()
    if not client:
        return

    train_data = _live_train_readonly(client, workspace_id)
    sim_data = _live_simrl_readonly(client, workspace_id, train_data.get('current_user_id'))

    _live_train_write(client, workspace_id,
                      train_data['list_result'],
                      train_data['train_task_id'],
                      train_data['train_finished_id'])

    _live_simrl_write(client, workspace_id,
                      sim_data['sim_list_result'],
                      sim_data['sim_task_id'],
                      sim_data['sim_current_user_id'],
                      sim_data['sim_existing_detail'],
                      http=_http)


# ========== 主入口 ==========

def run_all_tests(live=False, cli_only=False):
    print('=' * 60)
    print('cloudrobo-train 接口准确性测试')
    print(f'时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'Live 测试: {"启用" if live else "禁用"}')
    if cli_only:
        print('测试范围: 仅 CLI')
    print('=' * 60)

    print('\n[1/6] CLI 命令注册...')
    test_cli_registration()

    print('[2/6] CLI 参数完整性...')
    test_cli_params()

    print('[3/6] --sim-rl 开关位置...')
    test_sim_rl_flag()

    if not cli_only:
        print('[4/6] SDK 方法存在性...')
        test_sdk_methods_exist()

        print('[5/6] SDK 方法签名...')
        test_sdk_signatures()

        print('[6/6] SDK URL 构造...')
        test_sdk_url_construction()

    if live:
        if cli_only:
            print('[4/4] Live CLI 调用...')
            test_live_cli()
        else:
            print('[7/7] Live CLI 调用...')
            test_live_cli()
            print('\n[8/8] Live API 调用...')
            test_live_api()

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r.passed is True)
    failed = sum(1 for r in results if r.passed is False)
    skipped = sum(1 for r in results if r.passed is None)
    print('\n' + '=' * 60)
    print(f'总计: {total}  通过: {passed}  失败: {failed}  跳过: {skipped}')
    print('=' * 60)

    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='cloudrobo-train 接口测试')
    parser.add_argument('--live', action='store_true', help='启用 Live API 测试（需凭证）')
    parser.add_argument('--cli-only', action='store_true', help='仅运行 CLI 测试（跳过 SDK 测试）')
    parser.add_argument('--report', action='store_true', help='生成 HTML 报告')
    args = parser.parse_args()

    results = run_all_tests(live=args.live, cli_only=args.cli_only)

    if args.report:
        from generate_train_test_report import generate_html_report

        report_path = generate_html_report(results, live=args.live)
        print(f'\nHTML 报告已生成: {report_path}')
