"""Utility functions for the sample project."""

import re


def format_name(name: str) -> str:
    """Format a name to title case."""
    return name.strip().title()


def validate_email(email: str) -> bool:
    """Validate an email address."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text
