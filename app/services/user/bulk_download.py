# -*- coding: utf-8 -*-
"""用户批量下载服务 — ZIP 打包多个 .ovpn 文件"""
import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path

from app.core.constants import OVPN_PROFILES_DIR, USERS_DIR
from app.utils.file_lock import read_json

logger = logging.getLogger(__name__)


class BulkDownloadService:
    def create_zip(self, usernames: list[str]) -> tuple[bytes, list[str], str]:
        """
        将选中用户的 .ovpn 文件打包为 ZIP。
        返回 (zip_bytes, warnings_list, zip_filename)。
        zip_filename 格式: vpn-configs-YYYYMMDD-HHMMSS.zip
        """
        warnings = []
        buf = io.BytesIO()
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        zip_filename = f"vpn-configs-{ts}.zip"

        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for username in usernames:
                ovpn_path = self._find_ovpn(username)
                if not ovpn_path:
                    msg = f"用户 '{username}' 的 .ovpn 文件不存在"
                    logger.warning(msg)
                    warnings.append(msg)
                    continue

                try:
                    content = ovpn_path.read_bytes()
                    if not content:
                        msg = f"用户 '{username}' 的 .ovpn 文件为空"
                        logger.warning(msg)
                        warnings.append(msg)
                        continue
                    zf.writestr(f"{username}.ovpn", content)
                except Exception as e:
                    msg = f"用户 '{username}' 的 .ovpn 文件读取失败：{e}"
                    logger.warning(msg)
                    warnings.append(msg)

        return buf.getvalue(), warnings, zip_filename

    @staticmethod
    def _find_ovpn(username: str) -> Path | None:
        """查找用户的 .ovpn：现行目录、旧版 data/users、JSON 内路径。"""
        p0 = OVPN_PROFILES_DIR / f"{username}.ovpn"
        if p0.exists():
            return p0
        p1 = USERS_DIR / f"{username}.ovpn"
        if p1.exists():
            return p1
        p2 = USERS_DIR / username / f"{username}.ovpn"
        if p2.exists():
            return p2
        user_json = USERS_DIR / f"{username}.json"
        if user_json.exists():
            data = read_json(user_json)
            if data and data.get("ovpn_file_path"):
                p3 = Path(data["ovpn_file_path"])
                if p3.exists():
                    return p3
        return None
