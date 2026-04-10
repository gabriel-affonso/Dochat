import json
from datetime import datetime

KEYS_TO_EXCLUDE = ["content", "pages", "tables", "paragraphs", "sections", "figures"]


def filter_metadata(metadata: dict[str, any]) -> dict[str, any]:
    # Removes large/redundant fields from metadata dict.
    metadata = {
        key: value for key, value in metadata.items() if key not in KEYS_TO_EXCLUDE
    }
    return metadata


def process_metadata(
    metadata: dict[str, any],
) -> dict[str, any]:
    # Removes large fields and coerces values to scalar metadata types supported by vector DBs.
    result = {}
    for key, value in metadata.items():
        if key in KEYS_TO_EXCLUDE:
            continue
        if value is None:
            continue
        if isinstance(value, datetime):
            result[key] = value.isoformat()
            continue
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
            continue
        if isinstance(value, (list, dict, tuple, set)):
            result[key] = json.dumps(value, ensure_ascii=False, default=str)
            continue

        result[key] = str(value)
    return result
