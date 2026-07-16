"""Shared research engine exceptions."""


class ResearchCancelled(RuntimeError):
    """Raised when cooperative cancellation is requested."""
