"""Print the README's notes for one version (the paragraph that starts with 'vX.Y:')."""
import re
import sys

version = sys.argv[1].lstrip("v")
text = open("README.md", encoding="utf-8").read()
m = re.search(rf"^v{re.escape(version)}:(.*?)(?=^v\d+(?:\.\d+)+:|^## |\Z)", text, re.S | re.M)
print(m.group(1).strip() if m else f"AC8 Asset Extractor v{version}")
