#!/usr/bin/env python
"""Verify Celery Beat scheduler configuration for continuous catalog discovery"""

import sys
import os

# Add autopackager to path
sys.path.insert(0, os.path.dirname(__file__))

def verify_beat_schedule():
    """Verify that continuous-catalog-discovery is in the Celery Beat schedule"""
    try:
        from autopackager.orchestration.celery_app import celery_app
        from autopackager.utils.config import get_config

        print("=== Celery Beat Schedule Verification ===\n")

        # Get config
        config = get_config()
        discovery_config = config.get('discovery_schedule', {})
        discovery_enabled = discovery_config.get('enabled', False)
        discovery_interval_hours = discovery_config.get('interval_hours', 24)

        print(f"Discovery Enabled in Config: {discovery_enabled}")
        print(f"Discovery Interval Hours: {discovery_interval_hours}")
        print()

        # Get beat schedule
        beat_schedule = celery_app.conf.beat_schedule

        print(f"Total Beat Schedule Entries: {len(beat_schedule)}")
        print()

        # List all scheduled tasks
        if beat_schedule:
            print("Scheduled Tasks:")
            for task_name, task_config in beat_schedule.items():
                task_id = task_config.get('task', 'N/A')
                schedule_info = task_config.get('schedule', 'N/A')
                queue = task_config.get('options', {}).get('queue', 'default')

                # Get interval in seconds from schedule object
                interval_seconds = None
                if hasattr(schedule_info, 'run_every'):
                    interval_obj = schedule_info.run_every
                    # Handle timedelta objects
                    if hasattr(interval_obj, 'total_seconds'):
                        interval_seconds = interval_obj.total_seconds()
                    else:
                        interval_seconds = float(interval_obj)

                print(f"  - {task_name}")
                print(f"    Task: {task_id}")
                print(f"    Queue: {queue}")
                if interval_seconds:
                    print(f"    Interval: {interval_seconds} seconds ({interval_seconds/3600:.1f} hours)")
                print()
        else:
            print("No scheduled tasks found!")
            print()

        # Check for continuous-catalog-discovery specifically
        if 'continuous-catalog-discovery' in beat_schedule:
            print("✅ SUCCESS: 'continuous-catalog-discovery' task is scheduled!")
            task_config = beat_schedule['continuous-catalog-discovery']
            print(f"   Task: {task_config['task']}")

            schedule_info = task_config.get('schedule')
            if hasattr(schedule_info, 'run_every'):
                interval_obj = schedule_info.run_every
                # Handle timedelta objects
                if hasattr(interval_obj, 'total_seconds'):
                    interval_seconds = interval_obj.total_seconds()
                else:
                    interval_seconds = float(interval_obj)
                print(f"   Interval: {interval_seconds} seconds ({interval_seconds/3600:.1f} hours)")

            return True
        else:
            print("❌ FAILURE: 'continuous-catalog-discovery' task is NOT in the schedule!")

            if not discovery_enabled:
                print("   Reason: discovery_schedule.enabled is False in config.yaml")

            return False

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = verify_beat_schedule()
    sys.exit(0 if success else 1)
