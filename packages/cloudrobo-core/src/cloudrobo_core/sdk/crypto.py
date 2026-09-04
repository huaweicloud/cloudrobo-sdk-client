"""机器绑定加密：用机器特征派生密钥，AES-GCM 加解密 AK/SK"""
import base64
import getpass
import hashlib
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_NONCE_SIZE = 12


def _read_machine_id() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                return winreg.QueryValueEx(key, "MachineGuid")[0]
        except Exception as e:
            logger.debug("读取 Windows machine-id 失败: %s", e)
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="ignore")
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
        except Exception as e:
            logger.debug("读取 macOS machine-id 失败: %s", e)
    else:
        for p in [Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")]:
            if p.exists():
                return p.read_text(encoding="utf-8").strip()
    return "no-machine-id"


def _derive_key() -> bytes:
    features = f"{socket.gethostname()}:{getpass.getuser()}:{_read_machine_id()}"
    return hashlib.sha256(features.encode("utf-8")).digest()


def encrypt(plaintext: str) -> str:
    key = _derive_key()
    nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(encrypted: str) -> str:
    raw = base64.b64decode(encrypted)
    nonce = raw[:_NONCE_SIZE]
    ciphertext = raw[_NONCE_SIZE:]
    aesgcm = AESGCM(_derive_key())
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
