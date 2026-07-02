import asyncio
import os
import sys
sys.path.append(os.getcwd())
from core.brain import FridayBrain
from unittest.mock import patch, MagicMock

async def test_skills():
    brain = FridayBrain()
    print(f"Skills Discovered: {list(brain.skills.keys())}")
    
    # 1. File Audit
    print("\nRunning Skill: File Audit")
    res = await brain.skills["File Audit"].run(brain)
    print(f"Result: {res.get('message')}")
    print(f"Receipt Items: {len(res.get('receipt', {}).get('data', {}).get('files', []))}")

    # 2. Morning Briefing (Mocked)
    print("\nRunning Skill: Morning Briefing")
    with patch('core.universal_connector.UniversalConnector.execute_action', new_callable=MagicMock) as mock_exec:
        mock_execute = AsyncMock(side_effect=[
            {"status": "success", "message": "Weather: 30C"},
            {"status": "success", "message": "Calendar: No events"}
        ])
        brain.connector.execute_action = mock_execute
        res = await brain.skills["Morning Briefing"].run(brain)
        print(f"Result: {res.get('message')}")

if __name__ == "__main__":
    asyncio.run(test_skills())
