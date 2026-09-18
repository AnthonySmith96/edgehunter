"""Render-only deployment checks; these do not imply a Linux host was deployed."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("edgehunter_deploy", ROOT / "deploy" / "install.py")
assert SPEC and SPEC.loader
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


def render(**overrides):
    options = {"host": "control.example.invalid", "port": 8097, "admin_bind": "127.0.0.1",
               "release_root": "/srv/edgehunter/current", "architecture": "amd64"}
    options.update(overrides)
    return DEPLOY.render(**options)


@pytest.mark.parametrize("options", [
    {"host": "control.example.invalid;bad"}, {"host": "a..invalid"}, {"host": "-admin.invalid"},
    {"host": "a" * 64 + ".invalid"}, {"admin_bind": "8.8.8.8"}, {"admin_bind": "0.0.0.0"},
    {"release_root": "/srv/../etc"}, {"release_root": "/srv/edge hunter"}, {"port": 443},
])
def test_invalid_render_inputs_fail(options):
    with pytest.raises(ValueError):
        render(**options)


def test_service_and_proxy_boundaries():
    files = render()
    daemon = files["edgehunter-daemon.service"]
    control = files["edgehunter-pocketbase.service"]
    proxy = files["nginx-vpn.conf"]
    assert "run --mode shadow" in daemon and "--daemon-credentials /etc/edgehunter/daemon.json" in daemon
    assert "User=edgehunter\n" in daemon and "User=edgehunter-pb\n" in control
    assert "InaccessiblePaths=/var/lib/edgehunter-pb" in daemon
    assert "--http=127.0.0.1:8097" in control
    assert "listen 127.0.0.1:443 ssl;" in proxy
    assert "proxy_set_header Authorization $http_authorization;" in proxy
    assert "proxy_buffering off;" in proxy
    assert "WatchdogSec" not in daemon  # No claim of sd_notify integration.
    assert "listen [fd00::7]:443 ssl;" in render(admin_bind="fd00::7")["nginx-vpn.conf"]
    web = render(profile="web")["nginx-web.conf"]
    assert "BLOCKED until PB MFA" in web and "auth_basic off;" in web


def test_cli_default_dry_run_and_new_output_only(tmp_path):
    command = [sys.executable, str(ROOT / "deploy" / "install.py")]
    before = set(tmp_path.iterdir())
    plan = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
    metadata = json.loads(plan.stdout)
    assert metadata["status"] == "DRY_RUN" and metadata["host_modified"] is False
    assert set(tmp_path.iterdir()) == before
    destination = tmp_path / "review"
    result = subprocess.run(command + ["--output", str(destination)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["status"] == "RENDERED_FOR_REVIEW"
    contents = {path.name: path.read_bytes() for path in destination.iterdir()}
    assert len(contents) == 3
    retry = subprocess.run(command + ["--output", str(destination)], capture_output=True, text=True)
    assert retry.returncode != 0
    assert contents == {path.name: path.read_bytes() for path in destination.iterdir()}
