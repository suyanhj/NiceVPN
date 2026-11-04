# -*- coding: utf-8 -*-
"""用户 .ovpn 配置文件生成服务

根据用户证书和服务器配置，构造完整的 .ovpn 客户端配置文件。
所有证书和密钥以内联块嵌入，不引用外部文件路径。
"""

import os
import re
from pathlib import Path
from uuid import uuid4

from app.core.constants import OVPN_PROFILES_DIR

_CLIENT_OVPN_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "templates" / "openvpn" / "client.ovpn.in"
)


def _render_client_ovpn_template(values: dict[str, object]) -> str:
    """读取独立客户端模板并替换占位符。"""

    try:
        text = _CLIENT_OVPN_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"无法读取 OpenVPN client 模板: {_CLIENT_OVPN_TEMPLATE}") from exc
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    unresolved = [token for token in (
        "{{USERNAME}}",
        "{{GROUP_SUBNET}}",
        "{{PROTO}}",
        "{{SERVER_IP}}",
        "{{PORT}}",
        "{{CA_CERT}}",
        "{{USER_CERT}}",
        "{{USER_KEY}}",
        "{{TC2_CLIENT_KEY}}",
    ) if token in text]
    if unresolved:
        raise RuntimeError(
            "OpenVPN client 模板存在未填充占位符: " + ", ".join(unresolved)
        )
    return text


def generate_ovpn(username: str, group_subnet: str, config: dict) -> str:
    """构造完整 .ovpn 配置内容。

    所有证书和 tls-crypt-v2 密钥以内联块嵌入，不引用外部文件路径。

    参数:
        username: 用户名
        group_subnet: 用户所属组的子网（如 10.8.1.0/24），写入注释供人工参考
        config: 包含以下字段的字典:
            - server_ip (str): 服务器公网 IP 或域名
            - port (int, 可选): 服务端口，默认 1194
            - proto (str, 可选): 协议，默认 "udp"
            - ca_cert (str): CA 证书 PEM 内容
            - user_cert (str): 用户证书 PEM 内容
            - user_key (str): 用户私钥 PEM 内容
            - tc2_client_key (str): tls-crypt-v2 客户端密钥内容

    返回:
        .ovpn 文件内容字符串（不写盘；由调用方 save_ovpn 落盘）
    """
    server_ip = config["server_ip"]
    port = config.get("port", 1194)
    proto = config.get("proto", "udp")
    ca_cert = config["ca_cert"].strip()
    user_cert = config["user_cert"].strip()
    user_key = config["user_key"].strip()
    tc2_client_key = config["tc2_client_key"].strip()

    return _render_client_ovpn_template(
        {
            "USERNAME": username,
            "GROUP_SUBNET": group_subnet,
            "PROTO": proto,
            "SERVER_IP": server_ip,
            "PORT": port,
            "CA_CERT": ca_cert,
            "USER_CERT": user_cert,
            "USER_KEY": user_key,
            "TC2_CLIENT_KEY": tc2_client_key,
        }
    )


def save_ovpn(username: str, content: str) -> Path:
    """将 .ovpn 内容保存到文件，返回文件路径。

    参数:
        username: 用户名，用作文件名
        content: .ovpn 文件完整内容

    返回:
        保存后的文件路径 (OVPN_PROFILES_DIR/{username}.ovpn)
    """
    OVPN_PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    file_path = OVPN_PROFILES_DIR / f"{username}.ovpn"
    file_path.write_text(content, encoding="utf-8")

    return file_path



def rewrite_ovpn_connection_settings(
    content: str,
    *,
    server_ip: str,
    port: int,
    proto: str,
) -> str:
    """只更新客户端 OVPN 的连接地址/端口/协议，保留证书与密钥原文。"""

    target_host = str(server_ip or "").strip()
    target_proto = str(proto or "").strip().lower()
    target_port = int(port)
    if not target_host:
        raise ValueError("server_ip 不能为空")
    if target_proto not in {"udp", "tcp"}:
        raise ValueError(f"不支持的 OpenVPN 客户端协议: {proto}")
    if not 1 <= target_port <= 65535:
        raise ValueError(f"OpenVPN 端口无效: {port}")

    lines = content.splitlines(keepends=True)
    remote_re = re.compile(r"^(\s*)remote\s+\S+(?:\s+\S+)?(?:\s+.*)?$", re.IGNORECASE)
    proto_re = re.compile(r"^(\s*)proto\s+\S+(?:\s+.*)?$", re.IGNORECASE)

    remote_found = False
    proto_found = False
    output: list[str] = []
    for raw in lines:
        body = raw.rstrip("\r\n")
        ending = raw[len(body):]
        remote_match = remote_re.match(body)
        if remote_match and not remote_found:
            output.append(
                f"{remote_match.group(1)}remote {target_host} {target_port}{ending}"
            )
            remote_found = True
            continue
        proto_match = proto_re.match(body)
        if proto_match and not proto_found:
            output.append(f"{proto_match.group(1)}proto {target_proto}{ending}")
            proto_found = True
            continue
        output.append(raw)

    if not remote_found:
        raise RuntimeError("OVPN 中未找到可更新的 remote 指令")
    if not proto_found:
        raise RuntimeError("OVPN 中未找到可更新的 proto 指令")
    return "".join(output)


def _replace_text_preserve_metadata(path: Path, content: str) -> None:
    """原子替换文本，同时尽量保持原文件权限与属主。"""

    stat = path.stat()
    temp = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        temp.write_text(content, encoding="utf-8")
        if os.name != "nt":
            os.chmod(temp, stat.st_mode & 0o777)
            try:
                os.chown(temp, stat.st_uid, stat.st_gid)
            except PermissionError:
                # 非 root 开发环境允许保留当前属主；生产 root 运行时会保持原属主。
                pass
        temp.replace(path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def sync_ovpn_connection_settings(
    paths: list[Path],
    *,
    server_ip: str,
    port: int,
    proto: str,
) -> dict[str, int]:
    """事务式同步已有 OVPN 的 remote/proto；失败时回滚已写文件。"""

    originals: dict[Path, str] = {}
    planned: dict[Path, str] = {}
    missing = 0

    # 先完整预检，避免内容异常时产生部分写入。
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            missing += 1
            continue
        old = path.read_text(encoding="utf-8")
        new = rewrite_ovpn_connection_settings(
            old,
            server_ip=server_ip,
            port=port,
            proto=proto,
        )
        originals[path] = old
        if new != old:
            planned[path] = new

    changed: list[Path] = []
    try:
        for path, new in planned.items():
            _replace_text_preserve_metadata(path, new)
            changed.append(path)
    except Exception as exc:
        rollback_errors: list[str] = []
        for path in reversed(changed):
            try:
                _replace_text_preserve_metadata(path, originals[path])
            except Exception as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        detail = (
            f"；回滚失败: {'; '.join(rollback_errors)}"
            if rollback_errors
            else ""
        )
        raise RuntimeError(f"同步 OVPN 连接配置失败: {exc}{detail}") from exc

    return {
        "total": len(originals) + missing,
        "changed": len(planned),
        "unchanged": len(originals) - len(planned),
        "missing": missing,
    }
