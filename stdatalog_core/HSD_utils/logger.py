# *****************************************************************************
#  * @file    logger.py
#  * @author  SRA
# ******************************************************************************
# * @attention
# *
# * Copyright (c) 2022 STMicroelectronics.
# * All rights reserved.
# *
# * This software is licensed under terms that can be found in the LICENSE file
# * in the root directory of this software component.
# * If no LICENSE file comes with this software, it is provided AS-IS.
# *
# *
# ******************************************************************************
"""
Logger utilities for the HSDatalog application.
This module centralizes logging configuration and helpers used across the
HSDatalog codebase. It provides:
- A global application logger name (LOGGER_NAME) to keep logs organized.
- A CustomFormatter that adds ANSI color codes to console output based on
    log level for improved readability.
- setup_applevel_logger() to configure the root application logger with a
    colored stream handler and, optionally, a file handler.
- get_logger() to obtain per-module child loggers under the application
    logger hierarchy.
- get_datetime() to generate a consistently formatted timestamp string.
Features:
- Colored console logs for DEBUG/INFO (grey), WARNING (yellow),
    ERROR (red), and CRITICAL (bold red) levels.
- Simple setup with a single call that clears any existing handlers to
    avoid duplicate output.
- Optional file logging when debug mode is enabled and a file name is
    provided.
- Consistent, human-readable timestamp formatting.
Example:
        >>> from HSD_utils import logger
        >>> log = logger.setup_applevel_logger(is_debug=True)
        >>> mod_log = logger.get_logger(__name__)
        >>> mod_log.info("Starting module...")
        >>> mod_log.error("Something went wrong")
Notes:
- ANSI color sequences may not render correctly on all terminals or
    platforms. If color output is undesired, consider using a standard
    logging.Formatter instead of CustomFormatter.
- Handlers are cleared during setup_applevel_logger() to prevent duplicate
    logs when setup is called multiple times.
"""

import logging
import sys
from datetime import datetime

LOGGER_NAME = "HSDatalogApp"

class CustomFormatter(logging.Formatter):
    """
    Custom formatter to add colors based on log level.
    
    Attributes:
        - grey: Color code for DEBUG and INFO levels.
        - yellow: Color code for WARNING level.
        - red: Color code for ERROR level.
        - bold_red: Color code for CRITICAL level.
        - reset: Color code to reset formatting.
        - BASE_FORMAT: Log message format string.
    Methods:
        - format(record): Formats the log record with appropriate color.
    """
    grey = "\x1b[38;21m"
    yellow = "\x1b[33;21m"
    red = "\x1b[31;21m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    # Renamed to avoid clashing with the method name 'format'
    BASE_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + BASE_FORMAT + reset,
        logging.INFO: grey + BASE_FORMAT + reset,
        logging.WARNING: yellow + BASE_FORMAT + reset,
        logging.ERROR: red + BASE_FORMAT + reset,
        logging.CRITICAL: bold_red + BASE_FORMAT + reset,
    }

    def format(self, record):
        # Pick the colored format for the current level; fallback to a sane default
        log_fmt = self.FORMATS.get(record.levelno, self.grey + self.BASE_FORMAT + self.reset)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

def setup_applevel_logger( is_debug=True,
                        file_name=None,
                        stream_handler=sys.stdout):
    """
    Setup the application level logger.
    :param is_debug: Enable debug level logging if True.
    :param file_name: Optional file name to log to a file.
    :param stream_handler: Stream handler for console output.
    :return: Configured logger instance.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if is_debug else logging.INFO)

    formatter = CustomFormatter()

    sh = logging.StreamHandler(stream_handler)
    sh.setFormatter(formatter)
    logger.handlers.clear()
    logger.addHandler(sh)

    if is_debug and file_name:
        fh = logging.FileHandler(file_name)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

def get_logger(module_name):
    """
    Get a logger instance for the specified module.
    :param module_name: Name of the module.
    :return: Logger instance.
    """
    return logging.getLogger(LOGGER_NAME).getChild(module_name)

def get_datetime():
    """
    Get the current date and time as a formatted string.
    Format: "YYYY-MM-DD HH:MM:SS,mmm"
    :return: Formatted date-time string.
    """
    now = datetime.now()
    datetime_string = now.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    return datetime_string
