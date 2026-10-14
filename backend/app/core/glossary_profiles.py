"""
Series glossary profiles: characters (gender, name spelling) and terms that are saved once and
reused by every episode of a show, so each episode's glossary pass only has to add what is new.

Profiles are small JSON files under `<storage>/glossaries/`.
"""
import json
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings

_MAX_NAME = 80


def _dir():
    return settings.storage_dir / "glossaries"


def clean_name(name: Optional[str]) -> str:
    """A profile name as the user typed it, trimmed and without path characters."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", (name or "")).strip()[:_MAX_NAME]


def _path(name: str):
    return _dir() / f"{clean_name(name)}.json"


def list_profiles() -> List[str]:
    try:
        return sorted(p.stem for p in _dir().glob("*.json"))
    except OSError:
        return []


def load_profile(name: Optional[str]) -> Optional[Dict[str, Any]]:
    name = clean_name(name)
    if not name:
        return None
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_profile(name: str, glossary: Dict[str, Any]) -> None:
    name = clean_name(name)
    if not name:
        raise ValueError("Profile name is empty")
    _dir().mkdir(parents=True, exist_ok=True)
    _path(name).write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_glossary(known: Optional[Dict[str, Any]], fresh: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Combine the saved profile with a newly built glossary: whatever the profile already says
    wins, and the new glossary only adds characters and terms the profile does not have.
    """
    if not known:
        return fresh
    if not fresh:
        return known
    chars = list(known.get("characters") or [])
    seen = {(c.get("name") or "").strip().lower() for c in chars}
    for c in fresh.get("characters") or []:
        key = (c.get("name") or "").strip().lower()
        if key and key not in seen:
            chars.append(c)
            seen.add(key)
    terms = list(known.get("terms") or [])
    seen_terms = {(t.get("source") or "").strip().lower() for t in terms}
    for t in fresh.get("terms") or []:
        key = (t.get("source") or "").strip().lower()
        if key and key not in seen_terms:
            terms.append(t)
            seen_terms.add(key)
    return {
        "summary": known.get("summary") or fresh.get("summary") or "",
        "characters": chars,
        "terms": terms,
        "register_notes": known.get("register_notes") or fresh.get("register_notes") or "",
    }
