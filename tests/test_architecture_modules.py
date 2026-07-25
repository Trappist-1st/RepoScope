"""Tests for Module Discovery."""

from __future__ import annotations

from pathlib import Path

from app.intelligence.architecture.models import ModuleType
from app.intelligence.architecture.modules import (
    classify_module_type,
    cluster_key_for_path,
    discover_modules,
    infer_responsibility,
)
from app.intelligence.enrichers.roles import build_role_index
from tests.helpers_flow import FASTAPI_LOGIN, SPRING_LOGIN, ingest_fixture


def test_cluster_key_strips_src_main_java():
    assert cluster_key_for_path("src/main/java/auth/AuthController.java") == "auth"
    assert cluster_key_for_path("auth/AuthService.java") == "auth"


def test_cluster_key_app_services():
    assert cluster_key_for_path("app/services/auth_service.py") == "app/services"
    assert cluster_key_for_path("app/api/auth.py") == "app/api"
    assert cluster_key_for_path("app/repositories/user_repo.py") == "app/repositories"


def test_classify_layer_vs_feature():
    mtype, conf = classify_module_type("controller", {"controller": 3}, ["controller/A.java"])
    assert mtype == ModuleType.LAYER
    mtype2, conf2 = classify_module_type(
        "auth",
        {"controller": 1, "service": 1},
        ["auth/AuthController.java"],
    )
    assert mtype2 == ModuleType.FEATURE
    assert conf2 in {"high", "medium"}


def test_infer_responsibility_topic():
    assert infer_responsibility("auth", {"controller": 1}) == "login"
    assert infer_responsibility("services", {"service": 2}) == "application_service"


def test_discover_spring_feature_modules(tmp_path: Path):
    _, kg = ingest_fixture(SPRING_LOGIN, tmp_path)
    roles = build_role_index(kg)
    mmap = discover_modules(kg, role_index=roles)
    names = {m.name for m in mmap.modules}
    assert "auth" in names
    assert "user" in names
    auth = next(m for m in mmap.modules if m.name == "auth")
    assert auth.module_type == ModuleType.FEATURE
    assert auth.evidence
    assert auth.path_roots == ["auth"]
    user = next(m for m in mmap.modules if m.name == "user")
    assert user.module_type in {ModuleType.FEATURE, ModuleType.UNKNOWN}
    assert "repository" in user.role_mix or user.responsibility in {
        "persistence",
        "user",
        "module:user",
    }


def test_discover_fastapi_layer_regions(tmp_path: Path):
    _, kg = ingest_fixture(FASTAPI_LOGIN, tmp_path)
    mmap = discover_modules(kg)
    # app/api, app/services, app/repositories → layer-style regions
    keys = {m.path_roots[0] for m in mmap.modules if m.path_roots}
    assert any(k.endswith("api") or k == "app/api" for k in keys)
    assert any("services" in k for k in keys)
    assert any("repositories" in k for k in keys)
    for m in mmap.modules:
        if "api" in m.path_roots[0] or "services" in m.path_roots[0] or "repositories" in m.path_roots[0]:
            assert m.module_type in {ModuleType.LAYER, ModuleType.FEATURE, ModuleType.UNKNOWN}
            assert m.boundary_confidence in {"high", "medium", "low"}
            assert m.evidence
