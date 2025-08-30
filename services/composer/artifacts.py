from dataclasses import dataclass, asdict
from typing import List, Dict, Any

@dataclass
class Artifact:
    relpath: str                   # 相对仓根路径（不以 / 开头）
    kind: str = "file"             # "file" | "patch"
    encoding: str = "utf-8"        # "utf-8" | "base64"
    content: Any = ""              # 文本或 base64 字节

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def make_text(relpath: str, text: str) -> Dict[str, Any]:
    return Artifact(relpath=relpath, kind="file", encoding="utf-8", content=text).to_dict()

def make_patch(relpath: str, unified_diff: str) -> Dict[str, Any]:
    return Artifact(relpath=relpath, kind="patch", encoding="utf-8", content=unified_diff).to_dict()

def bundle(arts: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"artifacts": arts or []}
