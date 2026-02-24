import "dotenv/config";
import express from "express";
import multer from "multer";
import { Render, ClientError, ServerError } from "@renderinc/sdk";

const app = express();
const upload = multer({ storage: multer.memoryStorage() });

function getClient(): Render {
  const apiKey = process.env.RENDER_API_KEY;
  if (!apiKey) {
    throw new Error(
      "RENDER_API_KEY not configured. Get your API key from Render Dashboard > Account Settings > API Keys",
    );
  }
  return new Render();
}

function getTaskIdentifier(taskName: string): string {
  const serviceSlug = process.env.WORKFLOW_SERVICE_SLUG;
  if (!serviceSlug) {
    throw new Error(
      "WORKFLOW_SERVICE_SLUG not configured. Set this to your workflow service slug from Render Dashboard.",
    );
  }
  const identifier = `${serviceSlug}/${taskName}`;
  console.log(`Task identifier: ${identifier}`);
  return identifier;
}

app.get("/", (_req, res) => {
  res.json({
    service: "File Analyzer API",
    version: "0.1.0",
    description: "Upload CSV files for analysis via workflow tasks",
    endpoints: {
      "POST /analyze": "Upload and analyze a CSV file",
      "GET /health": "Health check and configuration status",
    },
  });
});

app.get("/health", (_req, res) => {
  const apiKey = process.env.RENDER_API_KEY;
  const serviceSlug = process.env.WORKFLOW_SERVICE_SLUG;

  res.json({
    status: "healthy",
    render_api_key_configured: Boolean(apiKey),
    workflow_service_slug_configured: Boolean(serviceSlug),
    workflow_service_slug: serviceSlug ?? null,
  });
});

app.post("/analyze", upload.single("file"), async (req, res) => {
  const file = req.file;
  if (!file) {
    res.status(400).json({ detail: "No file uploaded." });
    return;
  }

  if (!file.originalname.endsWith(".csv")) {
    res.status(400).json({ detail: "Only CSV files are supported. Please upload a .csv file." });
    return;
  }

  console.log(`Received file upload: ${file.originalname}`);

  try {
    const fileContent = file.buffer.toString("utf-8");
    console.log(`File content size: ${fileContent.length} bytes`);

    const client = getClient();
    const taskIdentifier = getTaskIdentifier("analyzeFile");

    console.log(`Calling workflow task: ${taskIdentifier}`);

    const taskRun = await client.workflows.runTask(taskIdentifier, [fileContent]);

    console.log(`Task started: ${taskRun.id}`);

    const result = await taskRun;

    console.log(`Task completed with status: ${result.status}`);

    res.json({
      task_run_id: result.id,
      status: result.status,
      message: `File '${file.originalname}' analyzed successfully`,
      result: result.results,
    });
  } catch (error) {
    if (error instanceof ClientError) {
      console.error(`Client error: ${error.statusCode}`);
      res.status(error.statusCode).json({ detail: `Client error: ${error.message}` });
    } else if (error instanceof ServerError) {
      console.error(`Server error: ${error.statusCode}`);
      res.status(500).json({ detail: `Server error: ${error.message}` });
    } else {
      console.error(`Unexpected error: ${error}`);
      res.status(500).json({ detail: `Analysis failed: ${String(error)}` });
    }
  }
});

app.post("/analyze-task/:taskName", upload.single("file"), async (req, res) => {
  const taskName = req.params.taskName as string;
  const file = req.file;

  if (!file) {
    res.status(400).json({ detail: "No file uploaded." });
    return;
  }

  if (!file.originalname.endsWith(".csv")) {
    res.status(400).json({ detail: "Only CSV files are supported. Please upload a .csv file." });
    return;
  }

  console.log(`Received file upload for task '${taskName}': ${file.originalname}`);

  try {
    const fileContent = file.buffer.toString("utf-8");
    const client = getClient();
    const taskIdentifier = getTaskIdentifier(taskName);

    console.log(`Calling workflow task: ${taskIdentifier}`);

    const taskRun = await client.workflows.runTask(taskIdentifier, [fileContent]);

    console.log(`Task started: ${taskRun.id}`);

    const result = await taskRun;

    console.log(`Task '${taskName}' completed with status: ${result.status}`);

    res.json({
      task_run_id: result.id,
      status: result.status,
      message: `File '${file.originalname}' processed with task '${taskName}'`,
      result: result.results,
    });
  } catch (error) {
    if (error instanceof ClientError) {
      res.status(error.statusCode).json({ detail: `Client error: ${error.message}` });
    } else if (error instanceof ServerError) {
      res.status(500).json({ detail: `Server error: ${error.message}` });
    } else {
      res.status(500).json({ detail: `Analysis failed: ${String(error)}` });
    }
  }
});

const PORT = parseInt(process.env.PORT ?? "8000", 10);

app.listen(PORT, () => {
  console.log("Starting File Analyzer API Service");
  console.log("This service calls workflow tasks using the Client SDK");
  console.log("Required environment variables:");
  console.log("  - RENDER_API_KEY: Your Render API key");
  console.log("  - WORKFLOW_SERVICE_SLUG: Your workflow service slug");
  console.log(`Listening on http://0.0.0.0:${PORT}`);
});
