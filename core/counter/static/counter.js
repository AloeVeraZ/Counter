'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="counter-token"]').content;
let state = null, auto = false, nextCount = null, ticking = false, setupSignature = '', calibrationSignature = '', displaySignature = '', pickerSignature = '', requestPending = 0, pollTimer = null, updateState = null, updateChannelChosen = false, updateWatch = null, calChannel = 0, previewTimer = null, draft = [], servoSignature = '', lastShown = [];
const MIN_PULSE = 600, MAX_PULSE = 2400, NUDGE = 10;
function schedulePoll(delay) { clearTimeout(pollTimer); pollTimer = setTimeout(tick,delay); }
function pollDelay() { if (document.hidden && !state?.armed) return 15000; return state?.busy ? 250 : auto ? 500 : state?.armed ? 1000 : 3000; }
function notice(message, error = false) { $('notice').textContent = message; $('notice').hidden = !message; $('notice').classList.toggle('error', error); }
function setAuto(value) { auto = value; nextCount = value ? Date.now() + Number($('interval').value) : null; $('auto').textContent = value ? 'Stop counting' : 'Count up'; $('auto').classList.toggle('primary', value); }
async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json', 'X-Counter-Token':token}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  if (response.status === 401) { setAuto(false); window.location.assign('/login'); throw new Error('Enter your Pi password to continue.'); }
  const payload = await response.json();
  if (!response.ok) { const error = new Error(payload.error || `Request failed (${response.status}).`); error.data = payload; throw error; }
  return payload;
}
async function command(path, data, message = '') {
  requestPending++;
  try { const result = await api(path, data); if (result.config) { render(result); if (!ticking) schedulePoll(150); } notice(message || result.message || ''); return result; }
  catch (error) { setAuto(false); notice(error.message, true); throw error; }
  finally { requestPending--; }
}
function run(action) { return () => { Promise.resolve().then(action).catch(() => {}); }; }
function view(name) {
  document.querySelectorAll('[data-page]').forEach(el => {el.hidden = el.dataset.page !== name;});
  document.querySelectorAll('[data-view]').forEach(el => {const active = el.dataset.view === name; el.classList.toggle('active', active); if (active) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current');});
  if(name==='system')checkUpdates().catch(error=>notice(error.message,true));
}
function option(value, text) { const el = document.createElement('option'); el.value = value; el.textContent = text; return el; }
function element(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; }
for (let n = 1; n <= 16; n++) $('count').append(option(n, `${n} ${n === 1 ? 'display' : 'displays'}`));
// Unsaved starting points: number 0 at the minimum pulse, evenly up to the maximum for 9.
function suggestedPulse(digit) { return Math.round(MIN_PULSE + digit * (MAX_PULSE - MIN_PULSE) / 9); }
function clampPulse(width) { return Math.min(MAX_PULSE, Math.max(MIN_PULSE, Math.round(width))); }
// Calibrate edits a draft of the chosen servo's ten pulses until Save. Servos use the board's channel numbers, 0–15.
function savedPositions() { return state?.config.positions[calChannel] || []; }
function unsavedCount() { const saved = savedPositions(); return draft.filter((width, digit) => width !== (saved[digit] ?? null)).length; }
function refreshCalibrationStatus() {
  const saved = savedPositions(), changes = unsavedCount();
  document.querySelectorAll('.calibration-row').forEach(row => {
    const digit = Number(row.dataset.digit), width = draft[digit] ?? null;
    const changed = width !== (saved[digit] ?? null);
    row.classList.toggle('changed', changed); row.classList.toggle('saved', !changed && width !== null); row.classList.toggle('unset', !changed && width === null);
    row.querySelector('.row-status').textContent = changed ? 'Not saved' : width === null ? 'Not set' : 'Saved';
  });
  $('save-calibration').textContent = changes ? `Save servo ${calChannel} · ${changes} change${changes === 1 ? '' : 's'}` : `Save servo ${calChannel}`;
  $('copy-calibration').textContent = `Same kind of servos? Copy servo ${calChannel} to the others`;
  $('copy-calibration').hidden = (state?.config.count || 1) < 2;
}
// Move to the latest − / + value once the previous move has finished, so quick taps do not pile up.
function previewSoon(channel, width, delay = 200) {
  clearTimeout(previewTimer);
  const attempt = () => { if (!state?.armed) return; if (state.busy || requestPending) { previewTimer = setTimeout(attempt, 250); return; } command('/api/preview', {channel, pulse_us: width}).catch(() => {}); };
  previewTimer = setTimeout(attempt, delay);
}
function selectDisplay(channel) {
  if (channel === calChannel) return true;
  if (unsavedCount() && !window.confirm(`Servo ${calChannel} has unsaved changes. Discard them?`)) return false;
  setAuto(false); clearTimeout(previewTimer); calChannel = channel; pickerSignature = ''; calibrationRows(true); renderPicker();
  return true;
}
function calibrationRows(force = false) {
  if (!state) return;
  const channel = calChannel;
  const signature = `${channel}:${JSON.stringify(savedPositions())}`;
  if (!force && signature === calibrationSignature) return;
  calibrationSignature = signature;
  draft = savedPositions().slice();
  $('calibration-rows').replaceChildren(...Array.from({length:10}, (_, digit) => {
    const row = element('div', 'calibration-row'); row.dataset.digit = digit;
    const suggested = suggestedPulse(digit);
    const input = element('input'); input.type = 'number'; input.min = MIN_PULSE; input.max = MAX_PULSE; input.step = '1'; input.inputMode = 'numeric';
    input.value = draft[digit] ?? ''; input.placeholder = String(suggested);
    input.setAttribute('aria-label', `Pulse for number ${digit} on servo ${channel}, in microseconds`);
    input.addEventListener('input', () => {
      const text = input.value.trim(); draft[digit] = text ? Number(text) : null; refreshCalibrationStatus();
      const width = Number(text);
      if (state.armed && text && Number.isInteger(width) && width >= MIN_PULSE && width <= MAX_PULSE) previewSoon(channel, width, 450);
    });
    input.addEventListener('change', () => { if (input.value.trim()) { draft[digit] = clampPulse(Number(input.value)); input.value = draft[digit]; refreshCalibrationStatus(); } });
    const nudge = direction => {
      const width = clampPulse((draft[digit] ?? suggested) + direction * NUDGE);
      draft[digit] = width; input.value = width; refreshCalibrationStatus();
      if (state.armed) previewSoon(channel, width); else notice('Press Start at the top to see it move.');
    };
    const minus = element('button', 'nudge', '−'); minus.setAttribute('aria-label', `Lower the pulse for number ${digit}`); minus.addEventListener('click', () => nudge(-1));
    const plus = element('button', 'nudge', '+'); plus.setAttribute('aria-label', `Raise the pulse for number ${digit}`); plus.addEventListener('click', () => nudge(1));
    const field = element('label', 'pulse-field'); field.append(input, element('span', null, 'µs'));
    const test = element('button', 'test', 'Test'); test.setAttribute('aria-label', `Test number ${digit}`);
    test.addEventListener('click', run(() => { setAuto(false); const width = draft[digit] ?? suggested; return command('/api/preview', {channel, pulse_us: width}, draft[digit] === null || draft[digit] === undefined ? `Number ${digit} isn't set yet, so this tried the suggested ${width} µs.` : ''); }));
    row.append(element('span', 'digit-label', String(digit)), minus, field, plus, test, element('span', 'row-status'));
    return row;
  }));
  refreshCalibrationStatus();
}
function renderPicker() {
  if (!state) return;
  const {count, positions} = state.config;
  const signature = JSON.stringify([count, positions, calChannel, state.moving_channel]);
  if (signature === pickerSignature) return;
  pickerSignature = signature;
  $('display-picker').replaceChildren(...Array.from({length:count}, (_, channel) => {
    const saved = positions[channel].filter(p => p !== null).length;
    const button = element('button', `display-chip${saved === 10 ? ' ready' : ''}${state.moving_channel === channel ? ' moving' : ''}`);
    button.type = 'button'; button.setAttribute('aria-pressed', String(channel === calChannel));
    button.setAttribute('aria-label', `Servo ${channel}, ${saved} of 10 numbers saved`);
    button.append(element('strong', null, String(channel)), element('small', null, saved === 10 ? '✓ Ready' : `${saved}/10`));
    button.addEventListener('click', () => selectDisplay(channel));
    return button;
  }));
  const saved = positions[calChannel].filter(p => p !== null).length;
  $('picked-status').textContent = `Servo ${calChannel} · ${saved} of 10 saved`;
}
// Settings → Servos: a read-only overview of every channel's saved pulses; editing happens in Calibrate.
function renderServoTable() {
  const {count, positions} = state.config;
  const signature = JSON.stringify([count, positions]);
  if (signature !== servoSignature) {
    servoSignature = signature;
    $('servo-rows').replaceChildren(...Array.from({length:16}, (_, channel) => {
      const used = channel < count, tr = element('tr', used ? '' : 'unused');
      const name = element('th'); name.scope = 'row'; name.append(element('strong', null, `Servo ${channel}`), element('small', null, used ? 'In use' : 'Not in use'));
      const now = element('td', 'servo-now'); now.id = `servo-now-${channel}`;
      tr.append(name, now);
      for (let digit = 0; digit < 10; digit++) {
        const width = positions[channel][digit];
        const cell = element('td', width === null ? 'unset' : '', width === null ? '—' : String(width));
        if (width === null) cell.setAttribute('aria-label', 'not set');
        tr.append(cell);
      }
      const cell = element('td');
      if (used) {
        const edit = element('button', 'servo-save', 'Calibrate'); edit.type = 'button'; edit.setAttribute('aria-label', `Calibrate servo ${channel}`);
        edit.addEventListener('click', () => { view('calibration'); if (selectDisplay(channel)) $('digit-positions').scrollIntoView({behavior:'smooth', block:'start'}); });
        cell.append(edit);
      }
      tr.append(cell);
      return tr;
    }));
  }
  for (let channel = 0; channel < 16; channel++) {
    const cell = $(`servo-now-${channel}`); if (!cell) continue;
    const digit = state.digits[channel], pulse = digit === null || digit === undefined ? null : positions[channel][Number(digit)];
    cell.textContent = channel >= count ? '—' : state.moving_channel === channel ? 'Moving…' : digit === null || digit === undefined ? 'Unknown' : pulse ? `${digit} · ${pulse} µs` : String(digit);
  }
}
function saveSetup(message) {
  setAuto(false);
  const settings = {count: Number($('count').value), settle_ms: Number($('settle').value), pause_ms: Number($('pause').value), release_after_move: true};
  return command('/api/setup', settings, message);
}
function render(value) {
  if (value.instance && value.instance !== token.slice(0,12)) { window.location.reload(); return; }
  state = value;
  const {count,settle_ms,positions} = value.config;
  const pause_ms = value.config.pause_ms ?? 500;
  const ready = positions.slice(0,count).filter(row => row.every(p => p !== null)).length;
  if (calChannel >= count) { calChannel = count - 1; calibrationSignature = ''; }
  $('connection').textContent = value.simulated ? 'Preview on this PC' : value.board.connected ? 'Board connected' : 'Board offline';
  $('connection').className = `status ${value.board.connected ? 'good' : 'bad'}`;
  $('mode').textContent = value.simulated ? 'SIMULATION · NO HARDWARE' : 'PCA9685 · 16 channels';
  $('movement').textContent = value.busy ? value.moving_channel === null ? 'Pausing between moves' : `Moving servo ${value.moving_channel}` : value.armed ? 'Servos on' : 'Servos off';
  $('movement').className = `status ${value.armed ? 'good' : ''}`;
  // One button turns the servos on to test and off again; Stop in the header always turns them off.
  $('control-title').textContent = value.armed ? 'Servos are on' : 'Servos are off';
  $('control-help').textContent = !value.board.connected && !value.simulated ? 'The servo board is offline. Check Settings → Servo board.'
    : value.armed ? `Servos move when you type a pulse, press − / +, Test, or Show. Press Stop when you finish.${value.simulated ? ' (Preview only: no real servos.)' : ''}`
    : `Press Start at the top to move the servos while you calibrate.${value.simulated ? ' This PC preview moves no real servos.' : ''}`;
  // Start and Stop sit together in the header; Stop always works, Start only when the board is ready.
  document.querySelector('.control-bar').classList.toggle('on', value.armed);
  document.body.classList.toggle('servos-on', value.armed);
  $('arm').disabled = value.armed || value.busy || !value.board.connected;
  $('arm').textContent = value.armed ? 'Started' : 'Start';
  $('show-number').disabled = !value.armed || value.busy;
  document.querySelectorAll('[data-step]').forEach(button => {button.disabled = !value.armed || value.busy;});
  $('zero').disabled = !value.armed || value.busy;
  $('auto').disabled = !value.armed;
  if (!value.armed) setAuto(false);
  $('calibrated').textContent = `${ready} of ${count}`;
  $('calibration-progress').style.width = `${100 * ready / count}%`;
  $('display-count').textContent = count; $('last-channel').textContent = count - 1;
  $('range-label').textContent = `${'0'.repeat(count)}–${'9'.repeat(count)}`;
  $('number').maxLength = count;
  const signature = `${count}:${settle_ms}:${pause_ms}`;
  if (signature !== setupSignature) {
    setupSignature = signature; $('count').value = count;
    if (![...$('settle').options].some(o => Number(o.value) === settle_ms)) $('settle').append(option(settle_ms, `${settle_ms} ms`));
    $('settle').value = settle_ms;
    if (![...$('pause').options].some(o => Number(o.value) === pause_ms)) $('pause').append(option(pause_ms,`${pause_ms} ms`));
    $('pause').value = pause_ms;
    $('number').value = value.number;
  }
  // Show each servo's last commanded number as it moves (a test walks 0–9), else the requested number.
  const shown = [...value.number].map((digit, channel) => {
    if (value.moving_channel === channel && value.moving_digit !== null && value.moving_digit !== undefined) lastShown[channel] = value.moving_digit;
    else if (value.digits[channel] !== null && value.digits[channel] !== undefined) lastShown[channel] = value.digits[channel];
    else if (value.moving_channel !== channel) lastShown[channel] = null;
    return lastShown[channel] ?? digit;
  });
  const nextDisplaySignature = `${shown.join('')}:${value.moving_channel}`;
  if (displaySignature !== nextDisplaySignature) {
    displaySignature = nextDisplaySignature;
    $('digits').classList.toggle('many',count>4);
    $('digits').replaceChildren(...shown.map((digit,channel) => {
      const module = element('div', `digit-module${value.moving_channel === channel ? ' moving' : ''}`);
      module.append(element('div', 'digit-window', digit), element('div', 'digit-channel', `CH ${String(channel).padStart(2,'0')}`));
      return module;
    }));
    $('digits').setAttribute('aria-label', `Number on the display: ${value.number}`);
  }
  calibrationRows();
  renderPicker();
  renderServoTable();
  document.querySelectorAll('.test').forEach(button=>{button.disabled=!value.armed || value.busy;});
  $('save-calibration').disabled = $('copy-calibration').disabled = value.busy;
  $('test-sequence').disabled = !value.armed || value.busy;
  $('test-sequence').textContent = value.busy && value.armed ? 'Testing…' : 'Test configuration';
  $('board-connected').textContent = value.simulated ? 'PREVIEW' : value.board.connected ? 'CONNECTED' : 'OFFLINE';
  $('board-connected').className = `pill ${value.board.connected ? 'good' : 'bad'}`;
  $('board-summary').textContent = value.simulated ? 'Running as a preview on this PC. No servo board is used.'
    : value.board.connected ? 'The servo board is powered and connected. Servos themselves can’t be detected, so the Servos list shows which channels are in use.'
    : 'Counter can’t reach the servo board. Check the wiring guide below, then restart Counter.';
  $('board-detail').textContent = value.board.message;
  if (value.error) notice(value.error,true);
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>view(button.dataset.view)));
$('arm').addEventListener('click',run(()=>command('/api/arm',{},'Servos are on. Type a pulse, press − / +, or Test to move a servo.')));
$('stop').addEventListener('click',run(()=>{setAuto(false);clearTimeout(previewTimer);return command('/api/stop',{},'Stopped. Servos are off.');}));
if ($('logout')) $('logout').addEventListener('click',run(async()=>{setAuto(false);await command('/api/logout',{});window.location.assign('/login');}));
$('number-form').addEventListener('submit',event=>{event.preventDefault();setAuto(false);run(()=>command('/api/number',{number:$('number').value.trim()}))();});
document.querySelectorAll('[data-step]').forEach(button=>button.addEventListener('click',run(async()=>{setAuto(false);const result=await command('/api/step',{delta:Number(button.dataset.step)});$('number').value=result.number;})));
$('zero').addEventListener('click',run(async()=>{setAuto(false);const result=await command('/api/number',{number:'0'});$('number').value=result.number;}));
$('auto').addEventListener('click',()=>setAuto(!auto));
$('test-sequence').addEventListener('click',run(()=>{setAuto(false);clearTimeout(previewTimer);return command('/api/test-sequence',{});}));
$('interval').addEventListener('change',()=>{if(auto)nextCount=Date.now()+Number($('interval').value);});
$('setup-form').addEventListener('submit',event=>{event.preventDefault();run(()=>saveSetup('Setup saved. Servos are off.'))();});
$('timing-form').addEventListener('submit',event=>{event.preventDefault();run(()=>saveSetup('Timing saved. Servos are off.'))();});
$('save-calibration').addEventListener('click',run(()=>{setAuto(false);clearTimeout(previewTimer);const channel=calChannel;return command('/api/calibration',{channel,positions:draft.slice()},`Servo ${channel} saved. Servos are off.`).then(()=>calibrationRows(true));}));
// For identical servos: copy one servo's saved pulses to the others, then fine-tune any that differ.
$('copy-calibration').addEventListener('click',run(async()=>{
  const source=calChannel, count=state.config.count, saved=state.config.positions[source].slice();
  if(!saved.some(width=>width!==null)){notice(`Save some pulses on servo ${source} first.`,true);return;}
  const extra=unsavedCount()?' Unsaved changes on this servo are not copied.':'';
  const others=Array.from({length:count},(_,channel)=>channel).filter(channel=>channel!==source).join(', ');
  if(!window.confirm(`Copy servo ${source}'s saved pulses to servo${count===2?'':'s'} ${others}? Their saved pulses will be replaced.${extra}`))return;
  setAuto(false);clearTimeout(previewTimer);
  for(let channel=0;channel<count;channel++){if(channel!==source)await command('/api/calibration',{channel,positions:saved});}
  notice(`Copied servo ${source} to the others. Check each one and fine-tune any that look off.`);
}));
function renderUpdates(){
  if(!updateState)return;
  const result=updateState, branch=$('update-branch').value, target=result.targets?.find(row=>row.branch===branch);
  const testing=branch==='testing', switching=branch!==(result.branch||'main');
  const installing=result.job?.state==='running';
  $('installed').textContent=result.installed||'Local source';
  $('update-channel').textContent=(result.branch||'main')==='testing'?'TESTING':'STABLE';
  $('latest').textContent=target?.latest||'—';
  const installerURL=`https://raw.githubusercontent.com/AloeVeraZ/Counter/${branch}/install.sh`;
  $('download-installer').href=installerURL;
  $('download-installer').textContent=`Get ${branch} installer ↗`;
  $('install-command').textContent=`curl -fsSL ${installerURL} | bash -s -- --branch ${branch}`;
  $('testing-warning').hidden=!testing;
  $('update').textContent=switching?`Switch to ${testing?'Testing':'Stable'}`:'Update now';
  $('update-branch').disabled=installing||Boolean(updateWatch);
  $('update').disabled=Boolean(updateWatch)||installing||!result.installable||!target?.available||result.checking||(testing&&!$('testing-ack').checked);
  $('update-log').textContent=(result.job?.log||[]).join('\n');
  $('update-log').hidden=!result.job?.log?.length;
  const label=testing?'Testing':'Stable';
  $('update-detail').textContent=installing?'Updating… the servos are off.':result.job?.state==='failed'?'The last update failed. The log below shows why.':result.checking?'Checking for updates…':target?.latest?(switching?`Switch this Pi to ${label}.`:target.available?'An update is ready to install.':`Counter is up to date (${label}).`):result.error||`No ${label} release is available.`;
  $('update-detail').classList.toggle('ready',Boolean(target?.available)&&!installing);
}
async function checkUpdates(refresh=false){
  updateState=await api(`/api/updates${refresh?'?refresh=1':''}`);
  if(!updateChannelChosen)$('update-branch').value=updateState.branch||'main';
  renderUpdates();
  if(updateState.job?.state==='running')watchUpdate(updateState.job.installed_commit||state?.installed_commit||'');
  else if(updateState.checking)setTimeout(()=>checkUpdates().catch(error=>notice(error.message,true)),1500);
}
async function updateJob(){
  const response=await fetch('/api/updates/job',{cache:'no-store'});
  if(response.status===401){window.location.assign('/login');throw new Error('Enter your Pi password to continue.');}
  if(!response.ok)throw new Error(`Update status unavailable (${response.status}).`);
  return response.json();
}
// The log follows its newest line, like a terminal, until someone scrolls up to read it.
let updateLogFollows=true;
$('update-log').addEventListener('scroll',event=>{const log=event.target;updateLogFollows=log.scrollHeight-log.scrollTop-log.clientHeight<24;});
function showUpdateProgress(mode,stage,progress,log){
  const block=$('update-progress-block');block.hidden=false;
  for(const name of ['failed','done','waiting'])block.classList.toggle(name,mode===name);
  $('update-stage').textContent=stage;
  if(progress!==null){$('update-progress').style.width=`${progress}%`;$('update-percent').textContent=`${progress}%`;}
  if(log!==null){const element=$('update-log');element.textContent=log.join('\n');element.hidden=!log.length;if(updateLogFollows)element.scrollTop=element.scrollHeight;}
}
function reloadToSystem(delay){setTimeout(()=>{window.location.hash='system';window.location.reload();},delay);}
// Follow the update on the System page through the service restart, then reload into the new release.
function watchUpdate(fromCommit){
  if(updateWatch)return;
  updateWatch={from:fromCommit,started:Date.now(),timer:null};
  setAuto(false);clearTimeout(pollTimer);updateLogFollows=true;
  if(document.querySelector('[data-page="system"]').hidden)view('system');
  $('update').disabled=$('update-branch').disabled=$('check-updates').disabled=true;
  $('connection').textContent='Updating';$('connection').className='status';
  $('update-detail').textContent='Updating… the servos are off. This page reloads by itself when it’s done.';
  showUpdateProgress('running','Starting the update…',0,null);
  pollUpdate();
}
async function pollUpdate(){
  const watch=updateWatch;if(!watch)return;
  let job=null;
  try{job=await updateJob();}catch{}
  if(updateWatch!==watch)return;
  if(!job){
    showUpdateProgress('waiting','Waiting for Counter to restart…',null,null);
  }else{
    if(!watch.from)watch.from=job.installed_commit||'';
    // A restarted service has a new instance token, so this page's commands would be refused.
    const restarted=job.instance!==token.slice(0,12)||Boolean(watch.from&&job.installed_commit&&job.installed_commit!==watch.from);
    const stage=job.stage||'installing';
    if(job.state==='finished'){
      showUpdateProgress('done','Update finished. Reloading…',100,job.log);
      if(restarted){reloadToSystem(1500);return;}
      endUpdateWatch('The update finished; Counter is already running this version.');return;
    }
    if(job.state==='failed'){
      showUpdateProgress('failed',`Update failed while ${stage}.`,null,job.log);
      $('update-detail').textContent='Counter is still on the previous version. The log below shows what went wrong.';
      if(restarted){reloadToSystem(4000);return;}
      endUpdateWatch('');return;
    }
    showUpdateProgress('running',`${stage[0].toUpperCase()}${stage.slice(1)}…`,job.progress??null,job.log);
  }
  if(Date.now()-watch.started>30*60*1000)$('update-detail').textContent='This is taking longer than usual. Check journalctl -u counter-update on the Pi.';
  watch.timer=setTimeout(pollUpdate,2000);
}
function endUpdateWatch(message){
  if(updateWatch)clearTimeout(updateWatch.timer);
  updateWatch=null;if(message)notice(message);schedulePoll(0);
  checkUpdates().catch(error=>notice(error.message,true));
}
// The update asks for the Pi password in a popup, checked by Counter before anything starts.
let updateBusy=false;
async function requestUpdate(password){
  setAuto(false);updateBusy=true;$('update').disabled=true;
  const from=state?.installed_commit||'';
  try{
    const result=await api('/api/updates',{branch:$('update-branch').value,acknowledge_testing:$('testing-ack').checked,...(password?{password}:{})});
    closeUpdatePassword();notice(result.message||'');watchUpdate(from);
  }catch(error){
    if(error.data?.password_required&&$('update-password-dialog'))openUpdatePassword(error.message);
    else{closeUpdatePassword();notice(error.message,true);}
    renderUpdates();
  }finally{updateBusy=false;setPasswordBusy(false);}
}
function setPasswordBusy(busy){
  if(!$('update-password-dialog'))return;
  $('update-password').disabled=$('update-password-cancel').disabled=$('update-password-submit').disabled=busy;
  $('update-password-submit').textContent=busy?'Checking…':'Update now';
}
function openUpdatePassword(problem=''){
  const dialog=$('update-password-dialog');
  $('update-password-error').textContent=problem;$('update-password-error').hidden=!problem;
  $('update-password').value='';setPasswordBusy(false);
  if(!dialog.open)dialog.showModal();
  $('update-password').focus();
}
function closeUpdatePassword(){const dialog=$('update-password-dialog');if(dialog?.open)dialog.close();}
if($('update-password-dialog')){
  $('update-password-form').addEventListener('submit',event=>{event.preventDefault();const password=$('update-password').value;if(!password||updateBusy)return;$('update-password').value='';setPasswordBusy(true);requestUpdate(password);});
  $('update-password-cancel').addEventListener('click',closeUpdatePassword);
  // Escape does not hide a password that is still being checked.
  $('update-password-dialog').addEventListener('cancel',event=>{if(updateBusy)event.preventDefault();});
  $('update-password-dialog').addEventListener('close',()=>{$('update-password').value='';});
}
$('update-branch').addEventListener('change',()=>{updateChannelChosen=true;$('testing-ack').checked=false;renderUpdates();});
$('testing-ack').addEventListener('change',renderUpdates);
$('check-updates').addEventListener('click',run(()=>checkUpdates(true)));
$('update').addEventListener('click',()=>{
  const branch=$('update-branch').value, switching=branch!==(updateState?.branch||'main');
  if(!window.confirm(`${switching?`Switch to ${branch==='testing'?'Testing':'Stable'}`:'Install the update'}? The servos turn off and Counter restarts. Your setup and calibration are kept.`))return;
  if($('update-password-dialog'))openUpdatePassword();else requestUpdate('');
});
$('theme').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;$('theme').setAttribute('aria-label',`Toggle ${theme==='dark'?'light':'dark'} theme`);try{localStorage.setItem('counter-theme',theme);}catch{}});
try{if(localStorage.getItem('counter-theme')==='light')document.documentElement.dataset.theme='light';}catch{}
document.addEventListener('visibilitychange',()=>{if(document.hidden)setAuto(false);if(!ticking)schedulePoll(document.hidden?pollDelay():0);});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!updateWatch&&!$('update-password-dialog')?.open)$('stop').click();});
async function tick(){
  if(ticking||updateWatch)return;ticking=true;
  try{
    const result=await api('/api/state');render(result);
    if(auto&&!requestPending){
      if(result.busy)nextCount=null;
      else if(nextCount===null)nextCount=Date.now()+Number($('interval').value);
      else if(Date.now()>=nextCount){nextCount=null;const response=await command('/api/step',{delta:1});$('number').value=response.number;}
    }
  }catch(error){setAuto(false);$('connection').textContent='Disconnected';$('connection').className='status bad';$('arm').disabled=$('show-number').disabled=true;notice(error.message||'Can’t reach Counter. Counting is paused.',true);}
  finally{ticking=false;schedulePoll(pollDelay());}
}
tick();
// Reopen System after an update reload, and follow an update already running (reload or another device).
if(window.location.hash==='#system'){view('system');history.replaceState(null,'',window.location.pathname);}
updateJob().then(job=>{if(job.state==='running')watchUpdate(job.installed_commit||'');}).catch(()=>{});
