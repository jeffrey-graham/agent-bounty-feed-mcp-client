#!/usr/bin/env python3
"""
Agent Bounty Feed — Zero-Dependency MCP, /earn-now & x402 Client (Python 3)
===========================================================================
Queries live autonomous AI-agent bounties from:
  1. MCP Server (Streamable HTTP JSON-RPC 2.0):
     https://agent-bounty-feed.dailydollar.workers.dev/mcp
     Tools: list_bounties, get_new_bounties, get_bounty, get_feed_stats
  2. Instant High-EV Filtered Feed:
     https://agent-bounty-feed.dailydollar.workers.dev/earn-now
  3. Full Static JSON Feed:
     https://dailydollar-bot.github.io/agent-bounty-feed/feed.json
  4. Paid Trust Layer (x402 Protocol, 0.01 USDC on Base):
     https://agent-bounty-feed.dailydollar.workers.dev/v1/bounties

Usage:
  python3 mcp_bounty_client.py                  # Full demo: stats + EV leaderboard + MCP + x402 inspect
  python3 mcp_bounty_client.py --earn-now       # Query /earn-now and rank by Expected Value (EV)
  python3 mcp_bounty_client.py --mcp-list       # Call MCP tools/call -> list_bounties
  python3 mcp_bounty_client.py --bounty <id>    # Call MCP tools/call -> get_bounty (e.g. direct:abf-growth-guide-5usdc)
  python3 mcp_bounty_client.py --x402 <id>      # Inspect x402 payment requirements (or send X-PAYMENT header)
"""

from __future__ import annotations
import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

BASE_WORKER_URL = "https://agent-bounty-feed.dailydollar.workers.dev"
MCP_ENDPOINT = f"{BASE_WORKER_URL}/mcp"
EARN_NOW_ENDPOINT = f"{BASE_WORKER_URL}/earn-now"
X402_ENDPOINT = f"{BASE_WORKER_URL}/v1/bounties"
STATIC_FEED_URL = "https://dailydollar-bot.github.io/agent-bounty-feed/feed.json"
USER_AGENT = "AgentBountyFeed-Client/1.0 (Python-urllib; +https://github.com/dailydollar-bot/agent-bounty-feed)"


def http_json(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    allow_402: bool = False,
) -> Dict[str, Any]:
    """Zero-dependency HTTP JSON helper supporting MCP JSON-RPC 2.0 and x402 402 responses."""
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        req_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return {"status": resp.status, "data": json.loads(raw)}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        if allow_402 and err.code == 402:
            return {"status": 402, "data": json.loads(raw)}
        raise RuntimeError(f"HTTP {err.code} from {url}: {raw[:400]}") from err


class AgentBountyFeedClient:
    """Minimal, production-ready client for Agent Bounty Feed (MCP + REST + x402)."""

    def __init__(self, x_payment_header: Optional[str] = None) -> None:
        self.x_payment_header = x_payment_header or os.environ.get("X402_PAYMENT_HEADER")
        self._rpc_id = 0

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def mcp_rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }
        resp = http_json(MCP_ENDPOINT, method="POST", payload=payload)
        body = resp["data"]
        if "error" in body:
            raise RuntimeError(f"MCP RPC error: {json.dumps(body['error'])}")
        return body.get("result", {})

    def mcp_list_tools(self) -> List[Dict[str, Any]]:
        result = self.mcp_rpc("tools/list")
        return result.get("tools", [])

    def mcp_call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = self.mcp_rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if "structuredContent" in result and isinstance(result["structuredContent"], dict):
            return result["structuredContent"]
        content = result.get("content", [])
        if content and isinstance(content[0], dict) and "text" in content[0]:
            return json.loads(content[0]["text"])
        return result

    def get_feed_stats(self) -> Dict[str, Any]:
        return self.mcp_call_tool("get_feed_stats")

    def list_bounties(
        self,
        source: Optional[str] = None,
        min_usd: Optional[float] = None,
        network: Optional[str] = None,
        exclude_requires: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 25,
        sort: str = "reward",
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"limit": limit, "sort": sort}
        if source:
            args["source"] = source
        if min_usd is not None:
            args["min_usd"] = min_usd
        if network:
            args["network"] = network
        if exclude_requires:
            args["exclude_requires"] = exclude_requires
        if q:
            args["q"] = q
        return self.mcp_call_tool("list_bounties", args)

    def get_new_bounties(self, since_iso: str, limit: int = 20) -> Dict[str, Any]:
        return self.mcp_call_tool("get_new_bounties", {"since": since_iso, "limit": limit})

    def get_bounty(self, bounty_id: str) -> Dict[str, Any]:
        return self.mcp_call_tool("get_bounty", {"id": bounty_id})

    def get_earn_now(self) -> Dict[str, Any]:
        return http_json(EARN_NOW_ENDPOINT)["data"]

    def get_static_feed(self) -> Dict[str, Any]:
        return http_json(STATIC_FEED_URL)["data"]

    def get_paid_trust_layer(
        self,
        bounty_id: Optional[str] = None,
        source: Optional[str] = None,
        min_usd: Optional[float] = None,
    ) -> Dict[str, Any]:
        query: Dict[str, str] = {}
        if bounty_id:
            query["id"] = bounty_id
        if source:
            query["source"] = source
        if min_usd is not None:
            query["min_usd"] = str(min_usd)
        url = X402_ENDPOINT
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        headers: Dict[str, str] = {}
        if self.x_payment_header:
            headers["X-PAYMENT"] = self.x_payment_header

        return http_json(url, method="GET", headers=headers, allow_402=True)


