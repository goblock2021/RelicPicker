/* ═══════════════ Relic Picker v5 — Frontend ═══════════════
   Namespace structure:
     API     — backend communication (pywebview or HTTP fallback)
     State   — local state cache
     Render  — DOM updates
     Popover — shared effect/curse picker
     Actions — user interaction handlers
*/

/* ═══════════════ API ═══════════════ */
const API = {
  _mode: null, // 'pywebview' | 'http' | null (auto-detect)

  async call(method, ...args) {
    // Auto-detect mode
    if (this._mode === null) {
      if (window.pywebview && window.pywebview.api) {
        this._mode = 'pywebview';
      } else if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
        this._mode = 'http';
      } else {
        // pywebview may take a tick to inject the api — wait and retry
        await new Promise(r => setTimeout(r, 200));
        if (window.pywebview && window.pywebview.api) {
          this._mode = 'pywebview';
        } else {
          throw new Error('No backend available. Is pywebview loaded?');
        }
      }
    }

    if (this._mode === 'http') {
      const resp = await fetch('/api/call', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({method, args, kwargs: {}})
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (!data.ok) throw new Error(data.error);
      return data.data;
    }

    // pywebview mode — retry if method not yet bound (race during init)
    const api = window.pywebview.api;
    for (let attempt = 0; attempt < 5; attempt++) {
      if (typeof api[method] === 'function') {
        return await api[method](...args);
      }
      await new Promise(r => setTimeout(r, 200));
    }
    throw new Error(`API method '${method}' not available`);
  },

  async getState() { return API.call('get_state'); },
  async setShop(shop) { return API.call('set_shop', shop); },
  async setColor(color) { return API.call('set_color', color); },
  async getEffects(query) { return API.call('get_available_effects', query); },
  async getCurses(query) { return API.call('get_available_curses', query); },
  async addEffect(id) { return API.call('add_effect', id); },
  async removeEffect(idx) { return API.call('remove_effect', idx); },
  async setCurse(idx, curseId) { return API.call('set_curse', idx, curseId); },
  async toggleFav(id) { return API.call('toggle_favorite', id); },
  async setRelic(id) { return API.call('set_relic', id); },
  async apply() { return API.call('apply'); },
  async getBox() { return API.call('get_box'); },
  async addToBox() { return API.call('add_to_box'); },
  async removeFromBox(idx) { return API.call('remove_from_box', idx); },
  async importBox(text) { return API.call('import_box', text); },
  async exportBox() { return API.call('export_box'); },
  async batchApply() { return API.call('batch_apply'); },
  async roll() { return API.call('roll'); },
};

/* ═══════════════ STATE ═══════════════ */
const State = {
  shop: 'normal-old',
  color: 0,
  effects: [],
  favorites: [],
  matches: [],
  selectedRelicId: null,
  boxCount: 0,
  status: 'incomplete',
  status_message: '',
  boxItems: [],

  async refresh() {
    const s = await API.getState();
    Object.assign(this, s);
  }
};

