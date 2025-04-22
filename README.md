Discord bot to start an AWS instance containing a Minecraft server.

Usage: `sudo docker compose up` to start the bot. Run with the `-d` flag if you
don't need to see debug output.

Running the bot once creates a blank config file. Populate it with your server's
details once it has been created.

The bot doesn't run the server application itself; it just puts the AWS instance
up and down. I use a systemd service on the AWS instance to launch and
gracefully stop the server on instance boot or shutdown.
