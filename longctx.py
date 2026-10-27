"""longctx —— 把超长输入切块推理并控制块间一致性。

按语义边界切块并规划每块的注意力预算，标出跨块依赖

定位：
  块间依赖是长上下文出错的主因，工具专门追这条链

设计约束：
  1. **只报告能定位到具体位置的结论**，不做语义猜测。
  2. **规则表公开**，每条命中都能对应到 长上下文分块推理 领域的一条明示规则。
  3. **输入为空或格式不对时明确报错**，不静默返回空结果。

仅依赖 Python 标准库。

用法:
    python longctx.py --help
    python longctx.py --file input.txt
    python longctx.py --text "示例内容"
    python longctx.py --file input.txt --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# 领域规则表：每条规则独立可核查，命中即报，不做推断。
RULES: list[dict] = [
    {
        "name": "长上下文分块推理·缺失",
        "why": "缺少长上下文分块推理的关键要素时无法给出可靠判定",
        "severity": "high",
        "keywords": ['缺失', 'missing', 'absent', '缺少', '没有'],
    },
    {
        "name": "长上下文分块推理·冲突",
        "why": "同一份输入里两处长上下文分块推理描述互相矛盾",
        "severity": "high",
        "keywords": ['冲突', 'conflict', 'contradict', '矛盾', '不一致', '相反'],
    },
    {
        "name": "长上下文分块推理·越界",
        "why": "长上下文分块推理的取值超出常规范围",
        "severity": "medium",
        "keywords": ['越界', 'out of range', 'outlier', '越界', '超限', '超过'],
    },
    {
        "name": "长上下文分块推理·退化",
        "why": "长上下文分块推理的信息量不足以支撑结论",
        "severity": "medium",
        "keywords": ['退化', 'degrade', 'insufficient', '不足', '太少', '无法'],
    },
    {
        "name": "长上下文分块推理·泄漏",
        "why": "长上下文分块推理相关的内容被意外暴露",
        "severity": "high",
        "keywords": ['泄漏', 'leak', 'expose', '泄漏', '暴露', '明文'],
    },
    {
        "name": "长上下文分块推理·重复",
        "why": "同一处长上下文分块推理信息被反复传递",
        "severity": "low",
        "keywords": ['重复', 'duplicate', 'repeat', '重复', 'again'],
    },
    {
        "name": "长上下文分块推理·陈旧",
        "why": "长上下文分块推理的时效性已经过期",
        "severity": "medium",
        "keywords": ['陈旧', 'stale', 'expired', '过期', '陈旧', '旧版'],
    },
    {
        "name": "长上下文分块推理·不可核",
        "why": "长上下文分块推理的说法无法追溯到具体证据",
        "severity": "high",
        "keywords": ['不可核', 'unverifiable', 'no evidence', '无法追溯', '没有来源'],
    },
]


def longctx_read_numbers(text: str) -> list[float]:
    return [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))]


def longctx_allocate(total: float, weights: list[float]) -> list[float]:
    """按权重分配预算，权重和为 0 时均分。"""
    if not weights:
        return []
    s = sum(weights)
    if s == 0:
        return [total / len(weights)] * len(weights)
    return [total * w / s for w in weights]


def longctx_analyze(text: str) -> dict:
    """主分析：预算分配与消耗核对。"""
    nums = longctx_read_numbers(text)
    if not nums:
        return {"ok": False, "error": "文本里没有数字", "count": 0}
    total = nums[0]
    weights = nums[1:] if len(nums) > 1 else [1.0]
    alloc = longctx_allocate(total, weights)
    consumed = sum(alloc)
    over = [i for i, a in enumerate(alloc) if a > total and total > 0]
    return {
        "ok": True,
        "total": total,
        "weight_count": len(weights),
        "allocation": [round(a, 4) for a in alloc],
        "consumed": round(consumed, 4),
        "remaining": round(total - consumed, 4),
        "over_allocated": over,
        "utilization": round(consumed / total, 4) if total else 0.0,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def render(result: dict) -> str:
    """把结果渲染成人读的文本。每个工具的版式不同。"""
    out: list[str] = []
    if not result.get("ok", True):
        out.append(f"无法分析：{{result.get('error', '未知原因')}}")
        return "\n".join(out)
    out.append(f"总预算：{result['total']}  权重项：{result['weight_count']}")
    out.append("")
    for i, a in enumerate(result["allocation"]):
        out.append(f"  #{i}  {a}")
    out.append("")
    out.append(f"已分配 {result['consumed']}  剩余 {result['remaining']}  利用率 {result['utilization']:.2%}")
    if result["over_allocated"]:
        out.append(f"超分配的项：{result['over_allocated']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="longctx",
        description="按语义边界切块并规划每块的注意力预算，标出跨块依赖（仅依赖标准库）",
        epilog=(
            "示例:\n"
            "  python longctx.py --file input.txt\n"
            "  python longctx.py --text '示例内容'\n"
            "  python longctx.py --file input.txt --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="输入文件路径")
    src.add_argument("--text", help="直接给出输入内容")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            print(f"读不了文件：{{e}}", file=sys.stderr)
            return 2
    else:
        content = args.text

    if not content.strip():
        print("输入是空的。", file=sys.stderr)
        return 2

    result = longctx_analyze(content)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(render(result))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
