# 鹈鹕重置屏 · Reset Pelican

一只骑车的鹈鹕，替你盯着 Codex 额度什么时候重置。

<p>
  <img src="docs/img/idle.gif" width="240" alt="日常划水中">
  <img src="docs/img/ride.gif" width="240" alt="即将重置！使劲蹬！">
  <img src="docs/img/rest.gif" width="240" alt="已重置！歇一歇…">
</p>

一块 1.54 寸的桌面小屏（ESP32-C3），联网后每 5 分钟查一次重置状态：平时鹈鹕双脚蹬地划水；36 小时内要重置时，它骑着儿童车拼命蹬；重置完的 72 小时里，它瘫在地上歇。手机配网，一个按键操作。

**📖 使用说明书：[docs/index.html](docs/index.html)**（给拿到设备的人看：三种画面、按键、配网、常见问题）

---

## 目录

- [功能](#功能)
- [硬件清单](#硬件清单)
- [复刻指南](#复刻指南)
- [改画面、改接口、换硬件](#改画面改接口换硬件)
- [工作原理](#工作原理)
- [项目结构](#项目结构)

## 功能

| 画面 | 什么时候出现 |
|---|---|
| 日常划水中 | 没有重置消息 |
| 即将重置！使劲蹬！ | 接口报告 36 小时内会重置 |
| 已重置！歇一歇… | 重置完成后 72 小时内 |

- **三种模式**：演示（短按切换画面，不联网）、正式（联网自动选画面）、配网（手机设置 Wi-Fi）。
- **一个按键**：短按 / 按住 3 秒 / 按住 10 秒，松手执行，按住时屏幕提示松手后的动作。
- **手机配网**：设备开热点，手机连上自动弹出配网页，不需要电脑。
- **不卡顿**：联网在后台任务里跑，动画始终 9–14 帧/秒。

## 硬件清单

### 方案 A：01Studio pyClock 套件（推荐，零改动）

本项目就是在这块板子上开发的，固件直接烧录即可。

| 部件 | 规格 |
|---|---|
| 01Studio pyClock | ESP32-C3-WROOM-02（4 MB 闪存）、1.54 寸 240×240 ST7789 屏、USB-C、一个按键、一个蓝色 LED |
| USB-C 数据线 | 能传数据的线，不是只能充电的 |

pyClock 主要在 01Studio 的淘宝店销售，下单前确认库存。

### 方案 B：通用模块自己组装

| 部件 | 规格 | 备注 |
|---|---|---|
| ESP32-C3 开发板 | 至少 4 MB 闪存，如 ESP32-C3 SuperMini | 固件约 2.3 MB，需要 3 MB 应用分区 |
| 1.54 寸 SPI 屏 | 240×240，ST7789 驱动，**带 CS 引脚** | 很多 7 针模块没有 CS，驱动方式要改 |
| 轻触按键 | 一个 | 接 GPIO 和 GND，程序里开了内部上拉 |
| 外壳 | 3D 打印 | 可选 |

换了硬件要改两处：引脚（见 [换硬件](#换硬件)），以及可能要重新调屏幕颜色参数。

### 以后可以加

pyClock 上还有 6 个空闲 GPIO：GPIO0 从 J1 三针座（GPIO0 / 3.3V / GND）引出；GPIO1、3、10、20、21 没引出，要焊在模组焊盘上。

| 想加 | 建议引脚 | 说明 |
|---|---|---|
| 闪灯 | GPIO0（J1） | 现成的三针座；亮灯要加三极管 |
| 蜂鸣器 | GPIO10 | 不是启动引脚，上电不会乱响；无源压电蜂鸣器可直接驱动 |
| 电池电量 | GPIO3 | 支持模拟量读取，电池电压经电阻分压后接入 |
| 电池本身 | — | 需带保护的锂电池充电板（如 TP4056）、电源开关、防倒灌二极管；屏幕背光常亮，1000 mAh 约 8–12 小时（估算） |

不建议用 GPIO20/21：它们是串口 0，上电时会输出启动信息。

## 复刻指南

### 0. 准备

- 安装 [uv](https://docs.astral.sh/uv/)。PlatformIO、esptool 都通过 `uvx` 临时运行，不用单独安装。
- 用数据线把板子接到电脑。macOS 上串口一般是 `/dev/cu.usbmodem*`，Linux 是 `/dev/ttyACM0`，Windows 是 `COMx`。

```bash
git clone https://github.com/hal3000-t2025/esp32-reset-pelican.git
cd esp32-reset-pelican
```

### 1. 先备份原厂固件

烧录会覆盖整块闪存上的应用。先把 4 MB 原样读出来，以后想恢复原厂程序就靠它：

```bash
uvx --from esptool esptool --port /dev/cu.usbmodem21101 read-flash 0 0x400000 original-backup.bin
```

备份里可能有你以前存过的 Wi-Fi 密码，别上传。

### 2. 编译并烧录

帧数据 `include/pelican_frames.h` 已经生成好放在仓库里，不装字体也能直接编译：

```bash
uvx --from platformio --with esptool==4.11.0 pio run -t upload
```

PlatformIO 会自动找串口；找错了就在后面加 `--upload-port <串口>`。第一次编译要下载工具链，需要几分钟。

烧录后设备开机进入**演示模式**，短按按键切换三个画面。

### 3. 配网，进入正式模式

按住按键 3 秒后松手。第一次会直接进入配网：手机连上屏幕上显示的 `Pelican-XXXX` 热点，在弹出的页面里选 Wi-Fi、填密码（只支持 2.4 GHz）。连上后设备自动进入正式模式。详见[说明书](docs/index.html#setup)。

### 恢复原厂固件

```bash
uvx --from esptool esptool --port /dev/cu.usbmodem21101 write-flash 0 original-backup.bin
```

### 串口日志

```bash
uvx --from platformio pio device monitor
```

每 5 秒一行帧率统计（含剩余内存 `heap=`），另有按键、模式切换、联网、接口结果、配网过程的日志。

## 改画面、改接口、换硬件

### 改画面

所有画面都在 `render_pelican.py` 里，用 Pillow 以 4 倍分辨率绘制再缩小抗锯齿。改完重新生成，再烧录：

```bash
uv run --with pillow python render_pelican.py
uvx --from platformio --with esptool==4.11.0 pio run -t upload
```

脚本会同时更新 `include/pelican_frames.h`、`preview/`（动图和逐帧拼图，方便检查）和 `docs/img/`（说明书用图）。

- 中文字体：优先用 [阿里巴巴普惠体](https://www.alibabafonts.com/) Heavy（`~/Library/Fonts/Alibaba-PuHuiTi-Heavy.ttf`，免费商用），没有则退回 macOS 自带的冬青黑体 / 华文黑体。其他系统改 `FONT_CANDIDATES`。
- 配网说明页里的热点名示例用了 macOS 的 Menlo 字体。
- 「使劲蹬」的腿速：`render_ride(f, leg_speed=4)`。

### 改接口

接口地址在 `src/main.cpp` 的 `API_URL`，刷新间隔是 `POLL_MS`。设备只读返回 JSON 里的两个字段：

| `upcoming_within_36h` | `public_reset_completed_within_72h` | 画面 |
|---|---|---|
| true | 任意 | 使劲蹬 |
| false | true | 歇一歇 |
| false | false | 日常划水中 |

### 换硬件

引脚在 `src/main.cpp` 顶部：

| 功能 | pyClock 引脚 |
|---|---|
| 屏幕 DC / CS / SCK / MOSI / RST | 4 / 5 / 6 / 7 / 8 |
| 按键 | 9（低电平有效，也是 BOOT 键） |

颜色不对时，烧色条诊断固件：

```bash
uvx --from platformio --with esptool==4.11.0 pio run -e color_test -t upload
```

屏幕从上到下应为 1 红、2 绿、3 蓝、4 白、5 黑、6 米色。纯色正常但米色和白色分不清、中间色发灰发紫，说明电压和 gamma 参数不适合这块屏，改 `applyPanelTuning()`，换成你那块屏的厂家初始化参数。

## 工作原理

**画面预渲染。** 芯片上实时画线没有抗锯齿，形状也有限。所以所有帧都在电脑上画好，编码成 RGB565 游程数据（每段 = 长度 + 颜色，3 字节），三个场景共约 1.1 MB，编进固件。设备把一帧解压进 240×240 的内存帧缓冲（113 KB），再整帧推到屏上，所以不会出现画了一半的画面。解压加推屏约 35 ms。

**闪存分区。** 帧数据太大，默认分区放不下，改用 `huge_app.csv`：3 MB 应用分区，不支持 OTA。现在固件约占 74%。

**屏幕方向。** 驱动用 `setRotation(0)`，这个方向的面板行偏移是对的；但 pyClock 上的屏是倒装的，所以解压时从缓冲区末尾倒着写，把画面转 180°。GFX 绘图（热点名、红点）用 `canvas.setRotation(2)` 对齐。

**屏幕调校。** Adafruit 的 ST7789 初始化不设电压和 gamma。这块屏在默认值下中间色调发灰偏紫，米色和白色分不清。`applyPanelTuning()` 在初始化后补发 pyClock 原厂固件里的 B2 / B7 / BB / C2 / C3 / C4 / C6 / D0 / E0 / E1 参数。

**联网。** 网络在单独的 FreeRTOS 任务里跑：演示模式关掉 Wi-Fi；正式模式每 5 分钟请求一次接口，按键可以提前唤醒；配网模式开热点、DNS 劫持（让手机自动弹出页面）和网页服务器。模式和 Wi-Fi 存在 NVS 里，断电保留。

**按键。** 所有动作在松手时执行：不到 3 秒是短按，3–10 秒切换模式，10 秒以上进入配网。按住期间屏幕中央显示松手后的动作。

## 项目结构

```
├── src/main.cpp              固件：解压播放、按键、三种模式、联网、配网
├── include/pelican_frames.h  生成的帧数据和提示图（render_pelican.py 输出，勿手改）
├── render_pelican.py         所有画面的绘制脚本
├── platformio.ini            编译配置（pyclock_c3 与 color_test 两个环境）
└── docs/                     使用说明书（index.html + img/）
```
