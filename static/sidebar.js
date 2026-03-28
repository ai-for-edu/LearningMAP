// Sidebar conversation management — shared across all pages
(function () {
  const STORAGE_KEY = 'openail_sessions';
  const CURRENT_KEY = 'openail_current_session';

  // One-time cleanup: remove legacy example sessions
  if (!localStorage.getItem('openail_v4')) {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(CURRENT_KEY);
    localStorage.removeItem('openail_examples_v2');
    localStorage.removeItem('openail_v3_clean');
    localStorage.setItem('openail_v4', '1');
  }

  function getSessions() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    } catch {
      return [];
    }
  }

  function saveSessions(sessions) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  }

  function getCurrentId() {
    return localStorage.getItem(CURRENT_KEY);
  }

  function setCurrentId(id) {
    localStorage.setItem(CURRENT_KEY, id);
  }

  function init() {
    const sessions = getSessions();
    const currentId = getCurrentId();
    renderSidebar(sessions, currentId);
  }

  function renderSidebar(sessions, currentId) {
    const container = document.getElementById('session-list');
    if (!container) return;

    container.innerHTML = '';

    // Close any open menu when clicking elsewhere
    document.addEventListener('click', function () {
      document.querySelectorAll('.session-menu').forEach(function (m) {
        m.classList.add('hidden');
      });
    });

    sessions.forEach(function (session) {
      const isActive = session.id === currentId;

      const wrapper = document.createElement('div');
      wrapper.className = 'relative group';

      const item = document.createElement('button');
      item.className =
        'w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-left text-sm transition-all ' +
        (isActive
          ? 'bg-primary-fixed/40 text-primary font-semibold'
          : 'text-on-surface-variant hover:bg-surface-container-high');

      item.innerHTML =
        '<span class="material-symbols-outlined text-base" style="font-variation-settings: \'FILL\' ' +
        (isActive ? '1' : '0') +
        ';">chat_bubble</span>' +
        '<span class="truncate flex-grow">' +
        escapeHtml(session.name) +
        '</span>';

      item.addEventListener('click', function () {
        if (!isActive) {
          setCurrentId(session.id);
          window.location.href = '/?session_id=' + session.id;
        }
      });

      // Three-dot menu button
      const menuBtn = document.createElement('button');
      menuBtn.className =
        'absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-md opacity-0 group-hover:opacity-100 hover:bg-slate-200 transition-all z-10';
      menuBtn.innerHTML = '<span class="material-symbols-outlined text-sm text-slate-400">more_horiz</span>';
      menuBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        document.querySelectorAll('.session-menu').forEach(function (m) {
          m.classList.add('hidden');
        });
        menu.classList.toggle('hidden');
      });

      // Dropdown menu with Rename + Delete
      const menu = document.createElement('div');
      menu.className =
        'session-menu hidden absolute right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-lg py-1 z-20 min-w-[120px]';

      // Rename button
      const renameBtn = document.createElement('button');
      renameBtn.className =
        'w-full flex items-center gap-2 px-3 py-2 text-sm text-on-surface-variant hover:bg-surface-container-high transition-colors text-left';
      renameBtn.innerHTML =
        '<span class="material-symbols-outlined text-base">edit</span>' +
        '<span>Rename</span>';
      renameBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        menu.classList.add('hidden');
        showRenameInput(session.id, wrapper);
      });

      // Delete button
      const deleteBtn = document.createElement('button');
      deleteBtn.className =
        'w-full flex items-center gap-2 px-3 py-2 text-sm text-error hover:bg-error/5 transition-colors text-left';
      deleteBtn.innerHTML =
        '<span class="material-symbols-outlined text-base">delete</span>' +
        '<span>Delete</span>';
      deleteBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        menu.classList.add('hidden');
        showDeleteConfirm(session.id, session.name);
      });

      menu.appendChild(renameBtn);
      menu.appendChild(deleteBtn);
      wrapper.appendChild(item);
      wrapper.appendChild(menuBtn);
      wrapper.appendChild(menu);
      container.appendChild(wrapper);
    });
  }

  function showRenameInput(sessionId, wrapper) {
    const sessions = getSessions();
    const session = sessions.find(function (s) { return s.id === sessionId; });
    if (!session) return;

    // Replace wrapper content with inline input
    const input = document.createElement('input');
    input.type = 'text';
    input.value = session.name;
    input.className = 'w-full px-3 py-2 rounded-xl text-sm border-2 border-primary outline-none font-body';
    input.select();

    wrapper.innerHTML = '';
    wrapper.appendChild(input);
    input.focus();

    function save() {
      const newName = input.value.trim() || '未命名';
      session.name = newName;
      saveSessions(sessions);
      renderSidebar(sessions, getCurrentId());
    }

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') save();
      if (e.key === 'Escape') renderSidebar(sessions, getCurrentId());
    });
    input.addEventListener('blur', save);
  }

  function showDeleteConfirm(sessionId, sessionName) {
    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 bg-black/30 flex items-center justify-center z-[100]';

    overlay.innerHTML =
      '<div class="bg-white rounded-3xl p-8 max-w-sm mx-4 shadow-xl">' +
      '  <div class="flex items-center gap-3 mb-4">' +
      '    <span class="material-symbols-outlined text-error text-2xl">warning</span>' +
      '    <h3 class="font-headline font-bold text-lg text-on-surface">Delete Conversation</h3>' +
      '  </div>' +
      '  <p class="font-body text-sm text-on-surface-variant mb-6">确定要删除「' + escapeHtml(sessionName) + '」吗？此操作不可撤销。</p>' +
      '  <div class="flex gap-3 justify-end">' +
      '    <button id="cancelDelete" class="px-5 py-2.5 rounded-xl text-sm font-headline font-bold text-on-surface-variant hover:bg-slate-100 transition-colors">Cancel</button>' +
      '    <button id="confirmDelete" class="px-5 py-2.5 rounded-xl text-sm font-headline font-bold text-white bg-error hover:bg-error/90 transition-colors shadow-sm">Delete</button>' +
      '  </div>' +
      '</div>';

    document.body.appendChild(overlay);

    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) overlay.remove();
    });

    document.getElementById('cancelDelete').addEventListener('click', function () {
      overlay.remove();
    });

    document.getElementById('confirmDelete').addEventListener('click', function () {
      deleteSession(sessionId);
      overlay.remove();
    });
  }

  function deleteSession(sessionId) {
    var sessions = getSessions();
    var currentId = getCurrentId();

    sessions = sessions.filter(function (s) { return s.id !== sessionId; });
    saveSessions(sessions);

    if (sessionId === currentId) {
      localStorage.removeItem(CURRENT_KEY);
      window.location.href = '/';
    } else {
      renderSidebar(sessions, getCurrentId());
    }
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // +New Session: create entry in sidebar and navigate to clean page
  window.createNewSession = function () {
    const sessions = getSessions();
    const id = Date.now().toString();
    sessions.unshift({ id: id, name: '未命名' });
    saveSessions(sessions);
    setCurrentId(id);
    window.location.href = '/?session_id=' + id;
  };

  // Allow pages to rename current session
  window.renameCurrentSession = function (newName) {
    const sessions = getSessions();
    const currentId = getCurrentId();
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i].id === currentId) {
        sessions[i].name = newName;
        break;
      }
    }
    saveSessions(sessions);
    renderSidebar(sessions, currentId);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
