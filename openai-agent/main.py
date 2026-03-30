"""
OpenAI Agent Workflow Example

This example demonstrates building an intelligent agent using OpenAI's SDK with
Render Workflows. It showcases:
- Multi-turn conversations with context management
- Tool/function calling for dynamic actions
- Stateful workflows with decision trees
- Complex agent orchestration
- Error handling for AI operations

Use Case: Customer support agent that can answer questions, look up information,
and perform actions based on user requests
"""

import json
import logging
import os
from datetime import datetime

from render_sdk import Retry, Workflows

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# OpenAI client initialization
_openai_import_error = None

try:
    from openai import AsyncOpenAI
except ImportError as e:
    _openai_import_error = e
    logger.warning("OpenAI package not installed. Install with: pip install openai")


def create_openai_client() -> "AsyncOpenAI":
    """Create a new OpenAI client instance.

    Creates a fresh client each time to avoid atexit registration issues
    that can occur with global async clients in workflow environments.
    """
    if _openai_import_error:
        raise ImportError(
            "OpenAI package not installed. Install with: pip install openai"
        ) from _openai_import_error

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable not set. "
            "Please set it in your Render environment variables."
        )

    return AsyncOpenAI(api_key=api_key)


# Initialize Workflows app with defaults
app = Workflows(
    default_retry=Retry(max_retries=3, wait_duration_ms=2000, backoff_scaling=2.0),
    default_timeout=300,
)


# ============================================================================
# Tool Functions - Actions the agent can perform
# ============================================================================


@app.task
def get_order_status(order_id: str) -> dict:
    """
    Tool: Look up order status.

    In production, this would query a real database or API.

    Args:
        order_id: The order ID to look up

    Returns:
        Dictionary with order status information
    """
    logger.info(f"[TOOL] Looking up order status for: {order_id}")

    # Simulated database lookup
    mock_orders = {
        "ORD-001": {
            "status": "shipped",
            "tracking": "1Z999AA1234567890",
            "eta": "2024-10-15",
        },
        "ORD-002": {"status": "processing", "tracking": None, "eta": "2024-10-12"},
        "ORD-003": {
            "status": "delivered",
            "tracking": "1Z999AA9876543210",
            "eta": "2024-10-08",
        },
    }

    if order_id in mock_orders:
        result = mock_orders[order_id]
        logger.info(f"[TOOL] Order {order_id} found: {result['status']}")
        return {"success": True, "order_id": order_id, **result}
    else:
        logger.warning(f"[TOOL] Order {order_id} not found")
        return {"success": False, "order_id": order_id, "error": "Order not found"}


@app.task
def process_refund(order_id: str, reason: str) -> dict:
    """
    Tool: Process a refund for an order.

    In production, this would integrate with payment systems.

    Args:
        order_id: The order ID to refund
        reason: Reason for the refund

    Returns:
        Dictionary with refund confirmation
    """
    logger.info(f"[TOOL] Processing refund for order: {order_id}")
    logger.info(f"[TOOL] Refund reason: {reason}")

    # Simulated refund processing
    refund_id = f"REF-{order_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    result = {
        "success": True,
        "refund_id": refund_id,
        "order_id": order_id,
        "reason": reason,
        "amount": 99.99,  # Mock amount
        "processed_at": datetime.now().isoformat(),
    }

    logger.info(f"[TOOL] Refund processed: {refund_id}")
    return result


