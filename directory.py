from utils import normalize_email


class UserDirectory:
    def __init__(self):
        self.users = {}

    def storage_key(self, email: str) -> str:
        # BUG: write path uses the raw identifier instead of the canonical key.
        return email

    def add_user(self, email: str, name: str):
        key = self.storage_key(email)
        self.users[key] = name

    def get_user(self, email: str):
        key = normalize_email(email)
        return self.users.get(key)
