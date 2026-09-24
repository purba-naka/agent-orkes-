import json
import sys

import httpx

AGENT_ID = sys.argv[1]
PROMPT = sys.argv[2]

client = httpx.Client(timeout=300.0)
with client.stream(
    "POST",
    f"http://127.0.0.1:8000/api/v1/agents/{AGENT_ID}/runs",
    json={"input": {"prompt": PROMPT}},
) as r:
    for line in r.iter_lines():
        if not line.startswith("data:"):
            continue
        d = json.loads(line[5:].strip())
        if d.get("code"):
            print("ERROR:", d)
        elif d.get("status"):
            print("STATUS:", d.get("status"), "| result:", d.get("result_name"))
            out = d.get("output") or {}
            print("CONTENT:", (out.get("content") or "")[:600])
            for name, val in (out.get("outputs") or {}).items():
                print(f"--- node {name}:", json.dumps(val, ensure_ascii=False)[:300])
