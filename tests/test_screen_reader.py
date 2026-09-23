import sys
from pathlib import Path
from types import SimpleNamespace as N
from unittest.mock import patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
from screen_reader import assign_sides,position_side,read_positions
from perception import TextBlock

class ScreenPositionTests(unittest.TestCase):
    def snapshot(self,text='测试消息'):
        message=N(text=text,side='unknown',conf=0,row_rect=(300,200,600,80))
        return {'window':{'wid':1,'x':0,'y':0,'w':1000,'h':800},'chat_title':'测试联系人',
                'messages':[message],'alignment_diagnostics':{'pane':(300,100,600,600)}}
    def block(self,text='测试消息',x=.36,w=.1,y=.68):
        return TextBlock(text,1,x,y,w,.02)
    def test_left_and_right_positions(self):
        self.assertEqual(assign_sides(self.snapshot(),[self.block()])['messages'][0].side,'them')
        self.assertEqual(assign_sides(self.snapshot(),[self.block(x=.74)])['messages'][0].side,'me')
        self.assertEqual(self.snapshot()['messages'][0].side,'unknown')
    def test_wrong_text_row_and_low_confidence_remain_unknown(self):
        for block in [self.block('其他消息'),self.block(y=.1),TextBlock('测试消息',.5,.36,.68,.1,.02)]:
            self.assertEqual(assign_sides(self.snapshot(),[block])['messages'][0].side,'unknown')
    def test_centered_or_duplicate_match_remain_unknown(self):
        self.assertEqual(position_side((550,200,100,20),(300,100,600,600)),'unknown')
        self.assertEqual(assign_sides(self.snapshot(),[self.block(),self.block(x=.74)])['messages'][0].side,'unknown')
    def test_background_window_never_captured(self):
        snapshot=self.snapshot()
        with patch('screen_reader.visible',return_value=False),patch('screen_reader.perception.capture_image',return_value=None),patch('screen_reader.perception.capture_window',return_value=False),patch('screen_reader.subprocess.run') as run:
            self.assertIs(read_positions(snapshot),snapshot);run.assert_not_called()
    def test_wrapped_text_same_row(self):
        blocks=[self.block('第一行',x=.7,w=.14,y=.7),self.block('第二行',x=.7,w=.09,y=.67)]
        self.assertEqual(assign_sides(self.snapshot('第一行\n第二行'),blocks)['messages'][0].side,'me')
    def test_title_mismatch_does_not_assign_sides(self):
        snapshot=self.snapshot()
        with patch('screen_reader.perception.capture_image',return_value=object()),patch('screen_reader.perception.ocr_image',return_value=[self.block()]):
            self.assertIs(read_positions(snapshot),snapshot)
    def test_changed_chat_during_capture_does_not_assign_sides(self):
        snapshot=self.snapshot();blocks=[self.block(),self.block('测试联系人',y=.94)]
        other=self.snapshot('另一条消息')
        with patch('screen_reader.perception.capture_image',return_value=object()),patch('screen_reader.perception.ocr_image',return_value=blocks),patch('ax_reader.read_accessibility',return_value=other):
            self.assertIs(read_positions(snapshot),snapshot)
