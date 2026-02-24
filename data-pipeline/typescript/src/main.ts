import "dotenv/config";
import { task } from "@renderinc/sdk/workflows";

interface User {
  id: string;
  name: string;
  email: string;
  plan: string;
}

interface Transaction {
  id: string;
  user_id: string;
  amount: number;
  type: string;
  date: string;
}

interface Engagement {
  user_id: string;
  page_views: number;
  sessions: number;
  last_active: string;
  feature_usage: { search: number; export: number; share: number };
}

interface EnrichedUser {
  user_id: string;
  name: string;
  email: string;
  plan: string;
  transaction_count: number;
  total_spent: number;
  total_refunded: number;
  net_revenue: number;
  engagement_score: number;
  segment: string;
  page_views: number;
  sessions: number;
  geo: { country: string; timezone: string; language: string };
}

const retry = {
  maxRetries: 3,
  waitDurationMs: 2000,
  factor: 1.5,
};

function simpleHash(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash * 31 + str.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}

// ---- Data Source Tasks ----

const fetchUserData = task(
  { name: "fetchUserData", retry },
  async function fetchUserData(userIds: string[]) {
    console.log(`[SOURCE] Fetching user data for ${userIds.length} users`);

    const mockUsers: { [key: string]: User } = {
      user_1: { id: "user_1", name: "Alice Johnson", email: "alice@example.com", plan: "premium" },
      user_2: { id: "user_2", name: "Bob Smith", email: "bob@example.com", plan: "basic" },
      user_3: { id: "user_3", name: "Charlie Brown", email: "charlie@example.com", plan: "premium" },
      user_4: { id: "user_4", name: "Diana Prince", email: "diana@example.com", plan: "basic" },
    };

    const users = userIds.map(
      (uid) => mockUsers[uid] ?? { id: uid, name: "Unknown", email: `${uid}@example.com`, plan: "none" },
    );

    console.log(`[SOURCE] Fetched ${users.length} user records`);
    return { success: true, source: "user_service", count: users.length, data: users };
  },
);

const fetchTransactionData = task(
  { name: "fetchTransactionData", retry },
  async function fetchTransactionData(userIds: string[], days: number = 30) {
    console.log(`[SOURCE] Fetching transactions for ${userIds.length} users (${days} days)`);

    const transactions: Transaction[] = [];
    const types = ["purchase", "refund", "subscription"];

    for (const userId of userIds) {
      const numTxns = (simpleHash(userId) % 10) + 1;
      for (let i = 0; i < numTxns; i++) {
        const key = `${userId}_${i}`;
        const daysAgo = simpleHash(key) % days;
        const date = new Date(Date.now() - daysAgo * 86400000);
        transactions.push({
          id: `txn_${key}`,
          user_id: userId,
          amount: (simpleHash(key) % 10000) / 100,
          type: types[simpleHash(key) % 3],
          date: date.toISOString(),
        });
      }
    }

    console.log(`[SOURCE] Fetched ${transactions.length} transactions`);
    return { success: true, source: "transaction_service", count: transactions.length, data: transactions };
  },
);

const fetchEngagementData = task(
  { name: "fetchEngagementData", retry },
  async function fetchEngagementData(userIds: string[]) {
    console.log(`[SOURCE] Fetching engagement data for ${userIds.length} users`);

    const engagement: Engagement[] = userIds.map((userId) => {
      const daysAgo = simpleHash(userId) % 30;
      return {
        user_id: userId,
        page_views: simpleHash(`pv_${userId}`) % 1000,
        sessions: simpleHash(`sess_${userId}`) % 100,
        last_active: new Date(Date.now() - daysAgo * 86400000).toISOString(),
        feature_usage: {
          search: simpleHash(`search_${userId}`) % 50,
          export: simpleHash(`export_${userId}`) % 20,
          share: simpleHash(`share_${userId}`) % 30,
        },
      };
    });

    console.log(`[SOURCE] Fetched engagement for ${engagement.length} users`);
    return { success: true, source: "analytics_service", count: engagement.length, data: engagement };
  },
);

