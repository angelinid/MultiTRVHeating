#!/bin/bash
# Test runner script for Multi-TRV Heating Controller
# Usage: ./run_tests.sh [debug|info|warning]

set -e

LOG_LEVEL=${1:-info}

if [ "$LOG_LEVEL" != "debug" ] && [ "$LOG_LEVEL" != "info" ] && [ "$LOG_LEVEL" != "warning" ]; then
    echo "Usage: $0 [debug|info|warning]"
    echo ""
    echo "  debug   - Detailed logging (recommended for debugging)"
    echo "  info    - Normal logging (default)"
    echo "  warning - Only warnings and errors"
    exit 1
fi

echo "=========================================="
echo "Multi-TRV Heating Controller - Test Suite"
echo "=========================================="
echo "Log Level: $LOG_LEVEL"
echo ""

# Run the test suite
cd "$(dirname "$0")"
python -u test_suite.py "$LOG_LEVEL"

echo ""
echo "=========================================="
echo "Tests Complete - Check logs/ directory"
echo "=========================================="
