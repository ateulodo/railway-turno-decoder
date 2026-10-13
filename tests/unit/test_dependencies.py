"""Regression checks for the supported framework/client dependency contract."""

import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def pinned_requirements(filename: str) -> dict[str, Requirement]:
    requirements = [
        Requirement(line)
        for line in (ROOT / filename).read_text().splitlines()
        if line and not line.startswith(("#", "-"))
    ]
    result = {canonicalize_name(req.name): req for req in requirements}
    assert len(result) == len(requirements), "Duplicate dependency snapshot entries"
    return result


@pytest.mark.parametrize("spec", PROJECT["dependencies"] + PROJECT["optional-dependencies"]["dev"])
def test_declared_dependencies_match_installed_and_pinned_versions(spec: str) -> None:
    declared = Requirement(spec)
    pins = pinned_requirements("requirements.txt") | pinned_requirements("requirements-dev.txt")
    pinned = pins[canonicalize_name(declared.name)]
    installed = version(declared.name)
    assert installed in declared.specifier
    assert installed in pinned.specifier


@pytest.mark.parametrize("incompatible_version", ["0.46.0", "0.52.1"])
def test_starlette_versions_requiring_legacy_httpx_are_excluded(incompatible_version: str) -> None:
    direct = {
        canonicalize_name(Requirement(spec).name): Requirement(spec)
        for spec in PROJECT["dependencies"]
    }
    assert incompatible_version not in direct["starlette"].specifier
    # The tested minimum must remain permitted; it supports the httpx2 TestClient.
    assert "1.8.0" in direct["starlette"].specifier


def test_snapshots_do_not_duplicate_runtime_dependencies_in_dev() -> None:
    assert (
        not pinned_requirements("requirements.txt").keys()
        & pinned_requirements("requirements-dev.txt").keys()
    )
    assert "-r requirements.txt" in (ROOT / "requirements-dev.txt").read_text().splitlines()
