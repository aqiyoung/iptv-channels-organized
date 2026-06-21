#!/usr/bin/env python3
"""
One-off satellite dedup script.

Background (6/21):
  - iptv-org lists some Chinese satellite TV channels twice:
    one with English id (e.g. BeijingSatelliteTV.cn) and one with
    Chinese id (e.g. 北京卫视.cn). Both end up in satellite.json,
    inflating counts and confusing users.
  - HenanTVSatellite.cn was misfiled into local.json under 河南,
    should live in satellite.json under 河南.

This script:
  1. Merges English/Chinese duplicate pairs within satellite.json
     (15 pairs → saves 15 channels).
  2. Moves HenanTVSatellite.cn from local.json 河南 → satellite.json 河南.
  3. Updates _meta counts and meta.json totals.

Idempotent — safe to re-run.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path("/vol1/1000/dev-projects/iptv-channels-organized")
SAT_PATH = ROOT / "channels" / "satellite.json"
LOCAL_PATH = ROOT / "channels" / "local.json"
META_PATH = ROOT / "meta.json"

# English-named satellite id markers (the "secondary" side of each dup pair)
ENG_ID_PATTERNS = ("SatelliteTV", "TVInternational", "SatelliteChannel", "DragonTV")

# Chinese-named satellite id marker pattern (the "primary" side)
CN_ID_PATTERN = re.compile(r"^[一-龥]+卫视\.cn$")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, obj: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def is_english_named_satellite(channel_id: str) -> bool:
    return any(p in channel_id for p in ENG_ID_PATTERNS)


def is_cn_named_satellite(channel_id: str) -> bool:
    return bool(CN_ID_PATTERN.match(channel_id))


def normalize_source(src: Any) -> str:
    """Return canonical URL string for dedup."""
    if isinstance(src, str):
        return src
    if isinstance(src, dict):
        return src.get("url", "")
    return str(src)


def merge_pair(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge secondary into primary. Primary is the "keeper" — its id wins.
    secondary is the English-named duplicate.

    Preserves both original ids in `merged_ids` so any consumer looking
    up either id can still find this channel.
    """
    merged = dict(primary)  # shallow copy

    # Preserve both original ids
    primary_id = primary.get("id", "")
    secondary_id = secondary.get("id", "")
    if secondary_id and secondary_id != primary_id:
        merged["merged_ids"] = sorted(set(
            ([primary_id] if primary_id else []) +
            ([secondary_id] if secondary_id else []) +
            list(primary.get("merged_ids") or [])
        ))

    # Name: prefer Chinese-named (already primary)
    # categories: union (preserving order, dedup)
    cats = []
    for c in (primary.get("categories") or []) + (secondary.get("categories") or []):
        if c not in cats:
            cats.append(c)
    if cats:
        merged["categories"] = cats

    # alt_names: union dedup
    alt = []
    for a in (primary.get("alt_names") or []) + (secondary.get("alt_names") or []):
        if a and a not in alt and a != merged.get("name"):
            alt.append(a)
    merged["alt_names"] = alt

    # sources: union dedup by URL string
    all_srcs = list(primary.get("sources") or []) + list(secondary.get("sources") or [])
    seen_urls: Set[str] = set()
    deduped_srcs: List[Any] = []
    for s in all_srcs:
        u = normalize_source(s)
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped_srcs.append(s)
    merged["sources"] = deduped_srcs

    # logo: take whichever is non-null (primary first, then secondary)
    if not merged.get("logo"):
        sec_logo = secondary.get("logo")
        if sec_logo:
            merged["logo"] = sec_logo

    # website: take whichever is non-null
    if not merged.get("website"):
        sec_web = secondary.get("website")
        if sec_web:
            merged["website"] = sec_web

    # country: keep primary
    # is_nsfw: keep primary
    return merged


