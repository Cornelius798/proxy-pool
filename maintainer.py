#!/usr/bin/env python3
"""Proxy pool maintainer: 增量更新代理池。
流程：
1. 从公开源抓取新候选
2. 加载现有 pool.txt
3. 合并去重后全部复验（TCP + HTTP代理可用性）
4. 只保留活着的，有变化才写回 pool.txt
5. 由外层脚本负责 git commit + push
"""
import concurrent.futures, os, re, socket, sys, time, urllib.request

SOURCES = [
    # http
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",
    # https
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/https.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/https/data.txt",
    # socks4
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks4/data.txt",
    # socks5
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks5.txt",
]

TIMEOUT = 6
POOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool.txt")

def fetch_source(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode(errors="ignore")
    except Exception as e:
        print(f"  source fail {url[:60]}: {e}", file=sys.stderr)
        return ""

def fetch_tg_channel(channel):
    """抓 TG 公开频道预览，返回 (scheme, ip, port) 列表。"""
    import urllib.request
    url = f"https://t.me/s/{channel}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            t = r.read().decode(errors="ignore")
    except Exception as e:
        print(f"  tg fail {channel}: {e}", file=sys.stderr)
        return []
    out = []
    for m in re.finditer(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})", t):
        ip, port = m.group(1), int(m.group(2))
        if ip.startswith(("0.", "127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.")):
            continue
        # 端口推断类型：1080=socks5, 443=https, 其它跳过（3478/3479 是 TURN）
        if port == 1080:
            out.append(f"socks5://{ip}:{port}")
        elif port == 443:
            out.append(f"https://{ip}:{port}")
    return out

TG_CHANNELS = ["otcfxq"]  # OTC分享频道

def scheme_of_source(url):
    """从源 URL 推断代理类型。"""
    u = url.lower()
    if "socks5" in u: return "socks5"
    if "socks4" in u: return "socks4"
    if "https" in u and "http.txt" not in u: return "https"
    return "http"

def parse_proxies(text, scheme="http"):
    out = set()
    for m in re.finditer(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})", text):
        ip, port = m.group(1), int(m.group(2))
        if 1 <= port <= 65535 and not ip.startswith(("0.", "127.", "10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.")):
            out.add(f"{scheme}://{ip}:{port}")
    return out

def load_pool():
    pool = set()
    if os.path.exists(POOL_FILE):
        with open(POOL_FILE) as f:
            for line in f:
                line = line.strip()
                # pool.txt 格式: scheme://ip:port
                m = re.search(r"((?:https?|socks[45])://\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})", line)
                if m:
                    pool.add(m.group(1))
    return pool

def test_proxy(proxy):
    """TCP + 协议可用性。http/https 走 httpbin 验；socks 只做 TCP（reqwest 运行时自检）。
    返回 (proxy, ms) 或 None。"""
    m = re.match(r"(https?|socks[45])://(\d{1,3}(?:\.\d{1,3}){3}):(\d+)", proxy)
    if not m:
        return None
    scheme, ip, port = m.group(1), m.group(2), m.group(3)
    t0 = time.time()
    try:
        s = socket.create_connection((ip, int(port)), timeout=TIMEOUT)
        s.close()
    except Exception:
        return None
    tcp_ms = (time.time() - t0) * 1000
    if tcp_ms > 4000:
        return None
    # socks 只验 TCP 连通（应用层由 Tryingopen-2api 运行时健康检查）
    if scheme in ("socks4", "socks5"):
        return (proxy, int(tcp_ms))
    # http/https 走 httpbin 验代理可用性
    try:
        ph = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
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
    # 1. 抓新候选
    print("Fetching new candidates...", flush=True)
    candidates = set()
    for url in SOURCES:
        scheme = scheme_of_source(url)
        found = parse_proxies(fetch_source(url), scheme)
        print(f"  [{scheme}] {url[:50]}: {len(found)}", flush=True)
        candidates |= found

    # 1b. TG 频道
    for ch in TG_CHANNELS:
        found = fetch_tg_channel(ch)
        print(f"  [tg:{ch}]: {len(found)}", flush=True)
        candidates |= set(found)

    # 2. 加载旧池
    old_pool = load_pool()
    print(f"Existing pool: {len(old_pool)}", flush=True)

    # 3. 合并
    all_proxies = old_pool | candidates
    print(f"Total to verify: {len(all_proxies)} (old {len(old_pool)} + new {len(candidates - old_pool)})", flush=True)

    # 4. 全部复验
    good = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=150) as ex:
        futures = {ex.submit(test_proxy, p): p for p in all_proxies}
        done = 0
        for f in concurrent.futures.as_completed(futures):
            done += 1
            r = f.result()
            if r:
                good.append(r)
            if done % 500 == 0 or done == len(all_proxies):
                print(f"  ... {done}/{len(all_proxies)} verified, {len(good)} alive", flush=True)

    # 5. 写回（按延迟排序）
    good.sort(key=lambda x: x[1])
    new_set = set(p for p, _ in good)
    died = old_pool - new_set
    born = new_set - old_pool
    print(f"Alive: {len(good)} (died: {len(died)}, new: {len(born)})", flush=True)

    with open(POOL_FILE, "w") as f:
        for proxy, ms in good:
            f.write(f"{proxy}\n")

    # 6. 输出变化摘要给外层脚本判断是否 push
    print(f"CHANGED={'yes' if new_set != old_pool else 'no'}")
    print(f"POOL_SIZE={len(good)}")

if __name__ == "__main__":
    main()
