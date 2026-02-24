"""
Data Pipeline Workflow Example

This example demonstrates a complex data pipeline using Render Workflows. It showcases:
- Fetching data from multiple sources (APIs, databases)
- Parallel data extraction
- Data transformation and enrichment
- Combining data from multiple sources
- Final aggregation and reporting
- Complex workflow orchestration

Use Case: Build a comprehensive customer analytics pipeline that combines data
from multiple sources, enriches it with external APIs, and generates insights
"""

import asyncio
import logging
from datetime import datetime, timedelta

from render_sdk import Retry, Workflows

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# HTTP client for API calls
_http_client = None

try:
    import httpx
except ImportError:
    logger.warning("httpx not installed. Install with: pip install httpx")


def get_http_client():
    """Get or initialize HTTP client."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


# Initialize Workflows app with defaults
app = Workflows(
    default_retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=1.5),
    default_timeout=300,
    auto_start=True,
)


# ============================================================================
# Data Source Tasks - Extract from multiple sources
# ============================================================================

@app.task
async def fetch_user_data(user_ids: list[str]) -> dict:
    """
    Fetch user profile data from user service.

    In production, this would call a real API or database.

    Args:
        user_ids: List of user IDs to fetch

    Returns:
        Dictionary containing user data
    """
    logger.info(f"[SOURCE] Fetching user data for {len(user_ids)} users")

    # Simulated user database
    mock_users = {
        "user_1": {"id": "user_1", "name": "Alice Johnson", "email": "alice@example.com", "plan": "premium"},
        "user_2": {"id": "user_2", "name": "Bob Smith", "email": "bob@example.com", "plan": "basic"},
        "user_3": {"id": "user_3", "name": "Charlie Brown", "email": "charlie@example.com", "plan": "premium"},
        "user_4": {"id": "user_4", "name": "Diana Prince", "email": "diana@example.com", "plan": "basic"},
    }

    users = [mock_users.get(uid, {"id": uid, "name": "Unknown", "email": f"{uid}@example.com", "plan": "none"}) for uid in user_ids]

    logger.info(f"[SOURCE] Fetched {len(users)} user records")

    return {
        "success": True,
        "source": "user_service",
        "count": len(users),
        "data": users
    }


@app.task
async def fetch_transaction_data(user_ids: list[str], days: int = 30) -> dict:
    """
    Fetch transaction history for users.

    In production, this would query a transactions database or data warehouse.

    Args:
        user_ids: List of user IDs
        days: Number of days of history to fetch

    Returns:
        Dictionary containing transaction data
    """
    logger.info(f"[SOURCE] Fetching transactions for {len(user_ids)} users ({days} days)")

    # Simulated transaction data
    transactions = []
    for user_id in user_ids:
        # Generate mock transactions
        num_transactions = hash(user_id) % 10 + 1
        for i in range(num_transactions):
            transactions.append({
                "id": f"txn_{user_id}_{i}",
                "user_id": user_id,
                "amount": (hash(f"{user_id}_{i}") % 10000) / 100,
                "type": ["purchase", "refund", "subscription"][hash(f"{user_id}_{i}") % 3],
                "date": (datetime.now() - timedelta(days=hash(f"{user_id}_{i}") % days)).isoformat()
            })

    logger.info(f"[SOURCE] Fetched {len(transactions)} transactions")

    return {
        "success": True,
        "source": "transaction_service",
        "count": len(transactions),
        "data": transactions
    }


@app.task
async def fetch_engagement_data(user_ids: list[str]) -> dict:
    """
    Fetch user engagement metrics.

    In production, this might come from analytics platforms or event tracking.

    Args:
        user_ids: List of user IDs

    Returns:
        Dictionary containing engagement data
    """
    logger.info(f"[SOURCE] Fetching engagement data for {len(user_ids)} users")

    # Simulated engagement data
    engagement = []
    for user_id in user_ids:
        engagement.append({
            "user_id": user_id,
            "page_views": hash(f"pv_{user_id}") % 1000,
            "sessions": hash(f"sess_{user_id}") % 100,
            "last_active": (datetime.now() - timedelta(days=hash(user_id) % 30)).isoformat(),
            "feature_usage": {
                "search": hash(f"search_{user_id}") % 50,
                "export": hash(f"export_{user_id}") % 20,
                "share": hash(f"share_{user_id}") % 30
            }
        })

    logger.info(f"[SOURCE] Fetched engagement for {len(engagement)} users")

    return {
        "success": True,
        "source": "analytics_service",
        "count": len(engagement),
        "data": engagement
    }


# ============================================================================
# Enrichment Tasks - Add additional context
# ============================================================================

@app.task
async def enrich_with_geo_data(user_email: str) -> dict:
    """
    Enrich user data with geographic information.

    In production, this might call a geo-IP service or similar.

    Args:
        user_email: User email (used as identifier)

    Returns:
        Dictionary with geographic data
    """
    logger.info(f"[ENRICH] Enriching geo data for {user_email}")

    # Simulated geo enrichment
    geo_data = {
        "country": ["USA", "Canada", "UK", "Germany"][hash(user_email) % 4],
        "timezone": ["America/New_York", "America/Toronto", "Europe/London", "Europe/Berlin"][hash(user_email) % 4],
        "language": ["en-US", "en-CA", "en-GB", "de-DE"][hash(user_email) % 4]
    }

    return geo_data


@app.task
async def calculate_user_metrics(
    user: dict,
    transactions: list[dict],
    engagement: dict
) -> dict:
    """
    Calculate comprehensive metrics for a single user.

    Combines data from multiple sources to generate user-level insights.

    Args:
        user: User profile data
        transactions: User's transaction history
        engagement: User's engagement metrics

    Returns:
        Dictionary with calculated metrics
    """
    logger.info(f"[METRICS] Calculating metrics for user {user['id']}")

    # Calculate transaction metrics
    user_transactions = [t for t in transactions if t['user_id'] == user['id']]
    total_spent = sum(t['amount'] for t in user_transactions if t['type'] == 'purchase')
    total_refunded = sum(t['amount'] for t in user_transactions if t['type'] == 'refund')
    net_revenue = total_spent - total_refunded

    # Calculate engagement score (0-100)
    engagement_score = min(100, (
        (engagement.get('page_views', 0) / 10) +
        (engagement.get('sessions', 0) / 2) +
        sum(engagement.get('feature_usage', {}).values())
    ))

    # Classify user segment
    if user['plan'] == 'premium' and net_revenue > 100:
        segment = "high_value"
    elif user['plan'] == 'premium':
        segment = "premium"
    elif engagement_score > 50:
        segment = "engaged"
    else:
        segment = "standard"

    metrics = {
        "user_id": user['id'],
        "name": user['name'],
        "email": user['email'],
        "plan": user['plan'],
        "transaction_count": len(user_transactions),
        "total_spent": round(total_spent, 2),
        "total_refunded": round(total_refunded, 2),
        "net_revenue": round(net_revenue, 2),
        "engagement_score": round(engagement_score, 2),
        "segment": segment,
        "page_views": engagement.get('page_views', 0),
        "sessions": engagement.get('sessions', 0)
    }

    logger.info(f"[METRICS] User {user['id']} - Segment: {segment}, Revenue: ${net_revenue:.2f}")

    return metrics


# ============================================================================
# Transformation Tasks - Process and combine data
# ============================================================================

@app.task
async def transform_user_data(
    user_data: dict,
    transaction_data: dict,
    engagement_data: dict
) -> dict:
    """
    Transform and combine data from multiple sources.

    This demonstrates combining parallel data sources and enriching each user.

    Args:
        user_data: Results from fetch_user_data
        transaction_data: Results from fetch_transaction_data
        engagement_data: Results from fetch_engagement_data

    Returns:
        Dictionary with enriched user profiles
    """
    logger.info("[TRANSFORM] Combining data from multiple sources")

    users = user_data.get("data", [])
    transactions = transaction_data.get("data", [])
    engagement_list = engagement_data.get("data", [])

    # Create engagement lookup
    engagement_map = {e['user_id']: e for e in engagement_list}

    # Process each user with all their data
    logger.info(f"[TRANSFORM] Processing {len(users)} users with enrichment")

    enriched_users = []
    for user in users:
        # Get user's engagement data
        user_engagement = engagement_map.get(user['id'], {})

        # Calculate metrics for this user
        user_metrics = await calculate_user_metrics(user, transactions, user_engagement)

        # Enrich with geo data
        user_email = user.get('email', f"{user['id']}@example.com")
        geo_data = await enrich_with_geo_data(user_email)
        user_metrics['geo'] = geo_data

        enriched_users.append(user_metrics)

    logger.info(f"[TRANSFORM] Enriched {len(enriched_users)} user profiles")

    return {
        "success": True,
        "count": len(enriched_users),
        "data": enriched_users
    }


# ============================================================================
# Aggregation Tasks - Generate insights
# ============================================================================

@app.task
def aggregate_insights(enriched_data: dict) -> dict:
    """
    Generate aggregate insights from enriched user data.

    Args:
        enriched_data: Results from transform_user_data

    Returns:
        Dictionary with aggregated insights
    """
    logger.info("[AGGREGATE] Generating insights from enriched data")

    users = enriched_data.get("data", [])

    if not users:
        return {"success": False, "error": "No data to aggregate"}

    # Segment distribution
    segments = {}
    for user in users:
        segment = user['segment']
        segments[segment] = segments.get(segment, 0) + 1

    # Revenue metrics
    total_revenue = sum(u['net_revenue'] for u in users)
    avg_revenue = total_revenue / len(users) if users else 0

    # Engagement metrics
    avg_engagement = sum(u['engagement_score'] for u in users) / len(users) if users else 0

    # Geographic distribution
    countries = {}
    for user in users:
        country = user.get('geo', {}).get('country', 'Unknown')
        countries[country] = countries.get(country, 0) + 1

    # Top users by revenue
    top_users = sorted(users, key=lambda u: u['net_revenue'], reverse=True)[:5]

    insights = {
        "total_users": len(users),
        "segment_distribution": segments,
        "revenue": {
            "total": round(total_revenue, 2),
            "average_per_user": round(avg_revenue, 2),
            "top_users": [
                {"name": u['name'], "revenue": u['net_revenue'], "segment": u['segment']}
                for u in top_users
            ]
        },
        "engagement": {
            "average_score": round(avg_engagement, 2),
            "total_page_views": sum(u['page_views'] for u in users),
            "total_sessions": sum(u['sessions'] for u in users)
        },
        "geographic_distribution": countries,
        "generated_at": datetime.now().isoformat()
    }

    logger.info(f"[AGGREGATE] Insights generated: {len(users)} users, ${total_revenue:.2f} revenue")
    logger.info(f"[AGGREGATE] Segments: {segments}")
    logger.info(f"[AGGREGATE] Countries: {countries}")

    return insights


# ============================================================================
# Main Pipeline Orchestrator
# ============================================================================

@app.task
async def run_data_pipeline(user_ids: list[str]) -> dict:
    """
    Execute the complete data pipeline.

    Pipeline stages:
    1. Extract: Fetch data from multiple sources in parallel
    2. Transform: Combine and enrich data
    3. Load: Aggregate insights and prepare for consumption

    This demonstrates:
    - Parallel data extraction from multiple sources
    - Sequential transformation stages
    - Complex data flow between tasks
    - Comprehensive orchestration

    Args:
        user_ids: List of user IDs to process

    Returns:
        Dictionary with complete pipeline results
    """
    logger.info("=" * 80)
    logger.info("[PIPELINE] Starting Data Pipeline")
    logger.info(f"[PIPELINE] Processing {len(user_ids)} users")
    logger.info("=" * 80)

    try:
        # Stage 1: EXTRACT - Fetch from all sources in parallel
        logger.info("[PIPELINE] Stage 1/3: EXTRACT (parallel)")
        user_task = fetch_user_data(user_ids)
        transaction_task = fetch_transaction_data(user_ids)
        engagement_task = fetch_engagement_data(user_ids)

        # Wait for all extractions to complete
        user_data, transaction_data, engagement_data = await asyncio.gather(
            user_task, transaction_task, engagement_task
        )

        logger.info(f"[PIPELINE] Extracted: {user_data['count']} users, "
                   f"{transaction_data['count']} transactions, "
                   f"{engagement_data['count']} engagement records")

        # Stage 2: TRANSFORM - Combine and enrich
        logger.info("[PIPELINE] Stage 2/3: TRANSFORM")
        enriched_data = await transform_user_data(
            user_data,
            transaction_data,
            engagement_data
        )

        logger.info(f"[PIPELINE] Enriched {enriched_data['count']} user profiles")

        # Stage 3: LOAD - Generate insights
        logger.info("[PIPELINE] Stage 3/3: AGGREGATE")
        insights = await aggregate_insights(enriched_data)

        logger.info("[PIPELINE] Insights generated successfully")

        # Build final result
        pipeline_result = {
            "status": "success",
            "user_count": len(user_ids),
            "stages": {
                "extract": {
                    "users": user_data['count'],
                    "transactions": transaction_data['count'],
                    "engagement": engagement_data['count']
                },
                "transform": {
                    "enriched_users": enriched_data['count']
                },
                "aggregate": {
                    "insights": insights
                }
            },
            "insights": insights,
            "completed_at": datetime.now().isoformat()
        }

        logger.info("=" * 80)
        logger.info("[PIPELINE] Data Pipeline Complete!")
        logger.info(f"[PIPELINE] Total Users: {len(user_ids)}")
        logger.info(f"[PIPELINE] Total Revenue: ${insights['revenue']['total']}")
        logger.info(f"[PIPELINE] Segments: {insights['segment_distribution']}")
        logger.info("=" * 80)

        return pipeline_result

    except Exception as e:
        logger.error(f"[PIPELINE] Pipeline failed: {e}")
        return {
            "status": "failed",
            "error": str(e),
            "failed_at": datetime.now().isoformat()
        }
