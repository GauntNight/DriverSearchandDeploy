#!/usr/bin/env python3
"""
AutoPackager CLI - Command Line Interface for AutoPackager
"""

import re
import sys

import click
from rich.console import Console
from rich.table import Table
from pathlib import Path

from autopackager.orchestration.engine import OrchestrationEngine
from autopackager.utils.azure_validator import AzureValidator, AzureConfigurationError, ValidationResult
from autopackager.models.job import JobType, JobState
from autopackager.models.deployment import Deployment
from autopackager.utils.config import get_config
from autopackager.utils.database import init_db, db_session_scope
from autopackager.utils.logger import setup_logging, get_logger
from autopackager.orchestration.tasks import create_packaging_job
from autopackager.agents.deployment.deployment_agent import DeploymentAgent

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

    # Display deployment ring information
    package_id = job.job_metadata.get('package_id') if job.job_metadata else None
    if package_id:
        _display_deployment_ring_info(package_id)

    if job.job_metadata:
        console.print(f"\n  Metadata:")
        for key, value in job.job_metadata.items():
            console.print(f"    {key}: {value}")


def _display_deployment_ring_info(package_id: int):
    """Display deployment ring and promotion eligibility information"""
    try:
        with db_session_scope() as session:
            # Get all deployments for this package, ordered by created_at descending
            deployments = session.query(Deployment).filter(
                Deployment.package_id == package_id
            ).order_by(Deployment.created_at.desc()).all()

            if not deployments:
                return

            console.print(f"\n  [bold]Deployment Status:[/bold]")

            deployment_agent = DeploymentAgent()

            for deployment in deployments:
                # Detach from session for use outside context
                session.expunge(deployment)

                # Check promotion eligibility
                is_eligible, reason = deployment_agent.is_eligible_for_promotion(deployment)

                # Display ring information
                ring_name = deployment.ring_name or deployment.ring_id
                ring_color = "green" if is_eligible else "yellow"
                console.print(f"    Ring: [{ring_color}]{ring_name}[/{ring_color}]")
                console.print(f"    Status: {deployment.status.value}")

                # Display promotion eligibility
                if is_eligible:
                    console.print(f"    Promotion: [bold green]✓ Eligible[/bold green] - {reason}")
                else:
                    console.print(f"    Promotion: [yellow]✗ Not Eligible[/yellow] - {reason}")

                    # Calculate time until eligible if it's a time-based restriction
                    if deployment.deployed_at and "hours remaining" in reason:
                        # Extract hours remaining from the reason string
                        match = re.search(r'(\d+\.?\d*)\s+hours remaining', reason)
                        if match:
                            hours_remaining = float(match.group(1))
                            days = int(hours_remaining // 24)
                            hours = int(hours_remaining % 24)
                            if days > 0:
                                console.print(f"    Time until eligible: {days}d {hours}h")
                            else:
                                console.print(f"    Time until eligible: {hours}h")

                # Display install statistics
                if deployment.target_device_count:
                    total_installs = deployment.successful_installs + deployment.failed_installs
                    success_rate = (deployment.successful_installs / total_installs * 100) if total_installs > 0 else 0
                    console.print(f"    Installs: {deployment.successful_installs}/{deployment.target_device_count} successful ({success_rate:.1f}%)")

                console.print("")  # Blank line between deployments

    except Exception as e:
        logger = get_logger(__name__)
        logger.error("Failed to retrieve deployment ring info", error=str(e))
        # Silently fail - don't disrupt the job status output


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


@jobs.command('rollback')
@click.argument('job_id', type=int)
@click.option('--yes', is_flag=True, help='Skip confirmation prompt')
def rollback_job(job_id, yes):
    """Rollback a failed deployment"""
    engine = OrchestrationEngine()
    job = engine.get_job(job_id)

    if not job:
        console.print(f"[bold red]✗[/bold red] Job {job_id} not found")
        return

    console.print(f"\n[bold]Job #{job.id}: {job.software_title}[/bold]")
    console.print(f"  State: {job.state.value}")
    console.print(f"  Version: {job.target_version or 'N/A'}")

    if not yes:
        click.confirm(f"\nRollback deployment for job #{job_id}?", abort=True)

    console.print(f"\n[bold yellow]⚠[/bold yellow] Rollback functionality not yet implemented")
    console.print(f"This will remove the deployed application from Intune")



@jobs.command('promote')
@click.argument('deployment_id', type=int)
@click.option('--force', is_flag=True, help='Force promotion even if eligibility checks fail')
def promote_deployment(deployment_id, force):
    """Manually promote a deployment to the next ring"""
    console.print(f"[bold blue]Promoting deployment #{deployment_id}...[/bold blue]\n")

    try:
        deployment_agent = DeploymentAgent()

        # Get deployment details first
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(
                Deployment.id == deployment_id
            ).first()

            if not deployment:
                console.print(f"[bold red]✗[/bold red] Deployment {deployment_id} not found")
                raise click.Abort()

            # Detach from session for use outside context
            session.expunge(deployment)

        # Check eligibility
        is_eligible, reason = deployment_agent.is_eligible_for_promotion(deployment)

        current_ring_name = deployment.ring_name or deployment.ring_id
        console.print(f"  Current Ring: {current_ring_name}")
        console.print(f"  Status: {deployment.status.value}")

        if not is_eligible and not force:
            console.print(f"\n[bold yellow]✗ Not Eligible for Promotion[/bold yellow]")
            console.print(f"  Reason: {reason}")
            console.print(f"\nUse --force to attempt promotion anyway")
            raise click.Abort()
        elif not is_eligible and force:
            console.print(f"\n[bold yellow]⚠ Warning:[/bold yellow] {reason}")
            console.print(f"  Attempting forced promotion...\n")
        else:
            console.print(f"  Eligibility: [bold green]✓ Eligible[/bold green] - {reason}\n")

        # Attempt promotion
        result = deployment_agent.promote_to_next_ring(deployment_id)

        console.print(f"[bold green]✓[/bold green] Promotion successful!")
        console.print(f"  From: {result['from_ring']}")
        console.print(f"  To: {result['to_ring']}")
        console.print(f"  Package ID: {result['package_id']}")
        console.print(f"  Intune App ID: {result['intune_app_id']}")

    except ValueError as e:
        console.print(f"[bold red]✗[/bold red] Promotion failed: {str(e)}")
        raise click.Abort()
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Unexpected error: {str(e)}")
        raise click.Abort()


@jobs.command('halt-promotion')
@click.argument('deployment_id', type=int)
@click.option('--reason', required=True, help='Reason for blocking automatic promotion')
def halt_promotion(deployment_id, reason):
    """Block automatic promotion for a deployment"""
    console.print(f"[bold blue]Halting automatic promotion for deployment #{deployment_id}...[/bold blue]\n")

    try:
        # Get deployment details and update promotion_blocked_reason
        with db_session_scope() as session:
            deployment = session.query(Deployment).filter(
                Deployment.id == deployment_id
            ).first()

            if not deployment:
                console.print(f"[bold red]✗[/bold red] Deployment {deployment_id} not found")
                raise click.Abort()

            # Display current deployment info
            ring_name = deployment.ring_name or deployment.ring_id
            console.print(f"  Deployment ID: {deployment_id}")
            console.print(f"  Ring: {ring_name}")
            console.print(f"  Status: {deployment.status.value}")

            # Check if promotion is already blocked
            if deployment.promotion_blocked_reason:
                console.print(f"\n[bold yellow]⚠ Warning:[/bold yellow] Promotion already blocked")
                console.print(f"  Previous reason: {deployment.promotion_blocked_reason}")
                console.print(f"\nUpdating with new reason...")

            # Update promotion_blocked_reason
            deployment.promotion_blocked_reason = reason

        console.print(f"\n[bold green]✓[/bold green] Automatic promotion blocked successfully")
        console.print(f"  Reason: {reason}")
        console.print(f"\nThis deployment will not be automatically promoted until the block is cleared.")

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Failed to halt promotion: {str(e)}")
        raise click.Abort()


@jobs.command('purge')
@click.option('--state', default=None,
              type=click.Choice([s.value for s in JobState]),
              help='Only delete jobs in this state (default: all states)')
@click.option('--yes', is_flag=True, help='Skip confirmation prompt')
def purge_jobs(state, yes):
    """Delete job records from the database."""
    engine = OrchestrationEngine()

    # Preview count before deleting
    if state:
        preview = len(engine.get_jobs_by_state(JobState(state)))
    else:
        preview = len(engine.get_all_jobs())

    if preview == 0:
        label = f"{state} " if state else ""
        console.print(f"[yellow]No {label}job records to delete[/yellow]")
        return

    label = f"{state} " if state else ""
    if not yes:
        click.confirm(f"Delete all {preview} {label}job(s) from the database?", abort=True)

    deleted = engine.purge_jobs(state)
    console.print(f"[bold green]✓[/bold green] Deleted {deleted} job(s) from the database")


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
def validate_azure():
    """Validate Azure/Intune configuration for deployment"""
    console.print("[bold blue]Validating Azure configuration...[/bold blue]\n")

    validator = AzureValidator()
    try:
        results = validator.validate_all()
    except AzureConfigurationError as e:
        results = e.results

    # Display results table
    table = Table(title="Azure Validation Results")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Details", style="white")

    has_failure = False
    for result in results:
        if result.passed:
            status = "[bold green]PASS[/bold green]"
        else:
            status = "[bold red]FAIL[/bold red]"
            has_failure = True

        table.add_row(
            result.check_name,
            status,
            result.details or result.message
        )

    console.print(table)

    if has_failure:
        console.print("\n[bold red]✗[/bold red] One or more validation checks failed.")

        if click.confirm("\nWould you like to see remediation steps?", default=True):
            console.print("\n[bold yellow]Remediation Steps[/bold yellow]")
            console.print("─" * 50)

            console.print("\n[bold]1. Set required environment variables:[/bold]")
            console.print("   AZURE_TENANT_ID     - Your Azure AD tenant ID")
            console.print("   AZURE_CLIENT_ID     - Application (client) ID")
            console.print("   AZURE_CLIENT_SECRET - Application client secret")

            console.print("\n[bold]2. Register an app in Azure Portal:[/bold]")
            console.print("   https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade")

            console.print("\n[bold]3. Or use az CLI commands:[/bold]")
            console.print("   az ad app create --display-name AutoPackager")
            console.print("   az ad app credential reset --id <app-id>")

            console.print("\n[bold]4. Run the setup script:[/bold]")
            console.print("   .\\azure-setup.ps1")

        sys.exit(1)
    else:
        console.print("\n[bold green]✓[/bold green] All Azure validation checks passed.")
        sys.exit(0)


@cli.command()
def version():
    """Show version information"""
    from autopackager import __version__
    console.print(f"[bold]AutoPackager[/bold] version {__version__}")


if __name__ == '__main__':
    cli()
