"""Paste a local reply image only into the revalidated current WeChat editor."""
import time
from pathlib import Path
import AppKit as K
import ApplicationServices as AX
import Quartz as Q
import fill
from wechat import identity, signature, new_tail


def image_in_editor(box):
    # WeChat exposes embedded attachment placeholders in its rich-text editor.
    value=fill._ax_value(box)
    if value and '\ufffc' in value:
        return True
    queue=list(fill._ax_attr(box,AX.kAXChildrenAttribute) or [])
    for _ in range(100):
        if not queue:break
        node=queue.pop(0)
        if fill._ax_attr(node,AX.kAXRoleAttribute)=='AXImage':return True
        queue.extend(fill._ax_attr(node,AX.kAXChildrenAttribute) or [])
    return False


def key(pid, code, flags=0):
    for down in (True,False):
        event=Q.CGEventCreateKeyboardEvent(None,code,down)
        Q.CGEventSetFlags(event,flags);Q.CGEventPostToPid(pid,event)


def send_image(adapter, path, expected, allowed):
    from config import DATA_DIR
    path=Path(path).resolve()
    if path.parent != (DATA_DIR/'stickers').resolve() or path.suffix != '.png' or path.stat().st_size>2000000:
        raise ValueError('表情图片不在本地表情库中，未发送')
    app=adapter.focus_for_send(expected, allowed)
    fresh=adapter.read()
    if identity(fresh)!=identity(expected) or signature(fresh)!=signature(expected):
        raise ValueError('会话或消息已变化，图片未发送')
    box=fill._find_input_box(app.processIdentifier(), expected.get('window'))
    if box is None or fill._ax_value(box)!='' or image_in_editor(box):
        raise ValueError('输入框已有草稿或无法确认，图片未发送')
    board=K.NSPasteboard.generalPasteboard()
    saved=[]
    for item in board.pasteboardItems() or []:
        copy=K.NSPasteboardItem.alloc().init()
        for kind in item.types():
            data=item.dataForType_(kind)
            if data is not None:copy.setData_forType_(data,kind)
        saved.append(copy)
    image=K.NSImage.alloc().initWithContentsOfFile_(str(path))
    if image is None:raise ValueError('本地表情图片无法打开')
    version=None
    try:
        AX.AXUIElementSetAttributeValue(box,AX.kAXFocusedAttribute,True)
        if not allowed() or not app.isActive() or not fill._ax_attr(box,AX.kAXFocusedAttribute):
            raise ValueError('输入框焦点已变化，图片未发送')
        board.clearContents();board.writeObjects_([image]);version=board.changeCount()
        key(app.processIdentifier(),9,Q.kCGEventFlagMaskCommand)
        for _ in range(8):
            time.sleep(.15)
            if image_in_editor(box):break
        fresh=adapter.read()
        if (not allowed() or not app.isActive() or not image_in_editor(box)
                or not fill._ax_attr(box,AX.kAXFocusedAttribute)
                or identity(fresh)!=identity(expected) or signature(fresh)!=signature(expected)
                or board.changeCount()!=version):
            raise ValueError('图片已准备，但无法确认输入框和会话，未执行发送；请检查微信')
        key(app.processIdentifier(),36)
        for _ in range(8):
            time.sleep(.3);fresh=adapter.read()
            if identity(fresh)!=identity(expected):break
            messages=fresh.get('messages',[])
            if (messages and messages[-1].text=='图片' and new_tail(expected,fresh)
                    and fill._ax_value(box)=='' and not image_in_editor(box)):
                return fresh
        raise ValueError('已执行一次图片发送，但无法确认结果；已暂停，不会重发')
    finally:
        # Never overwrite something the user copied while we were working.
        if version is not None and board.changeCount()==version:
            board.clearContents()
            if saved:board.writeObjects_(saved)