/* ═══════════════ RENDER ═══════════════ */
const Render = {
  async all() {
    this.filters();
    this.effects();
    this.matches();
    this.relics();
    this.status();
  },

  filters() {
    document.querySelectorAll('[data-shop]').forEach(c => {
      c.classList.toggle('on', c.dataset.shop === State.shop);
    });
    document.querySelectorAll('[data-val]').forEach(c => {
      c.classList.toggle('on', parseInt(c.dataset.val) === State.color);
    });
  },

  effects() {
    const list = document.getElementById('effects-list');
    const COL = {0:'火',1:'水',2:'光',3:'幽'};
    const deep = State.shop === 'deep-old' || State.shop === 'deep-new';

    const max = 3;
    const addBtn = State.effects.length < max
      ? `<span class="add-effect" onclick="Popover.open('effect')">+ 添加效果</span>` : '';
    if (!State.effects.length) {
      list.innerHTML = addBtn;
      return;
    }
    list.innerHTML = State.effects.map((e, i) => {
      const isCursed = e.variant === 'cursed-strong';
      const isClean = e.variant === 'cursed-weak';
      let tag = '', chipCls = '';
      if (deep && isCursed) { tag = '<span class="curse-tag cursed">强·需诅咒</span>'; chipCls = ' cursed'; }
      else if (deep && isClean) { tag = '<span class="curse-tag clean">弱·无诅咒</span>'; chipCls = ' clean'; }

      let cursePicker = '';
      if (isCursed) {
        const filled = e.curse_id ? ' filled' : '';
        const label = e.curse_name ? '诅咒: ' + e.curse_name : '选诅咒 ▸';
        cursePicker = `<span class="curse-picker${filled}" onclick="Popover.open('curse', ${i})">${label}</span>`;
      }

      const favCls = State.favorites.includes(e.eff_id) ? ' fav' : '';
      const invalidMark = e.shop_valid === false
        ? '<span style="color:var(--red);font-size:10px;margin-left:2px">⚠</span>'
        : '';
      const invalidClass = e.shop_valid === false ? ' invalid' : '';
      return `<div class="effect-row">
        <span class="effect-chip${chipCls}${invalidClass}" onclick="Popover.open('effect', null, ${i})">
          <svg class="ec-star${favCls}" viewBox="0 0 24 24" width="12" height="12" onclick="event.stopPropagation();Actions.toggleFav(${e.eff_id})"><use href="#icon-star${e.fav?'-filled':''}"/></svg>
          <span class="ec-name">${e.name}</span>${e.dlc_only ? '<span class="pi-tag dlc" style="margin:0 2px">DLC</span>' : ''}${invalidMark}<span style="font-size:9px;color:var(--faint);margin-left:4px">#${e.eff_id} P${e.pool_id}</span>${tag}
          <span class="ec-remove" onclick="event.stopPropagation();Actions.removeEffect(${i})">×</span>
        </span>
        ${cursePicker}
      </div>`;
    }).join('') + addBtn;
  },

  matches() {
    const el = document.getElementById('match-count');
    el.textContent = State.matches.length;
    // Show selected relic name in the match line
    const info = document.getElementById('match-info');
    if (info) {
      if (State.selected_relic_id && State.matches.length) {
        const sel = State.matches.find(m => m.relic_id === State.selected_relic_id);
        if (sel) {
          info.textContent = '— 已选: [' + sel.relic_id + '] ' + sel.relic_name;
          info.style.color = 'var(--green)';
        } else {
          info.textContent = '';
        }
      } else {
        info.textContent = '';
      }
    }
  },

  relics() {
    const list = document.getElementById('relic-list');
    const COL = {0:'火',1:'水',2:'光',3:'幽'};
    const COL_CLS = {0:'fire',1:'water',2:'light',3:'green'};

    if (!State.matches.length) {
      list.innerHTML = '<span class="add-effect" style="opacity:0.5">添加效果后将显示匹配遗物</span>';
      return;
    }

    list.innerHTML = State.matches.map(m => {
      const sel = m.relic_id === State.selected_relic_id ? ' selected' : '';
      const colName = COL[m.color] || '?';
      const colCls = COL_CLS[m.color] || '';
      return `<div class="relic-item${sel}" data-id="${m.relic_id}" onclick="Actions.selectRelic(${m.relic_id})">
        <span class="color-dot dot-${m.color >= 0 ? m.color : 2}"></span>
        <span class="ri-name">[${m.relic_id}] ${m.relic_name}</span>
        <span class="ri-color chip ${colCls}" style="font-size:10px;padding:1px 6px">${colName}</span>
        <span class="ri-slots" title="效果槽/诅咒槽">${m.pool_count}槽${m.curse_count ? ' +' + m.curse_count + '诅咒' : ''}</span>
        ${sel ? '<span style="color:var(--green);font-size:11px;margin-left:4px">✓</span>' : ''}
      </div>`;
    }).join('');
  },

  status() {
    const el = document.getElementById('status-text');
    const btnRoll = document.getElementById('btn-roll');
    const btnBox = document.getElementById('btn-box');
    const btnApply = document.getElementById('btn-apply');
    const disc = document.getElementById('disc-banner');

    if (!State.connected) {
      disc.classList.remove('hidden');
      el.textContent = '请先连接 Smithbox 并加载项目';
      el.className = 'status error';
      btnRoll.disabled = true;
      btnBox.disabled = true;
      btnApply.disabled = true;
    } else {
      disc.classList.add('hidden');
      const stats = [];
      if (State.loaded_relics) stats.push(State.loaded_relics + ' 遗物');
      if (State.loaded_effects) stats.push(State.loaded_effects + ' 效果');
      const statText = stats.length ? ' (' + stats.join(', ') + ')' : '';
      el.textContent = (State.status_message || (State.status === 'ready' ? '✓ 就绪' : '添加效果以开始')) + statText;
      el.className = 'status ' + State.status;
      btnRoll.disabled = false;
      btnBox.disabled = State.status !== 'ready';
      btnApply.disabled = State.status !== 'ready';
    }

    document.getElementById('box-badge').textContent = State.box_count;
  },

  boxLoading() {
    const list = document.getElementById('drawer-list');
    list.innerHTML = `<div class="box-loading">
      <div class="startup-spinner" style="margin:0 auto 16px"></div>
      <div style="font-size:13px;color:var(--muted)">加载中...</div>
    </div>`;
  },

  box() {
    const list = document.getElementById('drawer-list');
    document.getElementById('drawer-count').textContent = String(State.boxItems.length);

    if (!State.boxItems.length) {
      list.innerHTML = '<div class="box-empty">遗物盒是空的</div>';
      return;
    }

    const q = (document.getElementById('box-search')?.value || '').toLowerCase();
    const COL = ['火','水','光','幽'];
    const SHOP_NAMES = {
      'normal-old':'旧版普通','normal-new':'新版普通',
      'deep-old':'旧版深夜','deep-new':'新版深夜'
    };

    // Group items by shop
    const groups = {};
    for (let i = 0; i < State.boxItems.length; i++) {
      const b = State.boxItems[i];
      // Search filter
      if (q) {
        const text = (b.effect_names||[]).join(' ') + (b.shop||'') + SHOP_NAMES[b.shop];
        if (!text.toLowerCase().includes(q)) continue;
      }
      const key = b.shop || 'unknown';
      if (!groups[key]) groups[key] = [];
      groups[key].push({...b, _idx: i});
    }

    const sel = new Set(window._boxSel || []);

    let html = '';
    for (const [shop, items] of Object.entries(groups)) {
      html += `<div class="box-group">
        <div class="box-group-header" onclick="this.parentElement.classList.toggle('collapsed')">
          <span class="bgh-caret">▾</span>
          ${SHOP_NAMES[shop]||shop}
          <span class="bgh-count">${items.length}</span>
        </div>
        <div class="box-group-items">`;
      for (const b of items) {
        const effectLines = (b.effect_names||[]).map(n => `<div class="bi-line">${n}</div>`).join('');
        const curseNames = b.curse_names || [];
        const curseLines = curseNames.map(n => `<div class="bi-line curse">诅咒: ${n}</div>`).join('');
        const selCls = sel.has(b._idx) ? ' selected' : '';
        html += `<div class="box-item${selCls}" data-idx="${b._idx}"
                 onclick="Render.toggleBoxSel(${b._idx})"
                 ondblclick="Render.loadBoxItem(${b._idx}, event)">
          <span class="bi-dot"><span class="color-dot dot-${b.color>=0?b.color:2}"></span></span>
          <div class="bi-main">
            ${b.relic_name ? `<div class="bi-line" style="color:var(--gold)">[${b.relic_id}] ${b.relic_name}${b.relic_shop === false ? ' <span class="bi-tag tag-not-for-sale">非卖</span>' : ''}${b.is_illegal ? ' <span class="bi-tag tag-illegal">非法</span>' : ''}</div>` : ''}
            <div class="bi-effects">${effectLines||'<div class="bi-line">(空)</div>'}</div>
            ${curseLines ? `<div class="bi-curses">${curseLines}</div>` : ''}
            <div class="bi-date">${b.added_at}</div>
          </div>
          <button class="bi-del" onclick="event.stopPropagation();Actions.removeFromBox(${b._idx})">
            <svg viewBox="0 0 24 24" width="12" height="12"><use href="#icon-close"/></svg>
          </button>
        </div>`;
      }
      html += `</div></div>`;
    }
    list.innerHTML = html || '<div class="box-empty">没有匹配的遗物</div>';
  },

  toggleBoxSel(idx) {
    if (!window._boxSel) window._boxSel = [];
    const i = window._boxSel.indexOf(idx);
    if (i >= 0) window._boxSel.splice(i, 1);
    else window._boxSel.push(idx);
    this.box();
  },

  async loadBoxItem(idx, event) {
    window._boxSel = [];
    const b = State.boxItems[idx];
    if (!b) return;
    // Switch shop
    const chip = document.querySelector(`[data-shop="${b.shop}"]`);
    if (chip) await Actions.setShop(chip);
    // Clear current effects and load from box
    const saved = State.effects;
    for (let i = saved.length - 1; i >= 0; i--) await API.removeEffect(i);
    for (const e of b.effects) {
      await API.addEffect(e.eff_id);
      await State.refresh();
      if (e.curse_id) {
        const lastIdx = State.effects.length - 1;
        await API.setCurse(lastIdx, e.curse_id);
        await State.refresh();
      }
    }
    // Restore relic selection
    if (b.relic_id) {
      await API.setRelic(b.relic_id);
    }
    await State.refresh();
    Render.all();
    Actions.toggleBox();
    Toast.show('已加载配置');
  },
};

