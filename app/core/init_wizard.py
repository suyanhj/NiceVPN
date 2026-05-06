# -*- coding: utf-8 -*-
"""首次运行初始化状态机 — 引导管理员完成系统初始配置"""
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.core.config import load_config, save_config
from app.core.constants import (
    DATA_DIR,
    GROUPS_DIR,
    LOGS_DIR,
    OPENVPN_DEFAULT_MAX_CLIENTS,
)
from app.utils.posix_data_perms import (
    ensure_device_bind_log_file,
    sync_openvpn_runtime_permissions_from_config,
)
from app.services.openvpn.detector import detect_openvpn, find_easyrsa
from app.utils.audit_log import AuditLogger
from app.utils.cidr import validate_cidr

logger = logging.getLogger(__name__)


class WizardStep(str, Enum):
    """初始化向导步骤"""
    DETECT_OPENVPN = "detect_openvpn"
    INSTALL_OPENVPN = "install_openvpn"
    CUSTOM_PATH = "custom_path"
    CONFIG_PKI = "config_pki"
    CONFIG_SUBNET = "config_subnet"
    CREATE_DEFAULT_GROUP = "create_default_group"
    START_SERVICE = "start_service"
    DONE = "done"


@dataclass
class StepResult:
    """步骤执行结果"""
    success: bool
    message: str
    data: dict | None = None
    next_step: WizardStep | None = None


