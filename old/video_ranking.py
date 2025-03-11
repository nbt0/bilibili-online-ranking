import requests
import time
from concurrent.futures import ThreadPoolExecutor
import json
from collections import defaultdict
from bs4 import BeautifulSoup
import re

class BilibiliOnlineRanking:
    """B站视频实时在线人数排行榜爬虫
    
    用于获取B站热门视频的实时在线观看人数，并生成排行榜。
    使用B站官方API接口获取数据，支持多线程处理。
    """
    
    def __init__(self):
        # 设置请求头，模拟浏览器访问，避免被反爬
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://www.bilibili.com',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Origin': 'https://www.bilibili.com'
        }
        # B站API接口地址
        self.popular_api = 'https://api.bilibili.com/x/web-interface/popular'  # 热门视频列表接口
        self.online_api = 'https://api.bilibili.com/x/player/online/total'     # 在线人数接口
        self.video_info_api = 'https://api.bilibili.com/x/web-interface/view'  # 视频信息接口
        # 存储结果的字典，使用defaultdict避免KeyError
        self.results = defaultdict(dict)

    def get_online_count(self, bvid, cid):
        """获取视频实时在线观看人数
        
        Args:
            bvid: 视频的BV号
            cid: 视频分P的id
        
        Returns:
            str: 在线人数(例如: "1000+"、"1.2万+")或None
        """
        params = {
            'bvid': bvid,
            'cid': cid
        }
        try:
            response = requests.get(self.online_api, params=params, headers=self.headers)
            data = response.json()
            if data['code'] == 0:
                return data['data']['total']
            return None
        except Exception as e:
            print(f"获取在线人数失败: {bvid}, 错误: {e}")
            return None

    def get_video_info(self, bvid):
        """获取视频的基本信息
        
        Args:
            bvid: 视频的BV号
        
        Returns:
            dict: 包含视频标题、cid、UP主名称和封面URL的字典，获取失败返回None
        """
        params = {'bvid': bvid}
        try:
            response = requests.get(self.video_info_api, params=params, headers=self.headers)
            data = response.json()
            if data['code'] == 0:
                return {
                    'title': data['data']['title'],
                    'cid': data['data']['cid'],
                    'owner': data['data']['owner']['name'],
                    'pic': data['data']['pic']  # 添加封面URL字段
                }
            return None
        except Exception as e:
            print(f"获取视频信息失败: {bvid}, 错误: {e}")
            return None

    def convert_count_to_number(self, count_str):
        """将在线人数字符串转换为数字用于排序
        
        Args:
            count_str: 在线人数字符串(例如: "1000+"、"1.2万+")
        
        Returns:
            int: 转换后的数字(例如: 1000、12000)
        """
        try:
            if '万' in count_str:
                num = float(count_str.replace('万+', '')) * 10000
                return int(num)
            else:
                return int(count_str.replace('+', ''))
        except:
            return 0

    def process_video(self, bvid):
        """处理单个视频：获取信息和在线人数
        
        Args:
            bvid: 视频的BV号
        """
        print(f"正在处理视频 {bvid}...")
        video_info = self.get_video_info(bvid)
        if video_info:
            time.sleep(0.5)  # 添加延时避免请求过快
            online_count = self.get_online_count(bvid, video_info['cid'])
            if online_count:
                count_num = self.convert_count_to_number(online_count)
                self.results[bvid] = {
                    'title': video_info['title'],
                    'owner': video_info['owner'],
                    'online_count': online_count,  # 原始字符串，用于显示
                    'count_num': count_num,        # 转换后的数字，用于排序
                    'pic': video_info['pic']       # 添加封面URL到结果中
                }
                print(f"视频 {bvid} ({video_info['title']}) 当前在线: {online_count}")

    def get_popular_bvids(self):
        """从B站API获取热门视频BV号列表
        
        Returns:
            list: BV号列表
        """
        try:
            print("正在获取热门视频列表...")
            params = {
                'ps': 50,  # 每页数量
                'pn': 1    # 页码
            }
            response = requests.get(self.popular_api, params=params, headers=self.headers)
            data = response.json()
            
            if data['code'] == 0:
                bvids = []
                for item in data['data']['list']:
                    bvids.append(item['bvid'])
                
                print(f"成功获取到 {len(bvids)} 个热门视频")
                return bvids
            else:
                print(f"获取视频列表失败: {data['message']}")
                return []
        except Exception as e:
            print(f"获取热门视频列表失败: {e}")
            return []

    def get_popular_videos(self):
        """获取并处理所有热门视频信息"""
        bvids = self.get_popular_bvids()
        
        # 使用线程池并发处理视频信息
        with ThreadPoolExecutor(max_workers=5) as executor:  # 限制并发数避免请求过快
            executor.map(self.process_video, bvids)

    def print_ranking(self):
        """打印排序后的视频排行榜"""
        sorted_results = sorted(
            self.results.items(), 
            key=lambda x: x[1]['count_num'],  # 按转换后的数字排序
            reverse=True
        )
        
        print("\n=== B站视频实时在线人数排行榜 ===")
        print("排名  在线人数  UP主          标题")
        print("-" * 50)
        
        for rank, (bvid, info) in enumerate(sorted_results, 1):
            print(f"{rank:2d}.  {info['online_count']:8s}  {info['owner']:<12s} {info['title']}")

    def save_results(self):
        """将结果排序并保存到JSON文件"""
        sorted_results = dict(sorted(
            self.results.items(), 
            key=lambda x: x[1]['count_num'], 
            reverse=True
        ))
        
        # 先打印排行榜
        self.print_ranking()
        
        # 保存到JSON文件
        with open('bilibili_online_ranking.json', 'w', encoding='utf-8') as f:
            json.dump(sorted_results, f, ensure_ascii=False, indent=2)

def main():
    """主函数"""
    ranking = BilibiliOnlineRanking()
    ranking.get_popular_videos()
    ranking.save_results()
    print("排行榜数据已保存到 bilibili_online_ranking.json")

if __name__ == "__main__":
    main()