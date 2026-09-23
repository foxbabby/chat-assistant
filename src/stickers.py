"""Local, original reply cards; no downloads or cloud image recognition."""
import random
from pathlib import Path
import AppKit as K
from config import DATA_DIR

PALETTES = {'自然友好': ('#E4F3E8','#237459'), '高情商': ('#F8ECE6','#A05C49'),
            '简洁专业': ('#E8EFF9','#355C8A'), '轻松幽默': ('#FFF2D1','#8C651F'),
            '温柔耐心': ('#F5EAF5','#895B89'), '礼貌婉拒': ('#EBEEF0','#536570')}
PHRASES = {'自然友好': ['收到','好呀'], '高情商': ['收到','明白'], '简洁专业': ['收到','了解'],
           '轻松幽默': ['好嘞','收到'], '温柔耐心': ['在呢','收到'], '礼貌婉拒': ['谢谢理解']}

def choose(style, description):
    if any(word in description for word in ('哭','累','难过','委屈')):
        choices=['辛苦了']
    elif any(word in description for word in ('笑','开心','哈哈')):
        choices=['哈哈','好呀']
    elif any(word in description for word in ('谢谢','感谢')):
        choices=['不客气']
    else:
        choices=PHRASES[style]
    return random.choice(choices)


def color(hex):
    return K.NSColor.colorWithCalibratedRed_green_blue_alpha_(*(int(hex[i:i+2],16)/255 for i in (1,3,5)),1)


def render(style, phrase):
    directory=DATA_DIR/'stickers';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/(style+'-'+phrase+'.png')
    if path.exists(): return path
    image=K.NSImage.alloc().initWithSize_((320,320));image.lockFocus()
    background,foreground=PALETTES[style]
    color(background).setFill()
    K.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(K.NSMakeRect(0,0,320,320),48,48).fill()
    color(foreground).setFill()
    for x in (122,184): K.NSBezierPath.bezierPathWithOvalInRect_(K.NSMakeRect(x,190,14,20)).fill()
    smile=K.NSBezierPath.bezierPath();smile.moveToPoint_((130,162));smile.curveToPoint_controlPoint1_controlPoint2_((190,162),(145,142),(175,142));smile.setLineWidth_(6);color(foreground).setStroke();smile.stroke()
    attrs={K.NSFontAttributeName:K.NSFont.boldSystemFontOfSize_(42),K.NSForegroundColorAttributeName:color(foreground)}
    text=K.NSAttributedString.alloc().initWithString_attributes_(phrase,attrs);size=text.size();text.drawAtPoint_(((320-size.width)/2,64))
    image.unlockFocus()
    bitmap=K.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    data=bitmap.representationUsingType_properties_(K.NSBitmapImageFileTypePNG,{})
    data.writeToFile_atomically_(str(path),True);path.chmod(0o600)
    return path


def reply_card(config, description):
    from cloud import Draft
    phrase=choose(config['style'],description)
    draft=Draft('[表情图片] '+phrase)
    draft.image_path=str(render(config['style'],phrase))
    return draft
