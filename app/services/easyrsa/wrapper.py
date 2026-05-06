"""EasyRSA subprocess 封装 — 所有证书操作通过此模块委托给 EasyRSA 官方脚本"""
# -*- coding: utf-8 -*-
import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.core.config import load_config
from app.core.constants import (
    EASYRSA_CA_EXPIRE_DAYS,
    EASYRSA_CERT_EXPIRE_DAYS,
    OPENVPN_SEARCH_PATHS,
)

logger = logging.getLogger(__name__)


class EasyRSAWrapper:
    """
    封装 EasyRSA 命令行操作。
    所有调用通过 subprocess.run 执行，参数不通过命令行明文传递密码。
    """

    def __init__(self, easyrsa_bin: str, pki_dir: str):
        """
        初始化 EasyRSA 封装。

        Args:
            easyrsa_bin: easyrsa 脚本的绝对路径
            pki_dir: PKI 目录路径（存放 CA、证书、密钥）
        """
        self.easyrsa_bin = str(easyrsa_bin or "").strip()
        self.pki_dir = str(pki_dir or "").strip()

    def _run(self, args: list[str], env_extra: dict | None = None,
             stdin_text: str | None = None) -> subprocess.CompletedProcess:
        """
        执行 EasyRSA 命令的统一入口。
        通过 EASYRSA_PKI 环境变量指定 PKI 目录。
        密码短语通过 stdin 传递，不使用命令行参数。
        """
        env = os.environ.copy()
        env["EASYRSA_PKI"] = self.pki_dir
        # 禁用交互式确认提示
        env["EASYRSA_BATCH"] = "1"
        # 覆盖 vars 默认（约 825 天叶子）；与 constants 中 EASYRSA_*_EXPIRE_DAYS 一致
        env["EASYRSA_CERT_EXPIRE"] = str(EASYRSA_CERT_EXPIRE_DAYS)
        env["EASYRSA_CA_EXPIRE"] = str(EASYRSA_CA_EXPIRE_DAYS)
        if env_extra:
            env.update(env_extra)

        cmd = [self.easyrsa_bin] + [str(a) for a in args]
        logger.info("执行 EasyRSA 命令: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            input=stdin_text,
        )
        logger.info("EasyRSA 命令执行完成: %s, returncode=%s", " ".join(cmd), result.returncode)
        if result.returncode != 0:
            raise EasyRSAError(
                f"EasyRSA 命令执行失败: {' '.join(cmd)}\n"
                f"退出码: {result.returncode}\n"
                f"标准错误: {result.stderr}"
            )
        return result

    def init_pki(self) -> bool:
        """初始化 PKI 目录结构"""
        self._run(["init-pki"])
        return True

    def build_ca(self) -> bool:
        """
        构建 CA 证书。
        密码短语通过环境变量 EASYRSA_PASSIN 传递。
        """
        logger.info("开始生成无密码 CA: pki_dir=%s", self.pki_dir)
        self._run(["build-ca", "nopass"])
        return True

    def gen_dh(self) -> bool:
        """生成 DH 参数"""
        self._run(["gen-dh"])
        return True

    def gen_req(self, cn: str) -> bool:
        """为指定 CN 生成证书请求（无密码保护私钥）"""
        self._run(["gen-req", cn, "nopass"])
        return True

    def sign_req(self, req_type: str, cn: str) -> bool:
        """
        签署证书请求。

        Args:
            req_type: 'server' 或 'client'
            cn: 证书通用名称
        """
        self._run(["sign-req", req_type, cn])
        return True

    def gen_crl(self) -> str:
        """
        生成/更新证书吊销列表。
        返回 CRL 文件路径。
        """
        self._run(["gen-crl"])
        return str(Path(self.pki_dir) / "crl.pem")

    def revoke(self, cn: str) -> bool:
        """吊销指定 CN 的证书"""
        self._run(["revoke", cn])
        return True

    def renew(self, cn: str) -> bool:
        """续签指定 CN 的证书"""
        self._run(["renew", cn, "nopass"])
        return True

    def gen_tls_crypt_v2_server(self, output_path: str | None = None) -> str:
        """
        生成 tls-crypt-v2 服务端主密钥。
        返回生成的密钥文件路径。
        """
        if output_path is None:
            output_path = str(Path(self.pki_dir).parent / "tc2-server.key")
        # 使用 openvpn 命令生成 tls-crypt-v2 密钥
        subprocess.run(
            [_resolve_openvpn_bin(), "--genkey", "tls-crypt-v2-server", output_path],
            check=True, capture_output=True, text=True
        )
        return output_path

    def gen_tls_crypt_v2_client(self, cn: str, server_key: str,
                                 output_path: str | None = None) -> str:
        """
        为指定客户端生成唯一的 tls-crypt-v2 客户端密钥。

        Args:
            cn: 客户端通用名称
            server_key: 服务端 tls-crypt-v2 主密钥路径
            output_path: 输出路径，默认存放在 PKI 目录下

        返回生成的客户端密钥文件路径。
        """
        if output_path is None:
            output_dir = Path(self.pki_dir) / "tc2-clients"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"{cn}.key")

        subprocess.run(
            [_resolve_openvpn_bin(), "--tls-crypt-v2", server_key,
             "--genkey", "tls-crypt-v2-client", output_path],
            check=True, capture_output=True, text=True
        )
        return output_path

    def get_cert_info(self, cn: str) -> dict | None:
        """
        从 PKI 的 index.txt 中查询指定 CN 的证书信息。

        返回 dict: serial, status(V/R/E), expires_at, revoked_at
        或 None（未找到）。
        """
        index_file = Path(self.pki_dir) / "index.txt"
        if not index_file.exists():
            return None

        for line in index_file.read_text(encoding="utf-8").splitlines():
            # index.txt 格式: Status\tExpiry\t[RevocationDate]\tSerial\tunknown\tCN_DN
            parts = line.split("\t")
            if len(parts) >= 6 and f"/CN={cn}" in parts[-1]:
                status_char = parts[0]
                return {
                    "serial": parts[3],
                    "status": {"V": "valid", "R": "revoked", "E": "expired"}.get(status_char, "unknown"),
                    "expires_at": parts[1],
                    "revoked_at": parts[2] if status_char == "R" else None,
                    "common_name": cn,
                }
        return None


class EasyRSAError(Exception):
    """EasyRSA 操作异常"""
    pass


def _resolve_openvpn_bin() -> str:
    """解析 OpenVPN 可执行文件路径。"""
    config = load_config()
    configured = str(config.get("openvpn_bin", "") or "").strip()
    if configured and Path(configured).is_file():
        return configured

    which_path = shutil.which("openvpn")
    if which_path:
        return which_path

    for candidate in OPENVPN_SEARCH_PATHS:
        path = Path(candidate)
        if path.is_file():
            return str(path)

    raise FileNotFoundError("未找到 OpenVPN 可执行文件，请先完成 OpenVPN 安装")
