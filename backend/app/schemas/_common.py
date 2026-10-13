"""Shared schema helpers."""


def normalize_email(cls, v):
    """Strip surrounding whitespace and lowercase an email.

    ``EmailStr`` validates RFC syntax but does not normalize case, so two
    submissions like ``User@Example.com`` and ``user@example.com`` would
    otherwise create distinct rows. Lowercasing both local-part and domain
    keeps writes and lookups consistent against the ``educators`` table's
    ``UNIQUE(email)`` constraint. Runs after ``EmailStr`` has resolved the
    field to a ``str``.
    """
    if isinstance(v, str):
        return v.strip().lower()
    return v
