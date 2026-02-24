import "dotenv/config";
import { task } from "@renderinc/sdk/workflows";

interface ParsedData {
  success: boolean;
  rows: { [key: string]: string }[];
  columns: string[];
  row_count: number;
  error?: string;
  parsed_at?: string;
}

const retry = {
  maxRetries: 2,
  waitDurationMs: 1000,
  factor: 1.5,
};

// Subtask: parse CSV content into structured data
const parseCsvData = task(
  { name: "parseCsvData", retry },
  function parseCsvData(fileContent: string): ParsedData {
    console.log("[PARSE] Starting CSV parsing");

    try {
      const lines = fileContent.trim().split("\n");
      if (lines.length < 2) {
        console.warn("[PARSE] No data rows found in CSV");
        return { success: false, error: "No data rows found", rows: [], columns: [], row_count: 0 };
      }

      const headers = lines[0].split(",").map((h) => h.trim());
      const rows = lines.slice(1).map((line) => {
        const values = line.split(",").map((v) => v.trim());
        const row: { [key: string]: string } = {};
        headers.forEach((h, i) => { row[h] = values[i]; });
        return row;
      });

      console.log(`[PARSE] Successfully parsed ${rows.length} rows with ${headers.length} columns`);

      return {
        success: true,
        rows,
        columns: headers,
        row_count: rows.length,
        parsed_at: new Date().toISOString(),
      };
    } catch (e) {
      console.error(`[PARSE] Error parsing CSV: ${e}`);
      return { success: false, error: String(e), rows: [], columns: [], row_count: 0 };
    }
  },
);

// Subtask: calculate statistics from parsed data
const calculateStatistics = task(
  { name: "calculateStatistics", retry },
  function calculateStatistics(data: ParsedData) {
    console.log("[STATS] Calculating statistics");

    if (!data.success || data.rows.length === 0) {
      console.warn("[STATS] No data to analyze");
      return { success: false, error: "No data available for statistics" };
    }

    const { rows, columns } = data;

    const numericColumns: string[] = [];
    const numericData: { [key: string]: number[] } = {};

    for (const col of columns) {
      const values: number[] = [];
      for (const row of rows) {
        const val = row[col]?.trim();
        if (val) {
          const num = parseFloat(val);
          if (!Number.isNaN(num)) values.push(num);
        }
      }
      if (values.length > 0) {
        numericColumns.push(col);
        numericData[col] = values;
      }
    }

    const statistics: { [col: string]: { min: number; max: number; avg: number; sum: number; count: number } } = {};
    for (const col of numericColumns) {
      const values = numericData[col];
      statistics[col] = {
        min: Math.min(...values),
        max: Math.max(...values),
        avg: values.reduce((a, b) => a + b, 0) / values.length,
        sum: values.reduce((a, b) => a + b, 0),
        count: values.length,
      };
    }

    console.log(`[STATS] Calculated statistics for ${numericColumns.length} numeric columns`);

    return {
      success: true,
      numeric_columns: numericColumns,
      statistics,
      total_rows: rows.length,
      calculated_at: new Date().toISOString(),
    };
  },
);

// Subtask: identify trends and patterns
const identifyTrends = task(
  { name: "identifyTrends", retry },
  function identifyTrends(data: ParsedData) {
    console.log("[TRENDS] Identifying trends");

    if (!data.success || data.rows.length === 0) {
      console.warn("[TRENDS] No data to analyze");
      return { success: false, error: "No data available for trend analysis" };
    }

    const { rows, columns } = data;

    const categoricalAnalysis: {
      [col: string]: {
        unique_count: number;
        total_count: number;
        top_5: [string, number][];
        distribution: { [key: string]: number };
      };
    } = {};

    for (const col of columns) {
      const values = rows.map((r) => r[col]).filter(Boolean);
      const uniqueValues = new Set(values);

      if (uniqueValues.size < rows.length / 2) {
        const counts: { [key: string]: number } = {};
        for (const val of values) counts[val] = (counts[val] ?? 0) + 1;

        const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);

        categoricalAnalysis[col] = {
          unique_count: uniqueValues.size,
          total_count: values.length,
          top_5: sorted.slice(0, 5),
          distribution: Object.fromEntries(sorted),
        };
      }
    }

    console.log(`[TRENDS] Analyzed ${Object.keys(categoricalAnalysis).length} categorical columns`);

    return {
      success: true,
      categorical_columns: Object.keys(categoricalAnalysis),
      categorical_analysis: categoricalAnalysis,
      analyzed_at: new Date().toISOString(),
    };
  },
);

