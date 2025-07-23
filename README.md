# IPTV-Sources

这是一个自动收集、检测和合并 IPTV 直播源的项目，支持为每个频道提供多个直播源，当主源失效时可自动切换到备用源。  
项目通过 GitHub Actions 自动运行，每 6 小时更新一次。

## 特点

- 🔄 **自动收集**：从多个来源自动收集 IPTV 直播源
- 🛠️ **自动检测**：定期检测直播源有效性和性能
- 🔍 **去重合并**：对相同频道的不同源进行去重和合并
- 📺 **多源备份**：每个频道保留多个备选源，主源失效时自动切换
- 📋 **分类整理**：频道按类别整理，方便查找
- ⏱️ **定时更新**：通过 GitHub Actions 每 6 小时自动更新一次

## 直播源地址

您可以直接使用以下地址作为您的 IPTV 播放源：

- [https://raw.githubusercontent.com/cs3306/IPTV-Sources/main/data/output/iptv_collection.m3u](https://raw.githubusercontent.com/cs3306/IPTV-Sources/main/data/output/iptv_collection.m3u)  

## 播放器支持

本项目生成的直播源为标准 M3U 格式，绝大多数支持 M3U 播放列表的 IPTV 播放器均可直接使用。

只需在播放器中导入上述 URL，即可播放频道。

## 项目原理

本项目的工作流程如下：

- **收集阶段**：从多个预设的资源库和网站收集 M3U 格式的直播源
- **检测阶段**：检测每个直播源的可用性和性能表现
- **去重合并阶段**：对同一频道的多个源进行整合，并按性能排序
- **生成阶段**：生成支持多源切换的 M3U 文件，并按分类整理
- **更新阶段**：通过 GitHub Actions 自动定期执行以上流程

## 致谢

感谢以下项目和资源提供的直播源支持：

- [iptv-org/iptv](https://github.com/iptv-org/iptv)
- [YanG-1989/m3u](https://github.com/YanG-1989/m3u)
- [tv.iill.top](https://tv.iill.top)
- [live.zbds.top](https://live.zbds.top)
- [live.fanmingming.com](https://live.fanmingming.com)
- [MercuryZz/IPTVN](https://github.com/MercuryZz/IPTVN)
- [Moexin/IPTV](https://github.com/Moexin/IPTV)
- [gnodgl/IPTV](https://github.com/gnodgl/IPTV)
- [lalifeier/IPTV](https://github.com/lalifeier/IPTV)
- [cuikaipeng/IPTV](https://github.com/cuikaipeng/IPTV)
- [vicjl/myIPTV](https://github.com/vicjl/myIPTV)
- [skddyj/iptv](https://github.com/skddyj/iptv)
- [fenxp/iptv](https://github.com/fenxp/iptv)
- [Rivens7/Livelist](https://github.com/Rivens7/Livelist)
- [Guovin/TV](https://github.com/Guovin/TV)
- [qwerttvv/Beijing-IPTV](https://github.com/qwerttvv/Beijing-IPTV)
- [drangjchen/IPTV](https://github.com/drangjchen/IPTV)
- [YueChan/Live](https://github.com/YueChan/Live)
- [Ftindy/IPTV-URL](https://github.com/Ftindy/IPTV-URL)
- [jazzforlove/IPTV](https://github.com/jazzforlove/IPTV)
- [joevess/IPTV](https://github.com/joevess/IPTV)
- [GlowsSama/IPTV](https://github.com/GlowsSama/IPTV)
- [zbefine/iptv](https://github.com/zbefine/iptv)
- [BigBigGrandG/IPTV-URL](https://github.com/BigBigGrandG/IPTV-URL)

以及所有提供稳定直播源的网站和个人。

## 免责声明

本项目仅用于学习和技术研究，不存储任何媒体内容。  
所有内容均来自互联网公开的直播源，请确保您在合法的前提下使用。

## 许可证

[MIT License](LICENSE)
