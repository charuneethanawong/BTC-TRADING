"""
Utility decorators for error handling and performance monitoring.
"""

import functools
import time
import logging
from typing import Callable, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, 
          exceptions: tuple = (Exception,)):
    """
    Retry decorator with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry on
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            _delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:  # Last attempt
                        logger.error(f"Function {func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {e}. Retrying in {_delay}s...")
                    time.sleep(_delay)
                    _delay *= backoff
            return None  # Should never reach here
        return wrapper
    return decorator


def circuit_breaker(failure_threshold: int = 5, timeout: float = 60.0,
                   expected_exception: type = Exception):
    """
    Circuit breaker decorator to prevent cascading failures.
    
    Args:
        failure_threshold: Number of failures before opening the circuit
        timeout: Time in seconds to wait before attempting to close the circuit
        expected_exception: Exception type that triggers the circuit breaker
    """
    def decorator(func: Callable) -> Callable:
        # Circuit breaker state
        state = {
            'failure_count': 0,
            'last_failure_time': None,
            'state': 'closed',  # closed, open, half-open
        }
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            now = time.time()
            
            # Check if we should attempt to close the circuit
            if state['state'] == 'open':
                if now - state['last_failure_time'] > timeout:
                    state['state'] = 'half-open'
                    logger.info(f"Circuit breaker for {func.__name__} moving to half-open state")
                else:
                    raise Exception(f"Circuit breaker OPEN for {func.__name__}. "
                                  f"Retry after {timeout - (now - state['last_failure_time']):.1f}s")
            
            try:
                result = func(*args, **kwargs)
                # Success - reset failure count and close circuit
                if state['state'] == 'half-open':
                    logger.info(f"Circuit breaker for {func.__name__} CLOSED after successful call")
                state['failure_count'] = 0
                state['state'] = 'closed'
                return result
                
            except expected_exception as e:
                state['failure_count'] += 1
                state['last_failure_time'] = now
                
                if state['failure_count'] >= failure_threshold:
                    state['state'] = 'open'
                    logger.error(f"Circuit breaker OPENED for {func.__name__} after {failure_threshold} failures")
                
                raise e
        
        return wrapper
    return decorator


def timed(func: Callable) -> Callable:
    """
    Decorator to measure and log execution time of functions.
    
    Args:
        func: Function to decorate
        
    Returns:
        Wrapped function with timing logging
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        start_time = time.perf_counter()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            end_time = time.perf_counter()
            execution_time = end_time - start_time
            logger.debug(f"{func.__name__} executed in {execution_time:.4f}s")
            
            # Log warning if execution time is too long
            if execution_time > 1.0:  # More than 1 second
                logger.warning(f"{func.__name__} took {execution_time:.4f}s (>1.0s threshold)")
            elif execution_time > 0.5:  # More than 500ms
                logger.info(f"{func.__name__} took {execution_time:.4f}s (>0.5s threshold)")
                
    return wrapper


def log_errors(func: Callable) -> Callable:
    """
    Decorator to automatically log exceptions with context.
    
    Args:
        func: Function to decorate
        
    Returns:
        Wrapped function with error logging
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Exception in {func.__name__}: {e}", exc_info=True)
            raise  # Re-raise the exception after logging
    return wrapper


def validate_inputs(*validators):
    """
    Decorator to validate function inputs using provided validator functions.
    
    Args:
        *validators: Validator functions that take the same args as the decorated function
                    and return (is_valid, error_message) tuples
    
    Example:
        @validate_inputs(lambda x: (x > 0, "x must be positive"))
        def process_number(x):
            return x * 2
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            for i, validator in enumerate(validators):
                if i < len(args):
                    is_valid, error_msg = validator(args[i])
                    if not is_valid:
                        raise ValueError(f"Input validation failed for argument {i}: {error_msg}")
            return func(*args, **kwargs)
        return wrapper
    return decorator