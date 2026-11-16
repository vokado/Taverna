"""Pure parser benchmark. Usage: python scripts/measure_path_validation.py output.json.

No filesystem throughput, model/GPU latency or target Windows SLA is inferred.
"""

import cProfile
import hashlib
import inspect
import io
import json
import pstats
import sys
import time
from pathlib import Path

from taverna.storage import portable_parts

path = "chats/ca66979b-cd39-4d30-9bde-fd9563607990/messages/fragment." + "a" * 64 + ".json"
samples = []
for _ in range(3):
    t = time.perf_counter()
    for _i in range(20000):
        portable_parts(path)
    samples.append(time.perf_counter() - t)
p = cProfile.Profile()
p.enable()
for _i in range(2000):
    portable_parts(path)
p.disable()
output = io.StringIO()
pstats.Stats(p, stream=output).strip_dirs().sort_stats("cumtime").print_stats(10)
value = {
    "iterations": 20000,
    "samples_seconds": samples,
    "source_function_sha256": hashlib.sha256(inspect.getsource(portable_parts).encode()).hexdigest(),
    "note": "Pure syntax parser microbenchmark; no file I/O or model calls",
    "profile_2000_calls": output.getvalue(),
}
Path(sys.argv[1]).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"samples_seconds": samples}))
