# OpenAI Agent Workflow Example

An intelligent conversational agent built with OpenAI's SDK and Render Workflows, featuring tool calling and multi-turn conversations.

## Use Case

Customer support agent that can:
- Answer questions using a knowledge base
- Look up order status
- Process refunds
- Maintain conversation context
- Make intelligent decisions about which tools to use

This pattern is useful for:
- Customer service automation
- Interactive assistants
- Task automation agents
- Intelligent workflow orchestration
- AI-powered help desks

## Features

- **Tool Calling**: Agent can call functions to perform actions (order lookup, refunds, knowledge base search)
- **Multi-Turn Conversations**: Maintains context across multiple interactions
- **Dynamic Tool Selection**: AI decides which tools to use based on user intent
- **Stateful Workflows**: Preserves conversation history
- **Error Handling**: Robust handling of API failures with retries
- **Extensible**: Easy to add new tools and capabilities

## Workflow Structure

```
multi_turn_conversation (orchestrator)
  └── agent_turn (for each message)
      ├── call_llm_with_tools (OpenAI API call)
      └── execute_tool (if tools requested)
          ├── get_order_status
          ├── process_refund
          └── search_knowledge_base
```

## Local Development

### Prerequisites
- Python 3.10+
- OpenAI API key

### Setup and Run

```bash
# Navigate to example directory
cd openai-agent

# Install dependencies
pip install -r requirements.txt

# Set OpenAI API key
export OPENAI_API_KEY="your-openai-api-key"

# Run the workflow service
python main.py
```

## Deploying to Render

### Service Configuration

**Service Type**: Workflow

**Build Command**:
```bash
cd openai-agent && pip install -r requirements.txt
```

**Start Command**:
```bash
cd openai-agent && python main.py
```

### Environment Variables

Required:
- `RENDER_API_KEY` - Your Render API key (from Render dashboard)
- `OPENAI_API_KEY` - Your OpenAI API key (from OpenAI platform)

### Deployment Steps

1. **Create Workflow Service**
   - Go to Render Dashboard
   - Click "New +" → "Workflow"
   - Connect your repository
   - Name: `openai-agent-workflows`

2. **Configure Build Settings**
   - Build Command: `cd openai-agent && pip install -r requirements.txt`
   - Start Command: `cd openai-agent && python main.py`

3. **Set Environment Variables**
   - Add `RENDER_API_KEY` in the Environment section
   - Add `OPENAI_API_KEY` in the Environment section
   - Get Render API key from: Render Dashboard → Account Settings → API Keys
   - Get OpenAI API key from: https://platform.openai.com/api-keys

4. **Deploy**
   - Click "Create Workflow"
   - Render will build and start your workflow service

## Testing in Render Dashboard

Once deployed, you can test the agent directly in the Render Dashboard:

### How to Test

1. Go to your Workflow service in Render Dashboard
2. Click the **"Manual Run"** or **"Start Task"** button
3. Select the task you want to test
4. Enter the task input as JSON in the text area
5. Click **"Start task"**

### Example Task Inputs

**Important:** The OpenAI agent workflow expects array inputs, not JSON objects. The first element is the user message, the second is the conversation history.

**Recommended Starting Point:** Start with `agent_turn` for a single conversation turn, or `multi_turn_conversation` to see how the agent maintains context across multiple messages.

---

**Test single conversation turn:**

Task: `agent_turn`

Input:
```json
[
  "What is the status of order ORD-001?",
  []
]
```

This will make the agent look up the order and respond with status information.

---

**Test order refund:**

Task: `agent_turn`

Input:
```json
[
  "I need a refund for order ORD-002, it arrived damaged.",
  []
]
```

The agent will process the refund and provide confirmation.

---

**Test knowledge base search:**

Task: `agent_turn`

Input:
```json
[
  "What is your return policy?",
  []
]
```

The agent will search the knowledge base and provide policy information.

---

**Test multi-turn conversation:**

Task: `multi_turn_conversation`

Input:
```json
[
  "What is your return policy?",
  "What is the status of order ORD-001?",
  "Can you process a refund for that order? It arrived damaged."
]
```

This will process all messages in sequence, maintaining context between turns.

**Note:** The agent uses the configured OpenAI model, so you'll see the AI decide which tools to call based on the user's message.

## Triggering via SDK

Once deployed, interact with the agent via the Render API or SDK:

```python
from render_sdk import Render

# Uses RENDER_API_KEY environment variable automatically
render = Render()

# Single turn conversation
task_run = await render.workflows.run_task(
    "openai-agent-workflows/agent_turn",
    {
        "user_message": "What is the status of order ORD-001?",
        "conversation_history": []
    }
)
print(f"Agent: {task_run.results['response']}")

# Multi-turn conversation
task_run = await render.workflows.run_task(
    "openai-agent-workflows/multi_turn_conversation",
    [
        "What is your return policy?",
        "What is the status of order ORD-001?",
        "Can you process a refund for that order? It arrived damaged."
    ]
)
for turn in task_run.results['turns']:
    print(f"User: {turn['user']}")
    print(f"Agent: {turn['assistant']}")
    print(f"Tools used: {[t['tool'] for t in turn['tool_calls']]}")
    print()
```

