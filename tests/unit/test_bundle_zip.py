# -*- coding: utf-8 -*-
"""bundle_zip 单元测试"""

import zipfile

import app.core.constants as constants
from app.services.download.bundle_zip import build_ovpn_zip


def test_build_ovpn_zip_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "DOWNLOAD_BUNDLES_DIR", tmp_path)
    a = tmp_path / "a.ovpn"
    b = tmp_path / "b.ovpn"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    zp, name = build_ovpn_zip([("u1", a), ("u2", b)], "proj")
    assert name.endswith(".zip")
    assert "proj_" in name
    assert zp.is_file()
    with zipfile.ZipFile(zp, "r") as zf:
        assert set(zf.namelist()) == {"u1.ovpn", "u2.ovpn"}
