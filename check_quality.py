#!/usr/bin/env python3
"""
Quality checker for proxy nodes.
Tests TCP connectivity and measures latency, filters dead/slow nodes.
"""

import socket
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed

# Quality thresholds
TCP_TIMEOUT = 5  # seconds
MAX_LATENCY_MS = 3000  # 3 seconds max
MAX_WORKERS = 50

def check_tcp(server, port):
    """Test TCP connectivity, return latency in ms or None if failed."""
    try:
        start = time.time()
        sock = socket.create_connection((server, port), timeout=TCP_TIMEOUT)
        sock.close()
        latency = (time.time() - start) * 1000
        return latency
    except Exception:
        return None

def check_proxy(proxy):
    """Check a single proxy, return (proxy, latency_ms) or (proxy, None)."""
    server = proxy.get('server', '')
    port = proxy.get('port', 0)
    
    if not server or not port:
        return (proxy, None)
    
    try:
        port = int(port)
    except (ValueError, TypeError):
        return (proxy, None)
    
    latency = check_tcp(server, port)
    return (proxy, latency)

def main():
    print("Loading clash.yaml...", flush=True)
    with open("clash.yaml", "r", encoding="utf-8") as f:
        # Skip comment lines at top
        content = f.read()
    
    data = yaml.safe_load(content)
    proxies = data.get('proxies', []) if isinstance(data, dict) else []
    
    print(f"Checking {len(proxies)} proxies (TCP connectivity)...", flush=True)
    
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_proxy, p): p for p in proxies}
        done = 0
        for future in as_completed(futures):
            proxy, latency = future.result()
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(proxies)} checked...", flush=True)
            if latency is not None and latency <= MAX_LATENCY_MS:
                proxy['_latency_ms'] = round(latency, 1)
                results.append((latency, proxy))
    
    # Sort by latency (fastest first)
    results.sort(key=lambda x: x[0])
    
    print(f"\n{len(results)}/{len(proxies)} passed quality check", flush=True)
    
    if not results:
        print("ERROR: No proxies passed quality check", flush=True)
        return 1
    
    # Build output with quality info
    proxies_list = []
    for latency, p in results:
        p.pop('_latency_ms', None)
        # Prepend latency to name for visibility
        name = p.get('name', 'unnamed')
        # Keep original name, quality is implicit by ordering
        proxies_list.append(p)
    
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
        ],
    }
    
    with open("clash.yaml", "w", encoding="utf-8") as f:
        f.write("# Aggregated by free-nodes-crawler (quality-checked)\n")
        f.write(f"# Total: {len(proxies_list)} working proxies (from {len(proxies)} tested)\n")
        f.write(f"# Sorted by latency, fastest first\n\n")
        yaml.safe_dump(output, f, allow_unicode=True, sort_keys=False)
    
    print(f"Wrote clash.yaml: {len(proxies_list)} quality proxies", flush=True)
    return 0

if __name__ == "__main__":
    exit(main())
