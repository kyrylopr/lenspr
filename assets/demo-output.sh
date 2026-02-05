#!/bin/bash
case "$1" in
  init)
    sleep 0.3
    echo "✓ 1,815 functions parsed in 2.1s"
    ;;
  context)
    sleep 0.3
    echo ""
    echo "  validate_email(email)  ·  utils/validators.py"
    echo ""
    echo "  Callers:   signup, login, create_user, reset_password"
    echo "  Callees:   re.match"
    echo "  Tests:     test_validate_email, test_signup_validation"
    ;;
  impact)
    sleep 0.3
    echo ""
    echo "  Severity:    HIGH"
    echo "  Dependents:  12 functions across 4 files"
    echo "  Has tests:   yes"
    echo ""
    echo "  ⚠️  signup, login, create_user, reset_password depend on this"
    ;;
esac