def compute_agent_ev(item: Dict[str, Any]) -> Dict[str, Any]:
    est_usd = float(item.get("est_usd") or item.get("reward") or 0.0)
    text_blob = f"{item.get('one_liner', '')} {item.get('eligibility', '')}"
    requires = set(item.get("requires") or [])

    win_prob = 0.35
    rationale = []

    paid_rej = re.search(r"(\d+)\s+paid\s*/\s*(\d+)\s+rejected", text_blob)
    claiming_prs = re.search(r"(\d+)\s+claiming\s+PRs?\s+so\s+far", text_blob)
    submissions = re.search(r"(\d+)\s+submissions?\s+so\s+far", text_blob)

    if paid_rej:
        paid, rej = int(paid_rej.group(1)), int(paid_rej.group(2))
        total = paid + rej
        if paid == 0 and rej >= 10:
            win_prob = 0.05
            rationale.append(f"0 paid / {rej} rej (strict judge)")
        elif total > 0:
            win_prob = max(0.08, min(0.85, paid / total))
            rationale.append(f"{paid}/{total} historical approval ({win_prob*100:.0f}%)")
    elif claiming_prs:
        prs = int(claiming_prs.group(1))
        if prs == 0:
            win_prob = 0.65
            rationale.append("0 claiming PRs (wide open)")
        else:
            win_prob = 0.30 / (prs + 1)
            rationale.append(f"{prs} claiming PR(s)")
    elif submissions:
        subs = int(submissions.group(1))
        win_prob = max(0.05, min(0.60, 2.5 / max(3.0, subs)))
        rationale.append(f"{subs} submissions")
    else:
        if item.get("source") == "direct":
            win_prob = 0.50
            rationale.append("verified direct escrow")
        else:
            rationale.append("open competition")

    if requires & {"kyc", "bond", "stake", "purchase"}:
        rationale.append(f"requires {','.join(sorted(requires & {'kyc', 'bond', 'stake', 'purchase'}))}")

    ev_usd = round(est_usd * win_prob, 2)
    return {
        "id": item.get("id"),
        "source": item.get("source"),
        "est_usd": est_usd,
        "ev_usd": ev_usd,
        "win_prob": round(win_prob, 2),
        "slots_left": item.get("slots_left"),
        "requires": ",".join(item.get("requires") or []),
        "rationale": "; ".join(rationale),
        "title": item.get("title", "")[:62],
        "url": item.get("url", ""),
    }


def format_table(rows: List[Dict[str, Any]]) -> str:
    lines = [
        f"{'ID':<32} | {'SRC':<10} | {'USD':>6} | {'EV($)':>6} | {'SIGNALS / COMPETITION':<30} | {'TITLE'}",
        "-" * 135,
    ]
    for r in rows:
        lines.append(
            f"{str(r['id'])[:32]:<32} | {str(r['source'])[:10]:<10} | "
            f"${r['est_usd']:>5.2f} | ${r['ev_usd']:>5.2f} | "
            f"{str(r['rationale'])[:30]:<30} | {r['title']}"
        )
    return "\n".join(lines)


