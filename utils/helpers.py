# utils/helpers.py — Shared utilities: retry decorator, etc.

import functools
import time
import logging

logger = logging.getLogger(__name__)


def retry_on_failure(max_retries: int = 3, delay: float = 2, backoff: float = 2):
    """
    Decorator that retries a function on exception with exponential backoff.

    Usage:
        @retry_on_failure(max_retries=3, delay=1)
        def call_external_api():
            ...

    Will retry 3 times with delays: 1s → 2s → 4s
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            current_delay = delay
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    if retries == max_retries:
                        logger.error(
                            f"{func.__name__} failed after {max_retries} retries: {e}"
                        )
                        raise
                    logger.warning(
                        f"{func.__name__} retry {retries}/{max_retries} "
                        f"after {current_delay}s: {e}"
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator


def retry_on_failure_async(max_retries: int = 3, delay: float = 2, backoff: float = 2):
    """
    Async version of retry_on_failure decorator.

    Usage:
        @retry_on_failure_async(max_retries=3, delay=1)
        async def call_external_api():
            ...
    """
    import asyncio

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            retries = 0
            current_delay = delay
            while retries < max_retries:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    if retries == max_retries:
                        logger.error(
                            f"{func.__name__} failed after {max_retries} retries: {e}"
                        )
                        raise
                    logger.warning(
                        f"{func.__name__} retry {retries}/{max_retries} "
                        f"after {current_delay}s: {e}"
                    )
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator


def sanitize_ticker(ticker: str) -> str:
    """
    Normalize a ticker symbol: uppercase, strip $/#/whitespace.

    Examples:
        "$PEPE"  → "PEPE"
        "#bonk"  → "BONK"
        "  WIF " → "WIF"
    """
    return ticker.upper().strip().strip("$#").strip()
