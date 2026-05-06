"""证书生命周期管理服务 — 解析、过滤、吊销、续签、告警更新"""
import json
import logging
import signal
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.core.config import load_config
from app.core.constants import CERT_EXPIRY_WARN_DAYS, ALERTS_FILE
from app.models.cert import Certificate
from app.services.easyrsa.wrapper import EasyRSAWrapper, EasyRSAError
from app.utils.audit_log import AuditLogger
from app.utils.posix_data_perms import sync_openvpn_runtime_permissions_from_config

logger = logging.getLogger(__name__)


class CertService:
    """证书生命周期管理：列表、到期检测、吊销、续签、告警更新"""

    def __init__(self):
        self._config = load_config()
        self._audit = AuditLogger()

    # ---- 内部辅助 ----

    def _get_wrapper(self) -> EasyRSAWrapper:
        """根据当前配置构造 EasyRSA 封装实例"""
        easy = str(self._config.easyrsa_dir or "").strip()
        pki = str(self._config.pki_dir or "").strip()
        if not easy or not pki:
            raise ValueError("EasyRSA 脚本路径或 PKI 目录未配置，请检查系统配置")
        return EasyRSAWrapper(easy, pki)

    def _get_index_path(self) -> Path:
        """返回 index.txt 文件路径"""
        pki = str(self._config.pki_dir or "").strip()
        if not pki:
            raise ValueError("PKI 目录未配置，请检查系统配置")
        return Path(pki) / "index.txt"

    @staticmethod
    def _parse_easyrsa_date(date_str: str) -> datetime:
        """解析 EasyRSA index.txt 中的日期格式 YYMMDDHHMMSSZ。

        例如 '250615120000Z' 表示 2025-06-15T12:00:00 UTC。
        两位年份规则：>= 70 视为 19xx，< 70 视为 20xx。
        """
        # 去除末尾 Z
        s = date_str.rstrip("Z")
        if len(s) != 12:
            raise ValueError(f"无效的 EasyRSA 日期格式: {date_str}")

        yy = int(s[0:2])
        year = 2000 + yy if yy < 70 else 1900 + yy

        return datetime(
            year=year,
            month=int(s[2:4]),
            day=int(s[4:6]),
            hour=int(s[6:8]),
            minute=int(s[8:10]),
            second=int(s[10:12]),
            tzinfo=timezone.utc,
        )

    @staticmethod
    def _extract_cn(dn: str) -> str:
        """从 DN 字符串中提取 CN 字段。

        index.txt 的 DN 格式通常为 /CN=username 或 /C=xx/.../CN=username。
        """
        for part in dn.split("/"):
            if part.startswith("CN="):
                return part[3:]
        return dn

    # ---- 公开接口 ----

    def list_all(self) -> list[dict]:
        """从 EasyRSA PKI 目录解析 index.txt 获取所有证书状态。

        index.txt 每行格式:
            Status\tExpiry\t[RevocationDate]\tSerial\tunknown\tCN_DN
        Status: V=valid, R=revoked, E=expired
        Expiry 格式: YYMMDDHHMMSSZ (如 250615120000Z)

        返回 Certificate 模型的 dict 列表。
        """
        index_path = self._get_index_path()
        if not index_path.exists():
            logger.warning("index.txt 不存在: %s", index_path)
            return []

        results: list[dict] = []
        status_map = {"V": "valid", "R": "revoked", "E": "expired"}

        for line in index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue

            parts = line.split("\t")
            if len(parts) < 6:
                logger.debug("跳过格式不完整的行: %s", line)
                continue

            status_char = parts[0]
            expiry_str = parts[1]
            revocation_str = parts[2]  # 仅在 R 状态时有值
            serial = parts[3]
            # parts[4] 是 unknown 字段
            dn = parts[5]

            cn = self._extract_cn(dn)
            status = status_map.get(status_char, "unknown")

            # 解析到期时间
            try:
                expires_at = self._parse_easyrsa_date(expiry_str)
            except ValueError:
                logger.warning("无法解析证书到期日期: cn=%s, date=%s", cn, expiry_str)
                continue

            # 解析���销时间（仅 R 状态）
            revoked_at = None
            if status_char == "R" and revocation_str:
                try:
                    revoked_at = self._parse_easyrsa_date(revocation_str).isoformat()
                except ValueError:
                    pass

            # 检查有效证书是否实际已过期
            if status == "valid" and expires_at <= datetime.now(timezone.utc):
                status = "expired"

            cert = Certificate(
                serial=serial,
                common_name=cn,
                issued_at="",  # index.txt 不含签发时间，留空
                expires_at=expires_at.isoformat(),
                status=status,
                revoked_at=revoked_at,
            )
            results.append(cert.model_dump())

        return results

    def get_expiring(self, days: int = CERT_EXPIRY_WARN_DAYS) -> list[dict]:
        """过滤出 expires_at - now() <= days 天内到期的有效证书"""
        now = datetime.now(timezone.utc)
        threshold = now + timedelta(days=days)

        expiring: list[dict] = []
        for cert in self.list_all():
            if cert["status"] != "valid":
                continue
            try:
                expires_at = datetime.fromisoformat(cert["expires_at"])
            except (ValueError, TypeError):
                continue
            if expires_at <= threshold:
                expiring.append(cert)

        return expiring

    def revoke(self, username: str) -> bool:
        """吊销证书：调用 EasyRSAWrapper.revoke -> gen_crl，
        然后通知所有 OpenVPN 实例重载 CRL（发送 SIGHUP）。

        返回 True 表示成功，失败时记录审计日志并返回 False。
        """
        wrapper = self._get_wrapper()

        try:
            # 吊销证书
            wrapper.revoke(username)
            # 重新生成 CRL
            wrapper.gen_crl()
        except EasyRSAError as e:
            logger.error("吊销证书失败: cn=%s, error=%s", username, e)
            self._audit.log(
                action="revoke_cert",
                target_type="cert",
                target_id=username,
                detail=f"吊销用户 {username} 的证书",
                result="failure",
                error_message=str(e),
            )
            return False

        sync_openvpn_runtime_permissions_from_config()

        # 通知 OpenVPN 实例重载 CRL（向 openvpn 进程发送 SIGHUP）
        self._reload_openvpn_instances()

        self._audit.log(
            action="revoke_cert",
            target_type="cert",
            target_id=username,
            detail=f"已吊销用户 {username} 的证书并更新 CRL",
            result="success",
        )
        logger.info("证书已吊销: cn=%s", username)
        return True

    def renew(self, username: str) -> bool:
        """续签证书：调用 EasyRSAWrapper.renew 并重新生成 .ovpn 文件。

        返回 True 表示成功，失败时记录审计日志并返回 False。
        """
        wrapper = self._get_wrapper()

        try:
            wrapper.renew(username)
        except EasyRSAError as e:
            logger.error("续签证书失败: cn=%s, error=%s", username, e)
            self._audit.log(
                action="renew_cert",
                target_type="cert",
                target_id=username,
                detail=f"续签用户 {username} 的证书",
                result="failure",
                error_message=str(e),
            )
            return False

        info = wrapper.get_cert_info(username)
        if info and info.get("serial"):
            from app.services.user.crud import UserService

            try:
                UserService().update_cert_serial(username, str(info["serial"]))
                logger.info("已同步用户证书序列号: user=%s, serial=%s", username, info["serial"])
            except ValueError:
                logger.info("跳过用户序列号同步（无对应用户记录，可能为服务端证书）: %s", username)

        sync_openvpn_runtime_permissions_from_config()

        self._audit.log(
            action="renew_cert",
            target_type="cert",
            target_id=username,
            detail=f"已续签用户 {username} 的证书",
            result="success",
        )
        logger.info("证书已续签: cn=%s", username)
        return True

    def check_and_update_alerts(self):
        """检查到期证书并更新 data/alerts.json，供仪表盘读取。

        告警数据结构:
        {
            "cert_expiry_alerts": [
                {"common_name": "...", "expires_at": "...", "days_remaining": N}
            ],
            "updated_at": "ISO8601"
        }
        """
        expiring = self.get_expiring(days=CERT_EXPIRY_WARN_DAYS)

        now = datetime.now(timezone.utc)
        alerts = []
        for cert in expiring:
            try:
                expires_at = datetime.fromisoformat(cert["expires_at"])
                days_remaining = max(0, (expires_at - now).days)
            except (ValueError, TypeError):
                days_remaining = 0

            alerts.append({
                "common_name": cert["common_name"],
                "expires_at": cert["expires_at"],
                "days_remaining": days_remaining,
            })

        data = {
            "cert_expiry_alerts": alerts,
            "updated_at": now.isoformat(),
        }

        # 原子写入 alerts.json
        ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = ALERTS_FILE.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path.replace(ALERTS_FILE)
        except OSError as e:
            logger.error("更新告警文件失败: %s", e)
            return

        if alerts:
            logger.info("发现 %d 个即将到期的证书", len(alerts))
        else:
            logger.debug("无即将到期的证书")

    # ---- 私有方法 ----

    def _reload_openvpn_instances(self):
        """向所有运行中的 OpenVPN 实例发送 SIGHUP 信号以重载 CRL。

        通过 systemctl reload 或直接查找 openvpn 进程并发送信号。
        """
        try:
            # 使用 pgrep 查找所有 openvpn 进程 PID
            result = subprocess.run(
                ["pgrep", "-x", "openvpn"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                logger.debug("未发现运行中的 OpenVPN 进程")
                return

            pids = result.stdout.strip().splitlines()
            for pid_str in pids:
                pid = int(pid_str.strip())
                try:
                    # 发送 SIGHUP 让 OpenVPN 重载 CRL
                    subprocess.run(
                        ["kill", "-HUP", str(pid)],
                        check=True, capture_output=True, text=True,
                    )
                    logger.debug("已向 OpenVPN 进程 %d 发送 SIGHUP", pid)
                except subprocess.CalledProcessError:
                    logger.warning("向 OpenVPN 进程 %d 发送 SIGHUP 失败", pid)

        except (OSError, ValueError) as e:
            logger.warning("重载 OpenVPN 实例失败: %s", e)
