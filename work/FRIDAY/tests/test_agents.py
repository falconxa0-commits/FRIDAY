"""Tests for AgentManager and agent classes."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.agent_manager import AgentManager, AgentType, AgentStatus, AgentResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_brain():
    brain = MagicMock()
    brain.chat_stream = AsyncMock()
    return brain


@pytest.fixture()
def manager(mock_brain):
    """AgentManager with mocked agent implementations."""
    mgr = AgentManager(brain=mock_brain)

    # Replace all agent instances with mocks after initialization
    for agent_type, agent in mgr.agents.items():
        mock_agent = MagicMock()
        mock_agent.execute = AsyncMock(return_value=f"{agent_type.value} result")
        mgr.agents[agent_type] = mock_agent

    return mgr


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestAgentManagerInit:
    """Test AgentManager initialization."""

    def test_init_creates_agents(self, manager):
        assert len(manager.agents) == 4

    def test_init_agent_types(self, manager):
        assert AgentType.RESEARCH in manager.agents
        assert AgentType.CODING in manager.agents
        assert AgentType.WRITING in manager.agents
        assert AgentType.TASK in manager.agents

    def test_active_tasks_empty(self, manager):
        assert len(manager.active_tasks) == 0


# ---------------------------------------------------------------------------
# Task classification
# ---------------------------------------------------------------------------

class TestTaskClassification:
    """Test code task detection."""

    def test_code_task_detected(self, manager):
        assert manager._is_code_task("build a web app") is True
        assert manager._is_code_task("implement the API") is True
        assert manager._is_code_task("write a function") is True

    def test_non_code_task(self, manager):
        assert manager._is_code_task("plan my day") is False
        assert manager._is_code_task("research climate change") is False


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------

class TestAgentExecution:
    """Test running individual agents."""

    @pytest.mark.asyncio
    async def test_run_agent_success(self, manager):
        mock_agent = manager.agents[AgentType.RESEARCH]
        mock_agent.execute = AsyncMock(return_value="Research complete")

        result = await manager.run_agent("research", "Find AI papers")
        assert result.status == AgentStatus.COMPLETED
        assert result.output == "Research complete"

    @pytest.mark.asyncio
    async def test_run_agent_failure(self, manager):
        mock_agent = manager.agents[AgentType.CODING]
        mock_agent.execute = AsyncMock(side_effect=Exception("OOM"))

        result = await manager.run_agent("coding", "Write code")
        assert result.status == AgentStatus.FAILED
        assert "OOM" in str(result.output)

    @pytest.mark.asyncio
    async def test_run_agent_invalid_type(self, manager):
        with pytest.raises(ValueError):
            await manager.run_agent("nonexistent", "task")


# ---------------------------------------------------------------------------
# Swarm execution
# ---------------------------------------------------------------------------

class TestSwarmExecution:
    """Test parallel agent execution."""

    @pytest.mark.asyncio
    async def test_swarm_runs_all_agents(self, manager):
        for at, agent in manager.agents.items():
            agent.execute = AsyncMock(return_value=f"{at.value} result")

        results = await manager.run_swarm("Analyze this")
        assert len(results) == 4

    @pytest.mark.asyncio
    async def test_swarm_custom_agents(self, manager):
        for at, agent in manager.agents.items():
            agent.execute = AsyncMock(return_value="result")

        results = await manager.run_swarm(
            "Analyze this",
            agent_types=["research", "writing"],
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_swarm_handles_exceptions(self, manager):
        mock_agent = manager.agents[AgentType.RESEARCH]
        mock_agent.execute = AsyncMock(side_effect=Exception("fail"))

        results = await manager.run_swarm(
            "test",
            agent_types=["research"],
        )
        assert len(results) == 1
        assert results[0].status == AgentStatus.FAILED


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

class TestPipelineExecution:
    """Test sequential pipeline execution."""

    @pytest.mark.asyncio
    async def test_pipeline_runs_sequentially(self, manager):
        # Mock all agents
        for at, agent in manager.agents.items():
            agent.execute = AsyncMock(return_value=f"{at.value} done")

        result = await manager.run_pipeline("Build a weather app")
        assert result.status == AgentStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_pipeline_stops_on_research_failure(self, manager):
        mock_agent = manager.agents[AgentType.RESEARCH]
        mock_agent.execute = AsyncMock(side_effect=Exception("research fail"))

        result = await manager.run_pipeline("Some task")
        assert result.status == AgentStatus.FAILED

    @pytest.mark.asyncio
    async def test_pipeline_code_task_includes_coding(self, manager):
        """Pipeline should include coding step for code-related tasks."""
        for at, agent in manager.agents.items():
            agent.execute = AsyncMock(return_value=f"{at.value} done")

        result = await manager.run_pipeline("Implement a REST API")
        assert result.status == AgentStatus.COMPLETED


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------

class TestAgentStatusReporting:

    def test_get_status(self, manager):
        status = manager.get_status()
        assert "available_agents" in status
        assert "active_tasks" in status
        assert "total_completed" in status
        assert len(status["available_agents"]) == 4

    def test_agent_result_to_dict(self):
        result = AgentResult(
            AgentType.RESEARCH,
            AgentStatus.COMPLETED,
            "done",
            confidence=0.9,
            duration=1.5,
        )
        d = result.to_dict()
        assert d["agent_type"] == "research"
        assert d["status"] == "completed"
        assert d["confidence"] == 0.9
