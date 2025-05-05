import httpx
import asyncio
import time
import logging
import os
from functools import wraps
from typing import Callable, Any, TypeVar, Coroutine, Dict, List, Union, Optional
from lxml import etree # Import lxml here

logger = logging.getLogger(__name__)
RT = TypeVar('RT') # Return Type

# --- Rate Limiter Class ---
class AsyncRateLimiter:
    """A simple token bucket rate limiter for asyncio."""
    def __init__(self, rate: float, capacity: float):
        if rate <= 0 or capacity <= 0:
             raise ValueError("Rate and capacity must be positive")
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last_time = time.monotonic()
        self._lock = asyncio.Lock()

    async def _add_tokens(self):
        now = time.monotonic()
        elapsed = now - self._last_time
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_time = now

    async def acquire(self):
        async with self._lock:
            await self._add_tokens()
            wait_time = 0
            if self._tokens < 1:
                wait_time = (1 - self._tokens) / self.rate
                self._tokens = 0 # Prevent tokens going negative if wait is interrupted
            else:
                self._tokens -= 1

        if wait_time > 0:
            await asyncio.sleep(wait_time)


    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

# --- Instantiate Limiters (Reading from .env) ---
CONGRESS_RATE_DEFAULT = 1.3
MAX_CONCURRENCY_DEFAULT = 5
try:
    congress_rate = float(os.getenv("CONGRESS_RATE", CONGRESS_RATE_DEFAULT))
except ValueError:
    logger.warning(f"Invalid CONGRESS_RATE in .env, using default {CONGRESS_RATE_DEFAULT}")
    congress_rate = CONGRESS_RATE_DEFAULT
try:
    max_concurrency = int(os.getenv("MAX_CONCURRENCY", MAX_CONCURRENCY_DEFAULT))
except ValueError:
     logger.warning(f"Invalid MAX_CONCURRENCY in .env, using default {MAX_CONCURRENCY_DEFAULT}")
     max_concurrency = MAX_CONCURRENCY_DEFAULT

congress_api_limiter = AsyncRateLimiter(rate=congress_rate, capacity=max(10.0, congress_rate * 5))
xml_feed_limiter = AsyncRateLimiter(rate=float(max_concurrency), capacity=float(max_concurrency))
logger.info(f"Rate Limiters Initialized: Congress API rate={congress_rate}/s, XML Feeds concurrency={max_concurrency}")

