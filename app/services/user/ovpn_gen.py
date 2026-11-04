# -*- coding: utf-8 -*-
"""用户 .ovpn 配置文件生成服务

根据用户证书和服务器配置，构造完整的 .ovpn 客户端配置文件。
所有证书和密钥以内联块嵌入，不引用外部文件路径。
"""

from pathlib import Path

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
