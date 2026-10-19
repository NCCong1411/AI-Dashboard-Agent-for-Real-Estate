"""The setup rehearsal must never reuse caller credentials/install targets."""

import os
import subprocess
import sys

from scripts.verify_clean_setup import setup_environment


def test_clean_setup_removes_destination_and_python_startup_overrides():
    original = {key: "untrusted" for key in (
        "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_ROOT", "PIP_PYTHON", "PIP_CONFIG_FILE",
        "PYTHONHOME", "PYTHONUSERBASE", "PYTHONPATH", "PYTHONSTARTUP", "VIRTUAL_ENV",
        "LLM_API_KEY", "PGHOSTADDR", "RUNTIME_DATABASE_URL", "DATABASE_URL",
    )}
    original.update(PIP_INDEX_URL="https://registry.invalid/simple", HTTPS_PROXY="http://proxy.invalid", PATH="tools")
    result = setup_environment(original)
    assert all(key not in result for key in original if key not in {"PIP_CONFIG_FILE", "PIP_INDEX_URL", "HTTPS_PROXY", "PATH"})
    assert result["PIP_CONFIG_FILE"] == os.devnull
    assert result["PIP_REQUIRE_VIRTUALENV"] == "true"
    assert result["PYTHONNOUSERSITE"] == "1"
    assert result["PIP_INDEX_URL"] == original["PIP_INDEX_URL"]
    assert result["HTTPS_PROXY"] == original["HTTPS_PROXY"]
    assert original["PIP_TARGET"] == "untrusted"


def test_pip_itself_ignores_inherited_destination_config(tmp_path):
    configuration = tmp_path / "pip.ini"
    configuration.write_text("[install]\ntarget = " + str(tmp_path / "outside-venv") + "\n", encoding="utf-8")
    values = dict(os.environ, PIP_CONFIG_FILE=str(configuration), PIP_TARGET=str(tmp_path / "other-target"))
    result = subprocess.run([sys.executable, "-m", "pip", "config", "list"],
                            env=setup_environment(values), capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert "outside-venv" not in result.stdout and "other-target" not in result.stdout
    assert not (tmp_path / "outside-venv").exists()