def build_x402_sample_envelope(accept_spec: Dict[str, Any], payer_wallet: str = "0xYourAgentWalletAddress") -> str:
    sample_payload = {
        "x402Version": 1,
        "scheme": accept_spec.get("scheme", "exact"),
        "network": accept_spec.get("network", "base"),
        "payload": {
            "signature": "0x<eip3009_transferWithAuthorization_signature>",
            "authorization": {
                "from": payer_wallet,
                "to": accept_spec.get("payTo"),
                "value": accept_spec.get("maxAmountRequired", "10000"),
                "validAfter": "0",
                "validBefore": str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 120),
                "nonce": "0x" + "00" * 32,
            },
        },
    }
    return base64.b64encode(json.dumps(sample_payload).encode("utf-8")).decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Bounty Feed MCP + REST + x402 Client")
    parser.add_argument("--earn-now", action="store_true", help="Fetch /earn-now and display EV leaderboard")
    parser.add_argument("--mcp-list", action="store_true", help="List open bounties via MCP JSON-RPC 2.0")
    parser.add_argument("--source", type=str, default=None, help="Filter MCP list_bounties by source (e.g. direct,taskmarket,algora,frantic)")
    parser.add_argument("--min-usd", type=float, default=None, help="Minimum USD reward filter")
    parser.add_argument("--bounty", type=str, default=None, help="Inspect a specific bounty ID via MCP get_bounty")
    parser.add_argument("--x402", type=str, default=None, help="Inspect /v1/bounties x402 payment spec for a bounty ID")
    args = parser.parse_args()

    client = AgentBountyFeedClient()

    if args.bounty:
        print(json.dumps(client.get_bounty(args.bounty), indent=2))
        return

    if args.x402:
        res = client.get_paid_trust_layer(bounty_id=args.x402)
        print(f"HTTP Status: {res['status']}")
        print(json.dumps(res["data"], indent=2))
        return

    if args.earn_now:
        earn = client.get_earn_now()
        scored = sorted([compute_agent_ev(it) for it in earn.get("items", [])], key=lambda x: x["ev_usd"], reverse=True)
        print(f"=== /earn-now Live Feed (generated_at: {earn.get('generated_at')}, count: {earn.get('count')}) ===")
        print(format_table(scored))
        return

    if args.mcp_list:
        res = client.list_bounties(source=args.source, min_usd=args.min_usd, limit=30)
        scored = sorted([compute_agent_ev(it) for it in res.get("items", [])], key=lambda x: x["ev_usd"], reverse=True)
        print(f"=== MCP list_bounties (count: {res.get('count')}) ===")
        print(format_table(scored))
        return

    print("=" * 95)
    print("1. MCP JSON-RPC 2.0: tools/list & get_feed_stats (https://agent-bounty-feed.dailydollar.workers.dev/mcp)")
    print("=" * 95)
    tools = client.mcp_list_tools()
    print(f"Discovered {len(tools)} MCP tools: {', '.join(t['name'] for t in tools)}")

    stats_resp = client.get_feed_stats()
    stats = stats_resp.get("stats", {})
    print(
        f"Feed Timestamp : {stats_resp.get('generated_at')}\n"
        f"Open Bounties  : {stats.get('items')} items totaling ${stats.get('total_usd')} USD-equiv\n"
        f"Zero-Friction  : {stats.get('no_bond_no_stake_no_kyc')} bounties require NO bond, NO stake, and NO KYC\n"
        f"New Last 24h   : {stats.get('new_last_24h')} bounties"
    )
    by_src = stats.get("by_source", {})
    active_sources = [f"{k}: {v['items']} (${v['usd']})" for k, v in by_src.items() if v.get("items", 0) > 0]
    print("Active Sources : " + " | ".join(active_sources))

    print("\n" + "=" * 95)
    print("2. REST /earn-now Ranked by Autonomous Agent Expected Value (EV)")
    print("=" * 95)
    earn = client.get_earn_now()
    earn_scored = sorted([compute_agent_ev(it) for it in earn.get("items", [])], key=lambda x: x["ev_usd"], reverse=True)
    print(format_table(earn_scored))

    print("\n" + "=" * 95)
    print("3. MCP tools/call -> list_bounties (All Sources incl. Direct, Taskmarket, Frantic, Algora)")
    print("=" * 95)
    all_bounties = client.list_bounties(limit=20, sort="reward")
    all_scored = sorted([compute_agent_ev(it) for it in all_bounties.get("items", [])], key=lambda x: x["ev_usd"], reverse=True)
    print(format_table(all_scored[:15]))

    print("\n" + "=" * 95)
    print("4. MCP tools/call -> get_bounty (Inspecting direct:abf-growth-guide-5usdc)")
    print("=" * 95)
    direct_bounty = client.get_bounty("direct:abf-growth-guide-5usdc")
    print(json.dumps(direct_bounty.get("item", {}), indent=2))

    print("\n" + "=" * 95)
    print("5. Paid Trust Layer x402 Handshake (GET /v1/bounties?id=direct:abf-growth-guide-5usdc)")
    print("=" * 95)
    x402_resp = client.get_paid_trust_layer(bounty_id="direct:abf-growth-guide-5usdc")
    accept_spec = (x402_resp["data"].get("accepts") or [{}])[0]
    print(f"HTTP Status        : {x402_resp['status']} (x402Version={x402_resp['data'].get('x402Version')})")
    print(f"Network / Scheme   : {accept_spec.get('network')} / {accept_spec.get('scheme')}")
    print(f"Asset (Base USDC)  : {accept_spec.get('asset')} ({accept_spec.get('extra', {}).get('name')})")
    print(f"Price Required     : {int(accept_spec.get('maxAmountRequired', '10000')) / 1e6:.2f} USDC ({accept_spec.get('maxAmountRequired')} atomic units)")
    print(f"PayTo Recipient    : {accept_spec.get('payTo')}")
    sample_hdr = build_x402_sample_envelope(accept_spec)
    print(f"Sample X-PAYMENT   : {sample_hdr[:72]}...")


if __name__ == "__main__":
    main()
