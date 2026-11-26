
import ctypes, subprocess, time, os
from ctypes import wintypes
from PIL import Image

EXE = r"F:\QQBot\cwdecoder\dist\cw-decoder\cw-decoder.exe"
WD  = r"F:\QQBot\cwdecoder\dist\cw-decoder"
u = ctypes.windll.user32; g = ctypes.windll.gdi32

def find_windows(title):
    res=[]
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        b=ctypes.create_unicode_buffer(256); u.GetWindowTextW(h,b,256)
        if b.value.strip() in title: res.append(h)
        return True
    u.EnumWindows(cb,0); return res

t0=time.time()
p1=subprocess.Popen([EXE], cwd=WD)
hw=None
while time.time()-t0 < 40:
    ws=find_windows(("CW 解码器","CW Decoder"))
    if ws: hw=ws[0]; break
    time.sleep(0.1)
startup=time.time()-t0
print(f"启动到出窗口: {startup:.2f} 秒")

time.sleep(2.0)
r=wintypes.RECT(); u.GetWindowRect(hw, ctypes.byref(r))
w,h=r.right-r.left, r.bottom-r.top
hdc=u.GetWindowDC(hw); mdc=g.CreateCompatibleDC(hdc)
bmp=g.CreateCompatibleBitmap(hdc,w,h); g.SelectObject(mdc,bmp)
u.PrintWindow(hw, mdc, 2)
class BIH(ctypes.Structure):
    _fields_=[("biSize",wintypes.DWORD),("biWidth",wintypes.LONG),("biHeight",wintypes.LONG),
              ("biPlanes",wintypes.WORD),("biBitCount",wintypes.WORD),("biCompression",wintypes.DWORD),
              ("biSizeImage",wintypes.DWORD),("biXPelsPerMeter",wintypes.LONG),("biYPelsPerMeter",wintypes.LONG),
              ("biClrUsed",wintypes.DWORD),("biClrImportant",wintypes.DWORD)]
bi=BIH(ctypes.sizeof(BIH), w,-h,1,32,0,0,0,0,0,0)
buf=ctypes.create_string_buffer(w*h*4); g.GetDIBits(mdc,bmp,0,h,buf,ctypes.byref(bi),0)
img=Image.frombuffer("RGBA",(w,h),buf,"raw","BGRA",0,1).convert("RGB")
img.save(r"F:\QQBot\cwdecoder\dist\exe-ui.png")
print("窗口尺寸:", img.size)

# 第二实例：应当立即退出
t1=time.time()
p2=subprocess.Popen([EXE], cwd=WD)
try:
    code2=p2.wait(timeout=15)
    print(f"第二实例：退出码 {code2}，用时 {time.time()-t1:.1f} 秒（单实例保护生效）")
except subprocess.TimeoutExpired:
    print("第二实例：15 秒内没有退出（可能开了新窗口）"); p2.kill()

ws=find_windows(("CW 解码器","CW Decoder"))
print("当前窗口数:", len(ws))

p1.terminate(); time.sleep(1)
print("资源占用:", round(img.size[0]), "x", img.size[1])
