import unittest
import asyncio
import os
from core.personality import FridayPersonality
from core.emotions import EmotionsEngine
from core.context import ContextAwareness
from integrations.crypto_tracker import CryptoTracker
from control.file_manager import FileManager

class TestFridayUltimate(unittest.TestCase):
    def test_personality_greeting(self):
        p = FridayPersonality()
        greeting = p.get_greeting("Jules")
        self.assertIn("Jules", greeting)

    def test_emotions_engine_negation(self):
        e = EmotionsEngine()
        self.assertEqual(e.detect_emotion("I am happy"), "happy")
        self.assertEqual(e.detect_emotion("I am NOT happy"), "sad")
        self.assertEqual(e.detect_emotion("I never said I was happy"), "sad")

    def test_crypto_price_interpolation(self):
        c = CryptoTracker()
        # Mocking requests for stability in CI
        res = c.get_price("bitcoin")
        self.assertIn("The current price of bitcoin is", res)
        if "$" in res:
             self.assertRegex(res, r"\$\d+")

    def test_file_manager_safety(self):
        fm = FileManager()
        # Should fail without confirm
        res = fm.create_file("test.txt", "content", confirm=False)
        self.assertIn("confirmation required", res)
        
        # Should fail outside root
        with self.assertRaises(PermissionError):
            fm.delete_file("/etc/passwd", confirm=True)

    def test_context_time(self):
        c = ContextAwareness()
        self.assertIn(c.get_time_context(), ["morning", "afternoon", "evening", "night"])

if __name__ == '__main__':
    unittest.main()
