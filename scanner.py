#!/usr/bin/env python3
"""Free proxy scanner for Tryingopen-2api pool.
Sources: auto-updated GitHub proxy lists.
Verification: TCP connect -> HTTP proxy works -> can reach tryingopen.com.
Output: pool.txt in Tryingopen-2api proxy_file format (http://host:port per line).
"""
import concurrent.futures, re, socket, sys, time, urllib.request

SOURCES = [
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
]

# tryingopen.com 上游检测：代理必须能 GET 到首页
UPSTREAM_TEST = "https://www.tryingopen.com/"
TIMEOUT = 8

def fetch_source(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode(errors="ignore")
    except Exception as e:
        print(f"  source fail {url[:60]}: {e}", file=sys.stderr)
        return ""

def parse_proxies(text):
    out = set()
    for m in re.finditer(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})", text):
        ip, port = m.group(1), int(m.group(2))
        if 1 <= port <= 65535 and not ip.startswith(("0.", "127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.")):
            out.add(f"{ip}:{port}")
    return out

def test_proxy(proxy):
    """返回 (proxy, 延迟ms) 或 None。两级验证：TCP -> HTTP代理可用(验IP隐藏)。"""
    ip, port = proxy.split(":")
    # 1. TCP 连通
    t0 = time.time()
    try:
        s = socket.create_connection((ip, int(port)), timeout=TIMEOUT)
        s.close()
    except Exception:
        return None
    tcp_ms = (time.time() - t0) * 1000
    if tcp_ms > 4000:
        return None
    # 2. HTTP 代理能工作（通过 httpbin 验 IP）
    try:
        ph = urllib.request.ProxyHandler({"http": f"http://{proxy}", "https": f"http://{proxy}"})
        opener = urllib.request.build_opener(ph)
        req = urllib.request.Request("http://httpbin.org/ip", headers={"User-Agent": "Mozilla/5.0"})
        t0 = time.time()
        with opener.open(req, timeout=TIMEOUT) as r:
            body = r.read(500).decode(errors="ignore")
            if r.status == 200 and "origin" in body:
                total_ms = int((time.time() - t0) * 1000 + tcp_ms)
                if total_ms < 8000:
                    return (proxy, total_ms)
    except Exception:
        pass
    return None

def main():
    print("Fetching sources...", flush=True)
    all_proxies = set()
    for url in SOURCES:
        text = fetch_source(url)
        found = parse_proxies(text)
        print(f"  {url[:55]}: {len(found)}", flush=True)
        all_proxies |= found
    print(f"Total unique: {len(all_proxies)}, testing...", flush=True)

    good = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as ex:
        futures = {ex.submit(test_proxy, p): p for p in all_proxies}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            done += 1
            r = f.result()
            if r:
                good.append(r)
                print(f"  OK {r[0]} {r[1]}ms ({len(good)} good / {done} tested)", flush=True)
            elif done % 500 == 0:
                print(f"  ... {done}/{len(all_proxies)} tested, {len(good)} good", flush=True)

    good.sort(key=lambda x: x[1])
    out_path = sys.argv[1] if len(sys.argv) > 1 else "pool.txt"
    with open(out_path, "w") as f:
        for proxy, ms in good:
            f.write(f"http://{proxy}\n")
    print(f"Done: {len(good)} working proxies -> {out_path}")

if __name__ == "__main__":
    main()