/* ═══════════════ POPOVER ═══════════════ */
const Popover = {
  _mode: 'effect',   // 'effect' | 'curse'
  _curseIdx: null,   // effect index for curse picker

  get mask() { return document.getElementById('pop-mask'); },
  get pop() { return document.getElementById('pop'); },
  get search() { return document.getElementById('pop-search'); },
  get list() { return document.getElementById('pop-list'); },

  async open(mode, curseIdx, replaceIdx) {
    this._mode = mode;
    this._curseIdx = curseIdx ?? null;
    this._replaceIdx = replaceIdx ?? null;
    this.mask.classList.add('on');
    this.pop.classList.add('on');

    // Position
    const card = document.getElementById('effects-card');
    const r = card.getBoundingClientRect();
    this.pop.style.left = Math.min(r.left + 20, window.innerWidth - 400) + 'px';
    this.pop.style.top = Math.min(r.bottom + 8, window.innerHeight - 380) + 'px';

    this.search.value = '';
    this.search.placeholder = mode === 'curse' ? '搜索诅咒...' : '搜索效果...';
    await this.renderList();
    setTimeout(() => this.search.focus(), 50);
  },

  close() {
    this.mask.classList.remove('on');
    this.pop.classList.remove('on');
    this._curseIdx = null;
    this._replaceIdx = null;
  },

  onSearch() { this.renderList(); },

  async renderList() {
    const q = this.search.value.toLowerCase();

    if (this._mode === 'curse') {
      const items = await API.getCurses(q);
      const usedIds = new Set(State.effects.filter((_, i) => i !== this._curseIdx).map(e => e.curse_id).filter(Boolean));
      this.list.innerHTML =
        `<div class="pop-section">选择诅咒</div>` +
        items.map(c => {
          const picked = State.effects[this._curseIdx]?.curse_id === c.id;
          const used = usedIds.has(c.id);
          let cls = 'pop-item'; if (picked) cls += ' picked';
          const attr = (used && !picked) ? '' : ` onclick="Popover.pickCurse(${c.id})"`;
          return `<div class="${cls}"${attr}>
            <span class="pi-name">${c.name}</span>
            ${used && !picked ? '<span class="pi-tag conflict">已用</span>' : ''}
            ${picked ? '<span style="font-size:10px;color:var(--green)">✓</span>' : ''}
          </div>`;
        }).join('');
      return;
    }

    // Effect mode
    const items = await API.getEffects(q);
    const usedCompats = new Set(
      State.effects.filter((_, i) => i !== Popover._replaceIdx)
        .map(e => e.compat_id).filter(Boolean)
    );

    // Group by variant
    const favs = items.filter(e => e.is_fav);
    const strong = items.filter(e => e.variant === 'cursed-strong' && !e.is_fav);
    const weak = items.filter(e => e.variant === 'cursed-weak' && !e.is_fav);
    const normal = items.filter(e => e.variant === 'normal' && !e.is_fav);

    const renderItem = (e, tag, tagCls) => {
      const picked = State.effects.some(x => x.eff_id === e.id);
      const conflict = !picked && usedCompats.has(e.compat_id);
      const fav = State.favorites.includes(e.id) ? ' fav' : '';
      let cls = 'pop-item'; if (picked) cls += ' picked';
      const attr = (conflict || picked) ? '' : ` onclick="Popover.pickEffect(${e.id})"`;
      return `<div class="${cls}"${attr}>
        <svg class="pi-star${fav}" viewBox="0 0 24 24" width="12" height="12" onclick="event.stopPropagation();Popover.toggleFav(${e.id})"><use href="#icon-star${e.is_fav?'-filled':''}"/></svg>
        <span class="pi-name">${e.name}</span>
        ${e.dlc_only ? '<span class="pi-tag dlc">DLC</span>' : ''}
        <span style="font-size:10px;color:var(--faint);flex-shrink:0">#${e.id} P${e.pool_id}</span>
        ${tag ? `<span class="pi-tag ${tagCls}">${tag}</span>` : ''}
        ${conflict ? '<span class="pi-tag conflict">冲突</span>' : ''}
        ${picked ? '<span style="font-size:10px;color:var(--green)">✓</span>' : ''}
      </div>`;
    };

    const makeSection = (title, arr, tag, tagCls) => {
      if (!arr.length) return '';
      return `<div class="pop-section">${title}</div>` +
        arr.sort((a, b) => a.name.localeCompare(b.name)).map(e => renderItem(e, tag, tagCls)).join('');
    };

    // Render: favorites first, then variant groups
    const deep = State.shop === 'deep-old' || State.shop === 'deep-new';
    let html = '';
    html += makeSection('★ 收藏', favs, '', '');
    if (deep) {
      html += makeSection('强效 (需诅咒)', strong, '强·需诅咒', 'strong');
      html += makeSection('弱效 (无诅咒)', weak, '弱·无诅咒', 'weak');
    }
    html += makeSection('普通效果', normal, '', '');

    this.list.innerHTML = html || '<div style="padding:20px;text-align:center;color:var(--faint)">没有匹配的效果</div>';
  },

  async pickEffect(id) {
    if (this._replaceIdx !== null) {
      await API.removeEffect(this._replaceIdx);
    }
    await API.addEffect(id);
    await State.refresh();
    Render.all();
    this.close();
  },

  async pickCurse(id) {
    await API.setCurse(this._curseIdx, id);
    await State.refresh();
    Render.all();
    this.close();
  },

  async toggleFav(id) {
    await API.toggleFav(id);
    await State.refresh();
    Render.effects();
    this.renderList();
  },
};

