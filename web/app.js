const $ = id => document.getElementById(id);
let nativeAccessGranted = false;
let state = null, busy = false, toastTimer, platform = 'wechat', dingListProfile = null, historyChoice = {wechat:false,dingtalk:false}, historyLoaded = false;
async function api(path, data) {
  const response = await fetch('/api/' + path + (data === undefined ? '?platform='+platform : ''), data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...data,platform})});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || '操作失败');
  return body;
}
function toast(text) { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,5500); }
function render(next) {
  if(next.platform && next.platform!==platform)return;
  state=next;
  for(const name of ['wechat','dingtalk']){const p=state.platforms[name];$(name+'-status').textContent=p.enabled?'正在监听':p.starting?'正在开启':'已暂停';$(name+'-target').textContent=p.target||(name==='wechat'?'当前打开的会话':state.settings.dingtalk_name||'请在设置中选择会话');document.querySelector('[data-platform='+name+']').classList.toggle('selected',name===platform);}
  $('style-summary').textContent='回复风格 · '+state.settings.style;
  $('scope-title').textContent=platform==='wechat'?'微信 · 当前会话':'钉钉 · 固定会话';
  $('scope-description').textContent=platform==='wechat'?'发送时自动将微信置前；切换联系人后暂停微信监听。':'监听设置里选定的会话，不需要钉钉保持在前台。';
  if(!historyLoaded){for(const p of ['wechat','dingtalk'])historyChoice[p]=state.settings[p+'_reply_latest'];historyLoaded=true;}
  $('reply-latest').checked=historyChoice[platform];$('reply-latest').disabled=busy||state.enabled||state.starting;
  $('main-ding-picker').hidden=platform!=='dingtalk';
  if(dingListProfile!==state.settings.dingtalk_profile){dingListProfile=null;$('main-ding-conversation').replaceChildren();option($('main-ding-conversation'),'','请选择会话');}
  if(state.settings.dingtalk_conversation&&!Array.from($('main-ding-conversation').options).some(o=>o.value===state.settings.dingtalk_conversation))option($('main-ding-conversation'),state.settings.dingtalk_conversation,state.settings.dingtalk_name);
  $('main-ding-conversation').value=state.settings.dingtalk_conversation;
  $('main-ding-conversation').disabled=busy;
  $('dot').classList.toggle('on',state.enabled);
  $('run-label').textContent=state.enabled?'正在自动回复':state.starting?'正在开启':'已暂停';
  $('headline').textContent=state.enabled?(state.target?`正在照看「${state.target}」`:'自动回复已开启，等待会话'):'让每一次回应，恰到好处。';
  $('status').textContent=state.status;
  $('toggle').textContent=state.enabled||state.starting?'暂停自动回复':'开启自动回复 ↗';
  $('toggle').disabled=busy;
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
  if(state.latest){$('incoming-label').textContent=state.latest.sender?'最新消息 · '+state.latest.sender:'当前最新消息';$('reply-label').hidden=!state.latest.reply&&state.latest.state!=='生成中';$('chat-target').textContent=state.target;$('incoming').textContent=state.latest.incoming;$('reply').textContent=state.latest.reply||(state.latest.state==='生成中'?'正在生成…':'');$('reply').hidden=!state.latest.reply&&state.latest.state!=='生成中';$('reply-image').hidden=!state.latest.image_path;if(state.latest.image_path){const url='/api/reply-image?platform='+platform+'&v='+encodeURIComponent(state.latest.image_path);if($('reply-image').getAttribute('src')!==url)$('reply-image').src=url;}$('reply-state').textContent=state.latest.state+(state.latest.sources?.length?' · 依据：'+state.latest.sources.join('、'):'')+(state.latest.knowledge_warnings?.length?' · '+state.latest.knowledge_warnings.join('；'):'');}
  $('events').replaceChildren();
  if(!state.events.length){const li=document.createElement('li');li.className='muted';li.textContent='开启后显示处理进度';$('events').append(li);}
  for(const event of state.events){const li=document.createElement('li'),time=document.createElement('time'),text=document.createElement('span');time.textContent=event.time;text.textContent=event.text;li.append(time,text);$('events').append(li);}
}
async function refresh(){try{if(!busy)render(await api('state'));}catch(error){$('status').textContent='助手服务未连接，请重新打开应用';$('toggle').disabled=true;}}
function openSettings(){if(!state)return;$('ding-image-mode').value=state.settings.dingtalk_image_mode||'ocr';$('ding-vision-url').value=state.settings.dingtalk_vision_url||'';$('ding-vision-model').value=state.settings.dingtalk_vision_model||'';$('ding-vision-key').value='';$('ding-vision-key').placeholder=state.settings.has_vision_key?'已保存，留空保留原 Key':'填写视觉模型 API Key';$('base-url').value=state.settings.base_url;$('model').value=state.settings.model;$('api-key').value='';$('excluded-senders').value=state.settings.excluded_senders.join('\n');$('self-names').value=(state.settings.self_names||[]).join('\n');$('api-key').placeholder=state.settings.has_key?'已保存，留空保留原 Key':'填写你的 API Key';$('voice-profile').value=state.settings.voice_profile||'';$('reply-examples').value=state.settings.reply_examples||'';$('work-knowledge').value=state.settings.work_knowledge||'';$('spd-knowledge').value=state.settings.spd_knowledge||'enabled';$('settings-feedback').textContent='';fillDingSettings();$('settings').showModal();}
$('settings-open').onclick=openSettings;
$('settings-close').onclick=()=>$('settings').close();
$('settings').addEventListener('close',()=>{$('api-key').value='';$('ding-vision-key').value='';});
function formData(){return {dingtalk_image_mode:$('ding-image-mode').value,dingtalk_vision_url:$('ding-vision-url').value.trim(),dingtalk_vision_model:$('ding-vision-model').value.trim(),dingtalk_vision_key:$('ding-vision-key').value.trim(),style:document.querySelector('input[name=style]:checked').value, dingtalk_profile:$('ding-profile').value,dingtalk_conversation:$('ding-conversation').value,dingtalk_name:$('ding-conversation').value?($('ding-conversation').selectedOptions[0]?.textContent||''):'',dingtalk_interval:Number($('ding-interval').value),voice_profile:$('voice-profile').value.trim(),reply_examples:$('reply-examples').value.trim(),work_knowledge:$('work-knowledge').value.trim(),spd_knowledge:$('spd-knowledge').value,base_url:$('base-url').value.trim(),model:$('model').value.trim(),api_key:$('api-key').value.trim(),self_names:$('self-names').value.split('\n').map(x=>x.trim()).filter(Boolean),excluded_senders:$('excluded-senders').value.split('\n').map(x=>x.trim()).filter(Boolean)};}
$('settings-form').onsubmit=async e=>{e.preventDefault();const submit=e.submitter;submit.disabled=true;try{await api('settings',formData());$('settings').close();toast('设置已保存');await refresh();}catch(error){$('settings-feedback').textContent=error.message;}finally{submit.disabled=false;}};
$('test-connection').onclick=async()=>{const button=$('test-connection');button.disabled=true;button.textContent='连接中…';try{const result=await api('test',formData());$('settings-feedback').textContent=result.message;}catch(error){$('settings-feedback').textContent=error.message;}finally{button.disabled=false;button.textContent='测试连接';}};
$('toggle').onclick=async()=>{if(!state)return;if(!state.enabled&&!state.settings.has_key){openSettings();$('settings-feedback').textContent='先连接云端模型，再开启自动回复';return;}const action=state.enabled||state.starting?'stop':'start';busy=true;$('toggle').disabled=true;if(action==='start'){$('run-label').textContent='正在开启';$('status').textContent='正在连接当前会话…';$('toggle').textContent='正在开启…';}try{render(await api(action,action==='start'?{reply_latest:historyChoice[platform]}:{}));}catch(error){toast(error.message);}finally{busy=false;await refresh();}};
document.querySelectorAll('input[name=style]').forEach(input=>input.onchange=async()=>{busy=true;try{await api('settings',{style:input.value});toast('风格已更新，下一条回复生效');}catch(e){toast(e.message);}finally{busy=false;await refresh();}});
$('reply-latest').onchange=()=>{historyChoice[platform]=$('reply-latest').checked;};

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
$('preview-generate').onclick=async()=>{const button=$('preview-generate');button.disabled=true;button.textContent='生成中…';try{const result=await api('preview',{text:$('preview-input').value,style:state.settings.style});$('preview-result').textContent=result.reply;}catch(error){$('preview-result').textContent=error.message;}finally{button.disabled=false;button.textContent='生成示例回复';}};
refresh();setInterval(refresh,1200);

