"""WeChat adapter. No injection, no database access, no clipboard replacement."""
import time
import subprocess
import AppKit
import ApplicationServices as AX
import Quartz
import fill
import perception


def signature(snapshot):
    return tuple((m.side, m.sender or '', m.text) + ((m.message_id,) if getattr(m, 'message_id', None) else ()) for m in snapshot.get('messages', []))


def identity(snapshot):
    return (snapshot.get('window', {}).get('wid'), snapshot.get('chat_title', '').strip())


def new_tail(before, after):
    old = [m.text for m in before.get('messages', [])]
    new = [m.text for m in after.get('messages', [])]
    if old == new or not new:
        return False
    return not old or any(old[-n:] == new[:-1][-n:] for n in range(1, min(len(old), len(new)-1)+1))


class ReadUnavailable(ValueError):
    """Temporary perception failure; keep listening without sending."""


class WeChat:
    def __init__(self):
        self.previous_wid = None

    def permissions(self):
        return {'screen': None, 'accessibility': fill.has_accessibility()}

    def read(self):
        if not fill.has_accessibility():
            raise ValueError('请在系统设置中允许辅助功能访问')
        from ax_reader import read_accessibility
        accessible = read_accessibility()
        if accessible is not None:
            return accessible
        raise ReadUnavailable('微信会话暂时无法读取，请打开需要回复的聊天')

    def focus_for_send(self, expected, allowed):
        """Activate only when a prepared reply is ready, never during idle polling."""
        if not allowed():
            raise ValueError('已暂停，取消发送')
        app = fill._wechat_app()
        if app is None:
            raise ValueError('微信未运行，已取消发送')
        if app.isActive():
            return app
        fresh = self.read()
        if identity(fresh) != identity(expected) or signature(fresh) != signature(expected):
            raise ValueError('会话或消息已变化，已取消发送')
        if not allowed():
            raise ValueError('已暂停，取消发送')
        try:
            subprocess.run(['/usr/bin/open', '-b', fill.WECHAT_BUNDLE_ID],
                           check=True, timeout=4, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('无法将微信置前，请打开微信后重新开启') from None
        for _ in range(15):
            if not allowed():
                raise ValueError('已暂停，取消发送')
            app = fill._wechat_app()
            if app is not None and app.isActive():
                return app
            time.sleep(.1)
        raise ValueError('微信未能切到前台，未发送；请检查是否有系统弹窗')

    def send(self, text, expected, allowed):
        """Fill, targeted keypress, and confirm the outgoing bubble. Never retry a keypress."""
        if not allowed():
            raise ValueError('已暂停，取消发送')
        app = self.focus_for_send(expected, allowed)
        if not fill.has_accessibility():
            raise ValueError('请先授予辅助功能权限')
        fresh = self.read()
        if identity(fresh) != identity(expected) or signature(fresh) != signature(expected):
            raise ValueError('会话或消息已变化，已取消发送')
        box = fill._find_input_box(app.processIdentifier(), expected.get('window'))
        if box is None:
            raise ValueError('未找到微信输入框')
        if fill._ax_value(box) != '':
            raise ValueError('微信输入框已有内容，已暂停自动回复')
        if not allowed():
            raise ValueError('已暂停，取消发送')
        if not fill._ax_set_value(box, text) or fill._ax_value(box) != text:
            raise ValueError('回复填入后校验失败，已暂停')
        AX.AXUIElementSetAttributeValue(box, AX.kAXFocusedAttribute, True)
        # Re-read after fill; OCR excludes the editor, so the original context must match.
        fresh = self.read()
        focused = fill._ax_attr(box, AX.kAXFocusedAttribute)
        if (not allowed() or not app.isActive() or not focused or
                identity(fresh) != identity(expected) or signature(fresh) != signature(expected) or
                fill._ax_value(box) != text):
            raise ValueError('发送前状态发生变化，回复已填入但未发送，请检查微信')
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 36, down)
            Quartz.CGEventPostToPid(app.processIdentifier(), event)
        # A cleared input is insufficient: require a new outgoing message bubble too.
        for _ in range(5):
            time.sleep(0.45)
            result = self.read()
            if identity(result) != identity(expected):
                break
            messages = result.get('messages', [])
            compact = lambda s: ''.join(s.split())
            if (messages and
                    compact(messages[-1].text) == compact(text) and
                    new_tail(expected, result) and
                    signature(result) != signature(expected) and fill._ax_value(box) == ''):
                return result
        raise ValueError('已执行一次发送，但未确认消息气泡；已暂停，请在微信检查，系统不会重发')
