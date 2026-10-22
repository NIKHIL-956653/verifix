"""Document storage."""

from dataclasses import dataclass


class PermissionDenied(Exception):
    pass


class NotFound(Exception):
    pass


@dataclass
class User:
    name: str
    is_admin: bool = False


@dataclass
class Document:
    id: int
    owner: str
    title: str


class Store:
    def __init__(self) -> None:
        self.docs: dict[int, Document] = {}

    def add(self, doc: Document) -> None:
        self.docs[doc.id] = doc


def delete_document(user: User, doc_id: int, store: Store) -> None:
    if doc_id not in store.docs:
        raise NotFound(doc_id)
    del store.docs[doc_id]
