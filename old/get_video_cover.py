import requests
import json

def get_video_info(bvid):
    """
    获取B站视频的基本信息，包括封面URL
    
    Args:
        bvid: 视频的BV号
    
    Returns:
        dict: 包含视频信息的字典，获取失败返回None
    """
    # 设置请求头，模拟浏览器访问，避免被反爬
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': 'https://www.bilibili.com',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Origin': 'https://www.bilibili.com'
    }
    
    # B站视频信息API接口
    video_info_api = 'https://api.bilibili.com/x/web-interface/view'
    
    params = {'bvid': bvid}
    try:
        response = requests.get(video_info_api, params=params, headers=headers)
        data = response.json()
        if data['code'] == 0:
            video_info = {
                'title': data['data']['title'],
                'owner': data['data']['owner']['name'],
                'pic': data['data']['pic'],  # 封面URL
                'aid': data['data']['aid'],  # AV号
                'bvid': data['data']['bvid'],  # BV号
                'cid': data['data']['cid'],  # 视频CID
                'pubdate': data['data']['pubdate'],  # 发布时间
                'desc': data['data']['desc']  # 视频简介
            }
            return video_info
        else:
            print(f"获取视频信息失败: {data['message']}")
            return None
    except Exception as e:
        print(f"获取视频信息失败: {e}")
        return None

def main():
    # 测试获取BV1NM9mYHE6z的视频信息
    bvid = "BV1NM9mYHE6z"
    print(f"正在获取视频 {bvid} 的信息...")
    
    video_info = get_video_info(bvid)
    if video_info:
        print("\n视频信息获取成功:")
        print(f"标题: {video_info['title']}")
        print(f"UP主: {video_info['owner']}")
        print(f"封面URL: {video_info['pic']}")
        print(f"AV号: {video_info['aid']}")
        print(f"BV号: {video_info['bvid']}")
        print(f"CID: {video_info['cid']}")
        print(f"发布时间: {video_info['pubdate']}")
        print(f"视频简介: {video_info['desc']}")
        
        # 保存到JSON文件
        with open(f"{bvid}_info.json", 'w', encoding='utf-8') as f:
            json.dump(video_info, f, ensure_ascii=False, indent=2)
        print(f"\n视频信息已保存到 {bvid}_info.json")
    else:
        print("获取视频信息失败")

if __name__ == "__main__":
    main()