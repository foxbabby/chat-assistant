"""Listening and permission polling must never invoke screen capture APIs."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from wechat import WeChat

class NoCaptureTests(unittest.TestCase):
    def test_permission_poll_does_not_probe_screen_recording(self):
        with patch('perception.screen_capture_ok', side_effect=AssertionError('screen probe')), patch('fill.has_accessibility', return_value=True):
            self.assertEqual(WeChat().permissions(), {'screen': None, 'accessibility': True})

    def test_read_only_uses_accessibility(self):
        snapshot = {'chat_title': 'test', 'messages': []}
        with patch('perception.read_conversation', side_effect=AssertionError('capture')), patch('fill.has_accessibility', return_value=True), patch('ax_reader.read_accessibility', return_value=snapshot):
            self.assertIs(WeChat().read(), snapshot)
