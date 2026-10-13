"""Print the README's notes for one version (the paragraph that starts with 'vX.Y:'), plus the support link."""
import re
import sys

version = sys.argv[1].lstrip("v")
text = open("README.md", encoding="utf-8").read()
m = re.search(rf"^v{re.escape(version)}:(.*?)(?=^v\d+(?:\.\d+)+:|^## |\Z)", text, re.S | re.M)
print(m.group(1).strip() if m else f"AC8 Asset Extractor v{version}")
print("\n---\nLike the app? Support it on [Ko-fi](https://ko-fi.com/akiraseki) ☕")
