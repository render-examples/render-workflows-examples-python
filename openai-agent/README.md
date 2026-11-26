# OpenAI Agent Loop as Workflow Tasks

A customer support agent built on the OpenAI Responses API with the plain `openai` package. The agent loop is written by hand: every model call and every tool call runs as its own workflow task, so each step is retried and shows up in the Dashboard on its own.

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
- Python 3.11+
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

`multi_turn_conversation` takes `*messages`, so pass the messages as a list. A dict such as `{"messages": [...]}` fails, because a `*args` parameter can't be passed by name.

This will process all messages in sequence, maintaining context between turns.

**Note:** The agent uses the model set in `OPENAI_MODEL` (default `gpt-6.2-sol`), so you'll see the AI making intelligent decisions about which tools to call based on the user's message.

## Triggering via SDK

Once deployed, interact with the agent via the Render API or SDK:

```python
from render import Render

# Reads RENDER_API_KEY from the environment. Replace "openai-agent-workflows"
# with your workflow service's slug.
render = Render()

# Single turn. Python tasks also accept named arguments as a dict.
# run_task waits for the run to finish and returns its details.
run = render.workflows.run_task(
    "openai-agent-workflows/agent_turn",
    {
        "user_message": "What is the status of order ORD-001?",
        "conversation_history": [],
    },
)
if run.status == "completed":
    print(f"Agent: {run.results[0]['response']}")
else:
    print(f"Run {run.id} ended as {run.status}: {run.error}")

# Multi-turn. multi_turn_conversation takes *messages, so pass a list.
run = render.workflows.run_task(
    "openai-agent-workflows/multi_turn_conversation",
    [
        "What is your return policy?",
        "What is the status of order ORD-001?",
        "Can you process a refund for that order? It arrived damaged.",
    ],
)

# results is a list holding the task's return value
for turn in run.results[0]["turns"]:
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

### Tool Tasks (Called as Subtasks)

**`get_order_status`**: Looks up order status. Called as a subtask when the agent needs order information.

**`process_refund`**: Processes refunds. Called as a subtask when the agent needs to issue a refund.

**`search_knowledge_base`**: Searches for information. Called as a subtask when the agent needs help articles.

### Orchestration Tasks

**`call_llm_with_tools`**: Calls the OpenAI Responses API with tool definitions. The model decides whether to call tools or respond directly. If OpenAI rejects the request outright (unknown model, invalid key), the task returns an error result instead of raising, so Render doesn't retry it.

**`execute_tool`**: Dynamically executes a tool as a subtask based on the agent's decision:
```python
@app.task
async def execute_tool(ctx: TaskContext, tool_name: str, arguments: dict) -> dict:
    # SUBTASK CALL: ctx.run runs the chosen tool on its own compute.
    # Each tool takes different arguments.
    if tool_name == "get_order_status":
        return await ctx.run(get_order_status, arguments["order_id"])
    if tool_name == "process_refund":
        return await ctx.run(
            process_refund, arguments["order_id"], arguments["reason"]
        )
    if tool_name == "search_knowledge_base":
        return await ctx.run(search_knowledge_base, arguments["query"])
    return {"error": f"Unknown tool: {tool_name}"}
```

**`agent_turn`**: Executes a single conversation turn with nested subtask execution:
1. `await ctx.run(call_llm_with_tools, ...)` - Call LLM with the conversation so far
2. If tools requested: `await ctx.run(execute_tool, ...)` for each tool (which then runs the actual tool task), then go back to step 1 with the results
3. Stop when the model answers in text, or after `MAX_TOOL_ROUNDS` rounds

This demonstrates **nested subtask calling**: `agent_turn` → `execute_tool` → `get_order_status` (3 levels deep!).

**`multi_turn_conversation`**: Orchestrates multiple conversation turns:
```python
for user_message in messages:
    # SUBTASK CALL: Process each message through agent_turn
    turn_result = await ctx.run(agent_turn, user_message, conversation_history)
    conversation_history = turn_result["conversation_history"]
```
This demonstrates **running subtasks in a loop** to maintain conversation state.

## Adding New Tools

To add a new tool capability:

1. **Define the tool function**:
```python
@app.task
def new_tool(ctx: TaskContext, param: str) -> dict:
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

- **OpenAI Costs**: Be mindful of API costs when running the agent frequently
- **Model Selection**: Set `OPENAI_MODEL` to any model that supports function tools in the Responses API (default `gpt-6.2-sol`)
- **Tool Safety**: In production, add authorization checks before executing sensitive tools
- **Rate Limiting**: Consider implementing rate limits for production deployments
