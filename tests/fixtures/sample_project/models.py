"""Data models for the sample project."""

from dataclasses import dataclass


@dataclass
class User:
    """A user in the system."""

    name: str
    email: str

    def display_name(self) -> str:
        """Get formatted display name."""
        return f"{self.name} <{self.email}>"


@dataclass
class Admin(User):
    """An admin user with extra permissions."""

    role: str = "admin"

    def display_name(self) -> str:
        """Get admin display name with role."""
        return f"[{self.role}] {self.name} <{self.email}>"