# --- Error Handling Decorator ---
def handle_api_errors(limiter: AsyncRateLimiter, retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Decorator for robust async HTTP requests with retries, backoff, and specific rate limiter."""
    def decorator(func: Callable[..., Coroutine[Any, Any, RT]]) -> Callable[..., Coroutine[Any, Any, RT]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> RT:
            current_delay = delay
            last_exception = None
            func_name = func.__name__
            for attempt in range(retries):
                try:
                    # Apply the specific rate limiter for this function type
                    async with limiter:
                         logger.debug(f"Executing {func_name} (Attempt {attempt+1}/{retries}) with args: {args}, kwargs: {kwargs}")
                         result = await func(*args, **kwargs)
                         logger.debug(f"{func_name} (Attempt {attempt+1}) succeeded.")
                         return result # Success
                except httpx.HTTPStatusError as e:
                    last_exception = e
                    status = e.response.status_code
                    url = e.request.url
                    if status >= 500 or status == 429: # Retry on server errors or rate limits
                        if attempt == retries - 1:
                            logger.error(f"HTTP {status} on {url} | func={func_name} | attempt={attempt+1}/{retries} | Giving up.", exc_info=False) # Log less on final failure
                            break # Exit loop to raise last_exception
                        logger.warning(f"HTTP {status} on {url} | func={func_name} | attempt={attempt+1}/{retries} | Retrying in {current_delay:.2f}s...")
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else: # Don't retry on 4xx client errors
                        logger.error(f"HTTP {status} (Client Error) on {url} | func={func_name} | Not retrying.", exc_info=False)
                        break # Exit loop to raise last_exception
                except httpx.RequestError as e: # Network errors
                    last_exception = e
                    url = e.request.url if hasattr(e, 'request') and e.request else "Unknown URL"
                    if attempt == retries - 1:
                         logger.error(f"Network Error calling {url} | func={func_name} | attempt={attempt+1}/{retries} | Giving up.", exc_info=False)
                         break
                    logger.warning(f"Network Error calling {url} | func={func_name} | attempt={attempt+1}/{retries} | Retrying in {current_delay:.2f}s...")
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
                except Exception as e: # Catch other unexpected errors during function execution
                     last_exception = e
                     logger.exception(f"Unexpected error in {func_name} attempt {attempt+1}/{retries}", exc_info=True) # Log full traceback here
                     if attempt == retries - 1: break
                     await asyncio.sleep(current_delay) # Still wait before retry
                     current_delay *= backoff

            # If loop finished naturally (no success) or break due to non-retryable/final error
            logger.error(f"Function {func_name} failed permanently after {retries} attempts.")
            raise last_exception if last_exception is not None else RuntimeError(f"Unknown failure in {func_name} after {retries} attempts")

        return wrapper
    return decorator

# --- XML Parsing Helper ---
@handle_api_errors(limiter=xml_feed_limiter) # Use XML specific limiter
async def fetch_and_parse_xml(url: str) -> etree._Element:
    """Fetches and parses XML asynchronously with XML rate limiting & error handling."""
    logger.info(f"Fetching XML from: {url}")
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        response = await client.get(url)
        # raise_for_status() is called implicitly by the decorator's error handling
        # We only proceed if the request didn't raise an exception handled by the decorator
        try:
            # Use response.content (bytes) for robust parsing
            xml_root = etree.fromstring(response.content)
            logger.debug(f"Successfully parsed XML from {url}")
            return xml_root
        except etree.XMLSyntaxError as e:
            logger.error(f"XML Parsing Error for {url}: {e}", exc_info=True)
            raise ValueError(f"Failed to parse XML from {url}") from e # Raise specific error


# --- Payload Unwrapper Utility ---
def _unwrap_payload(data: Dict[str, Any], expected_key: str) -> Optional[Union[Dict[str, Any], List[Dict[str, Any]]]]:
    """
    Attempts to extract the main payload from common API nesting patterns like data['bills'], data['bill'].
    Returns the core object/list or None if structure is unexpected.
    """
    if not isinstance(data, dict):
        logger.debug(f"Cannot unwrap non-dict data: {type(data)}")
        return None

    # 1. Try direct key (e.g., 'bill' -> data['bill'])
    if expected_key in data:
        payload = data[expected_key]
        if isinstance(payload, (dict, list)):
            logger.debug(f"Unwrapped using direct key '{expected_key}'")
            return payload
        else:
            logger.warning(f"Payload under key '{expected_key}' is not dict or list: {type(payload)}")
            # Decide if we should still return it or None

    # 2. Try simple plural key (e.g., key='bill' -> data['bills'])
    plural_key = f"{expected_key}s"
    if plural_key in data:
         payload = data[plural_key]
         if isinstance(payload, list):
             logger.debug(f"Unwrapped using plural key '{plural_key}'")
             return payload
         else:
              logger.warning(f"Payload under plural key '{plural_key}' is not list: {type(payload)}")

    # 3. Try 'results' key (common pattern)
    if 'results' in data and isinstance(data['results'], list):
         logger.debug("Unwrapped using 'results' key")
         return data['results']

    # 4. Fallback: If data itself looks like the payload (basic check)
    # This is risky, only use if confident top-level is the object/list
    # Example: Check for common keys in a BillInfo model
    # if all(k in data for k in ['congress', 'number', 'title']):
    #     logger.debug("Assuming top-level data is the payload")
    #     return data # Return the whole dict

    # 5. If nothing matches:
    logger.warning(f"Could not reliably unwrap payload for key '{expected_key}' in data with keys: {list(data.keys())}")
    return None