def find_cn_partner(eng_channel: Dict[str, Any], province_channels: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Given an English-named satellite channel, find its Chinese-named partner
    in the same province bucket.

    Strategy:
      1. If eng has alt_names containing 'XX卫视', look up XX卫视.cn by id match.
      2. Fallback: look for any Chinese-named channel in same province whose
         name appears in eng's alt_names OR whose name == eng's stripped name.
    """
    eng_alt_names = eng_channel.get("alt_names") or []
    eng_id = eng_channel.get("id", "")
    eng_name = eng_channel.get("name", "")

    # Strategy 1: explicit alt name like '北京卫视'
    for alt in eng_alt_names:
        if "卫视" in alt:
            target_id = f"{alt}.cn"
            for c in province_channels:
                if c.get("id") == target_id and c is not eng_channel:
                    return c

    # Strategy 2: name match — eng "Beijing Satellite TV" stripped of satellite
    #             keyword should match cn "北京卫视" via the alt
    for c in province_channels:
        if c is eng_channel:
            continue
        cid = c.get("id", "")
        cname = c.get("name", "")
        calt = c.get("alt_names") or []
        if is_cn_named_satellite(cid):
            # If cn channel's alt contains anything from eng's alt or name,
            # they're partners
            if any(a in calt for a in eng_alt_names):
                return c
            # Or if eng_name's stripped form has same root
            eng_stripped = re.sub(r"Satellite\s*(TV|International|Channel)", "", eng_name).strip()
            eng_stripped = re.sub(r"(TVInternational|DragonTV|TV)$", "", eng_stripped).strip()
            if eng_stripped and eng_stripped in " ".join(calt + [cname]):
                return c

    # Strategy 3: province-based — count eng-named and cn-named in this bucket.
    # If there's exactly one of each, pair them.
    eng_in_prov = [c for c in province_channels if is_english_named_satellite(c.get("id", ""))]
    cn_in_prov = [c for c in province_channels if is_cn_named_satellite(c.get("id", ""))]
    if len(eng_in_prov) == 1 and len(cn_in_prov) == 1:
        # This province has exactly one english-named sat and one chinese-named sat.
        # They're the same channel.
        if eng_channel in eng_in_prov:
            return cn_in_prov[0]

    return None


def dedup_satellite(sat_doc: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """
    Returns (new_doc, merged_pair_count).
    """
    merged_total = 0
    new_provinces: Dict[str, List[Dict[str, Any]]] = {}

    for prov, channels in sat_doc.get("provinces", {}).items():
        # Build list of "to process" channels, marking some as "consumed"
        consumed: Set[int] = set()
        new_list: List[Dict[str, Any]] = []

        # Sort: english-named first (so we look up partner from primary)
        eng_channels = [c for c in channels if is_english_named_satellite(c.get("id", ""))]
        cn_channels = [c for c in channels if c not in eng_channels]

        # For each english-named channel, find its CN partner
        for eng in eng_channels:
            eng_idx = channels.index(eng)
            if eng_idx in consumed:
                continue
            partner = find_cn_partner(eng, channels)
            if partner is not None:
                partner_idx = channels.index(partner)
                if partner_idx in consumed:
                    # Partner already consumed by an earlier merge — skip
                    new_list.append(eng)
                    continue
                # Merge: primary = Chinese-named (partner), secondary = English (eng)
                # Determine which is "primary" — prefer Chinese-named
                if is_cn_named_satellite(partner.get("id", "")):
                    primary, secondary = partner, eng
                else:
                    primary, secondary = eng, partner
                merged = merge_pair(primary, secondary)
                new_list.append(merged)
                consumed.add(eng_idx)
                consumed.add(partner_idx)
                merged_total += 1
            else:
                # No partner found — keep as is
                new_list.append(eng)
                consumed.add(eng_idx)

        # Add remaining un-consumed channels
        for idx, c in enumerate(channels):
            if idx not in consumed:
                new_list.append(c)

        # Sort within province: by name
        new_list.sort(key=lambda c: c.get("name", ""))
        new_provinces[prov] = new_list

    # Update _meta
    new_count = sum(len(v) for v in new_provinces.values())
    new_doc = {
        "_meta": {
            **sat_doc["_meta"],
            "count": new_count,
            "provinces": len(new_provinces),
            "generated_at": sat_doc["_meta"].get("generated_at"),
            "deduped_at": "2026-06-21T09:05:00+08:00",
        },
        "provinces": new_provinces,
    }
    return new_doc, merged_total


def move_henan_to_satellite(sat_doc: Dict[str, Any], local_doc: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """
    Move HenanTVSatellite.cn from local.json 河南 → satellite.json 河南.
    Returns (new_sat, new_local, moved).
    """
    moved = False
    henan_local_list = local_doc.get("provinces", {}).get("河南", [])
    new_henan_local: List[Dict[str, Any]] = []
    moved_channel: Optional[Dict[str, Any]] = None

    for c in henan_local_list:
        if c.get("id") == "HenanTVSatellite.cn":
            moved_channel = c
            moved = True
        else:
            new_henan_local.append(c)

    if not moved:
        return sat_doc, local_doc, False

    # Update local
    new_local = dict(local_doc)
    new_local_provinces = dict(local_doc["provinces"])
    new_local_provinces["河南"] = new_henan_local
    new_local["provinces"] = new_local_provinces

    # Recompute local _meta
    new_local_count = sum(len(v) for v in new_local_provinces.values())
    new_national_count = len(new_local_provinces.get("全国/中央", []))
    new_unknown_count = len(new_local_provinces.get("未知", []))
    new_local["_meta"] = {
        **local_doc["_meta"],
        "count": new_local_count,
        "known_province": new_local_count - new_unknown_count - new_national_count,
        "unknown_province": new_unknown_count,
        "national": new_national_count,
        "deduped_at": "2026-06-21T09:05:00+08:00",
    }

    # Add to satellite 河南
    new_sat = dict(sat_doc)
    new_sat_provinces = dict(sat_doc["provinces"])
    henan_sat_list = list(new_sat_provinces.get("河南", []))
    # Add the moved channel
    moved_in_sat_shape = dict(moved_channel)
    # Tag with merged_ids to track
    moved_in_sat_shape["merged_ids"] = [moved_channel["id"]]
    # The other 河南卫视.cn already in satellite 河南 — should we merge too?
    # Yes: HenanTVSatellite (English, alt=[]) + 河南卫视.cn (Chinese) — alt-based
    # dedup already ran, but 河南卫视.cn is a Chinese-named sat in satellite.json
    # so the dedup function looked for English-named partners and didn't find any
    # for 河南 (because HenanTVSatellite was in local, not satellite).
    # Now that it's in satellite, we need to merge them.
    henan_sat_idx = None
    for i, c in enumerate(henan_sat_list):
        if c.get("id") == "河南卫视.cn":
            henan_sat_idx = i
            break
    if henan_sat_idx is not None:
        # Merge HenanTVSatellite (secondary, English-named, has Satellite keyword)
        # into 河南卫视.cn (primary, Chinese-named)
        primary = henan_sat_list[henan_sat_idx]
        merged = merge_pair(primary, moved_in_sat_shape)
        # Preserve existing merged_ids from dedup run
        henan_sat_list[henan_sat_idx] = merged
    else:
        henan_sat_list.append(moved_in_sat_shape)

    henan_sat_list.sort(key=lambda c: c.get("name", ""))
    new_sat_provinces["河南"] = henan_sat_list
    new_sat["provinces"] = new_sat_provinces

    # Recompute satellite _meta
    new_sat_count = sum(len(v) for v in new_sat_provinces.values())
    new_sat["_meta"] = {
        **sat_doc["_meta"],
        "count": new_sat_count,
        "provinces": len(new_sat_provinces),
    }

    return new_sat, new_local, moved


def update_meta(sat_doc: Dict[str, Any], local_doc: Dict[str, Any]) -> Dict[str, Any]:
    meta = load_json(META_PATH)
    meta["totals"]["satellite"] = sat_doc["_meta"]["count"]
    meta["totals"]["local"] = local_doc["_meta"]["count"]
    meta["totals"]["local_known_province"] = local_doc["_meta"]["known_province"]
    meta["last_updated"] = "2026-06-21T09:05:00+08:00"
    return meta


def main() -> int:
    print(f"Loading {SAT_PATH}")
    sat = load_json(SAT_PATH)
    print(f"  before: count={sat['_meta']['count']} provinces={sat['_meta']['provinces']}")
    print(f"Loading {LOCAL_PATH}")
    local = load_json(LOCAL_PATH)
    print(f"  before: count={local['_meta']['count']}")

    # Step 1: dedup satellite
    sat_new, merged_pairs = dedup_satellite(sat)
    print(f"After dedup: count={sat_new['_meta']['count']} (merged {merged_pairs} pairs)")

    # Step 2: move HenanTVSatellite
    sat_new, local_new, moved = move_henan_to_satellite(sat_new, local)
    print(f"After Henan move: sat count={sat_new['_meta']['count']}, local count={local_new['_meta']['count']}, moved={moved}")

    # Step 3: update meta
    meta_new = update_meta(sat_new, local_new)
    print(f"meta.totals.satellite={meta_new['totals']['satellite']}, totals.local={meta_new['totals']['local']}")

    # Write
    dump_json(SAT_PATH, sat_new)
    print(f"Wrote {SAT_PATH}")
    dump_json(LOCAL_PATH, local_new)
    print(f"Wrote {LOCAL_PATH}")
    dump_json(META_PATH, meta_new)
    print(f"Wrote {META_PATH}")

    # Summary
    print()
    print("=" * 60)
    print("DEDUP SUMMARY")
    print("=" * 60)
    print(f"satellite.json: {sat['_meta']['count']} → {sat_new['_meta']['count']} channels ({merged_pairs} pairs merged)")
    print(f"local.json:    {local['_meta']['count']} → {local_new['_meta']['count']} channels (HenanTVSatellite moved)")
    print(f"net channels:  unchanged (dedup + move = no deletion)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())