class InitWizard:
    """
    系统初始化向导状态机。
    引导流程：检测 OpenVPN → 安装/自定义路径 → 配置 PKI →
              配置全局子网 → 创建默认用户组 → 启动服务 → 完成
    """

    def __init__(self):
        self.config = load_config()
        self.audit = AuditLogger()

    def run_step(self, step: WizardStep, data: dict | None = None) -> StepResult:
        """执行指定步骤，返回执行结果和下一步建议"""
        handlers = {
            WizardStep.DETECT_OPENVPN: self._detect_openvpn,
            WizardStep.INSTALL_OPENVPN: self._install_openvpn,
            WizardStep.CUSTOM_PATH: self._custom_path,
            WizardStep.CONFIG_PKI: self._config_pki,
            WizardStep.CONFIG_SUBNET: self._config_subnet,
            WizardStep.CREATE_DEFAULT_GROUP: self._create_default_group,
            WizardStep.START_SERVICE: self._start_service,
        }
        handler = handlers.get(step)
        if not handler:
            return StepResult(False, f"未知步骤: {step}")
        return handler(data or {})

    def _detect_openvpn(self, data: dict) -> StepResult:
        """步骤一：检测 OpenVPN 安装状态"""
        logger.info("步骤 1/5：开始检测 OpenVPN 环境")
        result = detect_openvpn()
        if result["installed"] and result["meets_requirement"]:
            logger.info("步骤 1/5：检测结果满足要求，version=%s, path=%s", result["version"], result["path"])
        elif result["installed"]:
            logger.warning("步骤 1/5：检测到 OpenVPN，但版本过低，version=%s", result["version"])
        else:
            logger.warning("步骤 1/5：未检测到 OpenVPN")

        if result["installed"] and result["meets_requirement"]:
            # 已安装且满足版本要求
            easyrsa_path = find_easyrsa(result["path"])
            self.config.openvpn_bin = result["path"]
            self.config.easyrsa_dir = easyrsa_path
            save_config(self.config)
            logger.info("步骤 1/5：检测 OpenVPN 成功，version=%s, path=%s", result["version"], result["path"])
            return StepResult(
                True,
                f"已检测到 OpenVPN {result['version']}",
                data=result,
                next_step=WizardStep.CONFIG_PKI
            )
        elif result["installed"]:
            # 已安装但版本不满足
            return StepResult(
                False,
                f"OpenVPN 版本过低（{result['version']}），需要 2.7.0+",
                data=result,
                next_step=WizardStep.INSTALL_OPENVPN
            )
        else:
            # 未安装
            return StepResult(
                False,
                "未检测到 OpenVPN",
                data=result,
                next_step=WizardStep.INSTALL_OPENVPN
            )

    def _install_openvpn(self, data: dict) -> StepResult:
        """步骤二A：自动安装 OpenVPN"""
        from app.services.openvpn.installer import install_openvpn
        from app.services.openvpn.detector import get_distro_info, detect_distro_family

        logger.info("步骤 1/5：开始执行 OpenVPN 安装流程")
        distro_info = get_distro_info()
        distro_family = detect_distro_family(distro_info)
        if not distro_family:
            logger.error("步骤 1/5：无法识别系统发行版，安装终止")
        if not distro_family:
            return StepResult(False, "无法识别操作系统类型，仅支持红帽系和 Debian 系")

        success = install_openvpn(
            distro=distro_family,
            version_id=distro_info.get("version_id", ""),
            on_output=data.get("on_output"),
        )
        if success:
            # 安装后重新检测
            result = detect_openvpn()
            if result["installed"] and result["meets_requirement"]:
                easyrsa_path = find_easyrsa(result["path"])
                self.config.openvpn_bin = result["path"]
                self.config.easyrsa_dir = easyrsa_path
                save_config(self.config)
                self.audit.log("install_openvpn", "system", None,
                               {"version": result["version"]}, "success")
                logger.info("步骤 1/5：OpenVPN 安装完成，version=%s, path=%s", result["version"], result["path"])
                return StepResult(
                    True, f"OpenVPN {result['version']} 安装成功",
                    data=result, next_step=WizardStep.CONFIG_PKI
                )
        self.audit.log("install_openvpn", "system", None, {}, "failure")
        install_log = LOGS_DIR / "openvpn-install.log"
        logger.error("OpenVPN 安装失败，请检查安装日志: %s", install_log)
        return StepResult(False, f"OpenVPN 安装失败，请查看安装日志：{install_log}")

    def _custom_path(self, data: dict) -> StepResult:
        """步骤二B：使用自定义 OpenVPN 路径"""
        from app.services.openvpn.detector import validate_custom_path

        custom_path = data.get("path", "")
        logger.info("步骤 1/5：开始验证自定义 OpenVPN 路径: %s", custom_path)
        result = validate_custom_path(custom_path)
        if result["valid"]:
            easyrsa_path = find_easyrsa(custom_path)
            self.config.openvpn_bin = custom_path
            self.config.easyrsa_dir = easyrsa_path
            save_config(self.config)
            logger.info("步骤 1/5：自定义 OpenVPN 路径验证成功，version=%s, path=%s", result["version"], custom_path)
            return StepResult(
                True, f"自定义路径验证通过（版本 {result['version']}）",
                next_step=WizardStep.CONFIG_PKI
            )
        return StepResult(False, result.get("error", "路径无效"))

    def _config_pki(self, data: dict) -> StepResult:
        """步骤三：初始化 PKI 和 CA"""
        from app.services.easyrsa.wrapper import EasyRSAWrapper, EasyRSAError

        def emit(message: str):
            """输出初始化 PKI 的阶段日志。"""
            logger.info(message)
            callback = data.get("on_output")
            if callback:
                callback(message)

        easyrsa_dir = str(self.config.get("easyrsa_dir") or "").strip()
        if not easyrsa_dir:
            return StepResult(False, "未找到 EasyRSA，请检查 OpenVPN 安装")

        pki_dir = str(data.get("pki_dir") or "/etc/openvpn/pki").strip()
        try:
            emit(f"[阶段] 开始初始化 PKI: {pki_dir}")
            wrapper = EasyRSAWrapper(easyrsa_dir, pki_dir)
            emit("[阶段] 正在创建 PKI 目录结构")
            wrapper.init_pki()
            emit("[阶段] 正在生成无密码 CA")
            wrapper.build_ca()
            emit("[阶段] 正在生成 DH 参数")
            wrapper.gen_dh()
            emit("[阶段] 正在生成服务端证书 (CN=server，对应 issued/server.crt)")
            wrapper.gen_req("server")
            wrapper.sign_req("server", "server")
            emit("[阶段] 正在生成 CRL")
            wrapper.gen_crl()
            emit("[阶段] 正在生成 tls-crypt-v2 服务端密钥")
            # 生成 tls-crypt-v2 服务端密钥
            tc2_key = wrapper.gen_tls_crypt_v2_server()
            emit("[完成] PKI 初始化完成")

            self.config.pki_dir = pki_dir
            save_config(self.config)
            sync_openvpn_runtime_permissions_from_config()
            self.audit.log("init_pki", "system", None,
                           {"pki_dir": pki_dir}, "success")
            return StepResult(
                True, "PKI 初始化完成",
                data={"pki_dir": pki_dir, "tc2_server_key": tc2_key},
                next_step=WizardStep.CONFIG_SUBNET
            )
        except EasyRSAError as e:
            emit(f"[错误] PKI 初始化失败: {e}")
            self.audit.log("init_pki", "system", None,
                           {"error": str(e)}, "failure")
            return StepResult(False, f"PKI 初始化失败: {e}")

    # 全局子网允许的最小前缀长度（/16 = 65534 台主机，再大的网段不适合单个 VPN 实例）
    GLOBAL_SUBNET_MIN_PREFIX = 16

    def _config_subnet(self, data: dict) -> StepResult:
        """步骤四：配置全局 VPN 子网"""
        from ipaddress import IPv4Network

        subnet = data.get("subnet", "")
        logger.info("步骤 3/5：开始配置全局 VPN 子网: %s", subnet)
        if not validate_cidr(subnet):
            logger.warning("步骤 3/5：全局 VPN 子网格式无效: %s", subnet)
            return StepResult(False, f"无效的 CIDR 格式: {subnet}")

        # 前缀长度必须 >= 16，拒绝 /8、/12 等过大网段
        network = IPv4Network(subnet, strict=False)
        if network.prefixlen < self.GLOBAL_SUBNET_MIN_PREFIX:
            msg = (
                f"全局子网前缀长度 /{network.prefixlen} 过大，"
                f"仅允许 /{self.GLOBAL_SUBNET_MIN_PREFIX}～/30 的子网"
                f"（如 10.224.0.0/16）。"
            )
            logger.warning("步骤 3/5：%s", msg)
            return StepResult(False, msg)

        self.config.global_subnet = subnet
        # 保存服务端连接配置（可选，初始化时填写）
        server_ip = data.get("server_ip", "")
        if server_ip:
            self.config.server_ip = server_ip
        if data.get("port"):
            self.config.port = int(data["port"])
        if data.get("proto"):
            self.config.proto = data["proto"]
        # 与系统设置一致：推送到客户端的内网路由（可选）
        from pydantic import ValidationError

        from app.models.config import SystemConfig

        push_lines = data.get("push_lan_routes")
        if push_lines is None:
            push_lines = []
        if isinstance(push_lines, str):
            push_lines = [ln.strip() for ln in push_lines.splitlines() if ln.strip()]
        try:
            merged_cfg = SystemConfig(
                **{
                    **dict(self.config.to_dict()),
                    "push_lan_routes": push_lines,
                }
            )
        except ValidationError as exc:
            logger.warning("步骤 3/5：内网路由校验失败: %s", exc)
            return StepResult(
                False,
                f"内网路由不合法（须为 IPv4 CIDR）: {exc}",
            )
        self.config.push_lan_routes = list(merged_cfg.push_lan_routes)
        save_config(self.config)
        self.audit.log("config_subnet", "system", None,
                       {"subnet": subnet}, "success")
        logger.info("步骤 3/5：全局 VPN 子网配置完成: %s", subnet)
        return StepResult(
            True, f"全局子网配置完成: {subnet}",
            next_step=WizardStep.CREATE_DEFAULT_GROUP
        )

    def _create_default_group(self, data: dict) -> StepResult:
        """步骤五：创建默认用户组"""
        import json
        from ipaddress import IPv4Network

        global_subnet = self.config.global_subnet
        logger.info("步骤 4/5：开始创建默认用户组，global_subnet=%s", global_subnet)
        if not global_subnet:
            return StepResult(False, "请先配置全局子网")

        # 默认用户组就是全局组，子网 = 全局子网
        # 后续创建的子组必须是全局组子网的子网
        default_subnet = global_subnet

        now = datetime.now(timezone.utc).isoformat()
        group = {
            "id": str(uuid.uuid4()),
            "name": "默认用户组",
            "subnet": default_subnet,
            "status": "active",
            "user_count": 0,
            "firewall_rule_ids": [],
            "created_at": now,
            "updated_at": now,
        }

        # 确保目录存在
        GROUPS_DIR.mkdir(parents=True, exist_ok=True)
        group_file = GROUPS_DIR / f"{group['id']}.json"
        with open(group_file, "w", encoding="utf-8") as f:
            json.dump(group, f, ensure_ascii=False, indent=2)

        logger.info("步骤 4/5：默认用户组创建完成，group_id=%s, subnet=%s", group["id"], default_subnet)
        self.audit.log("create_default_group", "group", group["id"],
                       {"name": "默认用户组", "subnet": default_subnet}, "success")
        return StepResult(
            True, f"默认用户组已创建（子网: {default_subnet}）",
            data=group,
            next_step=WizardStep.START_SERVICE
        )

    def _start_service(self, data: dict) -> StepResult:
        """步骤六：生成服务端配置并启动 OpenVPN"""
        from app.models.config import SystemConfig
        from app.services.firewall.rule_service import FirewallRuleService
        from app.services.group.crud import GroupService
        from app.services.openvpn.instance import write_server_conf, start_instance
        from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts
        from ipaddress import IPv4Network

        instance_name = data.get("instance_name", "server")
        logger.info("步骤 5/5：开始生成服务端配置并启动 OpenVPN，instance=%s", instance_name)
        global_subnet = self.config.global_subnet
        network = IPv4Network(global_subnet, strict=False)

        ovpn_base = str(self.config.get("openvpn_conf_dir") or "/etc/openvpn")
        conf_config = {
            "server_network": str(network.network_address),
            "server_mask": str(network.netmask),
            "pki_dir": self.config.pki_dir,
            "openvpn_conf_dir": ovpn_base,
            "push_lan_routes": list(self.config.get("push_lan_routes") or []),
            "max_clients": int(self.config.get("max_clients") or OPENVPN_DEFAULT_MAX_CLIENTS),
        }

        try:
            # 先部署 client-connect / client-disconnect 脚本，再写 server.conf
            sync_packaged_openvpn_scripts(ovpn_base)

            conf_path = write_server_conf(instance_name, conf_config, conf_dir=ovpn_base)
            ensure_device_bind_log_file()

            success = start_instance(instance_name)
            if success:
                # 写入实例注册信息，供服务管理/仪表盘识别未启动实例
                merged = SystemConfig(**self.config.to_dict())
                inst_map = dict(merged.instances or {})
                inst_map[instance_name] = {
                    "port": 1194,
                    "proto": "udp",
                    "subnet": global_subnet,
                }
                merged.instances = inst_map
                merged.initialized = True
                save_config(merged)
                self.config = load_config()

                from app.utils.api_basic_credentials import ensure_api_basic_credentials_file

                ensure_api_basic_credentials_file()

                try:
                    from app.services.user.device_bind_policy import sync_device_bind_mode_file

                    sync_device_bind_mode_file(merged.device_bind_mode)
                except OSError as exc:
                    logger.warning("同步 device_bind_mode 到 /etc/openvpn/mgmt 失败: %s", exc)

                try:
                    from app.utils.sysctl_tune import install_vpn_sysctl_dropin

                    install_vpn_sysctl_dropin()
                except (OSError, RuntimeError) as exc:
                    logger.warning(
                        "初始化：sysctl.d 网关调优未安装（需 root；可稍后在系统设置手动执行）: %s",
                        exc,
                    )

                # 为默认组创建占位全放行规则，避免初始化后无法访问
                try:
                    default_group = next(
                        (g for g in GroupService().list_all() if g.get("name") == "默认用户组"),
                        None,
                    )
                    if default_group:
                        fw = FirewallRuleService()
                        if not fw.list_by_owner(default_group["id"]):
                            fw.create({
                                "owner_type": "group",
                                "owner_id": default_group["id"],
                                "instance": instance_name,
                                "action": "accept",
                                "protocol": "all",
                                "source_subnet": global_subnet,
                                "dest_ip": None,
                                "dest_port": None,
                                "priority": 10,
                                "enabled": True,
                                "description": "初始化默认放行",
                            })
                            logger.info(
                                "已为默认组创建占位防火墙规则: group_id=%s",
                                default_group["id"],
                            )
                except Exception as exc:
                    logger.error("创建默认防火墙规则失败: %s", exc)
                    raise

                # 与启动调度器一致：规则落盘后重建 iptables，使 MASQUERADE 与 FORWARD 立即生效
                if os.name != "nt":
                    FirewallRuleService().rebuild_iptables()
                    logger.info(
                        "初始化：已按当前配置重建 iptables/ipset（含 FORWARD 与 MASQUERADE）",
                    )

                self.audit.log("start_service", "openvpn", instance_name,
                               {"conf": str(conf_path)}, "success")
                logger.info("步骤 5/5：OpenVPN 服务启动完成，instance=%s, conf=%s", instance_name, conf_path)
                return StepResult(True, "OpenVPN 服务已启动",
                                  next_step=WizardStep.DONE)
            else:
                self.audit.log("start_service", "openvpn", instance_name,
                               {}, "failure")
                logger.error("步骤 5/5：OpenVPN 服务启动失败，instance=%s", instance_name)
                return StepResult(False, "服务启动失败，请检查配置和日志")
        except Exception as e:
            logger.exception("步骤 5/5：启动 OpenVPN 服务异常，instance=%s", instance_name)
            return StepResult(False, f"启动失败: {e}")
