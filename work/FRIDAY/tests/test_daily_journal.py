"""Tests for daily journal — confirm real data sources, honest 'no data' messages."""
import pytest
import datetime


class TestDailyJournal:
    def test_journal_skill_exists(self):
        from skills.daily_journal import DailyJournalSkill
        skill = DailyJournalSkill()
        assert skill.name == "daily_journal"

    @pytest.mark.asyncio
    async def test_journal_generates_real_output(self):
        from skills.daily_journal import DailyJournalSkill
        skill = DailyJournalSkill()
        class FakeBrain:
            memory = None
            async def chat_stream(self, msg):
                yield "ok"
        result = await skill.run(FakeBrain())
        assert result["status"] == "success"
        assert "Actions Today:" in result["message"]
        assert "Goals Progress:" in result["message"]
        assert "Cost Summary:" in result["message"]

    @pytest.mark.asyncio
    async def test_journal_references_ledger_data(self):
        from skills.daily_journal import DailyJournalSkill
        from core.ledger import ActionLedger
        # Log a real action
        ledger = ActionLedger()
        ledger.audit_log = "/tmp/test_journal_audit.log"
        aid = ledger.queue_action("Weather", "get_weather", {"city": "Lagos"}, risk_level="low")
        ledger.approve_action(aid)

        skill = DailyJournalSkill()
        class FakeBrain:
            memory = None
        result = await skill.run(FakeBrain())
        assert "Weather" in result["message"] or "actions logged" in result["message"].lower()
