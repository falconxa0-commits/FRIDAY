import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

# MOCK DISPLAY
sys.modules['pyautogui'] = MagicMock()
sys.modules['mouseinfo'] = MagicMock()
sys.modules['Xlib'] = MagicMock()

async def test_vision_click():
    from control.pc_control import PCControl
    pc = PCControl()
    
    # 1. Mock the analyzer to return coordinates
    # (Since we can't run GPT-4o with real image here)
    with patch('vision.screen_analyzer.ScreenAnalyzer.find_element') as mock_find,          patch('vision.screen_reader.ScreenReader.capture_screen') as mock_cap,          patch('core.ledger.ActionLedger.wait_for_approval') as mock_wait:
        
        mock_find.return_value = {"x": 500, "y": 300}
        mock_cap.return_value = "test_shot.png"
        mock_wait.return_value = True # Auto-approve for test
        
        print("Testing click_described('Login Button')...")
        res = await pc.click_described("Login Button")
        print(f"Result Status: {res.get('status')}")
        print(f"Result Message: {res.get('message')}")

if __name__ == "__main__":
    asyncio.run(test_vision_click())
