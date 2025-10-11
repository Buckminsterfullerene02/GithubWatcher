import discord
import os
import json
import urllib3
import traceback
from datetime import datetime
from dotenv import load_dotenv
from uptime import keep_alive
from discord.ext import commands, tasks
from make_embed import MakeEmbed, MakeReleaseEmbed

class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    GRAY = '\033[90m'

load_dotenv('variables.env')
loop_time = int(os.getenv('loop_time', 300))
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='$', intents=intents)
http = urllib3.PoolManager()
allrepos = []
CONFIG_FILE = os.getenv('config_file')

def log(message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    colored_timestamp = f"{Colors.GRAY}{timestamp}{Colors.RESET}"
    
    if level == "ERROR" or "Error" in message or "Failed" in message:
        colored_message = f"{Colors.RED}{message}{Colors.RESET}"
    elif level == "SUCCESS" or "Successfully" in message or "completed successfully" in message:
        colored_message = f"{Colors.GREEN}{message}{Colors.RESET}"
    elif level == "HEADER" or message.startswith("Starting") or message.startswith("Loading") or message.startswith("Initializing") or message.startswith("Checking"):
        colored_message = f"{Colors.CYAN}{Colors.BOLD}{message}{Colors.RESET}"
    elif "Rate limit" in message:
        colored_message = f"{Colors.YELLOW}{message}{Colors.RESET}"
    elif "Found new" in message or "Sent" in message:
        colored_message = f"{Colors.GREEN}{message}{Colors.RESET}"
    elif "Skipping" in message or "No new" in message or "304" in message:
        colored_message = f"{Colors.GRAY}{message}{Colors.RESET}"
    elif message.startswith("  ") and not message.startswith("    "):
        colored_message = f"{Colors.RESET}{message}{Colors.RESET}"
    else:
        colored_message = message
    
    print(f"{colored_timestamp} {colored_message}")

def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        log("Error: Invalid JSON in config.json", "ERROR")
        return {"repositories": []}
    except FileNotFoundError:
        log("Error: config.json not found", "ERROR")
        return {"repositories": []}

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

@bot.event
async def on_ready():
    log(f'hello world {bot.user}', "HEADER")
    
    config = load_config()
    log(f"Loading {len(config['repositories'])} repositories from config", "HEADER")
    
    for repo_config in config['repositories']:
        if not repo_config or not repo_config.get('url'):
            continue
            
        watcher = GithubWatcher(
            repo_config['url'], 
            repo_config['name'],
            repo_config.get('etag', ''),
            repo_config.get('last_event_id', 0),
            repo_config.get('tracked_events', []),
            repo_config.get('releases_url', ''),
            repo_config.get('releases_etag', ''),
            repo_config.get('last_release_id', 0)
        )
        allrepos.append(watcher)
        log(f"Added watcher for {watcher.name} - Tracking: {watcher.tracked_events}")

    log("Initializing ETags and IDs for all repositories...", "HEADER")
    for i in allrepos:
        await i.set_etag_and_id()

    await save_repositories_to_config()
    log("Starting repository monitoring loop...", "HEADER")
    looprepos.start()

async def save_repositories_to_config():
    config = load_config()
    
    for i, repo in enumerate(allrepos):
        if i < len(config['repositories']):
            config['repositories'][i]['etag'] = repo.Headers.get("if-none-match", "")
            config['repositories'][i]['last_event_id'] = repo.lastid
            config['repositories'][i]['releases_etag'] = repo.releases_headers.get("if-none-match", "")
            config['repositories'][i]['last_release_id'] = repo.last_release_id
    
    save_config(config)
    log("Saved repository states to config", "SUCCESS")

@tasks.loop(seconds=loop_time)
async def looprepos():
    global allrepos
    log(f"cycle loop is a go for {len(allrepos)} repositories...", "HEADER")

    try:
        for i in allrepos:
            log(f"Checking {i.name}...")
            await i.check_github()
        
        await save_repositories_to_config()
        log("Check cycle completed successfully", "SUCCESS")
        
    except Exception as e:
        log(f"Error in loop cycle: {e}", "ERROR")
        traceback.print_exc()

class GithubWatcher:
    def __init__(self, events_url: str, name: str = "", etag: str = "", last_event_id: int = 0, tracked_events: list = None, releases_url: str = "", releases_etag: str = "", last_release_id: int = 0):
        self.url = events_url
        self.releases_url = releases_url or events_url.replace('/events', '/releases')
        self.name = name or events_url[29:].replace('/events', '')
        self.Headers = {
            "if-none-match": etag,
            "authorization": f"token {os.getenv('git_token')}", 
            "Accept": "application/vnd.github+json"
        }
        self.releases_headers = {
            "if-none-match": releases_etag,
            "authorization": f"token {os.getenv('git_token')}", 
            "Accept": "application/vnd.github+json"
        }
        self.lastid = last_event_id
        self.last_release_id = last_release_id
        self.tracked_events = tracked_events or []
        log(f"Created watcher for {self.name} - Events: {self.lastid}, Releases: {self.last_release_id}")
    
    def validate_last_id_exists(self, last_id, data):
        if last_id == 0:
            log(f"  {self.name}: No stored ID to validate (starting fresh)")
            return False
        for item in data:
            if str(item['id']) == str(last_id):
                log(f"  {self.name}: Found stored ID {last_id} in current data")
                return True
        log(f"  {self.name}: Stored ID {last_id} NOT found in current data - will reset")
        return False

    async def set_etag_and_id(self):
        log(f"Initializing {self.name}...")
        
        # initialise events if needed
        if self.lastid == 0 or not self.Headers.get("if-none-match"):
            log(f"  {self.name}: Initializing events (lastid={self.lastid}, etag={bool(self.Headers.get('if-none-match'))})")
            await self.initialize_events()
        else:
            log(f"  {self.name}: Using saved event state (lastid={self.lastid})")
        
        # initialise releases if release event is traked
        if "ReleaseEvent" in self.tracked_events:
            if self.last_release_id == 0 or not self.releases_headers.get("if-none-match"):
                log(f"  {self.name}: Initializing releases (last_release_id={self.last_release_id}, etag={bool(self.releases_headers.get('if-none-match'))})")
                await self.initialize_releases()
            else:
                log(f"  {self.name}: Using saved release state (last_release_id={self.last_release_id})")
        else:
            log(f"  {self.name}: ReleaseEvent not tracked, skipping release initialization")

    async def initialize_events(self):
        try:
            log(f"  {self.name}: Making API request to {self.url}")
            url = http.request('GET', url=self.url, headers=self.Headers, timeout=30)
            log(f"  {self.name}: Events API response: {url.status}")
            
            if url.status == 200 and url.data:
                data = json.loads(url.data)
                if data:
                    old_etag = self.Headers.get("if-none-match", "none")
                    self.Headers["if-none-match"] = url.headers.get("ETag", "")
                    self.lastid = str(data[0]['id'])
                    log(f'  {self.name}: Events initialized - LastID={self.lastid}', "SUCCESS")
                else:
                    log(f"  {self.name}: Empty events data received")
            else:
                log(f"  {self.name}: Failed to initialize events - Status: {url.status}", "ERROR")
        except Exception as e:
            log(f"  {self.name}: Error initializing events: {e}", "ERROR")

    async def initialize_releases(self):
        try:
            log(f"  {self.name}: Making API request to {self.releases_url}")
            url = http.request('GET', url=self.releases_url, headers=self.releases_headers, timeout=30)
            log(f"  {self.name}: Releases API response: {url.status}")
            
            if url.status == 200 and url.data:
                data = json.loads(url.data)
                if data:
                    old_etag = self.releases_headers.get("if-none-match", "none")
                    self.releases_headers["if-none-match"] = url.headers.get("ETag", "")
                    self.last_release_id = data[0]['id']
                    log(f'  {self.name}: Releases initialized - LastReleaseID={self.last_release_id}', "SUCCESS")
                else:
                    log(f"  {self.name}: No releases found")
            else:
                log(f"  {self.name}: Failed to initialize releases - Status: {url.status}", "ERROR")
        except Exception as e:
            log(f"  {self.name}: Error initializing releases: {e}", "ERROR")

    async def check_github(self):
        try:
            id = int(os.getenv('channel_id'))
            channel = bot.get_channel(id)
            log(f"  {self.name}: Starting GitHub check - Tracking: {self.tracked_events}")

            # check rate limit
            try:
                log(f"  {self.name}: Checking rate limit...")
                rate_url = http.request('GET', 'https://api.github.com/rate_limit', headers=self.Headers, timeout=30)
                if rate_url.status == 200:
                    rate_data = json.loads(rate_url.data)
                    remaining = rate_data["resources"]["core"]["remaining"]
                    log(f"  {self.name}: Rate limit remaining: {remaining}")
                    if remaining <= 10:
                        log(f'  {self.name}: Rate limited - skipping check')
                        return
                else:
                    log(f"  {self.name}: Rate limit check failed: {rate_url.status}", "ERROR")
            except Exception as e:
                log(f"  {self.name}: Rate limit check exception: {e}", "ERROR")

            # check releases if ReleaseEvent is tracked
            if "ReleaseEvent" in self.tracked_events:
                log(f"  {self.name}: Checking releases...")
                await self.check_releases(channel)

            # check other events
            other_events = [event for event in self.tracked_events if event != "ReleaseEvent"]
            if other_events:
                log(f"  {self.name}: Checking other events: {other_events}")
                await self.check_events(channel, other_events)
            else:
                log(f"  {self.name}: No other events to check")

            log(f"  {self.name}: GitHub check completed", "SUCCESS")

        except Exception as e:
            log(f"  {self.name}: Unexpected error in check_github: {e}", "ERROR")
            traceback.print_exc()

    async def check_releases(self, channel):
        # more logging than code because this api is wacky af
        try:
            log(f"    {self.name}: Making releases API request...")
            url = http.request('GET', url=self.releases_url, headers=self.releases_headers, timeout=30)

            if url.status == 200:
                data = json.loads(url.data)
                log(f"    {self.name}: Received {len(data) if data else 0} releases")
                
                if not data:
                    log(f"    {self.name}: No releases data")
                    return

                # validate last release exists
                if not self.validate_last_id_exists(self.last_release_id, data):
                    log(f"    {self.name}: Updating to latest release without sending")
                    self.releases_headers["if-none-match"] = url.headers.get("ETag", "")
                    self.last_release_id = data[0]['id']
                    return

                # find new releases
                new_releases = []
                for release in data:
                    if release['id'] != self.last_release_id:
                        new_releases.append(release)
                        log(f"    {self.name}: Found new release: {release.get('tag_name', 'Unknown')} (ID: {release['id']})")
                    else:
                        log(f"    {self.name}: Reached known release {release['id']}, stopping")
                        break

                # send discord messages for new releases (oldest first)
                for release in reversed(new_releases):
                    try:
                        log(f"    {self.name}: Sending release embed for {release.get('tag_name', 'Unknown')}")
                        embed = MakeReleaseEmbed(release, self.name)
                        if embed:
                            await channel.send(embed=embed)
                            log(f"    {self.name}: Successfully sent release {release.get('tag_name', 'Unknown')}")
                        else:
                            log(f"    {self.name}: Failed to create embed for release", "ERROR")
                    except Exception as e:
                        log(f"    {self.name}: Error sending release embed: {e}", "ERROR")

                if new_releases:
                    old_id = self.last_release_id
                    self.releases_headers["if-none-match"] = url.headers.get("ETag", "")
                    self.last_release_id = data[0]['id']
                    log(f'    {self.name}: Updated release tracking from {old_id} to {self.last_release_id} ({len(new_releases)} new)', "SUCCESS")

            elif url.status == 304:
                log(f"    {self.name}: No new releases (304)")

            else:
                log(f"    {self.name}: Releases API error: {url.status}", "ERROR")

        except Exception as e:
            log(f"    {self.name}: Error checking releases: {e}", "ERROR")

    async def check_events(self, channel, tracked_events):
        try:
            log(f"    {self.name}: Making events API request...")
            url = http.request('GET', url=self.url, headers=self.Headers, timeout=30)
            log(f"    {self.name}: Events response: {url.status}")

            if url.status == 200:
                data = json.loads(url.data)
                log(f"    {self.name}: Received {len(data) if data else 0} events")
                
                if not data:
                    log(f"    {self.name}: No events data")
                    return

                # TODO: there is bug that if the last tracked event is very old and not in the last 30 events
                # it resets to the latest event and skips sending anything
                # but idk how to fix it without storing even more state, just seems like a pain
                # and anyway idrc about this events stuff, mostly the releases
                if not self.validate_last_id_exists(self.lastid, data):
                    log(f"    {self.name}: Updating to latest event without sending")
                    self.Headers["if-none-match"] = url.headers.get("ETag", "")
                    self.lastid = str(data[0]['id'])
                    return

                # find new events
                new_events = []
                for event in data:
                    if str(event['id']) != str(self.lastid):
                        new_events.append(event)
                        log(f"    {self.name}: Found new event: {event['type']} (ID: {event['id']})")
                    else:
                        log(f"    {self.name}: Reached known event {event['id']}, stopping")
                        break

                # filter and send events
                tracked_count = 0
                for event in reversed(new_events):
                    if event['type'] in tracked_events:
                        tracked_count += 1
                        try:
                            log(f"    {self.name}: Sending {event['type']} embed...")
                            embed = MakeEmbed(event)
                            if embed:
                                await channel.send(embed=embed)
                                log(f"    {self.name}: Successfully sent {event['type']} event")
                            else:
                                log(f"    {self.name}: Failed to create embed for {event['type']}", "ERROR")
                        except Exception as e:
                            log(f"    {self.name}: Error sending event embed: {e}", "ERROR")
                    else:
                        log(f"    {self.name}: Skipping {event['type']} (not tracked)")

                if new_events:
                    old_id = self.lastid
                    self.Headers["if-none-match"] = url.headers.get("ETag", "")
                    self.lastid = str(data[0]['id'])
                    log(f'    {self.name}: Updated event tracking from {old_id} to {self.lastid} ({tracked_count}/{len(new_events)} tracked)', "SUCCESS")

            elif url.status == 304:
                log(f"    {self.name}: No new events (304)")

            else:
                log(f"    {self.name}: Events API error: {url.status}", "ERROR")

        except Exception as e:
            log(f"    {self.name}: Error checking events: {e}", "ERROR")
    
keep_alive()
bot.run(os.getenv('discord_token'))