/* ═══════════════ ACTIONS ═══════════════ */
const Actions = {
  async setShop(el) {
    document.querySelectorAll('[data-shop]').forEach(c => c.classList.remove('on'));
    el.classList.add('on');
    const shop = el.dataset.shop;
    await API.setShop(shop);
    await State.refresh();
    Render.all();
  },

  async setColor(el) {
    document.querySelectorAll('[data-val]').forEach(c => c.classList.remove('on'));
    el.classList.add('on');
    const color = parseInt(el.dataset.val);
    await API.setColor(color);
    await State.refresh();
    Render.all();
  },

  async selectRelic(id) {
    await API.setRelic(id);
    await State.refresh();
    Render.relics();
  },

  async removeEffect(idx) {
    await API.removeEffect(idx);
    await State.refresh();
    Render.all();
  },

  async toggleFav(id) {
    await API.toggleFav(id);
    await State.refresh();
    Render.effects();
  },

  async roll() {
    const state = await API.roll();
    if (state.success === false) {
      Toast.show(state.message || '抽取失败');
      return;
    }
    Object.assign(State, state);
    Render.all();
    Toast.show(`抽到了 ${State.matches[0]?.relic_name || '?'}`);
  },

  async addToBox() {
    const result = await API.addToBox();
    await State.refresh();
    Render.all();
    Toast.show(result.message || '已加入遗物盒');
  },

  async apply() {
    let preview;
    try { preview = await API.call('preview'); }
    catch (e) { Toast.show('预览失败: ' + e.message); return; }
    if (!preview.success) { Toast.show(preview.message); return; }

    const COL = ['','火','水','光','幽'];
    let html = `<div class="cm-title">应用修改</div>`;
    html += `<div class="cm-section">遗物</div>`;
    html += `<div class="cm-row">[${preview.relic_id}] ${preview.relic_name}  ${COL[preview.color]||''}</div>`;
    html += `<div class="cm-section">修改内容</div>`;
    for (const n of preview.path_nodes) {
      html += `<div class="cm-row">表 ${n.table_id} → itemId=${n.item_id}</div>`;
    }
    for (const m of preview.pool_mods) {
      html += `<div class="cm-row">Pool[${m.pool_id}] ${m.effect_name}</div>`;
      if (m.curse_name) html += `<div class="cm-row" style="padding-left:16px">诅咒: ${m.curse_name}</div>`;
    }
    html += `<div class="confirm-actions">
      <button class="confirm-cancel" onclick="Modal.cancel()">取消</button>
      <button class="confirm-ok" onclick="Actions.doApply()">应用</button>
    </div>`;
    window._applyPreview = preview;
    Modal.showRaw(html);
  },

  async doApply() {
    const body = document.getElementById('confirm-body');
    body.innerHTML = `<div style="text-align:center;padding:20px">
      <div class="startup-spinner" style="margin:0 auto 12px"></div>
      <div style="font-size:13px;color:var(--muted)">正在应用...</div>
    </div>`;

    try {
      const result = await API.apply();
      if (result.success) {
        body.innerHTML = `<div style="text-align:center;padding:20px">
          <svg viewBox="0 0 24 24" width="32" height="32" style="color:var(--green);margin-bottom:8px"><use href="#icon-check"/></svg>
          <div style="font-size:13px;color:var(--green);font-weight:600">${result.message}</div>
          <div class="confirm-actions" style="justify-content:center;margin-top:12px">
            <button class="confirm-cancel" onclick="Modal.cancel()">关闭</button>
          </div>
        </div>`;
      } else {
        body.innerHTML = `<div style="text-align:center;padding:20px">
          <svg viewBox="0 0 24 24" width="32" height="32" style="color:var(--red);margin-bottom:8px"><use href="#icon-close"/></svg>
          <div style="font-size:13px;color:var(--red);font-weight:600">应用失败</div>
          <div style="font-size:11px;color:var(--muted);margin-top:4px">${result.message}</div>
          <div class="confirm-actions" style="justify-content:center;margin-top:12px">
            <button class="confirm-cancel" onclick="Modal.cancel()">关闭</button>
          </div>
        </div>`;
      }
    } catch (e) {
      body.innerHTML = `<div style="text-align:center;padding:20px">
        <svg viewBox="0 0 24 24" width="32" height="32" style="color:var(--red);margin-bottom:8px"><use href="#icon-close"/></svg>
        <div style="font-size:13px;color:var(--red);font-weight:600">应用失败</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px">${e.message}</div>
        <div class="confirm-actions" style="justify-content:center;margin-top:12px">
          <button class="confirm-cancel" onclick="Modal.cancel()">关闭</button>
        </div>
      </div>`;
    }
    await State.refresh();
    Render.all();
  },

  async reconnect() {
    Toast.show('正在连接 Smithbox...');
    try {
      const result = await API.call('reconnect');
      if (result.success) {
        Toast.show('已连接 ✓');
      } else {
        Toast.show('连接失败: ' + result.message);
      }
    } catch (e) {
      Toast.show('连接失败: ' + e.message);
    }
    await State.refresh();
    Render.all();
  },

  async toggleTheme() {
    const isLight = document.documentElement.classList.toggle('light');
    document.getElementById('theme-icon').innerHTML = isLight
      ? '<use href="#icon-sun"/>'
      : '<use href="#icon-moon"/>';
    try { await API.call('save_settings', {theme: isLight ? 'light' : 'dark'}); } catch (e) {}
  },

  async toggleBox() {
    const overlay = document.getElementById('box-overlay');
    if (!overlay.classList.contains('on')) {
      // Open drawer immediately with loading state
      overlay.classList.add('on');
      Render.boxLoading();
      // Load data async, then render
      State.boxItems = await API.getBox();
      Render.box();
    } else {
      overlay.classList.remove('on');
    }
  },

  async removeFromBox(idx) {
    await API.removeFromBox(idx);
    State.boxItems = await API.getBox();
    Render.box();
  },

  openSmithboxUrl() {
    try { API.call('open_url', 'https://github.com/vawser/Smithbox/releases/latest'); } catch (e) {}
  },

  _updateUrl: '',

  async checkUpdate() {
    try {
      const info = await API.call('check_update');
      if (info && info.has_update) {
        this._updateUrl = info.download_url || info.url;
        document.getElementById('update-text').textContent =
          `发现新版本 ${info.version}`;
        document.getElementById('update-banner').classList.remove('hidden');
      }
    } catch (e) { /* silent */ }
  },

  openUpdateUrl() {
    if (this._updateUrl) {
      try { API.call('open_url', this._updateUrl); } catch (e) {}
    }
  },

  dismissUpdate() {
    document.getElementById('update-banner').classList.add('hidden');
  },

  openHelp() {
    document.getElementById('help-overlay').classList.add('on');
  },

  closeHelp() {
    document.getElementById('help-overlay').classList.remove('on');
  },

  zoomImage(img) {
    const src = img.getAttribute('src');
    document.getElementById('img-viewer-img').src = src;
    document.getElementById('img-viewer').classList.add('on');
  },

  closeImage() {
    document.getElementById('img-viewer').classList.remove('on');
  },

  async selectAllBox() {
    window._boxSel = State.boxItems.map((_, i) => i);
    Render.box();
  },

  async deleteSelectedBox() {
    const sel = window._boxSel || [];
    if (!sel.length) { Toast.show('请先单击选中要删除的条目'); return; }
    sel.sort((a, b) => b - a);
    for (const idx of sel) await API.removeFromBox(idx);
    window._boxSel = [];
    State.boxItems = await API.getBox();
    Render.box();
    Toast.show(`已删除 ${sel.length} 个`);
  },

  importBox() {
    Modal.showRaw(`<div class="cm-title">导入遗物盒</div>
      <div class="import-options">
        <div class="import-opt" onclick="Actions.importFromFile();Modal.cancel()">
          <div class="io-icon"><svg viewBox="0 0 24 24" width="20" height="20"><use href="#icon-upload"/></svg></div>
          <div class="io-text">
            <div class="io-label">打开文件</div>
            <div class="io-desc">从 .txt 文件导入</div>
          </div>
        </div>
        <div class="import-opt" onclick="Actions.importFromClipboard();Modal.cancel()">
          <div class="io-icon"><svg viewBox="0 0 24 24" width="20" height="20"><use href="#icon-box"/></svg></div>
          <div class="io-text">
            <div class="io-label">从剪贴板读取</div>
            <div class="io-desc">读取已复制的文本</div>
          </div>
        </div>
      </div>`);
  },

  async importFromFile() {
    const result = await API.call('open_box_file');
    if (!result.ok) { if (result.error) Toast.show(result.error); return; }
    Toast.show('正在导入...', 0);
    await new Promise(r => setTimeout(r, 50));
    const r = await API.importBox(result.text);
    State.boxItems = await API.getBox();
    Render.box();
    Toast.show(r.message || '已导入');
  },

  async importFromClipboard() {
    const result = await API.call('read_clipboard');
    if (!result.ok) { Toast.show(result.error || '剪贴板读取失败'); return; }
    Toast.show('正在导入...', 0);
    await new Promise(r => setTimeout(r, 50));
    const r = await API.importBox(result.text);
    State.boxItems = await API.getBox();
    Render.box();
    Toast.show(r.message || '已从剪贴板导入');
  },

  async exportBox() {
    const result = await API.exportBox();
    const text = result.text || '';
    if (!text) { Toast.show('没有可导出的内容'); return; }
    window._exportText = text;
    const preview = text.length > 600 ? text.slice(0, 600) + '\n\n...' : text;
    const count = text.split('\n').filter(l => !l.startsWith('#') && l.includes(':')).length;
    const html = `<div class="cm-title">导出遗物盒</div>
      <div class="export-preview">${preview}</div>
      <div style="font-size:11px;color:var(--faint);margin-bottom:8px">共 ${count} 条数据</div>
      <div class="confirm-actions" style="gap:8px">
        <button class="confirm-cancel" onclick="Modal.cancel()">关闭</button>
        <button class="confirm-cancel" onclick="Actions.copyExportText()">复制到剪贴板</button>
        <button class="confirm-ok" onclick="Actions.saveBoxToFile();Modal.cancel()">保存到文件</button>
      </div>`;
    Modal.showRaw(html);
  },

  copyExportText() {
    const text = window._exportText || '';
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    Toast.show('已复制到剪贴板');
  },

  async saveBoxToFile() {
    const result = await API.exportBox();
    const saved = await API.call('save_box_to_file', result.text || '');
    Toast.show(saved.message || (saved.saved ? '已保存' : '已取消'));
  },

  async batchApply() {
    const sel = window._boxSel || [];
    const indices = sel.length ? sel : State.boxItems.map((_, i) => i);
    if (!indices.length) { Toast.show('没有可应用的条目'); return; }

    window._bpIndices = indices;
    window._bpCursor = 0;
    window._bpResults = [];
    Actions.showBatchStep();
  },

  showBatchStep() {
    const indices = window._bpIndices;
    const cursor = window._bpCursor;
    const total = indices.length;
    if (cursor >= total) {
      const results = window._bpResults;
      const ok = results.filter(r => r.ok).length;
      Modal.showRaw(`<div class="cm-title">批量应用 — 完成</div>
        <div class="batch-progress-wrap">
          ${indices.map((idx, i) => {
            const b = State.boxItems[idx];
            const r = results[i];
            const st = r ? (r.ok ? '✓' : '✗') : '?';
            const cl = r ? (r.ok ? 'var(--green)' : 'var(--red)') : 'var(--faint)';
            return `<div class="bp-row"><span class="bp-status" style="color:${cl}">${st}</span>
              <span class="bp-name">${(b.effect_names||[]).join(' / ')||'(空)'}</span>
              <span class="bp-shop">${r?.shop || ''}</span></div>`;
          }).join('')}
        </div>
        <div class="bp-summary" style="margin-top:8px">
          <span style="color:${ok===total?'var(--green)':'var(--red)'}">完成: ${ok}/${total}</span>
        </div>
        <div class="confirm-actions" style="margin-top:8px">
          <button class="confirm-cancel" onclick="Modal.cancel()">关闭</button>
        </div>`);
      return;
    }

    const idx = indices[cursor];
    const b = State.boxItems[idx];
    // Auto-apply on first visit
    const operating = !window._bpResults[cursor];
    if (operating) {
      Actions.doBatchApplyCurrent();
    }
    const r = window._bpResults[cursor];
    const applied = !!r;

    const SHOP_NAMES = {'normal-old':'旧版普通','normal-new':'新版普通','deep-old':'旧版深夜','deep-new':'新版深夜'};
    const shopName = SHOP_NAMES[b.shop] || b.shop || '';

    // Status bar
    let statusText, statusBg;
    if (!applied) {
      statusText = '应用中...'; statusBg = 'var(--blue-bg)';
    } else if (r.ok) {
      statusText = '✓ 成功'; statusBg = 'var(--green-bg)';
    } else {
      statusText = '✗ 失败'; statusBg = 'var(--red-bg)';
    }

    const errMsg = applied && !r.ok ? `<div style="font-size:10px;color:var(--red);margin-top:4px">${r.error||'未知错误'}</div>` : '';
    const relicName = applied && r.ok ? `<div style="font-size:11px;color:var(--green)">已应用: ${r.name}</div>` : '';

    const busy = !applied;

    Modal.showRaw(`<div class="cm-title">批量应用 — 第 ${cursor + 1}/${total} 个</div>
      <div style="font-size:14px;font-weight:600;color:var(--gold);margin:8px 0 4px">${shopName}</div>
      <div class="bp-cur-item">
        <div class="bi-main" style="flex:1">
          <div class="bi-effects">${(b.effect_names||[]).map(n=>`<div class="bi-line">· ${n}</div>`).join('')}</div>
          ${(b.curse_names||[]).length ? `<div class="bi-curses">${b.curse_names.map(n=>`<div class="bi-line" style="color:var(--red)">诅咒: ${n}</div>`).join('')}</div>` : ''}
          ${relicName}
          ${errMsg}
        </div>
      </div>
      <div style="margin:8px 0;padding:6px 12px;border-radius:6px;background:${statusBg};font-size:12px;font-weight:600;text-align:center">${statusText}</div>
      <div class="bp-progress">
        <div class="bp-bar"><div class="bp-fill" style="width:${(cursor/total)*100}%"></div></div>
      </div>
      <div class="confirm-actions" style="margin-top:8px">
        <button class="confirm-cancel" onclick="Modal.cancel()">关闭</button>
        <button class="confirm-cancel" onclick="Actions.batchPrev()" ${cursor===0||busy?'disabled':''}>上一个</button>
        <button class="confirm-ok" onclick="Actions.batchNext()" ${busy?'disabled':''}>去游戏购买 → 下一个</button>
      </div>`);
  },

  async doBatchApplyCurrent() {
    const idx = window._bpIndices[window._bpCursor];
    const result = await API.call('batch_apply', [idx]);
    if (result.results && result.results.length) {
      window._bpResults[window._bpCursor] = result.results[0];
    } else {
      window._bpResults[window._bpCursor] = {
        ok: false, name: '?',
        error: result.message || '未知错误'
      };
    }
    Actions.showBatchStep();
  },

  batchNext() {
    window._bpCursor++;
    Actions.showBatchStep();
  },

  batchPrev() {
    if (window._bpCursor > 0) window._bpCursor--;
    Actions.showBatchStep();
  },
};

