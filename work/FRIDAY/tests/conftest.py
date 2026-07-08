"""Pytest configuration — set up auth for all tests."""
import os

# Set FRIDAY_DEV_MODE=1 and clear token BEFORE any config imports
os.environ["FRIDAY_DEV_MODE"] = "1"
os.environ["FRIDAY_API_TOKEN"] = ""
