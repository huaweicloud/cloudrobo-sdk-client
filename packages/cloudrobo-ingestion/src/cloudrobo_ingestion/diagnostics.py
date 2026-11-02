"""Shared, bounded diagnostics for local processing, OBS and reporting."""

from __future__ import annotations

import json
import logging
import socket
import ssl
import time
import uuid

from cloudrobo_core.sdk.obs_diagnostics import credential_values, exception_details, redact

LABELS = {"obs": "OBS 上传异常", "report": "进度上报异常", "local": "本地处理异常"}
STAGES = {
    "initializing": "初始化",
    "obs_initializing": "初始化 OBS 客户端",
    "uploading": "上传 OBS 文件",
    "upload_local_io": "读取上传副本或 checkpoint",
    "obs_sdk": "调用 OBS SDK",
    "report_config": "读取上报配置",
    "checkpoint_cleanup": "清理 OBS 分段上传",
    "connect": "连接 Zenoh router",
    "publish": "发布进度消息",
    "serialize": "构建进度消息",
    "copying": "复制源数据",
    "scan": "扫描源目录",
    "validating": "校验数据格式",
    "cleanup": "清理本地副本",
    "database": "读写批次数据库",
    "worker_start": "启动上传子进程",
    "worker_exit": "上传子进程退出",
    "ipc": "进程间通信",
    "status": "写入状态文件",
    "config": "读取配置",
    "logging": "创建日志文件",
    "control": "本地进程控制",
    "runtime": "运行主进程",
    "close": "关闭连接",
}


