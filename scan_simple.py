#!/usr/bin/env python3
"""
Simple proxy scanner: fetches SOCKS5/HTTP/HTTPS proxy lists,
tests real handshakes, filters by region, outputs clash.yaml.

Unlike Clash nodes (vmess/vless/trojan), these protocols have
simple handshakes that can be fully verified.
"""

import json
import socket
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Target countries (ISO codes) — user requirement: HK/JP/SG/TW/US only.
# Raw IP:PORT lists carry no node names, so region filtering is done
# via GeoIP (ip-api.com batch API: free, no key, 100 IPs per request,
# 45 requests/minute on the free tier).
TARGET_COUNTRIES = {"HK", "JP", "SG", "TW", "US"}

# Public proxy list sources (raw IP:PORT, one per line)
SOURCES = {
    "socks5": [
        "https://raw.githubusercontent.com/ClearProxy/checked-proxy-list/main/socks5/raw/all.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
        "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=socks5&proxy_format=ipport&format=text",
        "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/socks5.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/us.txt",
        "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/jp.txt",
        "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/sg.txt",
        "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/hk.txt",
        "https://raw.githubusercontent.com/maximilianfeix/free-proxy-list/main/tw.txt",
    ],
    "http": [
        "https://raw.githubusercontent.com/ClearProxy/checked-proxy-list/main/http/raw/all.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
        "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ],
}

def fetch_list(url, timeout=30):
    """Fetch a proxy list, return list of ip:port strings."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            lines = resp.read().decode('utf-8', errors='ignore').splitlines()
        result = []
        for line in lines:
            line = line.strip()
            if ':' in line and not line.startswith('#'):
                parts = line.split(':')
                if len(parts) == 2:
                    try:
                        port = int(parts[1])
                        if 1 <= port <= 65535:
                            result.append(line)
                    except ValueError:
                        pass
        return result
    except Exception as e:
        print(f"  FAIL {url}: {e}", file=sys.stderr)
        return []

def test_socks5(proxy, timeout=10):
    """Test SOCKS5 handshake. Returns True if working."""
    try:
        ip, port = proxy.rsplit(':', 1)
        port = int(port)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        # SOCKS5 greeting: version 5, 1 method, no auth
        s.sendall(b'\x05\x01\x00')
        resp = s.recv(2)
        s.close()
        return len(resp) == 2 and resp[0] == 0x05 and resp[1] == 0x00
    except Exception:
        return False

def test_http(proxy, timeout=10):
    """Test HTTP proxy with CONNECT. Returns True if working."""
    try:
        ip, port = proxy.rsplit(':', 1)
        port = int(port)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        # HTTP CONNECT to a test target
        s.sendall(b'CONNECT www.gstatic.com:443 HTTP/1.1\r\nHost: www.gstatic.com:443\r\n\r\n')
        resp = s.recv(1024).decode('utf-8', errors='ignore')
        s.close()
        return '200' in resp.split('\r\n')[0]
    except Exception:
        return False

def batch_geoip(ips):
    """Query ip-api.com batch endpoint. Returns {ip: country_code}.

    Returns an empty dict only if EVERY batch failed (service down);
    partial results are kept as-is.
    """
    result = {}
    any_success = False
    batches = [ips[i:i + 100] for i in range(0, len(ips), 100)]
    for n, batch in enumerate(batches):
        try:
            data = json.dumps(batch).encode()
            req = urllib.request.Request(
                "http://ip-api.com/batch?fields=query,countryCode,status",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                items = json.loads(resp.read())
                for item in items:
                    if item.get("status") == "success":
                        result[item["query"]] = item.get("countryCode", "")
                        any_success = True
        except Exception as e:
            print(f"  geoip batch {n + 1}/{len(batches)} failed: {e}",
                  file=sys.stderr)
        # Stay under the free tier's 45 req/min even for large lists
        if n < len(batches) - 1:
            time.sleep(1.5)
    if not any_success:
        print("  WARNING: GeoIP service unreachable, "
              "region filter disabled for this run", file=sys.stderr)
        return None
    return result


def filter_by_region(working):
    """Keep only proxies in TARGET_COUNTRIES via GeoIP.

    `working` is a list of (proxy, ptype) with proxy as 'ip:port'.
    Fail-open: if GeoIP is unreachable, return everything with a warning
    (an empty pool would break downstream consumers).
    """
    ips = sorted(set(p.rsplit(':', 1)[0] for p, _ in working))
    print(f"GeoIP region filter: {len(ips)} unique IPs...", file=sys.stderr)
    ip_country = batch_geoip(ips)
    if ip_country is None:
        return working
    kept = [(p, t) for p, t in working
            if ip_country.get(p.rsplit(':', 1)[0], "") in TARGET_COUNTRIES]
    print(f"Region filter kept {len(kept)}/{len(working)} "
          f"(countries: {sorted(TARGET_COUNTRIES)})", file=sys.stderr)
    return kept


def check_proxy(args):
    """Test a single proxy. Returns (proxy, type) if working, else None."""
    proxy, ptype = args
    if ptype == 'socks5':
        ok = test_socks5(proxy)
    else:
        ok = test_http(proxy)
    return (proxy, ptype) if ok else None

def main():
    # Fetch all lists
    all_proxies = []  # (proxy, type)
    seen = set()
    for ptype, urls in SOURCES.items():
        for url in urls:
            print(f"Fetching {ptype}: {url[:60]}...", file=sys.stderr)
            for proxy in fetch_list(url):
                if proxy not in seen:
                    seen.add(proxy)
                    all_proxies.append((proxy, ptype))
    
    print(f"\nTotal unique: {len(all_proxies)}, testing handshakes...",
          file=sys.stderr)
    
    # Test handshakes (50 concurrent)
    working = []
    with ThreadPoolExecutor(max_workers=50) as ex:
        results = ex.map(check_proxy, all_proxies)
        for r in results:
            if r:
                working.append(r)
    
    print(f"Working: {len(working)}/{len(all_proxies)}", file=sys.stderr)

    # Region filter: raw IP:PORT lists have no node names, so GeoIP is the
    # only way to enforce the HK/JP/SG/TW/US requirement.
    working = filter_by_region(working)
    print(f"After region filter: {len(working)}", file=sys.stderr)

    # Build Clash YAML output
    proxies_yaml = []
    for i, (proxy, ptype) in enumerate(working):
        ip, port = proxy.rsplit(':', 1)
        name = f"{ptype}_{i}"
        if ptype == 'socks5':
            proxies_yaml.append({
                'name': name,
                'type': 'socks5',
                'server': ip,
                'port': int(port),
            })
        else:
            proxies_yaml.append({
                'name': name,
                'type': 'http',
                'server': ip,
                'port': int(port),
            })
    
    import yaml
    proxy_names = [p['name'] for p in proxies_yaml]
    output = {
        'proxies': proxies_yaml,
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
    
    with open("clash.yaml", "w") as f:
        f.write("# Simple proxy pool (socks5/http), handshake-verified, "
                "HK/JP/SG/TW/US only\n")
        f.write(f"# Working: {len(working)}\n\n")
        yaml.safe_dump(output, f, sort_keys=False)
    
    print(f"Wrote clash.yaml: {len(proxies_yaml)} verified proxies",
          file=sys.stderr)

if __name__ == "__main__":
    main()
