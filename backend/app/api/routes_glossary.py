from typing import List

from fastapi import APIRouter

from app.core.glossary_profiles import list_profiles

router = APIRouter(prefix="/api/glossary-profiles", tags=["Glossary"])


@router.get("", response_model=List[str])
def get_glossary_profiles():
    """Names of the saved series glossary profiles."""
    return list_profiles()