// Subtask: generate insights report
const generateInsights = task(
  { name: "generateInsights", retry },
  async function generateInsights(
    stats: { success?: boolean; numeric_columns?: string[]; statistics?: { [col: string]: { avg: number; min: number; max: number; sum: number } } },
    trends: { success?: boolean; categorical_columns?: string[]; categorical_analysis?: { [col: string]: { top_5: [string, number][]; distribution: { [key: string]: number } } } },
    metadata: ParsedData,
  ) {
    console.log("[INSIGHTS] Generating insights report");

    const keyFindings: { type: string; column: string; finding: string }[] = [];

    if (stats.success && stats.statistics) {
      for (const [col, stat] of Object.entries(stats.statistics)) {
        keyFindings.push({
          type: "numeric",
          column: col,
          finding: `${col}: avg=${stat.avg.toFixed(2)}, min=${stat.min.toFixed(2)}, max=${stat.max.toFixed(2)}, sum=${stat.sum.toFixed(2)}`,
        });
      }
    }

    if (trends.success && trends.categorical_analysis) {
      for (const [col, analysis] of Object.entries(trends.categorical_analysis)) {
        const topValue = analysis.top_5[0];
        if (topValue) {
          keyFindings.push({
            type: "categorical",
            column: col,
            finding: `${col}: Most common value is '${topValue[0]}' (${topValue[1]} occurrences, ${Object.keys(analysis.distribution).length} unique values)`,
          });
        }
      }
    }

    console.log(`[INSIGHTS] Generated ${keyFindings.length} key findings`);

    return {
      success: true,
      summary: {
        total_rows: metadata.row_count,
        total_columns: metadata.columns.length,
        numeric_columns_count: stats.numeric_columns?.length ?? 0,
        categorical_columns_count: trends.categorical_columns?.length ?? 0,
      },
      key_findings: keyFindings,
      generated_at: new Date().toISOString(),
    };
  },
);

// Root task: orchestrates the full analysis pipeline
task(
  { name: "analyzeFile", retry, timeoutSeconds: 300 },
  async function analyzeFile(fileContent: string) {
    console.log("[ANALYZE_FILE] Starting file analysis pipeline");

    console.log("[ANALYZE_FILE] Stage 1: Parsing CSV data");
    const parsedData = await parseCsvData(fileContent);

    if (!parsedData.success) {
      console.error("[ANALYZE_FILE] Failed to parse CSV data");
      return { success: false, error: "Failed to parse CSV data", details: parsedData.error };
    }

    console.log(`[ANALYZE_FILE] Parsed ${parsedData.row_count} rows`);

    console.log("[ANALYZE_FILE] Stage 2: Calculating statistics");
    const stats = await calculateStatistics(parsedData);

    console.log("[ANALYZE_FILE] Stage 3: Identifying trends");
    const trends = await identifyTrends(parsedData);

    console.log("[ANALYZE_FILE] Stage 4: Generating insights");
    const insights = await generateInsights(stats, trends, parsedData);

    console.log("[ANALYZE_FILE] Analysis pipeline completed successfully");

    return {
      success: true,
      file_metadata: { row_count: parsedData.row_count, columns: parsedData.columns },
      statistics: stats,
      trends,
      insights,
      completed_at: new Date().toISOString(),
    };
  },
);
