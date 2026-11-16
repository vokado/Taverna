"""Test-only ZIP authoring helper; stores source bytes without executing them."""

import io
import json
import sys
import zipfile

value = json.loads(sys.stdin.buffer.read())
out = io.BytesIO()
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("manifest.json", json.dumps(value["manifest"], ensure_ascii=False).encode("utf-8"))
    archive.writestr(value["manifest"]["entry"], value["source"].encode("utf-8"))
sys.stdout.buffer.write(out.getvalue())
