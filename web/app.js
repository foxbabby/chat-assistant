const $ = id => document.getElementById(id);
let nativeAccessGranted = false;
const autoChoice = {wechat:true};
const batchOptions={auto_reply:false,reply_latest:false};
let batchBusy = false, checkedRooms = new Set(), checkedProfile = null;
const batchSelection=()=>platform==='dingtalk'?(state?.rooms||[]).filter(r=>checkedRooms.has(r.id)):[];
const batchAction=()=>batchSelection().every(r=>r.enabled||r.starting)?'stop':'start';
let selectedRoom = '', roomsDraft = new Map(), roomListKey = '';
const choiceKey=()=>platform==='dingtalk'?'ding:'+selectedRoom:'wechat';
let replyInfoKey = '';
let state = null, busy = false, toastTimer, platform = 'wechat', dingListProfile = null, historyChoice = {wechat:false,dingtalk:false}, historyLoaded = false;
let dingSettingsConversations = [], dingMainConversations = [];
async function api(path, data) {
  const response = await fetch('/api/' + path + (data === undefined ? '?platform='+platform+'&conversation_id='+encodeURIComponent(platform==='dingtalk'?selectedRoom:'') : ''), data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...data,platform,conversation_id:platform==='dingtalk'?selectedRoom:''})});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '操作失败');
  return body;
}
function toast(text) { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,5500); }
function render(next) {
  if(next.platform && next.platform!==platform)return;
  if(platform==='dingtalk'&&selectedRoom&&next.conversation_id!==selectedRoom)return;
  state=next;
  if(platform==='dingtalk')selectedRoom=next.conversation_id||'';
  renderRooms();
  renderActivity();
  for(const name of ['wechat','dingtalk']){const p=state.platforms[name];$(name+'-status').textContent=p.enabled?'正在监听':p.starting?'正在开启':'已暂停';$(name+'-target').textContent=p.target||(name==='wechat'?'当前打开的会话':state.settings.dingtalk_name||'请在设置中选择会话');document.querySelector('[data-platform='+name+']').classList.toggle('selected',name===platform);}
  $('style-summary').textContent='回复风格 · '+state.settings.style;
  $('scope-title').textContent=platform==='wechat'?'微信 · 当前会话':'钉钉 · 多会话监听';
  $('scope-description').textContent=platform==='wechat'?'发送时自动将微信置前；切换联系人后暂停微信监听。':'各会话独立运行，切换查看不中断监听。';
  if(!historyLoaded){for(const p of ['wechat','dingtalk'])historyChoice[p]=state.settings[p+'_reply_latest'];historyLoaded=true;}
  const room=(state.settings.dingtalk_rooms||[]).find(r=>r.id===selectedRoom);
  if(autoChoice[choiceKey()]===undefined)autoChoice[choiceKey()]=room?.auto_reply??false;
  if(historyChoice[choiceKey()]===undefined)historyChoice[choiceKey()]=room?.reply_latest??false;
  if(state.enabled||state.starting)autoChoice[choiceKey()]=state.auto_reply;
  $('auto-reply').checked=state.enabled||state.starting?state.auto_reply:autoChoice[choiceKey()];$('auto-reply').disabled=busy||state.enabled||state.starting;
  if(!$('auto-reply').checked)historyChoice[choiceKey()]=false;
  $('reply-latest').checked=historyChoice[choiceKey()];$('reply-latest').disabled=busy||state.enabled||state.starting||!$('auto-reply').checked;
  $('main-ding-picker').hidden=platform!=='dingtalk';
  if(dingListProfile!==state.settings.dingtalk_profile){dingListProfile=null;dingMainConversations=[];setConversation('main-ding',state.settings.dingtalk_conversation,state.settings.dingtalk_name);}
  if(document.activeElement!==$('main-ding-input')&&$('main-ding-input').dataset.selectedId!==state.settings.dingtalk_conversation)setConversation('main-ding',state.settings.dingtalk_conversation,state.settings.dingtalk_name);
  $('main-ding-input').disabled=busy;
  $('selected-room-title').textContent=platform==='dingtalk'?(room?.name||'钉钉回复工作台'):'回复工作台';
  $('remove-room').hidden=platform!=='dingtalk'||!room;
  {const rooms=state.rooms||[],active=rooms.filter(r=>r.enabled).length,connecting=rooms.filter(r=>r.starting).length;$('dingtalk-status').textContent=[active?active+' 个监听中':'',connecting?connecting+' 个连接中':''].filter(Boolean).join(' · ')||'已暂停';$('dingtalk-target').textContent=rooms.length+' 个会话';}
  $('dot').classList.toggle('on',state.enabled);
  $('dot').classList.toggle('connecting',state.starting);
  $('run-label').textContent=state.enabled?'正在监听':state.starting?'正在开启':'已暂停';
  $('headline').textContent=state.enabled?(state.target?`正在照看「${state.target}」`:'监听已开启，等待会话'):(platform==='dingtalk'?(room?'准备照看「'+room.name+'」':'添加你想照看的会话'):'让每一次回应，恰到好处。');
  $('status').textContent=state.status;
  $('toggle').textContent=state.enabled||state.starting?'暂停监听':'开始监听';
  $('toggle').disabled=busy||(platform==='dingtalk'&&!selectedRoom);
  const checked=batchSelection();
  if(checked.length){
    $('toggle').textContent=batchBusy?'正在批量处理…':(batchAction()==='stop'?'暂停监听':'开始监听')+' · '+checked.length+' 个会话';
    $('auto-reply').checked=batchOptions.auto_reply;$('auto-reply').disabled=busy||batchAction()==='stop';
    $('reply-latest').checked=batchOptions.reply_latest;$('reply-latest').disabled=busy||batchAction()==='stop'||!batchOptions.auto_reply;
  }
  const settings=state.settings;
  $('cloud-status').textContent=settings.has_key?`云端已配置 · ${settings.model}`:'尚未配置云端模型';
  $('model-badge').textContent=settings.base_url.includes('api.deepseek.com')?'DeepSeek · 云端':`${settings.model} · 云端`;
  $('cloud-dot').classList.toggle('on',settings.has_key);
  document.querySelectorAll('input[name=style]').forEach(input=>{input.checked=input.value===settings.style;input.parentElement.classList.toggle('selected',input.checked);input.disabled=busy;});
  const perm=state.permissions;
  $('permissions-status').textContent=platform==='dingtalk'?(perm.connected?'钉钉接口已连接':'请在设置中连接钉钉'):(perm.accessibility?'微信访问权限已就绪':'请在设置中完成微信访问授权');
  $('permission-help').textContent=state.platforms.wechat.permissions.accessibility?'授权已生效，可以使用微信监听。':nativeAccessGranted?'原生应用已获授权，后台尚未生效。请退出并重开聊天助手后再检查。':'点击申请授权，系统会收到聊天助手的访问请求。打开对应开关后，返回此页面会自动检测。';
  $('screen-state').textContent='无需使用'; $('ax-state').textContent=state.platforms.wechat.permissions.accessibility?'已授权':'未授权';
  $('count').textContent=state.count;
  $('empty').hidden=!!state.latest; $('live-content').hidden=!state.latest;
  const nextReplyInfoKey=JSON.stringify([platform,selectedRoom,state.target,state.latest?.incoming,state.latest?.reply]);
  if(nextReplyInfoKey!==replyInfoKey){$('reply-info').open=false;replyInfoKey=nextReplyInfoKey;}
  if(state.latest){$('incoming-label').textContent=state.latest.sender?'最新消息 · '+state.latest.sender:'当前最新消息';$('reply-label').hidden=!state.latest.reply&&state.latest.state!=='生成中';$('chat-target').textContent=state.target;$('incoming').textContent=state.latest.incoming;$('reply').textContent=state.latest.reply||(state.latest.state==='生成中'?'正在生成…':'');$('reply').hidden=!state.latest.reply&&state.latest.state!=='生成中';$('reply-image').hidden=!state.latest.image_path;if(state.latest.image_path){const url='/api/reply-image?platform='+platform+'&v='+encodeURIComponent(state.latest.image_path);if($('reply-image').getAttribute('src')!==url)$('reply-image').src=url;}$('reply-state').textContent=state.latest.state+(state.latest.sources?.length?' · 依据：'+state.latest.sources.join('、'):'')+(state.latest.knowledge_warnings?.length?' · '+state.latest.knowledge_warnings.join('；'):'');}
  $('manual-actions').hidden=!state.latest?.pending_id;
  $('send-reply').disabled=$('discard-reply').disabled=busy;
  $('events').replaceChildren();
  if(!state.events.length){const li=document.createElement('li');li.className='muted';li.textContent='开启后显示处理进度';$('events').append(li);}
  for(const event of state.events){const li=document.createElement('li'),time=document.createElement('time'),text=document.createElement('span');time.textContent=event.time;text.textContent=event.text;li.append(time,text);$('events').append(li);}
}
async function refresh(){try{if(!busy)render(await api('state'));}catch(error){if(platform==='dingtalk'&&selectedRoom&&error.message.includes('会话已移除')){selectedRoom='';return refresh();}$('status').textContent='助手服务未连接，请重新打开应用';$('toggle').disabled=true;}}
function openSettings(){if(!state)return;$('ding-image-mode').value=state.settings.dingtalk_image_mode||'ocr';$('ding-vision-url').value=state.settings.dingtalk_vision_url||'';$('ding-vision-model').value=state.settings.dingtalk_vision_model||'';$('ding-vision-key').value='';$('ding-vision-key').placeholder=state.settings.has_vision_key?'已保存，留空保留原 Key':'填写视觉模型 API Key';$('base-url').value=state.settings.base_url;$('model').value=state.settings.model;$('api-key').value='';$('excluded-senders').value=state.settings.excluded_senders.join('\n');$('self-names').value=(state.settings.self_names||[]).join('\n');$('api-key').placeholder=state.settings.has_key?'已保存，留空保留原 Key':'填写你的 API Key';$('voice-profile').value=state.settings.voice_profile||'';$('reply-examples').value=state.settings.reply_examples||'';$('work-knowledge').value=state.settings.work_knowledge||'';$('spd-knowledge').value=state.settings.spd_knowledge||'enabled';$('settings-feedback').textContent='';fillDingSettings();$('settings').showModal();}
$('settings-open').onclick=openSettings;
$('settings-close').onclick=()=>$('settings').close();
$('settings').addEventListener('close',()=>{$('api-key').value='';$('ding-vision-key').value='';});
function formData(){return {dingtalk_image_mode:$('ding-image-mode').value,dingtalk_vision_url:$('ding-vision-url').value.trim(),dingtalk_vision_model:$('ding-vision-model').value.trim(),dingtalk_vision_key:$('ding-vision-key').value.trim(),style:document.querySelector('input[name=style]:checked').value, dingtalk_profile:$('ding-profile').value,dingtalk_interval:Number($('ding-interval').value),voice_profile:$('voice-profile').value.trim(),reply_examples:$('reply-examples').value.trim(),work_knowledge:$('work-knowledge').value.trim(),spd_knowledge:$('spd-knowledge').value,base_url:$('base-url').value.trim(),model:$('model').value.trim(),api_key:$('api-key').value.trim(),self_names:$('self-names').value.split('\n').map(x=>x.trim()).filter(Boolean),excluded_senders:$('excluded-senders').value.split('\n').map(x=>x.trim()).filter(Boolean)};}
$('settings-form').onsubmit=async e=>{e.preventDefault();const submit=e.submitter;submit.disabled=true;try{await api('settings',formData());$('settings').close();toast('设置已保存');await refresh();}catch(error){$('settings-feedback').textContent=error.message;}finally{submit.disabled=false;}};
$('test-connection').onclick=async()=>{const button=$('test-connection');button.disabled=true;button.textContent='连接中…';try{const result=await api('test',formData());$('settings-feedback').textContent=result.message;}catch(error){$('settings-feedback').textContent=error.message;}finally{button.disabled=false;button.textContent='测试连接';}};
$('toggle').onclick=async()=>{if(!state||busy)return;if(batchSelection().length)return batchRooms(batchAction());if(!state.enabled&&!state.settings.has_key){openSettings();$('settings-feedback').textContent='先连接云端模型，再开始监听';return;}const action=state.enabled||state.starting?'stop':'start';busy=true;$('toggle').disabled=true;if(action==='start'){$('run-label').textContent='正在开启';$('status').textContent='正在连接当前会话…';$('toggle').textContent='正在开启…';}try{render(await api(action,action==='start'?{reply_latest:historyChoice[choiceKey()],auto_reply:autoChoice[choiceKey()]}:{}));}catch(error){toast(error.message);}finally{busy=false;await refresh();}};
document.querySelectorAll('input[name=style]').forEach(input=>input.onchange=async()=>{busy=true;try{await api('settings',{style:input.value});toast('风格已更新，下一条回复生效');}catch(e){toast(e.message);}finally{busy=false;await refresh();}});
$('auto-reply').onchange=()=>{const enabled=$('auto-reply').checked;if(batchSelection().length){batchOptions.auto_reply=enabled;if(!enabled)batchOptions.reply_latest=false;}else{autoChoice[choiceKey()]=enabled;if(!enabled)historyChoice[choiceKey()]=false;}render(state);};
for(const action of ['send-reply','discard-reply'])$(action).onclick=async()=>{const pending_id=state?.latest?.pending_id;if(busy||!pending_id)return;busy=true;$('send-reply').disabled=$('discard-reply').disabled=true;try{render(await api(action,{pending_id}));}catch(e){toast(e.message);}finally{busy=false;await refresh();}};
$('reply-latest').onchange=()=>{const enabled=$('auto-reply').checked&&$('reply-latest').checked;if(batchSelection().length)batchOptions.reply_latest=enabled;else historyChoice[choiceKey()]=enabled;render(state);};

