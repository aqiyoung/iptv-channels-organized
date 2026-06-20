# iptv-channels-organized

独立托管 [aqiyoung/iptv-app](https://github.com/aqiyoung/iptv-app) 的频道分类数据。  
原始频道列表（CN + i18n）来自 app 仓库的 `assets/data/`，本 repo 只做**分类、分组、源健康度整理**，不存储任何流媒体内容。

## 用途

- 给 [iptv-app](https://github.com/aqiyoung/iptv-app) 提供结构化频道索引（CCTV / 卫视 / 地方 / 国际）。
- 与 app 仓库**解耦**：分类数据单独迭代，不影响 app 的 CI / Release 节奏。
- 每周一 03:00 Asia/Shanghai 自动从 iptv-app 仓库同步一次（GitHub Actions cron）。

## 数据来源

| 数据 | 原始位置 | License |
|------|---------|---------|
| 频道元数据 (CN) | [iptv-org/iptv](https://github.com/iptv-org/iptv) `channels_cn.json` | CC0 1.0 |
| 频道元数据 (i18n) | [iptv-org/iptv](https://github.com/iptv-org/iptv) `channels_*.json` (多国) | CC0 1.0 |
| 自建源 (known_sources) | iptv-app 内手工聚合 | 仅供学习研究 |
| 健康度探针 (candidates) | iptv-app server-side 探测 | 仅供学习研究 |

**本仓库不存储任何流媒体内容本身**，仅记录公开的 m3u8 URL 字符串。流媒体内容版权归原始电视台 / 内容方所有。

## 法律声明

> 本仓库仅供学习、研究、个人测试使用。  
> 不得用于商业用途、转播、再分发或任何违反当地法律法规的行为。  
> 仓库维护者不为下游使用承担任何法律责任。  
> 如有版权方异议，请通过 GitHub Issue 联系，会在 24 小时内移除相关条目。

## 同步策略

- **频率**：每周一 03:00 Asia/Shanghai (= UTC 周日 19:00, cron `0 19 * * 0`)
- **触发源**：[GitHub Actions](../../actions/workflows/weekly_sync.yml)
- **流程**：
  1. shallow clone `aqiyoung/iptv-app` 到 runner
  2. 跑 `scripts/build_channels.py` 生成 `channels/*.json` + `sources/*.json` + `meta.json`
  3. `git diff` 若有变化则 `git commit && git push`
- **手动触发**：Actions 页面 → "Weekly Channel Sync" → "Run workflow"

## 目录结构

```
iptv-channels-organized/
├── README.md                       # 本文件
├── meta.json                       # 顶层版本号 + 总数 + 分类计数
├── channels/
│   ├── cctv.json                   # 央视 (CCTV1~CCTV17 + CGTN + 数字频道)
│   ├── satellite.json              # 卫星频道 (按省分组)
│   ├── local.json                  # 地方频道 (按省 + 全国/中央 + 未知)
│   └── international.json          # 国际频道 (按 country code)
├── sources/
│   ├── known.json                  # 健康度评分后的可用源
│   └── dead.json                   # 黑名单 (从 candidates.json alive=false 派生)
├── scripts/
│   └── build_channels.py           # 一次性脚本, 从 iptv-app 提取 + 分类
└── .github/workflows/
    └── weekly_sync.yml             # CI workflow
```

## Schema 说明

### `meta.json`

```json
{
  "version": "1.0.0",                  // 本仓库数据版本
  "schema_version": 1,                 // JSON schema 版本
  "last_updated": "ISO-8601",          // 最后生成时间 (Asia/Shanghai)
  "totals": {
    "cctv": 44,
    "satellite": 52,
    "local": 102,
    "local_known_province": 83,
    "local_unknown_province": 1,
    "local_national": 18,
    "international": 133,
    "international_countries": 8,
    "known_sources_channels": 135,
    "dead_sources_probes": 133
  },
  "sources": {
    "iptv_org": "https://github.com/iptv-org/iptv",
    "iptv_app": "local:assets/data",
    "i18n_generated_at": "..."
  },
  "schedule": "weekly Monday 03:00 Asia/Shanghai (cron: 0 19 * * 0 UTC)"
}
```

### `channels/cctv.json`

```json
{
  "_meta": { "category": "cctv", "count": 44, "groups_count": 41, "generated_at": "..." },
  "groups": {
    "CCTV1": [ { "id": "CCTV1.cn", "name": "CCTV-1", "country": "CN", ... }, ... ],
    "CCTV4Asia": [ ... ],
    "CGTN": [ ... ],
    ...
  }
}
```

- `groups` 键名 = CCTV 编号 (CCTV1 ~ CCTV17 + 4K/8K 等变体) 或 `CGTN*`。
- 顺序按 CCTV 编号数字升序，再按字母变体排列。

### `channels/satellite.json`

```json
{
  "_meta": { "category": "satellite", "count": 52, "provinces": 31, "generated_at": "..." },
  "provinces": {
    "北京": [ { "id": "BeijingSatelliteTV.cn", ... }, ... ],
    "浙江": [ { "id": "ZhejiangSatelliteTV.cn", ... }, { "id": "..." } ],
    ...
    "其他": [ ... ]   // 推断不出省份的卫星频道
  }
}
```

- `provinces` 键名 = 中国省/直辖市/自治区名（中文）。
- 推断策略（按优先级）：`id` 关键词 → `alt_names` 关键词 → `name` 中文 → 推断不出则归入 `其他`。

### `channels/local.json`

```json
{
  "_meta": {
    "category": "local",
    "count": 102,
    "known_province": 83,
    "unknown_province": 1,
    "national": 18,        // 全国/中央级 (CETV/CGTN/CHC 等)
    "generated_at": "..."
  },
  "provinces": {
    "全国/中央": [ ... ],   // CETV/CGTN 等国家级
    "北京": [ ... ],
    "上海": [ ... ],
    ...
    "未知": [ ... ]        // 推断不出省份的地方频道
  }
}
```

### `channels/international.json`

```json
{
  "_meta": { "category": "international", "count": 133, "countries": 8, "generated_at": "..." },
  "countries": {
    "US": [ { "id": "...", "name": "...", "country": "US", "region": "North America", ... }, ... ],
    "IN": [ ... ],
    "TW": [ ... ],   // 注意: 含 台湾频道 (数据源决定, 不做政治判断)
    "DE": [ ... ],
    "GB": [ ... ],
    "FR": [ ... ],
    "RU": [ ... ],
    "JP": [ ... ]
  }
}
```

### `sources/known.json`

```json
{
  "_meta": { "category": "known_sources", "count": 135, "alive_probes": 53, "dead_probes": 133, "generated_at": "..." },
  "channels": [
    {
      "channel_id": "CCTV1.cn",
      "urls": [ "http://...", "http://..." ],
      "probe_count": 2,
      "alive_probe_count": 2,
      "dead_probe_count": 0,
      "best": { "url": "...", "alive": true, "score": 0.95, "rttMs": 42, "error": null, "method": "iptv_org" }
    },
    ...
  ]
}
```

- `best`：选取得分最高的 alive probe（若无 alive 则取最高分 probe）。
- `probe_count == 0` 表示 channels 未被服务端探测。

### `sources/dead.json`

```json
{
  "_meta": { "category": "dead_sources", "count": 133, "generated_at": "..." },
  "channels": [
    {
      "channel_id": "BeijingSatelliteTV.cn",
      "url": "http://...",
      "score": 0.0,
      "rtt_ms": 212,
      "error": "URLError: <urlopen error [Errno 111] Connection refused>",
      "method": "skipped"
    },
    ...
  ]
}
```

- 仅列出 `candidates.json` 中 `alive=false` 的探针。
- 供 app 端做 fail-fast 黑名单过滤。

## 本地开发

```bash
# 从 iptv-app 源数据生成（不会 commit，只是验证）
python3 scripts/build_channels.py /path/to/iptv-app/assets/data --out /tmp/test-out

# 查看顶层 meta
cat meta.json | jq .

# 按国家筛选国际频道
jq '.countries.US | length' channels/international.json
```

## App 集成方式（待定）

- 计划在 iptv-app 加一个 `lib/data/channels_organized_loader.dart`，从本 repo 拉 `meta.json` + 对应分类 JSON。
- 拉取策略：app 启动时 + 周一后台 sync 时。
- 缓存：本地 hive box + 上次更新日期，3 天内不重复拉取。

## 隐私

- 本仓库**无任何个人信息**。
- 仅记录公开 m3u8 URL 字符串，不存储实际视频流。
- 频道元数据全部来自 [iptv-org/iptv](https://github.com/iptv-org/iptv)（CC0 1.0）+ 自建源。

---

最后更新：见 [meta.json](./meta.json) → `last_updated`。