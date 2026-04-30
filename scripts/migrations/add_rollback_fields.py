"""Database Migration: Add Rollback Fields to Deployments Table

Adds rolled_back_at, rollback_reason, and previous_package_id columns
to the deployments table to support rollback tracking functionality.
"""

import argparse
import sys
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from autopackager.utils.database import get_database_url
from autopackager.utils.logger import get_logger

logger = get_logger(__name__)


def check_column_exists(inspector, table_name, column_name):
    """Check if a column exists in a table"""
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def add_rollback_fields(engine, dry_run=False):
    """Add rollback tracking fields to deployments table

    Args:
        engine: SQLAlchemy engine instance
        dry_run: If True, only check what would be done without executing

    Returns:
        bool: True if migration successful, False otherwise
    """
    inspector = inspect(engine)

    # Check if deployments table exists
    if 'deployments' not in inspector.get_table_names():
        logger.error("Deployments table does not exist")
        return False

    # Define columns to add
    columns_to_add = [
        {
            'name': 'rolled_back_at',
            'definition': 'TIMESTAMP NULL'
        },
        {
            'name': 'rollback_reason',
            'definition': 'VARCHAR(1024) NULL'
        },
        {
            'name': 'previous_package_id',
            'definition': 'INTEGER NULL'
        }
    ]

    # Check which columns need to be added
    columns_needed = []
    for column in columns_to_add:
        if not check_column_exists(inspector, 'deployments', column['name']):
            columns_needed.append(column)
            logger.info(
                "Column needs to be added",
                column=column['name'],
                table='deployments'
            )
        else:
            logger.info(
                "Column already exists",
                column=column['name'],
                table='deployments'
            )

    if not columns_needed:
        logger.info("All rollback fields already exist, no migration needed")
        return True

    if dry_run:
        logger.info(
            "Dry run - would add columns",
            columns=[col['name'] for col in columns_needed]
        )
        return True

    # Add missing columns
    try:
        with engine.connect() as conn:
            for column in columns_needed:
                alter_sql = f"ALTER TABLE deployments ADD COLUMN {column['name']} {column['definition']}"
                logger.info(
                    "Adding column",
                    column=column['name'],
                    sql=alter_sql
                )
                conn.execute(text(alter_sql))
                conn.commit()
                logger.info("Column added successfully", column=column['name'])

        logger.info("Migration completed successfully")
        return True

    except SQLAlchemyError as e:
        logger.error("Migration failed", error=str(e))
        return False


def main():
    """Main migration script entry point"""
    parser = argparse.ArgumentParser(
        description='Add rollback fields to deployments table'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Check what would be done without executing'
    )
    parser.add_argument(
        '--test-db',
        type=str,
        help='Use test database URL instead of config (e.g., sqlite:///test.db)'
    )

    args = parser.parse_args()

    try:
        # Get database URL
        if args.test_db:
            database_url = args.test_db
            logger.info("Using test database", url=database_url)
        else:
            database_url = get_database_url()
            logger.info(
                "Connecting to database",
                url=database_url.split('@')[-1] if '@' in database_url else database_url
            )

        engine = create_engine(database_url)

        # Run migration
        success = add_rollback_fields(engine, dry_run=args.dry_run)

        if success:
            print("Migration successful")
            sys.exit(0)
        else:
            print("Migration failed")
            sys.exit(1)

    except Exception as e:
        logger.error("Migration script failed", error=str(e))
        print(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
