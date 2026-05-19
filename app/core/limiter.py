from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_URL = os.getenv("REDIS_URL", f"redis://{REDIS_HOST}:{REDIS_PORT}/0")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=REDIS_URL,
    default_limits=[],
    strategy="fixed-window",
    headers_enabled=True,
)
