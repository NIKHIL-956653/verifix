"""Login handling."""


class LoginError(Exception):
    pass


def normalise_email(email):
    return email.strip().lower()


def login(payload: dict) -> dict:
    email = normalise_email(payload["email"])
    password = payload.get("password", "")
    if not password:
        raise LoginError("password required")
    return {"user": email}
