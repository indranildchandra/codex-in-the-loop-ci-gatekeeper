def normalize_email(email: str) -> str:
    if not email:
        return ""
    return email.strip().lower()