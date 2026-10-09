'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="counter-token"]').content;
let state = null, auto = false, nextCount = null, ticking = false, setupSignature = '', calibrationSignature = '', displaySignature = '', channelSignature = '', requestPending = 0, pollTimer = null;
function schedulePoll(delay) { clearTimeout(pollTimer); pollTimer = setTimeout(tick,delay); }
function pollDelay() { if (document.hidden && !state?.armed) return 15000; return state?.busy || auto ? 500 : state?.armed ? 1000 : 3000; }
function notice(message, error = false) { $('notice').textContent = message; $('notice').hidden = !message; $('notice').classList.toggle('error', error); }
function setAuto(value) { auto = value; nextCount = value ? Date.now() + Number($('interval').value) : null; $('auto').textContent = value ? 'Pause counting' : 'Start counting'; }
async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json', 'X-Counter-Token':token}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  if (response.status === 401) { setAuto(false); window.location.assign('/login'); throw new Error('Enter your Pi password to continue.'); }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
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
}
function option(value, text) { const el = document.createElement('option'); el.value = value; el.textContent = text; return el; }
for (let n = 1; n <= 16; n++) $('count').append(option(n, `${n} ${n === 1 ? 'display' : 'displays'}`));
function calibrationRows(force = false) {
  if (!state) return;
  const channel = Number($('cal-channel').value);
  const positions = state.config.positions[channel];
  const signature = `${channel}:${JSON.stringify(positions)}`;
  if (!force && signature === calibrationSignature) return;
  calibrationSignature = signature;
  $('test-channel').textContent = `CH ${channel}`;
  $('calibration-rows').replaceChildren();
  for (let digit = 0; digit < 10; digit++) {
    const row = document.createElement('div'); row.className = 'calibration-row';
    const digitLabel = document.createElement('span'); digitLabel.className = 'digit-label'; digitLabel.textContent = digit;
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'number'; input.min = '600'; input.max = '2400'; input.step = '1'; input.dataset.digit = digit; input.setAttribute('aria-label', `Digit ${digit} pulse width`);
    input.value = positions[digit] ?? ''; input.placeholder = String(Math.round(1000 + digit * 1000 / 9));
    const unit = document.createElement('span'); unit.textContent = 'µs'; label.append(input, unit);
    const test = document.createElement('button'); test.className = 'test'; test.textContent = 'Test digit'; test.disabled = !state.armed || state.busy; test.addEventListener('click',run(() => { if (!input.value) {notice('Enter a pulse width before testing this digit.',true); return;} setAuto(false); return command('/api/preview',{channel,pulse_us:Number(input.value)}); }));
    row.append(digitLabel,label,test); $('calibration-rows').append(row);
  }
}
function render(value) {
  if (value.instance && value.instance !== token.slice(0,12)) { window.location.reload(); return; }
  state = value;
  const {count,settle_ms,positions} = value.config;
  const pause_ms = value.config.pause_ms ?? 500;
  const ready = positions.slice(0,count).filter(row => row.every(p => p !== null)).length;
  $('connection').textContent = value.simulated ? 'Simulation' : value.board.connected ? 'Board connected' : 'Board offline';
  $('connection').className = `status ${value.board.connected ? 'good' : 'bad'}`;
  $('mode').textContent = value.simulated ? 'SIMULATION · NO HARDWARE' : 'PCA9685 · 16 channels';
  const controlName = value.simulated ? 'Preview controls' : 'Servo control';
  $('movement').textContent = value.busy ? value.moving_channel === null ? 'Pause between digit moves' : `Moving CH ${value.moving_channel}` : `${controlName} ${value.armed ? 'enabled' : 'disabled'}`;
  $('movement').className = `status ${value.armed ? 'good' : ''}`;
  $('armed-label').textContent = value.armed ? 'ENABLED' : 'DISABLED';
  const enableLabel = value.armed ? `${controlName} enabled` : `Enable ${controlName.toLowerCase()}`;
  $('arm').textContent = $('cal-arm').textContent = enableLabel;
  // Also updates an already-running preview whose template was loaded before this release.
  const controlPanel = $('arm').closest('.panel');
  controlPanel.querySelector('h2').textContent = controlName;
  controlPanel.querySelector('p.help').textContent = value.simulated
    ? 'Enable to try the display and calibration controls on this PC. This preview sends no commands to real servos.'
    : 'Enable to allow servo movement commands. Enabling does not move a servo; choose a number or test a position to move it. Stop outputs disables control again.';
  $('arm').disabled = $('cal-arm').disabled = value.armed || value.busy || !value.board.connected;
  $('show-number').disabled = !value.armed || value.busy;
  document.querySelectorAll('[data-step]').forEach(button => {button.disabled = !value.armed || value.busy;});
  $('zero').disabled = !value.armed || value.busy;
  $('auto').disabled = !value.armed;
  if (!value.armed) setAuto(false);
  $('calibrated').textContent = `${ready} / ${count} ready`;
  $('calibration-progress').style.width = `${100 * ready / count}%`;
  $('display-count').textContent = count; $('last-channel').textContent = count - 1;
  $('range-label').textContent = `${'0'.repeat(count)}–${'9'.repeat(count)}`;
  $('number').maxLength = count;
  const signature = `${count}:${settle_ms}:${pause_ms}`;
  if (signature !== setupSignature) {
    setupSignature = signature; $('count').value = count;
    if (![...$('settle').options].some(o => Number(o.value) === settle_ms)) $('settle').append(option(settle_ms, `${settle_ms} ms`));
    $('settle').value = settle_ms;
    if ($('pause')) {
      if (![...$('pause').options].some(o => Number(o.value) === pause_ms)) $('pause').append(option(pause_ms,`${pause_ms} ms`));
      $('pause').value = pause_ms;
    }
    if ($('release')) { $('release').checked = true; $('release').disabled = true; }
    $('number').value = value.number;
    const selected = Math.min(Number($('cal-channel').value || 0),count-1);
    $('cal-channel').replaceChildren(...Array.from({length:count},(_,channel) => option(channel, `CH ${channel} · Display ${channel+1}`)));
    $('cal-channel').value = selected;
  }
  const nextDisplaySignature = `${value.number}:${value.moving_channel}`;
  if (displaySignature !== nextDisplaySignature) {
  displaySignature = nextDisplaySignature;
  $('digits').classList.toggle('many',count>4);
  $('digits').replaceChildren(...[...value.number].map((digit,channel) => {
    const module = document.createElement('div'); module.className = `digit-module${value.moving_channel === channel ? ' moving' : ''}`;
    const window = document.createElement('div'); window.className = 'digit-window'; window.textContent = digit;
    const label = document.createElement('div'); label.className = 'digit-channel'; label.textContent = `CH ${String(channel).padStart(2,'0')}`;
    module.append(window,label); return module;
  }));
  $('digits').setAttribute('aria-label', `Requested number ${value.number}`);
  }
  const nextChannelSignature = JSON.stringify([count,positions,value.digits,value.moving_channel]);
  if (channelSignature !== nextChannelSignature) {
  channelSignature = nextChannelSignature;
  $('channels').replaceChildren(...Array.from({length:16},(_,channel) => {
    const active = channel<count, calibrated = positions[channel].filter(p=>p!==null).length;
    const card = document.createElement('div'); card.className = `channel-card${!active?' inactive':''}${value.moving_channel===channel?' moving':''}`;
    const top = document.createElement('div'); top.className = 'channel-top';
    const ch = document.createElement('span'); ch.textContent = `CH ${String(channel).padStart(2,'0')}`;
    const status = document.createElement('span'); status.className = `channel-state${calibrated===10?' ready':''}`; status.textContent = active ? `${calibrated}/10` : '—'; top.append(ch,status);
    const digit = document.createElement('div'); digit.className = 'channel-number'; digit.textContent = active ? value.digits[channel] ?? '—' : '·';
    const detail = document.createElement('small'); detail.textContent = active ? value.moving_channel === channel ? 'Moving' : 'Last commanded digit' : 'Not configured';
    card.append(top,digit,detail);
    if (active) {const button = document.createElement('button'); button.textContent = 'Calibrate'; button.addEventListener('click',()=>{setAuto(false); $('cal-channel').value=channel; calibrationRows(true); view('calibration');}); card.append(button);}
    return card;
  }));
  }
  calibrationRows();
  document.querySelectorAll('.test').forEach(button=>{button.disabled=!value.armed || value.busy;});
  $('jog').disabled = $('jog-minus').disabled = $('jog-plus').disabled = !value.armed || value.busy;
  $('save-calibration').disabled = value.busy;
  $('board-connected').textContent = value.simulated ? 'SIMULATED' : value.board.connected ? 'CONNECTED' : 'OFFLINE';
  $('board-detail').textContent = value.board.message;
  if (value.error) notice(value.error,true);
}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>view(button.dataset.view)));
$('open-calibration').addEventListener('click',()=>view('calibration'));
$('cal-channel').addEventListener('change',()=>calibrationRows(true));
$('arm').addEventListener('click',run(()=>command('/api/arm',{})));
$('cal-arm').addEventListener('click',run(()=>command('/api/arm',{})));
$('stop').addEventListener('click',run(()=>{setAuto(false);return command('/api/stop',{},'Control disabled. Enable it again when you are ready to move a display.');}));
if ($('logout')) $('logout').addEventListener('click',run(async()=>{setAuto(false);await command('/api/logout',{});window.location.assign('/login');}));
$('number-form').addEventListener('submit',event=>{event.preventDefault();setAuto(false);run(()=>command('/api/number',{number:$('number').value.trim()}))();});
document.querySelectorAll('[data-step]').forEach(button=>button.addEventListener('click',run(async()=>{setAuto(false);const result=await command('/api/step',{delta:Number(button.dataset.step)});$('number').value=result.number;})));
$('zero').addEventListener('click',run(async()=>{setAuto(false);const result=await command('/api/number',{number:'0'});$('number').value=result.number;}));
$('auto').addEventListener('click',()=>setAuto(!auto));
$('interval').addEventListener('change',()=>{if(auto)nextCount=Date.now()+Number($('interval').value);});
$('setup-form').addEventListener('submit',event=>{event.preventDefault();setAuto(false);const settings={count:Number($('count').value),settle_ms:Number($('settle').value),release_after_move:true};if($('pause'))settings.pause_ms=Number($('pause').value);run(()=>command('/api/setup',settings,'Setup saved. Outputs are stopped.'))();});
$('save-calibration').addEventListener('click',run(()=>{setAuto(false);return command('/api/calibration',{channel:Number($('cal-channel').value),positions:[...document.querySelectorAll('[data-digit]')].map(input=>input.value.trim()?Number(input.value):null)},'Digit positions saved. Outputs are stopped.');}));
function jog(delta=0){setAuto(false);const width=Number($('jog-pulse').value)+delta;$('jog-pulse').value=width;return command('/api/preview',{channel:Number($('cal-channel').value),pulse_us:width});}
$('jog').addEventListener('click',run(()=>jog()));$('jog-minus').addEventListener('click',run(()=>jog(-10)));$('jog-plus').addEventListener('click',run(()=>jog(10)));
async function checkUpdates(refresh=false){const result=await api(`/api/updates${refresh?'?refresh=1':''}`);$('installed').textContent=result.installed||'Local source';$('latest').textContent=result.latest||'—';$('update-detail').textContent=result.error|| (result.checking?'Checking GitHub…':result.available?'An update is available.':result.latest?'Counter is up to date.':'No update information yet.');$('update').disabled=!result.installable||!result.available; if(result.checking)setTimeout(()=>checkUpdates().catch(error=>notice(error.message,true)),1500);}
$('check-updates').addEventListener('click',run(()=>checkUpdates(true)));
$('update').addEventListener('click',run(async()=>{setAuto(false);$('update').disabled=true;await command('/api/updates',{});}));
$('theme').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;$('theme').setAttribute('aria-label',`Toggle ${theme==='dark'?'light':'dark'} theme`);try{localStorage.setItem('counter-theme',theme);}catch{}});
try{if(localStorage.getItem('counter-theme')==='light')document.documentElement.dataset.theme='light';}catch{}
document.addEventListener('visibilitychange',()=>{if(document.hidden)setAuto(false);if(!ticking)schedulePoll(document.hidden?pollDelay():0);});
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('stop').click();});
async function tick(){
  if(ticking)return;ticking=true;
  try{
    const result=await api('/api/state');render(result);
    if(auto&&!requestPending){
      if(result.busy)nextCount=null;
      else if(nextCount===null)nextCount=Date.now()+Number($('interval').value);
      else if(Date.now()>=nextCount){nextCount=null;const response=await command('/api/step',{delta:1});$('number').value=response.number;}
    }
  }catch(error){setAuto(false);$('connection').textContent='Disconnected';$('connection').className='status bad';$('arm').disabled=$('cal-arm').disabled=$('show-number').disabled=true;notice(error.message||'Cannot reach Counter. Counting is paused.',true);}
  finally{ticking=false;schedulePoll(pollDelay());}
}
tick();
