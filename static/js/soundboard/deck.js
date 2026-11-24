import {$, esc, hueOf, matchesQuery, shade} from '../core/dom.js';
import {api, uploadTile} from '../core/api.js';
import {state} from '../core/store.js';
import {showToast} from '../ui/toast.js';
import {moreMenu} from '../ui/menus.js';
import {swatchRow} from '../ui/themes.js';
import {setDeckMode, showChosenFile} from '../ui/dialogs.js';
import {updateStatus} from '../player/status.js';

// Groups the user created, plus any group that a sound uses.
export function deckGroups() {
  return [...new Set([...state.deckGroupsStored, ...state.deck.map(t => t.group).filter(Boolean)])].sort((x, y) => x.localeCompare(y));
}

function tileEditor(tile) {
  const link = tile.file
    ? '<p class="hint">Uploaded files keep their file. Only the name can change.</p>'
    : `<input type="url" id="editUrl" value="${esc(tile.url)}" aria-label="YouTube link" placeholder="YouTube link" />`;
  return `
      <div class="tile-edit">
        ${swatchRow(tile.color)}
        <div class="edit-grid">
          <input type="text" id="editTitle" value="${esc(tile.title)}" aria-label="Sound name" maxlength="200" />
          <input type="text" id="editGroup" value="${esc(tile.group || '')}" list="groupList" aria-label="Group" placeholder="Group (optional)" maxlength="30" />
          ${link}
        </div>
        <div class="row">
          <button class="btn-primary btn-small" data-action="save-edit">Save</button>
          <button class="btn-ghost btn-small" data-action="cancel-edit">Cancel</button>
        </div>
      </div>`;
}

// Re-rendering only when something changed keeps open menus and typed text intact between polls.
export function renderDeck(force = false) {
  const deck = state.deck;
  const groups = deckGroups();
  const hasUngrouped = deck.some(t => !t.group);
  // Fall back to "all" when the chosen group no longer exists.
  if (!groups.length) state.deckFilter = 'all';
  else if (state.deckFilter === '' && !hasUngrouped) state.deckFilter = 'all';
  else if (state.deckFilter !== 'all' && state.deckFilter !== '' && !groups.includes(state.deckFilter)) state.deckFilter = 'all';
  const deckFilter = state.deckFilter;

  const key = JSON.stringify([state.deck, state.deckGroupsStored, state.status.sounds ?? [], state.editing, state.openMenu, state.deckFilter, state.deckQuery]);
  if (!force && key === state.deckKey) return;
  state.deckKey = key;

  const namedGroup = state.deckFilter !== 'all' && state.deckFilter !== '';
  $('deckMenu').innerHTML = moreMenu('deck:actions', [
    {action: 'open-add-sound', label: '＋ Add sounds', data: {}},
    {action: 'deck-group-new', label: '＋ New group', data: {}},
    ...(namedGroup ? [{action: 'delete-group', label: '🗑 Delete this group', danger: true, data: {group: state.deckFilter}}] : []),
  ]);


  const tabs = [{key: 'all', label: 'All'}, ...groups.map(g => ({key: g, label: g}))];
  if (groups.length && hasUngrouped) tabs.push({key: '', label: 'Other'});
  $('deckTabs').innerHTML = tabs.map(t => `
      <button class="tab ${deckFilter === t.key ? 'active' : ''}" role="tab" aria-selected="${deckFilter === t.key}" data-action="deck-filter" data-group="${esc(t.key)}">${esc(t.label)}</button>`).join('')
    + '<button class="tab tab-add" role="tab" aria-selected="false" data-action="deck-group-new" title="Add a group">+ New</button>';
  $('groupList').innerHTML = groups.map(g => `<option value="${esc(g)}"></option>`).join('');

  const playing = new Set(state.status.sounds ?? []);
  const query = state.deckQuery.trim().toLowerCase();
  const visible = deck
    .map((tile, i) => ({tile, i}))
    .filter(({tile}) => deckFilter === 'all' || (deckFilter === '' ? !tile.group : tile.group === deckFilter))
    .filter(({tile}) => matchesQuery(query, tile.title, tile.group));

  $('deckGrid').innerHTML = !deck.length
    ? '<div class="empty" style="grid-column:1/-1"><span class="icon">🥁</span>No sounds yet. Add a YouTube link or a file below.</div>'
    : !visible.length
      ? '<div class="empty" style="grid-column:1/-1"><span class="icon">🏷️</span>No sounds match here. Try another group or search.</div>'
      : visible.map(({tile, i}) => {
        const id = tile.file || tile.url;
        const on = playing.has(id);
        const eq = '<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>';
        return `
        <div class="deck-tile${on ? ' playing' : ''}" style="--hue:${hueOf(id)};${tile.color ? `--c1:${tile.color};--c2:${shade(tile.color, 0.72)}` : ''}">
          <button class="deck-play" data-action="deck-play" data-index="${i}" title="${esc(tile.title)}" aria-pressed="${on}">
            <span>${esc(tile.title)}</span>
            <span class="deck-state">${on ? eq + ' Playing' : '▶ Play'}</span>
          </button>
          ${moreMenu('tile:' + i, [
            {action: 'edit-tile', label: '🎨 Edit & colour', data: {index: i}},
            {action: 'deck-delete', label: '🗑 Remove', danger: true, data: {index: i}},
          ])}
        </div>`;
      }).join('');

  $('tileEdit').innerHTML = state.editing?.kind === 'tile' && deck[state.editing.index] ? tileEditor(deck[state.editing.index]) : '';
  if (state.editing?.kind === 'tile') $('editTitle')?.focus();
}

