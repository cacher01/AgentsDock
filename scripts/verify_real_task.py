import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
    response = httpx.post(
        f"{base}/tasks",
        json={"input": "请用两句话解释什么是多智能体动态编排框架。"},
        timeout=120,
    )
    print("post", response.status_code, response.text[:300])
    response.raise_for_status()
    task_id = response.json()["task_id"]
    task = {}
    for _ in range(30):
        task_response = httpx.get(f"{base}/tasks/{task_id}", timeout=20)
        task_response.raise_for_status()
        task = task_response.json()
        print("status", task.get("status"), "workflow", task.get("workflow"))
        if task.get("status") in {"completed", "degraded", "failed"}:
            break
        time.sleep(2)
    result = httpx.get(f"{base}/tasks/{task_id}/result", timeout=20)
    print("result", result.status_code, result.text[:500])


if __name__ == "__main__":
    main()
