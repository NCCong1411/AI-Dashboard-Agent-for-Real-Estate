"""Safety tests for the disposable PostgreSQL runner's live opt-in."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import test_local_postgres as runner


def test_existing_demo_accounts_are_never_reseeded(monkeypatch, tmp_path):
    commands = []
    started = []
    monkeypatch.setattr(runner, "run", lambda *args, **kwargs: commands.append(args))

    def stop_at_launch(command, **kwargs):
        started.append(kwargs["env"])
        raise RuntimeError("stop before launching any process")

    monkeypatch.setattr(runner.subprocess, "Popen", stop_at_launch)
    accounts = {name: f"private-{name}" for name in
                ("builder_all", "viewer_all", "viewer_ca", "builder_tx")}
    with pytest.raises(RuntimeError, match="stop before launching"):
        runner.run_browser_gates(tmp_path, {}, e2e=False, llm_mode="live",
                                 live_smoke=True, existing_accounts=accounts)
    assert commands == []
    assert started[0]["E2E_BUILDER_ALL_PASSWORD"] == accounts["builder_all"]
    assert "E2E_DEMO_PASSWORD" not in started[0]
    assert not any(name.startswith("SEED_") for name in started[0])


def test_existing_accounts_cannot_enter_destructive_seed_or_fault_suite(tmp_path):
    with pytest.raises(ValueError, match="existing accounts"):
        runner.run_browser_gates(tmp_path, {}, existing_accounts={"builder_all": "private"})


def test_live_evaluator_is_explicit_and_isolated_from_mock_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, *, cwd, env):
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    base = {"APP_ENV": "test", "LLM_MODE": "mock", "LLM_API_KEY": "do-not-print"}

    assert runner.run_agent_evaluation(tmp_path, base, mode="live") == 0
    command, child_env = calls.pop()
    assert command[1:6] == ["scripts/evaluate_agent.py", "--mode", "live", "--phase", "full"]
    assert command[-2:] == ["--output", str(tmp_path / "live-full-evaluation.json")]
    assert child_env["LLM_MODE"] == "live"
    assert base["LLM_MODE"] == "mock"
    assert child_env is not base


def test_mock_evaluator_keeps_mock_mode_and_uses_separate_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, *, cwd, env):
        observed.update(command=command, env=env)
        (tmp_path / "mock-full-evaluation.json").write_text(
            '{"summary":{"PASS":1,"FAIL":0,"BLOCKED":0}}', encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    base = {"LLM_MODE": "mock"}
    assert runner.run_agent_evaluation(tmp_path, base, mode="mock") == 0
    assert observed["command"][-2:] == ["--output", str(tmp_path / "mock-full-evaluation.json")]
    assert observed["env"]["LLM_MODE"] == "mock"  # type: ignore[index]
    assert base == {"LLM_MODE": "mock"}


@pytest.mark.parametrize("returncode", [1, 2])
def test_live_evaluator_preserves_failure_and_blocker_exit_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int
) -> None:
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, returncode),
    )
    assert runner.run_agent_evaluation(tmp_path, {"LLM_MODE": "mock"}, mode="live") == returncode


def test_live_preflight_happens_before_binary_or_cluster_work_and_hides_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "sk-sensitive-preflight-value"

    def reject() -> None:
        raise ValueError(f"invalid key {secret}")

    monkeypatch.setattr(runner, "require_live_evaluation_preflight", reject)
    monkeypatch.setattr(sys, "argv", ["test_local_postgres.py", "--live-evaluate"])
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "live operation requires valid provider" in captured.err


def test_default_cli_does_not_run_live_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def preflight() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "require_live_evaluation_preflight", preflight)
    monkeypatch.setattr(sys, "argv", ["test_local_postgres.py", "--postgres-bin", "missing"])
    with pytest.raises(SystemExit):
        runner.main()
    assert called is False


@pytest.mark.parametrize("mode", ["mock", "live"])
def test_benchmark_process_uses_fixed_measurement_and_mode_specific_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, *, cwd, env):
        observed.update(command=command, cwd=cwd, env=env)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    base = {"LLM_MODE": "mock", "LLM_API_KEY": "environment-only"}
    assert runner.run_benchmark_process(
        tmp_path, base, base_url="http://127.0.0.1:3456", password="ephemeral", mode=mode
    ) == 0
    command = observed["command"]
    assert command[1:6] == ["scripts/benchmark.py", "--base-url", "http://127.0.0.1:3456", "--group", "both"]  # type: ignore[index]
    assert command[command.index("--expected-mode") + 1] == mode  # type: ignore[union-attr]
    assert command[command.index("--samples") + 1] == "30"  # type: ignore[union-attr]
    assert command[command.index("--warmup") + 1] == "3"  # type: ignore[union-attr]
    assert command[-2:] == ["--output", str(tmp_path / f"benchmark-{mode}.json")]  # type: ignore[index]
    assert observed["env"]["LLM_MODE"] == mode  # type: ignore[index]
    assert base["LLM_MODE"] == "mock"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--live-benchmark", "--e2e"],
        ["--live-benchmark", "--benchmark"],
        ["--live-benchmark", "--restore-only"],
        ["--live-benchmark", "--live-evaluate"],
    ],
)
def test_live_benchmark_rejects_conflicting_modes_before_preflight(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    called = False

    def preflight() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "require_live_evaluation_preflight", preflight)
    monkeypatch.setattr(sys, "argv", ["test_local_postgres.py", *arguments])
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    assert called is False


def test_live_benchmark_runs_preflight_before_binary_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def preflight() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "require_live_evaluation_preflight", preflight)
    monkeypatch.setattr(
        sys,
        "argv",
        ["test_local_postgres.py", "--live-benchmark", "--postgres-bin", "missing"],
    )
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    assert called is True


def test_live_smoke_uses_only_dedicated_playwright_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(command, *, cwd, env):
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    environment = {"LLM_MODE": "live", "E2E_DEMO_PASSWORD": "ephemeral"}
    assert runner.run_playwright_process(environment, live_smoke=True) == 0
    command, child_environment = calls.pop()
    assert command == [
        "node",
        "node_modules/@playwright/test/cli.js",
        "test",
        "--config",
        "playwright.live.config.ts",
    ]
    assert child_environment is environment
    assert child_environment["LLM_MODE"] == "live"


def test_default_playwright_command_remains_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.run_playwright_process({"LLM_MODE": "mock"}) == 0
    assert commands == [[
        "node", "node_modules/@playwright/test/cli.js", "test", "--reporter=line"
    ]]


@pytest.mark.parametrize(
    "conflict",
    ["--e2e", "--evaluate", "--live-evaluate", "--benchmark", "--live-benchmark", "--restore-only"],
)
def test_live_smoke_rejects_all_other_execution_modes_before_preflight(
    monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    called = False

    def preflight() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "require_live_evaluation_preflight", preflight)
    monkeypatch.setattr(sys, "argv", ["test_local_postgres.py", "--live-smoke", conflict])
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    assert called is False


def test_live_smoke_preflights_before_cluster_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def preflight() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "require_live_evaluation_preflight", preflight)
    monkeypatch.setattr(
        sys,
        "argv",
        ["test_local_postgres.py", "--live-smoke", "--postgres-bin", "missing"],
    )
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
    assert called is True
