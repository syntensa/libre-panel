"""The access token (editor/access.py) of every editor server a test starts, by its port.

conftest.py fills it; the clients of the tests send the token, as the app does."""

TOKENS: dict[int, str] = {}


def token_of(port) -> str:
    return TOKENS[int(port)]
