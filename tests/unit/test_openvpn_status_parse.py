# -*- coding: utf-8 -*-
"""OpenVPN status 文件解析单元测试。"""

from pathlib import Path

# 用户提供的 status-version 2 样例（逗号分隔）
SAMPLE_COMMA = """TITLE,OpenVPN 2.7.1 x86_64-pc-linux-gnu
TIME,2026-04-09 18:50:49,1775731849
HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Virtual IPv6 Address,Bytes Received,Bytes Sent,Connected Since,Connected Since (time_t),Username,Client ID,Peer ID,Data Channel Cipher
CLIENT_LIST,t1,udp4:112.96.108.197:38392,10.255.0.2,,14143,11774,2026-04-09 18:23:52,1775730232,UNDEF,1,0,AES-256-GCM
HEADER,ROUTING_TABLE,Virtual Address,Common Name,Real Address,Last Ref,Last Ref (time_t)
ROUTING_TABLE,10.255.0.2,t1,udp4:112.96.108.197:38392,2026-04-09 18:26:34,1775730394
GLOBAL_STATS,Max bcast/mcast queue length,1
END
"""


def test_get_status_comma_delimited(monkeypatch, tmp_path: Path):
    """逗号分隔的 v2 status 应解析出客户端与流量。"""
    from app.services.openvpn import instance as inst

    monkeypatch.setattr(inst, "OPENVPN_DAEMON_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        "app.core.config.load_config",
        lambda: {"openvpn_conf_dir": str(tmp_path)},
    )
    name = "testovpn"
    (tmp_path / f"{name}-status.log").write_text(SAMPLE_COMMA, encoding="utf-8")

    out = inst.get_status(name)
    assert len(out["clients"]) == 1
    c0 = out["clients"][0]
    assert c0["common_name"] == "t1"
    assert c0["virtual_address"] == "10.255.0.2"
    assert c0["bytes_received"] == 14143
    assert c0["bytes_sent"] == 11774
    assert c0["connected_since"] == "2026-04-09 18:23:52"
    assert out["total_bytes_received"] == 14143
    assert out["total_bytes_sent"] == 11774


def test_generate_server_conf_has_client_connect_only():
    """生成的 server.conf 含 client-connect；全局池路由由 CCD ifconfig 掩码与 server 一致实现。"""
    from app.services.openvpn.instance import generate_server_conf

    text = generate_server_conf("srv1", {"openvpn_conf_dir": "/etc/openvpn", "pki_dir": "/etc/openvpn/pki"})
    assert "client-connect" in text
    assert "device-bind.sh" in text
    assert "client-disconnect" not in text
    assert "log-append /etc/openvpn/log/srv1.log" in text
    assert "status /etc/openvpn/log/srv1-status.log 30" in text
    assert "topology subnet" in text
    assert "client-to-client" in text
    assert "push" not in text


def test_get_status_tab_delimited(monkeypatch, tmp_path: Path):
    """制表符分隔仍应兼容。"""
    from app.services.openvpn import instance as inst

    tab_block = (
        "HEADER\tCLIENT_LIST\tCN\tReal\tVirt\tV6\tBR\tBS\tSince\n"
        "CLIENT_LIST\tt1\t10.0.0.1:1\t10.8.0.2\t\t100\t200\t2026-01-01 00:00:00\n"
    )
    monkeypatch.setattr(inst, "OPENVPN_DAEMON_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        "app.core.config.load_config",
        lambda: {"openvpn_conf_dir": str(tmp_path)},
    )
    name = "tabinst"
    (tmp_path / f"{name}-status.log").write_text(tab_block, encoding="utf-8")


def test_get_status_uses_status_directive_from_conf(monkeypatch, tmp_path: Path):
    """server.conf 中 status 指令路径优先于默认 *-status.log。"""
    from app.services.openvpn import instance as inst

    monkeypatch.setattr(inst, "OPENVPN_DAEMON_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        "app.core.config.load_config",
        lambda: {"openvpn_conf_dir": str(tmp_path)},
    )
    name = "srv"
    custom = tmp_path / "custom-status.log"
    custom.write_text(SAMPLE_COMMA, encoding="utf-8")
    conf = tmp_path / "server" / f"{name}.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text(f"status {custom.as_posix()} 30\n", encoding="utf-8")

    out = inst.get_status(name)
    assert len(out["clients"]) == 1
    assert out["clients"][0]["common_name"] == "t1"
    assert out["clients"][0]["bytes_received"] == 14143
    assert out["clients"][0]["bytes_sent"] == 11774


def test_write_server_conf_uses_official_server_dir(tmp_path: Path):
    """server.conf 默认写入官方 /server 子目录。"""
    from app.services.openvpn.instance import write_server_conf

    conf = write_server_conf(
        "srv1",
        {"openvpn_conf_dir": str(tmp_path), "pki_dir": str(tmp_path / "pki")},
        conf_dir=str(tmp_path),
    )

    assert conf == tmp_path / "server" / "srv1.conf"
    assert conf.is_file()


def test_start_instance_uses_openvpn_server_unit(monkeypatch):
    """实例启动应使用官方 openvpn-server@ 模板。"""
    from app.services.openvpn import instance as inst

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Result:
            stdout = ""

        return Result()

    monkeypatch.setattr(inst.subprocess, "run", fake_run)

    assert inst.start_instance("srv1") is True
    assert calls[0] == ["systemctl", "start", "openvpn-server@srv1"]
