"""LLM providers for running the agent from Python (CLI/server). The web app calls the API from JS."""
from __future__ import annotations

import json
import os

import requests



class AnthropicLLM:
    """Anthropic Messages API with tool use (key from ANTHROPIC_API_KEY)."""

    def __init__(self, model: str, api_key: str | None = None):
        self.model, self.key = model, api_key or os.environ["ANTHROPIC_API_KEY"]

    def chat(self, system, messages, tools):
        msgs = []
        for m in messages:
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                    msgs[-1]["content"].append(block)
                else:
                    msgs.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                msgs.append({"role": "assistant", "content": m["content"]})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 1500, "system": system, "messages": msgs, "tools": tools})
        r.raise_for_status()
        content = r.json()["content"]
        return {"text": "".join(b.get("text", "") for b in content if b["type"] == "text"),
                "tool_calls": [{"id": b["id"], "name": b["name"], "input": b["input"]}
                               for b in content if b["type"] == "tool_use"],
                "raw_content": content}


class OpenAICompatLLM:
    """Any OpenAI-compatible chat endpoint, e.g. a local Ollama server at http://localhost:11434/v1."""

    def __init__(self, model: str, base_url: str = "http://localhost:11434/v1", api_key: str = "none"):
        self.model, self.base, self.key = model, base_url.rstrip("/"), api_key

    def chat(self, system, messages, tools):
        msgs = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                msgs.append({"role": "assistant", "content": m["content"] if isinstance(m["content"], str) else "",
                             "tool_calls": [{"id": c["id"], "type": "function", "function": {
                                 "name": c["name"], "arguments": json.dumps(c.get("input") or {})}}
                                            for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in tools]
        r = requests.post(f"{self.base}/chat/completions", timeout=180,
                          headers={"Authorization": f"Bearer {self.key}"},
                          json={"model": self.model, "messages": msgs, "tools": fns})
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        calls = [{"id": c["id"], "name": c["function"]["name"],
                  "input": json.loads(c["function"].get("arguments") or "{}")} for c in (msg.get("tool_calls") or [])]
        return {"text": msg.get("content") or "", "tool_calls": calls, "raw_content": msg.get("content") or ""}
