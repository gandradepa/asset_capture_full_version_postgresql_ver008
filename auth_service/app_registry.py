# /home/developer/auth_service/app_registry.py
"""
Central registry of application sections and items for RBAC.

To add a new section: append a new dict to APP_REGISTRY.
To add a new item:   append to the matching section's "items" list.

Convention: item_key "" is RESERVED for section-level grants in the DB;
never define an item with key "" here.
"""

APP_REGISTRY = [
    {
        "key": "application",
        "label": "Application",
        "items": [
            {"key": "asset_capture_mobile",  "label": "Asset Capture Mobile App"},
            {"key": "reviewer_mechanical",    "label": "Asset Reviewer - Mechanical"},
            {"key": "reviewer_backflow",      "label": "Asset Reviewer - Backflow"},
            {"key": "reviewer_electrical",    "label": "Asset Reviewer - Electrical"},
            {"key": "sdi_process",            "label": "SDI Process Application"},
        ],
    },
    {
        "key": "operations",
        "label": "Operations & Monitoring",
        "items": [
            {"key": "logs_pending",   "label": "Logs & Pending"},
            {"key": "reviewer_kpis",  "label": "Reviewer KPIs"},
            {"key": "cost_analysis",  "label": "Performance Analysis"},
            {"key": "asset_map",      "label": "Asset Map"},
            {"key": "fls_devices",    "label": "FLS Devices"},
            {"key": "lifecycle_assessment", "label": "Life Cycle Assessment"},
            {"key": "disposed_assets", "label": "Disposed Assets"},
            {"key": "user_activity",  "label": "User Activity"},
            {"key": "user_admin",     "label": "User Admin"},
        ],
    },
    {
        "key": "dictionary",
        "label": "Dictionary",
        "items": [
            {"key": "dictionary", "label": "Dictionary"},
        ],
    },
]

# Flat lookup maps for O(1) validation
_SECTION_MAP = {s["key"]: s for s in APP_REGISTRY}
_ITEM_MAP = {
    (s["key"], i["key"]): i
    for s in APP_REGISTRY
    for i in s["items"]
}


def get_registry() -> list:
    return APP_REGISTRY


def is_valid_section(section_key: str) -> bool:
    return section_key in _SECTION_MAP


def is_valid_item(section_key: str, item_key: str) -> bool:
    if item_key == "":
        return is_valid_section(section_key)
    return (section_key, item_key) in _ITEM_MAP
