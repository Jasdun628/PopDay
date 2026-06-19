import unittest
from datetime import datetime, timezone

from flask_app import _stale_front_door_summary


class FrontDoorStatusTests(unittest.TestCase):
    def test_stale_copy_message_is_user_facing(self):
        updated_at = datetime(2026, 6, 17, 14, 40, tzinfo=timezone.utc)

        message = _stale_front_door_summary(updated_at, broken=True)

        self.assertIn("PopDay is not current", message)
        self.assertIn("Codex needs to refresh", message)
        self.assertNotIn("status JSON", message)
        self.assertNotIn("last Mac Mini sync", message)


if __name__ == "__main__":
    unittest.main()