window.addEventListener('native-accessibility-status',event=>{nativeAccessGranted=event.detail?.granted===true;refresh();});
document.querySelectorAll('[data-permission]').forEach(button=>button.onclick=()=>{
  const bridge=window.webkit?.messageHandlers?.accessibilityPermission;
  if(!bridge){toast('请在聊天助手 App 中点击申请授权，浏览器预览无法发起原生授权');return;}
  bridge.postMessage('request');
  $('permission-help').textContent='已由聊天助手发起申请。请在系统设置中打开“聊天助手”的开关，返回后会自动检测。';
});
setInterval(()=>{if($('settings').open)window.webkit?.messageHandlers?.accessibilityPermission?.postMessage('status');},2000);

function tab(preview){$('preview-panel').hidden=!preview;$('live-panel').hidden=preview;$('preview-tab').setAttribute('aria-selected',String(preview));$('live-tab').setAttribute('aria-selected',String(!preview));}
$('preview-tab').onclick=()=>tab(true);$('live-tab').onclick=()=>tab(false);
document.querySelector('.tabs').onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){const preview=$('preview-panel').hidden;tab(preview);$(preview?'preview-tab':'live-tab').focus();}};
$('preview-generate').onclick=async()=>{
  const button=$('preview-generate');
  button.disabled=true;button.textContent='正在检索并生成…';
  $('preview-info').open=false;$('preview-info').hidden=true;
  $('preview-result').textContent='正在处理本次测试消息…';$('preview-details').textContent='';
  try{
    const result=await api('preview',{text:$('preview-input').value,style:state.settings.style});
    $('preview-result').textContent=result.reply;
    $('preview-info').hidden=false;
    $('preview-details').textContent=['实际模型：'+result.model,...(result.sources||[]).map(s=>'依据：'+s),...(result.warnings||[]).map(w=>'提示：'+w)].join('；');
  }catch(error){$('preview-result').textContent='测试未生成有效回复：'+error.message;}
  finally{button.disabled=false;button.textContent='测试真实回复';}
};
refresh();setInterval(refresh,1200);

