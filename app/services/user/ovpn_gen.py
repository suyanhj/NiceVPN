# -*- coding: utf-8 -*-
"""用户 .ovpn 配置文件生成服务

根据用户证书和服务器配置，构造完整的 .ovpn 客户端配置文件。
所有证书和密钥以内联块嵌入，不引用外部文件路径。
"""

from pathlib import Path

from app.core.constants import OVPN_PROFILES_DIR


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

    # 构造标准 OpenVPN 客户端配置
    lines = [
        "# OpenVPN 客户端配置文件",
        f"# 用户: {username}",
        f"# 组子网: {group_subnet}",
        "#",
        "# 此文件由系统自动生成，请勿手动修改",
        "",
        "client",
        "dev tun",
        f"proto {proto}",
        f"remote {server_ip} {port}",
        "",
        "resolv-retry infinite",
        "nobind",
        "persist-key",
        "persist-tun",
        "",
        "remote-cert-tls server",
        "cipher AES-256-GCM",
        "auth SHA256",
        "verb 3",
        "key-direction 1",
        "",
        "# MTU 优化",
        "tun-mtu 1400",
        "mssfix 1360",
        "",
        "# 密钥重协商周期（7天）",
        "reneg-sec 604800",
        "",
        "# 推送客户端信息用于设备绑定检查",
        "push-peer-info",
        "",
        "# ---- 内嵌证书与密钥 ----",
        "",
        "# CA 证书",
        "<ca>",
        ca_cert,
        "</ca>",
        "",
        "# 用户证书",
        "<cert>",
        user_cert,
        "</cert>",
        "",
        "# 用户私钥",
        "<key>",
        user_key,
        "</key>",
        "",
        "# TLS-Crypt-V2 客户端密钥",
        "<tls-crypt-v2>",
        tc2_client_key,
        "</tls-crypt-v2>",
        "",
    ]

    return "\n".join(lines)


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
