"""Constants for the ha_ems integration."""

DOMAIN = "ha_ems"
BASE_URL = "https://server-nj.ai-ec.cloud:8443"
STORAGE_DEVICE_TYPES = {85, 90, 99, 131, 174}

SCAN_INTERVAL_REALTIME = 30    # seconds — real-time energy flow
SCAN_INTERVAL_STATISTICS = 300  # seconds — energy statistics (5 minutes)

# Device icon types
ICON_TYPE_SOCKET = 5
ICON_TYPE_CHARGER = 6

# Energy mode options for select entity
ENERGY_MODE_OPTIONS = {
    "0": "General",
    "1": "Smart",
    "2": "Custom",
    "3": "Time of Use",
    "4": "Manual",
}
