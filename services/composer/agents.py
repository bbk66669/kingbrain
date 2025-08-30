import os
import yaml
from typing import Dict, Any, Optional, Tuple

def _read_yaml(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def load_agents(repo_root: str) -> Dict[str, Any]:
    cfg = _read_yaml(os.path.join(repo_root, ".collab", "agents.yaml"))
    return cfg.get("agents", {})

def get_agent(agents: Dict[str, Any], name: str) -> Dict[str, Any]:
    return agents.get(name, {}) or {}

# —— 可选 LLM（有 key 就用；失败自动回退占位） ——————————————
def call_llm(provider: str, model: str, system_prompt: str, user_prompt: str,
             max_tokens: int = 2000, temperature: float = 0.2) -> Optional[str]:
    try:
        provider = (provider or "").lower()
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role":"system", "content":system_prompt or ""},
                    {"role":"user", "content":user_prompt or ""},
                ],
                max_tokens=max_tokens or 1024,
                temperature=temperature or 0.2,
            )
            return (resp.choices[0].message.content or "").strip()

        if provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens or 1024,
                temperature=temperature or 0.2,
                system=system_prompt or "",
                messages=[{"role":"user","content":user_prompt or ""}],
            )
            # 支持 text 块合并
            parts = []
            for blk in msg.content:
                if getattr(blk, "type", "") == "text":
                    parts.append(getattr(blk, "text", "") or "")
                elif isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(blk.get("text","") or "")
            return "\n".join(parts).strip()

        return None
    except Exception:
        return None
