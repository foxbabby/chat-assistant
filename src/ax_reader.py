"""Read the visible WeChat conversation through its published accessibility tree.

Used when macOS window capture returns a blank image. Unknown message alignment
is retained as unknown, never guessed as a message from the other person.
"""
import time
import ApplicationServices as AX
import fill
from perception import Message


def rect(element):
    try:
        pos=fill._ax_attr(element, AX.kAXPositionAttribute)
        size=fill._ax_attr(element, AX.kAXSizeAttribute)
        ok1,p=AX.AXValueGetValue(pos,AX.kAXValueCGPointType,None)
        ok2,s=AX.AXValueGetValue(size,AX.kAXValueCGSizeType,None)
        return (float(p.x),float(p.y),float(s.width),float(s.height)) if ok1 and ok2 else None
    except Exception:
        return None


def alignment(box, pane):
    if not box or not pane or pane[2] <= 0 or box[2] >= pane[2]*.8:
        return 'unknown'
    center=(box[0]+box[2]/2-pane[0])/pane[2]
    return 'me' if center>.56 else 'them' if center<.44 else 'unknown'


def read_accessibility(window=None):
    app=fill._wechat_app()
    if app is None or not fill.has_accessibility():
        return None
    root=AX.AXUIElementCreateApplication(app.processIdentifier())
    windows=fill._ax_attr(root,AX.kAXWindowsAttribute) or []
    for w in windows:
        frame=rect(w)
        if not frame or (window and (abs(frame[0]-window['x'])>4 or abs(frame[1]-window['y'])>30)):
            continue
        queue=[w]; title=''; pane=None; nodes=[]; seen=0
        while queue and seen<3000:
            node=queue.pop(0);seen+=1
            identifier=fill._ax_attr(node,'AXIdentifier')
            if identifier=='current_chat_name_label':
                title=fill._ax_attr(node,AX.kAXValueAttribute) or ''
            if identifier=='chat_message_list':
                pane=node
                if title:
                    break
                continue  # Read message rows once below, not again during discovery.
            if title and pane is not None:
                break
            queue.extend(fill._ax_attr(node,AX.kAXChildrenAttribute) or [])
        if not title or pane is None:
            continue
        area=rect(pane)
        # Only documented message bubble nodes, never sidebar snippets or the editor.
        queue=list(fill._ax_attr(pane,AX.kAXChildrenAttribute) or [])
        seen=0
        while queue and seen<2000:
            node=queue.pop(0);seen+=1
            if fill._ax_attr(node,'AXIdentifier')=='chat_bubble_item_view':
                text=fill._ax_attr(node,AX.kAXValueAttribute)
                if not isinstance(text,str) or not text.strip():
                    text=fill._ax_attr(node,AX.kAXTitleAttribute) or fill._ax_attr(node,AX.kAXDescriptionAttribute)
                if isinstance(text,str) and text.strip():
                    box=rect(node);side=alignment(box,area)
                    message=Message(text=text.strip(),side=side,y=box[1] if box else seen,
                                    conf=1.0)
                    message.kind = ('sticker' if text.strip().startswith('动画表情') else 'image' if text.strip() == '图片' else 'media' if text.strip().startswith(('文件\n','语音通话','视频通话')) else 'text')
                    message.row_rect=box
                    nodes.append(message)
            else:
                queue.extend(fill._ax_attr(node,AX.kAXChildrenAttribute) or [])
        nodes.sort(key=lambda m:m.y)
        return {'ok':True,'chat_title':title,'window':window or {'wid':'ax:'+title,'x':frame[0],'y':frame[1],'w':frame[2],'h':frame[3]},'messages':nodes[-12:],
                'read_method':'accessibility', 'alignment_diagnostics': {'pane':area, 'sides':[m.side for m in nodes[-3:]]}}
    return None
