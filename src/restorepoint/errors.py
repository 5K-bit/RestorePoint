"""Exceptions. The CLI maps these to exit codes."""

from __future__ import annotations


class RestorePointError(Exception):
    """A problem the user can act on (bad argument, missing repository, unsafe request)."""


class IntegrityError(RestorePointError):
    """Stored data does not match its checksum, or a snapshot is damaged or forged."""


class LockedError(RestorePointError):
    """Another RestorePoint process is writing to the repository."""
