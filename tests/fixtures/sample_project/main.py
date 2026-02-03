"""Sample project entry point for testing."""

from utils.helpers import format_name, validate_email
from models import User


MAX_USERS = 100
APP_NAME = "SampleApp"


def main():
    """Main entry point."""
    user = create_user("Alice", "alice@example.com")
    print(f"Created: {user}")


def create_user(name: str, email: str) -> User:
    """Create a new user with validation."""
    clean_name = format_name(name)
    if not validate_email(email):
        raise ValueError(f"Invalid email: {email}")
    return User(name=clean_name, email=email)


if __name__ == "__main__":
    main()
