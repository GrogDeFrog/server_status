# config.py
from __future__ import annotations
import json, logging, sys
from pathlib import Path
from dataclasses import dataclass
from constants import CONFIG_PATH # Import config path constant

@dataclass(frozen=True, slots=True)
class ServerCfg:
    """Configuration specific to a single Minecraft server instance."""
    name: str
    instance_id: str
    region: str
    ip: str
    wait: float # Time to wait between checks after starting

@dataclass(frozen=True, slots=True)
class BotCfg:
    """Overall bot configuration including credentials and server definitions."""
    token: str
    aws_key: str
    aws_secret: str
    servers: dict[str, ServerCfg]

    @staticmethod
    def load(path: Path = CONFIG_PATH) -> "BotCfg":
        """Loads configuration from a JSON file, creating a default if none exists."""
        if not path.exists():
            # Create a default configuration file on the first run
            path.write_text(json.dumps({
                "DISCORD_TOKEN": "xxxxxxxxxxxxxxxx...",
                "AWS_ACCESS_KEY": "xxxxxxxxxxxx",
                "AWS_SECRET": "xxxxxxxxxxxxxxxxxxxxxxxx",
                "servers": {"default": {
                    "INSTANCE_ID": "i-xxxxxxxxxxxx",
                    "AWS_REGION": "us-east-2",
                    "SERVER_IP": "0.0.0.0",
                    "WAIT_TIME": 0.1  # Default wait time in seconds
                 }}
            }, indent=4))
            logging.info(f"Default configuration file created at '{path}'. Edit it then restart.")
            sys.exit(0)

        try:
            raw = json.loads(path.read_text())
            servers = {name: ServerCfg(name,
                                       s["INSTANCE_ID"],
                                       s["AWS_REGION"],
                                       s["SERVER_IP"],
                                       float(s["WAIT_TIME"]))
                       for name, s in raw["servers"].items()}

            return BotCfg(raw["DISCORD_TOKEN"],
                          raw["AWS_ACCESS_KEY"],
                          raw["AWS_SECRET"],
                          servers)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logging.error(f"Error loading configuration from '{path}': {e}")
            logging.error("Please ensure the config file is valid JSON and contains all required keys.")
            sys.exit(1)
        except FileNotFoundError:
            logging.error(f"Configuration file not found at '{path}'.")
            sys.exit(1)