$('scan').onclick=async()=>{const b=$('scan');b.disabled=true;b.setAttribute('aria-busy','true');b.title='正在读取消息';try{render(await api('scan',{}));}catch(error){toast(error.message);$('status').textContent=error.message;}finally{b.disabled=false;b.removeAttribute('aria-busy');b.title='刷新当前消息';}};

function option(select,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;select.append(o);}
function conversationList(prefix){return prefix==='main-ding'?dingMainConversations:dingSettingsConversations;}
function closeConversation(prefix){const input=$(prefix+'-input');$(prefix+'-options').hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');input.dataset.searching='0';input.value=input.dataset.selectedName||'';paintConversations(prefix);}
function paintConversations(prefix){
  const input=$(prefix+'-input'),box=$(prefix+'-options'),list=conversationList(prefix);
  const query=input.dataset.searching==='1'?input.value.trim().toLocaleLowerCase():'';
  const matches=query?list.filter(c=>String(c.name).toLocaleLowerCase().includes(query)):list;
  const shown=matches.slice(0,60),selected=list.find(c=>c.id===input.dataset.selectedId);
  if(!query&&selected&&!shown.some(c=>c.id===selected.id)){shown.unshift(selected);shown.length=Math.min(shown.length,60);}
  box.replaceChildren();input.removeAttribute('aria-activedescendant');input.dataset.activeIndex='-1';
  if(!shown.length){const empty=document.createElement('div');empty.className='conversation-empty';empty.textContent=list.length?'没有匹配的会话':'请先加载会话';box.append(empty);}
  for(const [index,c] of shown.entries()){
    const item=document.createElement('button');item.type='button';item.id=prefix+'-choice-'+index;item.setAttribute('role','option');item.setAttribute('aria-selected',String(c.id===input.dataset.selectedId));item.textContent=c.name;
    item.onmousedown=e=>e.preventDefault();item.onclick=()=>chooseConversation(prefix,c);box.append(item);
  }
  $(prefix+'-search-count').textContent=list.length?(query?`找到 ${matches.length} / ${list.length} 个会话`:`已加载 ${list.length} 个会话`)+(matches.length>shown.length?' · 继续输入可缩小范围':''):'';
}
function setConversation(prefix,id,name){const input=$(prefix+'-input');input.dataset.selectedId=id||'';input.dataset.selectedName=name||'';closeConversation(prefix);}
function openConversation(prefix){const input=$(prefix+'-input');$(prefix+'-options').hidden=false;input.setAttribute('aria-expanded','true');paintConversations(prefix);}
function chooseConversation(prefix,item){const previous=$(prefix+'-input').dataset.selectedId;setConversation(prefix,item.id,item.name);if(prefix==='main-ding'&&item.id!==previous)saveMainConversation(item);}
for(const prefix of ['ding','main-ding']){
  const input=$(prefix+'-input'),root=$(prefix+'-combo');
  input.onfocus=()=>{input.dataset.searching='0';input.select();openConversation(prefix);};
  input.onclick=()=>{if($(prefix+'-options').hidden)openConversation(prefix);};
  input.oninput=()=>{input.dataset.searching='1';openConversation(prefix);};
  input.onkeydown=e=>{
    const options=Array.from($(prefix+'-options').querySelectorAll('[role=option]'));
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){
      e.preventDefault();if($(prefix+'-options').hidden){openConversation(prefix);return;}
      if(!options.length)return;
      const step=e.key==='ArrowDown'?1:-1,old=Number(input.dataset.activeIndex||'-1');
      const next=old<0?(step>0?0:options.length-1):(old+step+options.length)%options.length;
      options.forEach((option,index)=>option.classList.toggle('active',index===next));
      input.dataset.activeIndex=String(next);input.setAttribute('aria-activedescendant',options[next].id);options[next].scrollIntoView({block:'nearest'});
    }else if(e.key==='Enter'&&!$(prefix+'-options').hidden){e.preventDefault();options[Math.max(0,Number(input.dataset.activeIndex||'-1'))]?.click();}
    else if(e.key==='Escape'&&!$(prefix+'-options').hidden){e.preventDefault();closeConversation(prefix);}
  };
  input.onblur=()=>setTimeout(()=>{if(!root.contains(document.activeElement))closeConversation(prefix);},0);
}
document.addEventListener('pointerdown',e=>{for(const prefix of ['ding','main-ding'])if(!$(prefix+'-combo').contains(e.target)&&!$(prefix+'-options').hidden)closeConversation(prefix);});
function fillDingSettings(){const c=state.settings;dingSettingsConversations=[];$('ding-profile').replaceChildren();option($('ding-profile'),'','请选择账号');if(c.dingtalk_profile)option($('ding-profile'),c.dingtalk_profile,'已保存的钉钉账号');$('ding-profile').value=c.dingtalk_profile;setConversation('ding',c.dingtalk_conversation,c.dingtalk_name);$('ding-interval').value=c.dingtalk_interval;$('ding-feedback').textContent='';}
$('ding-profile').onchange=()=>{dingSettingsConversations=[];setConversation('ding','','');};
$('ding-profiles').onclick=async()=>{const b=$('ding-profiles');b.disabled=true;try{const data=await api('dingtalk-profiles',{});const saved=$('ding-profile').value;$('ding-profile').replaceChildren();option($('ding-profile'),'','请选择账号');for(const p of data.profiles)option($('ding-profile'),p.profile,p.userName+' · '+p.corpName);if(data.profiles.some(p=>p.profile===saved))$('ding-profile').value=saved;else{const current=data.profiles.filter(p=>p.isOrgCurrent);if(current.length===1)$('ding-profile').value=current[0].profile;$('ding-profile').onchange();}$('ding-feedback').textContent=data.profiles.length?'请选择账号，然后连接加载会话':'未找到登录账号，请查看下方连接说明';}catch(e){$('ding-feedback').textContent=e.message;}finally{b.disabled=false;}};
$('ding-connect').onclick=async()=>{const b=$('ding-connect');b.disabled=true;$('ding-feedback').textContent='正在核对本人身份并读取会话…';const profile=$('ding-profile').value;try{const data=await api('dingtalk-connect',{profile});if($('ding-profile').value!==profile)return;dingSettingsConversations=data.conversations;paintConversations('ding');$('ding-feedback').textContent=data.name+' · 已连接'+(data.complete?'':'（当前仅加载部分会话）');}catch(e){$('ding-feedback').textContent=e.message;}finally{b.disabled=false;}};
document.querySelectorAll('[data-platform]').forEach(button=>button.onclick=()=>{if(busy)return;platform=button.dataset.platform;refresh();});

