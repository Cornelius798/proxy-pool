#!/usr/bin/env python3
"""
HTTP forward-proxy pipeline (one input of the tiered pool).

Fetches raw HTTP proxy lists, tests real handshakes AND full TLS
tunnels to opencode.ai, filters by region, and writes the surviving
nodes to http_nodes.json for tier_outputs.py to merge with the
subs-check subscription-node results.

Subscription nodes (VLESS/VMess/SS/Trojan/...) are NOT tested here —
they go through subs-check (mihomo core) in the workflow. This script
only handles plain HTTP CONNECT proxies.

Verification is two-stage (upgraded 2026-10-20): a bare handshake
is not enough — most "working" free proxies grant CONNECT but never
deliver a byte (measured: 288 handshake-verified nodes, only 5 could
complete a TLS tunnel to the real workload host). A node counts as
working only if a TLS handshake through it succeeds.

No speed test is done anywhere in this run.
"""

import json
import socket
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Target countries (ISO codes) — user requirement: HK/JP/SG/TW/US only.
# Raw IP:PORT lists carry no node names, so region filtering is done
# via GeoIP (ip-api.com batch API: free, no key, 100 IPs per request,
# 45 requests/minute on the free tier).
TARGET_COUNTRIES = {"HK", "JP", "SG", "TW", "US"}

