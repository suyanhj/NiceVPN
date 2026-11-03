# -*- coding: utf-8 -*-
"""Vue 预编译运行产物发布契约测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "frontend-dist.yml"
GITIGNORE = ROOT / ".gitignore"


def test_frontend_dist_workflow_builds_and_commits_runtime_assets() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "contents: write" in text
    assert 'node-version: "22"' in text
    assert "npm ci --no-audit --no-fund" in text
    assert "npm run check" in text
    assert "test -s ../dist/VpnSpotlightSurface.js" in text
    assert "test -s ../dist/VpnInteractiveGrid.js" in text
    assert "frontend-build.json" in text
    assert "git add -f app/ui/vue/dist" in text
    assert "[skip ci]" in text
    assert "git push origin HEAD:main" in text


def test_gitignore_only_reincludes_nicevpn_vue_dist() -> None:
    text = GITIGNORE.read_text(encoding="utf-8")

    assert "!app/ui/vue/dist/" in text
    assert "!app/ui/vue/dist/**" in text