$('ding-login').onclick=async()=>{try{$('ding-feedback').textContent=(await api('dingtalk-login',{})).message;}catch(e){$('ding-feedback').textContent=e.message;}};

async function loadMainConversations(){const b=$('main-ding-refresh');if(!state.settings.dingtalk_profile){openSettings();$('ding-feedback').textContent='先连接并保存钉钉账号，再在主页选择会话';return;}b.disabled=true;const profile=state.settings.dingtalk_profile;try{const data=await api('dingtalk-conversations',{profile});if(state.settings.dingtalk_profile!==profile)return;dingMainConversations=data.conversations;dingListProfile=profile;paintConversations('main-ding');if(!data.complete)toast('已加载部分会话，可刷新列表重试');}catch(e){toast(e.message);}finally{b.disabled=false;}}
$('main-ding-refresh').onclick=loadMainConversations;
async function saveMainConversation(item){const input=$('main-ding-input');busy=true;input.disabled=true;try{await api('settings',{dingtalk_conversation:item.id,dingtalk_name:item.name});toast('钉钉会话已选择，请点击开始监听');}catch(e){setConversation('main-ding',state.settings.dingtalk_conversation,state.settings.dingtalk_name);toast(e.message);}finally{busy=false;input.disabled=false;await refresh();}}

