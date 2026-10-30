# -*- coding: utf-8 -*-
"""用户 CRUD 服务 — 用户创建、删除、查询，含证书和配置文件全流程"""
import logging
import socket
import threading
import uuid
from datetime import datetime, timezone
from ipaddress import IPv4Network
from pathlib import Path

from app.core.config import load_config
from app.core.constants import CCD_DIR
from app.models.user import User
from app.utils.audit_log import AuditLogger
from app.storage.sqlite_store import SqliteStore, get_default_store
from app.utils.posix_data_perms import (
    fix_path_for_openvpn_shared_data,
    sync_openvpn_runtime_permissions_from_config,
)

logger = logging.getLogger(__name__)
_USER_CREATE_LOCK = threading.Lock()


def _ifconfig_push_netmask(group_subnet: IPv4Network) -> str:
    """ifconfig-push 第二参数，须与 server 行（global_subnet）网段一致；组子网仅用于选 IP 地址。

    若此处用组子网的细掩码（如 /24），再 push 整池路由时 Linux 会尝试
    ``via <服务端隧道 IP>``，该网关与 TUN 上 /24 不同网段，会报 ``Network is unreachable``。
    """
    cfg = load_config()
    gs = str(cfg.get("global_subnet") or "").strip()
    if not gs:
        return str(group_subnet.netmask)
    try:
        pool = IPv4Network(gs, strict=False)
    except ValueError:
        logger.warning("global_subnet 非法，ifconfig-push 回退为组子网掩码: %r", gs)
        return str(group_subnet.netmask)
    return str(pool.netmask)


