"""Read visible message positions when per-window capture is unavailable."""
import copy
import subprocess
import tempfile
from pathlib import Path
import Quartz
import fill
import perception


def normalized(text):
    return ''.join(text.split())


def position_side(box, pane):
    x, y, w, h = box
    px, py, pw, ph = pane
    if pw <= 0 or w <= 0 or x < px or x+w > px+pw+3:
        return 'unknown'
    left, right = x-px, px+pw-x-w
    # Require a clear asymmetric margin; wide/centered labels are not senders.
    if abs(left-right) < max(16, pw*.045):
        return 'unknown'
    return 'them' if left < right else 'me'


def assign_sides(snapshot, blocks):
    result = copy.deepcopy(snapshot)
    window = snapshot['window']
    pane = snapshot.get('alignment_diagnostics', {}).get('pane')
    if not pane:
        return result
    for message in result['messages']:
        if message.side != 'unknown':
            continue
        row = getattr(message, 'row_rect', None)
        if not row:
            continue
        candidates=[]
        for block in blocks:
            box=(window['x']+block.x*window['w'], window['y']+(1-block.y-block.h)*window['h'],
                 block.w*window['w'], block.h*window['h'])
            if (box[0] >= pane[0] and box[0]+box[2] <= pane[0]+pane[2]+3 and
                    box[1] >= max(row[1],pane[1])-3 and box[1]+box[3] <= min(row[1]+row[3],pane[1]+pane[3])+3):
                candidates.append((block,box))
        candidates.sort(key=lambda item:(round(item[1][1]/6),item[1][0]))
        matches=[]
        for start in range(len(candidates)):
            text=''
            for end in range(start,min(start+12,len(candidates))):
                text+=normalized(candidates[end][0].text)
                if text==normalized(message.text):
                    selected=candidates[start:end+1]
                    if min(b.conf for b,_ in selected)<.85:
                        continue
                    left=min(r[0] for _,r in selected);top=min(r[1] for _,r in selected)
                    right=max(r[0]+r[2] for _,r in selected);bottom=max(r[1]+r[3] for _,r in selected)
                    matches.append(position_side((left,top,right-left,bottom-top),pane))
        if len(matches)==1 and matches[0]!='unknown':
            message.side=matches[0];message.conf=1.0
    result['read_method']='accessibility+screen-position'
    return result


def visible(window):
    app=fill._wechat_app()
    if app is None or not app.isActive():
        return False
    wins=Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,0) or []
    for item in wins:
        if int(item.get('kCGWindowNumber',0))==window['wid']:
            return True
        if float(item.get('kCGWindowAlpha',1))<=0 or int(item.get('kCGWindowOwnerPID',0))==app.processIdentifier():
            continue
        b=item.get('kCGWindowBounds',{})
        if (float(b.get('X',0)) < window['x']+window['w'] and float(b.get('X',0))+float(b.get('Width',0)) > window['x'] and
            float(b.get('Y',0)) < window['y']+window['h'] and float(b.get('Y',0))+float(b.get('Height',0)) > window['y']):
            return False
    return False


def read_positions(snapshot):
    window=snapshot['window']
    blocks=[]
    # Window capture works in the background and cannot include unrelated windows.
    image=perception.capture_image(window['wid'])
    if image is not None:
        blocks=perception.ocr_image(image,chat_only=False)
    if not blocks:
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'chat-window.png'
            try:
                if perception.capture_window(window['wid'],path):
                    blocks=perception.ocr(path,chat_only=False)
            except (OSError,subprocess.TimeoutExpired,ValueError):
                pass
    # Only attempt display capture when the exact window is visible and unobscured.
    if not blocks and visible(window):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'chat-region.png'
            region=','.join(str(round(window[k])) for k in ('x','y','w','h'))
            try:
                capture=subprocess.run(['screencapture','-x','-R',region,str(path)],capture_output=True,timeout=4)
                if not capture.returncode and path.exists() and visible(window):
                    blocks=perception.ocr(path,chat_only=False)
            except (OSError,subprocess.TimeoutExpired,ValueError):
                pass
    title=normalized(snapshot['chat_title'])
    if not any(title==normalized(b.text) and b.y>.88 for b in blocks):
        return snapshot
    from ax_reader import read_accessibility
    fresh=read_accessibility(window)
    if (not fresh or fresh['chat_title']!=snapshot['chat_title'] or
        [m.text for m in fresh['messages']] != [m.text for m in snapshot['messages']]):
        return snapshot
    return assign_sides(snapshot,blocks)