$('scan').onclick=async()=>{const b=$('scan');b.disabled=true;b.setAttribute('aria-busy','true');b.title='正在读取消息';try{render(await api('scan',{}));}catch(error){toast(error.message);$('status').textContent=error.message;}finally{b.disabled=false;b.removeAttribute('aria-busy');b.title='刷新当前消息';}};

function option(select,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;select.append(o);}
function fillDingSettings(){const c=state.settings;$('ding-profile').replaceChildren();option($('ding-profile'),'','请选择账号');if(c.dingtalk_profile)option($('ding-profile'),c.dingtalk_profile,'已保存的钉钉账号');$('ding-profile').value=c.dingtalk_profile;$('ding-conversation').replaceChildren();option($('ding-conversation'),'','请选择监听会话');if(c.dingtalk_conversation)option($('ding-conversation'),c.dingtalk_conversation,c.dingtalk_name);$('ding-conversation').value=c.dingtalk_conversation;$('ding-interval').value=c.dingtalk_interval;$('ding-feedback').textContent='';}
$('ding-profile').onchange=()=>{$('ding-conversation').replaceChildren();option($('ding-conversation'),'','请连接账号后选择会话');};
$('ding-profiles').onclick=async()=>{const b=$('ding-profiles');b.disabled=true;try{const data=await api('dingtalk-profiles',{});const saved=$('ding-profile').value;$('ding-profile').replaceChildren();option($('ding-profile'),'','请选择账号');for(const p of data.profiles)option($('ding-profile'),p.profile,p.userName+' · '+p.corpName);if(data.profiles.some(p=>p.profile===saved))$('ding-profile').value=saved;else{const current=data.profiles.filter(p=>p.isOrgCurrent);if(current.length===1)$('ding-profile').value=current[0].profile;$('ding-profile').onchange();}$('ding-feedback').textContent=data.profiles.length?'请选择账号，然后连接加载会话':'未找到登录账号，请查看下方连接说明';}catch(e){$('ding-feedback').textContent=e.message;}finally{b.disabled=false;}};
$('ding-connect').onclick=async()=>{const b=$('ding-connect');b.disabled=true;$('ding-feedback').textContent='正在核对本人身份并读取会话…';const profile=$('ding-profile').value;try{const data=await api('dingtalk-connect',{profile});if($('ding-profile').value!==profile)return;const saved=$('ding-conversation').value;$('ding-conversation').replaceChildren();option($('ding-conversation'),'','请选择监听会话');for(const c of data.conversations)option($('ding-conversation'),c.id,c.name);if(data.conversations.some(c=>c.id===saved))$('ding-conversation').value=saved;$('ding-feedback').textContent=data.name+' · 已连接'+(data.complete?'':'（当前仅加载部分会话）');}catch(e){$('ding-feedback').textContent=e.message;}finally{b.disabled=false;}};
document.querySelectorAll('[data-platform]').forEach(button=>button.onclick=()=>{if(busy)return;platform=button.dataset.platform;refresh().then(()=>{if(platform==='dingtalk'&&state.settings.dingtalk_profile&&dingListProfile!==state.settings.dingtalk_profile)loadMainConversations();});});

