# -*- coding: utf-8 -*-
"""Docker 生产镜像必须消费 CI 预编译 Vue 产物。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "docker" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def test_docker_runtime_does_not_compile_vue() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "NODE_IMAGE" not in text
    assert "frontend-builder" not in text
    assert "frontend-assets" not in text
    assert "npm ci" not in text
    assert "npm run" not in text
    assert "COPY --from=frontend-builder" not in text


def test_docker_runtime_requires_committed_vue_dist() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    ignore = DOCKERIGNORE.read_text(encoding="utf-8")

    assert "test -s app/ui/vue/dist/VpnSpotlightSurface.js" in text
    assert "test -s app/ui/vue/dist/VpnInteractiveGrid.js" in text
    assert "test -s app/ui/vue/dist/frontend-build.json" in text
    assert "app/ui/vue/dist/" not in ignore


def test_docker_ci_tracks_runtime_dist_not_vue_sources() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "app/ui/vue/dist/*" in text
    docker_case = text.split("docker=false", 1)[1].split("browser=false", 1)[1]
    assert "app/ui/vue/frontend/*" not in docker_case.split("case", 2)[1]
