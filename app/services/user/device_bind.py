# -*- coding: utf-8 -*-
"""设备指纹绑定与验证服务

管理用户与设备的一对一绑定关系，确保每个 VPN 用户只能在指定设备上连接。
绑定记录以 JSON 文件形式持久化到 /etc/openvpn/mgmt/device_bindings/（见 constants.DEVICE_BINDINGS_DIR）。

实际放行逻辑由 OpenVPN client-connect 脚本 device-bind.sh 执行（绑定 JSON 的写入/更新由同目录
device_binding_json.py 完成）；策略模式见 SystemConfig.device_bind_mode 与 device_bind_policy.sync_device_bind_mode_file。
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.constants import DEVICE_BINDINGS_DIR
from app.models.device import DeviceBinding
from app.utils.file_lock import file_lock, write_json_atomic, read_json
from app.utils.posix_data_perms import fix_path_for_openvpn_shared_data

# IV_PLAT 常见取值 -> 用户页展示（与 OpenVPN Connect 等客户端一致）
_IV_PLAT_LABELS = {
    "android": "安卓",
    "ios": "iOS",
    "mac": "Mac",
    "macos": "Mac",
    "darwin": "Mac",
    "macosx": "Mac",
    "win": "Windows",
    "windows": "Windows",
    "linux": "Linux",
}


def format_iv_plat_display(raw: str | None) -> str:
    """
    将绑定记录中的 iv_plat 转为界面文案；空或未知则返回空字符串（未知非空时返回「其他(原始)」）。

    有设备绑定且脚本写入过 iv_plat 时，在线与离线展示一致。
    """
    if not raw or not str(raw).strip():
        return ""
    key = str(raw).strip().lower()
    if key in _IV_PLAT_LABELS:
        return _IV_PLAT_LABELS[key]
    return f"其他({raw.strip()})"


class DeviceBindingService:
    """设备绑定服务，提供绑定创建、验证、重置和查询功能。"""

    @staticmethod
    def _unlink_binding_json_and_lock(binding_file: Path) -> None:
        """删除绑定 JSON 及 read_json/write_json_atomic 使用的旁路锁文件（*.json.lock）。"""
        binding_file.unlink(missing_ok=True)
        Path(str(binding_file) + ".lock").unlink(missing_ok=True)

    def create_binding(
        self,
        username: str,
        fingerprint: str,
        source: str = "machine-id",
    ) -> DeviceBinding:
        """创建设备绑定记录，保存到 DEVICE_BINDINGS_DIR/{id}.json。

        如果用户已有绑定，会先移除旧绑定再创建新绑定。

        参数:
            username: 用户名
            fingerprint: 设备指纹字符串
            source: 指纹来源类型，默认 "machine-id"

        返回:
            创建成功的 DeviceBinding 实例
        """
        # 如果已存在旧绑定，先删除
        old_file = self._find_binding_file(username)
        if old_file is not None:
            self._unlink_binding_json_and_lock(old_file)

        # 创建新绑定
        binding = DeviceBinding(
            id=str(uuid.uuid4()),
            username=username,
            fingerprint=fingerprint,
            fingerprint_source=source,
            bound_at=datetime.now(timezone.utc).isoformat(),
        )

        # 确保目录存在并写入文件
        DEVICE_BINDINGS_DIR.mkdir(parents=True, exist_ok=True)
        file_path = DEVICE_BINDINGS_DIR / f"{binding.id}.json"
        write_json_atomic(file_path, binding.model_dump())
        fix_path_for_openvpn_shared_data(file_path)

        return binding

    def verify_binding(self, username: str, fingerprint: str) -> bool:
        """验证设备指纹是否与已绑定记录匹配。

        参数:
            username: 用户名
            fingerprint: 待验证的设备指纹

        返回:
            匹配返回 True，无绑定记录或不匹配返回 False
        """
        binding = self.get_binding(username)
        if binding is None:
            return False
        return binding.fingerprint == fingerprint

    def reset_binding(self, username: str) -> bool:
        """重置用户的设备绑定（管理员操作）。

        删除用户对应的绑定文件，使该用户可重新绑定新设备。

        参数:
            username: 用户名

        返回:
            成功删除返回 True，无绑定记录返回 False
        """
        binding_file = self._find_binding_file(username)
        if binding_file is None:
            return False

        self._unlink_binding_json_and_lock(binding_file)
        return True

    def get_binding(self, username: str) -> DeviceBinding | None:
        """获取用户的当前绑定记录。

        参数:
            username: 用户名

        返回:
            DeviceBinding 实例，无绑定时返回 None
        """
        binding_file = self._find_binding_file(username)
        if binding_file is None:
            return None

        data = read_json(binding_file)
        if not data:
            return None

        return DeviceBinding(**data)

    def build_user_binding_aux(self) -> dict[str, dict[str, str]]:
        """遍历绑定目录一次：username -> device_label、last_connected_since（原始字符串，可能为空）。"""
        result: dict[str, dict[str, str]] = {}
        if not DEVICE_BINDINGS_DIR.exists():
            return result
        for file_path in DEVICE_BINDINGS_DIR.glob("*.json"):
            try:
                data = read_json(file_path)
                if not data:
                    continue
                u = data.get("username")
                if not u:
                    continue
                lc = data.get("last_connected_since")
                result[str(u)] = {
                    "device_label": format_iv_plat_display(data.get("iv_plat")),
                    "last_connected_since": (str(lc).strip() if lc else ""),
                }
            except (json.JSONDecodeError, OSError):
                continue
        return result

    def _find_binding_file(self, username: str) -> Path | None:
        """按 username 字段搜索绑定文件。

        遍历 DEVICE_BINDINGS_DIR 下所有 JSON 文件，
        查找 username 字段匹配的记录。

        参数:
            username: 要搜索的用户名

        返回:
            匹配的文件路径，未找到返回 None
        """
        if not DEVICE_BINDINGS_DIR.exists():
            return None

        for file_path in DEVICE_BINDINGS_DIR.glob("*.json"):
            try:
                data = read_json(file_path)
                if data.get("username") == username:
                    return file_path
            except (json.JSONDecodeError, OSError):
                # 跳过损坏或无法读取的文件
                continue

        return None
