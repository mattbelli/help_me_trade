"""Shared broker errors."""


class BrokerError(Exception):
    """Broker API or configuration failure."""


class NotFoundError(BrokerError):
    """The broker states this resource does not exist for this account.

    Distinct from a failed read: absence is evidence a caller may act on (end a
    stuck execution row), a timeout never is. Adapters raise their own subclass.
    """
