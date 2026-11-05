# -*- coding: utf-8 -*-
"""日志尾部读取（大块文件仅扫末尾）。"""

from pathlib import Path

from app.ui.pages.services import ServicesPage


def test_read_log_last_lines_caps_count(tmp_path: Path) -> None:
    """只返回末尾 max_lines 行。"""
    p = tmp_path / "a.log"
    p.write_text("\n".join(f"line{i}" for i in range(1000)), encoding="utf-8")
    out = ServicesPage._read_log_last_lines(p, 500)
    lines = out.splitlines()
    assert len(lines) == 500
    assert lines[0] == "line500"
    assert lines[-1] == "line999"


def test_log_snapshot_never_uses_full_file_read(tmp_path: Path, monkeypatch) -> None:
    """日志文件即使非常大也只能 seek 到尾部读取。"""
    p = tmp_path / "large.log"
    with p.open("wb") as stream:
        stream.seek(2 * 1024 * 1024 * 1024)
        stream.write(b"older\\nlatest\\n")

    def forbid_full_read(*_args, **_kwargs):
        raise AssertionError("must not call Path.read_text for log snapshots")

    monkeypatch.setattr(Path, "read_text", forbid_full_read)
    assert ServicesPage._read_log_tail(p, 1) == "latest"
    assert ServicesPage._read_log_tail(p, 2) == "older\\nlatest"


def test_log_reader_bounds_single_giant_line(tmp_path: Path) -> None:
    """只有一条超长日志时也应按字节截断，避免大量分配。"""
    p = tmp_path / "one-long-line.log"
    p.write_bytes(b"x" * (4 * 1024 * 1024) + b"\\nEND")
    out = ServicesPage._read_log_tail(p)
    assert out.startswith("(日志过大，仅读取末尾 ")
    assert out.endswith("END")
    assert len(out.encode("utf-8")) <= 512 * 1024 + 100


def test_realtime_log_reader_respects_explicit_byte_cap(tmp_path: Path) -> None:
    p = tmp_path / "tail.log"
    p.write_bytes(b"A" * (2 * 1024 * 1024) + b"\\nnewest")
    output = ServicesPage._read_log_last_lines(p, 500, max_bytes=4096)
    assert output.startswith("(日志过大，仅读取末尾 4 KiB)")
    assert output.endswith("newest")
    assert len(output.encode("utf-8")) <= 4096 + 100


def test_log_missing_file_remains_file_not_found(tmp_path: Path) -> None:
    from pytest import raises

    with raises(FileNotFoundError):
        ServicesPage._read_log_tail(tmp_path / "missing.log")
