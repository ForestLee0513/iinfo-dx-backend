import unittest
from unittest.mock import patch

from app.crud.account.follows import can_view_private_profile


class PrivateProfileVisibilityTests(unittest.TestCase):
    def test_owner_can_view_private_profile(self):
        self.assertTrue(can_view_private_profile("owner", "owner"))

    def test_anonymous_viewer_cannot_view_private_profile(self):
        self.assertFalse(can_view_private_profile(None, "owner"))

    @patch("app.crud.account.follows.is_following", side_effect=[True, True])
    def test_mutual_followers_can_view_private_profile(self, is_following):
        self.assertTrue(can_view_private_profile("viewer", "owner"))
        is_following.assert_any_call("viewer", "owner")
        is_following.assert_any_call("owner", "viewer")

    @patch("app.crud.account.follows.is_following", side_effect=[True, False])
    def test_one_way_follower_cannot_view_private_profile(self, _):
        self.assertFalse(can_view_private_profile("viewer", "owner"))
