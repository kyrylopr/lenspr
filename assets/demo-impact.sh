#!/bin/bash
lenspr impact . "$1" 2>/dev/null | jq -r '"
  Severity:  \(.data.severity)
  Affected:  \(.data.total_affected) nodes
  Callers:   \([.data.direct_callers[] | select(startswith("tests") | not)] | join(", "))
  Has tests: \(.data.has_tests)
  \(.warnings[0] // "")"'