/* ═══════════════ MODAL ═══════════════ */
const Modal = {
  _resolve: null,

  show(html) {
    return new Promise(resolve => {
      this._resolve = resolve;
      document.getElementById('confirm-body').innerHTML =
        html + `<div class="confirm-actions">
          <button class="confirm-cancel" onclick="Modal.cancel()">取消</button>
          <button class="confirm-ok" onclick="Modal.ok()">应用</button>
        </div>`;
      document.getElementById('confirm-overlay').classList.add('on');
    });
  },

  showRaw(html) {
    document.getElementById('confirm-body').innerHTML = html;
    document.getElementById('confirm-overlay').classList.add('on');
  },

  ok() {
    document.getElementById('confirm-overlay').classList.remove('on');
    if (this._resolve) { this._resolve(true); this._resolve = null; }
  },

  cancel() {
    document.getElementById('confirm-overlay').classList.remove('on');
    if (this._resolve) { this._resolve(false); this._resolve = null; }
  },
};

/* ═══════════════ TOAST ═══════════════ */
const Toast = {
  show(msg, duration) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('on');
    clearTimeout(this._tid);
    if (duration !== 0) {
      this._tid = setTimeout(() => el.classList.remove('on'), duration || 1800);
    }
  }
};

/* ═══════════════ KEYBOARD ═══════════════ */
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { Modal.cancel(); }
  if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
    const overlay = document.getElementById('box-overlay');
    if (overlay.classList.contains('on')) { e.preventDefault(); Actions.selectAllBox(); }
  }
});

