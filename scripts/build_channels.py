#!/usr/bin/env python3
"""
Build categorized channel JSON files from iptv-app source data.

Reads:
  - channels_cn.json (CN channels)
  - channels_i18n.json (international channels, country code as string)
  - known_sources.json (URL-only source list per channel_id)
  - candidates.json (server-side health probe with score/alive/rttMs)

Writes:
  - channels/cctv.json
  - channels/satellite.json
  - channels/local.json       (grouped by province)
  - channels/international.json (grouped by country)
  - sources/known.json        (cross-ref sources + alive flag)
  - sources/dead.json         (blacklist from candidates.json alive=false)
  - meta.json                 (totals + version)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# Asia/Shanghai
CST = timezone(timedelta(hours=8))


# CN regex — CCTV1, CCTV1Asia, CGTN, CCTVBilliards etc.
# Matches anything starting with CCTV or CGTN (case-insensitive).
CCTV_PATTERN = re.compile(r"^(CCTV|CGTN)", re.IGNORECASE)

# Satellite keywords — mirror iptv-app/lib/data/channel_filter.dart + Chinese 卫视
SATELLITE_KEYWORDS = [
    "Satellite",
    "TVInternational",
    "卫视",
    "DragonTV",
    "PhoenixTV",  # 凤凰卫视
    "TVBSatellite",  # 港台
]


# ---------------------------------------------------------------------------
# Province keyword table (id + alt_names + logo URL + Chinese name fallback)
# ---------------------------------------------------------------------------
PROVINCE_KEYWORDS: Dict[str, List[str]] = {
    # 直辖市
    "北京": ["Beijing", "BTV", "BRTV", "北京", "bjip", "bjnews", "btv1", "btv2", "Fangshan", "房山"],
    "上海": ["Shanghai", "ShanghaiTV", "上海", "ShanghaiMedia", "STV"],
    "天津": ["Tianjin", "TJTV", "天津"],
    "重庆": ["Chongqing", "重庆"],
    # 省份
    "河北": ["Hebei", "河北"],
    "山西": ["Shanxi", "山西", "sxrtv"],
    "辽宁": ["Liaoning", "辽宁", "Shenyang", "Dalian", "Anshan"],
    "吉林": ["Jilin", "吉林", "JLTV", "Changchun", "Baicheng", "Siping", "Tonghua"],
    "黑龙江": ["Heilongjiang", "黑龙江", "HLJTV", "Harbin"],
    "江苏": ["Jiangsu", "江苏", "JSBC", "Nanjing", "Suzhou", "Wuxi", "Xuzhou", "Changzhou"],
    "浙江": ["Zhejiang", "浙江", "ZJTV", "Hangzhou", "Ningbo", "Wenzhou"],
    "安徽": ["Anhui", "安徽", "Hefei", "Chuzhou", "Huangshan"],
    "福建": ["Fujian", "福建", "FJTV", "Fuzhou", "Xiamen"],
    "江西": ["Jiangxi", "江西", "Nanchang", "Pingxiang"],
    "山东": ["Shandong", "山东", "SDTV", "Jinan", "Qingdao", "QTV", "Yantai", "Weihai", "Jiuquan"],
    "河南": ["Henan", "河南", "Zhengzhou", "Hebi", "Luoyang"],
    "湖北": ["Hubei", "湖北", "Wuhan"],
    "湖南": ["Hunan", "湖南", "Changsha"],
    "广东": ["Guangdong", "Shenzhen", "广州", "深圳", "佛山", "东莞", "广东", "GDTV", "Guangzhou"],
    "海南": ["Hainan", "海南"],
    "四川": ["Sichuan", "成都", "四川", "Liangshan"],
    "贵州": ["Guizhou", "贵州", "Anshun", "安顺"],
    "云南": ["Yunnan", "云南"],
    "陕西": ["Shaanxi", "陕西", "Xian", "Xi'an", "西安"],
    "甘肃": ["Gansu", "甘肃", "Dunhuang", "Lanzhou", "Hezheng", "和政"],
    "青海": ["Qinghai", "青海"],
    "台湾": ["Taiwan", "TaiwanTV", "台视", "中视", "华视", "民视", "TaiwanPlus"],
    # 自治区
    "内蒙古": ["InnerMongolia", "NeiMongol", "NeiMonggol", "内蒙古", "Chifeng"],
    "广西": ["Guangxi", "广西"],
    "西藏": ["Tibet", "西藏"],
    "宁夏": ["Ningxia", "宁夏"],
    "新疆": ["Xinjiang", "新疆"],
    # 特别行政区
    "香港": ["HongKong", "HK", "TVB", "ViuTV", "RTHK", "香港", "Phoenix", "PhoenixChinese", "PhoenixInfo", "凤凰"],
    "澳门": ["Macau", "Macao", "TDM", "澳门"],
}

# National-level channels (CETV, CGTN, etc.) that are not province-specific
NATIONAL_KEYWORDS = [
    "CETV", "CGNT", "CGTN", "ChinaTravel", "ChinaNews", "ChinaWeather",
    "DiscoveringChina", "CHCAction", "CHCHome", "DocumentaryHumanities",
    "GoldenEagle", "YouManCartoon", "VoATV", "BreadTV", "Fengshang",
    "AndoTV", "CND", "金鹰卡通",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_country(country: Any) -> str:
    """i18n channels have country as a simple code string like 'US', 'IN', 'TW'."""
    if isinstance(country, str):
        return country or "unknown"
    if isinstance(country, dict):
        return country.get("code") or country.get("name") or "unknown"
    return "unknown"


def infer_province(channel: Dict[str, Any]) -> Optional[str]:
    """Match province by id + name + alt_names + logo URL."""
    blob_parts: List[str] = [channel.get("id", "") or "", channel.get("name", "") or ""]
    alt_names = channel.get("alt_names") or []
    if isinstance(alt_names, list):
        blob_parts.extend(str(a) for a in alt_names)
    blob_parts.append(channel.get("logo") or "")
    blob_parts.append(channel.get("website") or "")
    text = " ".join(blob_parts)

    for province, keywords in PROVINCE_KEYWORDS.items():
        for kw in keywords:
            if kw and kw in text:
                return province
    return None


def slugify_cctv_group(channel_id: str) -> str:
    """CCTV1.cn -> CCTV1; CCTV4Asia.cn -> CCTV4Asia; CGTN -> CGTN; CCTVBilliards -> CCTVBilliards."""
    # Strip the .cn or @SUFFIX part
    base = channel_id.split(".", 1)[0]
    return base


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_source_data(src_dir: Path) -> Dict[str, Any]:
    cn_channels = load_json(src_dir / "channels_cn.json")
    i18n_blob = load_json(src_dir / "channels_i18n.json")
    if isinstance(i18n_blob, dict) and "channels" in i18n_blob:
        i18n_channels = i18n_blob["channels"]
    else:
        i18n_channels = i18n_blob

    # known_sources: {channel_id: [url1, url2, ...]} or {channel_id: [obj]}
    ks_raw = load_json(src_dir / "known_sources.json")
    known_sources: Dict[str, List[Any]] = {}
    if isinstance(ks_raw, dict):
        for k, v in ks_raw.items():
            if k.startswith("//"):
                continue
            if isinstance(v, list):
                known_sources[k] = v
    elif isinstance(ks_raw, list):
        # defensive
        for entry in ks_raw:
            if isinstance(entry, dict) and entry.get("id"):
                known_sources[entry["id"]] = entry.get("sources", [])

    # candidates: {"fetched_at":..., "channels": {channel_id: [probe, ...]}}
    cand_raw = load_json(src_dir / "candidates.json")
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(cand_raw, dict) and "channels" in cand_raw and isinstance(cand_raw["channels"], dict):
        for k, v in cand_raw["channels"].items():
            if isinstance(v, list):
                candidates[k] = v

    return {
        "cn": cn_channels,
        "i18n": i18n_channels,
        "known": known_sources,
        "candidates": candidates,
        "i18n_meta": {
            "generated_at": i18n_blob.get("_generated_at") if isinstance(i18n_blob, dict) else None,
            "comment": i18n_blob.get("_comment") if isinstance(i18n_blob, dict) else None,
        },
    }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _cctv_sort_key(group_id: str) -> tuple:
    """Sort CCTV groups by numeric prefix if available, else alphabetical."""
    m = re.match(r"^CCTV[-]?(\d+)([A-Za-z].*)?$", group_id)
    if m:
        return (0, int(m.group(1)), m.group(2) or "")
    if group_id.startswith("CCTV"):
        return (1, group_id)
    if group_id.startswith("CGTN"):
        return (2, group_id)
    return (3, group_id)


def build_cctv(cn_channels: List[Dict[str, Any]]) -> Dict[str, Any]:
    cctv = [c for c in cn_channels if CCTV_PATTERN.match(c.get("id", ""))]
    cctv.sort(key=lambda c: c.get("id", ""))
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in cctv:
        g = slugify_cctv_group(c.get("id", ""))
        groups.setdefault(g, []).append(c)
    sorted_groups = dict(sorted(groups.items(), key=lambda kv: _cctv_sort_key(kv[0])))
    return {
        "_meta": {
            "category": "cctv",
            "count": len(cctv),
            "groups_count": len(sorted_groups),
            "generated_at": now_iso(),
        },
        "groups": sorted_groups,
    }


def build_satellite(cn_channels: List[Dict[str, Any]]) -> Dict[str, Any]:
    sats = [
        c for c in cn_channels
        if any(kw in c.get("id", "") or kw in c.get("name", "") for kw in SATELLITE_KEYWORDS)
        or any(
            kw in " ".join(c.get("alt_names") or [])
            for kw in SATELLITE_KEYWORDS
        )
    ]
    sats.sort(key=lambda c: c.get("name", ""))

    # Group by province where possible (same inference as local),
    # fall back to first 2 chars of id for un-attributable sats.
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in sats:
        prov = infer_province(c) or "其他"
        groups.setdefault(prov, []).append(c)

    # Stable ordering: known provinces first, then 其他
    known_order = list(PROVINCE_KEYWORDS.keys())
    sorted_groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in known_order:
        if p in groups:
            sorted_groups[p] = groups[p]
    if "其他" in groups:
        sorted_groups["其他"] = groups["其他"]

    # Dedup English/Chinese duplicate pairs (iptv-org lists some sat channels
    # twice: e.g. BeijingSatelliteTV.cn + 北京卫视.cn). Adds a `merged_ids`
    # field on merged channels preserving both original ids.
    sorted_groups = _dedup_satellite_provinces(sorted_groups)

    total_count = sum(len(v) for v in sorted_groups.values())
    return {
        "_meta": {
            "category": "satellite",
            "count": total_count,
            "provinces": len(sorted_groups),
            "generated_at": now_iso(),
        },
        "provinces": sorted_groups,
    }


# ---------------------------------------------------------------------------
# Satellite dedup helpers (integrated into build_satellite)
# ---------------------------------------------------------------------------

_ENG_ID_PATTERNS = ("Satellite", "TVInternational", "DragonTV")
_CN_SAT_ID_PATTERN = re.compile(r"^[一-龥]+卫视\.cn$")


def _is_eng_named_satellite(channel_id: str) -> bool:
    return any(p in channel_id for p in _ENG_ID_PATTERNS)


def _is_cn_named_satellite(channel_id: str) -> bool:
    return bool(_CN_SAT_ID_PATTERN.match(channel_id))


def _normalize_source_url(src: Any) -> str:
    if isinstance(src, str):
        return src
    if isinstance(src, dict):
        return src.get("url", "")
    return str(src)


def _merge_satellite_pair(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    """Merge secondary into primary. Primary's id wins."""
    merged = dict(primary)
    primary_id = primary.get("id", "")
    secondary_id = secondary.get("id", "")
    if secondary_id and secondary_id != primary_id:
        merged["merged_ids"] = sorted(set(
            ([primary_id] if primary_id else []) +
            ([secondary_id] if secondary_id else []) +
            list(primary.get("merged_ids") or [])
        ))

    # categories: union dedup (preserve order)
    cats: List[Any] = []
    for c in (primary.get("categories") or []) + (secondary.get("categories") or []):
        if c not in cats:
            cats.append(c)
    if cats:
        merged["categories"] = cats

    # alt_names: union dedup (excluding own name)
    alt: List[Any] = []
    for a in (primary.get("alt_names") or []) + (secondary.get("alt_names") or []):
        if a and a not in alt and a != merged.get("name"):
            alt.append(a)
    merged["alt_names"] = alt

    # sources: union dedup by URL
    all_srcs = list(primary.get("sources") or []) + list(secondary.get("sources") or [])
    seen: set = set()
    deduped: List[Any] = []
    for s in all_srcs:
        u = _normalize_source_url(s)
        if u and u not in seen:
            seen.add(u)
            deduped.append(s)
    merged["sources"] = deduped

    # logo / website: take whichever is non-null
    if not merged.get("logo"):
        sec_logo = secondary.get("logo")
        if sec_logo:
            merged["logo"] = sec_logo
    if not merged.get("website"):
        sec_web = secondary.get("website")
        if sec_web:
            merged["website"] = sec_web
    return merged


