Quick and dirty bot to start a Minecraft server running on AWS through Discord.

Usage: `python bot.py` to start the bot. Run it in a screen instance if you don't want it to take up your shell. Run with `-v` for more console output.

There is a script provided to automatically run it in a screen `run_in_screen.sh`. Run `./run_in_screen.sh -d` to automatically detach.

It doesn't actually run any the server application itself; it just puts the AWS instance up and down. I launch the server on instance boot and gracefully stop it on shutdown with systemd.