export async function loadDeck() {
  const [tiles, groups] = await Promise.all([api('GET', '/deck'), api('GET', '/deck/groups')]);
  if (Array.isArray(tiles)) state.deck = tiles;
  if (Array.isArray(groups)) state.deckGroupsStored = groups;
  renderDeck(true);
}

export async function setTileColor(color) {
  if (state.editing?.kind !== 'tile') return;
  const r = await api('PUT', `/deck/${state.editing.index}`, {color});
  if (r?.ok) await loadDeck();
  else if (r) showToast(r.message ?? 'Could not set the colour', false);
}

export async function saveTileEdit() {
  const tile = state.deck[state.editing.index];
  const body = {title: $('editTitle').value.trim(), group: $('editGroup').value.trim()};
  if (!tile?.file) body.url = $('editUrl').value.trim();
  const r = await api('PUT', `/deck/${state.editing.index}`, body);
  if (!r) return;
  if (!r.ok) { showToast(r.message ?? 'Could not save', false); return; }
  state.editing = null;
  showToast('Sound saved');
  await loadDeck();
}

// Sound modal: a file is uploaded, a link is sent as JSON.
export async function deckAdd() {
  const soundLabel = $('deckLabel').value.trim();
  const group = $('deckGroup').value.trim();
  let r;
  if (state.deckMode === 'file') {
    const file = $('deckFile').files[0];
    if (!file) { showToast('Choose a file first', false); $('deckDrop').focus(); return; }
    const form = new FormData();
    form.append('file', file);
    form.append('label', soundLabel);
    form.append('group', group);
    r = await uploadTile(form);
  } else {
    const urlInput = $('deckUrl');
    const url = urlInput.value.trim();
    if (!url) { showToast('Paste a YouTube link first', false); urlInput.focus(); return; }
    r = await api('POST', '/deck', {url, label: soundLabel, group});
  }
  if (r?.ok) {
    $('deckUrl').value = '';
    $('deckFile').value = '';
    showChosenFile();
    $('deckLabel').value = '';   // the group stays, so several sounds can go in one group
    showToast('Sounds added');
    $('deckAdd').close();
    await loadDeck();
  } else if (r) {
    showToast(r.message, false);
  }
}

export async function deckDelete(index) {
  const tile = state.deck[index];
  if (!confirm(`Remove "${tile?.title ?? 'this sound'}" from the soundboard?`)) return;
  const r = await api('DELETE', `/deck/${index}`);
  if (r?.ok) {
    showToast('Sound removed');
    await loadDeck();
  }
}

export async function deckPlay(index) {
  const r = await api('POST', `/deck/${index}/play`);
  if (r && !r.ok) showToast(r.message, false);
  await updateStatus();
}

// Opens the sound modal with the group you are looking at already selected.
export function openAddSound() {
  const groups = deckGroups();
  const sel = $('deckGroup');
  sel.innerHTML = '<option value="">No group</option>' + groups.map(g => `<option value="${esc(g)}">${esc(g)}</option>`).join('');
  sel.value = groups.includes(state.deckFilter) ? state.deckFilter : '';
  setDeckMode(state.deckMode);
  const d = $('deckAdd');
  if (!d.open) d.showModal();
  (state.deckMode === 'file' ? $('deckDrop') : $('deckUrl')).focus();
}

export async function addGroup() {
  const input = $('newGroupInput');
  const name = input.value.trim();
  if (!name) { input.focus(); return; }
  const r = await api('POST', '/deck/groups', {name});
  if (r?.ok) {
    input.value = '';
    $('deckGroupAdd').hidden = true;
    state.deckFilter = name;
    showToast('Group added');
    await loadDeck();
  } else if (r) {
    showToast(r.message, false);
  }
}

// Removing a group keeps its sounds; they just move to "Other".
export async function deleteGroup(name) {
  if (!confirm(`Delete the group "${name}"? Its sounds stay on the soundboard.`)) return;
  const r = await api('DELETE', '/deck/groups/' + encodeURIComponent(name));
  if (!r?.ok) return;
  if (state.deckFilter === name) state.deckFilter = 'all';
  showToast('Group deleted');
  await loadDeck();
}
