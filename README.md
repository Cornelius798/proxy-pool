# proxy-pool

免费节点聚合池 - 每日自动更新。

## 订阅地址

- Clash: `https://cornelius798.github.io/proxy-pool/clash.yaml`

## 说明

- 每天 02:00 UTC 自动抓取 7 个 GitHub 免费节点源
- 按 server:port 去重
- 只保留香港、日本、新加坡、台湾、美国节点（名字匹配 + GeoIP 兜底）
- TCP 连通性检测，踢掉死节点
- 按延迟排序

## 历史

2026-10-14 前：手工验证代理池（已停用，相关文件保留在 `legacy/`）
2026-10-18 起：自动聚合爬虫
