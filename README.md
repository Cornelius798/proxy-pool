# proxy-pool

自动维护的免费代理池，给 Tryingopen-2api 供代理。

- **pool.txt**：当前活着的代理，每行 `scheme://ip:port`（http/https/socks4/socks5）
- 每 6 小时自动刷新：抓新 + 复验旧 + 踢掉死的
- QwenPaw 拉取：`curl -s https://raw.githubusercontent.com/Cornelius798/proxy-pool/main/pool.txt -o data/proxies.txt`
