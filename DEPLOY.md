# ClawBio Deployment — macOS

## Discord Bot (RoboTerri)

### Prerequisites
```bash
pip3 install discord.py openai python-dotenv
```

### Environment variables (in `.env`)
| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token from https://discord.com/developers/applications |
| `DISCORD_CHANNEL_ID` | Channel ID the bot listens on (right-click channel > Copy Channel ID) |
| `LLM_API_KEY` | OpenAI (or compatible) API key |
| `LLM_BASE_URL` | Optional — custom endpoint for OpenAI-compatible providers |
| `CLAWBIO_MODEL` | Optional — defaults to `gpt-4o` |

### Running manually
```bash
cd bot && python3 roboterri_discord.py
```

### Running persistently via launchd

The bot runs as a macOS Launch Agent that auto-starts on login and restarts on crash.

**Plist location**: `~/Library/LaunchAgents/com.clawbio.roboterri-discord.plist`

```bash
# Load (start)
launchctl load ~/Library/LaunchAgents/com.clawbio.roboterri-discord.plist

# Unload (stop)
launchctl unload ~/Library/LaunchAgents/com.clawbio.roboterri-discord.plist

# Check status
launchctl list | grep roboterri

# View logs
tail -f ~/Library/Logs/roboterri-discord.err.log
tail -f ~/Library/Logs/roboterri-discord.out.log
```

### Updating
After changing bot code or `.env`, restart:
```bash
launchctl unload ~/Library/LaunchAgents/com.clawbio.roboterri-discord.plist
launchctl load ~/Library/LaunchAgents/com.clawbio.roboterri-discord.plist
```
