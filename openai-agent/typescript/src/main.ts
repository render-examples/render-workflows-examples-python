import "dotenv/config";
import { task } from "@renderinc/sdk/workflows";
import OpenAI from "openai";
import type { ChatCompletionMessageParam, ChatCompletionTool } from "openai/resources/chat/completions";

const retry = {
  maxRetries: 3,
  waitDurationMs: 2000,
  factor: 2.0,
};

function createOpenAIClient(): OpenAI {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    throw new Error(
      "OPENAI_API_KEY environment variable not set. " +
      "Please set it in your Render environment variables.",
    );
  }
  return new OpenAI({ apiKey });
}

// ---- Tool Functions ----

const getOrderStatus = task(
  { name: "getOrderStatus", retry },
  function getOrderStatus(orderId: string) {
    console.log(`[TOOL] Looking up order status for: ${orderId}`);

    const mockOrders: { [key: string]: { status: string; tracking: string | null; eta: string } } = {
      "ORD-001": { status: "shipped", tracking: "1Z999AA1234567890", eta: "2024-10-15" },
      "ORD-002": { status: "processing", tracking: null, eta: "2024-10-12" },
      "ORD-003": { status: "delivered", tracking: "1Z999AA9876543210", eta: "2024-10-08" },
    };

    if (orderId in mockOrders) {
      const order = mockOrders[orderId];
      console.log(`[TOOL] Order ${orderId} found: ${order.status}`);
      return { success: true, order_id: orderId, ...order };
    }

    console.warn(`[TOOL] Order ${orderId} not found`);
    return { success: false, order_id: orderId, error: "Order not found" };
  },
);

const processRefund = task(
  { name: "processRefund", retry },
  function processRefund(orderId: string, reason: string) {
    console.log(`[TOOL] Processing refund for order: ${orderId}`);
    console.log(`[TOOL] Refund reason: ${reason}`);

    const refundId = `REF-${orderId}-${new Date().toISOString().replace(/[-:T.]/g, "").slice(0, 14)}`;

    const result = {
      success: true,
      refund_id: refundId,
      order_id: orderId,
      reason,
      amount: 99.99,
      processed_at: new Date().toISOString(),
    };

    console.log(`[TOOL] Refund processed: ${refundId}`);
    return result;
  },
);

const searchKnowledgeBase = task(
  { name: "searchKnowledgeBase", retry },
  function searchKnowledgeBase(query: string) {
    console.log(`[TOOL] Searching knowledge base: ${query}`);

    const knowledge: { [key: string]: { title: string; content: string } } = {
      shipping: {
        title: "Shipping Policy",
        content:
          "We offer free shipping on orders over $50. Standard shipping takes 3-5 business days. Express shipping is available for $15 and takes 1-2 business days.",
      },
      returns: {
        title: "Return Policy",
        content:
          "We accept returns within 30 days of purchase. Items must be unused and in original packaging. Refunds are processed within 5-7 business days.",
      },
      warranty: {
        title: "Warranty Information",
        content:
          "All products come with a 1-year manufacturer warranty. Extended warranties are available for purchase.",
      },
    };

    const queryLower = query.toLowerCase();
    const matches = Object.entries(knowledge)
      .filter(
        ([key, article]) =>
          queryLower.includes(key) ||
          queryLower.split(" ").some((word) => article.content.toLowerCase().includes(word)),
      )
      .map(([, article]) => article);

    console.log(`[TOOL] Found ${matches.length} knowledge base articles`);
    return { success: true, query, results: matches, count: matches.length };
  },
);

// ---- Agent Tasks ----

