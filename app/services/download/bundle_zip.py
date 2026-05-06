# -*- coding: utf-8 -*-
"""将多个 .ovpn 打成 zip，供单次下载链接指向（与 link_mgr 配合）。"""

import re
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.constants import DOWNLOAD_BUNDLES_DIR


def safe_bundle_filename_prefix(prefix: str) -> str:
    """用户前缀转为安全文件名片段（去路径与非法字符）。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", prefix.strip()).strip("._")
    return (s or "vpn_bundle")[:80]


def build_ovpn_zip(entries: list[tuple[str, Path]], bundle_prefix: str) -> tuple[Path, str]:
    """
    将多个用户 ovpn 写入同一 zip。

    参数:
        entries: (VPN 用户名, 磁盘上 .ovpn 文件路径)
        bundle_prefix: 用于压缩包命名前缀（通常与 API 用户名前缀一致）

    返回:
        (zip 绝对路径, 下载文件名，含 .zip)

    异常:
        FileNotFoundError: 某条目路径不是文件
        OSError: 无法创建目录或写入 zip
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = safe_bundle_filename_prefix(bundle_prefix)
    stem = f"{base}_{ts}"
    DOWNLOAD_BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

    download_name = ""
    zip_path: Path | None = None
    for attempt in range(200):
        suffix = "" if attempt == 0 else f"_{attempt}"
        candidate_name = f"{stem}{suffix}.zip"
        candidate = DOWNLOAD_BUNDLES_DIR / candidate_name
        if not candidate.exists():
            download_name = candidate_name
            zip_path = candidate
            break
    if zip_path is None:
        download_name = f"{stem}_{secrets.token_hex(8)}.zip"
        zip_path = DOWNLOAD_BUNDLES_DIR / download_name

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for un, src in entries:
            p = Path(src)
            if not p.is_file():
                raise FileNotFoundError(f"ovpn 文件不存在: {p}")
            zf.write(p, arcname=f"{un}.ovpn")

    return zip_path, download_name
