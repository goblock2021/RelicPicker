# Relic Picker v5

黑夜君临遗物定向选择工具。通过 Smithbox gRPC 接口修改游戏参数，实现指定词条的遗物抽取。

## 使用方法

1. 打开 [Smithbox](https://github.com/vawser/Smithbox/releases/latest)（需要 2.2.4 或更新版本）
2. 创建项目，启用 Param Editor
3. 启动游戏
4. 运行 Relic Picker v5
5. 选择商店来源和颜色
6. 添加想要的效果（最多 3 个）
7. 点击「应用」——自动修改 ItemTable 和 AttachEffectTable 权重参数
8. 去游戏里找对应颜色的遗物，抽取即可获得指定词条

### 深夜遗物

切换到深夜商店后，效果分为两组：

- **强效（需诅咒）**：效果更强，但必须附带一个诅咒
- **弱效（无诅咒）**：效果较弱，无需诅咒

### 遗物盒

配置好的组合可以存入遗物盒，支持：

- 双击加载到主界面
- 分组管理（按商店自动分类）
- 搜索过滤
- 多选批量操作
- 导出为 v4 兼容格式

## 从源码运行

```bash
pip install grpcio grpcio-tools pywebview
cd relic_picker_v4/v5
python main.py
```

调试模式（开启浏览器开发者工具）：

```bash
python main.py --debug
```

## 打包

```bash
pip install pyinstaller
build.bat
```

输出：`dist/RelicPicker_v5.exe`

## 技术架构

```
main.py          → pywebview 桌面窗口
api.py           → JS ↔ Python 桥接层
loader.py        → 从 Smithbox 加载遗物/效果数据
matcher.py       → 效果匹配算法（回溯 + compatId 去重）
models.py        → 数据模型
client.py        → gRPC 客户端
static/          → 前端 UI (HTML/CSS/JS)
proto/           → protobuf 生成文件
```