function renderRooms(){
  $('ding-room-section').hidden=platform!=='dingtalk';
  const rooms=state.rooms||[];
  if(checkedProfile!==state.settings.dingtalk_profile){checkedRooms.clear();batchOptions.auto_reply=false;batchOptions.reply_latest=false;checkedProfile=state.settings.dingtalk_profile;}
  checkedRooms=new Set([...checkedRooms].filter(id=>rooms.some(r=>r.id===id)));
  const active=rooms.filter(r=>r.enabled).length,connecting=rooms.filter(r=>r.starting).length;
  const failed=rooms.filter(r=>!r.enabled&&!r.starting&&r.status?.startsWith('开启失败')).length;
  $('room-state-summary').textContent=active+' 个监听中 · '+(rooms.length-active-connecting-failed)+' 个已暂停'+(connecting?' · '+connecting+' 个连接中':'')+(failed?' · '+failed+' 个失败':'');
  const all=$('select-all-rooms');all.checked=rooms.length>0&&checkedRooms.size===rooms.length;all.indeterminate=checkedRooms.size>0&&checkedRooms.size<rooms.length;all.disabled=busy||!rooms.length;
  $('batch-selection-note').textContent=checkedRooms.size?'已选 '+checkedRooms.size+' 个。'+(batchAction()==='stop'?'上方按钮可暂停所选监听。':'上方两个回复选项将应用于所选暂停会话；已运行的会话会跳过。'):'勾选会话后，可用上方按钮批量操作。';
  const key=JSON.stringify([platform,selectedRoom,busy,[...checkedRooms],rooms.map(r=>[r.id,r.name,r.enabled,r.starting,r.pending,r.auto_reply,r.status,r.activity])]);
  if(key===roomListKey)return;roomListKey=key;
  const list=$('ding-room-list');list.replaceChildren();
  if(!rooms.length){const p=document.createElement('p');p.className='muted room-empty';p.textContent='还没有监听会话，点击管理添加。';list.append(p);}
  for(const room of rooms){
    const button=document.createElement('button');button.type='button';button.className='room-item';button.classList.toggle('selected',room.id===selectedRoom);button.setAttribute('aria-pressed',String(room.id===selectedRoom));
    const phase=room.starting?'connecting':room.enabled?'listening':room.status?.startsWith('开启失败')?'failed':'paused';
    button.dataset.state=phase;button.classList.toggle('has-pending',!!room.pending);
    button.title=room.name+' · '+(room.status||'已暂停');
    const indicator=document.createElement('span');indicator.className='room-signal';indicator.setAttribute('aria-hidden','true');
    for(let i=0;i<4;i++){const bar=document.createElement('i');indicator.append(bar);}
    const copy=document.createElement('span');copy.className='room-copy';
    const title=document.createElement('strong');title.textContent=room.name;
    const status=document.createElement('small');status.className='room-status-line';
    const label=document.createElement('span');label.className='room-state-label';label.textContent=phase==='connecting'?'连接中':phase==='listening'?'监听中':phase==='failed'?'开启失败':'已暂停';status.append(label);
    if(room.pending){const pending=document.createElement('span');pending.className='room-pending-label';pending.textContent='待确认回复';status.append(pending);}
    else if(room.enabled){const mode=document.createElement('span');mode.className='room-reply-mode';mode.textContent=room.auto_reply?'自动回复':'人工确认';status.append(mode);}
    copy.append(title,status);
    if(room.activity){const activity=document.createElement('small');activity.className='room-activity';activity.textContent=room.activity.label+' · '+room.activity.time;copy.append(activity);}
    button.classList.toggle('is-active',room.enabled&&['generating','sending'].includes(room.activity?.phase));
    button.append(indicator,copy);
    button.onclick=()=>{if(busy)return;selectedRoom=room.id;refresh();};
    const row=document.createElement('div');row.className='room-select-row';
    const check=document.createElement('input');check.type='checkbox';check.checked=checkedRooms.has(room.id);check.disabled=busy;check.setAttribute('aria-label','选择会话：'+room.name);
    check.onchange=()=>{if(check.checked)checkedRooms.add(room.id);else checkedRooms.delete(room.id);$('batch-feedback').hidden=true;render(state);};
    row.append(check,button);list.append(row);
  }
}
function paintRoomOptions(){
  const box=$('rooms-options');box.replaceChildren();const q=$('rooms-search').value.trim().toLowerCase();
  const merged=new Map([...roomsDraft.values(),...dingMainConversations].map(r=>[r.id,r]));
  for(const room of merged.values()){
    if(q&&!room.name.toLowerCase().includes(q))continue;
    const label=document.createElement('label');label.className='room-option';const check=document.createElement('input');check.type='checkbox';check.checked=roomsDraft.has(room.id);const name=document.createElement('span');name.textContent=room.name;
    check.onchange=()=>{if(check.checked){if(roomsDraft.size>=12){check.checked=false;toast('最多添加 12 个会话');return;}roomsDraft.set(room.id,{id:room.id,name:room.name,auto_reply:false,reply_latest:false});}else roomsDraft.delete(room.id);$('rooms-save').textContent='保存选择 · '+roomsDraft.size;};label.append(check,name);box.append(label);
  }
  if(!box.children.length){const p=document.createElement('p');p.className='muted';p.textContent=q?'没有匹配的会话':'暂无可选会话，请刷新或检查账号连接。';box.append(p);}
  $('rooms-save').textContent='保存选择 · '+roomsDraft.size;
}
async function refreshRoomOptions(){
  const profile=state.settings.dingtalk_profile;if(!profile){$('rooms-feedback').textContent='请先在设置中连接并保存钉钉账号。';return;}
  $('rooms-refresh').disabled=true;$('rooms-feedback').textContent='正在读取会话…';
  try{const data=await api('dingtalk-conversations',{profile});if(state.settings.dingtalk_profile!==profile)return;dingMainConversations=data.conversations;dingListProfile=profile;$('rooms-feedback').textContent=data.complete?'新增会话默认人工确认，不会自动开启监听。':'仅加载部分会话，未显示的已选会话会保留。';paintRoomOptions();}catch(e){$('rooms-feedback').textContent=e.message;}finally{$('rooms-refresh').disabled=false;}
}
$('manage-rooms').onclick=()=>{roomsDraft=new Map((state.settings.dingtalk_rooms||[]).map(r=>[r.id,{...r}]));$('rooms-search').value='';$('rooms-feedback').textContent='';paintRoomOptions();$('rooms-dialog').showModal();refreshRoomOptions();};
$('rooms-close').onclick=()=>$('rooms-dialog').close();$('rooms-search').oninput=paintRoomOptions;$('rooms-refresh').onclick=refreshRoomOptions;
async function saveRooms(rooms){
  busy=true;try{const result=await api('dingtalk-rooms',{rooms});selectedRoom=result.conversation_id||'';render(result);return true;}catch(e){toast(e.message);return false;}finally{busy=false;await refresh();}
}
$('rooms-save').onclick=async()=>{const button=$('rooms-save');button.disabled=true;try{if(await saveRooms([...roomsDraft.values()])){$('rooms-dialog').close();toast('会话列表已更新');}}finally{button.disabled=false;}};
$('remove-room').onclick=async()=>{if(busy)return;await saveRooms((state.settings.dingtalk_rooms||[]).filter(r=>r.id!==selectedRoom));};

