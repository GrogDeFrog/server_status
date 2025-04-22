# main.py
import logging
import sys

# Import necessary components from other modules
from config import BotCfg
from bot import MinecraftServerBot
from constants import VERBOSE

def setup_logging():
    """Configures application logging based on verbosity."""
    log_level = logging.DEBUG if VERBOSE else logging.INFO
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=log_level, format=log_format)
    # Silence overly verbose libraries if not in verbose mode
    if not VERBOSE:
        logging.getLogger("discord").setLevel(logging.WARNING)
        logging.getLogger("websockets").setLevel(logging.WARNING)
        logging.getLogger("boto3").setLevel(logging.WARNING)
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)

if __name__ == "__main__":
    # Setup logging first
    setup_logging()

    logging.info("Application starting...")
    if VERBOSE:
        logging.debug("Verbose mode enabled.")

    # Load configuration
    try:
        config = BotCfg.load()
        logging.info(f"Configuration loaded successfully. {len(config.servers)} server(s) defined.")
        if not config.servers:
             logging.error("No servers defined in the configuration file. Exiting.")
             sys.exit(1)

    except Exception as e:
        # Specific errors handled in BotCfg.load, this is a fallback
        logging.exception("Failed to load configuration during startup.")
        sys.exit(1)

    # Create and run the bot instance
    bot = MinecraftServerBot(config)
    bot.run()

    logging.info("Application shutting down.")
