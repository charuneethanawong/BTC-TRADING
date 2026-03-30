"""
Logger Configuration Module
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logger(name: str, log_file: str = None, level: str = "INFO") -> logging.Logger:
    """
    Setup logger with file and console handlers.
    
    Args:
        name: Logger name
        log_file: Path to log file (optional)
        level: Logging level
    
    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Format
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File Handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # v21.0: Daily rotation at UTC midnight, keep 7 days
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=7,
            utc=True
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # v13.8: Disable propagate - logger has its own handlers, don't duplicate to root
    logger.propagate = False
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get or create logger - child loggers will propagate to root."""
    return logging.getLogger(name)


# v13.8: Initialize root logger - all child loggers without handlers will propagate here
def _init_root_logger():
    """Configure root logger so all child loggers have output."""
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        
        # Format
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Console Handler
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
        
        # File Handler - log to project logs folder
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "bot.log"
        
        # v21.0: Daily rotation at UTC midnight, keep 7 days
        file_handler = TimedRotatingFileHandler(
            str(log_file),
            when="midnight",
            interval=1,
            backupCount=7,
            utc=True
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


# Auto-init on import
_init_root_logger()