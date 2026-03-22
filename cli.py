#!/usr/bin/env python3
"""
AutoPackager CLI - Command Line Interface for AutoPackager
"""

import click
from rich.console import Console
from rich.table import Table
from pathlib import Path

from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.models.job import JobType, JobState
from autopackager.utils.config import get_config
from autopackager.utils.database import init_db
from autopackager.utils.logger import setup_logging, get_logger
from autopackager.orchestration.tasks import create_packaging_job

console = Console()


@click.group()
@click.option('--config', type=click.Path(), help='Path to configuration file')
@click.option('--debug', is_flag=True, help='Enable debug logging')
def cli(config, debug):
    """AutoPackager - Autonomous Software Packaging Factory"""
    # Setup logging
    log_level = "DEBUG" if debug else "INFO"
    setup_logging(log_level=log_level, log_file="data/logs/autopackager.log")


@cli.command()
def init():
    """Initialize database and create tables"""
    console.print("[bold blue]Initializing AutoPackager...[/bold blue]")

    try:
        init_db(create_tables=True)
        console.print("[bold green]✓[/bold green] Database initialized successfully")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to initialize database: {str(e)}")
        raise click.Abort()


@cli.command()
@click.option('--vendor', required=True, type=click.Choice(['hp', 'lenovo', 'dell'], case_sensitive=False), help='OEM vendor')
@click.option('--model', required=True, help='Hardware model (e.g., "ThinkPad X1 Carbon Gen 9" or "EliteBook 850 G8")')
@click.option('--driver-type', help='Driver type (e.g., chipset, network, graphics)')
@click.option('--current-version', help='Current driver version')
def create_driver_job(vendor, model, driver_type, current_version):
    """Create a new driver update job"""
    console.print(f"[bold blue]Creating driver update job...[/bold blue]")
    console.print(f"  Vendor: {vendor}")
    console.print(f"  Model: {model}")
    console.print(f"  Driver Type: {driver_type or 'All'}")

    try:
        # Create job via Celery task
        result = create_packaging_job.delay(
            job_type='driver_update',
            software_title=f"{vendor.upper()} {model} Driver Pack",
            vendor=vendor,
            current_version=current_version,
            hardware_model=model,
            driver_type=driver_type
        )

        console.print(f"[bold green]✓[/bold green] Job created successfully")
        console.print(f"  Task ID: {result.id}")
        console.print(f"\nUse 'autopackager jobs list' to check status")

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to create job: {str(e)}")
        raise click.Abort()


@cli.group()
def jobs():
    """Manage packaging jobs"""
    pass


@jobs.command('list')
@click.option('--state', type=click.Choice([s.value for s in JobState]), help='Filter by state')
@click.option('--limit', type=int, default=20, help='Number of jobs to display')
def list_jobs(state, limit):
    """List packaging jobs"""
    engine = OrchestrationEngine()

    if state:
        job_list = engine.get_jobs_by_state(JobState(state), limit=limit)
    else:
        job_list = engine.get_all_jobs(limit=limit)

    if not job_list:
        console.print("[yellow]No jobs found[/yellow]")
        return

    # Create table
    table = Table(title=f"Packaging Jobs (showing {len(job_list)})")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Vendor", style="blue")
    table.add_column("Version", style="green")
    table.add_column("State", style="yellow")
    table.add_column("Created", style="magenta")

    for job in job_list:
        state_color = {
            JobState.COMPLETED: "green",
            JobState.FAILED: "red",
            JobState.PENDING: "yellow",
            JobState.DISCOVERING: "blue",
            JobState.PACKAGING: "blue",
            JobState.TESTING: "blue",
            JobState.DEPLOYING: "blue"
        }.get(job.state, "white")

        table.add_row(
            str(job.id),
            job.software_title,
            job.vendor or "",
            f"{job.current_version or 'N/A'} → {job.target_version or '?'}",
            f"[{state_color}]{job.state.value}[/{state_color}]",
            job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else ""
        )

    console.print(table)


@jobs.command('status')
@click.argument('job_id', type=int)
def job_status(job_id):
    """Get detailed status of a job"""
    engine = OrchestrationEngine()
    job = engine.get_job(job_id)

    if not job:
        console.print(f"[bold red]✗[/bold red] Job {job_id} not found")
        return

    console.print(f"\n[bold]Job #{job.id}: {job.software_title}[/bold]")
    console.print(f"  Type: {job.job_type.value}")
    console.print(f"  State: [{job.state.value}]")
    console.print(f"  Vendor: {job.vendor or 'N/A'}")
    console.print(f"  Current Version: {job.current_version or 'N/A'}")
    console.print(f"  Target Version: {job.target_version or 'N/A'}")

    if job.hardware_model:
        console.print(f"  Hardware Model: {job.hardware_model}")

    console.print(f"  Created: {job.created_at}")
    console.print(f"  Updated: {job.updated_at}")

    if job.error_message:
        console.print(f"  [bold red]Error:[/bold red] {job.error_message}")

    if job.metadata:
        console.print(f"\n  Metadata:")
        for key, value in job.metadata.items():
            console.print(f"    {key}: {value}")


@jobs.command('cancel')
@click.argument('job_id', type=int)
@click.option('--all-stuck', is_flag=True, help='Cancel all non-terminal jobs')
def cancel_job(job_id, all_stuck):
    """Cancel a job (mark as cancelled in the database)"""
    engine = OrchestrationEngine()

    if all_stuck:
        stuck_states = [JobState.PENDING, JobState.DISCOVERING, JobState.PACKAGING,
                        JobState.TESTING, JobState.DEPLOYING]
        cancelled = 0
        for state in stuck_states:
            for job in engine.get_jobs_by_state(state):
                engine.update_job_state(job.id, JobState.CANCELLED)
                console.print(f"  Cancelled job #{job.id}: {job.software_title}")
                cancelled += 1
        console.print(f"[bold green]✓[/bold green] Cancelled {cancelled} job(s)")
    else:
        job = engine.get_job(job_id)
        if not job:
            console.print(f"[bold red]✗[/bold red] Job {job_id} not found")
            return
        engine.update_job_state(job_id, JobState.CANCELLED)
        console.print(f"[bold green]✓[/bold green] Job #{job_id} cancelled")





@cli.group()
def worker():
    """Manage Celery workers"""
    pass


@worker.command('purge')
@click.option('--yes', is_flag=True, help='Skip confirmation prompt')
def purge_queue(yes):
    """Purge all pending tasks from the Celery queue"""
    if not yes:
        click.confirm('This will discard all queued tasks. Continue?', abort=True)

    from autopackager.orchestration.celery_app import celery_app
    count = celery_app.control.purge()
    console.print(f"[bold green]✓[/bold green] Purged {count} task(s) from the queue")


@worker.command('start')
@click.option('--concurrency', type=int, default=4, help='Number of concurrent workers')
def start_worker(concurrency):
    """Start Celery worker"""
    console.print(f"[bold blue]Starting Celery worker with {concurrency} concurrent tasks...[/bold blue]")
    console.print(f"Use Ctrl+C to stop")

    import subprocess
    import sys

    cmd = [
        'celery',
        '-A', 'autopackager.orchestration.celery_app',
        'worker',
        '--loglevel=info',
        f'--concurrency={concurrency}'
    ]

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker stopped[/yellow]")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to start worker: {str(e)}")


@cli.command()
def version():
    """Show version information"""
    from autopackager import __version__
    console.print(f"[bold]AutoPackager[/bold] version {__version__}")


if __name__ == '__main__':
    cli()
