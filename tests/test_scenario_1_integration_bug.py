import unittest

from app import UserStore


class IntegrationBugTests(unittest.TestCase):
    def test_email_lookup_case_insensitive(self) -> None:
        store = UserStore()

        store.add_user("TestUser@Example.com", "Indranil")

        result = store.get_user("testuser@example.com")

        self.assertEqual(result, "Indranil")


if __name__ == "__main__":
    unittest.main()
