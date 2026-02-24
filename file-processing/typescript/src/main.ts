import "dotenv/config";
import { task } from "@renderinc/sdk/workflows";
import { readFileSync, existsSync } from "node:fs";
import { resolve, extname } from "node:path";

const retry = {
  maxRetries: 3,
  waitDurationMs: 1000,
  factor: 1.5,
};

// ---- File Reading Tasks ----

const readCsvFile = task(
  { name: "readCsvFile", retry },
  function readCsvFile(filePath: string) {
    console.log(`[CSV] Reading file: ${filePath}`);

    const fullPath = resolve(filePath);
    if (!existsSync(fullPath)) {
      console.warn(`[CSV] File not found: ${filePath}`);
      return { success: false, error: "File not found", file_path: filePath };
    }

    try {
      const content = readFileSync(fullPath, "utf-8");
      const lines = content.trim().split("\n");
      if (lines.length < 2) {
        return { success: true, file_path: filePath, file_type: "csv", row_count: 0, data: [], columns: [] };
      }

      const headers = lines[0].split(",").map((h) => h.trim());
      const rows = lines.slice(1).map((line) => {
        const values = line.split(",").map((v) => v.trim());
        const row: { [key: string]: string } = {};
        headers.forEach((h, i) => { row[h] = values[i]; });
        return row;
      });

      console.log(`[CSV] Successfully read ${rows.length} rows`);
      return { success: true, file_path: filePath, file_type: "csv", row_count: rows.length, data: rows, columns: headers };
    } catch (e) {
      console.error(`[CSV] Error reading file: ${e}`);
      return { success: false, error: String(e), file_path: filePath };
    }
  },
);

const readJsonFile = task(
  { name: "readJsonFile", retry },
  function readJsonFile(filePath: string) {
    console.log(`[JSON] Reading file: ${filePath}`);

    const fullPath = resolve(filePath);
    if (!existsSync(fullPath)) {
      console.warn(`[JSON] File not found: ${filePath}`);
      return { success: false, error: "File not found", file_path: filePath };
    }

    try {
      const content = readFileSync(fullPath, "utf-8");
      const data = JSON.parse(content);
      console.log("[JSON] Successfully parsed JSON");
      return {
        success: true,
        file_path: filePath,
        file_type: "json",
        data,
        keys: typeof data === "object" && !Array.isArray(data) ? Object.keys(data) : null,
      };
    } catch (e) {
      console.error(`[JSON] Error reading file: ${e}`);
      return { success: false, error: String(e), file_path: filePath };
    }
  },
);

const readTextFile = task(
  { name: "readTextFile", retry },
  function readTextFile(filePath: string) {
    console.log(`[TEXT] Reading file: ${filePath}`);

    const fullPath = resolve(filePath);
    if (!existsSync(fullPath)) {
      console.warn(`[TEXT] File not found: ${filePath}`);
      return { success: false, error: "File not found", file_path: filePath };
    }

    try {
      const content = readFileSync(fullPath, "utf-8");
      const lines = content.split("\n");
      const words = content.split(/\s+/).filter(Boolean);

      console.log(`[TEXT] Successfully read ${lines.length} lines`);
      return {
        success: true,
        file_path: filePath,
        file_type: "text",
        content,
        line_count: lines.length,
        word_count: words.length,
        char_count: content.length,
      };
    } catch (e) {
      console.error(`[TEXT] Error reading file: ${e}`);
      return { success: false, error: String(e), file_path: filePath };
    }
  },
);

// ---- Analysis Tasks ----

const analyzeCsvData = task(
  { name: "analyzeCsvData", retry },
  function analyzeCsvData(csvResult: { success?: boolean; data?: { [key: string]: string }[] }) {
    console.log("[ANALYSIS] Analyzing CSV data");

    if (!csvResult.success) return { success: false, error: "No data to analyze" };

    const rows = csvResult.data ?? [];
    if (rows.length === 0) return { success: false, error: "Empty dataset" };

    let totalQuantity = 0;
    let totalRevenue = 0;
    const products = new Set<string>();
    const regions = new Set<string>();

    for (const row of rows) {
      const quantity = parseInt(row.quantity ?? "0", 10) || 0;
      const price = parseFloat(row.price ?? "0") || 0;
      totalQuantity += quantity;
      totalRevenue += quantity * price;
      if (row.product) products.add(row.product);
      if (row.region) regions.add(row.region);
    }

    const analysis = {
      success: true,
      total_records: rows.length,
      total_quantity: totalQuantity,
      total_revenue: Math.round(totalRevenue * 100) / 100,
      unique_products: products.size,
      unique_regions: regions.size,
      products: [...products],
      regions: [...regions],
    };

    console.log(`[ANALYSIS] Total revenue: $${analysis.total_revenue}`);
    return analysis;
  },
);

const analyzeJsonStructure = task(
  { name: "analyzeJsonStructure", retry },
  function analyzeJsonStructure(jsonResult: { success?: boolean; data?: unknown }) {
    console.log("[ANALYSIS] Analyzing JSON structure");

    if (!jsonResult.success) return { success: false, error: "No data to analyze" };

    const data = jsonResult.data ?? {};

    function countKeys(obj: unknown): number {
      if (typeof obj === "object" && obj !== null && !Array.isArray(obj)) {
        let count = Object.keys(obj).length;
        for (const value of Object.values(obj)) count += countKeys(value);
        return count;
      }
      if (Array.isArray(obj)) return obj.reduce((sum, item) => sum + countKeys(item), 0);
      return 0;
    }

    const isDict = typeof data === "object" && !Array.isArray(data) && data !== null;
    return {
      success: true,
      type: Array.isArray(data) ? "array" : typeof data,
      top_level_keys: isDict ? Object.keys(data as object) : null,
      total_keys: countKeys(data),
      is_nested: isDict
        ? Object.values(data as object).some((v) => typeof v === "object" && v !== null)
        : false,
    };
  },
);

