# constants.py
import sys
from pathlib import Path

# Path to the configuration file
CONFIG_PATH = Path("config.json")

# Command line flag for verbose logging
VERBOSE = "-v" in sys.argv

# Number of consecutive 5-minute intervals the server must be empty before auto-stopping
CONSECUTIVE_EMPTY_LIMIT = 3
