"""Login handling."""


class LoginError(Exception):
    pass


def normalise_email(email):
    return email.strip().lower()


def login(payload: dict) -> dict:
    raw = payload.get("email")
    if not raw or not str(raw).strip():
        raise LoginError("email required")
    email = normalise_email(raw)
    password = payload.get("password", "")
    if not password:
        raise LoginError("password required")
    return {"user": email}
