#!/usr/bin/env python3
"""
Free nodes aggregator - fetches from multiple GitHub sources,
merges and deduplicates, outputs clash.yaml.
"""

import sys
import urllib.request
import re

# (name, url, format) - format: 'clash' or 'v2ray' or 'base64'
SOURCES = [
    ("free18", "https://ghfast.top/https://raw.githubusercontent.com/free18/v2ray/refs/heads/main/c.yaml", "clash"),
    ("ermaozi", "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/clash.yml", "clash"),
    ("ovmvo", "https://raw.githubusercontent.com/ovmvo/FreeSub/refs/heads/main/sub/permanent/mihomo.yaml", "clash"),
    ("anaer", "https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml", "clash"),
    ("peasoft", "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.yml", "clash"),
]

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='ignore')

def extract_proxies_clash(content):
    """Extract proxy entries from clash YAML (simple regex, MVP)."""
    # Find the proxies: section and extract individual proxy blocks
    # This is a simplified parser - for MVP we just return the whole content
    # TODO: Proper YAML parsing with pyyaml
    return content

def main():
    all_contents = []
    for name, url, fmt in SOURCES:
        try:
            print(f"Fetching {name}...", file=sys.stderr)
            content = fetch(url)
            if "proxies:" in content or "proxy" in content.lower():
                print(f"  OK: {len(content)} bytes", file=sys.stderr)
                all_contents.append((name, content))
            else:
                print(f"  SKIP: no proxy content", file=sys.stderr)
        except Exception as e:
            print(f"  FAIL: {e}", file=sys.stderr)
    
    if not all_contents:
        print("ERROR: No sources succeeded", file=sys.stderr)
        sys.exit(1)
    
    # MVP: Use the first successful source
    # TODO: Merge proxies from all sources with deduplication
    name, content = all_contents[0]
    
    # Add header comment
    header = f"# Aggregated by free-nodes-crawler\n# Source: {name}\n# Updated: from GitHub Action\n\n"
    
    with open("clash.yaml", "w", encoding="utf-8") as f:
        f.write(header + content)
    
    print(f"Wrote clash.yaml from {name} ({len(content)} bytes)")
    print(f"Total sources succeeded: {len(all_contents)}/{len(SOURCES)}")

if __name__ == "__main__":
    main()
