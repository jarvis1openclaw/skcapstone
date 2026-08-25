"""CLI integration tests for trustee management commands.

Tests the Click CLI layer for agents restart, scale, rotate, health,
and logs commands using Click's CliRunner.

Unit tests for TrusteeOps are in test_trustee_cli.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from skcapstone.blueprints.schema import ProviderType
from skcapstone.cli import main
from skcapstone.team_engine import AgentStatus, DeployedAgent, TeamDeployment, TeamEngine

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_agent(
    name: str = "test-team-alpha",
    spec_key: str = "alpha",
    status: AgentStatus = AgentStatus.RUNNING,
) -> DeployedAgent:
    """Build a minimal DeployedAgent for tests."""
    return DeployedAgent(
        name=name,
        instance_id=f"test-deploy/{name}",
        blueprint_slug="test-team",
        agent_spec_key=spec_key,
        status=status,
        provider=ProviderType.LOCAL,
        host="localhost",
        pid=12345,
    )


def _make_deployment(
    deployment_id: str = "test-team-1000",
    agents: dict | None = None,
) -> TeamDeployment:
    """Build a minimal TeamDeployment for tests."""
    return TeamDeployment(
        deployment_id=deployment_id,
        blueprint_slug="test-team",
        team_name="Test Team",
        provider=ProviderType.LOCAL,
        agents=agents or {"test-team-alpha": _make_agent()},
        status="running",
    )


@pytest.fixture
def tmp_home(tmp_path: Path) -> Path:
    """Temporary skcapstone home directory."""
    home = tmp_path / ".skcapstone"
    home.mkdir()
    return home


def _invoke(*args: str) -> Any:
    """Invoke the main CLI with given args."""
    runner = CliRunner()
    return runner.invoke(main, list(args))


def _invoke_cli(*args: str) -> Any:
    """Invoke the canonical modular CLI (skcapstone.cli:main) with given args.

    The focused ``agents health --agent`` surface lives in the shipped
    modular CLI (the ``[project.scripts]`` entrypoint), which is separate
    from the legacy ``_cli_monolith`` used by ``_invoke``.
    """
    from skcapstone.cli import main as cli_main

    runner = CliRunner()
    return runner.invoke(cli_main, list(args))


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------


class TestCLIRestart:
    """CLI tests for `skcapstone agents restart`."""

    def test_restart_missing_deployment_shows_error(self, tmp_home: Path) -> None:
        """CLI shows error for unknown deployment."""
        result = _invoke("agents", "restart", "no-such-id", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_restart_valid_deployment_refuses_on_an_ungoverned_home(self, tmp_home: Path) -> None:
        """Card e51a3e7e: restart/scale/rotate now refuse unless the freeze
        store is human-provisioned AND a capauth PDP allow is granted. A
        bare `--home` with neither (this fixture, and every real fresh
        estate before someone runs the provisioning ceremony) refuses with
        reason "unprovisioned" -- the CLI has no way to inject a fake
        capauth grant the way TrusteeOps' unit tests do (test_trustee_cli.py),
        so this is the true end-to-end default. See
        tests/test_trustee_ops.py::TestActuationGate for the gate's
        allow/deny paths exercised directly against TrusteeOps.
        """
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke("agents", "restart", "test-team-1000", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "Refused (unprovisioned)" in result.output

    def test_restart_single_agent_flag_refuses_on_an_ungoverned_home(self, tmp_home: Path) -> None:
        """Same refusal as above; --agent does not bypass the gate."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke(
            "agents",
            "restart",
            "test-team-1000",
            "--agent",
            "test-team-alpha",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "Refused (unprovisioned)" in result.output

    def test_restart_unknown_agent_shows_error(self, tmp_home: Path) -> None:
        """--agent with unknown name shows error."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke(
            "agents",
            "restart",
            "test-team-1000",
            "--agent",
            "ghost",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "not in deployment" in result.output


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------


class TestCLIScale:
    """CLI tests for `skcapstone agents scale`."""

    def test_scale_up_refuses_on_an_ungoverned_home(self, tmp_home: Path) -> None:
        """Card e51a3e7e: same "unprovisioned" refusal as restart (see its
        test above for why the CLI cannot bypass the gate)."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke(
            "agents",
            "scale",
            "test-team-1000",
            "--agent",
            "alpha",
            "--count",
            "3",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "Refused (unprovisioned)" in result.output

    def test_scale_unknown_deployment_shows_error(self, tmp_home: Path) -> None:
        """CLI scale shows error for unknown deployment."""
        result = _invoke(
            "agents",
            "scale",
            "no-such-id",
            "--agent",
            "alpha",
            "--count",
            "2",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_scale_requires_agent_option(self, tmp_home: Path) -> None:
        """--agent is required for scale command."""
        result = _invoke(
            "agents",
            "scale",
            "test-team-1000",
            "--count",
            "2",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code != 0

    def test_scale_requires_count_option(self, tmp_home: Path) -> None:
        """--count is required for scale command."""
        result = _invoke(
            "agents",
            "scale",
            "test-team-1000",
            "--agent",
            "alpha",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Rotate
# ---------------------------------------------------------------------------


class TestCLIRotate:
    """CLI tests for `skcapstone agents rotate`."""

    def test_rotate_refuses_on_an_ungoverned_home(self, tmp_home: Path) -> None:
        """Card e51a3e7e: same "unprovisioned" refusal as restart/scale
        (readiness is checked before the approved-change requirement, so
        this refuses for the same reason even without --change-id)."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke(
            "agents",
            "rotate",
            "test-team-1000",
            "--agent",
            "test-team-alpha",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "Refused (unprovisioned)" in result.output

    def test_rotate_unknown_agent_shows_error(self, tmp_home: Path) -> None:
        """CLI rotate shows error for unknown agent."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke(
            "agents",
            "rotate",
            "test-team-1000",
            "--agent",
            "ghost-agent",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "not in deployment" in result.output

    def test_rotate_unknown_deployment_shows_error(self, tmp_home: Path) -> None:
        """CLI rotate shows error for unknown deployment."""
        result = _invoke(
            "agents",
            "rotate",
            "no-deploy",
            "--agent",
            "any-agent",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_rotate_requires_agent_option(self, tmp_home: Path) -> None:
        """--agent is required for rotate command."""
        result = _invoke(
            "agents",
            "rotate",
            "test-team-1000",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestCLIHealth:
    """CLI tests for `skcapstone agents health`."""

    def test_health_shows_table(self, tmp_home: Path) -> None:
        """CLI health shows agent table."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke("agents", "health", "test-team-1000", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "test-team-alpha" in result.output

    def test_health_unknown_deployment_shows_error(self, tmp_home: Path) -> None:
        """CLI health shows error for unknown deployment."""
        result = _invoke("agents", "health", "ghost-deploy", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_health_shows_healthy_count(self, tmp_home: Path) -> None:
        """Health report panel shows healthy fraction."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke("agents", "health", "test-team-1000", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "healthy" in result.output.lower()

    def test_health_agent_focus_resolves_sentinel(self, tmp_home: Path) -> None:
        """`--agent sentinel` resolves the Sentinel role across deployments."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(
            _make_deployment(
                agents={
                    "test-team-sentinel": _make_agent(
                        name="test-team-sentinel",
                        spec_key="sentinel",
                    ),
                }
            )
        )

        result = _invoke_cli("agents", "health", "--agent", "sentinel", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "test-team-sentinel" in result.output

    def test_health_agent_focus_reports_absent(self, tmp_home: Path) -> None:
        """`--agent sentinel` reports absent when no Sentinel is deployed."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())  # only alpha, no sentinel

        result = _invoke_cli("agents", "health", "--agent", "sentinel", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "absent" in result.output.lower()

    def test_health_without_target_prompts(self, tmp_home: Path) -> None:
        """`agents health` with neither id nor --agent shows guidance."""
        result = _invoke_cli("agents", "health", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "--agent" in result.output


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


class TestCLILogs:
    """CLI tests for `skcapstone agents logs`."""

    def test_logs_shows_output(self, tmp_home: Path) -> None:
        """CLI logs runs without error for a valid deployment."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())

        result = _invoke("agents", "logs", "test-team-1000", "--home", str(tmp_home))
        assert result.exit_code == 0

    def test_logs_reads_file_content(self, tmp_home: Path) -> None:
        """CLI logs shows file content when log file exists."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())
        log_dir = tmp_home / "agents" / "local" / "test-team-alpha"
        log_dir.mkdir(parents=True)
        (log_dir / "agent.log").write_text("hello from agent\n")

        result = _invoke("agents", "logs", "test-team-1000", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "hello from agent" in result.output

    def test_logs_unknown_deployment_shows_error(self, tmp_home: Path) -> None:
        """CLI logs shows error for unknown deployment."""
        result = _invoke("agents", "logs", "no-deploy", "--home", str(tmp_home))
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_logs_single_agent_flag(self, tmp_home: Path) -> None:
        """--agent flag restricts logs to one agent."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())
        log_dir = tmp_home / "agents" / "local" / "test-team-alpha"
        log_dir.mkdir(parents=True)
        (log_dir / "agent.log").write_text("agent specific log\n")

        result = _invoke(
            "agents",
            "logs",
            "test-team-1000",
            "--agent",
            "test-team-alpha",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "agent specific log" in result.output

    def test_logs_tail_option(self, tmp_home: Path) -> None:
        """--tail limits output lines per agent."""
        engine = TeamEngine(home=tmp_home)
        engine._save_deployment(_make_deployment())
        log_dir = tmp_home / "agents" / "local" / "test-team-alpha"
        log_dir.mkdir(parents=True)
        lines = "\n".join(f"line{i}" for i in range(20))
        (log_dir / "agent.log").write_text(lines)

        result = _invoke(
            "agents",
            "logs",
            "test-team-1000",
            "--tail",
            "3",
            "--home",
            str(tmp_home),
        )
        assert result.exit_code == 0
        assert "line19" in result.output
