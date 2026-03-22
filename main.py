from datetime import datetime
import math
import os
from time import sleep
import traceback
from dotenv import load_dotenv
import discogs_client
from discogs_client.models import Release, WantlistItem, Wantlist
from discordwebhook import Discord
import json
from typing import TypedDict

load_dotenv()

WANTLIST_FILE = f'{os.path.dirname(os.path.abspath(__file__))}/data/wantlist_cache.json'

DISCORD_CHANNEL = Discord(url=os.environ.get('DISCORD_WEBHOOK'))
DISCORD_USER = os.environ.get('USER_ID')

class MinimalRelease(TypedDict):
    title: str
    lowest_price: float

def format_release(release: Release) -> MinimalRelease:
    price = 10.0 ** 100
    if release.marketplace_stats.num_for_sale > 0:
        price = release.marketplace_stats.lowest_price.value

    return {
        'title': f'{release.title}: {", ".join([format.get("text", "") for format in release.formats])}',
        'lowest_price': price
    }

def save_wantlist_map(wantlist_map: dict[int, MinimalRelease]):
    data = {}
    for id, release in wantlist_map.items():
        data[str(id)] = {
            'title': release['title'],
            'lowest_price': release['lowest_price']
        }
    
    with open(WANTLIST_FILE, 'w') as f:
        json.dump(data, f)

def load_wantlist_map(client: discogs_client.Client) -> dict[int, MinimalRelease]:
    """Load wantlist map from JSON file, or initialize if doesn't exist"""
    wantlist_map = {}
    
    if os.path.exists(WANTLIST_FILE):
        try:
            with open(WANTLIST_FILE, 'r') as f:
                data = json.load(f)
            
            # Rebuild the Price objects from cached data
            for id_str, item_data in data.items():
                if not isinstance(item_data, dict) or 'title' not in item_data or 'lowest_price' not in item_data:
                    log_msg(f'Invalid data format for release ID {id_str} in wantlist cache. Skipping.', tag=True)
                    continue

                id = int(id_str)
                wantlist_map[id] = {
                    'title': item_data['title'],
                    'lowest_price': item_data['lowest_price']
                }
        except Exception as e:
            log_msg(f'Error loading wantlist cache: {e}. Reinitializing.', tag=True)
            wantlist_map = init_wantlist_map(client, client.identity().wantlist)
    else:
        wantlist_map = init_wantlist_map(client, client.identity().wantlist)
    
    return wantlist_map

def init_wantlist_map(client: discogs_client.Client, wantlist: Wantlist) -> dict[int, MinimalRelease]:
    wantlist_map = {}
    for item in wantlist:
        if not isinstance(item, WantlistItem):
            continue

        id = item.id
        release = client.release(id)
        wantlist_map[id] = format_release(release)

    return wantlist_map

def update_wantlist_map(client: discogs_client.Client, wantlist: Wantlist, wantlist_map: dict[int, MinimalRelease]):
    # Add new items to the wantlist map
    for item in wantlist:
        if not isinstance(item, WantlistItem):
            continue

        id = item.id
        if id not in wantlist_map:
            release = client.release(id)
            wantlist_map[id] = format_release(release)
        
    # Remove items that are no longer in the wantlist
    wantlist_ids = [item.id for item in wantlist if isinstance(item, WantlistItem)]
    for id in list(wantlist_map.keys()):
        if id not in wantlist_ids:
            del wantlist_map[id]

def check_for_price_drops(client: discogs_client.Client, wantlist_map: dict[int, MinimalRelease]):
    for id, release in wantlist_map.items():
        lowest_price = release['lowest_price']

        updated_release = client.release(id)

        if updated_release.marketplace_stats.num_for_sale > 0:
            updated_lowest_price = updated_release.marketplace_stats.lowest_price.value

            if updated_lowest_price < lowest_price:
                log_price_drop(release, updated_lowest_price)
            
        # Want to always use the latest release, price may have increased
        wantlist_map[id] = format_release(updated_release)

def log_msg(text, tag=False):
    now = datetime.now()
    dt_string = now.strftime('%m/%d/%Y %H:%M:%S')

    msg = f'{dt_string} - {text}'

    print(msg)
    DISCORD_CHANNEL.post(content=f'{f"<@{DISCORD_USER}> " if tag else ""}{msg}')
    
def log_price_drop(release: MinimalRelease, new_price: float):
    log_msg(f'Price drop for {release["title"]}! Old price: ${release["lowest_price"]}, New price: ${new_price}', tag=True)

def main():
    try:
        client = discogs_client.Client('Discogs Watchlist Alert/1.0', user_token=os.getenv('DISCOGS_USER_TOKEN'))
        user = client.identity()
        
        # Load from disk or initialize
        wantlist_map = load_wantlist_map(client)
        log_msg(f'Loaded wantlist map with {len(wantlist_map)} items')

        # Single check run
        update_wantlist_map(client, user.wantlist, wantlist_map)
        check_for_price_drops(client, wantlist_map)
        
        # Save state for next cron invocation
        save_wantlist_map(wantlist_map)
        log_msg(f'Wantlist map saved with {len(wantlist_map)} items')
    except Exception as e:
        log_msg(f'Error in main execution: {e}', tag=True)
        traceback.print_exc()

if __name__ == '__main__':
    main()