# bot.py
from __future__ import annotations
import asyncio, logging
from typing import Optional
import boto3
import discord
from discord.ext import tasks
from mcstatus import JavaServer

# Import config structure and constants
from config import BotCfg, ServerCfg
from constants import VERBOSE, CONSECUTIVE_EMPTY_LIMIT

class MinecraftServerBot:
    """Handles Discord bot logic, interactions with AWS EC2, and Minecraft server status."""
    def __init__(self, cfg: BotCfg):
        self.cfg = cfg
        # Start with the first server listed in the config as the current one
        self.current: ServerCfg = next(iter(cfg.servers.values()))
        self._ec2 = None # Lazy-loaded boto3 client
        self._consecutive_empty = 0
        self._last_command_chan: Optional[discord.TextChannel] = None
        self._last_ec2_state = ""

        # Setup Discord client intents
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)

        # Register event handlers
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    # -- utilities -----------------------------------------------------------
    @property
    def ec2(self):
        """Lazy initializes and returns the boto3 EC2 client for the current server's region."""
        if self._ec2 is None:
            logging.info(f"Initializing Boto3 EC2 client for region: {self.current.region}")
            self._ec2 = boto3.client(
                "ec2",
                region_name=self.current.region,
                aws_access_key_id=self.cfg.aws_key,
                aws_secret_access_key=self.cfg.aws_secret
            )
        return self._ec2

    async def say(self, channel: Optional[discord.abc.Messageable], *args, **kw):
        """Sends a message to a Discord channel, logging if verbose mode is enabled."""
        if channel is None:
            logging.warning("Attempted to send message but no channel is known.")
            return None
        if VERBOSE:
            content = kw.get("embed", None)
            logging.debug(f"→ {channel.id}: {content or args}")
        return await channel.send(*args, **kw)

    # -- AWS / MC helpers -----------------------------------------------------
    def ec2_state(self) -> str:
        """Gets the current state of the active EC2 instance."""
        try:
            res = self.ec2.describe_instances(InstanceIds=[self.current.instance_id])
            state = res["Reservations"][0]["Instances"][0]["State"]["Name"]
            if VERBOSE and state != self._last_ec2_state:
                 logging.debug(f"EC2 state ({self.current.name}): {self._last_ec2_state} → {state}")
            self._last_ec2_state = state
            return state
        except Exception as e:
            logging.error(f"Failed to get EC2 instance state for {self.current.instance_id}: {e}")
            return "error" # Return a distinct state for errors

    async def mc_status(self):
        """Checks the status of the Minecraft server using mcstatus."""
        try:
            # Run blocking network IO in a separate thread
            status = await asyncio.to_thread(
                JavaServer.lookup(self.current.ip).status)
            players = [p.name for p in (status.players.sample or [])]
            return True, status.players.online, players, status
        except Exception as e:
            # Log specific errors if needed, but generally indicates server is unreachable
            if VERBOSE:
                logging.warning(f"MC status error for {self.current.ip}: {e}")
            return False, 0, [], None # Server unreachable or error occurred

    # -- embeds ---------------------------------------------------------------
    @staticmethod
    def embed(text: str, colour: discord.Color):
        """Creates a simple Discord embed with text and color."""
        return discord.Embed(description=text, colour=colour)

    async def status_embed(self) -> discord.Embed:
        """Creates a detailed status embed based on EC2 and Minecraft server status."""
        state = self.ec2_state()

        if state == "running":
            is_mc_up, online_players, player_names, mc_status_obj = await self.mc_status()
            if is_mc_up and mc_status_obj:
                embed = discord.Embed(title=mc_status_obj.description or "Minecraft Server", colour=discord.Color.green())
                # Consider adding a configurable thumbnail URL per server
                embed.set_thumbnail(url="https://www.packpng.com/static/pack.png") # Example thumbnail
                embed.add_field(name="IP", value=f"`{self.current.ip}`", inline=True)
                embed.add_field(name="Version", value=mc_status_obj.version.name, inline=True)
                embed.add_field(name="Players",
                                value=f"{online_players}/{mc_status_obj.players.max}", inline=True)
                player_list = "\n".join(player_names) if online_players else "No one is online."
                # Discord embed field values have character limits
                if len(player_list) > 1024:
                    player_list = player_list[:1020] + "..."
                embed.add_field(name="Player List", value=player_list, inline=False)
                return embed
            else:
                # Server is running but Minecraft process might be starting or crashed
                return self.embed(f"Server `{self.current.name}` is running (EC2) but Minecraft is not reachable at `{self.current.ip}` yet.",
                                  discord.Color.orange())
        elif state == "pending":
            return self.embed(f"Server `{self.current.name}` is starting...", discord.Color.yellow())
        elif state in ("stopping", "shutting-down"):
            return self.embed(f"Server `{self.current.name}` is stopping...", discord.Color.orange())
        elif state == "stopped":
            return self.embed(f"Server `{self.current.name}` is offline.", discord.Color.red())
        elif state == "error":
             return self.embed(f"Could not retrieve status for server `{self.current.name}` (AWS Error).", discord.Color.dark_red())
        else: # Other potential states like 'rebooting'
             return self.embed(f"Server `{self.current.name}` state is unknown or unusual: `{state}`.", discord.Color.greyple())


    # -- commands ------------------------------------------------------------
    async def cmd_status(self, channel: discord.abc.Messageable):
        """Handles the s!status command."""
        await self.say(channel, embed=await self.status_embed())

    async def cmd_start(self, channel: discord.abc.Messageable):
        """Handles the s!start command."""
        state = self.ec2_state()
        if state == "running":
            await self.say(channel, embed=self.embed(
                f"Server `{self.current.name}` is already running!", discord.Color.yellow()))
            return
        if state in ("pending", "stopping", "shutting-down"):
             await self.say(channel, embed=self.embed(
                f"Server `{self.current.name}` is currently busy (`{state}`). Please wait.", discord.Color.orange()))
             return

        self._last_command_chan = channel # Remember channel for potential auto-stop message
        await self.say(channel, embed=self.embed(f"Starting server `{self.current.name}`...", discord.Color.green()))
        try:
            self.ec2.start_instances(InstanceIds=[self.current.instance_id])
            # Wait for the instance to report it's running
            await asyncio.to_thread(
                self.ec2.get_waiter("instance_running").wait,
                InstanceIds=[self.current.instance_id]
            )
            logging.info(f"EC2 instance {self.current.instance_id} reported as running.")

            # Wait a bit longer for the Minecraft server process itself to start
            await self.say(channel, embed=self.embed(
                f"Server `{self.current.name}` is running (EC2). Waiting for Minecraft to respond...", discord.Color.yellow()))

            max_wait_loops = 120 # Approx 2 minutes if wait=1.0
            waited_loops = 0
            for i in range(max_wait_loops):
                waited_loops = i + 1
                is_mc_up, *_ = await self.mc_status()
                if is_mc_up:
                    logging.info(f"Minecraft server at {self.current.ip} responded after ~{waited_loops * self.current.wait:.1f}s.")
                    await self.say(channel, f"Server `{self.current.name}` started!", embed=await self.status_embed())
                    return # Success
                await asyncio.sleep(self.current.wait) # Use configured wait time

            logging.warning(f"Minecraft server did not respond after {waited_loops * self.current.wait:.1f}s.")
            await self.say(channel, embed=self.embed(
                f"Server `{self.current.name}` started (EC2), but Minecraft did not respond. Check server logs.", discord.Color.orange()))

        except Exception as e:
            logging.error(f"Error starting server {self.current.instance_id}: {e}")
            await self.say(channel, embed=self.embed(
                f"An error occurred while starting server `{self.current.name}`.", discord.Color.red()))

    async def cmd_stop(self, channel: Optional[discord.abc.Messageable], auto: bool = False):
        """Handles the s!stop command, can be triggered manually or automatically."""
        # Use last known channel if triggered automatically without a specific channel
        effective_channel = channel or self._last_command_chan

        if effective_channel is None:
            logging.error("cmd_stop called without a channel (manual or remembered).")
            return # Cannot report status

        state = self.ec2_state()
        if state not in ("running",):
            await self.say(effective_channel, embed=self.embed(
                f"Server `{self.current.name}` is not running (`{state}`), cannot stop.", discord.Color.yellow()))
            return

        is_mc_up, online_players, *_ = await self.mc_status()

        if not auto and is_mc_up and online_players > 0:
            await self.say(effective_channel, embed=self.embed(
                f"Cannot stop server `{self.current.name}`: {online_players} player(s) online!", discord.Color.red()))
            return

        msg = (f"Server `{self.current.name}` has been empty for {CONSECUTIVE_EMPTY_LIMIT * 5} minutes. Stopping..."
               if auto else f"Stopping server `{self.current.name}`...")
        await self.say(effective_channel, embed=self.embed(msg, discord.Color.orange()))

        try:
            self.ec2.stop_instances(InstanceIds=[self.current.instance_id])
            # Wait for the instance to report it's stopped
            await asyncio.to_thread(
                self.ec2.get_waiter("instance_stopped").wait,
                InstanceIds=[self.current.instance_id]
            )
            logging.info(f"EC2 instance {self.current.instance_id} reported as stopped.")
            await self.say(effective_channel, embed=self.embed(f"Server `{self.current.name}` stopped!", discord.Color.red()))
            self._consecutive_empty = 0 # Reset counter after successful stop
        except Exception as e:
            logging.error(f"Error stopping server {self.current.instance_id}: {e}")
            await self.say(effective_channel, embed=self.embed(
                f"An error occurred while stopping server `{self.current.name}`.", discord.Color.red()))

    async def cmd_ip(self, channel: discord.abc.Messageable):
        """Handles the s!ip command."""
        await self.say(channel, embed=self.embed(
            f"IP for server `{self.current.name}`: `{self.current.ip}`", discord.Color.blue()))

    async def cmd_mount(self, channel: discord.abc.Messageable, server_name: str):
        """Handles the s!mount command to switch the active server."""
        if self.ec2_state() == "running":
            await self.say(channel, embed=self.embed(
                f"Cannot change active server while `{self.current.name}` is running. Please stop it first.", discord.Color.red()))
            return

        if server_name not in self.cfg.servers:
            valid_servers = ", ".join(f"`{s}`" for s in self.cfg.servers)
            await self.say(channel, embed=self.embed(
                f"Invalid server name: `{server_name}`. Valid servers: {valid_servers}", discord.Color.red()))
            return

        if server_name == self.current.name:
             await self.say(channel, embed=self.embed(
                f"Server `{server_name}` is already the active server.", discord.Color.yellow()))
             return

        self.current = self.cfg.servers[server_name]
        self._ec2 = None # Force re-initialization of boto3 client for potentially new region
        self._last_ec2_state = "" # Reset last known state
        self._consecutive_empty = 0 # Reset empty counter
        logging.info(f"Switched active server to: {self.current.name} (Region: {self.current.region})")
        await self.say(channel, embed=self.embed(
            f"Switched active server to: `{server_name}`", discord.Color.blue()))
        # Optionally show status of the new server
        await self.cmd_status(channel)


    async def cmd_list(self, channel: discord.abc.Messageable):
        """Handles the s!list command."""
        lines = []
        for name, server_cfg in self.cfg.servers.items():
            active_marker = " (active)" if name == self.current.name else ""
            lines.append(f"- `{name}`{active_marker} (Region: {server_cfg.region}, IP: `{server_cfg.ip}`)")

        await self.say(channel, embed=discord.Embed(
            title="Available Servers",
            description="\n".join(lines) or "No servers configured.",
            colour=discord.Color.blue()))

    async def cmd_help(self, channel: discord.abc.Messageable):
        """Handles the s!help command."""
        await self.say(channel, embed=discord.Embed(
            title="Minecraft Server Bot Commands",
            description=(
                "`s! status` - Check current server status/player count.\n"
                "`s! start`  - Start the current server.\n"
                "`s! stop`   - Stop the current server (if empty).\n"
                "`s! ip`     - Show the current server IP.\n"
                "`s! list`   - List all configured servers.\n"
                "`s! mount <name>` - Switch active server (must be offline).\n"
                "`s! help`   - Display this message."
            ),
            colour=discord.Color.blue()))

    # -- discord events ------------------------------------------------------
    async def on_ready(self):
        """Called when the bot successfully connects to Discord."""
        logging.info(f"Logged in as {self.client.user}")
        # Start background task only after connection is ready
        if not self.monitor_players.is_running():
            logging.info("Starting player monitoring background task.")
            self.monitor_players.start()
        else:
             logging.warning("monitor_players task was already running on_ready.")


    async def on_message(self, message: discord.Message):
        """Called when a message is sent in a channel the bot can see."""
        # Ignore messages from the bot itself
        if message.author == self.client.user:
            return
        # Ignore messages that don't start with the prefix
        if not message.content.startswith("s!"):
            return

        # Store the channel where the last command was received
        # Useful for sending auto-stop messages if the original channel context is lost
        if isinstance(message.channel, discord.TextChannel):
             self._last_command_chan = message.channel

        # Parse the command and arguments
        full_command = message.content[2:].strip()
        parts = full_command.split()
        if not parts: # Just "s!" was sent
            cmd = ""
            args = []
        else:
            cmd = parts[0].lower()
            args = parts[1:]

        if VERBOSE:
            logging.debug(f"Cmd='{cmd}', Args={args} from {message.author} in #{message.channel}")

        # Command dispatcher
        match cmd:
            case "status": await self.cmd_status(message.channel)
            case "start":  await self.cmd_start(message.channel)
            case "stop":   await self.cmd_stop(message.channel, auto=False) # Explicit stop is never automatic
            case "ip":     await self.cmd_ip(message.channel)
            case "mount" if args: await self.cmd_mount(message.channel, args[0])
            case "mount":  await self.say(message.channel, embed=self.embed(
                               "Usage: `s! mount <server_name>`", discord.Color.red()))
            case "list":   await self.cmd_list(message.channel)
            case "help":   await self.cmd_help(message.channel)
            case "":       await self.say(message.channel, embed=self.embed(
                               "Command missing. Try `s! help`.", discord.Color.red()))
            case _:        await self.say(message.channel, embed=self.embed(
                               f"Unknown command: `{cmd}`. Try `s! help`.", discord.Color.red()))

    # -- background job ------------------------------------------------------
    @tasks.loop(minutes=5)
    async def monitor_players(self):
        """Periodically checks the server status and auto-stops if empty."""
        # Ensure loop doesn't run before bot is ready
        await self.client.wait_until_ready()

        state = self.ec2_state()
        # Only monitor if the server is supposed to be running
        if state != "running":
            if self._consecutive_empty > 0:
                 logging.info(f"Resetting empty counter because server state is {state}.")
                 self._consecutive_empty = 0
            return

        is_mc_up, online_players, *_ = await self.mc_status()

        if is_mc_up and online_players == 0:
            self._consecutive_empty += 1
            logging.info(f"Server {self.current.name} is empty. Consecutive empty checks: {self._consecutive_empty}/{CONSECUTIVE_EMPTY_LIMIT}")
            if self._consecutive_empty >= CONSECUTIVE_EMPTY_LIMIT:
                logging.info(f"Auto-stopping server {self.current.name} due to inactivity.")
                # Pass auto=True and None for channel (will use _last_command_chan if available)
                await self.cmd_stop(None, auto=True)
                # cmd_stop resets counter on success
        else:
            # If players are online OR mc is down (implying server starting/crashing, not stable empty)
            if self._consecutive_empty > 0:
                 logging.info(f"Resetting empty counter for {self.current.name}. Players online: {online_players}. MC Reachable: {is_mc_up}.")
                 self._consecutive_empty = 0

    # -- entry point ---------------------------------------------------------
    def run(self):
        """Starts the Discord bot client."""
        try:
            logging.info("Starting Discord client...")
            self.client.run(self.cfg.token)
        except discord.LoginFailure:
            logging.error("Failed to log in. Check your Discord token in the config file.")
        except Exception as e:
            logging.error(f"An unexpected error occurred while running the bot: {e}")
