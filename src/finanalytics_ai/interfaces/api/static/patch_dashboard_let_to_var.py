
from pathlib import Path
import sys

TARGET = Path(r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\static\dashboard.html")

fixes = [
    ("let currentTicker = null;", "var currentTicker = null;"),
    ("let currentInterval = ", "var currentInterval = "),
    ("let priceSeries = null;", "var priceSeries = null;"),
    ("let priceChart = null;", "var priceChart = null;"),
    ("let bars = [];", "var bars = [];"),
]

raw = TARGET.read_bytes()
crlf = b"\r\n" in raw
text = raw.decode("utf-8").replace("\r\n", "\n")

changed = []
for old, new in fixes:
    if old in text and new not in text:
        text = text.replace(old, new, 1)
        changed.append(old + " → " + new)

if "--dry-run" in sys.argv:
    for c in changed: print("[DRY]", c)
else:
    out = text.encode("utf-8")
    if crlf: out = out.replace(b"\n", b"\r\n")
    TARGET.write_bytes(out)
    for c in changed: print("[FIXED]", c)
    if not changed: print("[OK] Nada a mudar")
