import {$, formatTime, label} from '../core/dom.js';
import {api} from '../core/api.js';
import {state, view} from '../core/store.js';
import {showToast} from '../ui/toast.js';

export const STATUS_INTERVAL = 3000;
export const PROGRESS_INTERVAL = 1000;

function setToggle(id, on) {
  const btn = $(id);
  btn.classList.toggle('on', on);
  btn.setAttribute('aria-pressed', String(on));
}

export function renderProgress() {
  const status = state.status;
  const duration = status.current_track?.duration;
  let elapsed = status.elapsed;
  if (elapsed != null && status.is_playing) elapsed += (Date.now() - state.statusReceivedAt) / 1000;
  if (duration) elapsed = Math.min(elapsed ?? 0, duration);
  $('elapsedTime').textContent = formatTime(elapsed);
  $('durationTime').textContent = duration ? formatTime(duration) : '0:00';
  $('progressFill').style.width = duration ? `${(elapsed / duration) * 100}%` : '0%';
}

function renderUpNext() {
  const status = state.status;
  const queue = status.queue ?? [];
  if (!queue.length) {
    $('upNextList').textContent = 'Nothing queued';
    return;
  }
  const names = queue.slice(0, 2).map(t => t.title || t.url).join('  ·  ');
  const more = status.queue_length - 2;
  $('upNextList').textContent = more > 0 ? `${names}  ·  +${more} more` : names;
}

// Shown while the first data is loading, so the page does not jump when it arrives.
export function showSkeletons() {
  $('trackList').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  $('deckGrid').innerHTML = '<div class="skeleton tile"></div><div class="skeleton tile"></div><div class="skeleton tile"></div>';
}

// Draws the player bar and the now-playing text (lists are drawn by updateStatus).
function renderStatus() {
  const s = state.status;
  $('statusDot').className = 'dot' + (s.connected ? ' connected' : '');
  $('channelInfo').textContent = s.channel_name ? '🔊 ' + s.channel_name : (s.connected ? 'Connected' : '');

  const badge = $('statusBadge');
  if (s.is_playing) {
    badge.textContent = 'Playing';
    badge.className = 'badge playing';
  } else if (s.is_paused) {
    badge.textContent = 'Paused';
    badge.className = 'badge paused';
  } else {
    badge.textContent = s.connected ? 'Idle' : 'Offline';
    badge.className = 'badge';
  }

  document.body.classList.toggle('is-playing', Boolean(s.is_playing));
  $('artEq').classList.toggle('paused', Boolean(s.is_paused));
  $('nowLabel').textContent = s.is_paused ? 'Paused' : (s.current_track ? 'Now playing' : 'Nothing playing');

  $('btnPause').style.display = s.is_paused ? 'none' : '';
  $('btnResume').style.display = s.is_paused ? '' : 'none';

  $('currentTitle').textContent = s.current_track ? (s.current_track.title || s.current_track.url || s.current_track.file) : 'Pick a track or a sound to start';
  $('currentTitle').classList.toggle('muted', !s.current_track);
  $('currentCategory').textContent = s.current_category && s.current_track ? '📁 ' + label(s.current_category) : '';

  setToggle('btnLoop', s.loop);
  setToggle('btnShuffle', s.shuffle);
  renderUpNext();
  renderProgress();
}

const playingKey = st => `${st?.current_category}|${st?.current_track?.url ?? st?.current_track?.file}|${st?.is_paused}`;

export async function updateStatus() {
  const s = await api('GET', '/status');
  if (!s) return;
  const previous = state.status;
  state.status = s;
  state.statusReceivedAt = Date.now();
  renderStatus();
  // Re-render lists only when the playing track or its state changed, so hover and focus survive polls.
  if (playingKey(previous) !== playingKey(s)) view.playlist();
  view.deck();
}

export async function simpleCommand(path, message) {
  const r = await api('POST', path);
  if (r?.ok && message) showToast(message);
  await updateStatus();
}

export async function seekBy(offset) {
  const r = await api('POST', '/seek', { offset });
  if (r && !r.ok) showToast('Nothing to seek in', false);
  await updateStatus();
}

export async function seekToClick(e) {
  const duration = state.status.current_track?.duration;
  if (!duration || !state.status.current_track) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const fraction = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  const r = await api('POST', '/seek', { position: fraction * duration });
  if (r && !r.ok) showToast('Nothing to seek in', false);
  await updateStatus();
}

export async function toggleLoop() {
  const r = await api('POST', '/loop', {loop: !state.status.loop});
  if (r) showToast(r.loop ? 'Repeat is on' : 'Repeat is off');
  await updateStatus();
}

export async function toggleShuffle() {
  const r = await api('POST', '/shuffle', {shuffle: !state.status.shuffle});
  if (r) showToast(r.shuffle ? 'Shuffle is on' : 'Shuffle is off');
  await updateStatus();
}
