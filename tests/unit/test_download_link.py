# -*- coding: utf-8 -*-
"""一次性下载链接单元测试"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.services.download.link_mgr import create_link, consume_link
import app.core.constants as constants


@pytest.fixture(autouse=True)
def mock_download_dir(tmp_path, monkeypatch):
    """将 DOWNLOAD_LINKS_DIR 替换为临时目录，避免污染真实 data/ 目录"""
    monkeypatch.setattr(constants, "DOWNLOAD_LINKS_DIR", tmp_path)
    return tmp_path


class TestCreateLink:
    """测试下载链接创建"""

    def test_creates_token_file(self, tmp_path):
        """验证调用 create_link 后在目录中创建了 JSON 令牌文件"""
        url = create_link("alice", "/etc/openvpn/alice.ovpn", "https://vpn.example.com")

        # 提取令牌（URL 最后一段）
        token = url.rsplit("/", 1)[-1]
        token_file = tmp_path / f"{token}.json"

        assert token_file.exists(), "令牌文件应已创建"

        # 验证文件内容结构
        data = json.loads(token_file.read_text(encoding="utf-8"))
        assert data["username"] == "alice"
        assert data["file_path"] == "/etc/openvpn/alice.ovpn"
        assert data["used"] is False
        assert "expires_at" in data

    def test_url_format(self, tmp_path):
        """验证返回的 URL 格式正确"""
        url = create_link("bob", "/etc/openvpn/bob.ovpn", "https://vpn.example.com")

        assert url.startswith("https://vpn.example.com/download/")
        # 令牌部分不为空
        token = url.rsplit("/", 1)[-1]
        assert len(token) > 0

        # 去除 base_url 末尾斜杠的场景
        url2 = create_link("bob", "/etc/openvpn/bob.ovpn", "https://vpn.example.com/")
        assert url2.startswith("https://vpn.example.com/download/")
        assert "//" not in url2.replace("https://", "")


class TestConsumeLink:
    """测试下载链接消费"""

    def test_valid_consume(self, tmp_path):
        """正常消费：首次使用返回文件路径"""
        url = create_link("alice", "/etc/openvpn/alice.ovpn", "https://vpn.example.com")
        token = url.rsplit("/", 1)[-1]

        result = consume_link(token)

        assert result is not None
        assert result["error"] is None
        assert result["username"] == "alice"
        assert result["file_path"] == "/etc/openvpn/alice.ovpn"
        assert result.get("download_filename") is None

    def test_consume_custom_filename(self, tmp_path):
        """令牌带 download_filename 时原样返回，供 zip 等下载名"""
        url = create_link(
            "batch",
            "/data/bundle.zip",
            "https://vpn.example.com",
            download_filename="lisi_20260101_120000.zip",
        )
        token = url.rsplit("/", 1)[-1]
        result = consume_link(token)
        assert result is not None
        assert result["error"] is None
        assert result["download_filename"] == "lisi_20260101_120000.zip"
        """二次消费：返回已使用错误"""
        url = create_link("alice", "/etc/openvpn/alice.ovpn", "https://vpn.example.com")
        token = url.rsplit("/", 1)[-1]

        # 第一次消费
        consume_link(token)

        # 第二次消费应返回错误
        result = consume_link(token)
        assert result is not None
        assert result["error"] is not None
        assert "已被使用" in result["error"] or "already_used" in result["error"]

    def test_expired(self, tmp_path, monkeypatch):
        """过期令牌：返回过期错误"""
        # 将过期时间设为 1 秒
        monkeypatch.setattr(constants, "DOWNLOAD_LINK_EXPIRE_SECONDS", 1)

        url = create_link("alice", "/etc/openvpn/alice.ovpn", "https://vpn.example.com")
        token = url.rsplit("/", 1)[-1]

        # 修改令牌文件，将 expires_at 设为过去时间
        token_file = tmp_path / f"{token}.json"
        data = json.loads(token_file.read_text(encoding="utf-8"))
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        data["expires_at"] = past_time
        token_file.write_text(json.dumps(data), encoding="utf-8")

        result = consume_link(token)
        assert result is not None
        assert result["error"] is not None
        assert "过期" in result["error"] or "expired" in result["error"]

    def test_not_found(self):
        """不存在的令牌：返回 None"""
        result = consume_link("nonexistent_token_abc123")
        assert result is None
