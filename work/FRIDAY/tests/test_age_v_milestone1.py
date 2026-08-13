"""Tests for FRIDAY Age V — Civilization Core (Milestone 1).

Tests:
    - Citizen: creation, lifecycle, capabilities, trust
    - CitizenRegistry: registration, lookup, status updates, stats
    - IdentityEngine: token issuance, verification, revocation
    - ReputationSystem: scoring, autonomy levels, auto-approval
    - CivilizationManager: initialization, spawning, departments
    - Constitution: articles, amendments, violations
    - GovernanceEngine: policies, evaluation, decision log
    - ApprovalGate: requests, approval, rejection, voting, timeout
"""
import asyncio
import pytest

from core.civilization.citizen import (
    Citizen, CitizenID, CitizenRank, CitizenStatus, CitizenRegistry,
)
from core.civilization.identity import IdentityEngine, CapabilityToken
from core.civilization.reputation import ReputationSystem
from core.civilization.manager import CivilizationManager, Department
from core.governance.constitution import Constitution, ConstitutionArticle, ArticleType
from core.governance.policy import Policy, PolicyDecision, GovernanceEngine
from core.governance.approval import ApprovalGate, ApprovalRequest, ApprovalStatus


# ============ Citizen ============

class TestCitizen:
    def test_citizen_creation(self):
        cid = CitizenID(name="TestAgent", rank=CitizenRank.WORKER)
        c = Citizen(id=cid, name="TestAgent", rank=CitizenRank.WORKER)
        assert c.name == "TestAgent"
        assert c.rank == CitizenRank.WORKER
        assert c.status == CitizenStatus.PENDING
        assert c.trust_score == 50

    def test_rank_authority(self):
        assert CitizenRank.FOUNDER.authority_level > CitizenRank.GOVERNOR.authority_level
        assert CitizenRank.GOVERNOR.authority_level > CitizenRank.SPECIALIST.authority_level
        assert CitizenRank.SPECIALIST.authority_level > CitizenRank.WORKER.authority_level

    def test_can_spawn(self):
        c = Citizen(id=CitizenID(), rank=CitizenRank.WORKER)
        assert c.can_spawn is False
        c = Citizen(id=CitizenID(), rank=CitizenRank.GOVERNOR)
        assert c.can_spawn is True

    def test_can_approve(self):
        c = Citizen(id=CitizenID(), rank=CitizenRank.WORKER)
        assert c.can_approve is False
        c = Citizen(id=CitizenID(), rank=CitizenRank.SPECIALIST)
        assert c.can_approve is True

    def test_capabilities(self):
        c = Citizen(id=CitizenID(), capabilities={"memory.read"})
        assert c.has_capability("memory.read") is True
        assert c.has_capability("memory.write") is False
        c.grant_capability("memory.write")
        assert c.has_capability("memory.write") is True
        c.revoke_capability("memory.read")
        assert c.has_capability("memory.read") is False

    def test_trust_adjustment(self):
        c = Citizen(id=CitizenID())
        c.adjust_trust(20)
        assert c.trust_score == 70
        c.adjust_trust(-100)  # clamped
        assert c.trust_score == 0
        c.adjust_trust(200)  # clamped
        assert c.trust_score == 100

    def test_to_dict(self):
        c = Citizen(id=CitizenID(name="Test"), name="Test", rank=CitizenRank.WORKER)
        d = c.to_dict()
        assert d["name"] == "Test"
        assert d["rank"] == "worker"
        assert d["status"] == "pending"


# ============ CitizenRegistry ============

