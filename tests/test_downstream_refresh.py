"""Tests for the post-scan downstream cache refresh (July 2026 pipeline fix).

A scan that writes detections must also refresh derived stores (price
reactions, hype tracking) — and a failure in either must never break the scan.
"""

from __future__ import annotations

import unittest
from unittest import mock

from popday import cli


class DownstreamRefreshTests(unittest.TestCase):
    def test_both_refreshes_run_and_return_no_notes(self):
        with mock.patch.object(cli, "refresh_price_reactions", return_value=[1, 2]) as pr, \
                mock.patch.object(cli, "watch_hype_candidates", return_value=[1]) as hype:
            note = cli._refresh_downstream_caches(
                mock.Mock(sec_user_agent="ua"), mock.Mock()
            )
        pr.assert_called_once()
        hype.assert_called_once()
        self.assertEqual(note, "")

    def test_price_refresh_failure_is_reported_not_raised(self):
        with mock.patch.object(cli, "refresh_price_reactions", side_effect=RuntimeError("feed down")), \
                mock.patch.object(cli, "watch_hype_candidates", return_value=[]):
            note = cli._refresh_downstream_caches(
                mock.Mock(sec_user_agent="ua"), mock.Mock()
            )
        self.assertIn("price reaction refresh failed", note)
        self.assertIn("feed down", note)

    def test_hype_failure_does_not_mask_price_success(self):
        with mock.patch.object(cli, "refresh_price_reactions", return_value=[]), \
                mock.patch.object(cli, "watch_hype_candidates", side_effect=RuntimeError("edgar 403")):
            note = cli._refresh_downstream_caches(
                mock.Mock(sec_user_agent="ua"), mock.Mock()
            )
        self.assertIn("hype watcher failed", note)
        self.assertNotIn("price reaction", note)


if __name__ == "__main__":
    unittest.main()