@app.task
def search_knowledge_base(query: str) -> dict:
    """
    Tool: Search the knowledge base for information.

    In production, this would use vector search, Elasticsearch, etc.

    Args:
        query: The search query

    Returns:
        Dictionary with search results
    """
    logger.info(f"[TOOL] Searching knowledge base: {query}")

    # Simulated knowledge base
    knowledge = {
        "shipping": {
            "title": "Shipping Policy",
            "content": "We offer free shipping on orders over $50. Standard shipping takes 3-5 business days. Express shipping is available for $15 and takes 1-2 business days.",
        },
        "returns": {
            "title": "Return Policy",
            "content": "We accept returns within 30 days of purchase. Items must be unused and in original packaging. Refunds are processed within 5-7 business days.",
        },
        "warranty": {
            "title": "Warranty Information",
            "content": "All products come with a 1-year manufacturer warranty. Extended warranties are available for purchase.",
        },
    }

    # Simple keyword matching
    query_lower = query.lower()
    matches = []

    for key, article in knowledge.items():
        if key in query_lower or any(
            word in article["content"].lower() for word in query_lower.split()
        ):
            matches.append(article)

    logger.info(f"[TOOL] Found {len(matches)} knowledge base articles")

    return {"success": True, "query": query, "results": matches, "count": len(matches)}


# ============================================================================
# Agent Tasks
# ============================================================================


@app.task
async def call_llm_with_tools(
    messages: list[dict], tools: list[dict], model: str = "gpt-5.4"
) -> dict:
    """
    Call OpenAI with function/tool definitions.

    This task handles the LLM API call with tool definitions, allowing the
    model to decide which tools to call.

    Args:
        messages: Conversation history
        tools: Available tool definitions
        model: OpenAI model to use

    Returns:
        Dictionary with response and any tool calls
    """
    logger.info(f"[AGENT] Calling {model} with {len(tools)} tools available")

    client = create_openai_client()

    try:
        response = await client.chat.completions.create(
            model=model, messages=messages, tools=tools, tool_choice="auto"
        )

        message = response.choices[0].message
        result = {"content": message.content, "tool_calls": []}

        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
            logger.info(
                f"[AGENT] Model requested {len(result['tool_calls'])} tool calls"
            )

        return result

    except Exception as e:
        logger.error(f"[AGENT] LLM call failed: {e}")
        raise
    finally:
        await client.close()


@app.task
async def execute_tool(tool_name: str, arguments: dict) -> dict:
    """
    Execute a tool function by name.

    This demonstrates dynamic task chaining based on agent decisions.

    Args:
        tool_name: Name of the tool to execute
        arguments: Arguments to pass to the tool

    Returns:
        Tool execution result
    """
    logger.info(f"[AGENT] Executing tool: {tool_name}")
    logger.info(f"[AGENT] Arguments: {arguments}")

    # Map tool names to task functions
    tool_map = {
        "get_order_status": get_order_status,
        "process_refund": process_refund,
        "search_knowledge_base": search_knowledge_base,
    }

    if tool_name not in tool_map:
        logger.error(f"[AGENT] Unknown tool: {tool_name}")
        return {"error": f"Unknown tool: {tool_name}"}

    # Chain a run of the selected tool task
    tool_function = tool_map[tool_name]

    try:
        # Different tools have different signatures
        if tool_name == "get_order_status":
            result = await tool_function(arguments.get("order_id"))
        elif tool_name == "process_refund":
            result = await tool_function(
                arguments.get("order_id"), arguments.get("reason")
            )
        elif tool_name == "search_knowledge_base":
            result = await tool_function(arguments.get("query"))
        else:
            result = {"error": "Tool not implemented"}

        logger.info(f"[AGENT] Tool execution complete: {tool_name}")
        return result

    except Exception as e:
        logger.error(f"[AGENT] Tool execution failed: {e}")
        return {"error": str(e)}


