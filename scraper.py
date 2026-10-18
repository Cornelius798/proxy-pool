#!/usr/bin/env python3
"""
Free nodes aggregator - fetches from multiple GitHub sources,
merges and deduplicates by server:port, outputs clash.yaml.
"""

import sys
import urllib.request
import yaml

# Region filter: only keep nodes from these regions (matched against node name).
# Empty list = no filtering.
# 香港 Hong Kong | 日本 Japan | 新加坡 Singapore | 台湾 Taiwan | 美国 USA
REGION_KEYWORDS = [
    # Hong Kong
    "香港", "hongkong", "hong kong", "hk",
    # Japan
    "日本", "japan", "jp", "东京", "tokyo", "大阪", "osaka",
    # Singapore
    "新加坡", "singapore", "sg", "狮城",
    # Taiwan
    "台湾", "taiwan", "tw", "台北", "taipei",
    # USA
    "美国", "united states", "usa", "us",
    "洛杉矶", "los angeles", "旧金山", "san francisco", "纽约", "new york",
    "西雅图", "seattle", "芝加哥", "chicago", "达拉斯", "dallas",
]

def match_region(name):
    """Check if node name matches any wanted region keyword."""
    if not REGION_KEYWORDS:
        return True
    nl = name.lower()
    return any(kw in nl for kw in REGION_KEYWORDS)

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
            unmatched = []  # For GeoIP fallback
            for p in proxies:
                if not isinstance(p, dict):
                    continue
                # Region filter: name match first, GeoIP fallback later
                pname = p.get('name', '')
                if not match_region(pname):
                    unmatched.append(p)
                    continue
                key = get_proxy_key(p)
                if key and key not in all_proxies:
                    # Tag with source
                    p['_source'] = name
                    all_proxies[key] = p

            # GeoIP fallback for name-unmatched nodes
            if unmatched:
                try:
                    from geoip import filter_by_geoip
                    print(f"  {name}: {len(unmatched)} name-unmatched, trying GeoIP...",
                          file=sys.stderr)
                    geo_kept = filter_by_geoip(unmatched)
                    for p in geo_kept:
                        key = get_proxy_key(p)
                        if key and key not in all_proxies:
                            p['_source'] = name
                            all_proxies[key] = p
                except Exception as e:
                    print(f"  {name}: GeoIP fallback failed: {e}", file=sys.stderr)
            
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
