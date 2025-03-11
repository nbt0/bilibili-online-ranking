import requests
import json
import re

def get_online_ranking():
    url = "https://api.bilibili.com/x/web-interface/ranking/v2"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    response = requests.get(url, headers=headers)
    data = response.json()
    
    videos = []
    if data['code'] == 0:
        for item in data['data']['list']:
            videos.append({
                'title': item['title'],
                'url': f"https://www.bilibili.com/video/{item['bvid']}",
                'up': item['owner']['name'],
                'online': item['stat']['view']
            })
    
    return videos

if __name__ == '__main__':
    videos = get_online_ranking()
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(videos, f, ensure_ascii=False, indent=2) 