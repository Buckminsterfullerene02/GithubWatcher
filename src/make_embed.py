import discord
import re
from markdownify import markdownify as md

def MakeEmbed(json):
  embed = discord.Embed()
  user = json["actor"]["login"]
  userlink = f'https://github.com/{user}'
  repo = json["repo"]["name"]
  embed.set_thumbnail(url=json["actor"]["avatar_url"])
  embed.color = discord.Colour.from_str("#43f770")

  if json["type"] == "WatchEvent":
    embed.title = '' # so it isnt None on the length check and raises a error
    embed.description = f'[{user}]({userlink}) starred {repo}!'
  elif json["type"] == "IssueCommentEvent" and json["payload"]["action"] == "created":
    embed.url = json["payload"]["issue"]["html_url"]
    embed.title = f'{user} commented on {json["repo"]["name"]}#{json["payload"]["issue"]["number"]}'
    embed.description = json["payload"]["comment"]["body"]
  elif json["type"] == "IssuesEvent":
    link = json["payload"]["issue"]["html_url"]
    issue = f'{json["repo"]["name"]}#{json["payload"]["issue"]["number"]}'
    embed.url = link
    if json["payload"]["action"] == "opened":
      embed.title = f'{user} opened issue: {issue} {json["payload"]["issue"]["title"]}'
      embed.description = json["payload"]["issue"]["body"]
    elif json["payload"]["action"] == "closed":
      embed.title = f'{user} closed issue: {issue}'
      embed.description = f'reason: {json["payload"]["issue"]["state_reason"]}'
  elif json["type"] == "PullRequestEvent":
    link = json["payload"]["pull_request"]["html_url"]
    embed.url = link
    pr = f'{json["repo"]["name"]}#{json["payload"]["number"]}'
    if json["payload"]["action"] == "opened":
      embed.title = f'{user} opened pull request: {pr} {json["payload"]["pull_request"]["title"]}'
      embed.description = json["payload"]["pull_request"]["body"]
    elif json["payload"]["action"] == "closed":
      embed.title = f'{user} closed pull request: {pr}'
      embed.description = f'merged: {json["payload"]["pull_request"]["merged"]}'
  elif json["type"] == "ForkEvent":
     link = f'https://github.com/{json["payload"]["forkee"]["full_name"]}'
     embed.url = link
     embed.title = f'{user} forked {repo}'
     embed.description = '' # so it isnt None on the length check and raises a error
  elif json["type"] == "ReleaseEvent":
     link = json["payload"]["release"]["html_url"]
     embed.url = link
     embed.title = f'{user} published a release for {repo}: {json["payload"]["release"]["tag_name"]}'
     embed.description = json["payload"]["release"]["body"]
  elif json["type"] == "PushEvent":
     link = f'https://github.com/{repo}/compare/{json["payload"]["before"]}..{json["payload"]["head"]}'
     embed.url = link
     embed.title = f'{user} pushed {json["payload"]["size"]} commit(s) to {repo}'
     embed.description = ''
     for i in json["payload"]["commits"]:
       embed.description += f'[{i["sha"][0:6]}]({trimlink(i["url"])}) - {i["message"]} - {i["author"]["name"]} \n'
  else:
     return None

  if len(embed.description) > 4096:
    embed.description = "[body was too long, please visit the link]"

  if len(embed.title) > 256:
    embed.title = "[title was too long, click here]"
    
  return embed

# I tried to make it like the github webhook release embed but it kinda sucks
def MakeReleaseEmbed(release_data, repo_name):
    embed = discord.Embed()
    
    author = release_data["author"]["login"]
    author_url = release_data["author"]["html_url"]
    author_avatar = release_data["author"]["avatar_url"]
    
    embed.set_author(name=f"{author}", url=author_url, icon_url=author_avatar)
    embed.color = discord.Colour.from_str("#238636")
    
    embed.title = f"Release {release_data['tag_name']}"
    embed.url = release_data["html_url"]
    
    repo_url = f"https://github.com/{repo_name}"
    embed.description = f"**[{repo_name}]({repo_url})**\n"
    
    if release_data["name"] and release_data["name"] != release_data["tag_name"]:
        embed.description += f"**{release_data['name']}**\n"
    
    if release_data.get("body"):
        body = sanitize_release_body(release_data["body"])
        if len(body) > 500:
            body = body[:500] + "..."
        embed.description += f"\n{body}"
    
    if release_data.get("prerelease"):
        embed.add_field(name="Type", value="Pre-release", inline=True)
    elif release_data.get("draft"):
        embed.add_field(name="Type", value="Draft", inline=True)
    else:
        embed.add_field(name="Type", value="Release", inline=True)
    
    if release_data.get("published_at"):
        from datetime import datetime
        pub_date = datetime.fromisoformat(release_data["published_at"].replace('Z', '+00:00'))
        embed.timestamp = pub_date
    
    embed.set_footer(text="GitHub", icon_url="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png")
    
    return embed

def trimlink(api_link: str):
  link = api_link.replace("api.", "")
  link = link.replace("repos/", "")
  link = link.replace("commits", "commit")
  return link

def sanitize_release_body(body):
    if not body:
        return ""
    
    # no images n links
    body = md(body, strip=['img', 'a'])  
    
    # no excessive newlines
    body = re.sub(r'\n{3,}', '\n\n', body)
    
    return body.strip()