class TestCitizenRegistry:
    @pytest.mark.asyncio
    async def test_register(self):
        reg = CitizenRegistry()
        citizen = await reg.register("Agent1", CitizenRank.WORKER)
        assert citizen.name == "Agent1"
        assert citizen.rank == CitizenRank.WORKER

    @pytest.mark.asyncio
    async def test_get(self):
        reg = CitizenRegistry()
        citizen = await reg.register("Agent1")
        retrieved = await reg.get(citizen.id.id)
        assert retrieved is not None
        assert retrieved.name == "Agent1"

    @pytest.mark.asyncio
    async def test_list(self):
        reg = CitizenRegistry()
        await reg.register("Worker1", CitizenRank.WORKER)
        await reg.register("Gov1", CitizenRank.GOVERNOR)
        all_citizens = await reg.list()
        assert len(all_citizens) == 2
        governors = await reg.list(rank=CitizenRank.GOVERNOR)
        assert len(governors) == 1

    @pytest.mark.asyncio
    async def test_update_status(self):
        reg = CitizenRegistry()
        citizen = await reg.register("Agent1")
        result = await reg.update_status(citizen.id.id, CitizenStatus.ACTIVE)
        assert result is True
        retrieved = await reg.get(citizen.id.id)
        assert retrieved.status == CitizenStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_retire(self):
        reg = CitizenRegistry()
        citizen = await reg.register("Agent1")
        result = await reg.retire(citizen.id.id)
        assert result is True
        retrieved = await reg.get(citizen.id.id)
        assert retrieved.status == CitizenStatus.RETIRED

    @pytest.mark.asyncio
    async def test_ban(self):
        reg = CitizenRegistry()
        citizen = await reg.register("BadAgent")
        await reg.ban(citizen.id.id)
        retrieved = await reg.get(citizen.id.id)
        assert retrieved.status == CitizenStatus.BANNED

    @pytest.mark.asyncio
    async def test_founder_registration(self):
        reg = CitizenRegistry()
        founder = await reg.register("Founder", CitizenRank.FOUNDER)
        retrieved = await reg.get_founder()
        assert retrieved is not None
        assert retrieved.id.id == founder.id.id

    @pytest.mark.asyncio
    async def test_count(self):
        reg = CitizenRegistry()
        await reg.register("A1")
        await reg.register("A2")
        assert await reg.count() == 2

    def test_stats(self):
        reg = CitizenRegistry()
        stats = reg.get_stats()
        assert stats["total_citizens"] == 0


# ============ IdentityEngine ============

class TestIdentityEngine:
    def test_issue_token(self):
        engine = IdentityEngine()
        citizen = Citizen(id=CitizenID(name="Test"), name="Test")
        token = engine.issue_token(citizen, ["memory.read"])
        assert token.citizen_id == citizen.id.id
        assert "memory.read" in token.capabilities
        assert token.is_valid is True

    def test_verify_token(self):
        engine = IdentityEngine()
        citizen = Citizen(id=CitizenID(name="Test"), name="Test")
        token = engine.issue_token(citizen, ["memory.read"])
        assert engine.verify_token(token.token, "memory.read") is True
        assert engine.verify_token(token.token, "memory.write") is False

    def test_revoke_token(self):
        engine = IdentityEngine()
        citizen = Citizen(id=CitizenID(name="Test"), name="Test")
        token = engine.issue_token(citizen, ["memory.read"])
        engine.revoke_token(token.token)
        assert engine.verify_token(token.token, "memory.read") is False

    def test_revoke_all_for_citizen(self):
        engine = IdentityEngine()
        citizen = Citizen(id=CitizenID(name="Test"), name="Test")
        t1 = engine.issue_token(citizen, ["a"])
        t2 = engine.issue_token(citizen, ["b"])
        count = engine.revoke_all_for_citizen(citizen.id.id)
        assert count == 2
        assert not engine.verify_token(t1.token, "a")
        assert not engine.verify_token(t2.token, "b")

    def test_list_tokens(self):
        engine = IdentityEngine()
        citizen = Citizen(id=CitizenID(name="Test"), name="Test")
        engine.issue_token(citizen, ["a"])
        engine.issue_token(citizen, ["b"])
        tokens = engine.list_tokens(citizen.id.id)
        assert len(tokens) == 2

    def test_stats(self):
        engine = IdentityEngine()
        stats = engine.get_stats()
        assert stats["total_tokens"] == 0


# ============ ReputationSystem ============

