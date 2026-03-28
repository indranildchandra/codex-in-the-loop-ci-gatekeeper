import unittest

from directory import UserDirectory


class WrongFixPathTests(unittest.TestCase):
    def test_directory_uses_canonical_storage_key(self) -> None:
        store = UserDirectory()

        store.add_user("TestUser@Example.com", "Indranil")

        self.assertIn("testuser@example.com", store.users)
        self.assertNotIn("TestUser@Example.com", store.users)
        self.assertEqual(store.get_user("testuser@example.com"), "Indranil")


if __name__ == "__main__":
    unittest.main()