class UserService:
    """用户全生命周期管理服务"""

    def __init__(self, store: SqliteStore | None = None):
        self.audit = AuditLogger()
        self.store = store or get_default_store()

    def create(self, username: str, group_id: str,
               password_enabled: bool = False,
               password: str | None = None) -> User:
        """
        创建用户完整流程：
        1. 校验用户名唯一性和组存在性
        2. 调用 EasyRSA 生成证书和 tls-crypt-v2 客户端密钥
        3. 生成 .ovpn 配置文件（内联所有证书和密钥）
        4. 写入 CCD 文件（固定 IP 分配）
        5. 保存用户 JSON 记录
        6. 写入审计日志
        """
        with _USER_CREATE_LOCK:
            return self._create_locked(username, group_id, password_enabled, password)

    def _create_locked(self, username: str, group_id: str,
                       password_enabled: bool = False,
                       password: str | None = None) -> User:
        """在全局创建锁内执行用户创建，避免并发写 PKI 和 CCD。"""
        # 校验用户名唯一性
        if self._user_exists(username):
            raise ValueError(f"用户名已存在: {username}")

        # 校验组存在性
        group = self._load_group(group_id)
        if not group:
            raise ValueError(f"用户组不存在: {group_id}")

        if password_enabled and not str(password or ""):
            raise ValueError("启用客户端私钥密码时必须填写密码")

        config = load_config()
        now = datetime.now(timezone.utc).isoformat()

        # 生成证书
        cert_serial = None
        ovpn_path = None
        try:
            from app.services.easyrsa.wrapper import EasyRSAWrapper
            wrapper = EasyRSAWrapper(config.easyrsa_dir, config.pki_dir)

            # 生成证书请求并签署；可选密码仅用于加密客户端私钥。
            wrapper.gen_req(
                username,
                passphrase=password if password_enabled else None,
            )
            wrapper.sign_req("client", username)

            # 生成 tls-crypt-v2 客户端密钥
            tc2_server_key = str(Path(config.pki_dir).parent / "tc2-server.key")
            tc2_client_key = wrapper.gen_tls_crypt_v2_client(username, tc2_server_key)

            # 读取证书信息
            cert_info = wrapper.get_cert_info(username)
            cert_serial = cert_info["serial"] if cert_info else None

            ovpn_file = self._write_ovpn_from_current_material(
                username,
                group,
                config,
                tc2_client_key_path=tc2_client_key,
            )
            ovpn_path = str(ovpn_file)

            sync_openvpn_runtime_permissions_from_config()

        except Exception as e:
            self.audit.log("create_user", "user", username,
                           {"error": str(e), "group_id": group_id}, "failure")
            raise

        # 写入 CCD 文件（固定 IP 分配）
        self._write_ccd(username, group)

        # 客户端私钥密码只在 EasyRSA gen-req 时使用，不由 NiceVPN 保存。
        # password_hash 字段仅为旧数据兼容保留，新用户始终写空。
        password_hash = None

        # 构造用户对象
        user = User(
            username=username,
            group_id=group_id,
            password_enabled=password_enabled,
            password_hash=password_hash,
            status="active",
            ovpn_file_path=ovpn_path,
            cert_serial=cert_serial,
            created_at=now,
            updated_at=now,
        )

        # 保存结构化用户元数据
        self.store.upsert_user(user.model_dump())

        # 更新组的用户计数
        self._increment_group_user_count(group_id, 1)

        self.audit.log("create_user", "user", username,
                       {"group_id": group_id}, "success")
        from app.services.peer_instance.service import PeerService

        PeerService().sync_all_mesh_push_routes_in_ccd()
        return user

    def _write_ovpn_from_current_material(
        self,
        username: str,
        group: dict,
        config,
        *,
        tc2_client_key_path: str | Path | None = None,
    ) -> Path:
        """从当前 PKI 与系统配置生成并覆盖用户 OVPN。"""

        pki = Path(str(config.pki_dir))
        tc2_path = (
            Path(tc2_client_key_path)
            if tc2_client_key_path is not None
            else pki / "tc2-clients" / f"{username}.key"
        )
        material = {
            "ca_cert": pki / "ca.crt",
            "user_cert": pki / "issued" / f"{username}.crt",
            "user_key": pki / "private" / f"{username}.key",
            "tc2_client_key": tc2_path,
        }
        missing = [str(path) for path in material.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "重建 OVPN 缺少证书或密钥文件: " + ", ".join(missing)
            )

        from app.services.user.ovpn_gen import generate_ovpn, save_ovpn

        ovpn_content = generate_ovpn(
            username,
            str(group.get("subnet") or ""),
            {
                "server_ip": config.get("server_ip", "0.0.0.0"),
                "port": config.get("port", 1194),
                "proto": config.get("proto", "udp"),
                "ca_cert": material["ca_cert"].read_text(encoding="utf-8"),
                "user_cert": material["user_cert"].read_text(encoding="utf-8"),
                "user_key": material["user_key"].read_text(encoding="utf-8"),
                "tc2_client_key": material["tc2_client_key"].read_text(
                    encoding="utf-8"
                ),
            },
        )
        return save_ovpn(username, ovpn_content)

    def regenerate_ovpn(self, username: str) -> str:
        """按当前 PKI、组和系统配置重新生成用户 OVPN，并同步记录路径。"""

        user = self._load_user(username)
        if not user or user.get("status") == "deleted":
            raise ValueError(f"用户不存在或已删除: {username}")

        group_id = str(user.get("group_id") or "").strip()
        group = self._load_group(group_id)
        if not group:
            raise ValueError(f"用户组不存在: {group_id}")

        ovpn_file = self._write_ovpn_from_current_material(
            username,
            group,
            load_config(),
        )
        user["ovpn_file_path"] = str(ovpn_file)
        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.upsert_user(user)
        sync_openvpn_runtime_permissions_from_config()
        logger.info("已按当前 PKI 重建用户 OVPN: user=%s path=%s", username, ovpn_file)
        return str(ovpn_file)

    def delete(self, username: str) -> bool:
        """
        删除用户完整流程：
        1. 吊销证书并更新 CRL（服务端凭 CRL 拒绝已吊销证书，与是否删除磁盘文件无关）
        2. 若 index 中为已吊销或查无该 CN，则删除 PKI 下私钥/req/issued 残留/tc2 客户端密钥
        3. 删除 .ovpn 配置文件
        4. 删除 CCD 文件
        5. 删除设备绑定
        6. 标记用户为 deleted
        """
        user_data = self._load_user(username)
        if not user_data:
            raise ValueError(f"用户不存在: {username}")

        config = load_config()

        # 吊销 + CRL：连接是否被拒绝取决于服务端 crl-verify 与最新 crl.pem
        # EasyRSA revoke 会更新 index.txt，通常不会删除 private/*.key，需显式清理
        from app.services.easyrsa.wrapper import EasyRSAError, EasyRSAWrapper

        wrapper = EasyRSAWrapper(config.easyrsa_dir, config.pki_dir)
        try:
            wrapper.revoke(username)
        except EasyRSAError as exc:
            logger.warning(
                "删除用户：吊销证书命令失败（可能已吊销或缺少 issued 证书）user=%s err=%s",
                username,
                exc,
            )

        try:
            wrapper.gen_crl()
        except EasyRSAError as exc:
            logger.exception("删除用户：生成 CRL 失败 user=%s", username)
            raise RuntimeError(
                f"无法更新 CRL，用户 {username} 删除流程已中止；请检查 EasyRSA 与 PKI 目录权限。"
            ) from exc

        sync_openvpn_runtime_permissions_from_config()

        cert_info = wrapper.get_cert_info(username)
        if cert_info is None or cert_info.get("status") == "revoked":
            serial = (cert_info or {}).get("serial") or user_data.get("cert_serial")
            self._remove_user_pki_disk_files(Path(config.pki_dir), username, serial)
        else:
            logger.error(
                "删除用户：index 中证书仍为有效，未删除 PKI 私钥等文件，请排查吊销是否成功。user=%s",
                username,
            )

        # 删除 .ovpn 文件
        ovpn_path = user_data.get("ovpn_file_path")
        if ovpn_path and Path(ovpn_path).exists():
            Path(ovpn_path).unlink()

        # 删除 CCD 文件
        ccd_file = CCD_DIR / username
        if ccd_file.exists():
            ccd_file.unlink()

        # 删除设备绑定
        from app.services.user.device_bind import DeviceBindingService
        DeviceBindingService().reset_binding(username)

        # 更新用户状态为 deleted
        user_data["status"] = "deleted"
        user_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.upsert_user(user_data)

        # 减少组用户计数
        self._increment_group_user_count(user_data.get("group_id", ""), -1)

        self.audit.log("delete_user", "user", username, {}, "success")
        return True

    def _remove_user_pki_disk_files(
        self,
        pki_dir: Path,
        username: str,
        cert_serial: str | None = None,
    ) -> None:
        """删除 PKI 下该用户的客户端私钥、CSR、仍留在 issued 的证书、tls-crypt-v2 客户端密钥。

        仅在 index 中已为吊销状态或查无该 CN 时调用（避免在仍有效时删掉导致无法补救）。
        EasyRSA revoke 可能在 revoked 下按序列号归档：certs_by_serial、private_by_serial、
        reqs_by_serial；旧布局另有 revoked/certs。issued 下 CN 同名残留一并删除。

        上述按序列号目录均需传入 serial（与 index.txt 一致）。
        """
        rels = [
            pki_dir / "private" / f"{username}.key",
            pki_dir / "reqs" / f"{username}.req",
            pki_dir / "issued" / f"{username}.crt",
            pki_dir / "tc2-clients" / f"{username}.key",
        ]
        for path in rels:
            if path.is_file():
                path.unlink()
                logger.info("已删除用户 PKI 残留文件: %s", path)

        if cert_serial:
            raw = str(cert_serial).strip()
            if raw:
                # (目录, 该目录下可能的扩展名)
                serial_targets: list[tuple[Path, tuple[str, ...]]] = [
                    (pki_dir / "certs_by_serial", (".crt", ".pem")),
                    (pki_dir / "revoked" / "certs_by_serial", (".crt", ".pem")),
                    (pki_dir / "revoked" / "private_by_serial", (".key", ".pem")),
                    (pki_dir / "revoked" / "reqs_by_serial", (".req",)),
                    (pki_dir / "revoked" / "certs", (".crt", ".pem")),
                ]
                for base, exts in serial_targets:
                    for name in {raw, raw.upper()}:
                        for ext in exts:
                            p = base / f"{name}{ext}"
                            if p.is_file():
                                p.unlink()
                                logger.info("已删除用户 PKI 按序列号文件: %s", p)

    def toggle_status(self, username: str) -> str:
        """切换用户启用/停用状态，返回新状态。

        停用时：CCD 文件写入 disable 指令 + 通过管理接口踢人下线
        启用时：CCD 文件移除 disable 指令
        """
        data = self._load_user(username)
        if not data:
            raise ValueError(f"用户不存在: {username}")
        if data.get("status") == "deleted":
            raise ValueError(f"用户已删除: {username}")

        new_status = "disabled" if data["status"] == "active" else "active"
        data["status"] = new_status
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.upsert_user(data)

        # CCD 文件增删 disable 指令
        self._update_ccd_disable(username, new_status == "disabled")

        # 停用时通过管理接口踢人下线
        if new_status == "disabled":
            self._kill_client_session(username)

        self.audit.log("toggle_user_status", "user", username,
                       f"状态变更为 {new_status}", "success")
        return new_status

    def change_group(self, username: str, new_group_id: str) -> dict:
        """切换用户所属组，并按新组子网重新分配 CCD 固定 IP。

        参数:
            username: VPN 用户名。
            new_group_id: 目标用户组 ID。

        返回:
            切组结果，包含旧组、新组、旧虚拟 IP、新虚拟 IP。

        异常:
            ValueError: 用户不存在、目标组不存在、目标组停用或目标组无可用 IP。
            RuntimeError: mesh 路由同步失败。
        """
        user = self._load_user(username)
        if not user:
            raise ValueError(f"用户不存在: {username}")
        if user.get("status") == "deleted":
            raise ValueError(f"用户已删除: {username}")

        target_group_id = str(new_group_id or "").strip()
        if not target_group_id:
            raise ValueError("目标组不能为空")
        target_group = self._load_group(target_group_id)
        if not target_group:
            raise ValueError(f"用户组不存在: {target_group_id}")
        if target_group.get("status") != "active":
            raise ValueError(f"用户组已停用: {target_group_id}")

        old_group_id = str(user.get("group_id") or "").strip()
        if old_group_id == target_group_id:
            return {
                "username": username,
                "old_group_id": old_group_id,
                "new_group_id": target_group_id,
                "old_virtual_ip": self._read_ccd_virtual_ip(username),
                "new_virtual_ip": self._read_ccd_virtual_ip(username),
                "changed": False,
            }

        try:
            new_ip, netmask = self._allocate_virtual_ip_for_group(target_group)
            ccd_file = CCD_DIR / username
            old_text = ccd_file.read_text(encoding="utf-8") if ccd_file.exists() else ""
            old_ip, new_text = self._replace_ccd_ifconfig_push(old_text, new_ip, netmask)

            CCD_DIR.mkdir(parents=True, exist_ok=True)
            ccd_file.write_text(new_text, encoding="utf-8")
            fix_path_for_openvpn_shared_data(ccd_file)

            now = datetime.now(timezone.utc).isoformat()
            user["group_id"] = target_group_id
            user["updated_at"] = now
            self.store.upsert_user(user)
            self._increment_group_user_count(old_group_id, -1)
            self._increment_group_user_count(target_group_id, 1)

            from app.services.peer_instance.service import PeerService

            PeerService().sync_all_mesh_push_routes_in_ccd()
            self._kill_client_session(username)

            result = {
                "username": username,
                "old_group_id": old_group_id,
                "new_group_id": target_group_id,
                "old_virtual_ip": old_ip,
                "new_virtual_ip": new_ip,
                "changed": True,
            }
            self.audit.log("change_user_group", "user", username, result, "success")
            logger.info(
                "用户已切换组 username=%s old_group=%s new_group=%s old_ip=%s new_ip=%s",
                username,
                old_group_id,
                target_group_id,
                old_ip or "",
                new_ip,
            )
            return result
        except Exception as exc:
            logger.error(
                "用户切换组失败 username=%s old_group=%s new_group=%s: %s",
                username,
                old_group_id,
                target_group_id,
                exc,
            )
            self.audit.log(
                "change_user_group",
                "user",
                username,
                {"old_group_id": old_group_id, "new_group_id": target_group_id, "error": str(exc)},
                "failure",
                str(exc),
            )
            raise

    def _update_ccd_disable(self, username: str, disable: bool) -> None:
        """在 CCD 文件中写入或移除 disable 指令。"""
        ccd_file = CCD_DIR / username
        if not ccd_file.exists():
            if disable:
                CCD_DIR.mkdir(parents=True, exist_ok=True)
                ccd_file.write_text("disable\n", encoding="utf-8")
                fix_path_for_openvpn_shared_data(ccd_file)
            return

        lines = ccd_file.read_text(encoding="utf-8").splitlines()
        filtered = [ln for ln in lines if ln.strip() != "disable"]
        if disable:
            filtered.insert(0, "disable")
        ccd_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")
        fix_path_for_openvpn_shared_data(ccd_file)

    def _kill_client_session(self, username: str) -> None:
        """通过 OpenVPN 管理接口踢掉用户当前连接（遍历已注册实例端口，与 server.conf 一致）。"""
        from app.services.openvpn.instance import iter_instance_mgmt_ports

        endpoints = iter_instance_mgmt_ports()
        if not endpoints:
            logger.warning("instances 为空，无法通过管理接口踢用户: %s", username)
            return
        any_ok = False
        for _inst, mgmt_port in endpoints:
            try:
                with socket.create_connection(("127.0.0.1", mgmt_port), timeout=3) as sock:
                    sock.recv(4096)
                    sock.sendall(f"kill {username}\n".encode("utf-8"))
                    sock.recv(4096)
                logger.info("已通过管理接口断开用户连接: %s (mgmt_port=%s)", username, mgmt_port)
                any_ok = True
            except (OSError, socket.timeout) as exc:
                logger.debug(
                    "管理端口 %s 上 kill 失败（实例未监听或用户不在该实例）: %s",
                    mgmt_port,
                    exc,
                )
        if not any_ok:
            logger.warning(
                "所有已注册实例的管理接口均未成功执行 kill %s，请确认 openvpn 已启动",
                username,
            )

    def kick_offline(self, username: str) -> bool:
        """踢掉用户当前在线会话（一次性操作，不影响后续连接）。"""
        self._kill_client_session(username)
        self.audit.log("kick_offline", "user", username,
                       "管理员手动踢下线", "success")
        return True

    def get(self, username: str) -> dict | None:
        """获取用户信息"""
        return self._load_user(username)

    def update_cert_serial(self, username: str, cert_serial: str) -> None:
        """续签或重签后更新用户记录中的证书序列号。"""
        data = self._load_user(username)
        if not data:
            raise ValueError(f"用户不存在: {username}")
        data["cert_serial"] = cert_serial
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.upsert_user(data)

    def list_all(self) -> list[dict]:
        """列出所有用户（排除已删除）。"""
        return self.store.list_users()

    def list_page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        search_mode: str = "contains",
        group_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        sort_by: str = "username",
        sort_desc: bool = False,
    ) -> tuple[list[dict], int]:
        """数据库级用户分页查询。"""
        return self.store.list_users_page(
            offset=offset,
            limit=limit,
            search=search,
            search_mode=search_mode,
            group_ids=group_ids,
            statuses=statuses,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

    def count(self) -> int:
        """返回未删除用户数量。"""
        return self.store.count_users()

    def list_usernames_by_group(self, group_id: str) -> list[str]:
        """返回指定组下 status==active 的用户名（升序）。"""
        gid = str(group_id or "").strip()
        if not gid:
            raise ValueError("group_id 不能为空")
        return self.store.list_active_usernames_by_group(gid)

    def _user_exists(self, username: str) -> bool:
        """检查未删除用户名是否已存在。"""
        return self.store.user_exists(username)

    def _load_user(self, username: str) -> dict | None:
        """加载用户元数据。"""
        return self.store.get_user(username)

    def _load_group(self, group_id: str) -> dict | None:
        """加载用户组元数据。"""
        return self.store.get_group(group_id)

    def _write_ccd(self, username: str, group: dict):
        """写入 CCD 文件，为用户分配固定 IP。

        扫描同组已有用户的 CCD 文件，收集已分配 IP，
        在组子网的 hosts 范围内找到下一个可用地址。
        """
        CCD_DIR.mkdir(parents=True, exist_ok=True)
        client_ip, netmask = self._allocate_virtual_ip_for_group(group)

        # CCD 文件内容：固定 IP 分配；掩码用全局池，与 OpenVPN server 行一致
        ccd_content = f"ifconfig-push {client_ip} {netmask}\n"
        ccd_file = CCD_DIR / username
        ccd_file.write_text(ccd_content, encoding="utf-8")
        fix_path_for_openvpn_shared_data(ccd_file)

    def _allocate_virtual_ip_for_group(self, group: dict) -> tuple[str, str]:
        """从组子网分配一个未占用的虚拟 IP，并返回对应 ifconfig-push 掩码。"""
        subnet = group.get("subnet", "10.8.1.0/24")
        network = IPv4Network(subnet, strict=False)
        hosts = list(network.hosts())
        if not hosts:
            raise ValueError(f"组子网 {subnet} 没有可用的主机地址")

        used_ips = self._collect_used_ips_in_group(group["id"], network)
        for host in hosts[1:]:
            if str(host) not in used_ips:
                return str(host), _ifconfig_push_netmask(network)
        raise ValueError(f"组子网 {subnet} 内没有可用 IP，所有地址已分配")

    def _read_ccd_virtual_ip(self, username: str) -> str | None:
        """读取用户 CCD 中的 ifconfig-push 虚拟 IP。"""
        ccd_file = CCD_DIR / username
        if not ccd_file.exists():
            return None
        for line in ccd_file.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "ifconfig-push":
                return parts[1]
        return None

    def _replace_ccd_ifconfig_push(self, existing_text: str, client_ip: str, netmask: str) -> tuple[str | None, str]:
        """替换 CCD 中的固定 IP 行，保留 disable、iroute 和 mesh push 块。"""
        new_line = f"ifconfig-push {client_ip} {netmask}"
        lines = existing_text.splitlines()
        output: list[str] = []
        old_ip: str | None = None
        replaced = False
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "ifconfig-push":
                if old_ip is None:
                    old_ip = parts[1]
                if not replaced:
                    output.append(new_line)
                    replaced = True
                continue
            output.append(line)

        if not replaced:
            insert_at = 1 if output and output[0].strip() == "disable" else 0
            output.insert(insert_at, new_line)
        return old_ip, "\n".join(output).rstrip() + "\n"

    def _collect_used_ips_in_group(self, group_id: str, network: IPv4Network) -> set[str]:
        """收集指定组内所有用户已分配的虚拟 IP 地址。"""
        from ipaddress import IPv4Address

        used: set[str] = set()
        for data in self.store.list_users_by_group(group_id):
            ccd_file = CCD_DIR / str(data.get("username") or "")
            if ccd_file.exists():
                try:
                    content = ccd_file.read_text(encoding="utf-8")
                    parts = content.strip().split()
                    if len(parts) >= 2 and parts[0] == "ifconfig-push":
                        ip = parts[1]
                        if IPv4Address(ip) in network:
                            used.add(ip)
                except (ValueError, IndexError):
                    pass
        return used

    def list_ccd_virtual_ipv4_by_username(self) -> dict[str, str]:
        """扫描 CCD 目录：用户名（文件名）-> ifconfig-push 中的 IPv4。创建用户时写入，一般固定不变。"""
        result: dict[str, str] = {}
        if not CCD_DIR.exists():
            return result
        for ccd_file in CCD_DIR.iterdir():
            if not ccd_file.is_file():
                continue
            try:
                for ln in ccd_file.read_text(encoding="utf-8").splitlines():
                    parts = ln.strip().split()
                    if len(parts) >= 2 and parts[0] == "ifconfig-push":
                        result[ccd_file.name] = parts[1]
                        break
            except OSError:
                continue
        return result

    def _increment_group_user_count(self, group_id: str, delta: int):
        """更新组的用户计数。"""
        if not group_id:
            return
        data = self.store.get_group(group_id)
        if not data:
            return
        data["user_count"] = max(0, int(data.get("user_count", 0)) + delta)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.upsert_group(data)