$('ding-login').onclick=async()=>{try{$('ding-feedback').textContent=(await api('dingtalk-login',{})).message;}catch(e){$('ding-feedback').textContent=e.message;}};

async function loadMainConversations(){const b=$('main-ding-refresh');if(!state.settings.dingtalk_profile){openSettings();$('ding-feedback').textContent='先连接并保存钉钉账号，再在主页选择会话';return;}b.disabled=true;const profile=state.settings.dingtalk_profile;try{const data=await api('dingtalk-conversations',{profile});if(state.settings.dingtalk_profile!==profile)return;$('main-ding-conversation').replaceChildren();option($('main-ding-conversation'),'','请选择会话');for(const c of data.conversations)option($('main-ding-conversation'),c.id,c.name);dingListProfile=profile;$('main-ding-conversation').value=state.settings.dingtalk_conversation;if(!data.complete)toast('已加载部分会话，可刷新列表重试');}catch(e){toast(e.message);}finally{b.disabled=false;}}
$('main-ding-refresh').onclick=loadMainConversations;
$('main-ding-conversation').onchange=async()=>{const select=$('main-ding-conversation');busy=true;select.disabled=true;try{await api('settings',{dingtalk_conversation:select.value,dingtalk_name:select.value?select.selectedOptions[0].textContent:''});toast('钉钉会话已选择，请点击开启自动回复');}catch(e){toast(e.message);}finally{busy=false;await refresh();}};