/* ═══════════════ INIT ═══════════════ */
(async function applyTheme() {
  try {
    const settings = await API.call('get_settings');
    const isLight = settings.theme === 'light';
    if (isLight) document.documentElement.classList.add('light');
    document.getElementById('theme-icon').innerHTML = isLight
      ? '<use href="#icon-sun"/>'
      : '<use href="#icon-moon"/>';
  } catch (e) { /* ignore */ }
})();

/* ═══════════════ TOUR ═══════════════ */
const Tour = {
  steps: [
    {
      target: null,
      title: '欢迎使用 Relic Picker',
      desc: '这是艾尔登法环遗物定向选择工具。通过选择想要的词条效果，快速筛选并应用遗物到游戏中。接下来将逐步介绍主要功能。',
    },
    {
      target: '#filters',
      title: '选择遗物购买选项与顔色',
      desc: '在游戏商人处有4种遗物购买选项，普通/深夜各有两个。在购买选项右侧的描述中有“※可获得至游戏版本1.02 相同内容的遗物”的为老版本，没有则对应新版本。 在下方可自定义遗物的顔色（火燃、水滴、光耀、幽静）。',
    },
    {
      target: '#effects-card',
      title: '添加想要的效果',
      desc: '点击「添加效果」按钮，从列表中选择你想要的词条。点击词条左侧的星星图标可以将其加入收藏，收藏的词条总是在最上方显示。',
    },
    {
      target: '#relic-card',
      title: '浏览匹配遗物',
      desc: '所有匹配的遗物会列在这里。点击某个遗物即可选中它，然后进行后续操作。 通常在词条确定时，总是有3个遗物符合条件，这会影响遗物的外观。',
    },
    {
      target: '#action-bar',
      title: '随机抽取 / 加入遗物盒 / 应用',
      desc: '点击🎲可以随机抽取一个效果完全随机的遗物、点击📦加入遗物盒保存、点击✅将修改应用到游戏中。',
    },
    {
      target: '#topbar .box-btn',
      title: '遗物盒 — 你的收藏库',
      desc: '点击“加入遗物盒”后，遗物会被存放到遗物盒里，支持搜索、导出、批量应用。用这个功能和你的伙伴分享遗物方案！',
    },
  ],
  _idx: 0,
  _active: false,

  start() {
    if (this._active) return;
    this._idx = 0;
    this._active = true;
    document.getElementById('tour-overlay').classList.add('on');
    this._render();
  },

  _render() {
    const s = this.steps[this._idx];
    const el = s.target ? document.querySelector(s.target) : null;
    if (s.target && !el) { this.skip(); return; }

    // Spotlight — bring element above overlay and highlight
    this._clearHighlight();
    if (el) {
      el.classList.add('tour-highlight');
    }

    // Step label
    document.getElementById('tour-step-label').textContent = `${this._idx + 1} / ${this.steps.length}`;
    document.getElementById('tour-title').textContent = s.title;
    document.getElementById('tour-desc').textContent = s.desc;

    // Dots
    document.getElementById('tour-dots').innerHTML = this.steps
      .map((_, i) => `<div class="dot${i === this._idx ? ' active' : ''}"></div>`)
      .join('');

    // Buttons
    const isLast = this._idx === this.steps.length - 1;
    document.getElementById('tour-prev').style.display = this._idx === 0 ? 'none' : 'inline-block';
    document.getElementById('tour-next').style.display = isLast ? 'none' : 'inline-block';
    document.getElementById('tour-finish').style.display = isLast ? 'inline-block' : 'none';

    // Position tip near target (or center if no target)
    this._positionTip(el);
  },

  _positionTip(el) {
    const tip = document.getElementById('tour-tip');
    const tipW = 300;
    const tipH = tip.offsetHeight || 220;
    const gap = 14;

    // No target — center on screen
    if (!el) {
      tip.style.top = Math.max(20, (window.innerHeight - tipH) / 2) + 'px';
      tip.style.left = Math.max(12, (window.innerWidth - tipW) / 2) + 'px';
      tip.style.bottom = 'auto';
      return;
    }

    const r = el.getBoundingClientRect();

    // Left: center tip relative to target, clamp to screen
    const left = Math.max(12, Math.min(window.innerWidth - tipW - 12, r.left + r.width / 2 - tipW / 2));

    // Prefer above. If not enough room, go below.
    const spaceAbove = r.top - gap;
    if (spaceAbove >= tipH + 20) {
      tip.style.bottom = (window.innerHeight - r.top + gap) + 'px';
      tip.style.top = 'auto';
    } else {
      const belowTop = Math.min(r.bottom + gap, window.innerHeight - tipH - 12);
      tip.style.top = belowTop + 'px';
      tip.style.bottom = 'auto';
    }

    tip.style.left = left + 'px';
  },

  _clearHighlight() {
    document.querySelectorAll('.tour-highlight').forEach(el => {
      el.classList.remove('tour-highlight');
    });
  },

  next() {
    if (this._idx >= this.steps.length - 1) return;
    this._clearHighlight();
    this._idx++;
    this._render();
  },

  prev() {
    if (this._idx <= 0) return;
    this._clearHighlight();
    this._idx--;
    this._render();
  },

  skip() {
    this._clearHighlight();
    this._active = false;
    document.getElementById('tour-overlay').classList.remove('on');
    API.call('save_settings', {onboarded: true}).catch(() => {});
  },
};

