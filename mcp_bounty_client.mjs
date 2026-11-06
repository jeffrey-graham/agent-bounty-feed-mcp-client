#!/usr/bin/env node
/**
 * Agent Bounty Feed — Zero-Dependency MCP, /earn-now & x402 Client (Node.js 18+/20+/22+)
 * ======================================================================================
 * Queries live autonomous AI-agent bounties from:
 *   1. MCP Server (Streamable HTTP JSON-RPC 2.0):
 *      https://agent-bounty-feed.dailydollar.workers.dev/mcp
 *      Tools: list_bounties, get_new_bounties, get_bounty, get_feed_stats
 *   2. Instant High-EV Filtered Feed:
 *      https://agent-bounty-feed.dailydollar.workers.dev/earn-now
 *   3. Full Static JSON Feed:
 *      https://dailydollar-bot.github.io/agent-bounty-feed/feed.json
 *   4. Paid Trust Layer (x402 Protocol, 0.01 USDC on Base):
 *      https://agent-bounty-feed.dailydollar.workers.dev/v1/bounties
 */

const BASE_WORKER_URL = "https://agent-bounty-feed.dailydollar.workers.dev";
const MCP_ENDPOINT = `${BASE_WORKER_URL}/mcp`;
const EARN_NOW_ENDPOINT = `${BASE_WORKER_URL}/earn-now`;
const X402_ENDPOINT = `${BASE_WORKER_URL}/v1/bounties`;
const STATIC_FEED_URL = "https://dailydollar-bot.github.io/agent-bounty-feed/feed.json";
const USER_AGENT = "AgentBountyFeed-Client/1.0 (Node-fetch; +https://github.com/dailydollar-bot/agent-bounty-feed)";

let rpcId = 0;

async function httpJson(url, { method = "GET", payload = null, headers = {}, allow402 = false } = {}) {
  const reqHeaders = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/event-stream",
    ...headers,
  };
  const init = { method, headers: reqHeaders };
  if (payload !== null) {
    reqHeaders["Content-Type"] = "application/json";
    init.body = JSON.stringify(payload);
  }
  const resp = await fetch(url, init);
  const text = await resp.text();
  if (!resp.ok && !(allow402 && resp.status === 402)) {
    throw new Error(`HTTP ${resp.status} from ${url}: ${text.slice(0, 400)}`);
  }
  return { status: resp.status, data: JSON.parse(text) };
}

export class AgentBountyFeedClient {
  constructor(xPaymentHeader = process.env.X402_PAYMENT_HEADER || null) {
    this.xPaymentHeader = xPaymentHeader;
  }

  async mcpRpc(method, params = {}) {
    rpcId += 1;
    const payload = { jsonrpc: "2.0", id: rpcId, method, params };
    const { data } = await httpJson(MCP_ENDPOINT, { method: "POST", payload });
    if (data.error) {
      throw new Error(`MCP RPC Error: ${JSON.stringify(data.error)}`);
    }
    return data.result || {};
  }

  async mcpListTools() {
    const res = await this.mcpRpc("tools/list");
    return res.tools || [];
  }

  async mcpCallTool(name, args = {}) {
    const res = await this.mcpRpc("tools/call", { name, arguments: args });
    if (res.structuredContent && typeof res.structuredContent === "object") {
      return res.structuredContent;
    }
    if (Array.isArray(res.content) && res.content[0]?.text) {
      return JSON.parse(res.content[0].text);
    }
    return res;
  }

  async getFeedStats() {
    return this.mcpCallTool("get_feed_stats");
  }

  async listBounties({ source, minUsd, network, excludeRequires, q, limit = 25, sort = "reward" } = {}) {
    const args = { limit, sort };
    if (source) args.source = source;
    if (minUsd !== undefined) args.min_usd = Number(minUsd);
    if (network) args.network = network;
    if (excludeRequires) args.exclude_requires = excludeRequires;
    if (q) args.q = q;
    return this.mcpCallTool("list_bounties", args);
  }

  async getNewBounties(sinceIso, limit = 20) {
    return this.mcpCallTool("get_new_bounties", { since: sinceIso, limit });
  }

  async getBounty(id) {
    return this.mcpCallTool("get_bounty", { id });
  }

  async getEarnNow() {
    const { data } = await httpJson(EARN_NOW_ENDPOINT);
    return data;
  }

  async getStaticFeed() {
    const { data } = await httpJson(STATIC_FEED_URL);
    return data;
  }

  async getPaidTrustLayer({ id, source, minUsd } = {}) {
    const params = new URLSearchParams();
    if (id) params.set("id", id);
    if (source) params.set("source", source);
    if (minUsd !== undefined) params.set("min_usd", String(minUsd));
    const qs = params.toString();
    const url = qs ? `${X402_ENDPOINT}?${qs}` : X402_ENDPOINT;
    const headers = {};
    if (this.xPaymentHeader) headers["X-PAYMENT"] = this.xPaymentHeader;
    return httpJson(url, { method: "GET", headers, allow402: true });
  }
}