def _find_cn_partner(eng_channel: Dict[str, Any], province_channels: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    eng_alt = eng_channel.get("alt_names") or []
    eng_id = eng_channel.get("id", "")
    eng_name = eng_channel.get("name", "")

    # Strategy 1: alt_names contains 'XX卫视' → partner is XX卫视.cn
    for alt in eng_alt:
        if "卫视" in alt:
            target_id = f"{alt}.cn"
            for c in province_channels:
                if c.get("id") == target_id and c is not eng_channel:
                    return c

    # Strategy 2: stripped eng_name appears in partner's alt+name
    for c in province_channels:
        if c is eng_channel:
            continue
        cid = c.get("id", "")
        if not _is_cn_named_satellite(cid):
            continue
        calt = c.get("alt_names") or []
        cname = c.get("name", "")
        if any(a in calt for a in eng_alt):
            return c
        stripped = re.sub(r"Satellite\s*(TV|International|Channel)", "", eng_name).strip()
        stripped = re.sub(r"(TVInternational|DragonTV|TV)$", "", stripped).strip()
        if stripped and stripped in " ".join(calt + [cname]):
            return c

    # Strategy 3: province has exactly 1 eng-named and 1 cn-named sat — pair them
    eng_in_prov = [c for c in province_channels if _is_eng_named_satellite(c.get("id", ""))]
    cn_in_prov = [c for c in province_channels if _is_cn_named_satellite(c.get("id", ""))]
    if len(eng_in_prov) == 1 and len(cn_in_prov) == 1 and eng_channel in eng_in_prov:
        return cn_in_prov[0]
    return None


def _dedup_satellite_provinces(provinces: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Merge English/Chinese duplicate pairs within each province bucket."""
    new_provinces: Dict[str, List[Dict[str, Any]]] = {}
    for prov, channels in provinces.items():
        consumed: set = set()
        new_list: List[Dict[str, Any]] = []
        # Process English-named channels first
        for eng in [c for c in channels if _is_eng_named_satellite(c.get("id", ""))]:
            eng_idx = channels.index(eng)
            if eng_idx in consumed:
                continue
            partner = _find_cn_partner(eng, channels)
            if partner is not None:
                partner_idx = channels.index(partner)
                if partner_idx in consumed:
                    new_list.append(eng)
                    continue
                if _is_cn_named_satellite(partner.get("id", "")):
                    primary, secondary = partner, eng
                else:
                    primary, secondary = eng, partner
                new_list.append(_merge_satellite_pair(primary, secondary))
                consumed.add(eng_idx)
                consumed.add(partner_idx)
            else:
                new_list.append(eng)
                consumed.add(eng_idx)
        for idx, c in enumerate(channels):
            if idx not in consumed:
                new_list.append(c)
        new_list.sort(key=lambda c: c.get("name", ""))
        new_provinces[prov] = new_list
    return new_provinces


def build_local(cn_channels: List[Dict[str, Any]]) -> Dict[str, Any]:
    local = [
        c for c in cn_channels
        if not CCTV_PATTERN.match(c.get("id", ""))
        and not any(kw in c.get("id", "") or kw in c.get("name", "") for kw in SATELLITE_KEYWORDS)
        and not any(
            kw in " ".join(c.get("alt_names") or [])
            for kw in SATELLITE_KEYWORDS
        )
    ]
    provinces: Dict[str, List[Dict[str, Any]]] = {}
    national: List[Dict[str, Any]] = []
    unknown_count = 0
    for c in local:
        # Check national first (优先于 province, 避免 CGTN/CETV 误判)
        c_blob = " ".join([
            c.get("id", "") or "",
            c.get("name", "") or "",
            " ".join(c.get("alt_names") or []),
        ])
        if any(kw in c_blob for kw in NATIONAL_KEYWORDS):
            national.append(c)
            continue
        prov = infer_province(c)
        if prov is None:
            prov = "未知"
            unknown_count += 1
        provinces.setdefault(prov, []).append(c)

    # Stable ordering: known provinces first (by Chinese stroke-ish order via predefined list),
    # unknown last. National channel bucket first if non-empty.
    known_order = list(PROVINCE_KEYWORDS.keys())
    sorted_provinces: Dict[str, List[Dict[str, Any]]] = {}
    if national:
        sorted_provinces["全国/中央"] = sorted(national, key=lambda c: c.get("name", ""))
    for p in known_order:
        if p in provinces:
            sorted_provinces[p] = sorted(provinces[p], key=lambda c: c.get("name", ""))
    if "未知" in provinces:
        sorted_provinces["未知"] = sorted(provinces["未知"], key=lambda c: c.get("id", ""))

    return {
        "_meta": {
            "category": "local",
            "count": len(local),
            "known_province": len(local) - unknown_count - len(national),
            "unknown_province": unknown_count,
            "national": len(national),
            "generated_at": now_iso(),
        },
        "provinces": sorted_provinces,
    }


def build_international(i18n_channels: List[Dict[str, Any]]) -> Dict[str, Any]:
    intl_grouped: Dict[str, List[Dict[str, Any]]] = {}
    for c in i18n_channels:
        code = normalize_country(c.get("country"))
        intl_grouped.setdefault(code, []).append(c)
    for code in intl_grouped:
        intl_grouped[code].sort(key=lambda c: c.get("name", ""))
    sorted_intl = dict(sorted(intl_grouped.items()))
    return {
        "_meta": {
            "category": "international",
            "count": len(i18n_channels),
            "countries": len(intl_grouped),
            "generated_at": now_iso(),
        },
        "countries": sorted_intl,
    }


def build_known_sources(
    cn_channels: List[Dict[str, Any]],
    i18n_channels: List[Dict[str, Any]],
    known: Dict[str, List[Any]],
    candidates: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Cross-ref known_sources with candidates health score, flag alive/dead."""
    items: List[Dict[str, Any]] = []
    total_alive = 0
    total_dead = 0
    for cid, urls in known.items():
        if not isinstance(urls, list):
            continue
        probes = candidates.get(cid, [])
        alive_count = sum(1 for p in probes if isinstance(p, dict) and p.get("alive"))
        dead_count = sum(1 for p in probes if isinstance(p, dict) and not p.get("alive"))
        total_alive += alive_count
        total_dead += dead_count

        # best probe = highest score among alive
        best_probe: Optional[Dict[str, Any]] = None
        if probes:
            sorted_probes = sorted(
                [p for p in probes if isinstance(p, dict)],
                key=lambda p: (p.get("alive") is not True, -(p.get("score") or 0)),
            )
            if sorted_probes:
                best_probe = sorted_probes[0]

        items.append({
            "channel_id": cid,
            "urls": urls,
            "probe_count": len(probes),
            "alive_probe_count": alive_count,
            "dead_probe_count": dead_count,
            "best": best_probe,
        })
    items.sort(key=lambda x: x["channel_id"])

    return {
        "_meta": {
            "category": "known_sources",
            "count": len(items),
            "alive_probes": total_alive,
            "dead_probes": total_dead,
            "generated_at": now_iso(),
        },
        "channels": items,
    }


def build_dead_sources(candidates: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for cid, probes in candidates.items():
        if not isinstance(probes, list):
            continue
        for p in probes:
            if isinstance(p, dict) and p.get("alive") is False:
                items.append({
                    "channel_id": cid,
                    "url": p.get("url"),
                    "score": p.get("score"),
                    "rtt_ms": p.get("rttMs"),
                    "error": p.get("error"),
                    "method": p.get("method"),
                })
    items.sort(key=lambda x: (x.get("channel_id", ""), x.get("url", "")))
    return {
        "_meta": {
            "category": "dead_sources",
            "count": len(items),
            "generated_at": now_iso(),
        },
        "channels": items,
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def build_meta(
    cctv_doc: Dict[str, Any],
    sat_doc: Dict[str, Any],
    local_doc: Dict[str, Any],
    intl_doc: Dict[str, Any],
    known_doc: Dict[str, Any],
    dead_doc: Dict[str, Any],
    i18n_meta: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "version": "1.0.0",
        "schema_version": 1,
        "last_updated": now_iso(),
        "totals": {
            "cctv": cctv_doc["_meta"]["count"],
            "satellite": sat_doc["_meta"]["count"],
            "local": local_doc["_meta"]["count"],
            "local_known_province": local_doc["_meta"]["known_province"],
            "local_unknown_province": local_doc["_meta"]["unknown_province"],
            "international": intl_doc["_meta"]["count"],
            "international_countries": intl_doc["_meta"]["countries"],
            "known_sources_channels": known_doc["_meta"]["count"],
            "dead_sources_probes": dead_doc["_meta"]["count"],
        },
        "sources": {
            "iptv_org": "https://github.com/iptv-org/iptv",
            "iptv_app": "local:assets/data",
            "i18n_generated_at": i18n_meta.get("generated_at"),
        },
        "schedule": "weekly Monday 03:00 Asia/Shanghai (cron: 0 19 * * 0 UTC)",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("src_dir", help="iptv-app assets/data directory")
    p.add_argument(
        "--out",
        default=".",
        help="output directory (default: current dir)",
    )
    args = p.parse_args(argv)

    src_dir = Path(args.src_dir).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    if not (src_dir / "channels_cn.json").exists():
        print(f"ERROR: {src_dir}/channels_cn.json not found", file=sys.stderr)
        return 1

    data = load_source_data(src_dir)
    cn_channels = data["cn"]
    i18n_channels = data["i18n"]
    known = data["known"]
    candidates = data["candidates"]

    cctv_doc = build_cctv(cn_channels)
    sat_doc = build_satellite(cn_channels)
    local_doc = build_local(cn_channels)
    intl_doc = build_international(i18n_channels)
    known_doc = build_known_sources(cn_channels, i18n_channels, known, candidates)
    dead_doc = build_dead_sources(candidates)
    meta = build_meta(cctv_doc, sat_doc, local_doc, intl_doc, known_doc, dead_doc, data["i18n_meta"])

    dump_json(out_dir / "channels" / "cctv.json", cctv_doc)
    dump_json(out_dir / "channels" / "satellite.json", sat_doc)
    dump_json(out_dir / "channels" / "local.json", local_doc)
    dump_json(out_dir / "channels" / "international.json", intl_doc)
    dump_json(out_dir / "sources" / "known.json", known_doc)
    dump_json(out_dir / "sources" / "dead.json", dead_doc)
    dump_json(out_dir / "meta.json", meta)

    print("OK")
    print(json.dumps(meta["totals"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())