(async function init() {
  // Show loading immediately
  const overlay = document.getElementById('startup');
  const text = document.getElementById('startup-text');
  const retry = document.getElementById('startup-retry');
  const spinner = document.querySelector('.startup-spinner');
  overlay.classList.add('on');

  async function tryConnect() {
    text.textContent = '正在连接 Smithbox...';
    retry.classList.add('hidden');
    document.getElementById('startup-download').classList.add('hidden');
    document.getElementById('startup-help').classList.add('hidden');
    spinner.style.display = '';
    try {
      const result = await API.call('reconnect');
      if (result.success) {
        const empty = result.relics === 0 && result.effects === 0 && result.curses === 0;
        if (empty) {
          text.textContent = '未加载到任何数据。请先在 Smithbox 中创建项目并启用 Param Editor。';
          retry.classList.remove('hidden');
          document.getElementById('startup-download').classList.add('hidden');
          document.getElementById('startup-help').classList.remove('hidden');
          spinner.style.display = 'none';
          return;
        }
        overlay.classList.remove('on');
        await State.refresh();
        Render.all();
        Toast.show(result.message);
        Actions.checkUpdate();
        // 首次连接成功后显示引导式教程
        try {
          const settings = await API.call('get_settings');
          if (!settings.onboarded) {
            setTimeout(() => Tour.start(), 700);
          }
        } catch (e) { /* ignore */ }
        return;
      }
      throw new Error(result.message);
    } catch (e) {
      let msg = e.message || '无法连接';
      const dlBtn = document.getElementById('startup-download');
      if (msg.includes('UNIMPLEMENTED') || msg.includes('unimplemented')) {
        msg = 'Smithbox 版本太旧，请下载 2.2.4 或更新版本。';
        dlBtn.classList.remove('hidden');
      } else if (msg.includes('无法连接到 Smithbox')) {
        msg = '无法连接到 Smithbox — 请确认 Smithbox 已启动并加载了项目。';
        dlBtn.classList.remove('hidden');
      } else {
        dlBtn.classList.add('hidden');
      }
      text.textContent = msg;
      retry.classList.remove('hidden');
      document.getElementById('startup-help').classList.remove('hidden');
      spinner.style.display = 'none';
    }
  }

  await tryConnect();

  // Retry button calls reconnect then refresh
  const origReconnect = Actions.reconnect;
  Actions.reconnect = async function() {
    overlay.classList.add('on');
    await tryConnect();
  };
})();