def safe(value, secrets=()):
    secrets = tuple(secrets) + credential_values()
    if isinstance(value, dict):
        return {
            str(k): safe(v, secrets)
            for k, v in value.items()
            if str(k).lower()
            not in {"ak", "sk", "token", "password", "authorization", "secret_key", "access_key"}
        }
    if isinstance(value, (list, tuple)):
        return [safe(v, secrets) for v in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(value, secrets)


def make_error(
    component,
    stage,
    exc=None,
    *,
    diagnostic=None,
    context=None,
    code=None,
    message=None,
    hint=None,
    secrets=(),
):
    details = dict(diagnostic or {})
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        chain.append(exception_details(current, tuple(secrets) + credential_values()))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    if chain:
        details = chain[0] | details
        details["causes"] = chain
    evidence = json.dumps(safe(details, secrets), ensure_ascii=False).lower()
    if component == "obs" and details.get("exception_type") in (
        "PermissionError",
        "FileNotFoundError",
        "IsADirectoryError",
    ):
        component, stage = "local", "upload_local_io"
    elif component == "obs" and details.get("exception_type") in ("TypeError", "AttributeError"):
        component, stage = "local", "obs_sdk"
    reason = details.get("message") or "SDK 未提供明确原因，请根据错误 ID 查看日志中的技术详情"
    suffix, advice = "FAILED", "请根据错误 ID 查看日志，核对相关配置、文件和依赖。"
    if component in ("obs", "report"):
        if any(
            s in evidence
            for s in (
                "gaierror",
                "name or service not known",
                "getaddrinfo failed",
                "failed to resolve",
                "nodename nor servname",
            )
        ):
            suffix, reason = "DNS_FAILED", "无法解析服务器地址"
            advice = "请检查服务器地址、区域配置和本机 DNS。"
            if component == "report":
                advice = "请检查 router 地址和本机 DNS。"
        elif any(
            s in evidence
            for s in (
                "sslerror",
                "certificate verify failed",
                "unknown ca",
                "tls alert",
                "bad certificate",
            )
        ) or isinstance(exc, ssl.SSLError):
            suffix, reason = "TLS_FAILED", "TLS 连接或证书校验失败"
            advice = "请检查服务器地址、TLS 配置和证书信任链。"
        elif any(s in evidence for s in ("timeouterror", "timed out", "deadline has elapsed")):
            suffix, reason = "TIMEOUT", "连接或请求超时"
            advice = "请检查服务器可达性、网络和超时配置。"
        elif any(
            s in evidence
            for s in ("connectionrefusederror", "connection refused", "actively refused")
        ):
            suffix, reason = "CONNECT_FAILED", "目标拒绝连接"
            advice = "请检查目标地址、端口、服务状态及防火墙。"
        elif "zenoh link disconnected" in evidence:
            suffix, reason = "DISCONNECTED", "Zenoh 链路已断开"
            advice = "请检查本地 router 进程、网络及连接地址。"
        elif any(s in evidence for s in ("nosuchbucket",)):
            suffix, reason = "BUCKET_NOT_FOUND", "OBS 桶不存在"
            advice = "请检查 obs_path 中的桶名及 endpoint 对应区域。"
        elif (
            any(
                s in evidence
                for s in ("invalidaccesskeyid", "signaturedoesnotmatch", "expiredtoken")
            )
            or str(details.get("http_status")) == "401"
        ):
            suffix, reason = "AUTH_FAILED", "身份认证或请求签名失败"
            advice = "请检查凭证、系统时间和服务器区域配置。"
            if component == "report":
                advice = "请检查本地 router 的认证和授权配置。"
        elif "accessdenied" in evidence or str(details.get("http_status")) == "403":
            suffix, reason = "ACCESS_DENIED", "服务器拒绝访问"
            advice = "请检查凭证授权、桶策略及目标路径权限。"
            if component == "report":
                advice = "请检查本地 router 的认证和 topic 授权配置。"
        elif isinstance(exc, socket.error) and getattr(exc, "errno", None) in (101, 113):
            suffix, reason, advice = "NETWORK_FAILED", "网络不可达", "请检查网络路由及服务器地址。"
        elif component == "report":
            advice = "请检查本地 Zenoh router、连接地址、端口及认证/TLS 配置。"
        else:
            advice = "请检查 OBS endpoint、桶名、区域、网络及凭证；详细原因见日志。"
    if component == "local":
        prefix = {
            "upload_local_io": "上传所需的本地文件操作失败",
            "obs_sdk": "OBS SDK 调用异常",
            "copying": "本地复制失败",
            "scan": "源目录扫描失败",
            "validating": "数据校验失败",
            "database": "批次数据库读写失败",
            "cleanup": "本地副本清理失败",
            "serialize": "进度数据构建失败",
            "status": "状态文件写入失败",
            "config": "配置错误",
            "worker_exit": "上传子进程异常退出",
            "worker_start": "上传子进程启动失败",
        }.get(stage, "本地程序处理失败")
        if details.get("errno") == 28 or "no space left" in evidence:
            suffix, reason, advice = "DISK_FULL", "磁盘空间不足", "请释放工作目录所在磁盘空间。"
        elif "permissionerror" in evidence:
            suffix, advice = (
                "PERMISSION_DENIED",
                "请检查相关文件和工作目录的权限，以及是否被其他程序占用。",
            )
    else:
        prefix = "OBS 上传失败" if component == "obs" else "进度上报失败"
    code_stage = {
        "uploading": "UPLOAD",
        "obs_initializing": "INIT",
        "checkpoint_cleanup": "CLEANUP",
    }.get(stage, stage.upper())
    return safe(
        dict(
            error_id=uuid.uuid4().hex[:12],
            component=component,
            stage=stage,
            code=code
            or (
                f"{component.upper()}_{code_stage}_FAILED"
                if suffix == "FAILED"
                else f"{component.upper()}_{suffix}"
            ),
            message=message or f"{prefix}：{reason}",
            hint=hint or advice,
            time_ms=time.time_ns() // 1_000_000,
            context=context or {},
            details=details,
        ),
        secrets,
    )


def summary(info):
    text = f"{LABELS[info['component']]} [{info['code']}] {info['message']} [stage={info['stage']}]"
    extra = {
        k: v
        for k, v in info["details"].items()
        if k in ("http_status", "error_code", "request_id", "exception_type", "errno", "winerror")
        and v not in (None, "", "None")
    }
    return (
        text
        + (" " + json.dumps(extra, ensure_ascii=False) if extra else "")
        + f" 错误 ID={info['error_id']}"
    )


class DiagnosticError(RuntimeError):
    def __init__(self, info):
        self.info = info
        super().__init__(summary(info))


def log_error(logger, info, level=logging.ERROR):
    logger.log(
        level,
        "%s 建议：%s 诊断=%s",
        summary(info),
        info["hint"],
        json.dumps(info, ensure_ascii=False),
    )


class ErrorTracker:
    """One current diagnostic per reporter; repeated failures cannot grow history."""

    def __init__(self, logger, clock=time.monotonic):
        self.logger, self.clock = logger, clock
        self.info = None
        self.fingerprint = None
        self.count = 0
        self.logged_at = 0

    def failed(self, info):
        fingerprint = json.dumps(
            [info["component"], info["stage"], info["code"], info["message"], info["context"]],
            sort_keys=True,
        )
        now = self.clock()
        if fingerprint != self.fingerprint:
            self.info, self.fingerprint, self.count = info, fingerprint, 1
            log_error(self.logger, info)
            self.logged_at = now
        else:
            self.count += 1
            # Keep the same ID but update request IDs/technical details.
            info["error_id"] = self.info["error_id"]
            self.info = info
            if now - self.logged_at >= 60:
                log_error(self.logger, info)
                self.logger.warning("错误 ID=%s 重复次数=%s", info["error_id"], self.count)
                self.logged_at = now
        return self.info

    def recovered(self):
        if self.info:
            self.logger.info("异常已恢复：%s", summary(self.info))
        self.info = self.fingerprint = None
        self.count = 0
