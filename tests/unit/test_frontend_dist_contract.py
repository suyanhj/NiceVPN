# -*- coding: utf-8 -*-
"""Vue 预编译运行产物与 Docker 发布契约测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "frontend-dist.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
GITIGNORE = ROOT / ".gitignore"
DOCKERFILE = ROOT / "deploy" / "docker" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def test_frontend_dist_workflow_builds_reproducible_runtime_assets() -> None:
    """前端构建必须可复现，并把经过检查的产物提交到运行时目录。"""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    test_workflow = TEST_WORKFLOW.read_text(encoding="utf-8")
    lockfile = ROOT / "app" / "ui" / "vue" / "frontend" / "package-lock.json"
    lock_source = lockfile.read_text(encoding="utf-8")

    assert '"lockfileVersion": 3' in lock_source
    assert "registry.npmmirror.com" not in lock_source
    assert "registry.npmjs.org" in lock_source

    for source in (workflow, test_workflow):
        assert "npm ci --no-audit --no-fund" in source
        assert "npm install --no-audit --no-fund --package-lock=false" not in source
        assert "cache-dependency-path: app/ui/vue/frontend/package-lock.json" in source

    assert "contents: write" in workflow
    assert 'node-version: "22"' in workflow
    assert "npm run check" in workflow
    assert "test -s ../dist/VpnSpotlightSurface.js" in workflow
    assert "test -s ../dist/VpnInteractiveGrid.js" in workflow
    assert "frontend-build.json" in workflow
    assert "git add -f app/ui/vue/dist" in workflow
    assert "[skip ci]" in workflow
    assert "git push origin HEAD:main" in workflow


def test_docker_runtime_consumes_precompiled_dist_without_node_toolchain() -> None:
    """生产镜像只消费已构建的前端资源，不在镜像构建阶段安装 Node。"""

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")

    for marker in (
        "test -s app/ui/vue/dist/VpnSpotlightSurface.js",
        "test -s app/ui/vue/dist/VpnInteractiveGrid.js",
        "test -s app/ui/vue/dist/frontend-build.json",
    ):
        assert marker in dockerfile

    for marker in ("NODE_IMAGE", "frontend-builder", "frontend-assets", "npm ci", "npm run"):
        assert marker not in dockerfile

    assert "COPY --from=frontend-builder" not in dockerfile
    assert "app/ui/vue/dist/" not in dockerignore


def test_docker_ci_tracks_runtime_dist_not_vue_sources() -> None:
    """Docker 变更检测关注运行时产物，而不是前端开发源码。"""

    workflow = TEST_WORKFLOW.read_text(encoding="utf-8")

    assert "app/ui/vue/dist/*" in workflow
    docker_case = workflow.split("docker=false", 1)[1].split("browser=false", 1)[1]
    assert "app/ui/vue/frontend/*" not in docker_case.split("case", 2)[1]


def test_gitignore_only_reincludes_nicevpn_vue_dist() -> None:
    text = GITIGNORE.read_text(encoding="utf-8")

    assert "!app/ui/vue/dist/" in text
    assert "!app/ui/vue/dist/**" in text