// ---- Enrichment Tasks ----

const enrichWithGeoData = task(
  { name: "enrichWithGeoData", retry },
  async function enrichWithGeoData(userEmail: string) {
    console.log(`[ENRICH] Enriching geo data for ${userEmail}`);
    const idx = simpleHash(userEmail) % 4;
    return {
      country: ["USA", "Canada", "UK", "Germany"][idx],
      timezone: ["America/New_York", "America/Toronto", "Europe/London", "Europe/Berlin"][idx],
      language: ["en-US", "en-CA", "en-GB", "de-DE"][idx],
    };
  },
);

const calculateUserMetrics = task(
  { name: "calculateUserMetrics", retry },
  async function calculateUserMetrics(
    user: User,
    transactions: Transaction[],
    engagement: Engagement,
  ) {
    console.log(`[METRICS] Calculating metrics for user ${user.id}`);

    const userTxns = transactions.filter((t) => t.user_id === user.id);
    const totalSpent = userTxns.filter((t) => t.type === "purchase").reduce((s, t) => s + t.amount, 0);
    const totalRefunded = userTxns.filter((t) => t.type === "refund").reduce((s, t) => s + t.amount, 0);
    const netRevenue = totalSpent - totalRefunded;

    const featureSum = Object.values(engagement.feature_usage ?? {}).reduce((s, v) => s + v, 0);
    const engagementScore = Math.min(
      100,
      (engagement.page_views ?? 0) / 10 + (engagement.sessions ?? 0) / 2 + featureSum,
    );

    let segment: string;
    if (user.plan === "premium" && netRevenue > 100) segment = "high_value";
    else if (user.plan === "premium") segment = "premium";
    else if (engagementScore > 50) segment = "engaged";
    else segment = "standard";

    const metrics = {
      user_id: user.id,
      name: user.name,
      email: user.email,
      plan: user.plan,
      transaction_count: userTxns.length,
      total_spent: Math.round(totalSpent * 100) / 100,
      total_refunded: Math.round(totalRefunded * 100) / 100,
      net_revenue: Math.round(netRevenue * 100) / 100,
      engagement_score: Math.round(engagementScore * 100) / 100,
      segment,
      page_views: engagement.page_views ?? 0,
      sessions: engagement.sessions ?? 0,
    };

    console.log(`[METRICS] User ${user.id} - Segment: ${segment}, Revenue: $${netRevenue.toFixed(2)}`);
    return metrics;
  },
);

// ---- Transformation Tasks ----

const transformUserData = task(
  { name: "transformUserData", retry },
  async function transformUserData(
    userData: { data: User[] },
    transactionData: { data: Transaction[] },
    engagementData: { data: Engagement[] },
  ) {
    console.log("[TRANSFORM] Combining data from multiple sources");

    const users = userData.data ?? [];
    const transactions = transactionData.data ?? [];
    const engagementList = engagementData.data ?? [];

    const engagementMap = new Map(engagementList.map((e) => [e.user_id, e]));

    console.log(`[TRANSFORM] Processing ${users.length} users with enrichment`);

    const enrichedUsers = [];
    for (const user of users) {
      const userEngagement = engagementMap.get(user.id) ?? ({} as Engagement);
      const userMetrics = await calculateUserMetrics(user, transactions, userEngagement);
      const geoData = await enrichWithGeoData(user.email);
      enrichedUsers.push({ ...userMetrics, geo: geoData });
    }

    console.log(`[TRANSFORM] Enriched ${enrichedUsers.length} user profiles`);
    return { success: true, count: enrichedUsers.length, data: enrichedUsers };
  },
);

// ---- Aggregation Tasks ----