## Available Tools

### `get_order_status`
Looks up order status by order ID.

**Input**: `{"order_id": "ORD-001"}`

**Output**: Order status, tracking number, ETA

### `process_refund`
Processes a refund for an order.

**Input**: `{"order_id": "ORD-001", "reason": "Item damaged"}`

**Output**: Refund confirmation with refund ID

### `search_knowledge_base`
Searches the knowledge base for information.

**Input**: `{"query": "shipping policy"}`

**Output**: Relevant articles and information

## Task Descriptions

### Tool Tasks (Called via Task Chaining)

**`get_order_status`**: Looks up order status. Chained when the agent needs order information.

**`process_refund`**: Processes refunds. Chained when the agent needs to issue a refund.

**`search_knowledge_base`**: Searches for information. Chained when the agent needs help articles.

### Orchestration Tasks

**`call_llm_with_tools`**: Calls OpenAI API with tool definitions. The model decides whether to call tools or respond directly.

**`execute_tool`**: Dynamically executes a tool via task chaining based on the agent's decision:
```python
@app.task
async def execute_tool(tool_name: str, arguments: dict) -> dict:
    # Map tool names to tasks
    tool_map = {
        "get_order_status": get_order_status,
        "process_refund": process_refund,
        "search_knowledge_base": search_knowledge_base
    }

    # TASK CHAINING: Execute the appropriate tool function
    result = await tool_map[tool_name](**arguments)
    return result
```

**`agent_turn`**: Executes a single conversation turn with nested task chaining:
1. `await call_llm_with_tools(...)` - Call LLM with user message
2. If tools requested: `await execute_tool(...)` for each tool (which then calls the actual tool task)
3. `await call_llm_with_tools(...)` again with tool results to generate final response

This demonstrates **nested task chaining**: `agent_turn` → `execute_tool` → `get_order_status` (3 levels deep).

**`multi_turn_conversation`**: Orchestrates multiple conversation turns:
```python
for user_message in messages:
    # TASK CHAINING: Process each message through agent_turn
    turn_result = await agent_turn(user_message, conversation_history)
    conversation_history = turn_result["conversation_history"]
```
This demonstrates **task chaining in a loop** to maintain conversation state.

## Adding New Tools

To add a new tool capability:

1. **Define the tool function**:
```python
@app.task
def new_tool(param: str) -> dict:
    """Tool: Description of what this tool does."""
    # Implementation
    return {"result": "data"}
```

2. **Add tool definition** in `agent_turn`:
```python
{
    "type": "function",
    "function": {
        "name": "new_tool",
        "description": "Description for the AI",
        "parameters": {
            "type": "object",
            "properties": {
                "param": {
                    "type": "string",
                    "description": "Parameter description"
                }
            },
            "required": ["param"]
        }
    }
}
```

3. **Add to tool map** in `execute_tool`:
```python
tool_map = {
    "get_order_status": get_order_status,
    "process_refund": process_refund,
    "search_knowledge_base": search_knowledge_base,
    "new_tool": new_tool  # Add here
}
```

## Example Conversations

**Order Status Inquiry**:
```
User: "What is the status of order ORD-001?"
→ Agent calls get_order_status(order_id="ORD-001")
Agent: "Your order ORD-001 has been shipped! The tracking number is 1Z999AA1234567890 and the estimated delivery is October 15th."
```

**Refund Request**:
```
User: "I need a refund for order ORD-002, the item is defective."
→ Agent calls process_refund(order_id="ORD-002", reason="Item defective")
Agent: "I've processed your refund for order ORD-002. Your refund ID is REF-ORD-002-20241010... The amount of $99.99 will be returned to your original payment method within 5-7 business days."
```

**Knowledge Base Query**:
```
User: "What is your shipping policy?"
→ Agent calls search_knowledge_base(query="shipping policy")
Agent: "We offer free shipping on orders over $50. Standard shipping takes 3-5 business days, and express shipping is available for $15 with delivery in 1-2 business days."
```

## Important Notes

- **SDK languages**: Workflows support Python and TypeScript; this repo's examples are Python.
- **No Blueprint Support**: Workflows don't support render.yaml blueprint configuration
- **OpenAI Costs**: Be mindful of API costs when running the agent frequently
- **Model Selection**: The default model is configurable in `main.py`; choose based on latency/cost/quality needs.
- **Tool Safety**: In production, add authorization checks before executing sensitive tools
- **Rate Limiting**: Consider implementing rate limits for production deployments