async function batchRooms(action){
  if(!state||busy||batchBusy||platform!=='dingtalk')return;
  if(action==='start'&&!state.settings.has_key){openSettings();$('settings-feedback').textContent='先连接云端模型，再开始监听';return;}
  const options={...batchOptions};
  busy=true;batchBusy=true;render(state);
  const feedback=$('batch-feedback');feedback.hidden=false;feedback.textContent=action==='start'?'正在连接各会话，请稍候…':'正在暂停各会话，请稍候…';
  try{
    const result=await api('dingtalk-'+action+'-selected',{room_ids:[...checkedRooms],...(action==='start'?options:{})});
    const items=result.batch_results||[],failed=items.filter(r=>r.result==='failed');
    feedback.textContent=(action==='start'?'已开启 ':'已暂停 ')+items.filter(r=>r.result===(action==='start'?'started':'stopped')).length+' 个，跳过 '+items.filter(r=>r.result==='skipped').length+' 个，失败 '+failed.length+' 个。'+failed.map(r=>r.name+'：'+r.error).join('；');
    for(const item of items){if(item.result==='started'){historyChoice['ding:'+item.id]=options.reply_latest;autoChoice['ding:'+item.id]=options.auto_reply;}}
    render(result);
  }catch(error){feedback.textContent=error.message;}
  finally{busy=false;batchBusy=false;await refresh();}
}
$('select-all-rooms').onchange=()=>{checkedRooms=$('select-all-rooms').checked?new Set((state.rooms||[]).map(r=>r.id)):new Set();$('batch-feedback').hidden=true;render(state);};

function renderActivity(){
  const items=[{id:'wechat',name:state.platforms.wechat.target||'微信当前会话',platform:'wechat',...state.platforms.wechat},...(state.rooms||[]).map(r=>({...r,platform:'dingtalk'}))]
    .filter(r=>r.activity&&!['idle','paused'].includes(r.activity.phase))
    .sort((a,b)=>b.activity.at-a.activity.at).slice(0,4);
  const panel=$('recent-activity');panel.hidden=!items.length;panel.replaceChildren();
  if(!items.length)return;
  const title=document.createElement('strong');title.textContent='最近会话活动';panel.append(title);
  for(const room of items){
    const button=document.createElement('button');button.type='button';button.className='activity-link';
    button.classList.toggle('is-active',room.enabled&&['generating','sending'].includes(room.activity.phase));
    button.textContent=(room.platform==='wechat'?'微信':'钉钉')+' · '+room.name+' · '+room.activity.label+' · '+room.activity.time;
    button.onclick=()=>{if(busy)return;platform=room.platform;if(platform==='dingtalk')selectedRoom=room.id;refresh();};panel.append(button);
  }
}