const aggregateInsights = task(
  { name: "aggregateInsights", retry },
  function aggregateInsights(enrichedData: { data: EnrichedUser[] }) {
    console.log("[AGGREGATE] Generating insights from enriched data");

    const users = enrichedData.data ?? [];
    if (users.length === 0) return { success: false, error: "No data to aggregate" };

    const segments: { [key: string]: number } = {};
    const countries: { [key: string]: number } = {};

    for (const user of users) {
      segments[user.segment] = (segments[user.segment] ?? 0) + 1;
      const country = user.geo?.country ?? "Unknown";
      countries[country] = (countries[country] ?? 0) + 1;
    }

    const totalRevenue = users.reduce((s, u) => s + u.net_revenue, 0);
    const avgRevenue = users.length > 0 ? totalRevenue / users.length : 0;
    const avgEngagement = users.length > 0
      ? users.reduce((s, u) => s + u.engagement_score, 0) / users.length
      : 0;

    const topUsers = [...users]
      .sort((a, b) => b.net_revenue - a.net_revenue)
      .slice(0, 5)
      .map((u) => ({ name: u.name, revenue: u.net_revenue, segment: u.segment }));

    const insights = {
      total_users: users.length,
      segment_distribution: segments,
      revenue: {
        total: Math.round(totalRevenue * 100) / 100,
        average_per_user: Math.round(avgRevenue * 100) / 100,
        top_users: topUsers,
      },
      engagement: {
        average_score: Math.round(avgEngagement * 100) / 100,
        total_page_views: users.reduce((s, u) => s + u.page_views, 0),
        total_sessions: users.reduce((s, u) => s + u.sessions, 0),
      },
      geographic_distribution: countries,
      generated_at: new Date().toISOString(),
    };

    console.log(`[AGGREGATE] Insights generated: ${users.length} users, $${totalRevenue.toFixed(2)} revenue`);
    return insights;
  },
);

// Root task: full pipeline orchestrator
task(
  { name: "runDataPipeline", retry, timeoutSeconds: 300 },
  async function runDataPipeline(userIds: string[]) {
    console.log("=".repeat(80));
    console.log("[PIPELINE] Starting Data Pipeline");
    console.log(`[PIPELINE] Processing ${userIds.length} users`);
    console.log("=".repeat(80));

    try {
      // Stage 1: Parallel extraction
      console.log("[PIPELINE] Stage 1/3: EXTRACT (parallel)");
      const [userData, transactionData, engagementData] = await Promise.all([
        fetchUserData(userIds),
        fetchTransactionData(userIds),
        fetchEngagementData(userIds),
      ]);

      console.log(
        `[PIPELINE] Extracted: ${userData.count} users, ${transactionData.count} transactions, ${engagementData.count} engagement records`,
      );

      // Stage 2: Transform
      console.log("[PIPELINE] Stage 2/3: TRANSFORM");
      const enrichedData = await transformUserData(userData, transactionData, engagementData);
      console.log(`[PIPELINE] Enriched ${enrichedData.count} user profiles`);

      // Stage 3: Aggregate
      console.log("[PIPELINE] Stage 3/3: AGGREGATE");
      const insights = await aggregateInsights(enrichedData as { data: EnrichedUser[] });

      const pipelineResult = {
        status: "success",
        user_count: userIds.length,
        stages: {
          extract: { users: userData.count, transactions: transactionData.count, engagement: engagementData.count },
          transform: { enriched_users: enrichedData.count },
          aggregate: { insights },
        },
        insights,
        completed_at: new Date().toISOString(),
      };

      console.log("=".repeat(80));
      console.log("[PIPELINE] Data Pipeline Complete!");
      console.log("=".repeat(80));

      return pipelineResult;
    } catch (error) {
      console.error(`[PIPELINE] Pipeline failed: ${error}`);
      return { status: "failed", error: String(error), failed_at: new Date().toISOString() };
    }
  },
);
