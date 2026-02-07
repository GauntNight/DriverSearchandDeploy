"""Celery Application Configuration"""

from celery import Celery
from autopackager.utils.config import get_config

# Load configuration
config = get_config()
redis_config = config['redis']

# Create Celery app
celery_app = Celery(
    'autopackager',
    broker=f"redis://{redis_config['host']}:{redis_config['port']}/{redis_config['db']}",
    backend=f"redis://{redis_config['host']}:{redis_config['port']}/{redis_config['db']}"
)

# Configure Celery
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour
    task_soft_time_limit=3300,  # 55 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50
)

# Auto-discover tasks
celery_app.autodiscover_tasks(['autopackager.orchestration'])