# Public proxy list sources (raw IP:PORT, one per line). HTTP only —
# SOCKS5 sources were dropped 2026-11-23 (user decision).
SOURCES = {
    "http": [
        "https://raw.githubusercontent.com/ClearProxy/checked-proxy-list/main/http/raw/all.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
        "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ],
}

# Subscription sources moved to subs-check (subs-check/config-*.yaml)
# 2026-11-23: subscription nodes (VLESS/VMess/SS/Trojan/...) are
# tested by subs-check through the mihomo core, not by this script.
# Verified sources (all return HTTP 200 with a Clash YAML proxies
# list): ermaozi/get_subscribe, anaer/Sub, ripaojiedian/freenode,
# mahdibland/ShadowsocksAggregator. See subs-check/config-full.yaml.

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

def _resolve_server(server):
    """Resolve a node server to an IPv4 literal.

    Used by tier_outputs.py for subscription nodes whose server is a
    domain. Most nodes already use IP literals; resolve the occasional
    domain via DNS and return None if it does not resolve.
    """
    try:
        socket.inet_aton(server)
        return server
    except Exception:
        pass
    try:
        infos = socket.getaddrinfo(server, None, socket.AF_INET,
                                   socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    return None


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

# Tunnel verification target: the pool's real workload host. A proxy
# that cannot carry traffic to this host is useless to the gateway,
# regardless of how healthy its handshake looks.
TUNNEL_HOST = "opencode.ai"
TUNNEL_PORT = 443

_TLS_CTX = ssl.create_default_context()
_TLS_CTX.check_hostname = False
_TLS_CTX.verify_mode = ssl.CERT_NONE  # tunnel liveness only; the gateway verifies certs itself


def _http_tunnel(proxy, timeout=12):
    """Full HTTP tunnel: CONNECT + TLS handshake."""
    ip, port = proxy.rsplit(':', 1)
    try:
        s = socket.create_connection((ip, int(port)), timeout=6)
        s.settimeout(timeout)
        s.sendall(f'CONNECT {TUNNEL_HOST}:{TUNNEL_PORT} HTTP/1.1\r\n'
                  f'Host: {TUNNEL_HOST}:{TUNNEL_PORT}\r\n\r\n'.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = s.recv(4096)
            if not chunk:
                return False
            buf += chunk
            if len(buf) > 16384:
                return False
        if ' 200' not in buf.split(b'\r\n', 1)[0].decode(errors='ignore'):
            return False
        tls = _TLS_CTX.wrap_socket(s, server_hostname=TUNNEL_HOST)
        tls.close()
        return True
    except Exception:
        try:
            s.close()
        except Exception:
            pass
        return False


def check_tunnel(args):
    """Stage 2: real tunnel test. Returns (proxy, type) if it carries TLS."""
    proxy, ptype = args
    return (proxy, ptype) if _http_tunnel(proxy) else None

def batch_geoip(ips):
    """Query ip-api.com batch endpoint.

    Returns {ip: {"country": str, "org": str, "isp": str,
    "hosting": bool|None, "mobile": bool|None}} — country for the
    region filter, hosting/mobile for the residential classification.
    Returns None only if EVERY batch failed (service down); partial
    results are kept as-is.
    """
    result = {}
    any_success = False
    batches = [ips[i:i + 100] for i in range(0, len(ips), 100)]
    for n, batch in enumerate(batches):
        try:
            data = json.dumps(batch).encode()
            req = urllib.request.Request(
                "http://ip-api.com/batch?fields=query,countryCode,org,isp,hosting,mobile,status",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                items = json.loads(resp.read())
                for item in items:
                    if item.get("status") == "success":
                        result[item["query"]] = {
                            "country": item.get("countryCode", ""),
                            "org": item.get("org", ""),
                            "isp": item.get("isp", ""),
                            "hosting": item.get("hosting"),
                            "mobile": item.get("mobile"),
                        }
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
    Returns (kept, geo) where geo is the {ip: {...}} map from
    batch_geoip ({} when GeoIP is unreachable).
    Fail-open: if GeoIP is unreachable, return everything with a warning
    (an empty pool would break downstream consumers).
    """
    ips = sorted(set(p.rsplit(':', 1)[0] for p, _ in working))
    print(f"GeoIP region filter: {len(ips)} unique IPs...", file=sys.stderr)
    geo = batch_geoip(ips)
    if geo is None:
        return working, {}
    kept = [(p, t) for p, t in working
            if geo.get(p.rsplit(':', 1)[0], {}).get("country", "")
            in TARGET_COUNTRIES]
    print(f"Region filter kept {len(kept)}/{len(working)} "
          f"(countries: {sorted(TARGET_COUNTRIES)})", file=sys.stderr)
    return kept, geo


def is_residential(geo_entry):
    """Residential classification from one ip-api.com record.

    Residential = not hosting and not mobile (a home broadband exit,
    not a datacenter/VPS and not a cellular exit). Fail-open: missing
    hosting/mobile info means NOT residential — the node stays in the
    full pool but is not advertised as residential.
    """
    if not geo_entry:
        return False
    return geo_entry.get("hosting") is False and geo_entry.get("mobile") is False


def check_proxy(args):
    """Test a single proxy. Returns (proxy, type) if working, else None."""
    proxy, ptype = args
    return (proxy, ptype) if test_http(proxy) else None


def main():
    # Fetch all raw HTTP lists (subscription nodes go through
    # subs-check, not this script).
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

    # Stage 2: tunnel verification (30 concurrent). A handshake means the
    # proxy answers; only a completed TLS tunnel means it can carry traffic.
    tunneling = []
    with ThreadPoolExecutor(max_workers=30) as ex:
        results = ex.map(check_tunnel, working)
        for r in results:
            if r:
                tunneling.append(r)
    working = tunneling
    print(f"Tunnel-verified: {len(working)} (of handshake-passing)",
          file=sys.stderr)

    # Region filter: raw IP:PORT lists have no node names, so GeoIP is the
    # only way to enforce the HK/JP/SG/TW/US requirement.
    working, geo = filter_by_region(working)
    print(f"After region filter: {len(working)}", file=sys.stderr)

    # Emit the surviving HTTP nodes as full node dicts for
    # tier_outputs.py. Every node here passed the TLS tunnel check to
    # opencode.ai, so tier_outputs.py puts them in both the full tier
    # and the AI tier (deduped against the subs-check results).
    nodes = []
    for i, (proxy, ptype) in enumerate(working):
        ip, port = proxy.rsplit(':', 1)
        nodes.append({
            'name': f"{ptype}_{i}",
            'type': 'http',
            'server': ip,
            'port': int(port),
        })
    with open("http_nodes.json", "w") as f:
        json.dump(nodes, f)
    print(f"Wrote http_nodes.json: {len(nodes)} verified HTTP nodes",
          file=sys.stderr)

if __name__ == "__main__":
    main()
