#!/bin/bash
# Unified Test Runner for Multi-TRV Heating Controller
# 
# Usage: ./run_tests.sh [log_level] [suite]
#
# log_level: debug|info|warning (default: info)
# suite: core|features|integration|all (default: all)
#
# Examples:
#   ./run_tests.sh                      # Run all tests with info logging
#   ./run_tests.sh debug                # Run all tests with debug logging
#   ./run_tests.sh info core            # Run only core tests
#   ./run_tests.sh debug features       # Run feature tests with debug logging

set -e

LOG_LEVEL=${1:-info}
SUITE=${2:-all}

# Validate log level
if [ "$LOG_LEVEL" != "debug" ] && [ "$LOG_LEVEL" != "info" ] && [ "$LOG_LEVEL" != "warning" ]; then
    echo "Usage: $0 [log_level] [suite]"
    echo ""
    echo "log_level options:"
    echo "  debug   - Detailed logging (recommended for debugging)"
    echo "  info    - Normal logging (default)"
    echo "  warning - Only warnings and errors"
    echo ""
    echo "suite options:"
    echo "  core        - Core heating logic tests"
    echo "  features    - Pump discharge and pre-heating tests"
    echo "  integration - Sensor entity tests"
    echo "  all         - All test suites (default)"
    echo ""
    exit 1
fi

# Validate suite type
if [ "$SUITE" != "core" ] && [ "$SUITE" != "features" ] && [ "$SUITE" != "integration" ] && [ "$SUITE" != "all" ]; then
    echo "Error: Invalid suite '$SUITE'"
    echo "Valid options: core, features, integration, all"
    exit 1
fi

# Run the unified test runner
cd "$(dirname "$0")"
python -u test_runner.py "$LOG_LEVEL" "$SUITE"
