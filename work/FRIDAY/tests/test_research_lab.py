"""Tests for the research lab."""
import asyncio
import pytest

from core.research_lab import (
    ResearchLab, Experiment, ExperimentType, ExperimentStatus,
    Hypothesis, ExperimentResult,
)


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.research_lab
    core.research_lab._lab = None
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    lab = ResearchLab(base_dir=research_dir)
    yield lab
    core.research_lab._lab = None


class TestExperimentCreation:
    def test_create_experiment(self, lab):
        exp = asyncio.run(lab.create_experiment(
            title="Test ANN vs brute-force",
            type=ExperimentType.ALGORITHM,
            hypothesis=Hypothesis(
                statement="ANN is 100x faster",
                expected_result="100x speedup",
                metrics=["latency", "recall"],
            ),
            setup="Populate 10k vectors, query 100 times",
        ))
        assert exp.id
        assert exp.title == "Test ANN vs brute-force"
        assert exp.status == ExperimentStatus.PROPOSED
        assert exp.hypothesis.statement == "ANN is 100x faster"

    def test_create_simple_experiment(self, lab):
        exp = asyncio.run(lab.create_experiment(title="Simple test"))
        assert exp.type == ExperimentType.FEASIBILITY
        assert exp.hypothesis.statement == ""

    def test_experiment_persists_to_disk(self, lab, tmp_path):
        exp = asyncio.run(lab.create_experiment(title="Persist test"))
        exp_path = tmp_path / "research" / f"{exp.id}.json"
        assert exp_path.exists()


class TestExperimentLifecycle:
    def test_start_experiment(self, lab):
        exp = asyncio.run(lab.create_experiment(title="Start test"))
        started = asyncio.run(lab.start_experiment(exp.id))
        assert started.status == ExperimentStatus.RUNNING

    def test_record_result(self, lab):
        exp = asyncio.run(lab.create_experiment(title="Result test"))
        asyncio.run(lab.start_experiment(exp.id))
        result = ExperimentResult(
            metrics={"latency_p99": 0.5, "recall": 0.98},
            conclusion="ANN is 100x faster",
            supports_hypothesis=True,
        )
        updated = asyncio.run(lab.record_result(exp.id, result))
        assert updated.status == ExperimentStatus.COMPLETED
        assert updated.result.metrics["latency_p99"] == 0.5
        assert updated.result.supports_hypothesis is True

    def test_abandon_experiment(self, lab):
        exp = asyncio.run(lab.create_experiment(title="Abandon test"))
        abandoned = asyncio.run(lab.abandon_experiment(exp.id, reason="Not feasible"))
        assert abandoned.status == ExperimentStatus.ABANDONED

    def test_get_experiment(self, lab):
        exp = asyncio.run(lab.create_experiment(title="Get test"))
        retrieved = asyncio.run(lab.get_experiment(exp.id))
        assert retrieved is not None
        assert retrieved.id == exp.id

    def test_get_nonexistent_returns_none(self, lab):
        result = asyncio.run(lab.get_experiment("nonexistent"))
        assert result is None


class TestExperimentListing:
    def test_list_experiments(self, lab):
        asyncio.run(lab.create_experiment(title="Exp 1"))
        asyncio.run(lab.create_experiment(title="Exp 2"))
        exps = asyncio.run(lab.list_experiments())
        assert len(exps) == 2

    def test_list_by_status(self, lab):
        exp1 = asyncio.run(lab.create_experiment(title="Proposed"))
        exp2 = asyncio.run(lab.create_experiment(title="Started"))
        asyncio.run(lab.start_experiment(exp2.id))

        proposed = asyncio.run(lab.list_experiments(status=ExperimentStatus.PROPOSED))
        running = asyncio.run(lab.list_experiments(status=ExperimentStatus.RUNNING))
        assert len(proposed) == 1
        assert len(running) == 1

    def test_list_by_type(self, lab):
        asyncio.run(lab.create_experiment(title="A", type=ExperimentType.ALGORITHM))
        asyncio.run(lab.create_experiment(title="B", type=ExperimentType.ABLATION))

        algos = asyncio.run(lab.list_experiments(type=ExperimentType.ALGORITHM))
        assert len(algos) == 1


class TestComparison:
    def test_compare_two_experiments(self, lab):
        exp1 = asyncio.run(lab.create_experiment(title="Exp A", type=ExperimentType.BENCHMARK))
        exp2 = asyncio.run(lab.create_experiment(title="Exp B", type=ExperimentType.BENCHMARK))

        asyncio.run(lab.record_result(exp1.id, ExperimentResult(
            metrics={"latency": 10.0, "accuracy": 0.95},
        )))
        asyncio.run(lab.record_result(exp2.id, ExperimentResult(
            metrics={"latency": 5.0, "accuracy": 0.90},
        )))

        comparison = asyncio.run(lab.compare_experiments([exp1.id, exp2.id]))
        assert "Exp A" in comparison["experiments"]
        assert "Exp B" in comparison["experiments"]
        assert "latency" in comparison["metrics"]
        assert comparison["best"]["latency"] == "Exp A"  # higher latency is "best" by max

    def test_compare_requires_two_experiments(self, lab):
        exp = asyncio.run(lab.create_experiment(title="Only one"))
        comparison = asyncio.run(lab.compare_experiments([exp.id]))
        assert "error" in comparison


class TestPersistence:
    def test_experiments_survive_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
        import core.research_lab
        core.research_lab._lab = None

        research_dir = tmp_path / "research"
        research_dir.mkdir()

        lab1 = ResearchLab(base_dir=research_dir)
        exp = asyncio.run(lab1.create_experiment(title="Survive restart"))

        # Restart
        core.research_lab._lab = None
        lab2 = ResearchLab(base_dir=research_dir)
        retrieved = asyncio.run(lab2.get_experiment(exp.id))
        assert retrieved is not None
        assert retrieved.title == "Survive restart"


class TestStats:
    def test_stats_returns_counts(self, lab):
        asyncio.run(lab.create_experiment(title="A"))
        asyncio.run(lab.create_experiment(title="B", type=ExperimentType.BENCHMARK))
        stats = asyncio.run(lab.get_stats())
        assert stats["total"] == 2
        assert stats["by_status"]["proposed"] == 2
        assert stats["by_type"]["benchmark"] == 1
