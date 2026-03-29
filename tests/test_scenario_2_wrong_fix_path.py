import unittest

from user_registry import UserRegistry


class WrongFixPathTests(unittest.TestCase):
    def test_registry_uses_canonical_storage_key(self) -> None:
        store = UserRegistry()

        store.add_user("TestUser@Example.com", "Indranil")

        self.assertIn("testuser@example.com", store.users)
        self.assertNotIn("TestUser@Example.com", store.users)
        self.assertEqual(store.get_user("testuser@example.com"), "Indranil")


if __name__ == "__main__":
    unittest.main()