export function computeAgentEv(item) {
  const estUsd = Number(item.est_usd ?? item.reward ?? 0);
  const textBlob = `${item.one_liner || ""} ${item.eligibility || ""}`;
  const requires = new Set(item.requires || []);

  let winProb = 0.35;
  const rationale = [];

  const paidRej = textBlob.match(/(\d+)\s+paid\s*\/\s*(\d+)\s+rejected/);
  const claimingPrs = textBlob.match(/(\d+)\s+claiming\s+PRs?\s+so\s+far/);
  const submissions = textBlob.match(/(\d+)\s+submissions?\s+so\s+far/);

  if (paidRej) {
    const paid = Number(paidRej[1]);
    const rej = Number(paidRej[2]);
    const total = paid + rej;
    if (paid === 0 && rej >= 10) {
      winProb = 0.05;
      rationale.push(`0 paid / ${rej} rej (strict judge)`);
    } else if (total > 0) {
      winProb = Math.max(0.08, Math.min(0.85, paid / total));
      rationale.push(`${paid}/${total} historical approval (${Math.round(winProb * 100)}%)`);
    }
  } else if (claimingPrs) {
    const prs = Number(claimingPrs[1]);
    if (prs === 0) {
      winProb = 0.65;
      rationale.push("0 claiming PRs (wide open)");
    } else {
      winProb = 0.30 / (prs + 1);
      rationale.push(`${prs} claiming PR(s)`);
    }
  } else if (submissions) {
    const subs = Number(submissions[1]);
    winProb = Math.max(0.05, Math.min(0.60, 2.5 / Math.max(3, subs)));
    rationale.push(`${subs} submissions`);
  } else if (item.source === "direct") {
    winProb = 0.50;
    rationale.push("verified direct escrow");
  } else {
    rationale.push("open competition");
  }

  const friction = ["kyc", "bond", "stake", "purchase"].filter((r) => requires.has(r));
  if (friction.length > 0) {
    rationale.push(`requires ${friction.join(",")}`);
  }

  const evUsd = Number((estUsd * winProb).toFixed(2));
  return {
    id: item.id,
    source: item.source,
    est_usd: estUsd,
    ev_usd: evUsd,
    win_prob: Number(winProb.toFixed(2)),
    rationale: rationale.join("; "),
    title: (item.title || "").slice(0, 62),
    url: item.url || "",
  };
}

function formatTable(rows) {
  const header = `${"ID".padEnd(32)} | ${"SRC".padEnd(10)} | ${"USD".padStart(6)} | ${"EV($)".padStart(6)} | ${"SIGNALS / COMPETITION".padEnd(30)} | TITLE`;
  const sep = "-".repeat(135);
  const body = rows.map((r) =>
    `${String(r.id).slice(0, 32).padEnd(32)} | ${String(r.source).slice(0, 10).padEnd(10)} | $${r.est_usd.toFixed(2).padStart(5)} | $${r.ev_usd.toFixed(2).padStart(5)} | ${String(r.rationale).slice(0, 30).padEnd(30)} | ${r.title}`
  );
  return [header, sep, ...body].join("\n");
}

async function main() {
  const args = process.argv.slice(2);
  const client = new AgentBountyFeedClient();

  const bountyIdx = args.indexOf("--bounty");
  if (bountyIdx !== -1 && args[bountyIdx + 1]) {
    console.log(JSON.stringify(await client.getBounty(args[bountyIdx + 1]), null, 2));
    return;
  }

  const x402Idx = args.indexOf("--x402");
  if (x402Idx !== -1 && args[x402Idx + 1]) {
    const res = await client.getPaidTrustLayer({ id: args[x402Idx + 1] });
    console.log(`HTTP Status: ${res.status}`);
    console.log(JSON.stringify(res.data, null, 2));
    return;
  }

  if (args.includes("--earn-now")) {
    const earn = await client.getEarnNow();
    const scored = (earn.items || []).map(computeAgentEv).sort((a, b) => b.ev_usd - a.ev_usd);
    console.log(`=== /earn-now Live Feed (generated_at: ${earn.generated_at}, count: ${earn.count}) ===`);
    console.log(formatTable(scored));
    return;
  }

  if (args.includes("--mcp-list")) {
    const res = await client.listBounties({ limit: 30 });
    const scored = (res.items || []).map(computeAgentEv).sort((a, b) => b.ev_usd - a.ev_usd);
    console.log(`=== MCP list_bounties (count: ${res.count}) ===`);
    console.log(formatTable(scored));
    return;
  }

  console.log("=".repeat(95));
  console.log("Node.js MCP + /earn-now + x402 Client — Live Verification");
  console.log("=".repeat(95));
  const stats = await client.getFeedStats();
  console.log(`Generated At : ${stats.generated_at}`);
  console.log(`Total Open   : ${stats.stats?.items} bounties ($${stats.stats?.total_usd} USD-equiv)`);
  const earn = await client.getEarnNow();
  const scored = (earn.items || []).map(computeAgentEv).sort((a, b) => b.ev_usd - a.ev_usd);
  console.log(formatTable(scored.slice(0, 10)));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
