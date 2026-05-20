"""Celery Application Configuration"""

import sys

from celery import Celery
from celery.schedules import schedule
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
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    broker_connection_retry_on_startup=True,
)

# Windows does not support POSIX fork-based multiprocessing (billiard prefork pool).
# Use the solo pool to avoid PermissionError [WinError 5] from shared semaphores,
# and thread-based soft-timeout signals (SIGUSR1) which are also unsupported on Windows.
if sys.platform == 'win32':
    celery_app.conf.update(worker_pool='solo')

# Configure Celery Beat Schedule for periodic tasks
status_polling_config = config.get('status_polling', {})
polling_enabled = status_polling_config.get('enabled', True)
polling_interval_minutes = status_polling_config.get('polling_interval_minutes', 15)

discovery_schedule_config = config.get('discovery_schedule', {})
discovery_enabled = discovery_schedule_config.get('enabled', False)
discovery_interval_hours = discovery_schedule_config.get('interval_hours', 24)

ring_promotion_config = config.get('ring_promotion', {})
ring_promotion_enabled = (
    ring_promotion_config.get('enabled', False)
    and ring_promotion_config.get('auto_promote', False)
)
ring_promotion_interval_hours = ring_promotion_config.get('check_interval_hours', 6)

# Build beat schedule dynamically based on enabled features
celery_app.conf.beat_schedule = {}

# Add status polling if enabled
if polling_enabled:
    celery_app.conf.beat_schedule['poll-deployment-status'] = {
        'task': 'autopackager.poll_deployment_status',
        'schedule': schedule(run_every=polling_interval_minutes * 60.0),  # Convert minutes to seconds
        'options': {'queue': 'default'}
    }

# Add continuous catalog discovery if enabled
if discovery_enabled:
    celery_app.conf.beat_schedule['continuous-catalog-discovery'] = {
        'task': 'autopackager.continuous_catalog_discovery',
        'schedule': schedule(run_every=discovery_interval_hours * 3600.0),  # Convert hours to seconds
        'options': {'queue': 'default'}
    }

# Add automatic ring promotion check if enabled
if ring_promotion_enabled:
    celery_app.conf.beat_schedule['check-ring-promotions'] = {
        'task': 'autopackager.check_ring_promotions',
        'schedule': schedule(run_every=ring_promotion_interval_hours * 3600.0),  # Convert hours to seconds
        'options': {'queue': 'default'}
    }

# Auto-discover tasks
celery_app.autodiscover_tasks(['autopackager.orchestration'])
