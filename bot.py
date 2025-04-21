import io
import os
import sys
import json
import base64
import asyncio
import logging
import discord
from discord.ext import tasks
import boto3
from mcstatus import JavaServer

# -v flag for verbose logging
VERBOSE = "-v" in sys.argv

# Configure logging
logging.basicConfig(level=logging.INFO)

CONFIG_FILE = "config.json"

# Create a default config file if it doesn't exist
if not os.path.exists(CONFIG_FILE):
    default_config = {
        "DISCORD_TOKEN": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "AWS_ACCESS_KEY": "xxxxxxxxxxxxxxxxxxxx",
        "AWS_SECRET": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "servers": {
            "default": {
                "INSTANCE_ID": "i-xxxxxxxxxxxxxxxxx",
                "AWS_REGION": "us-east-2",
                "SERVER_IP": "xx.xxxxxxxx.xxx",
                "WAIT_TIME": 0.1
            }
        }
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(default_config, f, indent=4)
    logging.info("Default configuration file created. Please update it as needed and restart the application.")
    exit(0)

# Load configuration
with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

TOKEN = config["DISCORD_TOKEN"]
AWS_ACCESS_KEY = config["AWS_ACCESS_KEY"]
AWS_SECRET = config["AWS_SECRET"]

current_server_name = list(config["servers"].keys())[0]

def get_current_server():
    return config["servers"][current_server_name]

def get_wait_time():
    return float(config["servers"][current_server_name]["WAIT_TIME"])

def get_ec2_client():
    server = get_current_server()
    return boto3.client(
        "ec2",
        region_name=server["AWS_REGION"],
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET
    )

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

CONSECUTIVE_EMPTY_THRESHOLD = 3
consecutive_empty_checks = 0
last_used_channel = None
last_ec2_state = None

async def send_and_log(channel, *args, **kwargs):
    msg = ""
    if "embed" in kwargs and kwargs["embed"]:
        msg = kwargs["embed"].description or ""
    elif args:
        msg = args[0]
    if VERBOSE:
        logging.info(f"Bot sending message to channel {channel.id}: {msg}")
    return await channel.send(*args, **kwargs)

async def check_server_status(suppress_errors: bool = False) -> tuple[bool, int, list[str], object]:
    try:
        server = get_current_server()
        mc_server = JavaServer.lookup(server["SERVER_IP"])
        status = await asyncio.to_thread(mc_server.status)
        player_sample = status.players.sample or []
        return True, status.players.online, [p.name for p in player_sample], status
    except Exception as e:
        if not suppress_errors:
            logging.error(f"Error checking server status: {e}")
        return False, 0, [], None

def get_ec2_state() -> str:
    server = get_current_server()
    ec2 = get_ec2_client()
    response = ec2.describe_instances(InstanceIds=[server["INSTANCE_ID"]])
    return response["Reservations"][0]["Instances"][0]["State"]["Name"]

async def start_server(channel: discord.TextChannel) -> None:
    try:
        server = get_current_server()
        ec2 = get_ec2_client()
        ec2.start_instances(InstanceIds=[server["INSTANCE_ID"]])
        await send_and_log(channel, embed=discord.Embed(description="Starting server...", color=discord.Color.green()))
        waiter = ec2.get_waiter('instance_running')
        await asyncio.to_thread(waiter.wait, InstanceIds=[server["INSTANCE_ID"]])
        for _ in range(120):
            up, _, _, _ = await check_server_status(suppress_errors=True)
            if up:
                break
            await asyncio.sleep(get_wait_time())
        embed = await get_server_status_embed()
        await send_and_log(channel, "Server started!", embed=embed)
    except Exception as e:
        await send_and_log(channel, embed=discord.Embed(description=str(e), color=discord.Color.red()))

async def stop_server(channel: discord.TextChannel | None, auto: bool = False) -> None:
    try:
        if channel:
            msg = "Server has been empty for 15 minutes.\nStopping..." if auto else "Stopping server..."
            await send_and_log(channel, embed=discord.Embed(description=msg, color=discord.Color.orange()))

        server = get_current_server()
        ec2 = get_ec2_client()
        ec2.stop_instances(InstanceIds=[server["INSTANCE_ID"]])
        waiter = ec2.get_waiter('instance_stopped')
        await asyncio.to_thread(waiter.wait, InstanceIds=[server["INSTANCE_ID"]])
        if channel:
            await send_and_log(channel, embed=discord.Embed(description="Server stopped!", color=discord.Color.red()))
    except Exception as e:
        if auto:
            logging.error(f"Error stopping server: {e}")
        elif channel:
            await send_and_log(channel, embed=discord.Embed(description=str(e), color=discord.Color.red()))

@tasks.loop(minutes=5)
async def monitor_player_count() -> None:
    global consecutive_empty_checks, last_used_channel, last_ec2_state
    current_state = get_ec2_state()
    if VERBOSE and current_state != last_ec2_state:
        logging.info(f"Server status changed: {last_ec2_state} -> {current_state}")
    last_ec2_state = current_state

    if current_state != "running":
        consecutive_empty_checks = 0
        return
    up, players, _, _ = await check_server_status()
    if up:
        if players == 0:
            consecutive_empty_checks += 1
            if consecutive_empty_checks >= CONSECUTIVE_EMPTY_THRESHOLD:
                await stop_server(last_used_channel, auto=True)
                consecutive_empty_checks = 0
        else:
            consecutive_empty_checks = 0

async def get_server_status_embed() -> discord.Embed:
    state = get_ec2_state()
    if state == "running":
        up, players, player_list, status_info = await check_server_status()
        if up and status_info:
            embed = discord.Embed(title=status_info.description, color=discord.Color.green())

            embed.set_thumbnail(url="https://www.packpng.com/static/pack.png")

            # Doesn't actually try to decode the favicon. Oh well.

            embed.add_field(name="Version", value=status_info.version.name, inline=True)
            embed.add_field(name="Player Count", value=f"{players}/{status_info.players.max}", inline=True)
            player_list_text = "\n".join(player_list) if players > 0 else "No one is online."
            embed.add_field(name="Player List", value=player_list_text, inline=True)
        else:
            embed = discord.Embed(description="Server is running but not reachable yet.", color=discord.Color.orange())
    elif state == "pending":
        embed = discord.Embed(description="Server is starting...", color=discord.Color.yellow())
    elif state in ["stopping", "shutting-down"]:
        embed = discord.Embed(description="Server is stopping...", color=discord.Color.orange())
    elif state == "stopped":
        embed = discord.Embed(description="Server is offline.", color=discord.Color.red())
    else:
        embed = discord.Embed(description="Server state is unknown.", color=discord.Color.red())
    return embed

@client.event
async def on_ready() -> None:
    logging.info(f"Logged in as {client.user}")
    monitor_player_count.start()

@client.event
async def on_message(message: discord.Message) -> None:
    if message.author == client.user:
        return

    normalized = message.content.strip().lower()
    if not normalized.startswith("s!"):
        return

    args = normalized[2:].split()

    if not args:
        embed = discord.Embed(description=f"Unknown command: ` `.\nTry `s! help` for a list of commands.", color=discord.Color.red())
        await send_and_log(channel, embed=embed)
        return

    cmd = args[0]
    if VERBOSE:
        logging.info(f"Received command from {message.author}: {cmd}")

    channel = message.channel

    if cmd == "status":
        embed = await get_server_status_embed()
        global current_server_name
        await send_and_log(channel, content=f"Current server: `{current_server_name}`", embed=embed)
    elif cmd == "start":
        if get_ec2_state() == "running":
            await send_and_log(channel, embed=discord.Embed(description="Server already running!", color=discord.Color.yellow()))
        else:
            global last_used_channel
            last_used_channel = channel
            await start_server(channel)
    elif cmd == "stop":
        if get_ec2_state() != "running":
            await send_and_log(channel, embed=discord.Embed(description="Server is already offline!", color=discord.Color.yellow()))
        else:
            up, players, _, _ = await check_server_status()
            if up and players > 0:
                await send_and_log(channel, embed=discord.Embed(description="Cannot stop server: players online!", color=discord.Color.red()))
            else:
                await stop_server(channel)
    elif cmd == "ip":
        await send_and_log(channel, embed=discord.Embed(description=f"Server IP: `{server[SERVER_IP]}`", color=discord.Color.blurple()))
    elif cmd == "mount":
        if len(args) < 2:
            await send_and_log(channel, embed=discord.Embed(description="Usage: `s! mount <server_name>`", color=discord.Color.red()))
        elif get_ec2_state() == "running":
            await send_and_log(channel, embed=discord.Embed(
                description="Cannot change active server while the current server is online. Please stop it first.",
                color=discord.Color.red()))
        else:
            new_server = args[1]
            if new_server not in config["servers"]:
                valid_servers = ", ".join(f"`{server}`" for server in config["servers"].keys())
                await send_and_log(channel, embed=discord.Embed(
                    description=f"Invalid server name. Valid servers: {valid_servers}",
                    color=discord.Color.red()))
            else:
                current_server_name = new_server
                await send_and_log(channel, embed=discord.Embed(
                    description=f"Mounted server: `{new_server}`",
                    color=discord.Color.blurple()))
    elif cmd == "list":
        valid_servers = list(config["servers"].keys())
        embed = discord.Embed(
            title="Available Servers",
            description="\n".join(
                [f"- {name}{' (active)' if name == current_server_name else ''}" for name in valid_servers]
            ),
            color=discord.Color.blurple())
        await send_and_log(channel, embed=embed)
    elif cmd == "help":
        embed = discord.Embed(
            title="Available Commands",
            description=(
                "`s! status` - Check server status/player count.\n"
                "`s! start` - Start the server.\n"
                "`s! stop` - Stop the server if no players online.\n"
                "`s! ip` - Show server IP.\n"
                "`s! mount` - Change active server.\n"
                "`s! list` - List servers.\n"
                "`s! help` - Display this message."
            ),
            color=discord.Color.blurple()
        )
        await send_and_log(channel, embed=embed)
    else:
        embed = discord.Embed(description=f"Unknown command: `{cmd}`.\nTry `s! help` for a list of commands.", color=discord.Color.red())
        await send_and_log(channel, embed=embed)

client.run(TOKEN)