const tools: ChatCompletionTool[] = [
  {
    type: "function",
    function: {
      name: "get_order_status",
      description: "Look up the status of a customer order by order ID",
      parameters: {
        type: "object",
        properties: {
          order_id: { type: "string", description: "The order ID (e.g., ORD-001)" },
        },
        required: ["order_id"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "process_refund",
      description: "Process a refund for an order",
      parameters: {
        type: "object",
        properties: {
          order_id: { type: "string", description: "The order ID to refund" },
          reason: { type: "string", description: "Reason for the refund" },
        },
        required: ["order_id", "reason"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "search_knowledge_base",
      description: "Search the knowledge base for help articles and information",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "The search query" },
        },
        required: ["query"],
      },
    },
  },
];

const callLlmWithTools = task(
  { name: "callLlmWithTools", retry },
  async function callLlmWithTools(
    messages: ChatCompletionMessageParam[],
    toolDefs: ChatCompletionTool[],
    model: string = "gpt-4",
  ) {
    console.log(`[AGENT] Calling ${model} with ${toolDefs.length} tools available`);

    const client = createOpenAIClient();

    const response = await client.chat.completions.create({
      model,
      messages,
      tools: toolDefs,
      tool_choice: "auto",
    });

    const message = response.choices[0].message;
    const result: {
      content: string | null;
      tool_calls: { id: string; type: string; function: { name: string; arguments: string } }[];
    } = { content: message.content, tool_calls: [] };

    if (message.tool_calls) {
      result.tool_calls = message.tool_calls.map((tc) => ({
        id: tc.id,
        type: "function",
        function: { name: tc.function.name, arguments: tc.function.arguments },
      }));
      console.log(`[AGENT] Model requested ${result.tool_calls.length} tool calls`);
    }

    return result;
  },
);

const executeTool = task(
  { name: "executeTool", retry },
  async function executeTool(toolName: string, args: { [key: string]: string }) {
    console.log(`[AGENT] Executing tool: ${toolName}`);

    try {
      switch (toolName) {
        case "get_order_status":
          return await getOrderStatus(args.order_id);
        case "process_refund":
          return await processRefund(args.order_id, args.reason);
        case "search_knowledge_base":
          return await searchKnowledgeBase(args.query);
        default:
          console.error(`[AGENT] Unknown tool: ${toolName}`);
          return { error: `Unknown tool: ${toolName}` };
      }
    } catch (error) {
      console.error(`[AGENT] Tool execution failed: ${error}`);
      return { error: String(error) };
    }
  },
);

const agentTurn = task(
  { name: "agentTurn", retry },
  async function agentTurn(
    userMessage: string,
    conversationHistory: ChatCompletionMessageParam[] = [],
  ) {
    console.log("[AGENT TURN] Starting agent turn");

    if (typeof userMessage !== "string") {
      return {
        success: false,
        error: `user_message must be a string, got ${typeof userMessage}`,
        response: "I'm sorry, there was an error processing your message. Please try again.",
      };
    }

    const systemMessage: ChatCompletionMessageParam = {
      role: "system",
      content:
        "You are a helpful customer support agent. You can look up order " +
        "status, process refunds, and search the knowledge base for information. " +
        "Be polite, professional, and helpful. Use tools when necessary to " +
        "assist the customer.",
    };

    const messages: ChatCompletionMessageParam[] = [
      systemMessage,
      ...conversationHistory,
      { role: "user", content: userMessage },
    ];

    const llmResponse = await callLlmWithTools(messages, tools);

    if (!llmResponse.tool_calls.length) {
      console.log("[AGENT TURN] No tool calls, returning response");
      return {
        response: llmResponse.content,
        conversation_history: [
          ...conversationHistory,
          { role: "user" as const, content: userMessage },
          { role: "assistant" as const, content: llmResponse.content },
        ],
        tool_calls: [],
      };
    }

    console.log(`[AGENT TURN] Executing ${llmResponse.tool_calls.length} tool calls`);
    const toolResults: { tool: string; result: unknown }[] = [];

    for (const toolCall of llmResponse.tool_calls) {
      const result = await executeTool(
        toolCall.function.name,
        JSON.parse(toolCall.function.arguments),
      );
      toolResults.push({ tool: toolCall.function.name, result });
    }

    const toolMessages: ChatCompletionMessageParam[] = llmResponse.tool_calls.map((tc, i) => ({
      role: "tool" as const,
      tool_call_id: tc.id,
      content: JSON.stringify(toolResults[i].result),
    }));

    const finalMessages: ChatCompletionMessageParam[] = [
      ...messages,
      {
        role: "assistant" as const,
        content: llmResponse.content,
        tool_calls: llmResponse.tool_calls.map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: { name: tc.function.name, arguments: tc.function.arguments },
        })),
      },
      ...toolMessages,
    ];

    const finalResponse = await callLlmWithTools(finalMessages, tools);

    console.log("[AGENT TURN] Agent turn complete");

    return {
      response: finalResponse.content,
      conversation_history: [
        ...conversationHistory,
        { role: "user" as const, content: userMessage },
        { role: "assistant" as const, content: finalResponse.content },
      ],
      tool_calls: toolResults,
    };
  },
);

// Root task: multi-turn conversation
task(
  { name: "multiTurnConversation", retry, timeoutSeconds: 300 },
  async function multiTurnConversation(...messages: string[]) {
    console.log("=".repeat(80));
    console.log(`[CONVERSATION] Starting multi-turn conversation with ${messages.length} messages`);
    console.log("=".repeat(80));

    let conversationHistory: ChatCompletionMessageParam[] = [];
    const responses: { turn: number; user: string; assistant: string | null; tool_calls: unknown[] }[] = [];

    for (let i = 0; i < messages.length; i++) {
      console.log(`[CONVERSATION] Turn ${i + 1}/${messages.length}`);

      const turnResult = await agentTurn(messages[i], conversationHistory);

      responses.push({
        turn: i + 1,
        user: messages[i],
        assistant: turnResult.response,
        tool_calls: turnResult.tool_calls ?? [],
      });

      conversationHistory = turnResult.conversation_history ?? [];
    }

    console.log("=".repeat(80));
    console.log("[CONVERSATION] Multi-turn conversation complete");
    console.log("=".repeat(80));

    return {
      turns: responses,
      total_turns: responses.length,
      conversation_history: conversationHistory,
    };
  },
);
