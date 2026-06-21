import redis
from rq import Queue

from app.config import settings

redis_conn = redis.from_url(settings.redis_url)
task_queue = Queue("default", connection=redis_conn)
