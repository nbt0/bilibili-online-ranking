# Bilibili 在线人数排行榜
网址·[https://nbt0.github.io/bilibili-online-ranking/](https://nbt0.github.io/bilibili-online-ranking/)
实时统计B站热门视频的在线观看人数，并提供排行榜展示。

## 功能特点

- 获取B站热门视频实时在线人数
- 支持按在线人数排序
- 支持按标题和UP主搜索
- 自动每5分钟更新数据
- 美观的响应式界面

## 本地运行

1. 克隆仓库

bash
git clone https://github.com/nbt0/bilibili-online-ranking.git
cd bilibili-online-ranking

2. 安装依赖

bash
pip install requests

3. 运行爬虫

bash
python video_ranking.py

4. 启动本地服务器

bash
python -m http.server 8000

5. 访问 http://localhost:8000

## 更新记录

### v2.0
- 重构代码，优化性能
- 使用实时在线人数API
- 改进界面显示
- 添加搜索和排序功能

## 参考项目

- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)
- [bilibili-banner](https://github.com/palxiao/bilibili-banner/tree/main)：感谢该项目为本站顶部动态横幅提供的实现参考。

## License

MIT
