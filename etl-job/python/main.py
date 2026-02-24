"""
ETL Job Workflow Example

This example demonstrates a complete ETL (Extract, Transform, Load) pipeline using
Render Workflows. It showcases:
- Data extraction from CSV files
- Data validation and transformation
- Error handling and data quality checks
- Batch processing patterns
- Aggregation and reporting

Use Case: Process customer signup data, validate records, compute statistics
"""

import csv
import logging
from datetime import datetime
from pathlib import Path

from render_sdk import Retry, Workflows

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Workflows app with defaults
app = Workflows(
    default_retry=Retry(max_retries=3, wait_duration_ms=1000, backoff_scaling=1.5),
    default_timeout=300,
    auto_start=True,
)


# ============================================================================
# EXTRACT Tasks
# ============================================================================

@app.task
def extract_csv_data(file_path: str) -> list[dict]:
    """
    Extract data from a CSV file.

    This task reads a CSV file and returns records as a list of dictionaries.
    Includes retry logic for handling temporary file system issues.

    Args:
        file_path: Path to the CSV file

    Returns:
        List of dictionaries representing CSV rows
    """
    logger.info(f"[EXTRACT] Reading CSV file: {file_path}")

    try:
        path = Path(file_path)
        if not path.exists():
            logger.warning("[EXTRACT] File not found, using sample data")
            # In production, this would read from cloud storage or database
            return [
                {"id": "1", "name": "Alice", "email": "alice@example.com", "age": "28", "country": "USA"},
                {"id": "2", "name": "Bob", "email": "bob@example.com", "age": "34", "country": "Canada"},
                {"id": "3", "name": "Charlie", "email": "invalid-email", "age": "invalid", "country": "UK"},
            ]

        records = []
        with open(path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            records = list(reader)

        logger.info(f"[EXTRACT] Successfully extracted {len(records)} records")
        return records

    except Exception as e:
        logger.error(f"[EXTRACT] Failed to read CSV file: {e}")
        raise


# ============================================================================
# TRANSFORM Tasks
# ============================================================================

@app.task
def validate_record(record: dict) -> dict:
    """
    Validate and clean a single data record.

    Performs data quality checks:
    - Email format validation
    - Age range validation
    - Required field checks

    Args:
        record: Dictionary containing record data

    Returns:
        Dictionary with validation results and cleaned data
    """
    logger.info(f"[TRANSFORM] Validating record ID: {record.get('id', 'unknown')}")

    errors = []
    warnings = []

    # Validate required fields
    if not record.get('name'):
        errors.append("Missing name")
    if not record.get('email'):
        errors.append("Missing email")

    # Validate email format
    email = record.get('email', '')
    if email and '@' not in email:
        errors.append("Invalid email format")

    # Validate age
    try:
        age = int(record.get('age', 0))
        if age < 0 or age > 120:
            errors.append(f"Invalid age: {age}")
    except (ValueError, TypeError):
        errors.append(f"Age must be a number: {record.get('age')}")
        age = None

    # Clean and normalize data
    cleaned_record = {
        'id': record.get('id'),
        'name': record.get('name', '').strip(),
        'email': email.lower().strip() if email else None,
        'age': age,
        'country': record.get('country', '').strip(),
        'is_valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings
    }

    status = "✓ VALID" if cleaned_record['is_valid'] else "✗ INVALID"
    logger.info(f"[TRANSFORM] Record {record.get('id')}: {status}")
    if errors:
        logger.warning(f"[TRANSFORM] Errors: {', '.join(errors)}")

    return cleaned_record


@app.task
async def transform_batch(records: list[dict]) -> dict:
    """
    Transform a batch of records by validating each one.

    This demonstrates subtask execution in a loop, processing multiple
    records individually while maintaining error tracking.

    Args:
        records: List of raw records to validate

    Returns:
        Dictionary containing valid records, invalid records, and statistics
    """
    logger.info(f"[TRANSFORM] Starting batch transformation of {len(records)} records")

    valid_records = []
    invalid_records = []

    # Process each record through validation subtask
    # KEY PATTERN: Calling subtasks in a loop
    for i, record in enumerate(records, 1):
        logger.info(f"[TRANSFORM] Processing record {i}/{len(records)}")
        # SUBTASK CALL: Each record is validated by calling validate_record as a subtask
        validated = await validate_record(record)

        if validated['is_valid']:
            valid_records.append(validated)
        else:
            invalid_records.append(validated)

    result = {
        'valid_records': valid_records,
        'invalid_records': invalid_records,
        'total_processed': len(records),
        'valid_count': len(valid_records),
        'invalid_count': len(invalid_records),
        'success_rate': len(valid_records) / len(records) if records else 0
    }

    logger.info(f"[TRANSFORM] Batch complete: {result['valid_count']} valid, "
                f"{result['invalid_count']} invalid")

    return result


# ============================================================================
# LOAD Tasks
# ============================================================================

@app.task
def compute_statistics(valid_records: list[dict]) -> dict:
    """
    Compute statistical insights from validated records.

    Aggregates data to produce:
    - Country distribution
    - Age statistics
    - Data quality metrics

    Args:
        valid_records: List of validated records

    Returns:
        Dictionary containing computed statistics
    """
    logger.info(f"[LOAD] Computing statistics for {len(valid_records)} records")

    if not valid_records:
        logger.warning("[LOAD] No valid records to analyze")
        return {
            'total_records': 0,
            'country_distribution': {},
            'age_stats': {}
        }

    # Country distribution
    country_counts = {}
    for record in valid_records:
        country = record.get('country', 'Unknown')
        country_counts[country] = country_counts.get(country, 0) + 1

    # Age statistics
    ages = [r['age'] for r in valid_records if r.get('age') is not None]
    age_stats = {}
    if ages:
        age_stats = {
            'min': min(ages),
            'max': max(ages),
            'average': sum(ages) / len(ages),
            'count': len(ages)
        }

    statistics = {
        'total_records': len(valid_records),
        'country_distribution': country_counts,
        'age_stats': age_stats,
        'timestamp': datetime.now().isoformat()
    }

    logger.info("[LOAD] Statistics computed successfully")
    logger.info(f"[LOAD] Countries: {list(country_counts.keys())}")
    if age_stats:
        logger.info(f"[LOAD] Age range: {age_stats['min']}-{age_stats['max']}")

    return statistics


# ============================================================================
# MAIN ETL Pipeline
# ============================================================================

@app.task
async def run_etl_pipeline(source_file: str) -> dict:
    """
    Complete ETL pipeline orchestrating extract, transform, and load operations.

    Pipeline stages:
    1. Extract: Read data from CSV file
    2. Transform: Validate and clean records
    3. Load: Compute statistics and prepare for storage

    This demonstrates a full workflow with multiple subtask calls and
    comprehensive error handling.

    Args:
        source_file: Path to source CSV file

    Returns:
        Dictionary containing pipeline results and statistics
    """
    logger.info("=" * 80)
    logger.info("[PIPELINE] Starting ETL Pipeline")
    logger.info(f"[PIPELINE] Source: {source_file}")
    logger.info("=" * 80)

    try:
        # Stage 1: Extract
        logger.info("[PIPELINE] Stage 1/3: EXTRACT")
        # SUBTASK CALL: Extract data from CSV
        raw_records = await extract_csv_data(source_file)
        logger.info(f"[PIPELINE] Extracted {len(raw_records)} records")

        # Stage 2: Transform
        logger.info("[PIPELINE] Stage 2/3: TRANSFORM")
        # SUBTASK CALL: Transform calls validate_record for each record
        transform_result = await transform_batch(raw_records)
        logger.info(f"[PIPELINE] Transformation complete: "
                   f"{transform_result['success_rate']:.1%} success rate")

        # Stage 3: Load (compute statistics)
        logger.info("[PIPELINE] Stage 3/3: LOAD")
        # SUBTASK CALL: Compute final statistics
        statistics = await compute_statistics(transform_result['valid_records'])
        logger.info("[PIPELINE] Statistics computed")

        # Build final result
        pipeline_result = {
            'status': 'success',
            'extract': {
                'records_extracted': len(raw_records),
                'source': source_file
            },
            'transform': {
                'valid_count': transform_result['valid_count'],
                'invalid_count': transform_result['invalid_count'],
                'success_rate': transform_result['success_rate'],
                'invalid_records': transform_result['invalid_records']
            },
            'load': {
                'statistics': statistics
            },
            'completed_at': datetime.now().isoformat()
        }

        logger.info("=" * 80)
        logger.info("[PIPELINE] ETL Pipeline Complete!")
        logger.info(f"[PIPELINE] Processed: {len(raw_records)} records")
        logger.info(f"[PIPELINE] Valid: {transform_result['valid_count']} records")
        logger.info(f"[PIPELINE] Invalid: {transform_result['invalid_count']} records")
        logger.info("=" * 80)

        return pipeline_result

    except Exception as e:
        logger.error(f"[PIPELINE] ETL Pipeline failed: {e}")
        return {
            'status': 'failed',
            'error': str(e),
            'failed_at': datetime.now().isoformat()
        }
