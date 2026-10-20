"""Talks to InvestSights over its MCP connection using your personal key."""
import json
import os
import time

import requests

MCP_URL = os.environ.get(
    "INVESTSIGHTS_MCP_URL", "https://investsights.in/api/v2/mcp-public/mcp"
)


class QuotaReached(Exception):
    pass


class InvestSights:
    def __init__(self, key, budget=95, min_interval=6.5):
        self.key = key
        self.budget = budget
        self.min_interval = min_interval  # free plan: 10 requests per minute
        self.session = requests.Session()
        self.session_id = None
        self.req_id = 0
        self.last_call = 0.0
        self.calls = 0

    # ---------- low level ----------
    def _headers(self):
        h = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload):
        r = self.session.post(MCP_URL, headers=self._headers(), json=payload, timeout=120)
        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        r.raise_for_status()
        if "id" not in payload or not r.text.strip():
            return None
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            message = None
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if "result" in obj or "error" in obj:
                        message = obj
            return message
        return r.json()

    def connect(self):
        self.session_id = None
        self.req_id += 1
        self._post({
            "jsonrpc": "2.0", "id": self.req_id, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "stock-screener", "version": "1.0"},
            },
        })
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except requests.RequestException:
            pass

    # ---------- tool calls ----------
    def call(self, tool, arguments, retries=3):
        if self.calls >= self.budget:
            raise QuotaReached(f"Daily budget of {self.budget} requests used")
        last_error = None
        for attempt in range(retries):
            wait = self.min_interval - (time.time() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            self.last_call = time.time()
            self.req_id += 1
            try:
                msg = self._post({
                    "jsonrpc": "2.0", "id": self.req_id, "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                })
                self.calls += 1
            except requests.HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                last_error = e
                if code == 429:
                    time.sleep(65)
                elif code in (400, 404):      # session expired, reconnect
                    self.connect()
                elif code in (401, 403):
                    raise RuntimeError(
                        "InvestSights rejected the key. Check the INVESTSIGHTS_KEY secret."
                    ) from e
                else:
                    time.sleep(10)
                continue
            except requests.RequestException as e:
                last_error = e
                time.sleep(10)
                continue

            if msg is None:
                last_error = RuntimeError("Empty reply from InvestSights")
                continue
            if "error" in msg:
                raise RuntimeError(f"InvestSights error: {msg['error']}")
            result = msg.get("result", {})
            if result.get("structuredContent"):
                return result["structuredContent"]
            text = "".join(
                c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
            )
            if result.get("isError"):
                raise RuntimeError(f"InvestSights tool error: {text[:300]}")
            try:
                return json.loads(text)
            except ValueError:
                return {"raw": text}
        raise RuntimeError(f"InvestSights call {tool} failed: {last_error}")

    # ---------- helpers ----------
    def screen_all(self, filters, cap_min, cap_max, depth=0):
        """advanced_screen returns at most 100 rows, so split the market-cap
        range and screen each half if a result is full."""
        f = dict(filters)
        f["market_cap"] = {"gte": cap_min, "lte": cap_max}
        res = self.call("advanced_screen", {
            "filters": f, "limit": 100, "sort_by": "market_cap", "sort_order": "desc",
        })
        rows = res.get("results", []) or []
        total = res.get("total", len(rows)) or len(rows)
        meta = {"price_as_of": res.get("price_as_of"), "data_notes": res.get("data_notes") or {}}
        if total > len(rows) and depth < 6 and cap_max - cap_min > 50:
            mid = (cap_min + cap_max) / 2
            a, ma = self.screen_all(filters, cap_min, mid, depth + 1)
            b, mb = self.screen_all(filters, mid, cap_max, depth + 1)
            seen, merged = set(), []
            for r in a + b:
                if r["symbol"] not in seen:
                    seen.add(r["symbol"])
                    merged.append(r)
            meta["data_notes"].update(ma["data_notes"])
            meta["data_notes"].update(mb["data_notes"])
            return merged, meta
        return rows, meta

    def statements(self, symbol):
        return self.call("get_financial_statements", {
            "symbol": symbol, "statement": "all", "period": "annual", "limit": 4,
        })

    def concall(self, symbol):
        return self.call("get_concall_analysis", {"symbol": symbol})
