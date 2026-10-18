#!/usr/bin/env python3
"""
GeoIP fallback for region filtering.
Uses ip-api.com batch API (free, no key, 100 IPs per request).
Only used for nodes whose names don't match region keywords.
"""

import json
import socket
import sys
import urllib.request

# Target country codes (ISO 3166-1 alpha-2)
TARGET_COUNTRIES = {"HK", "JP", "SG", "TW", "US"}

def resolve_ips(servers):
    """Resolve hostnames to IPs. Returns {server: ip}."""
    result = {}
    for s in servers:
        try:
            # Skip if already an IP
            socket.inet_aton(s)
            result[s] = s
        except socket.error:
            try:
                result[s] = socket.gethostbyname(s)
            except Exception:
                pass
    return result

def batch_geoip(ips):
    """Query ip-api.com batch endpoint. Returns {ip: country_code}."""
    if not ips:
        return {}
    
    result = {}
    # 100 IPs per batch request
    for i in range(0, len(ips), 100):
        batch = ips[i:i+100]
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
        except Exception as e:
            print(f"  geoip batch failed: {e}", file=sys.stderr)
    return result

def filter_by_geoip(proxies):
    """
    For proxies that didn't match by name, check GeoIP.
    Returns list of proxies in target countries.
    """
    # Collect unique servers
    servers = list(set(
        p.get("server", "") for p in proxies if p.get("server")
    ))
    print(f"  Resolving {len(servers)} servers...", file=sys.stderr)
    server_ip = resolve_ips(servers)
    
    ips = list(set(server_ip.values()))
    print(f"  GeoIP lookup for {len(ips)} IPs...", file=sys.stderr)
    ip_country = batch_geoip(ips)
    
    kept = []
    for p in proxies:
        server = p.get("server", "")
        ip = server_ip.get(server, "")
        country = ip_country.get(ip, "")
        if country in TARGET_COUNTRIES:
            kept.append(p)
    
    print(f"  GeoIP kept {len(kept)}/{len(proxies)}", file=sys.stderr)
    return kept
