"""Verify the Google ADC path the way an image job uses it, then generate one real image.

    python scripts/verify_google_adc.py                       # 默认模型，出一张图
    python scripts/verify_google_adc.py --no-generate         # 只验证凭据和接口，不花钱
    python scripts/verify_google_adc.py --model gemini-3-pro-image --size 1536x864

退出码 0 表示全链路可用；非 0 表示某一步失败，错误会直接打印出来。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))


def main() -> int:
    parser = argparse.ArgumentParser(description="验证本地 ADC 能否调用 Vertex AI 图像模型")
    parser.add_argument("--model", default="gemini-nano-banana-2.1")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--prompt", default="a small orange cat reading a book, flat children's book illustration")
    parser.add_argument("--out", default="", help="图片输出目录，默认写到系统临时目录")
    parser.add_argument("--no-generate", action="store_true", help="只验证凭据与接口，不生成图片")
    args = parser.parse_args()

    report = {"model": args.model, "size": args.size, "steps": []}

    # 第 1 步：ADC 凭据能否加载并刷新（过期或未登录会在这里失败）
    try:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, detected_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
        report["steps"].append({"step": "adc_refresh", "ok": True})
    except Exception as exc:
        report["steps"].append({"step": "adc_refresh", "ok": False, "error": str(exc)})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    # 第 2 步：项目与区域要和出图任务实际用的一致
    from core.infra.ai.llm_client import _resolve_google_adc_project

    project = _resolve_google_adc_project()
    if not project:
        report["steps"].append(
            {
                "step": "resolve_project",
                "ok": False,
                "error": "未解析到项目。运行 gcloud auth application-default set-quota-project <PROJECT_ID> 或设置 GOOGLE_CLOUD_PROJECT",
            }
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    location = __import__("os").getenv("GOOGLE_CLOUD_LOCATION") or "global"
    report["steps"].append({"step": "resolve_project", "ok": True, "project": project, "location": location})

    # 第 3 步：count_tokens 是免费探针，能验证鉴权和模型路由
    from google import genai

    client = genai.Client(vertexai=True, project=project, location=location)
    try:
        tokens = client.models.count_tokens(model=args.model, contents="connection check")
        report["steps"].append({"step": "count_tokens", "ok": True, "total_tokens": tokens.total_tokens})
    except Exception as exc:
        hint = {
            "404": "该模型在此项目/区域不可用，换 gemini-nano-banana-2.1 或 gemini-3.1-flash-image",
            "403": "权限不足：确认 aiplatform.googleapis.com 已启用，账号具备 Vertex AI 权限，配额项目正确",
            "429": "触发限流，稍后重试",
        }
        report["steps"].append({"step": "count_tokens", "ok": False, "error": str(exc), "hint": hint.get(str(getattr(exc, "code", "")), "")})
        # count_tokens 对部分图像模型不开放，不因此判定失败，继续看真实生成
    if args.no_generate:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if all(step["ok"] for step in report["steps"]) else 1

    # 第 4 步：真实生成一张，顺带验证成本核算
    from core.infra.ai.image_cost import cost_summary, reset_costs
    from core.infra.ai.image_providers import IMAGE_PROVIDERS

    reset_costs()
    out_dir = Path(args.out) if args.out else Path(__import__("tempfile").gettempdir()) / "vertex-image-verify"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{args.model}_{args.size.replace('x', 'x')}.png"

    try:
        result = IMAGE_PROVIDERS["google_adc"].generate(prompt=args.prompt, size=args.size, model=args.model)
        data = result.get("data") or b""
        target.write_bytes(data)
        summary = cost_summary()
        report["steps"].append(
            {
                "step": "generate_image",
                "ok": bool(data),
                "bytes": len(data),
                "file": str(target),
                "cost_usd": summary["usd"],
            }
        )
    except Exception as exc:
        report["steps"].append({"step": "generate_image", "ok": False, "error": str(exc)})

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["steps"][-1].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