class TestReputationSystem:
    def test_record_success(self):
        rep = ReputationSystem()
        score = rep.record_event("agent-1", "task_success", "Completed task")
        assert score == 55  # 50 + 5

    def test_record_failure(self):
        rep = ReputationSystem()
        score = rep.record_event("agent-1", "task_failure", "Failed task")
        assert score == 40  # 50 - 10

    def test_founder_praise(self):
        rep = ReputationSystem()
        score = rep.record_event("agent-1", "founder_praise", "Excellent work")
        assert score == 65  # 50 + 15

    def test_founder_warning(self):
        rep = ReputationSystem()
        score = rep.record_event("agent-1", "founder_warning", "Security violation")
        assert score == 30  # 50 - 20

    def test_score_clamping(self):
        rep = ReputationSystem()
        rep.record_event("a", "task_failure", "", custom_delta=-100)
        assert rep.get_score("a") == 0
        rep.record_event("a", "task_success", "", custom_delta=200)
        assert rep.get_score("a") == 100

    def test_autonomy_level(self):
        rep = ReputationSystem()
        rep._scores["low"] = 10
        rep._scores["medium"] = 60
        rep._scores["high"] = 90
        assert rep.get_autonomy_level("low") == "none"
        assert rep.get_autonomy_level("medium") == "medium"
        assert rep.get_autonomy_level("high") == "high"

    def test_can_auto_approve(self):
        rep = ReputationSystem()
        rep._scores["trusted"] = 85
        rep._scores["untrusted"] = 10
        assert rep.can_auto_approve("trusted", "high") is True
        assert rep.can_auto_approve("trusted", "critical") is False
        assert rep.can_auto_approve("untrusted", "low") is False

    def test_history(self):
        rep = ReputationSystem()
        rep.record_event("a", "task_success")
        rep.record_event("a", "task_failure")
        history = rep.get_history("a")
        assert len(history) == 2

    def test_reset_score(self):
        rep = ReputationSystem()
        rep._scores["a"] = 90
        rep.reset_score("a")
        assert rep.get_score("a") == 50


# ============ CivilizationManager ============

class TestCivilizationManager:
    @pytest.mark.asyncio
    async def test_initialize(self):
        mgr = CivilizationManager()
        founder = await mgr.initialize("TestFounder")
        assert founder.name == "TestFounder"
        assert founder.rank == CitizenRank.FOUNDER
        assert mgr._initialized is True

    @pytest.mark.asyncio
    async def test_departments_created(self):
        mgr = CivilizationManager()
        await mgr.initialize()
        departments = mgr.list_departments()
        assert len(departments) == 7  # 7 default departments

    @pytest.mark.asyncio
    async def test_spawn_citizen(self):
        mgr = CivilizationManager()
        await mgr.initialize()
        citizen = await mgr.spawn_citizen(
            name="Worker1",
            rank=CitizenRank.WORKER,
            department="Runtime",
        )
        assert citizen.name == "Worker1"
        assert citizen.status == CitizenStatus.ACTIVE
        dept = mgr.get_department("Runtime")
        assert citizen.id.id in dept.member_ids

    @pytest.mark.asyncio
    async def test_appoint_governor(self):
        mgr = CivilizationManager()
        await mgr.initialize()
        citizen = await mgr.spawn_citizen("Gov1", CitizenRank.SPECIALIST)
        result = await mgr.appoint_governor(citizen.id.id, "Runtime")
        assert result is True
        dept = mgr.get_department("Runtime")
        assert dept.governor_id == citizen.id.id

    @pytest.mark.asyncio
    async def test_retire_citizen(self):
        mgr = CivilizationManager()
        await mgr.initialize()
        citizen = await mgr.spawn_citizen("Agent1")
        result = await mgr.retire_citizen(citizen.id.id)
        assert result is True

    @pytest.mark.asyncio
    async def test_civilization_status(self):
        mgr = CivilizationManager()
        await mgr.initialize()
        await mgr.spawn_citizen("Worker1", CitizenRank.WORKER)
        status = await mgr.get_civilization_status()
        assert status["initialized"] is True
        assert status["total_citizens"] >= 2  # founder + worker
        assert status["has_founder"] is True

    @pytest.mark.asyncio
    async def test_health(self):
        mgr = CivilizationManager()
        assert await mgr.is_healthy() is False
        await mgr.initialize()
        assert await mgr.is_healthy() is True


# ============ Constitution ============

class TestConstitution:
    def test_articles_exist(self):
        c = Constitution()
        articles = c.list_articles()
        assert len(articles) == 8

    def test_get_article(self):
        c = Constitution()
        article = c.get_article(1)
        assert article is not None
        assert article.title == "Founder Sovereignty"

    def test_check_violation(self):
        c = Constitution()
        assert c.check_violation("autonomous_modification") is True
        assert c.check_violation("bypass_security") is True
        assert c.check_violation("normal_action") is False

    def test_amend(self):
        c = Constitution()
        result = c.amend(1, "New text", "founder-123")
        assert result is True
        article = c.get_article(1)
        assert article.text == "New text"

    def test_amendments_log(self):
        c = Constitution()
        c.amend(1, "New", "founder")
        amendments = c.get_amendments()
        assert len(amendments) == 1

    def test_to_dict(self):
        c = Constitution()
        d = c.to_dict()
        assert d["article_count"] == 8

    @pytest.mark.asyncio
    async def test_is_healthy(self):
        c = Constitution()
        assert await c.is_healthy() is True


