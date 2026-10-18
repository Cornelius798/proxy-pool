#!/usr/bin/env python3
"""
Free nodes aggregator - fetches from multiple GitHub sources,
merges and deduplicates by server:port, outputs clash.yaml.
"""

import sys
import urllib.request
import yaml

# (name, url) - all clash YAML format
SOURCES = [
    ("free18", "https://ghfast.top/https://raw.githubusercontent.com/free18/v2ray/refs/heads/main/c.yaml"),
    ("ermaozi", "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/clash.yml"),
    ("ovmvo", "https://raw.githubusercontent.com/ovmvo/FreeSub/refs/heads/main/sub/permanent/mihomo.yaml"),
    ("anaer", "https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml"),
    ("peasoft", "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.yml"),
    ("lucas8864", "https://raw.githubusercontent.com/lucas8864/freesub/main/output/clash.yaml"),
    ("au1rxx", "https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/main/output/clash.yaml"),
]

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='ignore')

def get_proxy_key(proxy):
    """Deduplication key: server:port"""
    server = proxy.get('server', '')
    port = proxy.get('port', '')
    return f"{server}:{port}".lower()

def main():
    all_proxies = {}  # key -> proxy dict
    source_stats = {}
    
    for name, url in SOURCES:
        try:
            print(f"Fetching {name}...", file=sys.stderr)
            content = fetch(url)
            data = yaml.safe_load(content)
            
            proxies = []
            if isinstance(data, dict):
                proxies = data.get('proxies', []) or []
            elif isinstance(data, list):
                proxies = data
            
            count_before = len(all_proxies)
            for p in proxies:
                if not isinstance(p, dict):
                    continue
                key = get_proxy_key(p)
                if key and key not in all_proxies:
                    # Tag with source
                    p['_source'] = name
                    all_proxies[key] = p
            
            added = len(all_proxies) - count_before
            source_stats[name] = (len(proxies), added)
            print(f"  {name}: {len(proxies)} proxies, {added} new", file=sys.stderr)
            
        except Exception as e:
            print(f"  {name} FAIL: {e}", file=sys.stderr)
            source_stats[name] = (0, 0)
    
    if not all_proxies:
        print("ERROR: No proxies collected", file=sys.stderr)
        sys.exit(1)
    
    # Build output
    proxies_list = list(all_proxies.values())
    # Remove internal _source tag before output
    for p in proxies_list:
        p.pop('_source', None)
    
    # Simple proxy-groups for usability
    proxy_names = [p.get('name', f'proxy-{i}') for i, p in enumerate(proxies_list)]
    
    output = {
        'proxies': proxies_list,
        'proxy-groups': [
            {
                'name': 'auto',
                'type': 'url-test',
                'proxies': proxy_names,
                'url': 'http://www.gstatic.com/generate_204',
                'interval': 300,
            },
            {
                'name': 'select',
                'type': 'select',
                'proxies': ['auto'] + proxy_names,
            },
        ],
    }
    
    with open("clash.yaml", "w", encoding="utf-8") as f:
        f.write("# Aggregated by free-nodes-crawler\n")
        f.write(f"# Total: {len(proxies_list)} unique proxies\n")
        for name, (total, added) in source_stats.items():
            f.write(f"#   {name}: {total} fetched, {added} new\n")
        f.write("\n")
        yaml.safe_dump(output, f, allow_unicode=True, sort_keys=False)
    
    print(f"\nWrote clash.yaml: {len(proxies_list)} unique proxies", file=sys.stderr)

if __name__ == "__main__":
    main()
