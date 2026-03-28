import unittest

from app import UserStore


class WrongFixPathTests(unittest.TestCase):
    def test_user_store_normalizes_keys_on_write(self) -> None:
        store = UserStore()

        store.add_user("TestUser@Example.com", "Indranil")

        self.assertIn("testuser@example.com", store.users)
        self.assertNotIn("TestUser@Example.com", store.users)


if __name__ == "__main__":
    unittest.main()