const analyzeTextContent = task(
  { name: "analyzeTextContent", retry },
  function analyzeTextContent(textResult: {
    success?: boolean;
    content?: string;
  }) {
    console.log("[ANALYSIS] Analyzing text content");

    if (!textResult.success) return { success: false, error: "No data to analyze" };

    const content = textResult.content ?? "";
    const lines = content.split("\n");
    const words = content.split(/\s+/).filter(Boolean);

    const sections = lines.filter(
      (l) => l.trim() && (/^[A-Z]/.test(l.trim()) || l.trim().startsWith("-")),
    );

    const longWords = words.filter((w) => w.length > 6).map((w) => w.replace(/[.,!?]/g, "").toLowerCase());
    const freq: { [key: string]: number } = {};
    for (const word of longWords) freq[word] = (freq[word] ?? 0) + 1;

    const topKeywords = Object.entries(freq)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);

    return {
      success: true,
      total_lines: lines.length,
      total_words: words.length,
      total_chars: content.length,
      section_count: sections.length,
      top_keywords: Object.fromEntries(topKeywords),
      avg_line_length: lines.length > 0 ? content.length / lines.length : 0,
    };
  },
);

// ---- Orchestration Tasks ----

const processSingleFile = task(
  { name: "processSingleFile", retry },
  async function processSingleFile(filePath: string) {
    console.log(`[PROCESS] Processing file: ${filePath}`);

    const extension = extname(filePath).toLowerCase();
    let readResult: { success?: boolean; [key: string]: unknown };
    let analysis: { [key: string]: unknown } = {};

    if (extension === ".csv") {
      readResult = await readCsvFile(filePath);
      if (readResult.success) analysis = await analyzeCsvData(readResult as Parameters<typeof analyzeCsvData>[0]);
    } else if (extension === ".json") {
      readResult = await readJsonFile(filePath);
      if (readResult.success) analysis = await analyzeJsonStructure(readResult as Parameters<typeof analyzeJsonStructure>[0]);
    } else if (extension === ".txt") {
      readResult = await readTextFile(filePath);
      if (readResult.success) analysis = await analyzeTextContent(readResult as Parameters<typeof analyzeTextContent>[0]);
    } else {
      console.warn(`[PROCESS] Unsupported file type: ${extension}`);
      return { success: false, file_path: filePath, error: `Unsupported file type: ${extension}` };
    }

    console.log(`[PROCESS] File processed: ${filePath}`);
    return {
      success: readResult.success ?? false,
      file_path: filePath,
      file_type: extension.slice(1),
      read_result: readResult,
      analysis,
    };
  },
);

// Root task: processes multiple files in parallel
task(
  { name: "processFileBatch", retry, timeoutSeconds: 300 },
  async function processFileBatch(...filePaths: string[]) {
    console.log("=".repeat(80));
    console.log(`[BATCH] Starting batch processing of ${filePaths.length} files`);
    console.log("=".repeat(80));

    const results = await Promise.all(filePaths.map((fp) => processSingleFile(fp)));

    const successful = results.filter((r) => r.success);
    const failed = results.filter((r) => !r.success);

    const fileTypes: { [key: string]: number } = {};
    for (const result of successful) {
      const ft = (result.file_type as string) ?? "unknown";
      fileTypes[ft] = (fileTypes[ft] ?? 0) + 1;
    }

    const batchResult = {
      total_files: filePaths.length,
      successful: successful.length,
      failed: failed.length,
      success_rate: filePaths.length > 0 ? successful.length / filePaths.length : 0,
      file_types: fileTypes,
      results,
      processed_at: new Date().toISOString(),
    };

    console.log("=".repeat(80));
    console.log("[BATCH] Batch processing complete!");
    console.log(`[BATCH] Successful: ${successful.length}/${filePaths.length}`);
    console.log("=".repeat(80));

    return batchResult;
  },
);

// Root task: generate a consolidated report from batch results
task(
  { name: "generateConsolidatedReport", retry },
  async function generateConsolidatedReport(batchResult: {
    total_files?: number;
    successful?: number;
    failed?: number;
    success_rate?: number;
    file_types?: { [key: string]: number };
    results?: { success?: boolean; file_type?: string; analysis?: { total_records?: number; total_words?: number; total_keys?: number } }[];
  }) {
    console.log("[REPORT] Generating consolidated report");

    const results = batchResult.results ?? [];
    const successfulResults = results.filter((r) => r.success);

    let totalCsvRows = 0;
    let totalTextWords = 0;
    let totalJsonKeys = 0;

    for (const result of successfulResults) {
      const analysis = result.analysis ?? {};
      if (result.file_type === "csv") totalCsvRows += analysis.total_records ?? 0;
      else if (result.file_type === "text") totalTextWords += analysis.total_words ?? 0;
      else if (result.file_type === "json") totalJsonKeys += analysis.total_keys ?? 0;
    }

    const report = {
      title: "File Processing Report",
      generated_at: new Date().toISOString(),
      summary: {
        total_files_processed: batchResult.total_files,
        successful: batchResult.successful,
        failed: batchResult.failed,
        success_rate_pct: Math.round((batchResult.success_rate ?? 0) * 1000) / 10,
      },
      data_summary: {
        total_csv_rows: totalCsvRows,
        total_text_words: totalTextWords,
        total_json_keys: totalJsonKeys,
      },
      file_breakdown: batchResult.file_types ?? {},
      detailed_results: successfulResults,
    };

    console.log("[REPORT] Report generated successfully");
    return report;
  },
);
