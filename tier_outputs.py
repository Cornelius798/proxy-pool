#!/usr/bin/env python3
"""
Merge subs-check liveness results + the HTTP pipeline into tiered
Clash YAML outputs. Liveness only — no speed test anywhere.

Inputs (all in the repo workdir on the runner):
  output-full/all.yaml  subs-check run, alive-test-url=gstatic
                        (every node that answers at all)
  output-ai/all.yaml    subs-check run, alive-test-url=https://opencode.ai
                        (nodes that can reach opencode.ai)
  http_nodes.json       scan_simple.py: HTTP forward proxies that
                        passed the TLS tunnel check to opencode.ai

Outputs:
  clash.yaml        full pool: all alive nodes, HK/JP/SG/TW/US only
  residential.yaml  residential subset of the full pool
                    (ip-api.com hosting=false and mobile=false)
  ai.yaml           nodes that can reach opencode.ai (AI-run nodes
                    + all http_nodes.json nodes, deduped)

A missing all.yaml is treated as an empty list: subs-check skips
saving when a round yields zero nodes.
"""

import json
import sys

import yaml

from scan_simple import (
    TARGET_COUNTRIES,
    _resolve_server,
    batch_geoip,
    is_residential,
)


def load_subs_check_nodes(path):
    """Read a subs-check all.yaml, return [node dict, ...].

    Full node configs are kept verbatim (type, server, port, uuid,
    cipher, tls settings, ...); only the name may be adjusted later
    for uniqueness within a tier file.
    """
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"  NOTE {path} missing (round yielded zero nodes), "
              "treating as empty", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  FAIL {path}: {e}", file=sys.stderr)
        return []
    if not isinstance(doc, dict) or not isinstance(doc.get("proxies"), list):
        print(f"  SKIP {path}: no proxies list", file=sys.stderr)
        return []
    nodes = []
    for node in doc["proxies"]:
        if not isinstance(node, dict):
            continue
        server = node.get("server")
        port = node.get("port")
        ntype = node.get("type")
        if not server or not ntype:
            continue
        try:
            port = int(port)
        except (TypeError, ValueError):
            continue
        if not 1 <= port <= 65535:
            continue
        nodes.append(dict(node))
    return nodes


def load_http_nodes(path="http_nodes.json"):
    """Read scan_simple.py's http_nodes.json, return [node dict, ...]."""
    try:
        with open(path) as f:
            nodes = json.load(f)
    except FileNotFoundError:
        print(f"  NOTE {path} missing, treating as empty", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  FAIL {path}: {e}", file=sys.stderr)
        return []
    if not isinstance(nodes, list):
        return []
    out = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("server") or not n.get("type"):
            continue
        try:
            port = int(n.get("port"))
        except (TypeError, ValueError):
            continue
        if not 1 <= port <= 65535:
            continue
        out.append(dict(n))
    return out


def prepare(nodes, geo):
    """Dedupe by (type, server, port), resolve servers to IPs, apply
    the region filter. Returns [(node, ip), ...] in input order.

    `geo` is the {ip: {...}} map from batch_geoip, or None when GeoIP
    is unreachable — then the region filter is skipped (fail-open,
    same as scan_simple.py's filter_by_region).
    """
    seen = set()
    resolved = {}
    out = []
    for node in nodes:
        key = (str(node["type"]), str(node["server"]).lower(),
               int(node["port"]))
        if key in seen:
            continue
        seen.add(key)
        server = str(node["server"])
        if server not in resolved:
            resolved[server] = _resolve_server(server)
        ip = resolved[server]
        if ip is None:
            print(f"  SKIP unresolvable server: {server}", file=sys.stderr)
            continue
        if geo is not None:
            country = geo.get(ip, {}).get("country", "")
            if country not in TARGET_COUNTRIES:
                continue
        out.append((node, ip))
    return out


def write_tier_yaml(path, prepared, tier_note):
    """Write one tier file: full node configs + an auto url-test group.

    Node names are made unique within the file (Clash requires unique
    proxy names); the rest of each node config is written verbatim.
    An empty tier writes `proxies: []` with no proxy-groups.
    """
    nodes = []
    used = set()
    for node, _ip in prepared:
        name = str(node.get("name") or
                   f"{node['type']}_{node['server']}_{node['port']}")
        base = name
        n = 2
        while name in used:
            name = f"{base} #{n}"
            n += 1
        used.add(name)
        out = dict(node)
        out["name"] = name
        nodes.append(out)

    output = {"proxies": nodes}
    if nodes:
        proxy_names = [p["name"] for p in nodes]
        output["proxy-groups"] = [
            {
                "name": "auto",
                "type": "url-test",
                "proxies": proxy_names,
                "url": "http://www.gstatic.com/generate_204",
                "interval": 300,
            },
        ]

    with open(path, "w") as f:
        f.write("# Proxy pool: liveness-checked nodes "
                "(subs-check + HTTP pipeline), no speed test, "
                "HK/JP/SG/TW/US only\n")
        f.write(f"# Tier: {tier_note}\n")
        f.write(f"# Working: {len(nodes)}\n\n")
        yaml.safe_dump(output, f, sort_keys=False)

    print(f"Wrote {path}: {len(nodes)} nodes", file=sys.stderr)


def main():
    full_nodes = load_subs_check_nodes("output-full/all.yaml")
    ai_nodes = load_subs_check_nodes("output-ai/all.yaml")
    http_nodes = load_http_nodes("http_nodes.json")
    print(f"Loaded: subs-check full={len(full_nodes)}, "
          f"AI={len(ai_nodes)}, http={len(http_nodes)}", file=sys.stderr)

    # One GeoIP batch over every unique server IP (country for the
    # region filter, hosting/mobile for the residential split).
    servers = set()
    for node in full_nodes + ai_nodes + http_nodes:
        servers.add(str(node["server"]))
    ips = sorted({ip for s in servers
                  if (ip := _resolve_server(s)) is not None})
    print(f"GeoIP lookup: {len(ips)} unique IPs...", file=sys.stderr)
    geo = batch_geoip(ips)
    if geo is None:
        print("  WARNING: GeoIP unreachable, region filter disabled "
              "for this run (residential tier will be empty)",
              file=sys.stderr)
        geo = None

    # Full tier: subs-check alive nodes first, then HTTP nodes
    # (a duplicate http node keeps the subscription version).
    full = prepare(full_nodes + http_nodes, geo)
    print(f"Full tier: {len(full)}", file=sys.stderr)

    # AI tier: opencode.ai-alive nodes + every HTTP node (each passed
    # the TLS tunnel check to opencode.ai in scan_simple.py).
    ai = prepare(ai_nodes + http_nodes, geo)
    print(f"AI tier: {len(ai)}", file=sys.stderr)

    # Residential tier: home broadband exits only.
    residential = [(node, ip) for node, ip in full
                   if is_residential((geo or {}).get(ip))]
    print(f"Residential tier: {len(residential)}", file=sys.stderr)

    write_tier_yaml("clash.yaml", full,
                    "full pool: all liveness-checked nodes")
    write_tier_yaml("residential.yaml", residential,
                    "residential subset: hosting=false and mobile=false "
                    "per ip-api.com (home broadband exits only)")
    write_tier_yaml("ai.yaml", ai,
                    "AI tier: nodes that can reach opencode.ai "
                    "(alive check against https://opencode.ai)")


if __name__ == "__main__":
    main()
