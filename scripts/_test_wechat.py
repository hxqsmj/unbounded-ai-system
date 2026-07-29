"""快速测试: 查找并聚焦微信窗口"""
import ctypes
import time

user32 = ctypes.windll.user32

hwnd = user32.FindWindowW('Qt51514QWindowIcon', None)
if hwnd:
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    print(f'微信窗口: hwnd={hwnd:#x} title="{title.value}"')

    # 恢复并聚焦
    user32.ShowWindow(hwnd, 9)
    time.sleep(0.3)
    user32.SetForegroundWindow(hwnd)
    print('已聚焦微信窗口')

    # 检查微信窗口标题是否有未读
    if '(' in title.value and ')' in title.value:
        print(f'检测到未读消息')
    else:
        print('当前无未读消息标记')
else:
    # 尝试其他类名
    for cls in ['WeChatMainWnd', 'WeixinMainWnd', 'Qt51514QWindowIcon']:
        h = user32.FindWindowW(cls, None)
        if h:
            t = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, t, 256)
            print(f'找到: class={cls} title="{t.value}"')
            break
    else:
        print('未找到微信窗口! 请确保微信已打开且主窗口可见')