# ============ GovernanceEngine ============

class TestGovernanceEngine:
    def test_add_policy(self):
        engine = GovernanceEngine()
        engine.add_policy(Policy(name="test", effect="allow", capabilities=["test.cap"]))
        policies = engine.list_policies()
        assert len(policies) == 1

    def test_evaluate_allow(self):
        engine = GovernanceEngine()
        engine.add_policy(Policy(name="allow_test", effect="allow", capabilities=["test.cap"]))
        decision = engine.evaluate("test.cap")
        assert decision.allowed is True
        assert decision.policy_name == "allow_test"

    def test_evaluate_deny(self):
        engine = GovernanceEngine()
        decision = engine.evaluate("unknown.cap")
        assert decision.allowed is False
        assert "deny by default" in decision.reason

    def test_evaluate_priority(self):
        engine = GovernanceEngine()
        engine.add_policy(Policy(name="low", effect="deny", capabilities=["cap"], priority=1))
        engine.add_policy(Policy(name="high", effect="allow", capabilities=["cap"], priority=10))
        decision = engine.evaluate("cap")
        assert decision.allowed is True
        assert decision.policy_name == "high"

    def test_remove_policy(self):
        engine = GovernanceEngine()
        engine.add_policy(Policy(name="test", effect="allow"))
        assert engine.remove_policy("test") is True
        assert len(engine.list_policies()) == 0

    def test_decision_log(self):
        engine = GovernanceEngine()
        engine.evaluate("cap1")
        engine.evaluate("cap2")
        log = engine.get_decision_log()
        assert len(log) == 2

    def test_stats(self):
        engine = GovernanceEngine()
        engine.add_policy(Policy(name="test", effect="allow"))
        engine.evaluate("test.cap")
        stats = engine.get_stats()
        assert stats["total_policies"] == 1
        assert stats["total_decisions"] == 1


# ============ ApprovalGate ============

class TestApprovalGate:
    def test_request(self):
        gate = ApprovalGate()
        req = gate.request("agent-1", "deploy", "Deploy to prod", "critical")
        assert req.status == ApprovalStatus.PENDING
        assert req.risk_level == "critical"

    @pytest.mark.asyncio
    async def test_approve(self):
        gate = ApprovalGate()
        req = gate.request("agent-1", "action", "desc")
        result = gate.approve(req.id, "approver-1")
        assert result is True
        retrieved = gate.get_request(req.id)
        assert retrieved.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_reject(self):
        gate = ApprovalGate()
        req = gate.request("agent-1", "action", "desc")
        gate.reject(req.id, "rejector-1")
        retrieved = gate.get_request(req.id)
        assert retrieved.status == ApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_wait_for_approval_approved(self):
        gate = ApprovalGate()
        req = gate.request("agent-1", "action", "desc", timeout_seconds=5)

        async def approve_later():
            await asyncio.sleep(0.1)
            gate.approve(req.id, "approver")

        asyncio.create_task(approve_later())
        result = await gate.wait_for_approval(req.id, timeout=5)
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_approval_timeout(self):
        gate = ApprovalGate()
        req = gate.request("agent-1", "action", "desc", timeout_seconds=1)
        result = await gate.wait_for_approval(req.id, timeout=1)
        assert result is False

    def test_founder_override(self):
        gate = ApprovalGate()
        req = gate.request("agent-1", "action", "desc")
        result = gate.founder_override(req.id, True, "founder-1")
        assert result is True
        retrieved = gate.get_request(req.id)
        assert retrieved.status == ApprovalStatus.APPROVED
        assert retrieved.approved_by == "founder-1"

    def test_voting(self):
        gate = ApprovalGate()
        req = gate.request("agent-1", "action", "desc")
        gate.vote(req.id, "voter-1", "approve")
        gate.vote(req.id, "voter-2", "approve")
        gate.vote(req.id, "voter-3", "reject")
        retrieved = gate.get_request(req.id)
        assert retrieved.status == ApprovalStatus.APPROVED  # 2 approve > 1 reject

    def test_list_requests(self):
        gate = ApprovalGate()
        gate.request("a1", "action1")
        gate.request("a2", "action2")
        pending = gate.list_requests(status=ApprovalStatus.PENDING)
        assert len(pending) == 2

    def test_stats(self):
        gate = ApprovalGate()
        gate.request("a1", "action")
        stats = gate.get_stats()
        assert stats["total_requests"] == 1
        assert stats["pending"] == 1
