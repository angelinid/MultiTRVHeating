"""
Test Logging Infrastructure for Multi-TRV Heating Tests

Provides configurable logging for test execution with:
- Three logging levels: WARNING (0), INFO (1), DEBUG (2)
- Timestamped log files (YYYYMMDD_HHMMSS_test_results.txt)
- Test case descriptions and detailed step logging
- Execution time tracking
- Test result summaries
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class TestLogHandler(logging.Handler):
    """
    Custom logging handler that logs to both console and file.
    Provides color-coded console output and detailed file logging.
    """
    
    def __init__(self, log_file: str, level: int = logging.INFO):
        """
        Initialize the test log handler.
        
        Args:
            log_file: Path to log file
            level: Logging level (logging.DEBUG, logging.INFO, logging.WARNING)
        """
        super().__init__(level)
        self.log_file = log_file
        
        # Color codes for console output
        self.COLORS = {
            logging.DEBUG: '\033[36m',      # Cyan
            logging.INFO: '\033[92m',       # Green
            logging.WARNING: '\033[93m',    # Yellow
            logging.ERROR: '\033[91m',      # Red
            logging.CRITICAL: '\033[95m',   # Magenta
        }
        self.RESET = '\033[0m'
    
    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a log record to both console and file.
        
        Args:
            record: LogRecord to emit
        """
        try:
            # Format the message
            msg = self.format(record)
            
            # Console output with colors
            color = self.COLORS.get(record.levelno, self.RESET)
            print(f"{color}[{record.levelname:8}]{self.RESET} {msg}")
            
            # File output
            with open(self.log_file, 'a') as f:
                f.write(f"[{record.levelname:8}] {msg}\n")
        except Exception:
            self.handleError(record)


class TestLogger:
    """
    Main test logging interface for test cases.
    Manages log file creation, test descriptions, and logging.
    """
    
    # Mapping of level names to logging levels
    LEVEL_MAP = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
    }
    
    def __init__(self, log_level: str = 'info', test_name: str = 'test_run'):
        """
        Initialize test logger.
        
        Args:
            log_level: Logging level ('warning', 'info', 'debug')
            test_name: Name of the test for log file prefix
        """
        # Create logs directory if it doesn't exist
        self.log_dir = Path(__file__).parent / 'logs'
        self.log_dir.mkdir(exist_ok=True)
        
        # Create timestamped log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{timestamp}_{test_name}_results.txt"
        
        # Convert string level to logging level
        level = self.LEVEL_MAP.get(log_level.lower(), logging.INFO)
        
        # Create logger
        self.logger = logging.getLogger('test_suite')
        self.logger.setLevel(level)
        
        # Clear existing handlers to avoid duplicates
        self.logger.handlers.clear()
        
        # Add our custom handler
        handler = TestLogHandler(str(self.log_file), level)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(funcName)s() - %(message)s',
            datefmt='%H:%M:%S'
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        
        # Write header to file
        self._write_header()
    
    def _write_header(self) -> None:
        """Write header information to log file."""
        with open(self.log_file, 'w') as f:
            f.write("=" * 80 + "\n")
            f.write(f"Multi-TRV Heating Controller - Test Results\n")
            f.write(f"Test Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")
    
    def test_case(self, name: str, description: str = "") -> None:
        """
        Log the start of a test case with description.
        
        Args:
            name: Name of the test case
            description: Detailed description of what the test does
        """
        self.logger.info("")
        self.logger.info("┌" + "─" * 78 + "┐")
        self.logger.info(f"│ TEST CASE: {name:<66} │")
        if description:
            # Word-wrap description to fit in 78 characters
            desc_lines = self._wrap_text(description, 74)
            for line in desc_lines:
                self.logger.info(f"│ {line:<76} │")
        self.logger.info("└" + "─" * 78 + "┘")
    
    def step(self, step_num: int, description: str) -> None:
        """
        Log a test step.
        
        Args:
            step_num: Sequential step number
            description: What this step does
        """
        self.logger.info(f"\n  Step {step_num}: {description}")
    
    def verify(self, condition: bool, message: str) -> None:
        """
        Log a verification/assertion.
        
        Args:
            condition: Boolean result of verification
            message: Description of what was verified
        """
        result = "✓ PASS" if condition else "✗ FAIL"
        self.logger.info(f"    {result}: {message}")
        if not condition:
            raise AssertionError(f"Verification failed: {message}")
    
    def debug(self, message: str) -> None:
        """Log a debug message."""
        self.logger.debug(f"  → {message}")
    
    def info(self, message: str) -> None:
        """Log an info message."""
        self.logger.info(f"  • {message}")
    
    def warning(self, message: str) -> None:
        """Log a warning message."""
        self.logger.warning(f"  ⚠ {message}")
    
    def summary(self, passed: int, failed: int, total: int) -> None:
        """
        Log test summary at end of test suite.
        
        Args:
            passed: Number of passed tests
            failed: Number of failed tests
            total: Total number of tests
        """
        pass_percent = (passed / total * 100) if total > 0 else 0
        
        self.logger.info("\n")
        self.logger.info("=" * 80)
        self.logger.info(f"TEST SUMMARY")
        self.logger.info("=" * 80)
        self.logger.info(f"Total Tests:    {total}")
        self.logger.info(f"Passed:         {passed} ({pass_percent:.1f}%)")
        self.logger.info(f"Failed:         {failed}")
        self.logger.info(f"Test Log File:  {self.log_file}")
        self.logger.info("=" * 80)
    
    @staticmethod
    def _wrap_text(text: str, width: int) -> list[str]:
        """
        Wrap text to fit within specified width.
        
        Args:
            text: Text to wrap
            width: Maximum line width
            
        Returns:
            List of wrapped lines
        """
        lines = []
        current_line = ""
        
        for word in text.split():
            if len(current_line) + len(word) + 1 <= width:
                current_line += word + " "
            else:
                if current_line:
                    lines.append(current_line.rstrip())
                current_line = word + " "
        
        if current_line:
            lines.append(current_line.rstrip())
        
        return lines
    
    def get_log_file(self) -> str:
        """Get the path to the log file."""
        return str(self.log_file)


def create_test_logger(log_level: str = 'info', test_name: str = 'test_run') -> TestLogger:
    """
    Factory function to create a test logger.
    
    Args:
        log_level: Logging level ('warning', 'info', 'debug')
        test_name: Name of the test for log file
        
    Returns:
        TestLogger instance
    """
    return TestLogger(log_level, test_name)