@app.task
async def agent_turn(
    user_message: str, conversation_history: list[dict] = None
) -> dict:
    """
    Execute a single agent turn with tool calling capability.

    This demonstrates:
    - Multi-turn conversation management
    - Tool/function calling
    - Context preservation

    Args:
        user_message: The user's message
        conversation_history: Previous conversation messages

    Returns:
        Dictionary with agent response and updated history
    """
    logger.info("[AGENT TURN] Starting agent turn")

    # Handle case where user_message might be a slice object or other type
    if isinstance(user_message, str):
        logger.info(f"[AGENT TURN] User message: {user_message[:100]}...")
    else:
        logger.error(
            f"[AGENT TURN] Invalid user_message type: {type(user_message)}, value: {user_message}"
        )
        return {
            "success": False,
            "error": f"user_message must be a string, got {type(user_message)}",
            "response": "I'm sorry, there was an error processing your message. Please try again.",
        }

    if conversation_history is None:
        conversation_history = []

    # Define available tools
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_order_status",
                "description": "Look up the status of a customer order by order ID",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "The order ID (e.g., ORD-001)",
                        }
                    },
                    "required": ["order_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "process_refund",
                "description": "Process a refund for an order",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "order_id": {
                            "type": "string",
                            "description": "The order ID to refund",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason for the refund",
                        },
                    },
                    "required": ["order_id", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_knowledge_base",
                "description": "Search the knowledge base for help articles and information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query"}
                    },
                    "required": ["query"],
                },
            },
        },
    ]

    # System prompt
    system_message = {
        "role": "system",
        "content": (
            "You are a helpful customer support agent. You can look up order "
            "status, process refunds, and search the knowledge base for information. "
            "Be polite, professional, and helpful. Use tools when necessary to "
            "assist the customer."
        ),
    }

    # Build messages
    messages = (
        [system_message]
        + conversation_history
        + [{"role": "user", "content": user_message}]
    )

    # Call LLM
    llm_response = await call_llm_with_tools(messages, tools)

    # If no tool calls, return the response
    if not llm_response.get("tool_calls"):
        logger.info("[AGENT TURN] No tool calls, returning response")
        return {
            "response": llm_response["content"],
            "conversation_history": conversation_history
            + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": llm_response["content"]},
            ],
            "tool_calls": [],
        }

    # Execute tool calls
    logger.info(f"[AGENT TURN] Executing {len(llm_response['tool_calls'])} tool calls")
    tool_results = []

    for tool_call in llm_response["tool_calls"]:
        result = await execute_tool(
            tool_call["function"]["name"],
            json.loads(tool_call["function"]["arguments"]),
        )
        tool_results.append({"tool": tool_call["function"]["name"], "result": result})

    # Format tool results for LLM
    tool_messages = [
        {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(tr["result"])}
        for tc, tr in zip(llm_response["tool_calls"], tool_results)
    ]

    # Get final response from LLM with tool results
    final_messages = messages + [
        {
            "role": "assistant",
            "content": llm_response.get("content"),
            "tool_calls": llm_response["tool_calls"],
        },
        *tool_messages,
    ]

    final_response = await call_llm_with_tools(final_messages, tools)

    logger.info("[AGENT TURN] Agent turn complete")

    return {
        "response": final_response["content"],
        "conversation_history": conversation_history
        + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_response["content"]},
        ],
        "tool_calls": tool_results,
    }


@app.task
async def multi_turn_conversation(*messages: str) -> dict:
    """
    Run a multi-turn conversation with the agent.

    This demonstrates how to maintain conversation state across multiple
    agent interactions.

    Args:
        messages: List of user messages to process sequentially

    Returns:
        Dictionary with full conversation and all responses
    """
    messages_list = list(messages)

    logger.info("=" * 80)
    logger.info(
        f"[CONVERSATION] Starting multi-turn conversation with {len(messages_list)} messages"
    )
    logger.info("=" * 80)

    conversation_history = []
    responses = []

    for i, user_message in enumerate(messages_list, 1):
        logger.info(f"[CONVERSATION] Turn {i}/{len(messages_list)}")

        turn_result = await agent_turn(user_message, conversation_history)

        responses.append(
            {
                "turn": i,
                "user": user_message,
                "assistant": turn_result["response"],
                "tool_calls": turn_result.get("tool_calls", []),
            }
        )

        conversation_history = turn_result["conversation_history"]

    logger.info("=" * 80)
    logger.info("[CONVERSATION] Multi-turn conversation complete")
    logger.info(f"[CONVERSATION] Total turns: {len(responses)}")
    logger.info("=" * 80)

    return {
        "turns": responses,
        "total_turns": len(responses),
        "conversation_history": conversation_history,
    }


if __name__ == "__main__":
    app.start()
