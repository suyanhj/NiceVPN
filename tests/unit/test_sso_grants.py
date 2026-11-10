"""SSO grant is only trusted after server config activates auth Management."""
import importlib.util
import time

from app.services.auth.sso_grants import write_sso_grant


def _helper():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "app/scripts/sso_grant.py"
    spec = importlib.util.spec_from_file_location("nicevpn_hook_grant", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_grant_only_bypasses_binding_with_management_auth(tmp_path):
    root = tmp_path / "grants"
    conf = tmp_path / "server.conf"
    write_sso_grant("server", "alice", peer_ip="203.0.113.2", peer_port="60321", grant_dir=root)
    helper = _helper()
    conf.write_text("management 127.0.0.1 7505\n", encoding="utf-8")
    assert not helper.has_grant(grant_dir=root, instance="server",
                                common_name="alice", peer_ip="203.0.113.2", peer_port="60321", config=conf)
    conf.write_text("management /etc/openvpn/mgmt/sso.sock unix\n"
                    "management-client-user root\nmanagement-client-auth\n",
                    encoding="utf-8")
    assert helper.has_grant(grant_dir=root, instance="server",
                            common_name="alice", peer_ip="203.0.113.2", peer_port="60321", config=conf)
    assert not helper.has_grant(grant_dir=root, instance="server",
                                common_name="bob", peer_ip="203.0.113.2", peer_port="60321", config=conf)
    assert not helper.has_grant(grant_dir=root, instance="other",
                                common_name="alice", peer_ip="203.0.113.2", peer_port="60321", config=conf)
    assert not helper.has_grant(grant_dir=root, instance="server",
                                common_name="alice", peer_ip="203.0.113.3", peer_port="60321", config=conf)
    assert not helper.has_grant(grant_dir=root, instance="server",
                                common_name="alice", peer_ip="203.0.113.2", peer_port="60322", config=conf)

def test_post_auth_hook_does_not_depend_on_optional_config_environment():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "app" / "scripts" /
              "device-bind.sh").read_text(encoding="utf-8")
    assert 'SSO_SERVER_CONF="${OVPN_ETC}/server/${VPN_INSTANCE_NAME:-}.conf"' in script
    assert '--server-config "${SSO_SERVER_CONF}"' in script
    assert '&& [ -f "${SSO_SERVER_CONF}" ]' in script
    assert '[ -n "${config:-}" ]' not in script
