# 打包与分发说明

面向想自己打包、或想了解 Releases 里 zip 是怎么来的读者。
（普通用户不需要看这份，直接下载 Releases 即可。）

---

## 1. 需要准备的东西

```
· 一台 Windows 10/11（打包产物是 Windows 专用：回环采集依赖 WASAPI）
· Python 3.10+（开发与测试使用 3.12）
· 依赖：pip install -r requirements.txt pyinstaller
```

---

## 2. 两种打包模式

| | onefile | onedir |
|---|---|---|
| 产物 | 单个 exe | 文件夹（exe + DLL） |
| 启动 | 每次解压到临时目录，**2–5 秒** | 直接加载，**实测 1.5 秒**（首次约 2.2 秒） |
| 体积 | 压缩包约 70–90 MB | 解压约 155–190 MB |
| 杀软误报 | 更容易 | 较少 |
| 排查 | 难（临时目录） | 容易 |

**本项目推荐 onedir + zip**：启动快、好排查、误报少。

---

## 3. 打包命令（onedir）

**推荐：直接用仓库里的 spec（已验证）**

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller tools/cw-decoder.spec --noconfirm --distpath dist --workpath build
```

**等价的手写命令**

```bash
pyinstaller ^
  --noconfirm --clean --windowed --onedir ^
  --name cw-decoder ^
  --add-data "models;models" ^
  --add-data "assets;assets" ^
  --add-data "samples;samples" ^
  --collect-submodules scipy.signal ^
  --hidden-import pyaudiowpatch ^
  --hidden-import soundcard ^
  run.py
```

说明：

```
--windowed        不弹控制台（崩溃信息写 crash.log）
--add-data        模型和插图必须按原结构带上
--collect-submodules scipy.signal   scipy 的延迟导入常被漏掉
--hidden-import   PyAudioWPatch / soundcard 是运行时按需导入
```

**产物**：`dist/cw-decoder/`（整个文件夹压成 zip 即为 Release 附件）

**实测体积**（v0.1.3，Python 3.12 / PyInstaller 6.22.3）

```
解压后：1160 个文件，191 MB（v0.1.3 实测）
zip：   78.2 MB（SHA256 见 Release 说明）
启动：  1.5 秒（首次 2.2 秒）
内存：  约 131 MB
```

**布局自检（v0.1.3 新增）**

```bash
cw-decoder.exe --uidump dump.json
```

写出窗口/画布/文本框的实测尺寸，用于确认"频谱与文本框等高"在打包后仍然成立：

**时间轴缩放自检（v0.1.3 新增）**

```bash
cw-decoder.exe --zoomcheck sample.wav dump.json
```

确认"Shift+滚轮缩放时间轴"在打包后仍然生效（会分别在文本框与画布上注入事件）：

```json
{"before": [0.0, 380.16], "after_label_wheel": [17.84, 321.97],
 "after_canvas_wheel": [24.13, 267.43], "label_ok": true, "canvas_ok": true}
```

```json
{"version": "0.1.3", "window": [1536, 773],
 "canvas": [1494, 251], "text": [1512, 251],
 "slider": [18, 215], "slider_enabled": false,
 "view": [200.0, 1600.0], "wf_mode": false}
```

---

## 3.5 打包后自检（不用手点界面）

```bash
dist\cw-decoder\cw-decoder.exe --selftest
```

会在 exe 同目录写出 `self-test.txt`，内容形如：

```
sample_cq.wav / dsp: 'CQ CQ DE BD6LBA K' [0.10s]
sample_cq.wav / ctc: 'CQ CQ DE BD6LBA K' [0.21s]
sample_noisy.wav / dsp: 'CQ CQ DE BD6LBA K' [0.07s]
sample_noisy.wav / ctc: 'CQ CQ DE BD6LBA K' [0.10s]
python=3.12.15 frozen=True
```

`frozen=True` 说明跑的是打包产物；文本正确说明模型路径、依赖、音频解码都正常。

---

## 4. 打包后必须验证的清单

```
□ 在没装 Python 的机器/虚拟机里能启动
□ 文件解码正常（用 samples/sample_cq.wav）
□ CTC 能加载模型（最容易出问题：模型路径）
□ 实时监听能列出设备、能出字
□ 另存文本 / 保存录音 / 保存 WAV 路径正确
□ 装在 Program Files（只读目录）时，结果会回退到 %LOCALAPPDATA%\CWDecoder\out
□ 双击两次只开一个窗口（单实例）
□ 故意删掉模型 → 程序不崩，CTC 自动退回 DSP，crash.log 有记录
□ 中文路径下运行正常
□ 首次启动耗时、内存占用可接受
```

---

## 5. 发布（GitHub Releases）

```
1. 打 tag：git tag v0.1.3 && git push origin v0.1.3
2. GitHub → Releases → Draft a new release
3. 选择 tag，标题 v0.1.3，粘贴 CHANGELOG 对应段落
4. 上传 zip（cw-decoder-v0.1.3-win64.zip）
5. 发布
```

**注意**

```
· 不要把 exe/zip 提交进仓库（git 单文件上限 100 MB，且历史会膨胀）
· 二进制只放 Releases
```

---

## 6. 国内分发建议

```
GitHub Release 在国内下载可能只有几十 KB/s
建议同时上传一份到国内网盘（蓝奏云 / 123 云盘），在 README 里给出两个入口
```

---

## 7. 已知的打包坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报 `No module named scipy.signal.something` | 延迟导入未被收集 | `--collect-submodules scipy.signal` |
| 实时没有设备 | PyAudioWPatch 未打包 | `--hidden-import pyaudiowpatch` |
| CTC 报模型找不到 | 模型没按结构带上 | `--add-data "models;models"` |
| 保存结果失败 | 装在只读目录 | 程序会自动回退到用户目录；仍建议装到可写路径 |
| 杀软报毒 | PyInstaller bootloader 行为 | 提交白名单 / 代码签名 |
| 启动慢 2–3 秒 | onefile 解压 | 改用 onedir |
