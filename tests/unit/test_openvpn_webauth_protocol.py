"""Protocol-only WebAuth tests; no VPN daemon or external office provider."""
import pytest

from app.services.auth.openvpn_webauth import (
    ManagementNotificationParser, ManagementStatusParser,
    auth_allow, auth_deny, pending_webauth,
)


def test_status_v3_snapshot_ignores_interleaved_async_management_events():
    status = ManagementStatusParser()
    status.begin(123.0)
    assert status.feed("TITLE\tOpenVPN 2.7") is None
    header = ("HEADER\tCLIENT_LIST\tCommon Name\tReal Address\t"
              "Virtual Address\tVirtual IPv6 Address\tBytes Received\tBytes Sent\t"
              "Connected Since\tConnected Since (time_t)\tUsername\tClient ID\t"
              "Peer ID\tData Channel Cipher")
    assert status.feed(header) is None
    assert status.feed(">CLIENT:REAUTH,55,4") is None
    assert status.feed(">CLIENT:ENV,common_name=alice") is None
    assert status.feed("CLIENT_LIST\talice\t198.51.100.2:5000\t10.8.0.2\t\t10\t20\t"
                       "2026-11-10\t1773150000\tUNDEF\t55\t0\tAES-256-GCM") is None
    assert status.feed("END") == {55: ("alice", 1773150000)}
    assert not status.pending


def test_status_empty_snapshot_is_valid_but_missing_header_is_not():
    status = ManagementStatusParser()
    status.begin(10.0)
    assert status.feed("END") is None
    status.begin(11.0)
    status.feed("HEADER\tCLIENT_LIST\tCommon Name\tClient ID\tConnected Since (time_t)")
    assert status.feed("END") == {}
    status.begin(12.0)
    assert status.feed("ERROR: unsupported status") is None
    assert not status.pending


def test_connect_event_is_atomic_and_tracks_capabilities():
    parser = ManagementNotificationParser()
    assert parser.feed(">CLIENT:CONNECT,4,0\n") is None
    assert parser.feed(">CLIENT:ENV,common_name=alice\n") is None
    assert parser.feed(">CLIENT:ENV,IV_SSO=openurl,webauth\n") is None
    event = parser.feed(">CLIENT:ENV,END\n")
    assert event.kind == "CONNECT"
    assert (event.cid, event.kid, event.common_name) == (4, 0, "alice")
    assert event.supports_webauth
    assert event.env["IV_SSO"] == "openurl,webauth"


def test_reauth_and_disconnect_with_unrelated_lines():
    parser = ManagementNotificationParser()
    parser.feed(">CLIENT:REAUTH,5,2")
    parser.feed("SUCCESS: some other management output")
    parser.feed(">CLIENT:ENV,IV_SSO=openurl")
    event = parser.feed(">CLIENT:ENV,END")
    assert event.kind == "REAUTH"
    assert event.supports_webauth is False
    parser.feed(">CLIENT:DISCONNECT,5")
    disconnect = parser.feed(">CLIENT:ENV,END")
    assert disconnect.kind == "DISCONNECT"
    assert disconnect.kid is None


def test_final_commands_allow_only_known_values():
    assert auth_allow(4, 0) == "client-auth-nt 4 0\n"
    assert auth_deny(4, 0) == 'client-deny 4 0 "SSO authentication failed"\n'
    for cid, kid in ((-1, 0), (1, -1), ("1\nfoo", 0)):
        with pytest.raises(ValueError):
            auth_allow(cid, kid)
    with pytest.raises(ValueError):
        auth_deny(4, 0, '"\nclient-auth-nt 8 0')


def test_pending_auth_rejects_unsafe_urls():
    expected = ("client-pending-auth 2 0 "
                "WEB_AUTH:external:https://vpn.example.com/auth/sso/start?t=abc 180\n")
    assert pending_webauth(2, 0, "https://vpn.example.com/auth/sso/start?t=abc") == expected
    assert pending_webauth(
        2, 0, "http://192.168.10.20:8881/auth/sso/start?t=abc"
    ) == ("client-pending-auth 2 0 "
          "WEB_AUTH:external:http://192.168.10.20:8881/auth/sso/start?t=abc 180\n")
    assert pending_webauth(
        2, 0, "http://localhost:8881/auth/sso/start?t=abc"
    ).startswith("client-pending-auth 2 0 WEB_AUTH:external:http://localhost:8881")
    for url in ("ftp://vpn.example.com/a", "https://u:p@vpn.example.com/a",
                "https://vpn.example.com/hi\nclient-auth-nt 9 0",
                "https://vpn.example.com/has space", "/relative",
                "http://127.0.0.1:bad/auth", "http://server/#fragment"):
        with pytest.raises(ValueError):
            pending_webauth(2, 0, url)
    with pytest.raises(ValueError):
        pending_webauth(2, 0, "https://vpn.example.com", timeout_seconds=10)
