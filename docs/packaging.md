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
| 启动 | 每次解压到临时目录，**1–3 秒** | 直接加载，**0.3–0.8 秒** |
| 体积 | 压缩包约 70–90 MB | 解压约 155–190 MB |
| 杀软误报 | 更容易 | 较少 |
| 排查 | 难（临时目录） | 容易 |

**本项目推荐 onedir + zip**：启动快、好排查、误报少。

---

## 3. 打包命令（onedir）

```bash
pyinstaller ^
  --noconfirm --clean --windowed --onedir ^
  --name cw-decoder ^
  --add-data "models;models" ^
  --add-data "assets;assets" ^
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
1. 打 tag：git tag v0.1.0 && git push origin v0.1.0
2. GitHub → Releases → Draft a new release
3. 选择 tag，标题 v0.1.0，粘贴 CHANGELOG 对应段落
4. 上传 zip（cw-decoder-v0.1.0-win64.zip）
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
