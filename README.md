# free-nodes-crawler

Aggregates free proxy nodes from public GitHub sources into a single clash subscription.

## Sources

- free18/v2ray (daily updated, via ghfast.top)
- ermaozi/get_subscribe
- ovmvo/FreeSub
- lucas8864/freesub
- peasoft/NoMoreWalls
- Au1rxx/free-vpn-subscriptions
- anaer/Sub

## Usage

The aggregated subscription is available at:
- `https://cornelius798.github.io/free-nodes-crawler/clash.yaml`

## How it works

GitHub Action runs daily, fetches all sources, merges and deduplicates nodes, outputs `clash.yaml` to GitHub Pages.
