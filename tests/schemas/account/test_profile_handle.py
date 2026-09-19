import unittest

from pydantic import ValidationError

from app.schemas.account.profile import HANDLE_LOOKUP_PATTERN, ProfileUpdateRequest


class ProfileHandleValidationTests(unittest.TestCase):
    def test_accepts_discord_compatible_handle(self):
        self.assertEqual(
            ProfileUpdateRequest(handle="user.name_123").handle, "user.name_123"
        )

    def test_rejects_disallowed_characters_and_uppercase(self):
        for handle in (
            "User",
            "user name",
            "user-name",
            "user@name",
            "user#name",
            "user:name",
            "user`name",
            "user😀",
        ):
            with self.subTest(handle=handle), self.assertRaises(ValidationError):
                ProfileUpdateRequest(handle=handle)

    def test_rejects_consecutive_periods(self):
        with self.assertRaises(ValidationError):
            ProfileUpdateRequest(handle="user..name")

    def test_allows_previously_blocked_substrings(self):
        for handle in ("everyone1", "is_here", "discord_user"):
            with self.subTest(handle=handle):
                self.assertEqual(ProfileUpdateRequest(handle=handle).handle, handle)

    def test_allows_uppercase_only_when_looking_up_a_handle(self):
        self.assertIsNotNone(HANDLE_LOOKUP_PATTERN.match("forestLee"))
        with self.assertRaises(ValidationError):
            ProfileUpdateRequest(handle="forestLee")
