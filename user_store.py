from utils import normalize_email


class UserStore:
    def __init__(self):
        self.users = {}

    def add_user(self, email: str, name: str):
        # BUG: inconsistent normalization (write path broken)
        self.users[email] = name

    def get_user(self, email: str):
        key = normalize_email(email)
        return self.users.get(key)
