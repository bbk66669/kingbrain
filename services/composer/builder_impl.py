# （预留文件）Builder 更复杂能力的扩展点
# 说明：
# - 当前“真实实现”的核心逻辑已并入 nodes/builder.py，确保 LangGraph 节点即可自给自足。
# - 若后续要拆分：把 _borrow_git/_run_linters/_make_newfile_diff 等提炼到本模块，由节点薄封装调用。

from typing import Dict, Any, List

def placeholder(_: Dict[str, Any]) -> List[Dict[str, Any]]:
    return []
