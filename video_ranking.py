import requests
import json
import time
from operator import itemgetter

class BilibiliCrawler:
    def __init__(self):
        # 设置请求头，模拟浏览器访问
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://www.bilibili.com/v/popular/rank/all',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Origin': 'https://www.bilibili.com',
            'Cookie': "buvid3=2D4B09A5-0E5F-4537-9F7C-E293CE7324F7167646infoc"  # 添加一个基础的Cookie
        }
        # API接口地址
        self.video_info_api = 'https://api.bilibili.com/x/web-interface/view'  # 视频信息API
        self.online_count_api = 'https://api.bilibili.com/x/player/online/total'  # 在线人数API
        self.ranking_api = 'https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all'  # 修改为完整的排行榜API
        self.results = {}  # 存储结果

    def get_ranking_videos(self):
        """获取B站排行榜视频列表"""
        try:
            response = requests.get(self.ranking_api, headers=self.headers)
            data = response.json()
            if data['code'] == 0:
                return data['data']['list']
            return []
        except Exception as e:
            print(f"获取排行榜失败: {e}")
            return []

    def get_online_count(self, bvid, cid):
        """获取视频实时在线观看人数
        
        Args:
            bvid: 视频的BV号
            cid: 视频的cid
        
        Returns:
            str: 格式化的在线人数，如 "1000+"、"1.2万+"
        """
        params = {
            'bvid': bvid,
            'cid': cid
        }
        try:
            response = requests.get(self.online_count_api, params=params, headers=self.headers)
            data = response.json()
            return data['data']['total'] if data['code'] == 0 else "0"
        except:
            return "0"

    def convert_count_to_number(self, count_str):
        """将格式化的人数转换为具体数字
        
        Args:
            count_str: 格式化的人数字符串，如 "1000+"、"1.2万+"
        
        Returns:
            int: 转换后的具体数字
        """
        if '万+' in count_str:
            return int(float(count_str.replace('万+', '')) * 10000)
        elif '000+' in count_str:
            return int(count_str.replace('000+', '000'))
        return int(count_str)

    def display_ranking(self):
        # 按在线人数排序
        sorted_videos = sorted(self.results.items(), key=lambda x: x[1]['count_num'], reverse=True)
        
        print("\n=== B站视频实时在线人数排行榜 ===")
        print(f"更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        for i, (bvid, info) in enumerate(sorted_videos[:20], 1):  # 只显示前20个
            print(f"{i:2d}. {info['online_count']:>8} | {info['title'][:30]:30} | UP主: {info['owner']}")
        
        print("\n" + "="*50)

    def run(self):
        """主运行函数"""
        videos = self.get_ranking_videos()
        print(f"获取到 {len(videos)} 个视频")
        
        for item in videos:
            bvid = item['bvid']
            cid = item['cid']
            online_count = self.get_online_count(bvid, cid)
            count_num = self.convert_count_to_number(online_count)
            
            self.results[bvid] = {
                'title': item['title'],
                'owner': item['owner']['name'],
                'mid': str(item['owner']['mid']),
                'pic': item['pic'],
                'online_count': online_count,
                'count_num': count_num
            }
            time.sleep(1)

        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        
        self.display_ranking()

if __name__ == '__main__':
    crawler = BilibiliCrawler()
    crawler.run() 